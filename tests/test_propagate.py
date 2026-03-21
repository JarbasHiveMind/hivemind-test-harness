"""
TS-PROP-01..04 — PROPAGATE scenarios.
"""
import pytest
from ovos_bus_client.message import Message
from hivemind_bus_client.message import HiveMessage, HiveMessageType


def _propagate_msg():
    inner = HiveMessage(HiveMessageType.THIRDPRTY,
                        payload={"data": "propagate-payload"})
    return HiveMessage(HiveMessageType.PROPAGATE, payload=inner)


class TestPropagateFanOut:
    """TS-PROP-01 — PROPAGATE from one satellite fans out to all siblings."""

    def test_siblings_receive_propagate(self, star_topology):
        b = star_topology
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")
        s1 = b.get_satellite("S1")
        s2 = b.get_satellite("S2")

        s1_received = []
        s2_received = []
        s1.shim.emitter.on(HiveMessageType.PROPAGATE, s1_received.append)
        s2.shim.emitter.on(HiveMessageType.PROPAGATE, s2_received.append)

        s0.send(_propagate_msg())

        assert len(s1_received) == 1, "S1 should receive PROPAGATE"
        assert len(s2_received) == 1, "S2 should receive PROPAGATE"

    def test_sender_does_not_receive_own_propagate(self, star_topology):
        b = star_topology
        s0 = b.get_satellite("S0")

        s0_received = []
        s0.shim.emitter.on(HiveMessageType.PROPAGATE, s0_received.append)

        s0.send(_propagate_msg())

        assert len(s0_received) == 0, "Sender must not receive its own PROPAGATE"

    def test_propagate_callback_fires_on_master(self, star_topology):
        b = star_topology
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")

        prop_calls = []
        m0.hm_protocol.propagate_callback = prop_calls.append

        s0.send(_propagate_msg())

        assert len(prop_calls) == 1, "propagate_callback should fire on master"

    def test_propagate_to_master_noop_without_upstream(self, star_topology):
        """Top-level master has no upstream — propagate_to_master is a no-op."""
        b = star_topology
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")

        # M0 has no upstream bound, so propagate_to_master does nothing.
        # Verify no error and the callback still fires.
        calls = []
        m0.hm_protocol.propagate_callback = calls.append
        s0.send(_propagate_msg())
        assert len(calls) == 1, "propagate_callback must fire even without upstream"


class TestPropagateCannotPropagate:
    """TS-PROP-02 — satellite with can_propagate=False is rejected."""

    def test_illegal_propagate_fires_callback(self):
        from hivemind_test_harness.topology import TopologyBuilder
        b = TopologyBuilder()
        b.add_master("M0")
        b.add_satellite("S0", upstream=b.get_master("M0"), can_propagate=False)
        b.add_satellite("S1", upstream=b.get_master("M0"))
        b.start_all()

        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")
        s1 = b.get_satellite("S1")

        illegal_calls = []
        m0.hm_protocol.illegal_callback = illegal_calls.append

        s1_received = []
        s1.shim.emitter.on(HiveMessageType.PROPAGATE, s1_received.append)

        s0.send(_propagate_msg())

        assert len(illegal_calls) == 1, "illegal_callback should fire"
        assert len(s1_received) == 0, "PROPAGATE must not be forwarded"
        b.stop_all()


class TestPropagateChain:
    """TS-PROP-03 — PROPAGATE from leaf satellite crosses master boundary."""

    def test_propagate_crosses_relay(self, chain_topology):
        b = chain_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        m0_prop_calls = []
        m0.hm_protocol.propagate_callback = m0_prop_calls.append

        s0.send(_propagate_msg())

        assert len(m0_prop_calls) == 1, \
            "PROPAGATE should cross relay and reach top master"
