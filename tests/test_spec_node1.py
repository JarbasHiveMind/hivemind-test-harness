"""
HIVEMIND-NODE-1 conformance — the node-role and traversal MUSTs that had no
pinning test.

Covered here:

  * §2   — the role table is descriptive; no wire field carries a role
  * §3.3 — a node MUST NOT be required to run the POLICY-1 chain over traffic
           it only forwards
  * §3.4 — BROADCAST is exempt from loop prevention (one hop, cannot loop)
  * §4   — a node MUST honour the traversal direction of a routing message and
           MUST NOT widen it
  * §4   — HELLO MUST NOT be relayed

Deliberately NOT covered here:

  * §4 the flood hop-count ceiling — unimplemented, and the spec scopes it as a
    SHOULD for dense meshes nobody deploys.
  * §5.5 the originator's query timeout — unimplemented (a real gap; QUERY has
    no timer, only CASCADE does). A test would pin the absence, not a MUST.
"""
import time

import pytest
from hivemind_bus_client.message import HiveMessage, HiveMessageType
from hivemind_core.protocol import HiveMindClientConnection, HiveMindNodeType
from hivemind_plugin_manager.policy import Verdict
from ovos_bus_client.message import Message

from hivescope.topology import TopologyBuilder

from tests.conftest import poll_until

UTTERANCE = "recognizer_loop:utterance"


# ---------------------------------------------------------------------------
# NODE-1 §2 — roles are descriptive
# ---------------------------------------------------------------------------

class TestRolesAreDescriptiveOnly:
    """NODE-1 §2 — 'This table is descriptive... No wire field carries a role.'

    The role vocabulary is a way to talk about deployments, not an attribute a
    peer asserts about itself. The moment a role reaches the wire it becomes a
    self-declared capability claim, and a receiver that reads it is trusting a
    peer's word for what it is allowed to do — an authorization decision that
    belongs to the ACL, keyed on the access key.
    """

    def test_role_vocabulary_matches_the_spec_table(self):
        assert {t.name: t.value for t in HiveMindNodeType} == {
            "CANDIDATE_NODE": "candidate",
            "NODE": "node",
            "MIND": "mind",
            "FAKECROFT": "fakecroft",
            "SLAVE": "slave",
            "TERMINAL": "terminal",
            "BRIDGE": "bridge",
            "HIVE": "hive",
            "MASTER_MIND": "master",
        }

    def test_no_role_appears_in_any_frame_of_a_full_connection(self, minimal_topology):
        """The connection object carries a ``node_type`` placeholder, but no
        frame the node emits may expose it. Scanned over every frame of a
        complete connect + handshake + HELLO + a bus message."""
        b = minimal_topology
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")

        conn = m0.hm_protocol.clients[s0.peer]
        assert hasattr(conn, "node_type"), (
            "precondition: the connection is where the role placeholder lives")

        s0.send(Message(UTTERANCE, {"utterances": ["role check"]}))
        poll_until(lambda: m0.agent_protocol.last_injected(UTTERANCE), timeout=3,
                   message="the probe message never reached the agent bus")

        frames = [str(rec.payload) for rec in
                  m0.recorder.snapshot() + s0.recorder.snapshot()]
        assert frames, "precondition: some frames must have been recorded"
        for frame in frames:
            for forbidden in ("node_type", '"role"', "'role'"):
                assert forbidden not in frame, (
                    f"NODE-1 §2: a role reached the wire ({forbidden!r} in "
                    f"{frame[:200]}); roles are descriptive and MUST NOT be "
                    "carried by any wire field")


# ---------------------------------------------------------------------------
# NODE-1 §3.3 — forwarded traffic is not policy-reviewed
# ---------------------------------------------------------------------------

class _DenyEverythingSpy:
    """Stand-in policy chain that denies everything and records what it saw.

    Substituted for a relay's real chain so that "the relay did not review this"
    becomes observable: anything the relay *does* review is denied and stops
    dead, so a forwarded message that still arrives upstream proves the chain
    was never consulted for it.
    """

    def __init__(self):
        self.reviewed = []

    def review(self, message, client):
        self.reviewed.append(getattr(message, "msg_type", str(message)))
        return Verdict.deny("test_deny_all", "spy chain denies everything")

    def review_binary(self, payload, client):
        self.reviewed.append("binary")
        return Verdict.deny("test_deny_all", "spy chain denies everything")

    def observe(self, message, client):
        pass


@pytest.fixture
def relay_chain():
    """M0 ─ R1 (relay) ─ S0, every hop allowed to carry utterances."""
    b = TopologyBuilder()
    try:
        b.add_master("M0")
        relay = b.add_relay("R1", upstream=b.get_master("M0"),
                            allowed_types=[UTTERANCE])
        b.add_satellite("S0", upstream=relay.listener, allowed_types=[UTTERANCE])
        b.start_all()
        yield b
    finally:
        b.stop_all()


class TestForwardedTrafficSkipsThePolicyChain:
    """NODE-1 §3.3 — 'A node MUST NOT be required to run the POLICY-1 chain
    over traffic it only forwards.'

    A relay is not the admission point for traffic destined elsewhere. If it
    ran its own chain over everything passing through, a mesh would silently
    become an AND of every intermediate node's whitelist: adding a relay in the
    middle of a working path would break it, and the loss would be invisible to
    both endpoints.
    """

    def test_a_relay_forwards_upstream_even_when_its_own_chain_denies_everything(
            self, relay_chain):
        b = relay_chain
        m0 = b.get_master("M0")
        relay = b.get_relay("R1")
        s0 = b.get_satellite("S0")

        spy = _DenyEverythingSpy()
        relay.listener.hm_protocol.policy_chain = spy

        before = len(m0.recorder.snapshot())
        inner = HiveMessage(HiveMessageType.BUS,
                            payload=Message(UTTERANCE, {"utterances": ["pass through"]}))
        s0.send(HiveMessage(HiveMessageType.PROPAGATE, payload=inner))

        arrived = poll_until(
            lambda: [r for r in m0.recorder.snapshot()[before:]
                     if r.direction == "in"
                     and r.msg_type == HiveMessageType.PROPAGATE],
            timeout=5,
            message="NODE-1 §3.3: the relay ran its admission chain over "
                    "traffic it was only forwarding, so the message never "
                    "reached the node upstream")
        assert arrived

    def test_the_same_relay_does_review_traffic_addressed_to_it(self, relay_chain):
        """Control: the spy really is wired in. A plain BUS message is the
        relay's *own* inbound traffic and MUST go through the chain — so this
        one is denied and never injected."""
        b = relay_chain
        relay = b.get_relay("R1")
        s0 = b.get_satellite("S0")

        spy = _DenyEverythingSpy()
        relay.listener.hm_protocol.policy_chain = spy

        s0.send(Message(UTTERANCE, {"utterances": ["admit me"]}))

        poll_until(lambda: spy.reviewed, timeout=3,
                   message="the relay did not review a message addressed to it")
        relay.listener.agent_protocol.assert_not_injected(UTTERANCE)


# ---------------------------------------------------------------------------
# NODE-1 §3.4 — BROADCAST is exempt from loop prevention
# ---------------------------------------------------------------------------

class TestBroadcastIsExemptFromLoopSuppression:
    """NODE-1 §3.4 — 'BROADCAST is exempt: it travels one hop downstream and
    cannot loop.'

    Route-based loop suppression is correct for PROPAGATE/ESCALATE/CASCADE/PING
    and wrong for BROADCAST. A BROADCAST's route legitimately names nodes it
    has already been through — the inner envelope of a relayed BROADCAST
    carries the accumulated route — so applying the loop gate here would make a
    relay stop broadcasting to its own leaves.
    """

    def test_broadcast_reaches_the_leaf_even_when_the_route_already_names_the_relay(
            self, relay_chain):
        b = relay_chain
        m0 = b.get_master("M0")
        relay = b.get_relay("R1")
        s0 = b.get_satellite("S0")

        received = []
        s0.shim.emitter.on(HiveMessageType.BROADCAST, received.append)

        inner = HiveMessage(HiveMessageType.BUS,
                            payload=Message("test.broadcast.exempt", {"n": 1}))
        msg = HiveMessage(HiveMessageType.BROADCAST, payload=inner)
        # Pre-seed the route with a hop naming the relay itself. For a
        # PROPAGATE this is exactly the condition that suppresses forwarding.
        msg.replace_route([
            {"source": relay.listener.hm_protocol._node_id,
             "targets": [s0.peer]},
        ])
        m0.send_to_all(msg)

        poll_until(lambda: received, timeout=3,
                   message="NODE-1 §3.4: BROADCAST was loop-suppressed at the "
                           "relay; it is one-hop and exempt, so the leaf must "
                           "still receive it")


# ---------------------------------------------------------------------------
# NODE-1 §4 — traversal direction
# ---------------------------------------------------------------------------

@pytest.fixture
def two_satellite_star():
    b = TopologyBuilder()
    try:
        b.add_master("M0")
        b.add_satellite("S0", upstream=b.get_master("M0"),
                        allowed_types=[UTTERANCE])
        b.add_satellite("S1", upstream=b.get_master("M0"),
                        allowed_types=[UTTERANCE])
        b.start_all()
        yield b
    finally:
        b.stop_all()


class TestTraversalDirectionIsNotWidened:
    """NODE-1 §4 — 'A node MUST honour the traversal direction of a routing
    message and MUST NOT widen it.'

    Each type names an axis: ESCALATE upstream only, BROADCAST one hop
    downstream, PROPAGATE both. Widening is the dangerous direction: an
    ESCALATE that also fanned out downstream would deliver a message a
    satellite sent *up in confidence* to all of its siblings, and would do it
    without any of them having asked. The existing suite pins that each axis
    reaches where it should; nothing pinned that it reaches nowhere else.
    """

    def test_escalate_is_not_fanned_out_to_siblings(self, two_satellite_star):
        b = two_satellite_star
        m0 = b.get_master("M0")
        s0, s1 = b.get_satellite("S0"), b.get_satellite("S1")

        sibling_got = []
        s1.shim.emitter.on(HiveMessageType.ESCALATE, sibling_got.append)
        s1.shim.emitter.on(HiveMessageType.BROADCAST, sibling_got.append)
        s1.shim.emitter.on(HiveMessageType.PROPAGATE, sibling_got.append)

        inner = HiveMessage(HiveMessageType.BUS,
                            payload=Message(UTTERANCE, {"utterances": ["upward only"]}))
        s0.send(HiveMessage(HiveMessageType.ESCALATE, payload=inner,
                            target_site_id=m0.identity.site_id))

        # The escalation did arrive and was handled at the top of the chain...
        poll_until(lambda: m0.agent_protocol.last_injected(UTTERANCE), timeout=5,
                   message="the ESCALATE never reached the top of the chain")
        # ...and MUST NOT have leaked sideways to the sibling on the way.
        assert sibling_got == [], (
            "NODE-1 §4: an ESCALATE was widened into a downstream fan-out; "
            f"the sibling received {[m.msg_type for m in sibling_got]}")

    def test_a_relay_forwards_an_escalate_as_an_escalate(self, relay_chain):
        """The envelope type IS the traversal axis. A relay that re-wrapped an
        ESCALATE as a PROPAGATE would widen it one hop later, at a node that
        has no way to know the axis was changed."""
        b = relay_chain
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")

        before = len(m0.recorder.snapshot())
        inner = HiveMessage(HiveMessageType.BUS,
                            payload=Message(UTTERANCE, {"utterances": ["axis check"]}))
        s0.send(HiveMessage(HiveMessageType.ESCALATE, payload=inner))

        inbound = poll_until(
            lambda: [r for r in m0.recorder.snapshot()[before:]
                     if r.direction == "in"],
            timeout=5,
            message="nothing was forwarded upstream at all")
        assert [r.msg_type for r in inbound] == [HiveMessageType.ESCALATE], (
            "NODE-1 §4: the relay changed the traversal axis when forwarding; "
            f"upstream saw {[r.msg_type for r in inbound]}")


# ---------------------------------------------------------------------------
# NODE-1 §4 — HELLO is not relayed
# ---------------------------------------------------------------------------

class TestHelloIsNotRelayed:
    """NODE-1 §4 — 'HELLO MUST NOT be relayed.'

    HELLO is a statement about *this connection*: the pubkey it carries is
    TOFU-pinned by the node on the other end of that connection and becomes the
    trust anchor for INTERCOM signature verification (CRYPTO-1 §5). Relaying
    one would hand an upstream node a pin for a key it has no connection to,
    attributed to the relay's own access key — a downstream peer could then
    plant the anchor used to verify messages it did not send.
    """

    def test_a_leafs_hello_stops_at_its_own_master(self, relay_chain):
        b = relay_chain
        m0 = b.get_master("M0")
        relay = b.get_relay("R1")
        s0 = b.get_satellite("S0")

        before = len(m0.recorder.snapshot())
        pinned_before = dict(m0.hm_protocol.trusted_pubkeys)

        s0.send(HiveMessage(HiveMessageType.HELLO, payload={
            "pubkey": s0.identity.public_key,
            "site_id": s0.identity.site_id,
        }))

        # The relay itself processed it (its own downstream connection).
        assert relay.listener.hm_protocol.trusted_pubkeys.get(s0._connection.key) \
            == s0.identity.public_key, \
            "precondition: the leaf's own master must have handled the HELLO"

        # Give a (bug-induced) relay time to forward before asserting the
        # negative, rather than passing by racing it.
        time.sleep(0.3)
        relayed = [r for r in m0.recorder.snapshot()[before:]
                   if r.direction == "in" and r.msg_type == HiveMessageType.HELLO]
        assert relayed == [], (
            "NODE-1 §4: a leaf's HELLO was relayed upstream; the top node "
            "would pin a key for a connection it does not have")
        assert m0.hm_protocol.trusted_pubkeys == pinned_before, (
            "NODE-1 §4: a relayed HELLO moved a pin on a node that never "
            "spoke to the peer that sent it")
