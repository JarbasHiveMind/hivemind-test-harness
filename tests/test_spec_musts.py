"""
Spec-MUST conformance tests — one focused test per protocol-spec MUST.

Each test names the spec clause it enforces and is either a real behavioural
assertion (the stack implements the MUST) or a ``xfail(strict=True)`` that
documents the exact missing capability (the stack does NOT implement it). A
strict xfail flips to a failure the moment the gap is closed, so an
unimplemented MUST can never quietly rot into false coverage.

Covered here (in-process, via the hivescope shim):

  * NODE-1 §3.4 / MSG-1 §5  — flood-loop suppression bounds a PING in a mesh
  * MSG-1 §5                — route-based loop suppression for generic PROPAGATE
                              (strict-xfail: not implemented — only the sender
                              hop is skipped, ``route`` is not consulted)
  * POLICY-1 §5             — fail-closed: a raising policy denies, never defaults open
  * POLICY-1 §4             — live whitelist mutation takes effect on the next message
  * WIRE-1 §4.2             — reserved binary codes 8/11 (strict-xfail: reused)
  * CRYPTO-1 §5             — INTERCOM signature verification (strict-xfail: TODO)
  * NODE-1 §3.3             — BROADCAST through a relay to a leaf (strict-xfail: gap)
  * cross-runtime matrix    — a BUS utterance round-trips on every node combo

The Noise-handshake MUSTs (CRYPTO-1 §3.4 identity pinning, KKpsk0 negotiation)
cannot run in-process — the shim completes only the legacy v2 handshake — so they
live in ``test_protocol_v3_noise.py`` against a real ``hivemind-core`` hub.
"""
import time
import uuid

import pytest
from ovos_bus_client.message import Message
from hivemind_bus_client.message import HiveMessage, HiveMessageType

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
    PING flood_id cache: it must bound *any* routed message, not only PINGs."""

    @pytest.mark.xfail(
        strict=True,
        reason="hivemind-core handle_propagate_message forwards to every peer "
               "except the immediate sender and never consults message.route, "
               "so route-based loop suppression (MSG-1 §5) is unimplemented for "
               "non-PING PROPAGATE; only PING is bounded, via _seen_flood_ids.",
    )
    def test_propagate_naming_peer_in_route_is_not_forwarded(self):
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

            # Craft a PROPAGATE whose route already names S1 → M0 MUST NOT
            # forward it to S1.
            inner = HiveMessage(HiveMessageType.BUS,
                                payload=Message("test.route.loop", {}))
            prop = HiveMessage(HiveMessageType.PROPAGATE, payload=inner,
                               route=[{"source": s1.peer}])
            s0.send(prop)

            assert received == [], \
                "PROPAGATE naming S1 in its route must not be forwarded to S1"
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
    """WIRE-1 §4.2 — codes 8 and 11 are RESERVED: a sender MUST NOT emit them,
    the registry MUST NOT reuse them, and a receiver MUST reject a frame
    carrying an unassigned/reserved value."""

    @pytest.mark.xfail(
        strict=True,
        reason="hivemind_bus_client.serialization._INT2TYPE reuses the reserved "
               "codes (8→CASCADE, 11→THIRDPRTY) and decode_bitstring defaults an "
               "unknown 5-bit value to 11 (THIRDPRTY) instead of rejecting it, "
               "violating WIRE-1 §4.2.",
    )
    def test_reserved_codes_are_not_assigned(self):
        from hivemind_bus_client.serialization import _INT2TYPE
        assert 8 not in _INT2TYPE, "code 8 is reserved and MUST NOT be assigned"
        assert 11 not in _INT2TYPE, "code 11 is reserved and MUST NOT be assigned"


# ---------------------------------------------------------------------------
# CRYPTO-1 §5 — INTERCOM signature verification
# ---------------------------------------------------------------------------

class TestIntercomSignatureVerification:
    """CRYPTO-1 §5 — an INTERCOM target that verifies a signature MUST reject a
    message whose signature does not verify, and deliver one that does."""

    @pytest.mark.xfail(
        strict=True,
        reason="hivemind-core does not implement INTERCOM signature verification: "
               "the signature field is a TODO (senders emit signature=b''), "
               "handle_intercom_message checks the outer INTERCOM msg_type so the "
               "decrypted inner is never dispatched, and there is no origin-"
               "authenticity check (CRYPTO-1 §5; hivemind-websocket-client#130).",
    )
    def test_valid_signed_intercom_delivers_inner(self, minimal_topology):
        import pybase64
        from poorman_handshake.asymmetric.utils import encrypt_RSA, load_RSA_key

        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        inner = HiveMessage(HiveMessageType.BUS,
                            payload=Message("recognizer_loop:utterance",
                                            {"utterances": ["signed intercom"]},
                                            context={"session": {
                                                "session_id": s0.shim.session_id}}))
        master_priv = load_RSA_key(m0.identity.private_key)
        ciphertext = encrypt_RSA(master_priv.publickey(),
                                 inner.serialize().encode("utf-8"))
        # A genuine origin signature would go here; the sender API leaves it empty.
        payload = {
            "ciphertext": pybase64.b64encode(ciphertext).decode(),
            "signature": pybase64.b64encode(b"").decode(),
        }
        s0.send(HiveMessage(HiveMessageType.INTERCOM, payload=payload,
                            target_pubkey=m0.identity.public_key))

        # The MUST: a verified INTERCOM delivers its inner BUS to the target.
        m0.agent_protocol.assert_injected("recognizer_loop:utterance")


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
