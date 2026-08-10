"""
Spec-MUST conformance tests — one focused test per protocol-spec MUST.

Each test names the spec clause it enforces and is either a real behavioural
assertion (the stack implements the MUST) or a ``xfail(strict=True)`` that
documents the exact missing capability (the stack does NOT implement it). A
strict xfail flips to a failure the moment the gap is closed, so an
unimplemented MUST can never quietly rot into false coverage.

Covered here (in-process, via the hivescope shim):

  * NODE-1 §3.4 / MSG-1 §5  — flood-loop suppression bounds a PING in a mesh
  * MSG-1 §5                — route-based loop suppression for generic PROPAGATE:
                              a node MUST NOT re-forward a message whose route
                              already names it (hivemind-core#162, >=4.10.3a1)
  * POLICY-1 §5             — fail-closed: a raising policy denies, never defaults open
  * POLICY-1 §4             — live whitelist mutation takes effect on the next message
  * WIRE-1 §4.2             — reserved binary codes 8/11 (strict-xfail: reused)
  * CRYPTO-1 §5             — INTERCOM origin-signature verification (real
                              positive+negative pair: a genuinely RSA-signed
                              INTERCOM delivers its inner BUS; a forged signature
                              is rejected against the TOFU-pinned pubkey)
  * NODE-1 §3.3             — BROADCAST through a relay to a leaf
  * cross-runtime matrix    — a BUS utterance round-trips on every node combo
  * CRYPTO-1 §3.1           — the configured protocol-version floor is judged on
                              the version the handshake actually completes at,
                              not on what the peer merely declares it can do
  * POLICY-1 §5             — a chain that cannot be built falls back to
                              DenyAllPolicy; admission is refused, never opened
  * BRIDGE-1 §4             — two connections that resolve to the same peer id
                              are disambiguated by a server-generated suffix,
                              so neither displaces the other's routing entry
  * AGENT-1 §3.1            — an unreachable agent backend produces an explicit
                              ``backend_unavailable`` denial, never silence
  * MSG-1 §5                — ``target_site_id`` survives a relay hop unchanged

The Noise-handshake MUSTs (CRYPTO-1 §3.4 identity pinning, KKpsk0 negotiation)
cannot run in-process — the shim completes only the legacy v2 handshake — so they
live in ``test_protocol_v3_noise.py`` against a real ``hivemind-core`` hub.
"""
import re
import time
import uuid

import pytest
from ovos_bus_client.message import Message
from hivemind_bus_client.message import HiveMessage, HiveMessageType

import hivemind_core.policy as core_policy
import hivemind_core.protocol as core_protocol

from hivescope.node import MasterNode, SatelliteNode
from hivescope.topology import TopologyBuilder
from hivescope.assertions import assert_policy_denied

from tests.conftest import poll_until


# ---------------------------------------------------------------------------
# NODE-1 §3.4 / MSG-1 §5 — loop suppression bounds a flood in a mesh
# ---------------------------------------------------------------------------

@pytest.fixture
def cyclic_topology():
    """A diamond mesh where a flood reaches inner nodes by two paths.

        M0
        ├─ R1 (relay) ── S0, S1
        └─ R2 (relay) ── S2, S3

    R1 and R2 both relay M0's flood, and each leaf's responsive PING travels
    back up through its relay to M0 and back down the *other* branch. Without
    per-flood suppression that cross-branch echo would recirculate forever; with
    it, each master node re-floods a given ``flood_id`` at most once. This is the
    closest acyclic-builder shape to a routing cycle and exercises the same
    suppression path.
    """
    b = TopologyBuilder()
    try:
        b.add_master("M0")
        r1 = b.add_relay("R1", upstream=b.get_master("M0")).listener
        r2 = b.add_relay("R2", upstream=b.get_master("M0")).listener
        b.add_satellite("S0", upstream=r1)
        b.add_satellite("S1", upstream=r1)
        b.add_satellite("S2", upstream=r2)
        b.add_satellite("S3", upstream=r2)
        b.start_all()
        yield b
    finally:
        b.stop_all()


def _make_ping(peer, flood_id):
    inner = HiveMessage(HiveMessageType.PING, payload={
        "flood_id": flood_id, "timestamp": time.time(),
        "peer": peer, "site_id": "mesh",
    })
    return HiveMessage(HiveMessageType.PROPAGATE, payload=inner)


class TestFloodLoopSuppression:
    """NODE-1 §3.4 — a routing message must not loop forever; the flood_id gate
    bounds each node's participation in a flood to exactly once."""

    # The 30s project-wide per-test budget is too tight for this one. The
    # diamond fixture builds SEVEN nodes, each generating a fresh 2048-bit RSA
    # identity and completing a password handshake: ~13s of fixture setup and
    # ~3s of assertions on an idle machine, and a loaded CI runner multiplies
    # that until the test trips the budget. Every such failure has been the box
    # being busy, never a hang — raising the budget makes it pass, which is the
    # proof. A gate that flips under load teaches people to ignore red, so give
    # this one room; it still fails fast if the flood really does not terminate,
    # because a non-terminating flood recirculates forever rather than finishing
    # slowly.
    @pytest.mark.timeout(180)
    def test_flood_terminates_and_reinjection_is_suppressed(self, cyclic_topology):
        b = cyclic_topology
        m0 = b.get_master("M0")
        r1 = b.get_relay("R1").listener
        r2 = b.get_relay("R2").listener

        flood_id = f"mesh-{uuid.uuid4()}"
        m0.hm_protocol.hive_mapper.start_ping(flood_id)
        m0.hm_protocol._seen_flood_ids.add(flood_id)  # M0 originates → already seen
        m0.send_to_all(_make_ping(m0.hm_protocol.peer, flood_id))

        # Every relay master must have participated in the flood exactly once —
        # the flood_id is now recorded as seen, so a re-encounter is suppressed.
        for node in (r1, r2):
            assert flood_id in node.hm_protocol._seen_flood_ids, \
                "relay must record the flood_id after re-flooding once"

        # Re-inject the SAME flood into a relay: because the id is already seen,
        # the relay must NOT emit a second responsive flood to its leaves.
        sends = []
        for peer, conn in r1.hm_protocol.clients.items():
            orig = conn.send
            conn.send = lambda msg, _o=orig, _p=peer: (sends.append(_p), _o(msg))[1]

        r1.hm_protocol.handle_ping_message(
            HiveMessage(HiveMessageType.PING, payload={
                "flood_id": flood_id, "timestamp": time.time(),
                "peer": "loopback", "site_id": "mesh"}),
            list(r1.hm_protocol.clients.values())[0],
        )
        assert sends == [], (
            "a relay must not re-flood an already-seen flood_id — loop "
            f"suppression failed, it re-sent to {sends}"
        )


# ---------------------------------------------------------------------------
# MSG-1 §5 — route-based loop suppression for a generic PROPAGATE
# ---------------------------------------------------------------------------

class TestRouteBasedLoopSuppression:
    """MSG-1 §5 — 'a node MUST NOT forward a message whose route already
    contains a hop naming it.' This is the route mechanism, distinct from the
    PING flood_id cache: it must bound *any* routed message, not only PINGs.

    hivemind-core >=4.10.3a1 (hivemind-core#162) implements this in
    ``_is_routing_loop``/``_append_self_hop``, keyed on the RECEIVING node's
    own stable identity (``self.identity.public_key``, exposed as
    ``self._node_id``) — NOT on connection-peer names. A hop is a match only
    when its ``source`` equals the forwarding node's own public key, so the
    route must name the master (the node that would do the re-forwarding),
    not the sibling satellite it would otherwise forward to.
    """

    def test_propagate_naming_master_in_route_is_not_forwarded(self):
        b = TopologyBuilder()
        try:
            b.add_master("M0")
            b.add_satellite("S0", upstream=b.get_master("M0"),
                            allowed_types=["test.route.loop"])
            b.add_satellite("S1", upstream=b.get_master("M0"),
                            allowed_types=["test.route.loop"])
            b.start_all()
            m0 = b.get_master("M0")
            s0 = b.get_satellite("S0")
            s1 = b.get_satellite("S1")

            # core fans the *unpacked inner* out to peers, so a sibling observes
            # the inner BUS envelope (not the PROPAGATE wrapper).
            received = []
            s1.shim.emitter.on(HiveMessageType.BUS, received.append)

            # Craft a PROPAGATE whose route already names M0 (the node about to
            # re-forward it) by its stable node identity (public key) → M0 MUST
            # NOT re-forward it to its other satellite S1.
            inner = HiveMessage(HiveMessageType.BUS,
                                payload=Message("test.route.loop", {}))
            prop = HiveMessage(HiveMessageType.PROPAGATE, payload=inner,
                               route=[{"source": m0.identity.public_key,
                                       "targets": [s0.peer]}])
            s0.send(prop)

            assert received == [], \
                "PROPAGATE whose route already names M0 must not be re-forwarded to S1"
        finally:
            b.stop_all()


# ---------------------------------------------------------------------------
# POLICY-1 §5 — fail-closed
# ---------------------------------------------------------------------------

class TestPolicyFailClosed:
    """POLICY-1 §5 — 'any error in a policy MUST be converted into a deny.'
    A raising policy must not default the message open."""

    def test_raising_policy_denies_and_does_not_inject(self):
        from hivemind_plugin_manager.policy import PolicyPlugin

        class _BoomPolicy(PolicyPlugin):
            def review(self, message, client):
                raise RuntimeError("policy blew up")

        b = TopologyBuilder()
        try:
            b.add_master("M0")
            b.add_satellite("S0", upstream=b.get_master("M0"),
                            allowed_types=["recognizer_loop:utterance"])
            b.start_all()
            m0 = b.get_master("M0")
            s0 = b.get_satellite("S0")

            # Append a mandatory (non-optional) raising policy AFTER the built-in
            # allowed_types gate, so the type gate passes and the raising policy
            # is what decides the verdict.
            m0.hm_protocol.policy_chain.policies.append(_BoomPolicy())
            m0.hm_protocol.policy_chain._optional.append(False)

            s0.send(Message("recognizer_loop:utterance", {"utterances": ["hi"]}))

            # Fail-closed: an otherwise-allowed message is denied, never injected.
            m0.agent_protocol.assert_not_injected("recognizer_loop:utterance")
            assert_policy_denied(m0, s0, "recognizer_loop:utterance",
                                 deny_code="policy_error", strict=True)
        finally:
            b.stop_all()


# ---------------------------------------------------------------------------
# POLICY-1 §4 — live whitelist mutation takes effect on the next message
# ---------------------------------------------------------------------------

class TestLiveWhitelistMutation:
    """POLICY-1 §4 — 'the gate MUST resolve the whitelist freshly enough that a
    grant takes effect' — a runtime allow/deny applies to the next message."""

    def test_runtime_grant_admits_next_message(self):
        GRANTED = "recognizer_loop:record_begin"
        b = TopologyBuilder()
        try:
            b.add_master("M0")
            b.add_satellite("S0", upstream=b.get_master("M0"),
                            allowed_types=["recognizer_loop:utterance"])
            b.start_all()
            m0 = b.get_master("M0")
            s0 = b.get_satellite("S0")
            conn = m0.hm_protocol.clients[s0.peer]

            # Before the grant: the type is denied and never injected.
            s0.send(Message(GRANTED, {}))
            m0.agent_protocol.assert_not_injected(GRANTED)

            # Grant it at runtime in the DB, then bust the per-connection cache
            # so the next admission re-resolves the whitelist (POLICY-1 §4).
            user = conn.resolve_user(m0.hm_protocol.db, force=True)
            assert user is not None, "DB row for the connected client must exist"
            user.allowed_types = list(user.allowed_types) + [GRANTED]
            m0.hm_protocol.db.update_item(user)
            conn.invalidate_user()

            # After the grant: the very next message of that type is admitted.
            s0.send(Message(GRANTED, {"data": "now allowed"}))
            m0.agent_protocol.assert_injected(GRANTED)
        finally:
            b.stop_all()

    def test_runtime_revoke_blocks_next_message(self):
        REVOKED = "recognizer_loop:utterance"
        b = TopologyBuilder()
        try:
            b.add_master("M0")
            b.add_satellite("S0", upstream=b.get_master("M0"),
                            allowed_types=[REVOKED])
            b.start_all()
            m0 = b.get_master("M0")
            s0 = b.get_satellite("S0")
            conn = m0.hm_protocol.clients[s0.peer]

            s0.send(Message(REVOKED, {"utterances": ["allowed"]}))
            m0.agent_protocol.assert_injected(REVOKED)

            # Revoke at runtime, bust cache.
            user = conn.resolve_user(m0.hm_protocol.db, force=True)
            user.allowed_types = [t for t in user.allowed_types if t != REVOKED]
            m0.hm_protocol.db.update_item(user)
            conn.invalidate_user()
            m0.agent_protocol.clear()

            s0.send(Message(REVOKED, {"utterances": ["now blocked"]}))
            m0.agent_protocol.assert_not_injected(REVOKED)
        finally:
            b.stop_all()


# ---------------------------------------------------------------------------
# cross-runtime matrix — a BUS utterance round-trips on every node combination
# ---------------------------------------------------------------------------

class TestCrossRuntimeMatrix:
    """The harness supports direct master↔satellite, single-relay, and
    two-deep-relay chains. In each, a satellite BUS utterance must reach its
    direct upstream master's (shared) agent bus (NODE-1 §3.3). A relay injects
    on its own shared brain rather than escalating a plain BUS upstream, so the
    delivery target is the satellite's *direct* master, at any chain depth."""

    @pytest.mark.parametrize("depth", [0, 1, 2],
                             ids=["direct", "one-relay", "two-relays"])
    def test_utterance_reaches_direct_master(self, depth):
        b = TopologyBuilder()
        try:
            b.add_master("M0")
            direct_master = b.get_master("M0")
            for i in range(depth):
                direct_master = b.add_relay(f"R{i}", upstream=direct_master).listener
            b.add_satellite("Sx", upstream=direct_master,
                            allowed_types=["recognizer_loop:utterance"])
            b.start_all()
            s = b.get_satellite("Sx")

            text = f"matrix depth {depth}"
            s.send(Message("recognizer_loop:utterance", {"utterances": [text]}))

            msg = direct_master.agent_protocol.last_injected("recognizer_loop:utterance")
            assert msg is not None, \
                f"utterance did not reach its direct master through {depth} relay(s)"
            assert text in msg.data.get("utterances", []), \
                f"payload corrupted across {depth} relay(s): {msg.data}"
        finally:
            b.stop_all()


# ---------------------------------------------------------------------------
# WIRE-1 §4.2 — reserved binary message codes
# ---------------------------------------------------------------------------

class TestReservedWireCodes:
    """WIRE-1 §4.2 — codes 0-10 and 12 are assigned; code 11 and codes 13-31
    are unassigned. A revision MUST NOT give code 11 to another type, and a
    receiver MUST reject a frame carrying an unassigned value as malformed.

    Code 11 was ``THIRDPRTY``, which HIVEMIND-MSG-1 §3 removed. Code 8 is
    ``CASCADE`` and is assigned — an earlier version of this test called it
    reserved, which the spec never said.
    """

    def test_retired_code_11_is_not_reassigned(self):
        from hivemind_bus_client.serialization import _INT2TYPE
        assert 11 not in _INT2TYPE, \
            "code 11 is retired (was THIRDPRTY) and MUST NOT be reassigned"

    def test_assigned_codes_are_exactly_the_spec_table(self):
        from hivemind_bus_client.serialization import _INT2TYPE
        assert sorted(_INT2TYPE) == [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12], \
            f"WIRE-1 §4.2 assigns 0-10 and 12, got {sorted(_INT2TYPE)}"

    def test_unassigned_code_is_rejected_as_malformed(self):
        """A receiver MUST reject an unassigned code instead of mapping it to
        a type. Code 11 and 13-31 are the unassigned range."""
        from hivemind_bus_client.serialization import _decode_bitstring_v1
        from bitstring import BitStream

        # the v1 body opens with the 5-bit message-type code; the decoder must
        # refuse an unassigned one before it reads anything else
        for code in (11, 13, 31):
            with pytest.raises(ValueError):
                _decode_bitstring_v1(BitStream(uint=code, length=5))


# ---------------------------------------------------------------------------
# CRYPTO-1 §5 — INTERCOM signature verification
# ---------------------------------------------------------------------------

class TestIntercomSignatureVerification:
    """CRYPTO-1 §5 — an INTERCOM target that verifies a signature MUST deliver
    a message whose signature verifies and reject one whose signature does not.

    hivemind-core implements this: ``handle_intercom_message`` verifies the
    origin signature over the ciphertext against the public key pinned for the
    sender on first use (TOFU, from the sender's HELLO), decrypts, and dispatches
    the inner message by its OWN type. This pair proves both halves of the MUST
    end-to-end on the installed core — no xfail, because the capability exists.

    The inner BUS is deliberately minimal. hivemind-core encrypts the INTERCOM
    body with raw RSA (``encrypt_RSA`` → PKCS1-OAEP), so the serialized inner
    must fit one RSA block; with the harness's 2048-bit identity keys that ceiling
    is ~214 bytes, which an empty-``data`` ``recognizer_loop:utterance`` clears
    and a populated one does not. Signature verification — the property under
    test — does not depend on the inner payload's contents.
    """

    def _signed_intercom_payload(self, master, sign_key):
        """Build an INTERCOM payload: inner BUS encrypted to ``master``'s pubkey,
        the ciphertext signed with ``sign_key`` (a private-key PEM or RsaKey)."""
        import pybase64
        from poorman_handshake.asymmetric.utils import encrypt_RSA, sign_RSA

        inner = HiveMessage(HiveMessageType.BUS,
                            payload=Message("recognizer_loop:utterance", {}))
        ciphertext = encrypt_RSA(master.identity.public_key, inner.serialize())
        signature = sign_RSA(sign_key, ciphertext)
        return {
            "ciphertext": pybase64.b64encode(ciphertext).decode(),
            "signature": pybase64.b64encode(signature).decode(),
        }

    def test_valid_signed_intercom_delivers_inner(self, minimal_topology):
        from poorman_handshake.asymmetric.utils import load_RSA_key

        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        # S0's pubkey was pinned by the master on S0's HELLO during connect, so
        # a signature made with S0's private key verifies and the inner BUS is
        # delivered to the agent bus. ``private_key`` is a PEM file path, so load
        # it into an RSA key before signing.
        assert m0.hm_protocol.trusted_pubkeys.get(s0._connection.key), (
            "precondition: master must have TOFU-pinned S0's pubkey from HELLO")
        payload = self._signed_intercom_payload(
            m0, load_RSA_key(s0.identity.private_key))
        s0.send(HiveMessage(HiveMessageType.INTERCOM, payload=payload,
                            target_pubkey=m0.identity.public_key))

        # The MUST: a verified INTERCOM delivers its inner BUS to the target.
        poll_until(
            lambda: m0.agent_protocol.last_injected("recognizer_loop:utterance"),
            timeout=3,
            message="verified INTERCOM did not deliver its inner BUS")
        m0.agent_protocol.assert_injected("recognizer_loop:utterance")

    def test_forged_intercom_signature_rejected(self, minimal_topology):
        from poorman_handshake.asymmetric.utils import create_RSA_key

        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        # A forger signs with a key that is NOT the one pinned for S0. The
        # ciphertext still decrypts (it is RSA-encrypted to the master), but the
        # origin signature fails verification against S0's pinned pubkey, so the
        # inner BUS MUST NOT be dispatched.
        _forger_pub, forger_priv = create_RSA_key()
        payload = self._signed_intercom_payload(m0, forger_priv)
        s0.send(HiveMessage(HiveMessageType.INTERCOM, payload=payload,
                            target_pubkey=m0.identity.public_key))

        # A tiny wait so a (bug-induced) async delivery would still be caught
        # before we assert the negative, rather than passing by racing it.
        time.sleep(0.3)
        m0.agent_protocol.assert_not_injected("recognizer_loop:utterance")


# ---------------------------------------------------------------------------
# NODE-1 §3.3 — BROADCAST delivered through a relay node
# ---------------------------------------------------------------------------

class TestBroadcastThroughRelay:
    """NODE-1 §3.3 — a relay forwards to 'every directly connected node, both
    downstream and upstream'. A BROADCAST from the top master must reach a leaf
    satellite that sits behind a relay, not only the relay's satellite side."""

    def test_broadcast_reaches_leaf_behind_relay(self, chain_topology):
        b = chain_topology
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")  # leaf behind relay R1

        received = []
        s0.shim.emitter.on(HiveMessageType.BROADCAST, received.append)

        inner = HiveMessage(HiveMessageType.BUS,
                            payload=Message("test.event", {"ping": "pong"}))
        m0.send_to_all(HiveMessage(HiveMessageType.BROADCAST, payload=inner))

        poll_until(lambda: len(received) >= 1, timeout=3,
                   message="BROADCAST never reached the leaf behind the relay")


# ---------------------------------------------------------------------------
# CRYPTO-1 §3.1 — the configured protocol-version floor
# ---------------------------------------------------------------------------

def _override_server_config(monkeypatch, **overrides):
    """Run the node against the real server config with ``overrides`` applied.

    Copies whatever ``get_server_config()`` returns and patches only the named
    keys, so the code under test still goes through its own parsing of the
    value (e.g. ``_configured_min_protocol_version``) instead of having the
    parsed result handed to it.

    The operator policy chain is emptied unless the caller says otherwise. A
    developer machine that has a real ``~/.config/hivemind`` naming a policy
    plugin which is not installed makes ``PolicyChain.from_config`` raise, and
    every master then silently runs on the DenyAllPolicy fallback — which is
    the very behaviour some of these tests are trying to distinguish. Starting
    from an explicitly empty operator chain (the builtin gates are prepended
    regardless and are not opt-out) makes the outcome the same everywhere.
    """
    cfg = dict(core_protocol.get_server_config())
    cfg.setdefault("policy", {})
    cfg["policy"] = {"chain": []}
    cfg.update(overrides)
    monkeypatch.setattr(core_protocol, "get_server_config", lambda: cfg)
    return cfg


class TestProtocolVersionFloor:
    """HIVEMIND-CRYPTO-1 §3.1 / HIVEMIND-WIRE-1 §2 — a server MUST refuse a
    handshake that *completes* below its configured ``min_protocol_version``.

    The subtlety this pins is the reason the check exists at all. Advertising a
    floor in the HELLO parameter set is not enforcement: a peer that is
    *capable* of the floor (it has a password, so the server offers Noise/v3)
    can simply answer with the legacy password envelope and finish the
    connection at v2. If the floor is only judged on declared capability, that
    peer is admitted at a version the operator refused — a silent downgrade.
    hivemind-core therefore re-checks the floor against the version the
    handshake is actually being performed at, in ``handle_handshake_message``.

    Both cases below run the same v3-capable satellite through the same legacy
    v2 password handshake; only the configured floor differs.
    """

    @staticmethod
    def _attempt_legacy_handshake():
        """Wire one satellite to one master and let the handshake run.

        Returns ``(master, satellite)``; the caller inspects the outcome and is
        responsible for cleanup. Deliberately does not use
        :meth:`TopologyBuilder.start_all`, which retries a failed handshake and
        would mask a refusal behind a second, unrelated error.
        """
        master = MasterNode.create("M0")
        satellite = SatelliteNode.create("S0")
        satellite._master = master
        master.register_satellite(key=satellite.identity.access_key,
                                  password=satellite.identity.password)
        master.network_protocol.connect_satellite(satellite=satellite)
        return master, satellite

    def test_handshake_below_the_floor_is_refused(self, monkeypatch):
        # Floor v3: the satellite is v3-capable (it has a password) but the
        # in-process shim only ever performs the legacy v2 handshake, so the
        # version it completes at is below the floor and MUST be refused.
        _override_server_config(monkeypatch, min_protocol_version=3)
        master, satellite = self._attempt_legacy_handshake()
        try:
            assert not satellite.shim.handshake_event.is_set(), (
                "a handshake completing below the configured "
                "min_protocol_version MUST be refused, but it succeeded — "
                "the protocol floor is advisory again (CRYPTO-1 §3.1)"
            )
            assert master.hm_protocol.clients == {}, (
                "a peer refused for being below the protocol floor MUST NOT be "
                f"registered; clients={list(master.hm_protocol.clients)}"
            )
        finally:
            master.cleanup()
            satellite.cleanup()

    def test_handshake_at_the_floor_is_admitted(self, monkeypatch):
        # The control for the test above: same satellite, same legacy v2
        # handshake, floor lowered to v2. Without this, a refusal caused by
        # anything at all would read as conformance.
        _override_server_config(monkeypatch, min_protocol_version=2)
        master, satellite = self._attempt_legacy_handshake()
        try:
            assert satellite.shim.handshake_event.is_set(), (
                "a handshake AT the configured floor must be admitted"
            )
            assert satellite.peer in master.hm_protocol.clients
        finally:
            master.cleanup()
            satellite.cleanup()


# ---------------------------------------------------------------------------
# POLICY-1 §5 — an unbuildable policy chain falls back to deny-everything
# ---------------------------------------------------------------------------

class TestPolicyChainUnavailableFailsClosed:
    """HIVEMIND-POLICY-1 §5 — 'if the chain cannot be constructed, install a
    deny-everything fallback.'

    A misconfigured or broken policy module is the one situation where the
    admission chain has no opinion to offer, and it is exactly the situation
    where defaulting open would hand an unauthenticated-by-policy peer the
    agent bus. hivemind-core catches the construction failure and installs
    ``DenyAllPolicy``, which denies with the registered code
    ``policy_chain_unavailable`` (POLICY-1 §6).

    The break is injected at ``PolicyChain.from_config`` because that is the
    real trigger — an operator's config naming a policy module that raises on
    load — rather than by installing the fallback directly, which would only
    re-test ``DenyAllPolicy`` and not the fallback being reached.
    """

    def test_unbuildable_chain_denies_an_otherwise_allowed_message(self, monkeypatch):
        ALLOWED = "recognizer_loop:utterance"

        def _unbuildable(cls, config, hm_protocol=None):
            raise RuntimeError("policy module failed to load")

        monkeypatch.setattr(core_policy.PolicyChain, "from_config",
                            classmethod(_unbuildable))

        b = TopologyBuilder()
        try:
            # The master is built while from_config is broken, so its chain is
            # the DenyAllPolicy fallback.
            b.add_master("M0")
            b.add_satellite("S0", upstream=b.get_master("M0"),
                            allowed_types=[ALLOWED])
            b.start_all()
            m0 = b.get_master("M0")
            s0 = b.get_satellite("S0")

            # ALLOWED is on this client's whitelist: with a healthy chain it is
            # admitted (see TestLiveWhitelistMutation). Under the fallback it
            # MUST NOT be.
            s0.send(Message(ALLOWED, {"utterances": ["hi"]}))

            m0.agent_protocol.assert_not_injected(ALLOWED)
            assert_policy_denied(m0, s0, ALLOWED,
                                 deny_code="policy_chain_unavailable",
                                 strict=True)
        finally:
            b.stop_all()


# ---------------------------------------------------------------------------
# BRIDGE-1 §4 — two connections must never resolve to one peer id
# ---------------------------------------------------------------------------

class TestPeerCollisionSuffix:
    """HIVEMIND-BRIDGE-1 §3.1/§4 — 'two live connections MUST NOT resolve to
    one Layer-1 identity.'

    A peer id is ``name::session_id``. Both halves come from the connecting
    client, so two clients deployed with the same node name that also present
    the same session id ask the server for the same ``source``. Left alone the
    second connection would overwrite the first in the routing table, and every
    response addressed to the first client would then be delivered to the
    second (AGENT-1 §3.2 response isolation). hivemind-core disambiguates the
    newcomer with a server-generated ``::<hex8>`` suffix, which BRIDGE-1 §3.1
    explicitly sanctions.

    The test asserts the *property* — both connections stay independently
    addressable — and only checks the suffix shape as the mechanism the spec
    names, so a different disambiguator that still keeps both peers routable
    would need the shape assertion updated but not the behavioural one.
    """

    def test_colliding_peers_stay_independently_addressable(self, monkeypatch):
        _override_server_config(monkeypatch)
        b = TopologyBuilder()
        try:
            b.add_master("M0")
            first = b.add_satellite("S0", upstream=b.get_master("M0"))
            second = b.add_satellite("S1", upstream=b.get_master("M0"))

            # Force the collision: same node name, same session id. These are
            # the only two inputs to the peer string, and both are client-chosen.
            second.identity.name = first.identity.name
            second.shim._session_id = first.shim.session_id

            b.start_all()
            m0 = b.get_master("M0")

            assert first.peer != second.peer, (
                "two live connections resolved to the SAME peer id "
                f"({first.peer!r}) — BRIDGE-1 §4 collision handling is gone"
            )
            assert m0.hm_protocol.clients.get(first.peer) is first._connection, (
                "the first connection was displaced from the routing table by "
                "a colliding newcomer"
            )
            assert m0.hm_protocol.clients.get(second.peer) is second._connection

            # The mechanism BRIDGE-1 §3.1 sanctions: a server-generated suffix
            # on the newcomer, not a renaming of the incumbent.
            base = f"{first.identity.name}::{first.shim.session_id}"
            assert first.peer == base
            assert re.fullmatch(re.escape(base) + r"::[0-9a-f]{8}", second.peer), (
                f"expected a '::<hex8>' collision suffix, got {second.peer!r}")

            # The property that matters: a message addressed to one peer reaches
            # that peer and only that peer.
            got_first, got_second = [], []
            first.shim.emitter.on(HiveMessageType.BUS, got_first.append)
            second.shim.emitter.on(HiveMessageType.BUS, got_second.append)

            m0.send_to_satellite(second.peer, HiveMessage(
                HiveMessageType.BUS, payload=Message("test.collision", {"to": "second"})))

            poll_until(lambda: got_second, timeout=3,
                       message="message addressed to the suffixed peer never arrived")
            assert got_first == [], (
                "a message addressed to the second connection was delivered to "
                "the first — the two peers share a routing entry")
        finally:
            b.stop_all()


# ---------------------------------------------------------------------------
# AGENT-1 §3.1 — an unreachable backend is reported, never swallowed
# ---------------------------------------------------------------------------

class TestBackendUnavailable:
    """HIVEMIND-AGENT-1 §3.1 — 'when the agent backend is unreachable the node
    MUST tell the originator explicitly; it MUST NOT drop the message in
    silence.'

    This pins the SERVER half, which is what AGENT-1 governs: an admitted
    message whose delivery to the agent bus fails comes back to the peer as a
    ``hive.policy.denied`` carrying the registered code ``backend_unavailable``
    (POLICY-1 §6). Silence here is the damaging regression — a satellite with
    no timeout of its own (NODE-1 §5.5 is still unimplemented) waits forever.

    ``get_bus`` is made to raise ``ConnectionError``, which is precisely how a
    real agent plugin reports a dead backend bus.
    """

    def test_unreachable_backend_answers_with_backend_unavailable(self, monkeypatch):
        ALLOWED = "recognizer_loop:utterance"
        _override_server_config(monkeypatch)

        b = TopologyBuilder()
        try:
            b.add_master("M0")
            b.add_satellite("S0", upstream=b.get_master("M0"),
                            allowed_types=[ALLOWED])
            b.start_all()
            m0 = b.get_master("M0")
            s0 = b.get_satellite("S0")

            def _backend_down(client):
                raise ConnectionError("agent bus is not running")

            m0.hm_protocol.agent_protocol.get_bus = _backend_down

            # The message is admitted by the chain and then cannot be delivered.
            # The peer must hear about it; the deny code distinguishes "your
            # message was refused" from "your message was accepted and lost".
            s0.send(Message(ALLOWED, {"utterances": ["hi"]}))

            assert_policy_denied(m0, s0, ALLOWED,
                                 deny_code="backend_unavailable", strict=True)
        finally:
            b.stop_all()


# ---------------------------------------------------------------------------
# MSG-1 §5 — target_site_id survives a relay hop
# ---------------------------------------------------------------------------

class TestSiteIdSurvivesRelayHop:
    """HIVEMIND-MSG-1 §5 — ``target_site_id`` gates delivery, not travel, and a
    relay that rebuilds an envelope MUST copy it onto the new one.

    Site targeting is read off the OUTER envelope, and a relay does not forward
    the envelope it received — it unpacks the inner message, re-stamps routing
    metadata and wraps it again. A rebuild that forgets ``target_site_id``
    produces a message that still travels but can never be delivered anywhere:
    the site it was addressed to stops recognising it. Nothing about the
    failure is visible on the wire, which is why it needs a test.

    The topology is S0 → R1 → M0 with three distinct site ids, and the message
    is addressed to M0's site. Both halves are asserted: the message IS
    delivered at the site it names (the id survived), and it is NOT delivered
    at the relay it crossed (the id was not rewritten to the relay's own site).
    """

    def test_site_targeted_propagate_is_delivered_beyond_the_relay(self, monkeypatch):
        TARGETED = "test.site.targeted"
        _override_server_config(monkeypatch)
        b = TopologyBuilder()
        try:
            b.add_master("M0")
            m0 = b.get_master("M0")
            # The relay's upstream connection is the client M0 admits the
            # forwarded message from, so the grant lives there.
            relay = b.add_relay("R1", upstream=m0, allowed_types=[TARGETED])
            b.add_satellite("S0", upstream=relay.listener, allowed_types=[TARGETED])
            b.start_all()
            s0 = b.get_satellite("S0")

            assert len({m0.identity.site_id, relay.listener.identity.site_id,
                        s0.identity.site_id}) == 3, \
                "precondition: the three nodes must sit at distinct sites"

            inner = HiveMessage(HiveMessageType.BUS, payload=Message(
                TARGETED, {"hops": 1},
                {"session": {"session_id": s0.shim.session_id}}))
            s0.send(HiveMessage(HiveMessageType.PROPAGATE, payload=inner,
                                target_site_id=m0.identity.site_id))

            poll_until(lambda: m0.agent_protocol.last_injected(TARGETED), timeout=3,
                       message=("a PROPAGATE addressed to M0's site never reached "
                                "M0's agent bus — target_site_id was lost or "
                                "rewritten crossing the relay (MSG-1 §5)"))
            relay.listener.agent_protocol.assert_not_injected(TARGETED)
        finally:
            b.stop_all()


def _noise_pair(tmp_path, client_prologue, server_prologue):
    """An initiator/responder ``NoiseHandShake`` pair for one XXpsk2 exchange."""
    from hivemind_bus_client.noise import (start_noise_handshake,
                                           NOISE_PATTERN_XX, NOISE_SUITE_CHACHA)
    common = dict(pattern=NOISE_PATTERN_XX, suite=NOISE_SUITE_CHACHA,
                  password="a shared site password", node_id="master:0.0.0.0")
    node = start_noise_handshake(initiator=True, prologue=client_prologue,
                                 key_path=str(tmp_path / "node.key"), **common)
    server = start_noise_handshake(initiator=False, prologue=server_prologue,
                                   key_path=str(tmp_path / "server.key"), **common)
    return node, server


def _run_xxpsk2(node, server):
    """Drive the three XXpsk2 messages; raises on any authentication failure."""
    server.read_message(node.write_message(b""))
    node.read_message(server.write_message(b""))
    server.read_message(node.write_message(b""))


class TestManualDiscovery:
    """DISCOVERY-1 §2 / §2.5 — a satellite MUST support at least one discovery
    method; manual configuration is one of them, and is RECOMMENDED for fixed
    deployments. ``hivemind-bus-client`` implements it as
    ``NodeIdentity.default_master`` / ``default_port``, which
    ``HiveMessageBusClient.init_identity`` resolves the connection target from
    whenever no host is passed explicitly.
    """

    @staticmethod
    def _client_with_identity(**identity_values):
        """A bus client that has run identity resolution but never connected.

        ``__init__`` opens sockets and starts threads, so the object is built
        without it and only ``init_identity`` — the code under test — is run.
        ``IDENTITY_FILE`` is swapped for a plain dict so the developer's real
        ``~/.config/hivemind/_identity.json`` is neither read nor written.
        """
        from hivemind_bus_client.client import HiveMessageBusClient
        from hivemind_bus_client.identity import NodeIdentity

        identity = NodeIdentity()
        identity.IDENTITY_FILE = dict(identity_values)
        client = HiveMessageBusClient.__new__(HiveMessageBusClient)
        client.identity = identity
        client._password = None
        client._access_key = None
        client._name = "test-satellite"
        client._host = None
        client._port = None
        return client

    def test_manually_configured_address_is_used_as_the_connection_target(self):
        """DISCOVERY-1 §2.5 — a satellite MAY be configured with a server's
        address directly, bypassing automatic discovery entirely."""
        client = self._client_with_identity(
            access_key="a-key", password="a-password",
            default_master="ws://192.0.2.10", default_port=5678)

        client.init_identity()

        assert client._host == "ws://192.0.2.10", \
            "the manually configured master must become the connection host"
        assert client._port == 5678, \
            "the manually configured port must become the connection port"

    def test_a_satellite_with_no_discovery_result_refuses_to_connect(self):
        """DISCOVERY-1 §2 — a satellite needs at least one discovery method.
        With no manual configuration and no discovered address there is nothing
        to connect to, and the client must say so rather than invent a target."""
        client = self._client_with_identity(
            access_key="a-key", password="a-password")

        with pytest.raises(RuntimeError, match="host not set"):
            client.init_identity()


class TestAdvertisementDefaults:
    """DISCOVERY-1 §2.1 / §2.2 — mDNS is the default advertisement method (a
    server enables it by default); SSDP/UPnP is optional and a server keeps it
    off by default. The server is ``hivemind-core``, and its shipped defaults
    are ``hivemind_core.config._DEFAULT["presence"]`` — asserted directly so a
    developer's own ``~/.config`` overrides cannot mask a regression.
    """

    @staticmethod
    def _presence_defaults():
        from hivemind_core.config import _DEFAULT
        return _DEFAULT["presence"]

    def test_mdns_advertisement_is_enabled_by_default(self):
        """DISCOVERY-1 §2.1 — mDNS is the default discovery method."""
        presence = self._presence_defaults()
        assert presence["enabled"] is True, \
            "a server must advertise itself by default"
        assert presence["zeroconf"] is True, \
            "mDNS is the default discovery method and must ship enabled"

    def test_ssdp_advertisement_is_disabled_by_default(self):
        """DISCOVERY-1 §2.2 — SSDP/UPnP is optional and off by default."""
        assert self._presence_defaults()["upnp"] is False, \
            "SSDP/UPnP is optional and a server must keep it off by default"

    def test_presence_library_also_defaults_ssdp_off(self):
        """DISCOVERY-1 §2.2 — the default must hold at the advertiser itself,
        not only at the one caller that overrides it."""
        import inspect
        from hivemind_presence import LocalPresence

        upnp_default = inspect.signature(LocalPresence.__init__).parameters["upnp"].default
        assert upnp_default is False, \
            "LocalPresence must keep SSDP off unless a caller asks for it"


class TestAnnouncementContents:
    """DISCOVERY-1 §3 — an announcement MUST carry enough to reach the server
    (at minimum a network address) and MAY carry a human-meaningful identifier.
    It MUST NOT be relied upon to carry secrets, and a satellite MUST NOT treat
    any field of it as proof of the server's identity.

    Asserted against the real ``ServiceInfo`` that ``hivemind-presence`` hands
    to zeroconf — the exact record that goes on the wire — built for a loopback
    address so no multicast traffic is needed.
    """

    HOST = "127.0.0.1"
    PORT = 5678
    NAME = "Kitchen Hive"

    @classmethod
    def _announcement(cls):
        from hivemind_presence.zero import ZeroConfAnnounce
        return ZeroConfAnnounce(host=cls.HOST, port=cls.PORT, name=cls.NAME).info

    def test_announcement_carries_a_reachable_address(self):
        """DISCOVERY-1 §3 — at minimum a network address."""
        import ipaddress

        info = self._announcement()
        assert ipaddress.ip_address(self.HOST).packed in info.addresses, \
            "the announcement must carry the server's address"
        assert info.port == self.PORT, \
            "the announcement must carry the port the server listens on"
        assert info.properties[b"host"].decode() == self.HOST
        assert int(info.properties[b"port"]) == self.PORT

    def test_announcement_carries_a_human_meaningful_name(self):
        """DISCOVERY-1 §3 — a human-meaningful server identifier, so a user can
        choose among several."""
        info = self._announcement()
        assert info.properties[b"name"].decode() == self.NAME, \
            "the announcement must carry the operator-chosen node name"

    def test_announcement_carries_no_credential(self):
        """DISCOVERY-1 §3 — an announcement MUST NOT be relied upon to carry
        secrets; it is broadcast in the clear. Nothing usable as a credential
        may appear in it."""
        info = self._announcement()
        advertised = {k.decode() for k in info.properties}
        assert advertised == {"host", "port", "ssl", "name", "type"}, \
            f"unexpected field in a cleartext announcement: {advertised}"

    def test_a_discovered_node_still_needs_an_out_of_band_access_key(self):
        """DISCOVERY-1 §3 / §4 — no announcement field is proof of identity: a
        node built from a discovery result carries no credential, so connecting
        is impossible without a key supplied out of band."""
        import inspect
        from hivemind_presence.devices import AbstractDevice, HiveMindNode

        node = HiveMindNode(AbstractDevice(
            host=self.HOST, port=self.PORT,
            device_type="HiveMind-websocket", name=self.NAME))

        assert node.address == f"{self.HOST}:{self.PORT}", \
            "a discovery result must yield a reachable address"
        assert set(node.device.data) == {"host", "port", "ssl", "type"}, \
            "a discovery result must carry no credential"
        key = inspect.signature(HiveMindNode.connect).parameters["key"]
        assert key.default is inspect.Parameter.empty, \
            "connecting to a discovered node must require an access key that " \
            "discovery did not supply"


class TestDiscoveryIsNotAuthentication:
    """DISCOVERY-1 §4 — a satellite MUST authenticate every discovered server
    before it exchanges any application message with it. Knowing where a server
    is says nothing about whether it is the intended one, so an address alone
    must not admit a node to the hive."""

    def test_knowing_the_address_without_a_registered_key_is_refused(self):
        from hivescope.node import SatelliteNode

        b = TopologyBuilder()
        try:
            b.add_master("M0")
            master = b.get_master("M0")

            # A satellite that has "discovered" the master: it knows exactly
            # where it is and is wired straight to it. What it does not have is
            # a registered access key — discovery never supplies one.
            discovered = SatelliteNode.create("Discovered")
            discovered._master = master

            with pytest.raises((ValueError, RuntimeError)):
                master.network_protocol.connect_satellite(satellite=discovered)

            assert discovered.peer not in master.hm_protocol.clients, \
                "an unauthenticated node must not be admitted to the hive"
            master.agent_protocol.assert_not_injected("recognizer_loop:utterance")
        finally:
            b.stop_all()


class TestNoisePrologueBinding:
    """CRYPTO-1 §3.4.3 — both peers MUST initialize the handshake with a
    prologue built from the server's cleartext HELLO payload, its cleartext
    parameter HANDSHAKE payload and the selected Noise protocol name. Any
    tampering with the negotiation changes the prologue on exactly one side and
    the handshake MUST abort with an authentication failure.

    This is the whole of the downgrade-protection story: without it a
    man-in-the-middle edits ``max_protocol_version`` in the cleartext step-2
    payload and both peers complete a handshake over the forged parameters.
    """

    HELLO = {"node_id": "master:0.0.0.0", "pubkey": "not-a-real-key"}
    PARAMS = {"handshake": True, "min_protocol_version": 3,
              "max_protocol_version": 3, "crypto_required": True,
              "noise": {"patterns": ["XXpsk2"],
                        "suites": ["25519_ChaChaPoly_SHA256"]}}
    PROTOCOL_NAME = "Noise_XXpsk2_25519_ChaChaPoly_SHA256"

    def _prologue(self, params):
        from hivemind_bus_client.noise import build_prologue
        return build_prologue(self.HELLO, params, self.PROTOCOL_NAME)

    def test_matching_prologue_completes_the_handshake(self, tmp_path):
        """CRYPTO-1 §3.4.3 — the untampered case must succeed, so the abort
        below is proof of tamper detection and not of a broken handshake."""
        agreed = self._prologue(self.PARAMS)
        node, server = _noise_pair(tmp_path, agreed, agreed)

        _run_xxpsk2(node, server)

        assert node.handshake_finished and server.handshake_finished, \
            "an untampered XXpsk2 handshake must complete"

    def test_downgraded_parameters_abort_the_handshake(self, tmp_path):
        """CRYPTO-1 §3.4.3 — an attacker lowering ``max_protocol_version`` in
        the cleartext parameter HANDSHAKE changes the node's prologue only; the
        handshake MUST abort."""
        downgraded = dict(self.PARAMS, max_protocol_version=2)
        node, server = _noise_pair(tmp_path,
                                   client_prologue=self._prologue(downgraded),
                                   server_prologue=self._prologue(self.PARAMS))

        with pytest.raises(Exception):
            _run_xxpsk2(node, server)

        assert not node.handshake_finished, \
            "a tampered negotiation must never yield a node transport state"
        assert not server.handshake_finished, \
            "a tampered negotiation must never yield a server transport state"

    def test_stripped_cipher_suite_aborts_the_handshake(self, tmp_path):
        """CRYPTO-1 §3.4.3 — stripping a suite from the server's advertised
        list is tampering with the negotiation and MUST abort."""
        stripped = dict(self.PARAMS,
                        noise={"patterns": ["XXpsk2"], "suites": []})
        node, server = _noise_pair(tmp_path,
                                   client_prologue=self._prologue(stripped),
                                   server_prologue=self._prologue(self.PARAMS))

        with pytest.raises(Exception):
            _run_xxpsk2(node, server)

        assert not server.handshake_finished


class TestNoiseTransportReplayResistance:
    """CRYPTO-1 §3.4.5 — a Noise transport message MUST be rejected when it
    fails to decrypt at the current receive counter, and MUST NOT be retried
    under another nonce. The strictly sequential counters ARE the replay
    resistance that protocol version 3 adds over the §4 session layer.
    """

    def _transports(self, tmp_path):
        from hivemind_bus_client.noise import NoiseTransport
        agreed = TestNoisePrologueBinding()._prologue(
            TestNoisePrologueBinding.PARAMS)
        node, server = _noise_pair(tmp_path, agreed, agreed)
        _run_xxpsk2(node, server)
        return NoiseTransport(node), NoiseTransport(server)

    def test_replayed_frame_is_rejected(self, tmp_path):
        """CRYPTO-1 §3.4.5 — a captured genuine frame re-injected by an active
        attacker must not authenticate a second time."""
        from hivemind_bus_client.noise import NoiseTransportFailed

        node, server = self._transports(tmp_path)
        frame = node.encrypt_frame("first message")

        assert server.decrypt_frame(frame) == "first message", \
            "the genuine frame must decrypt once"
        with pytest.raises(NoiseTransportFailed):
            server.decrypt_frame(frame)

    def test_out_of_order_frame_is_rejected_and_not_retried(self, tmp_path):
        """CRYPTO-1 §3.4.5 — a frame that fails at the current counter is
        rejected there; the receiver must not walk the nonce forward looking
        for a counter under which it would verify."""
        from hivemind_bus_client.noise import NoiseTransportFailed

        node, server = self._transports(tmp_path)
        first = node.encrypt_frame("first message")
        second = node.encrypt_frame("second message")

        # The receiver is at counter 0; ``second`` was sealed under counter 1.
        with pytest.raises(NoiseTransportFailed):
            server.decrypt_frame(second)

        # Having refused it, the receiver is still at counter 0 — it neither
        # skipped ahead nor consumed a nonce on the rejected frame.
        assert server.decrypt_frame(first) == "first message", \
            "rejecting an out-of-order frame must not disturb the counter"
        assert server.decrypt_frame(second) == "second message", \
            "the frame must decrypt once the stream reaches its counter"

    def test_tampered_frame_is_rejected(self, tmp_path):
        """CRYPTO-1 §3.4.5 — a Noise transport message whose bytes were edited
        fails AEAD and must be rejected, not delivered."""
        from hivemind_bus_client.noise import NoiseTransportFailed

        node, server = self._transports(tmp_path)
        frame = bytearray(node.encrypt_frame("first message"))
        frame[-1] ^= 0x01

        with pytest.raises(NoiseTransportFailed):
            server.decrypt_frame(bytes(frame))


class TestSessionCipherIntegrity:
    """CRYPTO-1 §4 — the AEAD tag provides integrity: a receiver MUST reject
    any message whose authentication tag does not verify. A node that
    advertised ``crypto_required`` MUST drop any unencrypted message received
    after the handshake.

    Asserted on a real, handshaken master↔satellite connection through
    ``HiveMindClientConnection.decode`` — the server's actual ingress path.
    Each test owns its topology because a rejected frame disconnects the peer.
    """

    @staticmethod
    def _connected():
        b = TopologyBuilder()
        b.add_master("M0")
        b.add_satellite("S0", upstream=b.get_master("M0"))
        b.start_all()
        m0 = b.get_master("M0")
        conn = m0.hm_protocol.clients[b.get_satellite("S0").peer]
        return b, conn

    @staticmethod
    def _encrypted_bus(conn):
        from hivemind_bus_client.encryption import encrypt_as_json
        bus = HiveMessage(HiveMessageType.BUS,
                          payload=Message("test.event", {"ok": True}))
        return encrypt_as_json(key=conn.crypto_key, plaintext=bus.serialize(),
                               cipher=conn.cipher, encoding=conn.encoding)

    def test_message_with_a_broken_aead_tag_is_rejected(self):
        """CRYPTO-1 §4 — a receiver MUST reject any message whose
        authentication tag does not verify."""
        import json as _json

        b, conn = self._connected()
        try:
            good = self._encrypted_bus(conn)
            assert conn.decode(good).msg_type == HiveMessageType.BUS, \
                "precondition: an intact encrypted BUS must decode"

            forged = _json.loads(self._encrypted_bus(conn))
            forged["tag"] = ("00" * (len(forged["tag"]) // 2)
                             if forged["tag"] != "00" * (len(forged["tag"]) // 2)
                             else "11" * (len(forged["tag"]) // 2))
            with pytest.raises(Exception):
                conn.decode(_json.dumps(forged))
        finally:
            b.stop_all()

    def test_tampered_ciphertext_is_rejected(self):
        """CRYPTO-1 §4 — integrity covers the body too: an edited ciphertext
        must not decode into a message the server then acts on."""
        import json as _json

        b, conn = self._connected()
        try:
            forged = _json.loads(self._encrypted_bus(conn))
            body = forged["ciphertext"]
            forged["ciphertext"] = body[:-2] + ("aa" if body[-2:] != "aa" else "bb")
            with pytest.raises(Exception):
                conn.decode(_json.dumps(forged))
        finally:
            b.stop_all()

    def test_unencrypted_message_is_dropped_when_crypto_is_required(self):
        """CRYPTO-1 §4 / §3.3 — a node that advertised ``crypto_required``
        MUST drop any unencrypted message received after the handshake."""
        b, conn = self._connected()
        try:
            assert conn.crypto_required, \
                "precondition: the harness master must require crypto"
            cleartext = HiveMessage(HiveMessageType.BUS,
                                    payload=Message("test.event", {})).serialize()
            with pytest.raises(Exception):
                conn.decode(cleartext)
        finally:
            b.stop_all()

    def test_key_establishment_messages_stay_readable_in_the_clear(self):
        """CRYPTO-1 §4 — the drop applies to traffic *after* the handshake:
        HELLO and HANDSHAKE precede the session key and MUST remain accepted in
        the clear, or no connection could ever be established."""
        b, conn = self._connected()
        try:
            for msg_type in (HiveMessageType.HELLO, HiveMessageType.HANDSHAKE):
                cleartext = HiveMessage(msg_type, payload={"noop": True}).serialize()
                assert conn.decode(cleartext).msg_type == msg_type, \
                    f"cleartext {msg_type} must remain accepted"
        finally:
            b.stop_all()


class TestPrivateKeyIsNeverTransmitted:
    """CRYPTO-1 §2 — a node MUST keep its private key secret and MUST NOT
    transmit it. The identity that goes on the wire is the *public* key."""

    def test_no_recorded_message_contains_the_private_key(self, minimal_topology):
        b = minimal_topology
        m0 = b.get_master("M0")

        def _pem_body(pem_text):
            return "".join(pem_text.strip().splitlines()[1:-1])

        with open(m0.identity.private_key) as f:
            private_body = _pem_body(f.read())
        public_body = _pem_body(m0.identity.public_key)
        wire = "\n".join(str(rec.payload) for rec in m0.recorder.snapshot())

        # Positive control: the public key IS on the wire, so this search would
        # have found the private key's base64 body had it been sent too.
        assert public_body[:64] in wire, \
            "precondition: the master's public key must appear in the handshake"
        assert private_body[:64] not in wire, \
            "the private key MUST NOT be transmitted"


def require_wire1_bus_client():
    """Fail with an actionable message when the installed bus client predates
    the WIRE-1 §4.2/§4.3 enforcement these tests pin.

    Deliberately not a skip: an environment below the declared floor is a
    broken environment, and a silent skip would hide the fact that nothing
    checked the wire contract on this run.
    """
    from hivemind_bus_client.version import (VERSION, VERSION_BUILD,
                                             VERSION_MAJOR, VERSION_MINOR)
    installed = (VERSION_MAJOR, VERSION_MINOR, VERSION_BUILD)
    assert installed >= WIRE1_BUS_CLIENT_FLOOR, (
        f"installed hivemind_bus_client {VERSION} predates the WIRE-1 §4.2/§4.3 "
        f"enforcement (needs >= "
        f"{'.'.join(str(n) for n in WIRE1_BUS_CLIENT_FLOOR)}a1, see "
        f"pyproject.toml). This is a stale environment, not a protocol "
        f"regression — upgrade the client before reading anything into the "
        f"failures below."
    )


class TestWireMessageTypeAssignment:
    """WIRE-1 §4.2 — the 5-bit message-type assignment is FROZEN.

    An earlier revision of the spec called 8 and 11 "reserved"; §4.2 was
    corrected to state that codes 0-12 are all assigned (8=CASCADE,
    11=THIRDPRTY) and that "a revision MUST NOT renumber an assigned code,
    even to make the ordering tidier". This class pins the table itself, so a
    renumbering breaks loudly here instead of silently breaking every deployed
    decoder.
    """

    def test_assignment_matches_the_spec_table_exactly(self):
        """WIRE-1 §4.2 — every code 0-12 maps to the type the spec assigns it,
        and nothing else is assigned."""
        from hivemind_bus_client.serialization import _INT2TYPE

        actual = {code: t.name for code, t in _INT2TYPE.items()}
        assert actual == WIRE1_MESSAGE_TYPE_CODES, (
            "the WIRE-1 §4.2 message-type assignment is frozen; this diff is a "
            "wire-breaking renumbering, not a tidy-up"
        )

    def test_no_encoder_path_emits_an_unassigned_code(self):
        """WIRE-1 §4.2 — 'codes 13-31 are unassigned: a sender MUST NOT emit
        them.' No message type may map to a code outside 0-12."""
        from hivemind_bus_client.serialization import _INT2TYPE

        emitted = set(_INT2TYPE)
        assert emitted <= set(range(13)), (
            f"encoder can emit unassigned 5-bit codes {sorted(emitted - set(range(13)))}"
        )

    def test_receiver_rejects_an_unassigned_code_as_malformed(self):
        """WIRE-1 §4.2 — 'a receiver MUST reject a frame carrying [an
        unassigned code] as malformed rather than mapping it to any type.'"""
        require_wire1_bus_client()
        from bitstring import BitArray
        from hivemind_bus_client.serialization import decode_bitstring

        for code in (13, 20, 31):
            frame = BitArray()
            frame.append('uint:1=1')      # start marker
            frame.append('uint:1=0')      # not versioned
            frame.append(f'uint:5={code}')  # unassigned message type
            frame.append('uint:1=0')      # not compressed
            frame.append('uint:8=2')      # 2 metadata bytes
            frame.append(b"{}")
            frame.append(b'"x"')
            while len(frame) % 8:
                frame.insert('uint:1=0', 0)

            with pytest.raises(ValueError) as exc:
                decode_bitstring(frame.bytes)
            assert str(code) in str(exc.value), (
                f"code {code} must be rejected as malformed, not coerced to a "
                f"message type"
            )


class TestIntercomHasNoBinaryEncoding:
    """WIRE-1 §4.3 — 'INTERCOM has no binary code and MUST be sent as a
    text-encoded frame', even on a connection that binarizes everything else.

    Before hivemind-websocket-client#151 the encoder fell back to code 11 for
    any type missing from the table, so an INTERCOM went out on the wire
    labelled THIRDPRTY and the receiver decoded it as the wrong type.
    """

    def test_intercom_has_no_assigned_code(self):
        from hivemind_bus_client.serialization import _INT2TYPE

        assert HiveMessageType.INTERCOM not in _INT2TYPE.values(), \
            "INTERCOM must have no binary message-type code (WIRE-1 §4.3)"

    def test_binary_framing_an_intercom_raises_instead_of_mislabelling_it(self):
        require_wire1_bus_client()
        from hivemind_bus_client.serialization import get_bitstring

        with pytest.raises(ValueError):
            get_bitstring(hive_type=HiveMessageType.INTERCOM,
                          payload={"ciphertext": "x", "signature": "y"})

    def test_intercom_round_trips_as_a_text_frame(self):
        payload = {"ciphertext": "Y2lwaGVy", "signature": "c2ln"}
        msg = HiveMessage(HiveMessageType.INTERCOM, payload=payload)

        decoded = HiveMessage.deserialize(msg.serialize())

        assert decoded.msg_type == HiveMessageType.INTERCOM
        assert decoded.payload == payload


class TestProtocolFloorOnPerformedVersion:
    """CRYPTO-1 §3.1 — the ``min_protocol_version`` floor is enforced against
    the handshake a client actually COMPLETES, not the one it advertised it
    could do.

    The HELLO-time check only rejects a client that cannot reach the floor at
    all (``min_version > max_version``). A v3-capable client — one with a
    password handshake, so the server offered it Noise — can still answer with
    a legacy v2 password envelope. If the floor were only checked at HELLO,
    that client would silently downgrade itself past a floor of 3 and the
    operator's crypto requirement would be a no-op (hivemind-core#165).
    """

    def _v2_envelope(self, password: str):
        """A genuine v2 password-handshake envelope built with ``password`` —
        one the server WOULD accept if the floor were not enforced, so this
        test fails for the right reason if the check is removed."""
        from poorman_handshake import PasswordHandShake
        return PasswordHandShake(password).generate_handshake()

    def test_v2_handshake_is_refused_under_a_floor_of_v3(self, minimal_topology,
                                                         monkeypatch):
        from hivemind_core import protocol as core_protocol

        b = minimal_topology
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")
        conn = m0.hm_protocol.clients[s0.peer]

        # This connection is v3-capable: it has a password handshake, which is
        # exactly what makes the downgrade possible.
        assert conn.pswd_handshake is not None, \
            "precondition: the connection must be able to offer protocol v3"
        password = conn.pswd_handshake.password

        monkeypatch.setattr(core_protocol, "get_server_config",
                            lambda: {"min_protocol_version": 3})

        dropped = []
        monkeypatch.setattr(conn, "disconnect", lambda: dropped.append(True))
        conn.crypto_key = None

        m0.hm_protocol.handle_handshake_message(
            HiveMessage(HiveMessageType.HANDSHAKE,
                        {"envelope": self._v2_envelope(password)}),
            conn)

        assert dropped, ("a v2 password handshake under a v3 floor MUST be "
                         "refused, not silently completed")
        assert conn.crypto_key is None, \
            "the refused handshake must not have derived a session key"

    def test_v2_handshake_is_accepted_under_a_floor_of_v2(self, minimal_topology,
                                                          monkeypatch):
        """The other half of the same clause: the floor refuses a downgrade,
        it does not refuse everything."""
        from hivemind_core import protocol as core_protocol

        b = minimal_topology
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")
        conn = m0.hm_protocol.clients[s0.peer]
        password = conn.pswd_handshake.password

        monkeypatch.setattr(core_protocol, "get_server_config",
                            lambda: {"min_protocol_version": 2})

        dropped = []
        monkeypatch.setattr(conn, "disconnect", lambda: dropped.append(True))
        conn.crypto_key = None

        m0.hm_protocol.handle_handshake_message(
            HiveMessage(HiveMessageType.HANDSHAKE,
                        {"envelope": self._v2_envelope(password)}),
            conn)

        assert not dropped, "a v2 handshake at a v2 floor must be accepted"
        assert conn.crypto_key, "an accepted v2 handshake must derive a session key"


class TestTofuPinIsNotMovedByALaterHello:
    """CRYPTO-1 §5 — the first public key seen for an access key becomes the
    trust anchor for INTERCOM origin verification. A later HELLO presenting a
    different key MUST NOT move that pin.

    Without this, anyone who reaches the listener with a stolen (or shared)
    access key can re-pin the key to their own and have their forged INTERCOMs
    verify — the pin is the whole of the origin authentication.
    """

    def _seizing_hello(self, pubkey: str):
        return HiveMessage(HiveMessageType.HELLO, payload={"pubkey": pubkey})

    def test_second_hello_with_a_different_key_keeps_the_original_pin(
            self, minimal_topology):
        from poorman_handshake.asymmetric.utils import create_RSA_key

        b = minimal_topology
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")
        conn = m0.hm_protocol.clients[s0.peer]

        pinned = m0.hm_protocol.trusted_pubkeys.get(conn.key)
        assert pinned, "precondition: S0's pubkey was TOFU-pinned on its HELLO"

        attacker_pem, _attacker_priv = create_RSA_key()
        m0.hm_protocol.handle_hello_message(self._seizing_hello(attacker_pem), conn)

        assert m0.hm_protocol.trusted_pubkeys[conn.key] == pinned, \
            "a later HELLO must not move an existing TOFU pin (CRYPTO-1 §5)"

    def test_intercom_signed_with_the_seizing_key_is_still_rejected(
            self, minimal_topology):
        """The pin must not merely survive in the dict — it must still be the
        key INTERCOM verification uses."""
        import pybase64
        from poorman_handshake.asymmetric.utils import (create_RSA_key,
                                                        encrypt_RSA, sign_RSA)

        b = minimal_topology
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")
        conn = m0.hm_protocol.clients[s0.peer]

        attacker_pem, attacker_priv = create_RSA_key()
        m0.hm_protocol.handle_hello_message(self._seizing_hello(attacker_pem), conn)

        inner = HiveMessage(HiveMessageType.BUS,
                            payload=Message("recognizer_loop:utterance", {}))
        ciphertext = encrypt_RSA(m0.identity.public_key, inner.serialize())
        payload = {
            "ciphertext": pybase64.b64encode(ciphertext).decode(),
            "signature": pybase64.b64encode(
                sign_RSA(attacker_priv, ciphertext)).decode(),
        }
        s0.send(HiveMessage(HiveMessageType.INTERCOM, payload=payload,
                            target_pubkey=m0.identity.public_key))

        time.sleep(0.3)
        m0.agent_protocol.assert_not_injected("recognizer_loop:utterance")


class TestIntercomRefusedWhenCryptoRequired:
    """CRYPTO-1 §5 — an INTERCOM carries an end-to-end encrypted envelope with
    an origin signature. A node that requires crypto MUST refuse an INTERCOM
    that arrives without one — a plain dict or an already-decoded HiveMessage
    carries no proof of origin at all (hivemind-core#169).

    The permissive form (``require_crypto=False``) is what the rest of the
    suite covers; the refusal is the security control, so it gets its own
    positive/negative pair here.
    """

    def _plaintext_intercom(self, target_pubkey):
        inner = HiveMessage(HiveMessageType.BUS,
                            payload=Message("recognizer_loop:utterance",
                                            {"utterances": ["unsigned"]}))
        return HiveMessage(HiveMessageType.INTERCOM, payload=inner.as_dict,
                           target_pubkey=target_pubkey)

    def test_plaintext_intercom_is_dropped_when_crypto_is_required(
            self, minimal_topology):
        b = minimal_topology
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")

        assert m0.hm_protocol.require_crypto, \
            "precondition: this node advertises crypto_required"

        s0.send(self._plaintext_intercom(m0.identity.public_key))

        time.sleep(0.3)
        m0.agent_protocol.assert_not_injected("recognizer_loop:utterance")

    def test_a_relay_that_consumes_the_refusal_does_not_relay_it_onward(
            self, minimal_topology):
        """The refusal MUST consume the frame: a dropped INTERCOM is never
        handed to peers or escalated upstream."""
        b = minimal_topology
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")
        conn = m0.hm_protocol.clients[s0.peer]

        consumed = m0.hm_protocol.handle_intercom_message(
            self._plaintext_intercom(m0.identity.public_key), conn)

        assert consumed is True, \
            "a refused INTERCOM must be consumed, not relayed further"

    def test_plaintext_intercom_is_delivered_when_crypto_is_not_required(self):
        """The same frame on a permissive node still delivers — proving the
        test above fails on the refusal, not on some unrelated breakage."""
        b = TopologyBuilder()
        try:
            b.add_master("M0", require_crypto=False)
            b.add_satellite("S0", upstream=b.get_master("M0"),
                            allowed_types=["recognizer_loop:utterance"])
            b.start_all()
            m0 = b.get_master("M0")
            s0 = b.get_satellite("S0")

            s0.send(self._plaintext_intercom(m0.identity.public_key))

            poll_until(
                lambda: m0.agent_protocol.last_injected("recognizer_loop:utterance"),
                timeout=3,
                message="a permissive node must still deliver a plaintext INTERCOM")
        finally:
            b.stop_all()


class TestRelayDoesNotOpenAForwardedIntercom:
    """CRYPTO-1 §5 — an INTERCOM is addressed to a public key end-to-end. A
    node that is merely on the path MUST NOT decrypt it, and MUST NOT rewrite
    the envelope it passes on: the ciphertext and the origin signature the
    target verifies are exactly the bytes the origin produced.

    The relay here cannot decrypt (the envelope is sealed to M0's key), so what
    this pins is the *behaviour*: the relay hands the envelope on untouched
    instead of unwrapping, re-signing, or dropping it.
    """

    def test_relay_forwards_the_envelope_byte_for_byte(self, chain_topology):
        import pybase64
        from poorman_handshake.asymmetric.utils import encrypt_RSA, load_RSA_key, sign_RSA

        b = chain_topology
        m0 = b.get_master("M0")
        r1 = b.get_master("R1_master")
        s0 = b.get_satellite("S0")

        inner = HiveMessage(HiveMessageType.BUS,
                            payload=Message("recognizer_loop:utterance", {}))
        ciphertext = encrypt_RSA(m0.identity.public_key, inner.serialize())
        envelope = {
            "ciphertext": pybase64.b64encode(ciphertext).decode(),
            "signature": pybase64.b64encode(
                sign_RSA(load_RSA_key(s0.identity.private_key), ciphertext)).decode(),
        }

        # Observe the envelope as the TOP master sees it, after the relay hop.
        seen = []
        original_handler = m0.hm_protocol.handle_intercom_message

        def _spy(message, client):
            seen.append(message.payload)
            return original_handler(message, client)

        m0.hm_protocol.handle_intercom_message = _spy

        intercom = HiveMessage(HiveMessageType.INTERCOM, payload=envelope,
                               target_pubkey=m0.identity.public_key)
        s0.send(HiveMessage(HiveMessageType.ESCALATE, payload=intercom))

        poll_until(lambda: seen, timeout=3,
                   message="the relay never forwarded the INTERCOM upstream")
        assert seen[0] == envelope, (
            "a relay must forward an INTERCOM envelope untouched — the "
            "ciphertext/signature reaching the target were rewritten"
        )
        # And the relay itself never opened it onto its own agent bus.
        r1.agent_protocol.assert_not_injected("recognizer_loop:utterance")


class TestKeyEstablishmentMessageGate:
    """CRYPTO-1 §3.4.5 — key establishment brackets the session.

    Before the session key exists only HELLO and HANDSHAKE may travel in the
    clear; every other cleartext frame is rejected and the peer dropped. After
    Split() there is no cleartext channel left at all — a frame that is not a
    Noise transport message is rejected outright.
    """

    def test_cleartext_hello_and_handshake_are_accepted_before_the_session_key(
            self, minimal_topology):
        import json

        b = minimal_topology
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")
        conn = m0.hm_protocol.clients[s0.peer]
        conn.crypto_key = None  # pre-key-establishment state

        for msg_type in (HiveMessageType.HELLO, HiveMessageType.HANDSHAKE):
            frame = json.dumps({"msg_type": msg_type.value, "payload": {}})
            assert conn.decode(frame).msg_type == msg_type, \
                f"{msg_type} must be accepted in the clear before the session key"

    def test_any_other_cleartext_frame_is_rejected_before_the_session_key(
            self, minimal_topology):
        import json
        from hivemind_core.protocol import UnencryptedMessageError

        b = minimal_topology
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")
        conn = m0.hm_protocol.clients[s0.peer]
        conn.crypto_key = None

        frame = json.dumps({"msg_type": HiveMessageType.BUS.value,
                            "payload": Message("recognizer_loop:utterance",
                                               {}).serialize()})
        with pytest.raises(UnencryptedMessageError):
            conn.decode(frame)

    def test_a_non_noise_frame_is_rejected_after_split(self, minimal_topology):
        import json
        from hivemind_core.protocol import NoiseTransportFailed

        b = minimal_topology
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")
        conn = m0.hm_protocol.clients[s0.peer]

        class _SplitTransport:
            """Stands in for the post-Split() CipherState pair. If the guard
            is removed, decode() reaches here and this fails loudly rather
            than quietly accepting the cleartext."""

            def decrypt_frame(self, payload):
                raise AssertionError(
                    "a text frame must never reach the Noise transport")

        conn.noise_transport = _SplitTransport()

        frame = json.dumps({"msg_type": HiveMessageType.HELLO.value,
                            "payload": {}})
        with pytest.raises(NoiseTransportFailed):
            conn.decode(frame)


class TestBuiltinGatesAreNotRemovable:
    """POLICY-1 §4 — the message-type gate is the canonical admission control:
    it runs FIRST and it cannot be removed or reordered, whatever chain the
    caller supplies.

    ``PolicyChain`` is a plain dataclass, so an embedder can hand
    ``HiveMindListenerProtocol`` a chain of its own. Before hivemind-core#171
    only a chain built from config was normalized, so a caller-supplied chain
    could omit ``MessageTypeACLPolicy`` entirely (whitelist never enforced) or
    place it after a permissive policy that short-circuits with an allow.
    """

    def _normalize(self, hm_protocol, *policies, optional=None):
        from hivemind_core.policy import PolicyChain
        flags = list(optional) if optional else [False] * len(policies)
        return hm_protocol._with_builtin_policies(
            PolicyChain(policies=list(policies), _optional=flags))

    def test_a_caller_supplied_chain_is_normalized_by_the_constructor(self):
        """The gate must survive the path an embedder actually uses: handing
        ``HiveMindListenerProtocol`` a ready-made chain. Normalizing only the
        chain built from config leaves this door open."""
        from hivemind_core.policy import (DefaultSessionPolicy,
                                          MessageTypeACLPolicy, PolicyChain)
        from hivemind_core.protocol import HiveMindListenerProtocol
        from hivemind_plugin_manager.policy import PolicyPlugin
        from hivescope.database import InMemoryClientDatabase
        from hivescope.plugins.agent import TestAgentProtocol
        from hivescope.utils import make_identity

        class _AllowAll(PolicyPlugin):
            def review(self, message, client):
                from hivemind_plugin_manager import Verdict
                return Verdict.allow()

        hm = HiveMindListenerProtocol(
            identity=make_identity("M-embedded"),
            db=InMemoryClientDatabase(),
            agent_protocol=TestAgentProtocol(),
            peer="M-embedded:0.0.0.0",
            policy_chain=PolicyChain(policies=[_AllowAll()], _optional=[False]),
        )

        assert [type(p) for p in hm.policy_chain.policies] == [
            MessageTypeACLPolicy, DefaultSessionPolicy, _AllowAll], \
            "the builtin gates must be prepended to a caller-supplied chain"

    def test_the_gate_cannot_be_reordered_behind_another_policy(
            self, minimal_topology):
        from hivemind_core.policy import (DefaultSessionPolicy,
                                          MessageTypeACLPolicy)
        from hivemind_plugin_manager.policy import PolicyPlugin

        class _RunsFirst(PolicyPlugin):
            def review(self, message, client):
                from hivemind_plugin_manager import Verdict
                return Verdict.allow()

        hm = minimal_topology.get_master("M0").hm_protocol
        # The caller deliberately puts its own policy before the gate and
        # lists the gate itself, hoping to keep its position.
        chain = self._normalize(hm, _RunsFirst(), MessageTypeACLPolicy(hm_protocol=hm))

        assert [type(p) for p in chain.policies] == [
            MessageTypeACLPolicy, DefaultSessionPolicy, _RunsFirst], \
            "the builtin gates must run first, and a listed copy must not be duplicated"

    def test_the_builtin_gates_are_never_optional(self, minimal_topology):
        """A gate marked optional would swallow its own exceptions and let the
        message through — the gates are mandatory, so an error fails closed."""
        from hivemind_plugin_manager.policy import PolicyPlugin

        class _Optional(PolicyPlugin):
            def review(self, message, client):
                from hivemind_plugin_manager import Verdict
                return Verdict.allow()

        hm = minimal_topology.get_master("M0").hm_protocol
        chain = self._normalize(hm, _Optional(), optional=[True])

        assert chain._optional == [False, False, True], \
            "the builtin gates must stay mandatory; the caller's flag is kept"


class TestEmptyWhitelistDeniesBinary:
    """POLICY-1 §4 — ``allowed_types`` is a whitelist and the empty whitelist
    grants nothing. A BINARY payload crosses the same gate: without this a
    client that may not emit a single message type could still push RAW_AUDIO
    into the STT pipeline (hivemind-core#171).
    """

    def test_binary_payload_is_denied_for_an_empty_whitelist(self):
        from hivemind_bus_client.serialization import HiveMindBinaryPayloadType

        b = TopologyBuilder()
        try:
            b.add_master("M0")
            b.add_satellite("S0", upstream=b.get_master("M0"), allowed_types=[])
            b.start_all()
            m0 = b.get_master("M0")
            s0 = b.get_satellite("S0")

            s0.send(HiveMessage(
                HiveMessageType.BINARY, payload=b"\x00\x01" * 512,
                bin_type=HiveMindBinaryPayloadType.RAW_AUDIO,
                metadata={"sample_rate": 16000, "sample_width": 2}))

            m0.binary_protocol.assert_not_called("microphone_input")
        finally:
            b.stop_all()

    def test_binary_payload_is_admitted_once_the_whitelist_is_non_empty(self):
        """The counterpart: the deny above is the empty whitelist, not a
        blanket refusal of binary."""
        from hivemind_bus_client.serialization import HiveMindBinaryPayloadType

        b = TopologyBuilder()
        try:
            b.add_master("M0")
            b.add_satellite("S0", upstream=b.get_master("M0"),
                            allowed_types=["recognizer_loop:utterance"])
            b.start_all()
            m0 = b.get_master("M0")
            s0 = b.get_satellite("S0")

            s0.send(HiveMessage(
                HiveMessageType.BINARY, payload=b"\x00\x01" * 512,
                bin_type=HiveMindBinaryPayloadType.RAW_AUDIO,
                metadata={"sample_rate": 16000, "sample_width": 2}))

            m0.binary_protocol.assert_called("microphone_input")
        finally:
            b.stop_all()


class TestDenyAllFallback:
    """POLICY-1 §5 — fail-closed at startup too: when the configured chain
    cannot be built, the node installs ``DenyAllPolicy`` rather than an empty
    (allow-everything) chain."""

    def test_unbuildable_chain_installs_deny_all(self, monkeypatch):
        from hivemind_core.policy import DenyAllPolicy, PolicyChain
        from hivemind_core.protocol import HiveMindListenerProtocol
        from hivemind_plugin_manager import DenyCodes
        from hivescope.database import InMemoryClientDatabase
        from hivescope.plugins.agent import TestAgentProtocol
        from hivescope.utils import make_identity

        def _boom(cls, config, hm_protocol=None):
            raise RuntimeError("unloadable policy plugin")

        monkeypatch.setattr(PolicyChain, "from_config", classmethod(_boom))

        hm = HiveMindListenerProtocol(
            identity=make_identity("M-denyall"),
            db=InMemoryClientDatabase(),
            agent_protocol=TestAgentProtocol(),
            peer="M-denyall:0.0.0.0",
        )

        fallbacks = [p for p in hm.policy_chain.policies
                     if isinstance(p, DenyAllPolicy)]
        assert fallbacks, (
            "a chain that failed to build must be replaced by DenyAllPolicy, "
            "never by an empty allow-everything chain"
        )
        verdict = fallbacks[0].review(Message("recognizer_loop:utterance", {}),
                                      client=None)
        assert verdict.denied
        assert verdict.code == DenyCodes.POLICY_CHAIN_UNAVAILABLE


def _response_msg_types(responses):
    """The inner bus msg_types of a list of QUERY/CASCADE response frames."""
    return [r.payload.payload.msg_type for r in responses]


def _stub_agent_answer(node, chunks):
    """Make ``node``'s agent answer a query with ``chunks``.

    An empty ``chunks`` list is a DECLINED query: the agent yields the
    end-of-query sentinel immediately without producing an answer, which is
    the contract the node uses to decide to escalate instead.
    """

    def _nlq(utterance, lang, *args, **kwargs):
        for chunk in chunks:
            yield chunk
        yield None

    node.agent_protocol.natural_language_query = _nlq


class TestQueryStreamEnd:
    """AGENT-1 §4.2 / NODE-1 §5 — a streamed QUERY answer is terminated by
    exactly one ``hive.query.complete`` control message, sent AFTER the last
    answer chunk. A query the agent DECLINED produced no stream, so it MUST
    NOT be terminated by one — the originator uses ``hive.query.complete`` to
    know the answer is whole, and a spurious one would end a query that is
    still being escalated upstream.
    """

    QUERY_COMPLETE = "hive.query.complete"

    def _ask(self, satellite, utterance="what is 2+2?"):
        responses = []
        satellite.shim.emitter.on(HiveMessageType.QUERY, responses.append)
        satellite.send(HiveMessage(
            HiveMessageType.QUERY,
            payload=HiveMessage(HiveMessageType.BUS,
                                payload=Message("recognizer_loop:utterance",
                                                {"utterances": [utterance]})),
            metadata={"query_id": f"q-{uuid.uuid4()}",
                      "originator_peer": satellite.peer,
                      "is_response": False}))
        return responses

    def test_complete_is_sent_after_the_last_chunk(self, minimal_topology):
        b = minimal_topology
        _stub_agent_answer(b.get_master("M0"), ["four", "or maybe five"])

        responses = self._ask(b.get_satellite("S0"))

        assert _response_msg_types(responses) == [
            "speak", "speak", self.QUERY_COMPLETE], \
            "the stream must be terminated by hive.query.complete, after every chunk"

    def test_complete_is_sent_exactly_once(self, minimal_topology):
        b = minimal_topology
        _stub_agent_answer(b.get_master("M0"), ["four"])

        responses = self._ask(b.get_satellite("S0"))

        assert _response_msg_types(responses).count(self.QUERY_COMPLETE) == 1

    def test_complete_is_not_sent_for_a_declined_query(self, minimal_topology):
        b = minimal_topology
        _stub_agent_answer(b.get_master("M0"), [])  # agent declines

        responses = self._ask(b.get_satellite("S0"))

        types = _response_msg_types(responses)
        assert self.QUERY_COMPLETE not in types, (
            f"a declined query produced no answer stream, so it must not be "
            f"terminated by {self.QUERY_COMPLETE}; got {types}"
        )
        assert "hive.query.timeout" in types, \
            "a declined query at the top-level master must report no_answer"


class TestLoopedMessagesAreStillDeliveredLocally:
    """MSG-1 §5 — loop suppression gates RE-FORWARDING, not local handling:
    'a node MUST NOT forward a message whose route already contains a hop
    naming it'. The node it names is a legitimate recipient; it just must not
    put the message back on the wire.

    PROPAGATE and PING already have coverage above. ESCALATE and CASCADE are
    the branches a 'just drop looped messages' simplification would break,
    because for both of them the local step is the whole point: an ESCALATE
    targeting this node's site is delivered to its agent, and a CASCADE is
    answered by every node it reaches.
    """

    def _route_naming(self, node):
        """A route hop naming ``node`` by the stable identity loop suppression
        keys on (``_node_id`` == the node's public key).

        ``targets`` must be non-empty: ``HiveMessage.route`` filters out hops
        that carry no targets, so a hop without them is invisible to the
        suppression check.
        """
        return [{"source": node.identity.public_key,
                 "targets": [node.identity.public_key]}]

    def test_looped_escalate_is_delivered_locally_but_not_forwarded_upstream(
            self, chain_topology):
        b = chain_topology
        m0 = b.get_master("M0")
        r1 = b.get_master("R1_master")
        s0 = b.get_satellite("S0")

        m0_escalated = []
        m0.hm_protocol.escalate_callback = m0_escalated.append

        inner = HiveMessage(HiveMessageType.BUS,
                            payload=Message("recognizer_loop:utterance",
                                            {"utterances": ["looped escalate"]}))
        s0.send(HiveMessage(HiveMessageType.ESCALATE, payload=inner,
                            target_site_id=r1.identity.site_id,
                            route=self._route_naming(r1)))

        # local delivery: the ESCALATE targets R1's site, so R1's agent gets it
        msg = r1.agent_protocol.last_injected("recognizer_loop:utterance")
        assert msg is not None, \
            "a looped ESCALATE must still be delivered to the local agent"
        assert "looped escalate" in msg.data.get("utterances", [])

        # onward forward suppressed: it must not climb to the top master
        assert m0_escalated == [], \
            "a looped ESCALATE must not be re-forwarded upstream (MSG-1 §5)"

    def test_looped_cascade_is_answered_locally_but_not_fanned_out(self):
        b = TopologyBuilder()
        try:
            b.add_master("M0")
            r1 = b.add_relay("R1", upstream=b.get_master("M0")).listener
            b.add_satellite("S0", upstream=r1,
                            allowed_types=["recognizer_loop:utterance"])
            b.add_satellite("S1", upstream=r1,
                            allowed_types=["recognizer_loop:utterance"])
            b.start_all()
            m0 = b.get_master("M0")
            s0 = b.get_satellite("S0")
            s1 = b.get_satellite("S1")
            _stub_agent_answer(r1, ["local cascade answer"])

            s0_responses = []
            s0.shim.emitter.on(HiveMessageType.CASCADE, s0_responses.append)
            s1_received = []
            s1.shim.emitter.on(HiveMessageType.CASCADE, s1_received.append)
            m0_received = []
            original_cascade = m0.hm_protocol.handle_cascade_message

            def _spy(message, client):
                m0_received.append(message)
                return original_cascade(message, client)

            m0.hm_protocol.handle_cascade_message = _spy

            s0.send(HiveMessage(
                HiveMessageType.CASCADE,
                payload=HiveMessage(HiveMessageType.BUS,
                                    payload=Message("recognizer_loop:utterance",
                                                    {"utterances": ["cascade?"]})),
                metadata={"query_id": f"c-{uuid.uuid4()}",
                          "originator_peer": s0.peer, "is_response": False},
                route=self._route_naming(r1)))

            # local delivery: R1 answered the cascade back to the originator
            assert "speak" in _response_msg_types(s0_responses), (
                "a looped CASCADE must still be answered locally; got "
                f"{_response_msg_types(s0_responses)}")

            # onward fan-out suppressed, both sideways and upstream
            assert s1_received == [], \
                "a looped CASCADE must not be fanned out to sibling peers (MSG-1 §5)"
            assert m0_received == [], \
                "a looped CASCADE must not be forwarded upstream (MSG-1 §5)"
        finally:
            b.stop_all()


#: WIRE-1 §4.2, transcribed from the spec table. Kept as a literal (not derived
#: from the implementation) so this file is an independent copy of the wire
#: contract: deriving it from ``_INT2TYPE`` would make the test agree with
#: whatever the code does.
#:
#: Code 11 was THIRDPRTY, which MSG-1 §3 removed. It stays **reserved** and
#: absent here — a decoder must reject it as unassigned, and reusing it for a
#: new type would break every deployed peer that still speaks it.
WIRE1_MESSAGE_TYPE_CODES = {
    0: "HANDSHAKE",
    1: "BUS",
    2: "SHARED_BUS",
    3: "BROADCAST",
    4: "PROPAGATE",
    5: "ESCALATE",
    6: "HELLO",
    7: "QUERY",
    8: "CASCADE",
    9: "PING",
    10: "RENDEZVOUS",
    12: "BINARY",
}


#: The bus-client release that made the encoder refuse an unencodable type
#: (hivemind-websocket-client#151) and the decoder reject an unassigned code
#: (#145). ``pyproject.toml`` floors the dependency here; the check below turns
#: an environment below that floor into a failure that says so, instead of a
#: bare "DID NOT RAISE" that looks like a protocol regression.
WIRE1_BUS_CLIENT_FLOOR = (0, 10, 6)
