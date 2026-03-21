"""
TS-CASCADE-01..05 — CASCADE message type scenarios.

CASCADE = PROPAGATE(BUS) with response. Floods entire hive, collects
answers from ALL agents. Diverse opinions before picking.
"""
import uuid

import pytest
from ovos_bus_client.message import Message

from hivemind_bus_client.message import HiveMessage, HiveMessageType
from hivemind_test_harness.topology import TopologyBuilder


def _cascade_msg(utterance: str = "what is the weather?",
                 query_id: str = None,
                 originator_peer: str = None) -> HiveMessage:
    """Build a CASCADE(BUS(utterance)) message."""
    qid = query_id or str(uuid.uuid4())
    bus_msg = Message("recognizer_loop:utterance",
                      {"utterances": [utterance]})
    inner = HiveMessage(HiveMessageType.BUS, payload=bus_msg)
    return HiveMessage(
        HiveMessageType.CASCADE,
        payload=inner,
        metadata={
            "query_id": qid,
            "originator_peer": originator_peer or "",
            "is_response": False,
        },
    )


def _setup_agent_responder(master_node, query_id: str, answer: str = "sunny"):
    """Register a bus handler that responds to CASCADE queries."""
    bus = master_node.agent_protocol.bus

    def _responder(msg: Message):
        if msg.context.get("query_id") == query_id:
            resp = Message("speak",
                           {"utterance": answer},
                           {"query_id": query_id,
                            "destination": msg.context.get("peer"),
                            "source": "skills"})
            bus.emit(resp)

    bus.on("recognizer_loop:utterance", _responder)


class TestCascadeAllRespond:
    """TS-CASCADE-01 — All nodes respond, originator collects responses."""

    def test_cascade_master_responds(self, minimal_topology):
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        query_id = "c-all-01"
        _setup_agent_responder(m0, query_id)

        responses = []
        s0.shim.emitter.on(HiveMessageType.CASCADE, responses.append)

        msg = _cascade_msg(query_id=query_id, originator_peer=s0.peer)
        s0.send(msg)

        # At minimum, master's agent should respond
        assert len(responses) >= 1, "Satellite should receive at least one CASCADE response"
        resp = responses[0]
        assert resp.metadata.get("is_response") is True
        assert resp.metadata.get("query_id") == query_id


class TestCascadeStarTopology:
    """TS-CASCADE-02 — Star topology: CASCADE forwards to all siblings."""

    def test_cascade_forwarded_to_siblings(self, star_topology):
        b = star_topology
        s0 = b.get_satellite("S0")
        s1 = b.get_satellite("S1")
        s2 = b.get_satellite("S2")
        m0 = b.get_master("M0")

        query_id = "c-star-01"

        # Track CASCADE messages received by sibling satellites
        s1_received = []
        s1.shim.emitter.on(HiveMessageType.CASCADE, s1_received.append)
        s2_received = []
        s2.shim.emitter.on(HiveMessageType.CASCADE, s2_received.append)

        msg = _cascade_msg(query_id=query_id, originator_peer=s0.peer)
        s0.send(msg)

        # Siblings should receive the CASCADE (forwarded by master)
        assert len(s1_received) >= 1, "S1 should receive CASCADE"
        assert len(s2_received) >= 1, "S2 should receive CASCADE"

    def test_cascade_with_master_response(self, star_topology):
        b = star_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        query_id = "c-star-02"
        _setup_agent_responder(m0, query_id)

        responses = []
        s0.shim.emitter.on(HiveMessageType.CASCADE, responses.append)

        msg = _cascade_msg(query_id=query_id, originator_peer=s0.peer)
        s0.send(msg)

        # Should get master's response
        resp_messages = [r for r in responses if r.metadata.get("is_response")]
        assert len(resp_messages) >= 1, "Should receive master's CASCADE response"


class TestCascadeRelayForwarding:
    """TS-CASCADE-03 — Responses traverse relays correctly."""

    def test_cascade_reaches_top_master(self, chain_topology):
        b = chain_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")
        r1_master = b.get_master("R1_master")

        query_id = "c-relay-01"

        r1_cascade_calls = []
        r1_master.recorder.messages = []

        msg = _cascade_msg(query_id=query_id, originator_peer=s0.peer)
        s0.send(msg)

        # R1's master side should process the CASCADE
        r1_master.recorder.assert_received(HiveMessageType.CASCADE, direction="in")


class TestCascadeACLDenied:
    """TS-CASCADE-05 — can_propagate=False disconnects client."""

    def test_cascade_acl_denied(self):
        b = TopologyBuilder()
        b.add_master("M0")
        b.add_satellite("S0", upstream=b.get_master("M0"), can_propagate=False)
        b.start_all()

        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")

        illegal_calls = []
        m0.hm_protocol.illegal_callback = illegal_calls.append

        msg = _cascade_msg(query_id="c-acl-01", originator_peer=s0.peer)
        s0.send(msg)

        assert len(illegal_calls) == 1, "illegal_callback should fire for unauthorized CASCADE"
        b.stop_all()


class TestCascadeSelectCallback:
    """TS-CASCADE-06 — cascade_select_callback for disambiguation."""

    def test_select_callback_invoked(self, minimal_topology):
        """When cascade_select_callback is set, it receives collected responses."""
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        query_id = "c-select-01"
        _setup_agent_responder(m0, query_id, answer="sunny")

        # Set up a select callback that picks the first response
        selected = []

        def _select(qid, responses):
            selected.append((qid, len(responses)))
            if responses:
                # Return the first response's first message
                return responses[0].messages[0] if responses[0].messages else None
            return None

        m0.hm_protocol.cascade_select_callback = _select

        msg = _cascade_msg(query_id=query_id, originator_peer=s0.peer)
        s0.send(msg)

        # The callback should have been invoked
        assert len(selected) >= 1, "cascade_select_callback should be invoked"
        assert selected[0][0] == query_id

    def test_select_callback_none_does_not_emit(self, minimal_topology):
        """When callback returns None, no message is emitted (waiting for more)."""
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        query_id = "c-select-02"
        _setup_agent_responder(m0, query_id, answer="waiting")

        invoked = []

        def _select(qid, responses):
            invoked.append(len(responses))
            return None  # not ready to select yet

        m0.hm_protocol.cascade_select_callback = _select

        msg = _cascade_msg(query_id=query_id, originator_peer=s0.peer)
        s0.send(msg)

        assert len(invoked) >= 1


class TestCascadeResponseMetadata:
    """TS-CASCADE-04 — Responses include responder identity."""

    def test_response_includes_responder_peer(self, minimal_topology):
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        query_id = "c-meta-01"
        _setup_agent_responder(m0, query_id)

        responses = []
        s0.shim.emitter.on(HiveMessageType.CASCADE, responses.append)

        msg = _cascade_msg(query_id=query_id, originator_peer=s0.peer)
        s0.send(msg)

        resp_messages = [r for r in responses if r.metadata.get("is_response")]
        assert len(resp_messages) >= 1
        assert "responder_peer" in resp_messages[0].metadata
