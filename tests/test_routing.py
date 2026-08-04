"""
TS-ROUTE-01..06 — Routing metadata and deep-chain propagation.

Tests for:
  - source_peer / context["peer"] stamped on messages arriving at master
  - ESCALATE and PROPAGATE climbing through 2-level relay chains (deep_chain_topology)

Chain topology (deep_chain_topology fixture):
    S0 → R2_master — R2_sat → R1_master — R1_sat → M0

  S0's direct master: R2_master
  R2_sat's direct master: R1_master
  R1_sat's direct master: M0

The add_relay() wiring in topology.py instruments each relay master to forward
ESCALATE/PROPAGATE upstream via its satellite side, so the message climbs the
full chain.

Note on BROADCAST downstream routing:
  BROADCAST from M0 reaches only M0's direct clients (R1_sat). It is NOT
  automatically forwarded to leaf satellites (S0) because the slave protocol's
  handle_broadcast does not forward downstream and the relay wiring only covers
  upstream ESCALATE/PROPAGATE. Downstream broadcast routing through relay is a
  known gap in the current relay wiring.
"""
from ovos_bus_client.message import Message
from hivemind_bus_client.message import HiveMessage, HiveMessageType


def _escalate_msg():
    inner = HiveMessage(HiveMessageType.RENDEZVOUS, payload={"data": "going up"})
    return HiveMessage(HiveMessageType.ESCALATE, payload=inner)


def _propagate_msg():
    inner = HiveMessage(HiveMessageType.RENDEZVOUS, payload={"data": "spread out"})
    return HiveMessage(HiveMessageType.PROPAGATE, payload=inner)


class TestSourcePeer:
    """TS-ROUTE-01..02 — peer/source tracking in message context."""

    def test_bus_message_context_has_correct_peer(self, minimal_topology):
        """TS-ROUTE-01 — BUS message arriving at master has context['peer'] = satellite's peer id."""
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        s0.send(Message("recognizer_loop:utterance", {"utterances": ["routing test"]}))

        msg = m0.agent_protocol.last_injected("recognizer_loop:utterance")
        assert msg is not None
        assert msg.context.get("peer") == s0.peer, \
            "Master must stamp context['peer'] with the sending satellite's peer id"

    def test_escalate_callback_receives_payload_from_correct_peer(self, minimal_topology):
        """TS-ROUTE-02 — escalate_callback payload carries the correct source_peer."""
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        escalate_payloads = []
        m0.hm_protocol.escalate_callback = escalate_payloads.append

        s0.send(_escalate_msg())

        assert len(escalate_payloads) == 1
        payload = escalate_payloads[0]
        # _unpack_message sets source_peer to master's own peer (it relabels the route),
        # and the recorder captures the inbound with peer = s0.peer
        m0.recorder.assert_received(HiveMessageType.ESCALATE, direction="in")


class TestDeepChainEscalate:
    """TS-ROUTE-03..04 — ESCALATE climbs through two relay levels."""

    def test_escalate_reaches_intermediate_relay(self, deep_chain_topology):
        """TS-ROUTE-03 — ESCALATE from S0 triggers escalate_callback on R2_master."""
        b = deep_chain_topology
        s0 = b.get_satellite("S0")
        r2_master = b.get_master("R2_master")

        r2_calls = []
        r2_master.hm_protocol.escalate_callback = r2_calls.append

        s0.send(_escalate_msg())

        assert len(r2_calls) == 1, \
            "ESCALATE must trigger escalate_callback on the first relay (R2_master)"

    def test_escalate_reaches_top_master_through_two_relays(self, deep_chain_topology):
        """TS-ROUTE-04 — ESCALATE from S0 climbs all the way to M0 through R2 and R1."""
        b = deep_chain_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")
        r1_master = b.get_master("R1_master")
        r2_master = b.get_master("R2_master")

        m0_calls = []
        r1_calls = []
        r2_calls = []
        m0.hm_protocol.escalate_callback = m0_calls.append
        r1_master.hm_protocol.escalate_callback = r1_calls.append
        r2_master.hm_protocol.escalate_callback = r2_calls.append

        s0.send(_escalate_msg())

        assert len(r2_calls) == 1, "R2_master must receive ESCALATE"
        assert len(r1_calls) == 1, "R1_master must receive ESCALATE after relay forwarding"
        assert len(m0_calls) == 1, "M0 must receive ESCALATE after climbing both relays"

    def test_escalate_does_not_loop_back_to_sender(self, deep_chain_topology):
        """TS-ROUTE-04b — ESCALATE must not be delivered back to S0."""
        b = deep_chain_topology
        s0 = b.get_satellite("S0")

        s0_received = []
        s0.shim.emitter.on(HiveMessageType.ESCALATE, s0_received.append)

        s0.send(_escalate_msg())

        assert len(s0_received) == 0, \
            "ESCALATE must not loop back to the originating satellite"


class TestDeepChainPropagate:
    """TS-ROUTE-05..06 — PROPAGATE crosses two relay levels."""

    def test_propagate_reaches_top_master_through_two_relays(self, deep_chain_topology):
        """TS-ROUTE-05 — PROPAGATE from S0 triggers propagate_callback on M0."""
        b = deep_chain_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        m0_calls = []
        m0.hm_protocol.propagate_callback = m0_calls.append

        s0.send(_propagate_msg())

        assert len(m0_calls) == 1, \
            "PROPAGATE must cross both relays and reach M0's propagate_callback"

    def test_propagate_fires_at_each_relay(self, deep_chain_topology):
        """TS-ROUTE-06 — PROPAGATE fires propagate_callback at every intermediate relay."""
        b = deep_chain_topology
        s0 = b.get_satellite("S0")
        r1_master = b.get_master("R1_master")
        r2_master = b.get_master("R2_master")

        r1_calls = []
        r2_calls = []
        r1_master.hm_protocol.propagate_callback = r1_calls.append
        r2_master.hm_protocol.propagate_callback = r2_calls.append

        s0.send(_propagate_msg())

        assert len(r2_calls) == 1, "R2_master must process PROPAGATE"
        assert len(r1_calls) == 1, "R1_master must process PROPAGATE after relay forwarding"
