"""
TS-ESC-01..03 — ESCALATE scenarios.
"""
import pytest
from ovos_bus_client.message import Message
from hivemind_bus_client.message import HiveMessage, HiveMessageType


def _escalate_msg():
    inner = HiveMessage(HiveMessageType.RENDEZVOUS,
                        payload={"data": "escalate-payload"})
    return HiveMessage(HiveMessageType.ESCALATE, payload=inner)


class TestEscalateGoesUpstream:
    """TS-ESC-01 — ESCALATE from satellite goes to master only, not to siblings."""

    def test_escalate_reaches_master(self, minimal_topology):
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        escalate_calls = []
        m0.hm_protocol.escalate_callback = escalate_calls.append

        s0.send(_escalate_msg())

        assert len(escalate_calls) == 1, "Master escalate_callback should fire once"

    def test_escalate_does_not_loop_back_to_satellite(self, star_topology):
        b = star_topology
        s0 = b.get_satellite("S0")
        s1 = b.get_satellite("S1")

        s1_received = []
        s1.shim.emitter.on(HiveMessageType.ESCALATE, s1_received.append)

        s0.send(_escalate_msg())

        assert len(s1_received) == 0, "ESCALATE must not be forwarded to sibling satellites"

    def test_escalate_recorded_on_master(self, minimal_topology):
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        s0.send(_escalate_msg())

        m0.recorder.assert_received(HiveMessageType.ESCALATE, direction="in")


class TestEscalateRespectsCantEscalate:
    """TS-ESC-02 — satellite with can_escalate=False is rejected."""

    def test_illegal_escalate_fires_callback(self):
        from hivescope.topology import TopologyBuilder
        b = TopologyBuilder()
        try:
            b.add_master("M0")
            b.add_satellite("S0", upstream=b.get_master("M0"), can_escalate=False)
            b.start_all()

            m0 = b.get_master("M0")
            s0 = b.get_satellite("S0")

            illegal_calls = []
            m0.hm_protocol.illegal_callback = illegal_calls.append
            escalate_calls = []
            m0.hm_protocol.escalate_callback = escalate_calls.append

            s0.send(_escalate_msg())

            assert len(illegal_calls) == 1, "illegal_callback should fire"
            assert len(escalate_calls) == 0, "escalate_callback must not fire"
        finally:
            b.stop_all()


class TestEscalateChain:
    """TS-ESC-03 — ESCALATE climbs the full chain: S0 → R1 → M0."""

    def test_escalate_reaches_top_master(self, chain_topology):
        b = chain_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")
        r1_master = b.get_master("R1_master")

        m0_escalate_calls = []
        m0.hm_protocol.escalate_callback = m0_escalate_calls.append

        r1_escalate_calls = []
        r1_master.hm_protocol.escalate_callback = r1_escalate_calls.append

        s0.send(_escalate_msg())

        assert len(r1_escalate_calls) == 1, "Relay master should process ESCALATE"
        assert len(m0_escalate_calls) == 1, "Top master should also receive ESCALATE"

    def test_escalate_does_not_go_downstream(self, chain_topology):
        b = chain_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        # After escalate, no downstream ESCALATE should come back
        s0_received = []
        s0.shim.emitter.on(HiveMessageType.ESCALATE, s0_received.append)

        s0.send(_escalate_msg())

        assert len(s0_received) == 0, "ESCALATE must not be sent back downstream"
