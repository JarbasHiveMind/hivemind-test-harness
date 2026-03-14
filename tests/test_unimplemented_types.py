"""
TS-STUB-01..05 — Unimplemented HiveMessage types.

QUERY, CASCADE, PING, and RENDEZVOUS are defined in HiveMessageType but have no
handler in HiveMindListenerProtocol. They fall through to handle_unknown_message(),
which is an empty stub (does nothing). These tests verify:
  1. Sending them does not crash the master.
  2. The master records the inbound message.

THIRDPRTY is used as an inner payload in PROPAGATE/ESCALATE/BROADCAST but is also
a valid top-level message type. As a top-level message it also falls to
handle_unknown_message().

TODO: When QUERY/CASCADE/PING/RENDEZVOUS are implemented, replace these no-crash
      tests with behavioral tests that verify the actual handler logic.
"""
import pytest
from ovos_bus_client.message import Message
from hivemind_bus_client.message import HiveMessage, HiveMessageType
from hivemind_test_harness.topology import TopologyBuilder


def _make_topology():
    b = TopologyBuilder()
    b.add_master("M0")
    b.add_satellite("S0", upstream=b.get_master("M0"))
    b.start_all()
    return b


class TestUnimplementedTypes:
    """TS-STUB-01..05 — unimplemented types must not crash the master."""

    def test_query_does_not_crash(self):
        """TS-STUB-01 — QUERY falls to handle_unknown_message (empty stub).
        TODO: implement QUERY handler (distributed query across hive)."""
        b = _make_topology()
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")

        msg = HiveMessage(HiveMessageType.QUERY,
                          payload={"query": "what is 2+2?"})
        s0.send(msg)

        m0.recorder.assert_received(HiveMessageType.QUERY, direction="in")
        b.stop_all()

    def test_cascade_does_not_crash(self):
        """TS-STUB-02 — CASCADE falls to handle_unknown_message (empty stub).
        TODO: implement CASCADE handler (cascading query responses)."""
        b = _make_topology()
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")

        # CASCADE payload must be a HiveMessage (same structure as PROPAGATE/ESCALATE/BROADCAST)
        inner = HiveMessage(HiveMessageType.THIRDPRTY, payload={"data": "cascade-data"})
        msg = HiveMessage(HiveMessageType.CASCADE, payload=inner)
        s0.send(msg)

        m0.recorder.assert_received(HiveMessageType.CASCADE, direction="in")
        b.stop_all()

    def test_ping_does_not_crash(self):
        """TS-STUB-03 — PING falls to handle_unknown_message (empty stub).
        TODO: implement PING handler (latency/alive check)."""
        b = _make_topology()
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")

        msg = HiveMessage(HiveMessageType.PING, payload={})
        s0.send(msg)

        m0.recorder.assert_received(HiveMessageType.PING, direction="in")
        b.stop_all()

    def test_rendezvous_does_not_crash(self):
        """TS-STUB-04 — RENDEZVOUS falls to handle_unknown_message (empty stub).
        TODO: implement RENDEZVOUS handler (peer discovery / hole-punching)."""
        b = _make_topology()
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")

        msg = HiveMessage(HiveMessageType.RENDEZVOUS,
                          payload={"peer": "some-peer-id"})
        s0.send(msg)

        m0.recorder.assert_received(HiveMessageType.RENDEZVOUS, direction="in")
        b.stop_all()

    def test_thirdprty_toplevel_does_not_crash(self):
        """TS-STUB-05 — THIRDPRTY as a top-level message falls to handle_unknown_message.
        THIRDPRTY is typically used as an inner payload inside PROPAGATE/ESCALATE,
        but is also a valid standalone message type."""
        b = _make_topology()
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")

        msg = HiveMessage(HiveMessageType.THIRDPRTY,
                          payload={"vendor": "acme", "data": "custom payload"})
        s0.send(msg)

        m0.recorder.assert_received(HiveMessageType.THIRDPRTY, direction="in")
        b.stop_all()
