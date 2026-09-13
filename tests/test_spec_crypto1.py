"""
HIVEMIND-CRYPTO-1 conformance — the security MUSTs that had no test.

This is the security-critical document, so each test below states the attack
or misconfiguration it is holding the line against, and every value asserted
is a value the spec (as corrected against the shipped code) actually fixes.

Pinned here:

  * CRYPTO-1 §2     — the protocol-v3 static X25519 key pair is persisted and
                      reloaded, so a node keeps one long-lived Noise identity
  * CRYPTO-1 §3.3   — a wrong password fails cryptographically at handshake
                      time and a failure is fatal: no Noise session, no
                      registered client
  * CRYPTO-1 §3     — the handshake is mandatory; there is no pre-shared-key
                      alternative, and the server advertises only Noise
  * CRYPTO-1 §3.4.3 — a node selects its pattern/suite only from what the
                      server offered, and the server refuses a selection it
                      never offered (including KKpsk0 without a pinned key)
  * CRYPTO-1 §3.4.4 — the Noise PSK is 32 bytes and derived deterministically
                      from (password, server node id)
  * CRYPTO-1 §4     — a fresh, unique nonce per message, at the deployed sizes
  * CRYPTO-1 §3.5   — the session keys are ephemeral: a reconnection runs a
                      fresh handshake and derives new ones

Retired with protocol v3 (hivemind-core 5.x): the v2 password-handshake AEAD
negotiation (CRYPTO-1 §3 — Noise "is the single key-exchange mechanism"; the
suite is selected from the server's offer, §3.1-§3.3, pinned in
``TestNodeSelectsOnlyFromWhatTheServerOffered``) and the pre-shared-key skip
(CRYPTO-1 §3 — "there is no cleartext, pre-shared-key, or password
alternative").

Deliberately NOT re-pinned here (already covered): the §3.1 protocol floor and
§5 INTERCOM rules (``test_spec_musts.py``), the §3.4.2/§3.4.5 Noise patterns,
prologue binding and TOFU pinning (``test_protocol_v3_noise.py``), and the
``crypto_required`` cleartext drop (``test_protocol_rules.py``).
"""
import pytest

from hivemind_bus_client.encryption import (AES_NONCE_SIZE,
                                            CHACHA20_NONCE_SIZE,
                                            SupportedCiphers, encrypt_bin)
from hivemind_bus_client.message import HiveMessage, HiveMessageType
from hivemind_bus_client.noise import (NOISE_PATTERN_KK, NOISE_PATTERN_XX,
                                       NOISE_SUITE_AESGCM, NOISE_SUITE_CHACHA,
                                       NOISE_SUPPORTED, select_noise_options,
                                       start_noise_handshake)

import hivemind_core.protocol as core_protocol

from hivescope.node import MasterNode, SatelliteNode
from hivescope.topology import TopologyBuilder


requires_noise = pytest.mark.skipif(
    not NOISE_SUPPORTED,
    reason="poorman-handshake was installed without the noise primitive; the "
           "protocol-v3 MUSTs cannot be evaluated in this environment")


# ---------------------------------------------------------------------------
# CRYPTO-1 §3.2 — the password verifier, and what a failure costs
# ---------------------------------------------------------------------------

#: A wrong password that is still strong enough to be accepted by
#: poorman-handshake's entropy floor — the test is about the verifier
#: rejecting a *mismatch*, not about password strength.
WRONG_PASSWORD = "correct-horse-battery-staple-but-the-wrong-one-42"


def _attempt_password_handshake(master_password, satellite=None):
    """Wire one satellite to one master with the master's DB holding
    ``master_password`` for it, and let the handshake run to whatever end.

    Deliberately bypasses :meth:`SatelliteNode.connect`, which raises when the
    handshake does not complete — the failure IS the behaviour under test.
    Returns ``(master, satellite)``; the caller cleans up.
    """
    master = MasterNode.create("M0")
    satellite = satellite or SatelliteNode.create("S0")
    satellite._master = master
    master.register_satellite(key=satellite.identity.access_key,
                              password=master_password)
    master.network_protocol.connect_satellite(satellite=satellite)
    return master, satellite


class TestPasswordVerifierIsCheckedAtHandshakeTime:
    """CRYPTO-1 §3.3 — 'A wrong password fails cryptographically at handshake
    time, not as a decrypt error on the first application frame', and an
    authentication failure at any Noise step is fatal.

    The password enters the handshake only as the Noise PSK (§3.4), so a
    mismatch aborts the handshake. If it did not, the peer would be
    registered, have a routing entry, and be announced to the agent before
    the failure showed. The negative case below is the whole point; the
    positive case is its control, so a refusal caused by anything else cannot
    read as conformance.
    """

    def test_a_wrong_password_leaves_no_session_and_no_client(self):
        master, satellite = _attempt_password_handshake(WRONG_PASSWORD)
        try:
            assert not satellite.shim.handshake_event.is_set(), (
                "a handshake whose password verifier failed MUST NOT complete")
            assert master.hm_protocol.clients == {}, (
                "an unauthenticated peer MUST NOT be registered; "
                f"clients={list(master.hm_protocol.clients)}")
            conn = satellite._connection
            assert conn is None or conn.noise_transport is None, (
                "no Noise session may be established for a failed authentication")
            assert satellite.shim.noise_transport is None, (
                "the node MUST NOT hold a Noise session after a failed authentication")
        finally:
            master.cleanup()
            satellite.cleanup()

    def test_the_matching_password_completes_and_derives_a_session_key(self):
        satellite = SatelliteNode.create("S0")
        master, satellite = _attempt_password_handshake(
            satellite.identity.password, satellite=satellite)
        try:
            assert satellite.shim.handshake_event.is_set()
            assert satellite.peer in master.hm_protocol.clients
            assert master.hm_protocol.clients[satellite.peer].noise_transport, (
                "a successful handshake must establish a Noise session")
        finally:
            master.cleanup()
            satellite.cleanup()


class TestSessionKeyIsEphemeral:
    """CRYPTO-1 §3.5 — 'The session keys are ephemeral: they live only for the
    duration of the connection, and a reconnection runs a fresh handshake.'

    A session key that survived a reconnection would make every future session
    decryptable from one recorded compromise. The handshake hash ``h`` binds
    the ephemeral keys of one handshake, so a fresh handshake gives a new one.
    """

    def test_reconnecting_derives_a_different_session_key(self):
        master = MasterNode.create("M0")
        satellite = SatelliteNode.create("S0")
        try:
            satellite.connect(master)
            first = master.hm_protocol.clients[satellite.peer].noise_transport
            assert first is not None and first.handshake_hash

            # The same peer identity connects again — same access key, same
            # password, same session id. Only the connection is new, which is
            # exactly the case a surviving key would go unnoticed in. The
            # first connection is closed first, as a real reconnect does.
            satellite.disconnect()
            assert satellite.peer not in master.hm_protocol.clients
            # The in-process shim has no socket close, so reset what the real
            # client resets when its connection closes
            # (HiveMessageBusClient._clear_connection_state).
            satellite.shim.noise_transport = None
            satellite.shim.handshake_event.clear()
            satellite.slave_protocol.reset_connection_state()
            # Forget the pinned server key so the reconnection runs XXpsk2
            # again. A pinned key would select KKpsk0, which the in-process
            # shim does not complete; freshness is the same for either pattern.
            identity = satellite.slave_protocol.identity
            for pin_id in list(identity.pinned_noise_keys):
                identity.forget_noise_key(pin_id)
            satellite.connect(master)
            assert satellite.shim.handshake_event.is_set(), (
                "the reconnection must complete its own handshake")
            second = master.hm_protocol.clients[satellite.peer].noise_transport
            assert second is not None and second.handshake_hash

            assert first.handshake_hash != second.handshake_hash, (
                "the session keys MUST be derived fresh per connection; the "
                "reconnection reused the previous handshake, so one recorded "
                "compromise would decrypt every later session")
        finally:
            master.cleanup()
            satellite.cleanup()


# ---------------------------------------------------------------------------
# CRYPTO-1 §3 — the handshake is mandatory; the server offers only Noise
# ---------------------------------------------------------------------------

class TestTheServerOffersOnlyTheNoiseHandshake:
    """CRYPTO-1 §3 — 'The handshake is mandatory on every connection: there is
    no cleartext, pre-shared-key, or password alternative.' (WIRE-1 §2 says
    the same.)

    This replaces the protocol-v2 test that a pre-shared key let a node skip
    the handshake. That mechanism is gone, so what the wire contract now
    fixes is the opposite: the parameter HANDSHAKE (§3.3 step 2) advertises
    Noise patterns and suites, and no field that offers a way around them.
    """

    def test_the_parameter_set_offers_noise_and_no_alternative(self):
        master = MasterNode.create("M0")
        satellite = SatelliteNode.create("S0")
        try:
            satellite.connect(master)
            advertised = satellite._connection._handshake_payload
            noise = advertised.get("noise") or {}
            assert NOISE_PATTERN_XX in noise.get("patterns", []), (
                "CRYPTO-1 §3.2: XXpsk2 MUST be offered; "
                f"advertised={advertised}")
            assert NOISE_SUITE_CHACHA in noise.get("suites", []), (
                "CRYPTO-1 §3.1: 25519_ChaChaPoly_SHA256 is mandatory to "
                f"implement; advertised={advertised}")
            for legacy in ("preshared_key", "handshake", "crypto_key"):
                assert not advertised.get(legacy), (
                    f"the server advertised the legacy '{legacy}' field as "
                    "true — CRYPTO-1 §3 allows no alternative to the Noise "
                    f"handshake; advertised={advertised}")
        finally:
            master.cleanup()
            satellite.cleanup()


# ---------------------------------------------------------------------------
# CRYPTO-1 §3.4.3 / §3.4.4 — Noise negotiation and the PSK
# ---------------------------------------------------------------------------

@requires_noise
class TestNodeSelectsOnlyFromWhatTheServerOffered:
    """CRYPTO-1 §3.4.3 — 'the pattern and suite the node selects MUST be one
    the server offered.'

    Enforced on both sides, and both sides matter. The node side stops an
    honest node from proposing something the server never advertised (which
    would abort the connection for no reason); the server side is the security
    half — it stops a node from *asserting* a pattern the server withheld, most
    importantly ``KKpsk0``, which skips the static-key exchange and is only
    safe when the server already has that node's key pinned.
    """

    def test_the_node_picks_the_mutually_supported_suite(self):
        pattern, suite = select_noise_options(
            server_patterns=[NOISE_PATTERN_XX],
            server_suites=[NOISE_SUITE_CHACHA, NOISE_SUITE_AESGCM])
        assert pattern == NOISE_PATTERN_XX
        assert suite == NOISE_SUITE_CHACHA, (
            "CRYPTO-1 §3.4.1 makes 25519_ChaChaPoly_SHA256 the mandatory suite; "
            "it must win whenever both peers offer it")

    def test_the_node_selects_nothing_when_no_suite_is_shared(self):
        assert select_noise_options(server_patterns=[NOISE_PATTERN_XX],
                                    server_suites=["25519_NotARealSuite_SHA256"]) is None, (
            "a node MUST NOT fall back to a suite the server did not offer")

    def test_the_node_selects_nothing_when_no_pattern_is_shared(self):
        assert select_noise_options(server_patterns=["IKpsk1"],
                                    server_suites=[NOISE_SUITE_CHACHA]) is None, (
            "a node MUST NOT propose a pattern the server did not offer")

    def test_kk_is_only_selected_when_a_pinned_key_exists(self):
        offered = [NOISE_PATTERN_KK, NOISE_PATTERN_XX]
        unpinned = select_noise_options(offered, [NOISE_SUITE_CHACHA])
        pinned = select_noise_options(offered, [NOISE_SUITE_CHACHA],
                                      pinned_remote_key="ab" * 32)
        assert unpinned[0] == NOISE_PATTERN_XX, (
            "KKpsk0 presumes a pinned static key; without one the node MUST "
            "fall back to XXpsk2")
        assert pinned[0] == NOISE_PATTERN_KK, (
            "CRYPTO-1 §3.4.2 prefers KKpsk0 when both peers hold pinned keys")

    @staticmethod
    def _assert_server_refuses(monkeypatch, pattern, suite):
        """Drive the server side of Noise message 1 with an unoffered
        selection and assert the refusal happened *at the offer check*.

        The observable is that ``start_noise_handshake`` is never reached: a
        node whose selection was refused for being unoffered leaves no
        cryptographic state behind at all. Asserting only that the connection
        dropped would also pass if the offer check were removed — the crafted
        Noise message would then fail a step later, for the wrong reason.
        """
        started = []
        master = MasterNode.create("M0")
        satellite = SatelliteNode.create("S0")
        try:
            # Connect first: the real handshake needs start_noise_handshake.
            # The spy goes in only for the crafted message 1 below.
            satellite.connect(master)
            monkeypatch.setattr(
                core_protocol, "start_noise_handshake",
                lambda *a, **kw: started.append(kw.get("pattern")) or (_ for _ in ()).throw(
                    AssertionError("unreachable")))
            conn = master.hm_protocol.clients[satellite.peer]
            conn.noise_handshake = None
            conn._handshake_payload = {
                "noise": {"patterns": [NOISE_PATTERN_XX],
                          "suites": [NOISE_SUITE_CHACHA]}}

            master.hm_protocol.handle_noise_handshake_message(
                HiveMessage(HiveMessageType.HANDSHAKE,
                            {"noise": {"pattern": pattern, "suite": suite,
                                       "msg": "00"}}),
                conn)

            assert started == [], (
                f"the server began a {pattern}/{suite} handshake it never "
                "offered — the selection must be refused before any Noise "
                "state is created")
            assert conn.noise_handshake is None
            assert satellite.peer not in master.hm_protocol.clients, (
                "asserting an unoffered pattern/suite is a fatal handshake "
                "failure — the connection MUST be dropped, not downgraded")
        finally:
            master.cleanup()
            satellite.cleanup()

    def test_the_server_refuses_a_suite_it_did_not_offer(self, monkeypatch):
        """The server half of the rule. A suite is used rather than a pattern
        because ``KKpsk0`` is additionally caught by the pinned-key guard
        below, so a pattern would not isolate the offer check."""
        self._assert_server_refuses(monkeypatch, NOISE_PATTERN_XX,
                                    NOISE_SUITE_AESGCM)

    def test_kkpsk0_is_refused_when_the_server_holds_no_pin(self, monkeypatch):
        """CRYPTO-1 §3.4.2 / §3.4.5 — ``KKpsk0`` presumes both peers already
        hold each other's static keys, so it never transmits one. Accepting it
        from a peer this server has no pin for would authenticate the peer
        against a key nobody checked — the pin becomes optional, which is the
        whole security value of TOFU-then-pin.

        KKpsk0 is offered explicitly here, so the offer check cannot be what
        refuses it. The pin check is what must refuse it.
        """
        started = []
        master = MasterNode.create("M0")
        satellite = SatelliteNode.create("S0")
        try:
            # Connect first: the real handshake needs start_noise_handshake.
            # The spy goes in only for the crafted message 1 below.
            satellite.connect(master)
            monkeypatch.setattr(
                core_protocol, "start_noise_handshake",
                lambda *a, **kw: started.append(kw.get("pattern")) or (_ for _ in ()).throw(
                    AssertionError("unreachable")))
            conn = master.hm_protocol.clients[satellite.peer]
            conn.noise_handshake = None
            conn._handshake_payload = {
                "noise": {"patterns": [NOISE_PATTERN_KK, NOISE_PATTERN_XX],
                          "suites": [NOISE_SUITE_CHACHA]}}

            master.hm_protocol.handle_noise_handshake_message(
                HiveMessage(HiveMessageType.HANDSHAKE,
                            {"noise": {"pattern": NOISE_PATTERN_KK,
                                       "suite": NOISE_SUITE_CHACHA,
                                       "msg": "00"}}),
                conn)

            assert started == [], (
                "the server began a KKpsk0 handshake for a peer whose static "
                "key it has never pinned")
            assert satellite.peer not in master.hm_protocol.clients
        finally:
            master.cleanup()
            satellite.cleanup()


@requires_noise
class TestNoisePskDerivation:
    """CRYPTO-1 §3.4.4 — 'the PSK is 32 bytes, derived from the shared site
    password, salted by the server node id.'

    What is pinned is the derivation *contract*, not the caching that sits in
    front of it: the same (password, node id) must give both peers the same
    PSK, or the handshake cannot complete; and a different password or a
    different server must give a different PSK, or a peer authenticated for
    one site is authenticated for another. The argon2 parameters themselves
    belong to poorman-handshake and are not asserted here.
    """

    @staticmethod
    def _psk(password, node_id):
        from poorman_handshake.noise import derive_psk
        return derive_psk(password, node_id=node_id)

    def test_the_psk_is_32_bytes(self):
        assert len(self._psk("hunter2", "node-a")) == 32, (
            "the Noise psk slot takes exactly 32 bytes")

    def test_the_same_inputs_give_the_same_psk(self):
        assert self._psk("hunter2", "node-a") == self._psk("hunter2", "node-a"), (
            "both peers derive independently — a non-deterministic PSK means "
            "no v3 handshake can ever complete")

    def test_a_different_password_gives_a_different_psk(self):
        assert self._psk("hunter2", "node-a") != self._psk("hunter3", "node-a"), (
            "the password is the only secret authenticating the handshake")

    def test_a_different_server_node_id_gives_a_different_psk(self):
        assert self._psk("hunter2", "node-a") != self._psk("hunter2", "node-b"), (
            "the node id is the salt: without it, one site password would "
            "authenticate a handshake against a different server")


@requires_noise
class TestStaticX25519KeyIsPersisted:
    """CRYPTO-1 §2 — 'a protocol-v3 node additionally holds a static X25519
    key pair, persisted.'

    Persistence is what makes TOFU pinning (§3.4.5) mean anything: a node that
    minted a fresh static key per start would contradict its own pin on every
    restart, and every peer would have to choose between refusing it forever
    and pinning nothing.
    """

    def _pubkey(self, key_path, node_id="server-node"):
        hs = start_noise_handshake(
            initiator=True, pattern=NOISE_PATTERN_XX, suite=NOISE_SUITE_CHACHA,
            password="hunter2", node_id=node_id, prologue=b"prologue",
            key_path=str(key_path))
        return hs.pubkey

    def test_the_same_key_path_reloads_the_same_static_key(self, tmp_path):
        path = tmp_path / "noise" / "static.key"
        first = self._pubkey(path)
        assert path.exists(), "the static key MUST be persisted, not ephemeral"
        assert self._pubkey(path) == first, (
            "a node MUST keep one long-lived static key; regenerating it on "
            "every handshake would break every peer's TOFU pin")

    def test_a_different_node_has_a_different_static_key(self, tmp_path):
        assert self._pubkey(tmp_path / "a.key") != self._pubkey(tmp_path / "b.key"), (
            "the static key is a node identity — two nodes must not share one")


# ---------------------------------------------------------------------------
# CRYPTO-1 §4 — nonces
# ---------------------------------------------------------------------------

class TestNoncesAreFreshAndTheDeployedSize:
    """CRYPTO-1 §4 — 'a fresh, unique IV per message.'

    Nonce reuse under GCM or ChaCha20-Poly1305 is catastrophic — it leaks the
    XOR of two plaintexts and, for GCM, the authentication subkey. That is the
    requirement worth pinning.

    On the size: the spec's blanket '12 bytes' is a *spec* defect. ChaCha20-
    Poly1305 is 12 per RFC 7539, but the AES-GCM path ships a 16-byte nonce and
    that number is frozen — the ESP32, MicroPython and JS decoders are all
    built against it, and changing it would break every deployed non-Python
    peer for no security gain. This test therefore pins the deployed values, so
    a well-meaning 'fix the spec violation' commit fails here first.
    """

    #: AES-GCM takes a 16-byte key, ChaCha20-Poly1305 a 32-byte one.
    KEYS = {SupportedCiphers.AES_GCM: "0123456789ABCDEF",
            SupportedCiphers.CHACHA20_POLY1305: "0123456789ABCDEF0123456789ABCDEF"}
    KEY = "0123456789ABCDEF"
    PLAINTEXT = b"the same plaintext, twice"

    def test_aes_gcm_nonce_is_the_frozen_16_bytes(self):
        blob = encrypt_bin(self.KEY, self.PLAINTEXT, SupportedCiphers.AES_GCM)
        assert AES_NONCE_SIZE == 16, (
            "the deployed AES-GCM nonce size is frozen at 16 bytes for "
            "ESP32/MicroPython/JS compatibility — see CRYPTO-1 §4")
        assert len(blob) > AES_NONCE_SIZE + len(self.PLAINTEXT)

    def test_chacha20_poly1305_nonce_is_12_bytes(self):
        assert CHACHA20_NONCE_SIZE == 12, "RFC 7539 fixes the ChaCha nonce at 12"

    @pytest.mark.parametrize("cipher,nonce_size", [
        (SupportedCiphers.AES_GCM, AES_NONCE_SIZE),
        (SupportedCiphers.CHACHA20_POLY1305, CHACHA20_NONCE_SIZE),
    ])
    def test_every_message_gets_a_fresh_nonce(self, cipher, nonce_size):
        key = self.KEYS[cipher]
        nonces = {encrypt_bin(key, self.PLAINTEXT, cipher)[:nonce_size]
                  for _ in range(64)}
        assert len(nonces) == 64, (
            f"{cipher} reused a nonce across 64 encryptions of the same "
            "plaintext — nonce reuse breaks the AEAD outright")

    @pytest.mark.parametrize("cipher", list(SupportedCiphers))
    def test_the_same_plaintext_never_produces_the_same_ciphertext(self, cipher):
        key = self.KEYS[cipher]
        a = encrypt_bin(key, self.PLAINTEXT, cipher)
        b = encrypt_bin(key, self.PLAINTEXT, cipher)
        assert a != b, (
            "a deterministic ciphertext means the IV is not fresh per message")
