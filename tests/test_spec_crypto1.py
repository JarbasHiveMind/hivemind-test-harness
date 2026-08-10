"""
HIVEMIND-CRYPTO-1 conformance — the security MUSTs that had no test.

This is the security-critical document, so each test below states the attack
or misconfiguration it is holding the line against, and every value asserted
is a value the spec (as corrected against the shipped code) actually fixes.

Pinned here:

  * CRYPTO-1 §2     — the protocol-v3 static X25519 key pair is persisted and
                      reloaded, so a node keeps one long-lived Noise identity
  * CRYPTO-1 §3.2   — the password verifier is checked at handshake time and a
                      failure is fatal: no session key, no registered client
  * CRYPTO-1 §3.3   — a pre-shared key lets a node skip the handshake, and the
                      server says so in its advertised parameter set
  * CRYPTO-1 §3.4.3 — a node selects its pattern/suite only from what the
                      server offered, and the server refuses a selection it
                      never offered (including KKpsk0 without a pinned key)
  * CRYPTO-1 §3.4.4 — the Noise PSK is 32 bytes and derived deterministically
                      from (password, server node id)
  * CRYPTO-1 §4     — AEAD negotiation picks the most-preferred cipher both
                      peers support, and refuses when there is no overlap
  * CRYPTO-1 §4     — a fresh, unique nonce per message, at the deployed sizes
  * CRYPTO-1 §4     — the v2 session key is ephemeral: a reconnection derives
                      a new one

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
    """CRYPTO-1 §3.2 — the password handshake is the baseline authentication,
    an explicit verifier reject at handshake time is RECOMMENDED, and
    'authentication failure is fatal'.

    Without the explicit reject, a wrong password only surfaced later as a
    decrypt failure on the first encrypted frame — by which time the peer is
    registered, has a routing entry, and has been announced to the agent. The
    negative case below is the whole point; the positive case is its control,
    so a refusal caused by anything else cannot read as conformance.
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
            assert conn is None or conn.crypto_key is None, (
                "no session key may be derived for a failed authentication")
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
            assert master.hm_protocol.clients[satellite.peer].crypto_key, (
                "a successful password handshake must derive a session key")
        finally:
            master.cleanup()
            satellite.cleanup()


class TestSessionKeyIsEphemeral:
    """CRYPTO-1 §4 — 'the session key is ephemeral; a fresh key is derived on
    reconnection.'

    A session key that survived a reconnection would make every future session
    decryptable from one recorded compromise, and would defeat the per-
    connection ``HandShake`` the code creates in ``__post_init__``.
    """

    def test_reconnecting_derives_a_different_session_key(self):
        master = MasterNode.create("M0")
        satellite = SatelliteNode.create("S0")
        try:
            satellite.connect(master)
            first = master.hm_protocol.clients[satellite.peer].crypto_key
            assert first

            # The same peer identity connects again — same access key, same
            # password, same session id. Only the connection is new, which is
            # exactly the case a surviving key would go unnoticed in.
            master.network_protocol.connect_satellite(satellite=satellite)
            satellite.slave_protocol.start_handshake()
            second = master.hm_protocol.clients[satellite.peer].crypto_key
            assert second

            assert first != second, (
                "the v2 session key MUST be derived fresh per connection; the "
                "reconnection reused the previous key, so one recorded "
                "compromise would decrypt every later session")
        finally:
            master.cleanup()
            satellite.cleanup()


# ---------------------------------------------------------------------------
# CRYPTO-1 §3.3 — a pre-shared key may skip the handshake
# ---------------------------------------------------------------------------

class TestPresharedKeySkipsTheHandshake:
    """CRYPTO-1 §3.3 — 'a pre-shared key may skip the handshake.'

    The server has to *say* so, because the node cannot otherwise tell whether
    silence means 'go ahead' or 'you failed'. The advertised parameter set is
    the only place this is expressed, so the two flags below are wire contract,
    not an internal detail. The complementary rule — that ``crypto_required``
    still drops cleartext on such a connection — is already pinned in
    ``test_protocol_rules.py``.
    """

    def test_the_parameter_set_reports_no_handshake_and_a_preshared_key(self):
        master = MasterNode.create("M0")
        satellite = SatelliteNode.create("S0")
        satellite._master = master
        try:
            master.register_satellite(key=satellite.identity.access_key,
                                      password=satellite.identity.password,
                                      crypto_key="0123456789ABCDEF")
            master.network_protocol.connect_satellite(satellite=satellite)

            conn = satellite._connection
            advertised = conn._handshake_payload
            assert advertised["preshared_key"] is True, (
                "a node holding a pre-shared key MUST be told so")
            assert advertised["handshake"] is False, (
                "with a pre-shared key in place the server MUST NOT demand a "
                f"handshake; advertised={advertised}")
            # (The session key itself is not asserted: a node MAY still run
            # the optional handshake afterwards and rotate it. What §3.3
            # fixes is that the server told the peer it need not.)
        finally:
            master.cleanup()
            satellite.cleanup()

    def test_without_a_preshared_key_the_handshake_is_demanded(self):
        master = MasterNode.create("M0")
        satellite = SatelliteNode.create("S0")
        try:
            satellite.connect(master)
            advertised = satellite._connection._handshake_payload
            assert advertised["handshake"] is True
            assert advertised["preshared_key"] is False
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
        monkeypatch.setattr(
            core_protocol, "start_noise_handshake",
            lambda *a, **kw: started.append(kw.get("pattern")) or (_ for _ in ()).throw(
                AssertionError("unreachable")))

        master = MasterNode.create("M0")
        satellite = SatelliteNode.create("S0")
        try:
            satellite.connect(master)
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

        The server here has never pinned S0's Noise key (the shim only ever
        completes the legacy v2 handshake), and KKpsk0 is offered explicitly,
        so the offer check cannot be what refuses it.
        """
        started = []
        monkeypatch.setattr(
            core_protocol, "start_noise_handshake",
            lambda *a, **kw: started.append(kw.get("pattern")) or (_ for _ in ()).throw(
                AssertionError("unreachable")))

        master = MasterNode.create("M0")
        satellite = SatelliteNode.create("S0")
        try:
            satellite.connect(master)
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
# CRYPTO-1 §4 — AEAD selection and nonces
# ---------------------------------------------------------------------------

def _override_server_config(monkeypatch, **overrides):
    """Run the node against the real server config with ``overrides`` applied.

    A developer machine (or a CI image) with its own ``~/.config/hivemind``
    can widen or narrow ``allowed_ciphers``, which would make a negotiation
    assertion pass or fail for reasons that have nothing to do with the code.
    Pinning the operator half of the negotiation is the only way the test can
    say anything about the intersection.
    """
    cfg = dict(core_protocol.get_server_config())
    cfg.update(overrides)
    monkeypatch.setattr(core_protocol, "get_server_config", lambda: cfg)
    return cfg


class TestAeadNegotiation:
    """CRYPTO-1 §4 — 'the AEAD is chosen from {CHACHA20-POLY1305, AES-GCM};
    peers use the most-preferred cipher both support.'

    A negotiation that silently settled on *anything* when the sets do not
    overlap would be a downgrade oracle. hivemind-core intersects the client's
    preference-ordered list with the operator's allow-list, takes the client's
    top surviving choice, and drops the connection when nothing survives.
    """

    @staticmethod
    def _renegotiate(master, satellite, ciphers):
        """Send a fresh password HANDSHAKE offering ``ciphers``.

        Re-handshaking on a live connection is a supported operation (the code
        comments call it key rotation), which is what makes the negotiation
        reachable in-process without rebuilding the node.
        """
        conn = master.hm_protocol.clients[satellite.peer]
        envelope = satellite.slave_protocol.pswd_handshake.generate_handshake()
        master.hm_protocol.handle_handshake_message(
            HiveMessage(HiveMessageType.HANDSHAKE,
                        {"envelope": envelope, "ciphers": ciphers}),
            conn)
        return conn

    def test_the_client_top_choice_is_selected_when_the_server_allows_it(self, monkeypatch):
        master = MasterNode.create("M0")
        satellite = SatelliteNode.create("S0")
        try:
            satellite.connect(master)
            _override_server_config(monkeypatch, allowed_ciphers=[
                SupportedCiphers.AES_GCM.value,
                SupportedCiphers.CHACHA20_POLY1305.value])
            conn = self._renegotiate(
                master, satellite,
                [SupportedCiphers.CHACHA20_POLY1305.value,
                 SupportedCiphers.AES_GCM.value])
            assert conn.cipher == SupportedCiphers.CHACHA20_POLY1305, (
                "with both ciphers allowed, the peers must land on the "
                f"client's most-preferred; got {conn.cipher!r}")
        finally:
            master.cleanup()
            satellite.cleanup()

    def test_the_server_allow_list_narrows_the_choice(self, monkeypatch):
        master = MasterNode.create("M0")
        satellite = SatelliteNode.create("S0")
        try:
            satellite.connect(master)
            # The client still prefers ChaCha20-Poly1305, but the operator only
            # allows AES-GCM: the selection must be the most-preferred cipher
            # *both* support, not the client's first choice.
            _override_server_config(
                monkeypatch, allowed_ciphers=[SupportedCiphers.AES_GCM.value])
            conn = self._renegotiate(
                master, satellite,
                [SupportedCiphers.CHACHA20_POLY1305.value,
                 SupportedCiphers.AES_GCM.value])
            assert conn.cipher == SupportedCiphers.AES_GCM, (
                "a cipher the operator did not allow MUST NOT be selected; "
                f"got {conn.cipher!r}")
        finally:
            master.cleanup()
            satellite.cleanup()

    def test_no_shared_cipher_drops_the_connection(self, monkeypatch):
        master = MasterNode.create("M0")
        satellite = SatelliteNode.create("S0")
        try:
            satellite.connect(master)
            assert satellite.peer in master.hm_protocol.clients
            _override_server_config(
                monkeypatch, allowed_ciphers=[SupportedCiphers.AES_GCM.value])
            self._renegotiate(master, satellite,
                              [SupportedCiphers.CHACHA20_POLY1305.value])
            assert satellite.peer not in master.hm_protocol.clients, (
                "with no mutually supported AEAD the connection MUST be "
                "dropped, never continued in the clear or on a guessed cipher")
        finally:
            master.cleanup()
            satellite.cleanup()


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
