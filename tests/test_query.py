"""
TS-QUERY-01..05 — QUERY message type scenarios.

QUERY = ESCALATE(BUS) with response. Climbs upstream until a node's agent
can answer. First answer wins — stops propagation.
"""
import uuid

import pytest
from ovos_bus_client.message import Message

from hivemind_bus_client.message import HiveMessage, HiveMessageType
from hivescope.topology import TopologyBuilder


def _query_msg(utterance: str = "what is 2+2?",
               query_id: str = None,
               originator_peer: str = None) -> HiveMessage:
    """Build a QUERY(BUS(utterance)) message."""
    qid = query_id or str(uuid.uuid4())
    bus_msg = Message("recognizer_loop:utterance",
                      {"utterances": [utterance]})
    inner = HiveMessage(HiveMessageType.BUS, payload=bus_msg)
    return HiveMessage(
        HiveMessageType.QUERY,
        payload=inner,
        metadata={
            "query_id": qid,
            "originator_peer": originator_peer or "",
            "is_response": False,
        },
    )


def _setup_agent_responder(master_node, query_id: str, response_type: str = "speak",
                           response_data: dict = None):
    """Register a bus handler on the master's agent bus that responds to queries.

    Simulates an OVOS skill that handles the utterance and emits a response.
    """
    bus = master_node.agent_protocol.bus

    def _responder(msg: Message):
        if msg.context.get("query_id") == query_id:
            resp = Message(response_type,
                           response_data or {"utterance": "4"},
                           {"query_id": query_id,
                            "destination": msg.context.get("peer"),
                            "source": "skills"})
            bus.emit(resp)

    bus.on("recognizer_loop:utterance", _responder)


class TestQueryLocalAnswer:
    """TS-QUERY-01 — Master's agent handles the query, satellite receives response."""

    def test_query_local_answer(self, minimal_topology):
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        query_id = "q-local-01"
        _setup_agent_responder(m0, query_id)

        msg = _query_msg(query_id=query_id, originator_peer=s0.peer)
        s0.send(msg)

        # Satellite should receive a QUERY response
        s0.recorder.assert_received(HiveMessageType.QUERY, direction="in")

    def test_query_response_has_correct_metadata(self, minimal_topology):
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        query_id = "q-meta-01"
        _setup_agent_responder(m0, query_id)

        msg = _query_msg(query_id=query_id, originator_peer=s0.peer)

        # Capture the response on the satellite side
        responses = []
        s0.shim.emitter.on(HiveMessageType.QUERY, responses.append)

        s0.send(msg)

        assert len(responses) >= 1, "Satellite should receive QUERY response"
        resp = responses[0]
        assert resp.metadata.get("is_response") is True
        assert resp.metadata.get("query_id") == query_id
        assert resp.metadata.get("originator_peer") == s0.peer


class TestQueryEscalateOnTimeout:
    """TS-QUERY-02 — Master can't answer, forwards upstream (or returns error)."""

    def test_query_no_answer_returns_error(self, minimal_topology):
        """Top-level master with no agent response returns timeout/error."""
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        query_id = "q-timeout-01"
        # No agent responder set up — agent can't handle this

        msg = _query_msg(query_id=query_id, originator_peer=s0.peer)

        responses = []
        s0.shim.emitter.on(HiveMessageType.QUERY, responses.append)

        s0.send(msg)

        # Should get an error/timeout response back
        assert len(responses) >= 1, "Satellite should receive error response"
        resp = responses[0]
        assert resp.metadata.get("is_response") is True
        assert resp.metadata.get("query_id") == query_id

    def test_query_escalates_through_relay(self, chain_topology):
        """QUERY from S0 → R1 (no answer) → M0 (has answer) → back to S0."""
        b = chain_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        query_id = "q-chain-01"
        # Only M0 has a responder, R1 does not
        _setup_agent_responder(m0, query_id)

        msg = _query_msg(query_id=query_id, originator_peer=s0.peer)
        s0.send(msg)

        # M0 should have received the QUERY
        m0.recorder.assert_received(HiveMessageType.QUERY, direction="in")


class TestQueryACLDenied:
    """TS-QUERY-04 — can_escalate=False disconnects client."""

    def test_query_acl_denied(self):
        b = TopologyBuilder()
        try:
            b.add_master("M0")
            b.add_satellite("S0", upstream=b.get_master("M0"), can_escalate=False)
            b.start_all()

            m0 = b.get_master("M0")
            s0 = b.get_satellite("S0")

            illegal_calls = []
            m0.hm_protocol.illegal_callback = illegal_calls.append

            msg = _query_msg(query_id="q-acl-01", originator_peer=s0.peer)
            s0.send(msg)

            assert len(illegal_calls) == 1, "illegal_callback should fire for unauthorized QUERY"
        finally:
            b.stop_all()


class TestQueryResponseRouting:
    """TS-QUERY-05 — Response has correct query_id and routes correctly."""

    def test_response_query_id_matches(self, minimal_topology):
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        query_id = "q-id-match-01"
        _setup_agent_responder(m0, query_id)

        responses = []
        s0.shim.emitter.on(HiveMessageType.QUERY, responses.append)

        msg = _query_msg(query_id=query_id, originator_peer=s0.peer)
        s0.send(msg)

        assert len(responses) >= 1
        assert responses[0].metadata["query_id"] == query_id
