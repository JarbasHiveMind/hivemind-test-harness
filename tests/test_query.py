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
from tests.conftest import poll_until

# The answering node runs the agent off its IOLoop (HiveMind-core 5.3.6a1), so
# an answer reaches the originator after ``send`` returns. Every cell waits
# for it rather than reading the satellite's state straight after the send.
ANSWER_TIMEOUT = 5.0
# A node whose agent never answers declines only once the agent's stream has
# been idle for its bound (10 s in hivescope's agent, as in production).
DECLINE_TIMEOUT = 15.0


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


def _setup_agent_responder(master_node, response_type: str = "speak",
                           response_data: dict = None):
    """Register a bus handler on the master's agent bus that responds to queries.

    Simulates an OVOS skill that handles the utterance and emits a response.
    The agent asks on its bus under a query_id of its own, not the QUERY's
    (which never reaches the agent bus), so the handler answers under the id
    it was asked with and then ends the query.
    """
    bus = master_node.agent_protocol.bus

    def _responder(msg: Message):
        qid = msg.context.get("query_id")
        bus.emit(Message(response_type,
                         response_data or {"utterance": "4"},
                         {"query_id": qid, "source": "skills"}))
        bus.emit(Message("ovos.utterance.handled", {}, {"query_id": qid}))

    bus.on("recognizer_loop:utterance", _responder)


def _grant_query_access(host_master, satellite,
                        types=("recognizer_loop:utterance",)):
    """Whitelist the utterance for ``satellite`` on ``host_master``, on the
    live connection (what the policy reads) and on the DB row.

    hivemind-core is deny-by-default: a QUERY whose utterance the host's
    policy denies is dropped, never answered or escalated (MSG-1 §5.1).
    """
    conn = host_master.hm_protocol.clients.get(satellite.peer)
    if conn is not None:
        conn.allowed_types = list(types)
    db_client = host_master.db.get_client_by_api_key(satellite.identity.access_key)
    if db_client is not None:
        db_client.allowed_types = list(types)
        host_master.db.update_item(db_client)


def _inner_types(responses):
    """The inner bus msg_types of a list of QUERY response frames."""
    return [r.payload.payload.msg_type for r in responses]


class TestQueryLocalAnswer:
    """TS-QUERY-01 — Master's agent handles the query, satellite receives response."""

    def test_query_local_answer(self, minimal_topology):
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        query_id = "q-local-01"
        _setup_agent_responder(m0)

        responses = []
        s0.shim.emitter.on(HiveMessageType.QUERY, responses.append)

        msg = _query_msg(query_id=query_id, originator_peer=s0.peer)
        s0.send(msg)

        # Satellite should receive the master's answer, not a no_answer error
        poll_until(lambda: "speak" in _inner_types(responses),
                   timeout=ANSWER_TIMEOUT,
                   message="Satellite should receive the master's QUERY answer")
        assert "hive.query.timeout" not in _inner_types(responses)

    def test_query_response_has_correct_metadata(self, minimal_topology):
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        query_id = "q-meta-01"
        _setup_agent_responder(m0)

        msg = _query_msg(query_id=query_id, originator_peer=s0.peer)

        # Capture the response on the satellite side
        responses = []
        s0.shim.emitter.on(HiveMessageType.QUERY, responses.append)

        s0.send(msg)

        poll_until(lambda: responses, timeout=ANSWER_TIMEOUT,
                   message="Satellite should receive QUERY response")
        resp = responses[0]
        assert resp.payload.payload.msg_type == "speak"
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
        poll_until(lambda: responses, timeout=DECLINE_TIMEOUT,
                   message="Satellite should receive error response")
        resp = responses[0]
        assert resp.payload.payload.msg_type == "hive.query.timeout"
        assert resp.metadata.get("is_response") is True
        assert resp.metadata.get("query_id") == query_id

    def test_query_escalates_through_relay(self, chain_topology):
        """QUERY from S0 → R1 (no answer) → M0 (has answer) → back to S0."""
        b = chain_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        query_id = "q-chain-01"
        # Only M0 has a responder, R1 does not
        _setup_agent_responder(m0)
        # M0 must admit the utterance R1 escalates on S0's behalf
        _grant_query_access(m0, b.get_satellite("R1_sat"))

        responses = []
        s0.shim.emitter.on(HiveMessageType.QUERY, responses.append)

        msg = _query_msg(query_id=query_id, originator_peer=s0.peer)
        s0.send(msg)

        # M0 should have received the QUERY, once R1's agent declined it
        assert m0.recorder.wait_for(HiveMessageType.QUERY, direction="in",
                                    timeout=DECLINE_TIMEOUT), \
            "R1 should escalate the QUERY to M0 after its agent declines"
        m0.recorder.assert_received(HiveMessageType.QUERY, direction="in")

        # and M0's answer should come back down the relay to the originator
        poll_until(lambda: "speak" in _inner_types(responses),
                   timeout=ANSWER_TIMEOUT,
                   message="M0's answer should reach S0 through R1; got "
                           f"{_inner_types(responses)}")


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
        _setup_agent_responder(m0)

        responses = []
        s0.shim.emitter.on(HiveMessageType.QUERY, responses.append)

        msg = _query_msg(query_id=query_id, originator_peer=s0.peer)
        s0.send(msg)

        poll_until(lambda: responses, timeout=ANSWER_TIMEOUT,
                   message="Satellite should receive QUERY response")
        assert responses[0].metadata["query_id"] == query_id
