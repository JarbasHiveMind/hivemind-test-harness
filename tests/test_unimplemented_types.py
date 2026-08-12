"""
TS-STUB-01..04 — frames that no handler is expected to act on.

These began as coverage for message types that had no handler at all. All four
now route: QUERY, CASCADE and PING have full handlers and their own suites, and
RENDEZVOUS reaches a store-and-forward mailbox on a node that has one
(hivemind-core 4.13.0a1). What survives here is the weaker guarantee that still
matters: sending these frames does not crash the master, and the master records
them as inbound.

RENDEZVOUS also serves as the harness's stand-in for an inert inner payload in
PROPAGATE/ESCALATE/BROADCAST. That still holds: those verbs forward the inner
frame rather than dispatching it, so no mailbox is involved.

TODO: RENDEZVOUS needs real conformance coverage of its own — deposit, collect,
      ack, redelivery before ack, and the mailbox binding that stops one node
      collecting another's mail. See docs/02-protocol-coverage.md.
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
        """TS-STUB-01 — QUERY is now handled by handle_query_message (implemented)."""
        b = None
        try:
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
            if b is not None:
                b.stop_all()

    def test_cascade_dispatches_to_handler(self):
        """TS-STUB-02 — CASCADE is now handled by handle_cascade_message (implemented)."""
        b = None
        try:
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
            if b is not None:
                b.stop_all()

    def test_ping_does_not_crash(self):
        """TS-STUB-03 — PING falls to handle_unknown_message (empty stub).
        TODO: implement PING handler (latency/alive check)."""
        b = None
        try:
            b = _make_topology()
            m0 = b.get_master("M0")
            s0 = b.get_satellite("S0")

            msg = HiveMessage(HiveMessageType.PING, payload={})
            s0.send(msg)

            m0.recorder.assert_received(HiveMessageType.PING, direction="in")
        finally:
            if b is not None:
                b.stop_all()

    def test_rendezvous_does_not_crash(self):
        """TS-STUB-04 — a RENDEZVOUS frame reaches the master without crashing.

        RENDEZVOUS is routed now (hivemind-core 4.13.0a1): a node with no
        mailbox answers not_a_rendezvous_node instead of dropping the frame.
        This test only pins that the frame arrives, so it stays valid, but it
        no longer covers the type. TODO: real conformance for deposit,
        collect, ack, redelivery before ack, and the mailbox binding."""
        b = None
        try:
            b = _make_topology()
            m0 = b.get_master("M0")
            s0 = b.get_satellite("S0")

            msg = HiveMessage(HiveMessageType.RENDEZVOUS,
                              payload={"peer": "some-peer-id"})
            s0.send(msg)

            m0.recorder.assert_received(HiveMessageType.RENDEZVOUS, direction="in")
        finally:
            if b is not None:
                b.stop_all()
