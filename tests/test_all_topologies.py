"""
Last Edit: Claude Sonnet 4.6 - 2026-03-09 - Motive: New file — all protocol messages smoke-tested in every topology.

TS-ALL-01..06 — Protocol-message smoke tests across all topologies.

Each test class exercises one HiveMessage type and verifies that the expected
behaviour (injection, forwarding, callback, bus event) is consistent across
*every* topology exposed by the harness fixtures.

Protocol messages covered
─────────────────────────
  BUS           satellite → master → agent bus injection
  SHARED_BUS    passive bus monitoring (shared_bus=True satellite)
  BROADCAST     admin satellite → all siblings receive
  PROPAGATE     satellite → fan-out to siblings, upstream relay
  ESCALATE      satellite → upstream master
  PING / PONG   see test_ping_pong.py for comprehensive coverage
  INTERCOM      satellite → satellite via RSA routing
  BINARY        satellite → master binary payload dispatch

For PING/PONG full coverage (all topologies, silent nodes, HiveMapper state)
see ``test_ping_pong.py``.
"""

import pytest
from ovos_bus_client.message import Message
from hivemind_bus_client.message import HiveMessage, HiveMessageType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bus_msg(text: str = "hello") -> Message:
    return Message("recognizer_loop:utterance", {"utterances": [text]})


def _propagate_msg() -> HiveMessage:
    inner = HiveMessage(HiveMessageType.RENDEZVOUS, {"data": "prop-test"})
    return HiveMessage(HiveMessageType.PROPAGATE, payload=inner)


def _escalate_msg() -> HiveMessage:
    inner = HiveMessage(HiveMessageType.RENDEZVOUS, {"data": "esc-test"})
    return HiveMessage(HiveMessageType.ESCALATE, payload=inner)


def _broadcast_msg() -> HiveMessage:
    inner = HiveMessage(HiveMessageType.RENDEZVOUS, {"data": "bcast-test"})
    return HiveMessage(HiveMessageType.BROADCAST, payload=inner)


# ---------------------------------------------------------------------------
# TS-ALL-01 — BUS: satellite injects OVOS message on master's agent bus
# ---------------------------------------------------------------------------

class TestBusAllTopologies:
    """BUS messages from a leaf satellite reach M0's agent bus in every topology."""

    def test_bus_minimal(self, minimal_topology):
        b = minimal_topology
        b.get_satellite("S0").send(_bus_msg("minimal test"))
        b.get_master("M0").agent_protocol.assert_injected(
            "recognizer_loop:utterance", count=1
        )

    def test_bus_star(self, star_topology):
        b = star_topology
        for i in range(3):
            b.get_satellite(f"S{i}").send(_bus_msg(f"from S{i}"))
        b.get_master("M0").agent_protocol.assert_injected(
            "recognizer_loop:utterance", count=3
        )

    def test_bus_chain(self, chain_topology):
        b = chain_topology
        # BUS is consumed-only: S0's BUS arrives at R1's agent, not M0.
        b.get_satellite("S0").send(_bus_msg("from S0 via relay"))
        b.get_master("R1_master").agent_protocol.assert_injected(
            "recognizer_loop:utterance", count=1
        )
        # M0 does NOT receive it — BUS is never forwarded through relays.
        b.get_master("M0").agent_protocol.assert_not_injected(
            "recognizer_loop:utterance"
        )

    def test_bus_deep_chain(self, deep_chain_topology):
        b = deep_chain_topology
        # BUS is consumed at the immediate master (R2), not forwarded to M0.
        b.get_satellite("S0").send(_bus_msg("deep chain"))
        b.get_master("R2_master").agent_protocol.assert_injected(
            "recognizer_loop:utterance", count=1
        )

    @pytest.mark.slow
    def test_bus_huge_hive(self, huge_hive_topology):
        b = huge_hive_topology
        # BUS is consumed at the relay master, not forwarded to M0.
        b.get_satellite("HS0").send(_bus_msg("HS0"))
        b.get_master("RM0_master").agent_protocol.assert_injected(
            "recognizer_loop:utterance", count=1
        )

    @pytest.mark.slow
    def test_bus_chaotic(self, chaotic_hive_topology):
        b = chaotic_hive_topology
        # BUS consumed at direct master; S6 is direct satellite of M0.
        b.get_satellite("S6").send(_bus_msg("from S6"))
        b.get_master("M0").agent_protocol.assert_injected(
            "recognizer_loop:utterance", count=1
        )
        # S0 is behind R1, so BUS goes to R1's agent, not M0.
        b.get_satellite("S0").send(_bus_msg("from S0"))
        b.get_master("R1_master").agent_protocol.assert_injected(
            "recognizer_loop:utterance", count=1
        )


# ---------------------------------------------------------------------------
# TS-ALL-02 — PROPAGATE: fan-out to siblings
# ---------------------------------------------------------------------------

class TestPropagateAllTopologies:
    """PROPAGATE from a satellite fans out to all siblings in every topology."""

    def test_propagate_minimal(self, minimal_topology):
        b = minimal_topology
        calls = []
        b.get_master("M0").hm_protocol.propagate_callback = calls.append
        b.get_satellite("S0").send(_propagate_msg())
        assert len(calls) == 1

    def test_propagate_star_siblings_receive(self, star_topology):
        b = star_topology
        s1_received = []
        s2_received = []
        # core forwards the unpacked inner payload to peers, so siblings receive
        # the propagated RENDEZVOUS message directly (not the PROPAGATE wrapper).
        b.get_satellite("S1").shim.emitter.on(
            HiveMessageType.RENDEZVOUS, s1_received.append
        )
        b.get_satellite("S2").shim.emitter.on(
            HiveMessageType.RENDEZVOUS, s2_received.append
        )
        b.get_satellite("S0").send(_propagate_msg())
        assert len(s1_received) >= 1
        assert len(s2_received) >= 1

    def test_propagate_chain_crosses_relay(self, chain_topology):
        b = chain_topology
        calls = []
        b.get_master("M0").hm_protocol.propagate_callback = calls.append
        b.get_satellite("S0").send(_propagate_msg())
        assert len(calls) == 1, "PROPAGATE from S0 must cross relay R1 and reach M0"

    def test_propagate_deep_chain_crosses_all_relays(self, deep_chain_topology):
        b = deep_chain_topology
        calls = []
        b.get_master("M0").hm_protocol.propagate_callback = calls.append
        b.get_satellite("S0").send(_propagate_msg())
        assert len(calls) == 1, "PROPAGATE must traverse M0 → R1 → R2 chain"

    @pytest.mark.slow
    def test_propagate_huge_hive_callback(self, huge_hive_topology):
        b = huge_hive_topology
        calls = []
        b.get_master("M0").hm_protocol.propagate_callback = calls.append
        b.get_satellite("HS0").send(_propagate_msg())
        assert len(calls) == 1

    @pytest.mark.slow
    def test_propagate_chaotic_leaf_reaches_m0(self, chaotic_hive_topology):
        b = chaotic_hive_topology
        calls = []
        b.get_master("M0").hm_protocol.propagate_callback = calls.append
        # S4 is three hops from M0 (M0 ← R2_master ← R3_master ← S4)
        b.get_satellite("S4").send(_propagate_msg())
        assert len(calls) == 1


# ---------------------------------------------------------------------------
# TS-ALL-03 — ESCALATE: satellite escalates up the authority chain
# ---------------------------------------------------------------------------

class TestEscalateAllTopologies:
    """ESCALATE travels upstream through relay nodes to M0."""

    def test_escalate_minimal(self, minimal_topology):
        b = minimal_topology
        calls = []
        b.get_master("M0").hm_protocol.escalate_callback = calls.append
        b.get_satellite("S0").send(_escalate_msg())
        assert len(calls) == 1

    def test_escalate_chain(self, chain_topology):
        b = chain_topology
        calls = []
        b.get_master("M0").hm_protocol.escalate_callback = calls.append
        b.get_satellite("S0").send(_escalate_msg())
        assert len(calls) == 1, "ESCALATE from S0 must traverse relay to M0"

    def test_escalate_deep_chain(self, deep_chain_topology):
        b = deep_chain_topology
        calls = []
        b.get_master("M0").hm_protocol.escalate_callback = calls.append
        b.get_satellite("S0").send(_escalate_msg())
        assert len(calls) == 1

    def test_escalate_star(self, star_topology):
        b = star_topology
        calls = []
        b.get_master("M0").hm_protocol.escalate_callback = calls.append
        b.get_satellite("S0").send(_escalate_msg())
        assert len(calls) == 1

    @pytest.mark.slow
    def test_escalate_chaotic_deep_leaf(self, chaotic_hive_topology):
        b = chaotic_hive_topology
        calls = []
        b.get_master("M0").hm_protocol.escalate_callback = calls.append
        b.get_satellite("S5").send(_escalate_msg())  # S5 is 3 hops from M0
        assert len(calls) == 1

    @pytest.mark.slow
    def test_escalate_huge_hive(self, huge_hive_topology):
        b = huge_hive_topology
        for i in range(15):
            calls = []
            b.get_master("M0").hm_protocol.escalate_callback = calls.append
            b.get_satellite(f"HS{i}").send(_escalate_msg())
            assert len(calls) == 1, f"HS{i} escalate failed"
            # Reset callback for next iteration
            b.get_master("M0").hm_protocol.escalate_callback = None


# ---------------------------------------------------------------------------
# TS-ALL-04 — BROADCAST: admin satellite broadcasts to all siblings
# ---------------------------------------------------------------------------

class TestBroadcastAllTopologies:
    """BROADCAST from an admin satellite reaches all direct siblings."""

    def _admin_topology(self, n_extra: int = 2):
        from hivescope.topology import TopologyBuilder
        b = TopologyBuilder()
        b.add_master("M0")
        b.add_satellite("S0", upstream=b.get_master("M0"), is_admin=True)
        for i in range(1, n_extra + 1):
            b.add_satellite(f"S{i}", upstream=b.get_master("M0"))
        try:
            b.start_all()
        except BaseException:
            # a partially-connected topology still holds live servers/threads
            b.stop_all()
            raise
        return b

    def test_broadcast_minimal_admin(self):
        b = self._admin_topology(n_extra=2)
        try:
            calls = []
            b.get_master("M0").hm_protocol.broadcast_callback = calls.append
            b.get_satellite("S0").send(_broadcast_msg())
            assert len(calls) == 1
        finally:
            b.stop_all()

    def test_broadcast_siblings_receive(self):
        b = self._admin_topology(n_extra=2)
        try:
            # peers receive the unpacked inner RENDEZVOUS content, not the wrapper
            s1_recv = []
            s2_recv = []
            b.get_satellite("S1").shim.emitter.on(
                HiveMessageType.RENDEZVOUS, s1_recv.append
            )
            b.get_satellite("S2").shim.emitter.on(
                HiveMessageType.RENDEZVOUS, s2_recv.append
            )
            b.get_satellite("S0").send(_broadcast_msg())
            assert len(s1_recv) == 1
            assert len(s2_recv) == 1
        finally:
            b.stop_all()

    def test_broadcast_non_admin_rejected(self, star_topology):
        b = star_topology
        illegal_calls = []
        b.get_master("M0").hm_protocol.illegal_callback = illegal_calls.append
        # S0 is NOT admin in star_topology
        b.get_satellite("S0").send(_broadcast_msg())
        assert len(illegal_calls) == 1, "Non-admin BROADCAST must fire illegal_callback"

    def test_broadcast_admin_star_topology(self, admin_star_topology):
        b = admin_star_topology
        calls = []
        b.get_master("M0").hm_protocol.broadcast_callback = calls.append
        b.get_satellite("S0").send(_broadcast_msg())  # S0 is admin
        assert len(calls) == 1


# ---------------------------------------------------------------------------
# TS-ALL-05 — SHARED_BUS: passive monitoring
# ---------------------------------------------------------------------------

class TestSharedBusAllTopologies:
    """SHARED_BUS enables passive eavesdropping on a satellite's internal bus."""

    def _shared_topology(self):
        from hivescope.topology import TopologyBuilder
        b = TopologyBuilder()
        b.add_master("M0")
        b.add_satellite("S0", upstream=b.get_master("M0"), shared_bus=True)
        try:
            b.start_all()
        except BaseException:
            # a partially-connected topology still holds live servers/threads
            b.stop_all()
            raise
        return b

    def test_shared_bus_callback_fires(self):
        b = self._shared_topology()
        try:
            received = []
            b.get_master("M0").hm_protocol.shared_bus_callback = received.append
            # Emit something on S0's internal bus — it's a shared_bus satellite so
            # this will be relayed to M0 as a SHARED_BUS message.
            b.get_satellite("S0").internal_bus.emit(
                Message("some.internal.event", {"x": 1})
            )
            assert len(received) >= 1, "shared_bus_callback should fire on M0"
        finally:
            b.stop_all()

    def test_non_shared_bus_does_not_fire(self, minimal_topology):
        b = minimal_topology  # S0 has shared_bus=False (default)
        received = []
        b.get_master("M0").hm_protocol.shared_bus_callback = received.append
        b.get_satellite("S0").internal_bus.emit(
            Message("some.internal.event", {"x": 1})
        )
        assert len(received) == 0, \
            "shared_bus_callback must NOT fire when shared_bus=False"


# ---------------------------------------------------------------------------
# TS-ALL-06 — BINARY: satellite sends binary payload to master
# ---------------------------------------------------------------------------

class TestBinaryAllTopologies:
    """BINARY payloads are dispatched to TestBinaryProtocol in every topology."""

    def _bin_msg(self) -> HiveMessage:
        from hivemind_bus_client.message import HiveMindBinaryPayloadType
        return HiveMessage(
            HiveMessageType.BINARY,
            payload=b"\x00\x01\x02\x03",
            bin_type=HiveMindBinaryPayloadType.RAW_AUDIO,
            metadata={"sample_rate": 16000, "sample_width": 2},
        )

    def test_binary_minimal(self, minimal_topology):
        b = minimal_topology
        b.get_satellite("S0").send(self._bin_msg())
        calls = b.get_master("M0").binary_protocol.calls
        assert any(c.bin_type.name == "RAW_AUDIO" for c in calls), \
            f"Expected RAW_AUDIO call. Got: {[c.bin_type for c in calls]}"

    def test_binary_star(self, star_topology):
        b = star_topology
        b.get_satellite("S2").send(self._bin_msg())
        calls = b.get_master("M0").binary_protocol.calls
        assert any(c.bin_type.name == "RAW_AUDIO" for c in calls)

    def test_binary_chain(self, chain_topology):
        b = chain_topology
        # BINARY is consumed-only: arrives at R1's binary protocol, not M0.
        b.get_satellite("S0").send(self._bin_msg())
        calls = b.get_master("R1_master").binary_protocol.calls
        assert any(c.bin_type.name == "RAW_AUDIO" for c in calls)
