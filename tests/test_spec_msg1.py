"""
HIVEMIND-MSG-1 conformance — the envelope, naming and correlation MUSTs that
had no pinning test.

Covered here:

  * §2   — the envelope is ``msg_type`` + ``payload`` (+ ``context``); there is
           no ``destination`` envelope field
  * §2   — a receiver MUST treat an unknown ``msg_type`` as unroutable and MUST
           NOT interpret its payload
  * §3   — the canonical wire strings, verbatim
  * §3   — ``PONG`` is not a message type
  * §5   — ``source_peer`` is stamped when it is not derivable from the
           connection
  * §5   — ``target_site_id`` gates *delivery*, not *travel*, and a relay MUST
           copy it onto the envelope it forwards
  * §5.1 — a hop's ``targets`` MUST NOT drive a delivery decision
  * §5.2 — answer routing: the direct originator first, else walk ``route``
           backwards; a response with no return path is dropped, never fanned
           out to siblings
  * §5.2 — mint ``query_id`` / set ``originator_peer`` when absent
  * §5.2 — carry ``query_id``/``originator_peer``/``is_response`` unchanged and
           set ``responder_peer`` to self

Deliberately NOT covered here:

  * §5 ``target_peers`` on the wire — MSG-1 §5 lists it as an envelope key, but
    it is deliberately absent from ``HiveMessage.as_dict``: the INTERCOM
    PKCS1-OAEP block ceiling is ~214 bytes and the minimum BUS envelope is
    already 207, so adding the key breaks real INTERCOM traffic. That is a
    SPEC defect, not a code gap. ``hivemind-websocket-client``'s
    ``tests/test_message.py::TestWireSizeCeiling`` pins the ceiling; nothing
    here asserts the key is present.
  * §3 "MUST NOT reject a connection over an un-understood payload" and §4
    "MUST NOT rewrite a wrapped inner payload" — both are open spec-vs-spec
    contradictions (NODE-1 §4 explicitly permits the disconnect; the §4
    exemption list omits routing-metadata maintenance). Pinning either would
    pin one side of an unresolved disagreement.
"""
import uuid

import pytest
from hivemind_bus_client.message import HiveMessage, HiveMessageType
from ovos_bus_client.message import Message

from hivescope.topology import TopologyBuilder

from tests.conftest import poll_until

UTTERANCE = "recognizer_loop:utterance"


# ---------------------------------------------------------------------------
# MSG-1 §2 — the envelope
# ---------------------------------------------------------------------------

class TestEnvelopeShape:
    """MSG-1 §2 — 'A HiveMind message is an envelope with a ``msg_type`` and a
    ``payload``.' The envelope is what every non-Python peer parses, so its key
    set is contract.
    """

    def test_envelope_carries_msg_type_and_payload_and_no_destination(self):
        bus = Message("recognizer_loop:utterance", {"utterances": ["hi"]},
                      {"session": {"session_id": "s1"}})
        env = HiveMessage(HiveMessageType.BUS, payload=bus).as_dict

        assert env["msg_type"] == HiveMessageType.BUS
        assert env["payload"]["type"] == UTTERANCE
        assert env["payload"]["data"]["utterances"] == ["hi"]
        # The per-message routing target is Layer-1 (inside the payload's OVOS
        # context), never an envelope field. A `destination` envelope key was a
        # documented past fabrication; it must not come back.
        assert "destination" not in env, (
            "MSG-1 §2: routing destination is Layer-1 payload context, not an "
            "envelope field")

    def test_envelope_round_trips_through_serialize(self):
        bus = Message("speak", {"utterance": "hello"}, {"destination": "sat"})
        original = HiveMessage(HiveMessageType.BUS, payload=bus,
                               target_site_id="site-a")
        restored = HiveMessage.deserialize(original.serialize())

        assert restored.msg_type == original.msg_type
        assert restored.payload.msg_type == "speak"
        assert restored.payload.data == {"utterance": "hello"}
        # the Layer-1 destination rides inside the payload context untouched
        assert restored.payload.context["destination"] == "sat"
        assert restored.target_site_id == "site-a"


class TestUnknownMsgTypeIsUnroutable:
    """MSG-1 §2 — 'A receiver MUST treat an unknown ``msg_type`` as unroutable
    and MUST NOT interpret its payload.'

    The failure mode this forbids is a receiver that shrugs at the type it does
    not know and then handles the payload anyway on a best-effort guess.
    """

    def test_constructing_an_unknown_type_is_refused(self):
        with pytest.raises(ValueError, match="Unknown HiveMessage.msg_type"):
            HiveMessage("no-such-hivemind-type",
                        payload=Message(UTTERANCE, {"utterances": ["boo"]}))

    def test_a_frame_with_an_unknown_type_is_refused_before_the_payload_is_read(
            self, minimal_topology):
        """End-to-end: the master's own decoder refuses the frame, so the
        payload never reaches the agent bus.

        The frame is properly encrypted for this connection on purpose. A
        cleartext frame would be refused by the ``crypto_required`` guard
        instead, and the test would pass without the unknown-type rule
        existing at all.
        """
        import json

        from hivemind_bus_client.encryption import encrypt_as_json

        b = minimal_topology
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")
        conn = m0.hm_protocol.clients[s0.peer]
        assert conn.crypto_key, "precondition: the session key must be established"

        frame = encrypt_as_json(
            key=conn.crypto_key,
            plaintext=json.dumps({
                "msg_type": "totally-made-up",
                "payload": {"type": UTTERANCE,
                            "data": {"utterances": ["interpret me"]},
                            "context": {}},
            }),
            cipher=conn.cipher, encoding=conn.encoding)

        with pytest.raises(ValueError, match="Unknown HiveMessage.msg_type"):
            conn.decode(frame)

        m0.agent_protocol.assert_not_injected(UTTERANCE)


# ---------------------------------------------------------------------------
# MSG-1 §3 — the canonical wire strings
# ---------------------------------------------------------------------------

# The literal strings that go on the wire. A non-Python peer compares these
# byte for byte; renaming a member is free, changing a value is a break.
_CANONICAL_WIRE_STRINGS = {
    "HANDSHAKE": "shake",
    "BUS": "bus",
    "SHARED_BUS": "shared_bus",
    "INTERCOM": "intercom",
    "BROADCAST": "broadcast",
    "PROPAGATE": "propagate",
    "ESCALATE": "escalate",
    "HELLO": "hello",
    "QUERY": "query",
    "CASCADE": "cascade",
    "PING": "ping",
    "RENDEZVOUS": "rendezvous",
    "BINARY": "bin",
}


class TestCanonicalWireStrings:
    """MSG-1 §3 — the canonical message-type strings, verbatim."""

    @pytest.mark.parametrize("name,wire", sorted(_CANONICAL_WIRE_STRINGS.items()))
    def test_wire_string_is_frozen(self, name, wire):
        assert getattr(HiveMessageType, name).value == wire, (
            f"MSG-1 §3: '{wire}' is the canonical wire string for {name}; "
            "changing it breaks every peer that matches on the literal")

    def test_pong_is_not_a_message_type(self):
        """MSG-1 §3 — 'There is no ``PONG`` type.' A PING is answered by
        another PING carrying the same ``flood_id``, which is what makes the
        flood self-terminating; introducing a PONG type would give the reply a
        path that skips the flood_id gate."""
        assert not hasattr(HiveMessageType, "PONG")
        assert "pong" not in {t.value for t in HiveMessageType}


# ---------------------------------------------------------------------------
# MSG-1 §5 — envelope routing metadata
# ---------------------------------------------------------------------------

def _propagated_bus(text: str, **kwargs) -> HiveMessage:
    inner = HiveMessage(HiveMessageType.BUS,
                        payload=Message(UTTERANCE, {"utterances": [text]}))
    return HiveMessage(HiveMessageType.PROPAGATE, payload=inner, **kwargs)


@pytest.fixture
def two_satellite_star():
    """M0 with two sibling satellites, both allowed to send utterances."""
    b = TopologyBuilder()
    try:
        b.add_master("M0")
        b.add_satellite("S0", upstream=b.get_master("M0"),
                        allowed_types=[UTTERANCE])
        b.add_satellite("S1", upstream=b.get_master("M0"),
                        allowed_types=[UTTERANCE])
        b.start_all()
        yield b
    finally:
        b.stop_all()


class TestSourcePeerIsStamped:
    """MSG-1 §5 — 'A node MUST set ``source_peer`` when it is not derivable
    from the connection.'

    On the inbound hop the sender is derivable (it is the connection). The
    moment a node re-wraps the envelope and fans it out, that context is gone:
    the sibling that receives it has no way to know who originated it unless
    the forwarding node names itself. An unstamped forward makes an answer
    unroutable and a loop undetectable.
    """

    def test_forwarded_envelope_names_the_forwarding_node(self, two_satellite_star):
        b = two_satellite_star
        m0 = b.get_master("M0")
        s0, s1 = b.get_satellite("S0"), b.get_satellite("S1")

        received = []
        s1.shim.emitter.on(HiveMessageType.PROPAGATE, received.append)
        s0.send(_propagated_bus("who sent this?"))

        poll_until(lambda: received, timeout=3,
                   message="sibling never received the PROPAGATE")
        inner = received[0].payload
        assert inner.source_peer == m0.identity.public_key, (
            "MSG-1 §5: the relaying node MUST name itself in ``source_peer`` "
            f"on the envelope it forwards; got {inner.source_peer!r}")


class TestHopTargetsDoNotGateDelivery:
    """MSG-1 §5.1 — 'A hop's ``targets`` MUST NOT drive a delivery decision.'

    ``targets`` is diagnostic breadcrumb data recorded for topology mapping. A
    node that started filtering on it would silently stop delivering to peers
    that a *different* node happened not to list, and the loss would look like
    a network fault rather than a bug.
    """

    def test_sibling_still_receives_when_targets_names_nobody(self, two_satellite_star):
        b = two_satellite_star
        s0, s1 = b.get_satellite("S0"), b.get_satellite("S1")

        received = []
        s1.shim.emitter.on(HiveMessageType.PROPAGATE, received.append)

        msg = _propagated_bus("targets must not gate me")
        msg.replace_route([{"source": "some-other-node",
                            "targets": ["a-peer-that-is-not-here"]}])
        s0.send(msg)

        poll_until(lambda: received, timeout=3,
                   message="MSG-1 §5.1: a hop's ``targets`` gated delivery — "
                           "the sibling was skipped because an unrelated hop "
                           "did not name it")


@pytest.fixture
def site_chain():
    """M0 ─ R1 (relay) ─ S0, every hop allowed to carry utterances.

    Three distinct site ids (``M0-site``, ``R1_master-site``, ``S0-site``) so a
    site-targeted message has exactly one legitimate delivery point.
    """
    b = TopologyBuilder()
    try:
        b.add_master("M0")
        relay = b.add_relay("R1", upstream=b.get_master("M0"),
                            allowed_types=[UTTERANCE])
        b.add_satellite("S0", upstream=relay.listener, allowed_types=[UTTERANCE])
        b.start_all()
        yield b
    finally:
        b.stop_all()


class TestTargetSiteIdGatesDeliveryNotTravel:
    """MSG-1 §5 — '``target_site_id`` gates delivery, not travel. A relay MUST
    copy it onto the new envelope it sends on.'

    This is the failure the ``_rewrap`` docstring describes: ``metadata``,
    ``target_site_id`` and ``target_pubkey`` are constructor-only, so a naive
    ``HiveMessage(type, payload=payload)`` drops them. A site-targeted message
    would then travel one hop and become undeliverable everywhere — reaching
    its destination but no longer knowing it had arrived.
    """

    def test_site_targeted_message_crosses_a_relay_and_lands_only_at_its_site(
            self, site_chain):
        b = site_chain
        m0 = b.get_master("M0")
        relay = b.get_relay("R1")
        s0 = b.get_satellite("S0")

        assert m0.identity.site_id != relay.listener.identity.site_id, \
            "precondition: the relay must not share the target site"

        s0.send(_propagated_bus("for M0's site only",
                                target_site_id=m0.identity.site_id))

        # travel: the envelope survives the relay hop with its site id intact,
        # so the target still recognises itself and delivers locally.
        poll_until(lambda: m0.agent_protocol.last_injected(UTTERANCE), timeout=5,
                   message="MSG-1 §5: the site-targeted message never reached "
                           "its target site — the relay dropped "
                           "``target_site_id`` when re-wrapping the envelope")
        assert m0.agent_protocol.last_injected(UTTERANCE).data["utterances"] == \
            ["for M0's site only"]

        # gating: the relay forwarded it but MUST NOT have delivered it locally.
        relay.listener.agent_protocol.assert_not_injected(UTTERANCE)

    def test_message_targeted_at_another_site_is_not_delivered_here(self, site_chain):
        """The other half of the gate: an envelope that travels through a node
        whose site does not match is forwarded, never injected."""
        b = site_chain
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")

        s0.send(_propagated_bus("for nobody here",
                                target_site_id="a-site-that-does-not-exist"))

        # give the message the same budget the positive case needs to arrive
        poll_until(lambda: m0.recorder.snapshot(), timeout=1,
                   message="no traffic reached M0 at all")
        m0.agent_protocol.assert_not_injected(UTTERANCE)
        b.get_relay("R1").listener.agent_protocol.assert_not_injected(UTTERANCE)


# ---------------------------------------------------------------------------
# MSG-1 §5.2 — request/answer correlation
# ---------------------------------------------------------------------------

def _answering_agent(master, utterance_reply: str = "42"):
    """Make the master's agent answer any utterance with one speak chunk.

    ``natural_language_query`` streams whatever ``speak`` messages appear on the
    agent bus for the query-scoped session, so a plain bus handler is enough.
    """
    bus = master.agent_protocol.bus

    def _responder(msg: Message):
        qid = msg.context.get("query_id")
        bus.emit(Message("speak", {"utterance": utterance_reply},
                         {"query_id": qid, "session": msg.context.get("session")}))
        bus.emit(Message("ovos.utterance.handled", {},
                         {"query_id": qid, "session": msg.context.get("session")}))

    bus.on(UTTERANCE, _responder)


def _query(utterance: str = "what is the answer?", metadata=None) -> HiveMessage:
    inner = HiveMessage(HiveMessageType.BUS,
                        payload=Message(UTTERANCE, {"utterances": [utterance]}))
    return HiveMessage(HiveMessageType.QUERY, payload=inner,
                       metadata=metadata if metadata is not None else {})


class TestQueryCorrelationFields:
    """MSG-1 §5.2 — a responding node MUST mint a ``query_id`` and set
    ``originator_peer`` when the request carries neither, MUST carry both back
    unchanged when it does, MUST mark the answer ``is_response``, and MUST set
    ``responder_peer`` to itself.

    Without a minted id an originator that fires two queries cannot tell the
    two answer streams apart; without ``responder_peer`` a CASCADE gatherer
    cannot key responses per responder (AGENT-1 §4.3).
    """

    def _answers(self, satellite):
        got = []
        satellite.shim.emitter.on(HiveMessageType.QUERY, got.append)
        return got

    def test_missing_correlation_fields_are_minted(self, minimal_topology):
        b = minimal_topology
        m0, s0 = b.get_master("M0"), b.get_satellite("S0")
        _answering_agent(m0)

        answers = self._answers(s0)
        s0.send(_query(metadata={}))  # no query_id, no originator_peer

        poll_until(lambda: answers, timeout=8,
                   message="no QUERY answer came back")
        meta = answers[0].metadata
        assert meta.get("query_id"), (
            "MSG-1 §5.2: a node answering a QUERY with no ``query_id`` MUST "
            "mint one, or concurrent answer streams cannot be told apart")
        assert meta.get("originator_peer") == s0.peer, (
            "MSG-1 §5.2: ``originator_peer`` MUST be set to the asking peer "
            "when the request did not carry one")
        # every chunk of one answer stream shares the minted id
        assert {a.metadata.get("query_id") for a in answers} == {meta["query_id"]}

    def test_supplied_correlation_fields_are_carried_unchanged(self, minimal_topology):
        b = minimal_topology
        m0, s0 = b.get_master("M0"), b.get_satellite("S0")
        _answering_agent(m0)

        qid = f"q-{uuid.uuid4().hex[:8]}"
        answers = self._answers(s0)
        s0.send(_query(metadata={"query_id": qid, "originator_peer": s0.peer,
                                 "is_response": False}))

        poll_until(lambda: answers, timeout=8,
                   message="no QUERY answer came back")
        for answer in answers:
            meta = answer.metadata
            assert meta.get("query_id") == qid
            assert meta.get("originator_peer") == s0.peer
            assert meta.get("is_response") is True, (
                "MSG-1 §5.2: an answer MUST be marked ``is_response`` or the "
                "receiver re-admits it as a fresh request")
            assert meta.get("responder_peer") == m0.identity.public_key, (
                "MSG-1 §5.2: ``responder_peer`` MUST name the answering node")


class TestAnswerRoutingFallback:
    """MSG-1 §5.2 — 'Route an answer to ``originator_peer`` directly when it is
    connected here; otherwise walk ``route`` backwards.'

    Only the direct-originator branch had coverage. The fallback is what keeps
    an answer moving when the originator sits two hops away, and its failure
    mode — fanning the answer out downstream instead — hands one peer's private
    answer to its siblings (NODE-1 §5.2, AGENT-1 §3.2).
    """

    def _response(self, originator: str, route) -> HiveMessage:
        inner = HiveMessage(HiveMessageType.BUS,
                            payload=Message("speak", {"utterance": "the answer"}))
        msg = HiveMessage(HiveMessageType.QUERY, payload=inner,
                          metadata={"query_id": "q-fallback",
                                    "originator_peer": originator,
                                    "responder_peer": "somebody-upstream",
                                    "is_response": True})
        msg.replace_route(route)
        return msg

    def test_answer_walks_the_route_back_when_originator_is_not_connected(
            self, two_satellite_star):
        b = two_satellite_star
        s0, s1 = b.get_satellite("S0"), b.get_satellite("S1")

        to_s1, to_s0 = [], []
        s1.shim.emitter.on(HiveMessageType.QUERY, to_s1.append)
        s0.shim.emitter.on(HiveMessageType.QUERY, to_s0.append)

        # The originator is two hops away behind S1, so it is not in
        # ``clients``; the recorded route names S1 as the way back.
        s0.send(self._response(originator="a-peer-behind-S1",
                               route=[{"source": s1.peer,
                                       "targets": ["a-peer-behind-S1"]}]))

        poll_until(lambda: to_s1, timeout=3,
                   message="MSG-1 §5.2: the answer was not sent back along the "
                           "recorded route toward its originator")
        assert to_s0 == [], "the answer must not be echoed to the sender"

    def test_answer_with_no_return_path_is_dropped_not_fanned_out(
            self, two_satellite_star):
        b = two_satellite_star
        s0, s1 = b.get_satellite("S0"), b.get_satellite("S1")

        to_s1, to_s0 = [], []
        s1.shim.emitter.on(HiveMessageType.QUERY, to_s1.append)
        s0.shim.emitter.on(HiveMessageType.QUERY, to_s0.append)

        s0.send(self._response(originator="a-peer-nobody-knows",
                               route=[{"source": "a-node-not-connected-here",
                                       "targets": ["a-peer-nobody-knows"]}]))

        # Nothing to poll for — assert the negative after giving a wrong
        # implementation time to deliver.
        import time
        time.sleep(0.3)
        assert to_s1 == [], (
            "MSG-1 §5.2 / AGENT-1 §3.2: an answer with no return path MUST be "
            "dropped; fanning it downstream hands one peer's answer to its "
            "siblings")
        assert to_s0 == []
