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
from hivescope.topology import TopologyBuilder


def _make_topology():
    b = TopologyBuilder()
    b.add_master("M0")
    b.add_satellite("S0", upstream=b.get_master("M0"))
    b.start_all()
    return b


class TestUnimplementedTypes:
    """TS-STUB-01..05 — unimplemented types must not crash the master."""

    def test_query_dispatches_to_handler(self):
        try:
            """TS-STUB-01 — QUERY is now handled by handle_query_message (implemented)."""
            b = _make_topology()
            m0 = b.get_master("M0")
            s0 = b.get_satellite("S0")

            inner_bus = Message("recognizer_loop:utterance",
                                {"utterances": ["what is 2+2?"]})
            inner = HiveMessage(HiveMessageType.BUS, payload=inner_bus)
            msg = HiveMessage(HiveMessageType.QUERY, payload=inner,
                              metadata={"query_id": "test-q-1",
                                        "originator_peer": s0.peer,
                                        "is_response": False})
            s0.send(msg)

            m0.recorder.assert_received(HiveMessageType.QUERY, direction="in")
        finally:
            b.stop_all()

    def test_cascade_dispatches_to_handler(self):
        try:
            """TS-STUB-02 — CASCADE is now handled by handle_cascade_message (implemented)."""
            b = _make_topology()
            m0 = b.get_master("M0")
            s0 = b.get_satellite("S0")

            inner_bus = Message("recognizer_loop:utterance",
                                {"utterances": ["what is the weather?"]})
            inner = HiveMessage(HiveMessageType.BUS, payload=inner_bus)
            msg = HiveMessage(HiveMessageType.CASCADE, payload=inner,
                              metadata={"query_id": "test-c-1",
                                        "originator_peer": s0.peer,
                                        "is_response": False})
            s0.send(msg)

            m0.recorder.assert_received(HiveMessageType.CASCADE, direction="in")
        finally:
            b.stop_all()

    def test_ping_does_not_crash(self):
        try:
            """TS-STUB-03 — PING falls to handle_unknown_message (empty stub).
            TODO: implement PING handler (latency/alive check)."""
            b = _make_topology()
            m0 = b.get_master("M0")
            s0 = b.get_satellite("S0")

            msg = HiveMessage(HiveMessageType.PING, payload={})
            s0.send(msg)

            m0.recorder.assert_received(HiveMessageType.PING, direction="in")
        finally:
            b.stop_all()

    def test_rendezvous_does_not_crash(self):
        try:
            """TS-STUB-04 — RENDEZVOUS falls to handle_unknown_message (empty stub).
            TODO: implement RENDEZVOUS handler (peer discovery / hole-punching)."""
            b = _make_topology()
            m0 = b.get_master("M0")
            s0 = b.get_satellite("S0")

            msg = HiveMessage(HiveMessageType.RENDEZVOUS,
                              payload={"peer": "some-peer-id"})
            s0.send(msg)

            m0.recorder.assert_received(HiveMessageType.RENDEZVOUS, direction="in")
        finally:
            b.stop_all()

    def test_thirdprty_toplevel_does_not_crash(self):
        try:
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
        finally:
            b.stop_all()
