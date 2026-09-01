"""
HIVEMIND-AGENT-1 conformance — the backend-contract MUSTs that had no test.

AGENT-1 is the seam between HiveMind routing and whatever brain is behind it.
Almost every clause is about *not losing a query*: a backend that declines, a
backend that is down, and a gathering that never completes each have a defined
outcome, and each of them was implemented with nothing holding it in place.

Pinned here:

  * AGENT-1 §3.0 — both decline signals are honoured: a first yielded ``None``
                   and a raised ``NotImplementedError``
  * AGENT-1 §3.0 — a ``None`` yielded *after* a chunk is not a decline; the
                   stream still completes
  * AGENT-1 §5   — ``hive.query.timeout`` carries ``query_id`` and
                   ``error: "no_answer"``, and is emitted only by a node with
                   no upstream to escalate to
  * AGENT-1 §4.3 — the gatherer keys by query id AND responder, and bounds the
                   number of never-completed gatherings it retains
  * AGENT-1 §2   — the backend is asked for a bus per client, so a
                   multiplexing agent really does get per-client isolation

Already pinned elsewhere: §3.1 ``backend_unavailable`` (``test_spec_musts.py``),
§3.2 response isolation and §4.1/§4.2 chunk streaming (``test_query.py``,
``test_e2e_multi_satellite.py``).
"""
import uuid

import pytest
from ovos_bus_client.message import Message
from ovos_utils.fakebus import FakeBus

from hivemind_bus_client.message import HiveMessage, HiveMessageType
from hivemind_core.protocol import CascadeCollector

from hivescope.plugins.agent import TestAgentProtocol
from hivescope.topology import TopologyBuilder

from tests.conftest import poll_until


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class ScriptedAgent(TestAgentProtocol):
    """An agent backend whose ``answer_query`` follows a fixed script.

    ``script`` is either an exception instance to raise, or a list of chunks to
    yield (``None`` included, since a ``None`` in the stream is exactly what
    the decline rules are about).
    """

    def set_script(self, script):
        self._script = script
        self.asked = []
        return self

    def answer_query(self, utterance, lang, client=None):
        self.asked.append(utterance)
        script = getattr(self, "_script", [None])
        if isinstance(script, BaseException):
            raise script
        for chunk in script:
            yield chunk


def _query(originator_peer, query_id=None):
    qid = query_id or str(uuid.uuid4())
    inner = HiveMessage(HiveMessageType.BUS,
                        payload=Message("recognizer_loop:utterance",
                                        {"utterances": ["what is 2+2?"]}))
    return qid, HiveMessage(HiveMessageType.QUERY, payload=inner,
                            metadata={"query_id": qid,
                                      "originator_peer": originator_peer,
                                      "is_response": False})


def _query_responses(satellite):
    """Inbound QUERY responses captured on the satellite, as OVOS messages."""
    out = []
    for r in satellite.recorder.snapshot():
        if r.direction != "in" or r.msg_type != HiveMessageType.QUERY.value:
            continue
        payload = r.payload if isinstance(r.payload, dict) else {}
        inner = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
        out.append(inner)
    return out


def _inner_types(satellite):
    types = []
    for inner in _query_responses(satellite):
        t = inner.get("type") or ((inner.get("payload") or {}).get("type")
                                  if isinstance(inner.get("payload"), dict) else None)
        if t:
            types.append(t)
    return types


# ---------------------------------------------------------------------------
# AGENT-1 §3.0 / §5 — declining, and what the originator hears
# ---------------------------------------------------------------------------

class TestBackendDeclineSignals:
    """AGENT-1 §3.0 — 'a server MUST accept both decline signals: a first
    yielded ``None``, and ``NotImplementedError``.'

    Two signals exist because two kinds of backend exist: one that has an NL
    stack but nothing to say about this utterance, and one that has no NL stack
    at all. Treating either as an *answer* would stop the query dead at the
    first node instead of escalating it — the satellite would get silence, or
    an empty ``speak``, and never reach the node that could have answered.
    """

    def _run(self, script):
        agent = ScriptedAgent().set_script(script)
        b = TopologyBuilder()
        b.add_master("M0", agent_protocol=agent)
        b.add_satellite("S0", upstream=b.get_master("M0"),
                        allowed_types=["recognizer_loop:utterance"])
        b.start_all()
        s0 = b.get_satellite("S0")
        qid, msg = _query(s0.peer)
        s0.send(msg)
        return b, s0, qid

    @pytest.mark.parametrize("script,label", [
        ([None], "a first yielded None"),
        (NotImplementedError("no NL backend here"), "NotImplementedError"),
    ])
    def test_a_decline_at_the_top_of_the_chain_yields_no_answer(self, script, label):
        """Combined with §5: a declining node with no upstream is the only node
        allowed to answer at all, and it MUST say ``no_answer`` rather than
        fabricate a reply or go quiet."""
        b, s0, qid = self._run(script)
        try:
            timeout = poll_until(
                lambda: next((i for i in _query_responses(s0)
                              if i.get("type") == "hive.query.timeout"), None),
                timeout=3,
                message=f"a decline via {label} produced no hive.query.timeout "
                        "— the originator was left waiting forever")
            data = timeout.get("data") or {}
            assert data.get("error") == "no_answer", (
                f"AGENT-1 §5 fixes error='no_answer'; got {data}")
            assert data.get("query_id") == qid, (
                "the timeout MUST correlate to the query it answers; got "
                f"{data.get('query_id')!r} for {qid!r}")
            assert "hive.query.complete" not in _inner_types(s0), (
                "a declined query MUST NOT be terminated as if it had streamed "
                "an answer")
        finally:
            b.stop_all()

    def test_a_none_after_a_chunk_is_not_a_decline(self):
        """AGENT-1 §3.0 — 'MUST NOT read a later ``None`` as a decline.'

        The end-of-stream sentinel and the decline sentinel are the same value;
        only the position distinguishes them. Reading the terminator as a
        decline would make every successful answer *also* escalate, duplicating
        the reply at every node up the chain.
        """
        b, s0, qid = self._run(["four", None])
        try:
            poll_until(
                lambda: "hive.query.complete" in _inner_types(s0), timeout=3,
                message="a stream that yielded a chunk then None MUST still be "
                        "completed — the trailing None was read as a decline")
            assert "hive.query.timeout" not in _inner_types(s0), (
                "an answered query MUST NOT also report no_answer")
            spoken = [i for i in _query_responses(s0) if i.get("type") == "speak"]
            assert spoken, "the chunk before the None must reach the originator"
        finally:
            b.stop_all()


class TestOnlyTheTopOfTheChainReportsNoAnswer:
    """AGENT-1 §5 / NODE-1 §5.4 — 'only a node with no upstream returns
    ``hive.query.timeout``.'

    An intermediate node that answered ``no_answer`` on its own behalf would
    terminate the query one hop early and hide a node further up that could
    have answered — and the originator would receive several contradictory
    verdicts for one query id.
    """

    def test_a_relay_escalates_instead_of_reporting_no_answer(self):
        m_agent = ScriptedAgent().set_script([None])
        r_agent = ScriptedAgent().set_script([None])
        b = TopologyBuilder()
        try:
            b.add_master("M0", agent_protocol=m_agent)
            r1 = b.add_relay("R1", upstream=b.get_master("M0"),
                             allowed_types=["recognizer_loop:utterance"])
            r1.listener.agent_protocol = r_agent
            r1.listener.hm_protocol.agent_protocol = r_agent
            b.add_satellite("S0", upstream=r1.listener,
                            allowed_types=["recognizer_loop:utterance"])
            b.start_all()
            s0 = b.get_satellite("S0")

            qid, msg = _query(s0.peer)
            s0.send(msg)

            poll_until(
                lambda: [i for i in _query_responses(s0)
                         if i.get("type") == "hive.query.timeout"],
                timeout=5,
                message="the query never resolved at all")
            assert m_agent.asked, (
                "the relay answered no_answer on its own behalf instead of "
                "escalating — the master's agent was never consulted, so a "
                "node that could have answered never saw the query")
            timeouts = [i for i in _query_responses(s0)
                        if i.get("type") == "hive.query.timeout"]
            assert len(timeouts) == 1, (
                "exactly one node — the one with no upstream — may report "
                f"no_answer; the originator received {len(timeouts)}")
        finally:
            b.stop_all()


# ---------------------------------------------------------------------------
# AGENT-1 §4.3 — the CASCADE gatherer
# ---------------------------------------------------------------------------

def _cascade_response(query_id, responder, utterance, originator_peer=""):
    inner = HiveMessage(HiveMessageType.BUS,
                        payload=Message("speak", {"utterance": utterance}))
    return HiveMessage(HiveMessageType.CASCADE, payload=inner,
                       metadata={"query_id": query_id, "is_response": True,
                                 "originator_peer": originator_peer,
                                 "responder_peer": responder,
                                 "responder_site_id": "site"})


class TestGathererKeysByQueryIdAndResponder:
    """AGENT-1 §4.3 — 'the gatherer MUST key by query id **and** responder.'

    A CASCADE fans out to the whole mesh and every node streams its own
    multi-chunk answer, interleaved. Keying by query id alone would splice two
    nodes' answers into one reply; keying by responder alone would merge two
    different questions. The collector is the query-id half; ``_by_responder``
    is the other.
    """

    def test_two_responders_produce_two_entries(self):
        c = CascadeCollector(query_id="q1", originator_peer="S0")
        c.add_response(_cascade_response("q1", "R1", "from R1"))
        c.add_response(_cascade_response("q1", "R2", "from R2"))
        assert [r.responder_peer for r in c.responses] == ["R1", "R2"], (
            "two responders MUST NOT share one entry")

    def test_one_responder_streaming_two_chunks_produces_one_entry(self):
        c = CascadeCollector(query_id="q1", originator_peer="S0")
        c.add_response(_cascade_response("q1", "R1", "first"))
        c.add_response(_cascade_response("q1", "R1", "second"))
        assert len(c.responses) == 1, (
            "one responder's chunks accumulate into one answer, not two")
        assert [m.data["utterance"] for m in c.responses[0].messages] == [
            "first", "second"], "chunks must accumulate in arrival order"

    def test_responders_are_kept_in_order_of_first_arrival(self):
        """A select callback picks a winner progressively, so 'who answered
        first' has to survive."""
        c = CascadeCollector(query_id="q1", originator_peer="S0")
        for responder in ("R2", "R1", "R2", "R3"):
            c.add_response(_cascade_response("q1", responder, "x"))
        assert [r.responder_peer for r in c.responses] == ["R2", "R1", "R3"]


class TestNeverCompletedGatheringsAreBounded:
    """AGENT-1 §4.3 — 'a gatherer MUST bound never-completed gatherings.'

    A CASCADE whose responders never all answer leaves its collector behind.
    Without a bound, any peer that can originate a CASCADE can grow the node's
    memory without limit simply by never letting one finish — an unauthenticated
    -by-policy peer cannot do this, but an authenticated satellite with a bug
    certainly can.
    """

    def test_the_pending_collector_map_does_not_grow_without_limit(self):
        b = TopologyBuilder()
        try:
            b.add_master("M0")
            b.add_satellite("S0", upstream=b.get_master("M0"),
                            allowed_types=["recognizer_loop:utterance"])
            b.start_all()
            m0, s0 = b.get_master("M0"), b.get_satellite("S0")
            # A select callback is what makes the node collect at all.
            m0.hm_protocol.cascade_select_callback = lambda *a, **kw: None

            for i in range(600):
                query_id = f"q{i}"
                # MSG-1 §5.2 participation binding: a response is only routed
                # (and only reaches the CASCADE collector) if the master
                # already recorded an outstanding request for its query_id —
                # otherwise it is dropped as unbacked before the collector
                # bound in _route_query_response is ever exercised.
                m0.hm_protocol._record_outstanding_query(query_id, s0.peer)
                m0.hm_protocol._route_query_response(
                    _cascade_response(query_id, "R1", "x",
                                      originator_peer=s0.peer),
                    None)

            pending = m0.hm_protocol._pending_cascades
            assert len(pending) <= 256, (
                "never-completed CASCADE gatherings MUST be bounded; the "
                f"pending map grew to {len(pending)} entries")
            assert "q599" in pending, (
                "the bound must evict the oldest gathering, not stop "
                "collecting new ones")
        finally:
            b.stop_all()


# ---------------------------------------------------------------------------
# AGENT-1 §2 / §3.0 — the backend supplies a bus per client
# ---------------------------------------------------------------------------

class PerClientBusAgent(TestAgentProtocol):
    """A multiplexing backend: one isolated bus per connected peer.

    This is the whole reason ``get_bus`` takes a client. If hivemind-core ever
    resolved the bus once and cached it, every 'one brain per access key'
    deployment would silently collapse into a shared brain — and nothing else
    in the system would report an error.
    """

    def per_client_buses(self):
        if not hasattr(self, "_buses"):
            self._buses = {}
        return self._buses

    def get_bus(self, client=None):
        if client is None:
            return self.bus
        buses = self.per_client_buses()
        if client.peer not in buses:
            buses[client.peer] = FakeBus()
        return buses[client.peer]


class TestBackendSuppliesABusPerClient:
    """AGENT-1 §2 / §3.0 — 'the backend MUST expose ``get_bus(client)``', and
    hivemind-core MUST use it per injected message."""

    def test_each_peer_message_lands_on_that_peers_bus(self):
        agent = PerClientBusAgent()
        b = TopologyBuilder()
        try:
            b.add_master("M0", agent_protocol=agent)
            b.add_satellite("S0", upstream=b.get_master("M0"),
                            allowed_types=["recognizer_loop:utterance"])
            b.add_satellite("S1", upstream=b.get_master("M0"),
                            allowed_types=["recognizer_loop:utterance"])
            b.start_all()
            s0, s1 = b.get_satellite("S0"), b.get_satellite("S1")

            seen = {}
            for sat in (s0, s1):
                bus = agent.get_bus(
                    b.get_master("M0").hm_protocol.clients[sat.peer])
                seen[sat.peer] = []
                bus.on("recognizer_loop:utterance",
                       lambda m, _p=sat.peer: seen[_p].append(m))

            s0.send(Message("recognizer_loop:utterance", {"utterances": ["from s0"]}))

            poll_until(lambda: seen[s0.peer], timeout=3,
                       message="S0's message never reached the bus the backend "
                               "returned for S0 — get_bus(client) was not honoured")
            assert seen[s1.peer] == [], (
                "S0's message landed on S1's bus: a multiplexing backend's "
                "per-client isolation was bypassed")
        finally:
            b.stop_all()
