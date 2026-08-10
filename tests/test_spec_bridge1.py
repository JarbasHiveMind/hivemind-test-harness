"""
HIVEMIND-BRIDGE-1 conformance — the Layer-1 boundary MUSTs that had no test.

The bridge is where a HiveMind envelope becomes an OVOS bus message. Its whole
contract is 'change as little as possible, and change exactly these things',
which is precisely the kind of rule that erodes silently: every new feature is
tempted to stamp one more field on the way through.

Pinned here:

  * BRIDGE-1 §2 — the Layer-1 payload is opaque apart from the authorized
                  boundary operations: session install, ``destination``
                  defaulting, and ``peer``/``source`` stamping. Nothing else
                  in the payload is touched.
  * BRIDGE-1 §4 — OVOS-SESSION-1 contents are preserved intact; a field may be
                  omitted but never carried as ``null``
  * BRIDGE-1 §4 — a stale ``pipeline`` is not reattached to a later message

Not pinned, and why — see the notes at the bottom of this file: BRIDGE-1 §6
(per-peer FIFO) cannot be honestly evaluated in the in-process shim, and the
§4 "MUST NOT silently drop an unknown session field" clause turns out not to
hold in the shipped code, so it is a conformance finding rather than something
to pin.
"""
from ovos_bus_client.message import Message

from hivescope.topology import TopologyBuilder

from tests.conftest import poll_until


ALLOWED = "recognizer_loop:utterance"


def _bridge():
    b = TopologyBuilder()
    b.add_master("M0")
    b.add_satellite("S0", upstream=b.get_master("M0"),
                    allowed_types=[ALLOWED, "speak"])
    b.start_all()
    return b


def _inject(b, message, msg_type=None):
    """Send ``message`` from S0 and return what reached M0's agent bus."""
    s0, m0 = b.get_satellite("S0"), b.get_master("M0")
    s0.send(message)
    return poll_until(
        lambda: m0.agent_protocol.last_injected(msg_type or message.msg_type),
        timeout=3,
        message=f"{message.msg_type} never reached the agent bus")


# ---------------------------------------------------------------------------
# BRIDGE-1 §2 — the payload is opaque apart from the authorized operations
# ---------------------------------------------------------------------------

class TestOnlyAuthorizedBoundaryOperationsTouchThePayload:
    """BRIDGE-1 §2 — 'the Layer-1 BUS payload is treated as opaque except for
    the authorized boundary operations.'

    The three sanctioned operations are named by the spec. ``peer``/``source``
    stamping is already pinned (``assert_source_stamped``); the ``destination``
    default is not, and it is the one with teeth: an OVOS message with no
    destination is a *broadcast* on the agent bus, so failing to default it
    turns every satellite utterance into traffic every service sees.
    """

    def test_a_message_with_no_destination_is_defaulted_to_skills(self):
        b = _bridge()
        try:
            injected = _inject(b, Message(ALLOWED, {"utterances": ["hi"]}, {}))
            assert injected.context.get("destination") == "skills", (
                "a payload with no destination MUST be routed to the agent, "
                "not left to broadcast across the whole internal bus; got "
                f"{injected.context.get('destination')!r}")
        finally:
            b.stop_all()

    def test_an_explicit_destination_is_left_alone(self):
        b = _bridge()
        try:
            injected = _inject(b, Message(
                ALLOWED, {"utterances": ["hi"]},
                {"destination": ["some-service"]}))
            assert injected.context["destination"] == ["some-service"], (
                "the boundary defaults a MISSING destination; it MUST NOT "
                "rewrite one the peer chose")
        finally:
            b.stop_all()

    def test_the_payload_data_is_forwarded_unchanged(self):
        """The opacity half of §2: the bridge is authorized to touch the
        *context*, never the message body."""
        b = _bridge()
        try:
            data = {"utterances": ["hi"], "lang": "pt-pt",
                    "an_unknown_field": {"nested": [1, 2, 3]}}
            injected = _inject(b, Message(ALLOWED, dict(data), {}))
            assert injected.data == data, (
                "the bridge MUST treat the payload body as opaque; it was "
                f"rewritten to {injected.data}")
        finally:
            b.stop_all()


# ---------------------------------------------------------------------------
# BRIDGE-1 §4 — session contents survive the crossing
# ---------------------------------------------------------------------------

def _session_of(b, payload_session):
    """Send one message carrying ``payload_session`` and return the session
    that reached the agent bus."""
    m0, s0 = b.get_master("M0"), b.get_satellite("S0")
    # The peer must name its own pinned session id; a payload naming a
    # different id does not move the connection session (BRIDGE-1 §4, already
    # pinned in test_e2e_session.py) and would not exercise this path.
    sid = m0.hm_protocol.clients[s0.peer].sess.session_id
    ctx = {"session": {"session_id": sid, **payload_session}}
    return _inject(b, Message(ALLOWED, {"utterances": ["hi"]}, ctx)).context["session"]


class TestSessionIsPreservedIntact:
    """BRIDGE-1 §4 — 'preserve OVOS-SESSION-1 contents intact; a field may be
    omitted but never carried as ``null``.'

    The null rule is the load-bearing one: OVOS-SESSION-1 treats an explicit
    ``null`` as malformed, so forwarding one makes every downstream consumer
    choose between crashing and inventing a default — and they will not all
    invent the same one.
    """

    def test_no_session_field_is_forwarded_as_null(self):
        b = _bridge()
        try:
            session = _session_of(b, {"lang": None, "site_id": "the-lab"})
            nulls = [k for k, v in session.items() if v is None]
            assert not nulls, (
                f"session fields {nulls} crossed the boundary as null; "
                "OVOS-SESSION-1 §2 makes an explicit null malformed")
        finally:
            b.stop_all()

    def test_a_non_null_session_field_crosses_unchanged(self):
        """The control: stripping nulls must not take the rest of the session
        with it."""
        b = _bridge()
        try:
            session = _session_of(b, {"site_id": "the-lab", "lang": "pt-PT"})
            assert session.get("site_id") == "the-lab"
            assert session.get("lang") == "pt-PT", (
                "a session field the peer set MUST survive the crossing; got "
                f"{session.get('lang')!r}")
        finally:
            b.stop_all()

    def test_a_stale_pipeline_is_not_reattached_to_a_later_message(self):
        """BRIDGE-1 §4 / OVOS-SESSION-1 §2 — each bus message owns its own
        outbound pipeline. Reattaching the previous message's pipeline makes a
        satellite's second utterance inherit the first one's intent-matching
        plan, which is a real and very confusing misroute."""
        b = _bridge()
        try:
            with_pipeline = _session_of(b, {"pipeline": ["converse", "fallback"]})
            assert with_pipeline.get("pipeline") == ["converse", "fallback"], (
                "a message that carries its own pipeline must keep it")

            without = _session_of(b, {})
            assert "pipeline" not in without, (
                "a message that did NOT carry a pipeline must not inherit the "
                f"previous message's; got {without.get('pipeline')!r}")
        finally:
            b.stop_all()


# ---------------------------------------------------------------------------
# BRIDGE-1 §6 — NOT PINNED HERE, deliberately
# ---------------------------------------------------------------------------
#
# §6 requires per-peer FIFO in both directions. Neither half can be honestly
# evaluated in the in-process simulator:
#
#   * Outbound — the requirement is that writes are serialized through one
#     ordered queue. In production that is Tornado's single-threaded
#     ``IOLoop.add_callback`` queue; in the shim ``send`` delivers
#     synchronously on the caller's stack, so ordering is a property of Python
#     statement order, not of the code under test. A test here would pass with
#     the real queue removed.
#   * Inbound — same problem in reverse: the shim hands each frame straight to
#     ``handle_message``, so there is no concurrency for a FIFO to preserve.
#
# Pinning either needs a real socket and a real IOLoop — the loopback network
# protocol with a concurrent writer — which is a transport-level test, not a
# bridge-level one. Left as a known gap rather than a green that means nothing.

#
# BRIDGE-1 §4 — "MUST NOT silently drop or reshape unknown session fields" is
# NOT pinned here because the shipped code does not satisfy it. Inbound session
# payloads go through ``Session.from_message`` (``handle_bus_message``), whose
# schema is fixed: a field OVOS-SESSION-1 does not name is dropped on the way
# in, before the bridge's null-strip ever sees it. Verified against a live
# node: a session carrying ``{"future_field": {...}}`` reaches the agent bus
# without it. That is a divergence to raise against the code or the spec, not
# a MUST to pin green — writing an xfail here would only restate the finding.
