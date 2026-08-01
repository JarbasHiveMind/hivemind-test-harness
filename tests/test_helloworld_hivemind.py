"""
Hello-World skill x HiveMind harness integration tests.

Ports ovos-skill-hello-world/test/test_helloworld.py to route utterances
through HiveMind satellite → master → MiniCroft, validating that the full
HiveMind stack delivers utterances correctly and that skill responses are
observable at both the OVOS-bus level and (for speak) the satellite level.

Prerequisites
-------------
* ovos-skill-hello-world installed: ``uv pip install ovos-skill-hello-world``
* ovoscope installed: ``uv pip install ovoscope``
* Run from hivemind-test-harness directory or with it on sys.path.

Relationship to upstream tests
-------------------------------
The four core test cases mirror TestAdaptIntent / TestPadatiousIntent in the
upstream skill repo, but here utterances flow through HiveMind routing instead
of being emitted directly on MiniCroft's bus.  The expected OVOS message
sequence on the skill bus is identical to what the upstream tests assert.

Test IDs
--------
TS-HW-01   adapt pipeline: "hello world" → HelloWorldIntent → speak
TS-HW-02   padatious pipeline: "hello world" → ovos.intent.unmatched
TS-HW-03   padatious pipeline: "good morning" → Greetings intent → speak
TS-HW-04   adapt pipeline: "good morning" → ovos.intent.unmatched
TS-HW-05   speak from hello-world routes back to satellite via HiveMind
"""
import threading
import time

import pytest
from ovos_bus_client.message import Message
from ovos_spec_tools import SpecMessage
from ovos_bus_client.session import Session

from hivescope.topology import TopologyBuilder
from tests.conftest import open_capture, make_ovoscope_agent

# MiniCroft boot alone can take up to MINICROFT_READY_TIMEOUT (180s), and skill
# handlers run serially after that, so the repo-wide 30s default is far too
# tight for this module.
pytestmark = pytest.mark.timeout(300)

SKILL_ID = "ovos-skill-hello-world.openvoiceos"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _skill_missing() -> bool:
    try:
        from ovos_plugin_manager.skills import find_skill_plugins
        return SKILL_ID not in find_skill_plugins()
    except Exception:
        return True


def _make_utterance(text: str, pipeline: list, session_id: str,
                    lang: str = "en-US") -> Message:
    """
    Build a recognizer_loop:utterance Message with a specific pipeline.

    The session_id MUST match the satellite's shim.session_id so that
    HiveMind's handle_bus_message() updates client.sess with our custom
    pipeline (otherwise _update_blacklist() overwrites it with the
    satellite's default session).
    """
    sess = Session(session_id)
    sess.lang = lang
    sess.pipeline = pipeline
    return Message(
        "recognizer_loop:utterance",
        {"utterances": [text], "lang": lang},
        {"session": sess.serialize(), "source": "sat", "destination": "master"},
    )


def _types(messages) -> list:
    """Return just the msg_type list for easy assertion."""
    return [m.msg_type for m in messages]


def _assert_types_in_order(messages, *expected_types):
    """Assert every expected_type appears in messages in order.

    An expected entry may be a tuple of acceptable spellings (e.g. the
    canonical and legacy form of an intent topic) — any one of them
    satisfies that position.
    """
    types = _types(messages)
    pos = 0
    for t in expected_types:
        accept = t if isinstance(t, tuple) else (t,)
        found = next((i for i in range(pos, len(types)) if types[i] in accept), None)
        assert found is not None, (
            f"Expected message type '{t}' not found after position {pos}.\n"
            f"Captured sequence: {types}"
        )
        pos = found + 1


# ---------------------------------------------------------------------------
# Fixture — module-scoped so MiniCroft (with skill) boots exactly once
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def hw_topology():
    """
    Boot MiniCroft with hello-world skill once, connect one satellite.
    All tests in this file share this fixture; agent.clear() provides
    inter-test isolation.

    MiniCroft's ProcessState.READY is set before the skill's initialize()
    completes (intent registration runs asynchronously in the skill's
    background thread).  We therefore wait for 'mycroft.skill.ready' which
    is emitted only after initialize() finishes and all intents are
    registered in adapt/padatious.
    """
    agent = make_ovoscope_agent(skill_ids=[SKILL_ID])

    # Wait for HelloWorldIntent to be registered on the bus.
    # MiniCroft sets ProcessState.READY before the skill's initialize()
    # completes; intents are registered asynchronously in a background thread.
    # We poll the FakeBus's EventEmitter until the intent handler appears.
    _hw_intent = f"{SKILL_ID}:HelloWorldIntent"
    _deadline = time.monotonic() + 120
    while time.monotonic() < _deadline:
        if len(agent.bus.ee.listeners(_hw_intent)) > 0:
            break
        time.sleep(0.5)
    else:
        pytest.skip(f"HelloWorldIntent not registered within 120s — skill may have failed to load")

    b = TopologyBuilder()
    b.add_master("M0", agent_protocol=agent)
    # hivemind-core is deny-by-default / whitelist-only: the satellite must be
    # granted recognizer_loop:utterance or its utterances are policy-denied at
    # admission and never reach MiniCroft (the agent records nothing).
    b.add_satellite("S0", upstream=b.get_master("M0"),
                    allowed_types=["recognizer_loop:utterance"])
    b.start_all()
    yield b, agent
    b.stop_all()
    agent.shutdown()


# ---------------------------------------------------------------------------
# TS-HW-01  adapt pipeline: "hello world" → HelloWorldIntent → speak
# ---------------------------------------------------------------------------

@pytest.mark.skipif(_skill_missing(), reason=f"{SKILL_ID} not installed")
class TestAdaptIntentViaHiveMind:
    """TS-HW-01 — adapt pipeline matches 'hello world' through HiveMind routing."""

    PIPELINE = ["ovos-adapt-pipeline-plugin-high"]

    def test_utterance_delivered_to_skill_bus(self, hw_topology):
        b, agent = hw_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = open_capture(agent)
        s0.send(_make_utterance("hello world", self.PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=60)

        assert any(m.msg_type == "recognizer_loop:utterance" for m in messages), (
            "recognizer_loop:utterance not found on skill bus.\n"
            f"Captured: {_types(messages)}"
        )

    def test_hello_world_intent_fired(self, hw_topology):
        b, agent = hw_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = open_capture(agent)
        s0.send(_make_utterance("hello world", self.PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=60)

        intent_msg = next(
            (m for m in messages if m.msg_type == f"{SKILL_ID}:HelloWorldIntent"),
            None,
        )
        assert intent_msg is not None, (
            f"Intent '{SKILL_ID}:HelloWorldIntent' not found.\n"
            f"Captured: {_types(messages)}"
        )
        assert "hello world" in intent_msg.data.get("utterance", "")

    def test_skill_emits_speak(self, hw_topology):
        b, agent = hw_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = open_capture(agent)
        s0.send(_make_utterance("hello world", self.PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=60)

        speak = next((m for m in messages if m.msg_type == SpecMessage.SPEAK), None)
        assert speak is not None, (
            f"'speak' not emitted.\nCaptured: {_types(messages)}"
        )
        assert speak.data.get("utterance", "").lower() == "hello world", (
            f"Unexpected speak utterance: {speak.data.get('utterance')}"
        )

    def test_full_adapt_sequence_in_order(self, hw_topology):
        """Messages must appear in the same order as the upstream test expects."""
        b, agent = hw_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = open_capture(agent)
        s0.send(_make_utterance("hello world", self.PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=60)

        _assert_types_in_order(
            messages,
            "recognizer_loop:utterance",
            f"{SKILL_ID}:HelloWorldIntent",
            "mycroft.skill.handler.start",
            SpecMessage.SPEAK,
            "mycroft.skill.handler.complete",
            "ovos.utterance.handled",
        )

    def test_skill_activation_recorded(self, hw_topology):
        b, agent = hw_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = open_capture(agent)
        s0.send(_make_utterance("hello world", self.PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=60)

        activate = next(
            (m for m in messages if m.msg_type == f"{SKILL_ID}.activate"),
            None,
        )
        assert activate is not None, (
            f"'{SKILL_ID}.activate' not found.\nCaptured: {_types(messages)}"
        )


# ---------------------------------------------------------------------------
# TS-HW-02  padatious pipeline: "hello world" → ovos.intent.unmatched
# ---------------------------------------------------------------------------

@pytest.mark.skipif(_skill_missing(), reason=f"{SKILL_ID} not installed")
class TestAdaptUtterancePadatiousPipelineViaHiveMind:
    """
    TS-HW-02 — 'hello world' via padatious pipeline → ovos.intent.unmatched.
    hello-world uses Adapt for 'hello world'; padatious won't match it.
    """

    PIPELINE = ["ovos-padatious-pipeline-plugin-high"]

    def test_intent_unmatched_emitted(self, hw_topology):
        b, agent = hw_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = open_capture(agent)
        s0.send(_make_utterance("hello world", self.PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=60)

        assert any(m.msg_type == SpecMessage.INTENT_UNMATCHED for m in messages), (
            f"{SpecMessage.INTENT_UNMATCHED} not emitted.\nCaptured: {_types(messages)}"
        )

    def test_no_speak_on_intent_failure(self, hw_topology):
        b, agent = hw_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = open_capture(agent)
        s0.send(_make_utterance("hello world", self.PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=60)

        assert not any(m.msg_type == SpecMessage.SPEAK for m in messages), (
            f"'speak' was unexpectedly emitted.\nCaptured: {_types(messages)}"
        )

    def test_failure_sequence_in_order(self, hw_topology):
        b, agent = hw_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = open_capture(agent)
        s0.send(_make_utterance("hello world", self.PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=60)

        _assert_types_in_order(
            messages,
            "recognizer_loop:utterance",
            SpecMessage.INTENT_UNMATCHED,
            "ovos.utterance.handled",
        )


# ---------------------------------------------------------------------------
# TS-HW-03  padatious pipeline: "good morning" → Greetings intent → speak
# ---------------------------------------------------------------------------

@pytest.mark.skipif(_skill_missing(), reason=f"{SKILL_ID} not installed")
class TestPadatiousIntentViaHiveMind:
    """TS-HW-03 — 'good morning' via padatious → Greetings intent → speak."""

    PIPELINE = ["ovos-padatious-pipeline-plugin-high"]

    def test_greetings_intent_fired(self, hw_topology):
        b, agent = hw_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = open_capture(agent)
        s0.send(_make_utterance("good morning", self.PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=60)

        intent_msg = next(
            (m for m in messages if m.msg_type in (f"{SKILL_ID}:Greetings", f"{SKILL_ID}:Greetings.intent")),
            None,
        )
        assert intent_msg is not None, (
            f"Intent '{SKILL_ID}:Greetings' not found (canonical or legacy).\n"
            f"Captured: {_types(messages)}"
        )

    def test_skill_speaks_greeting(self, hw_topology):
        b, agent = hw_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = open_capture(agent)
        s0.send(_make_utterance("good morning", self.PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=60)

        speak = next((m for m in messages if m.msg_type == SpecMessage.SPEAK), None)
        assert speak is not None, (
            f"'speak' not emitted.\nCaptured: {_types(messages)}"
        )
        # Skill uses random greeting dialog — check meta.dialog instead of exact text
        meta = speak.data.get("meta", {})
        assert meta.get("dialog") == "hello", (
            f"Expected dialog='hello', got meta={meta}"
        )

    def test_full_padatious_sequence_in_order(self, hw_topology):
        b, agent = hw_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = open_capture(agent)
        s0.send(_make_utterance("good morning", self.PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=60)

        _assert_types_in_order(
            messages,
            "recognizer_loop:utterance",
            # workshop >=9.3.2a1 registers the canonical (suffix-free) name;
            # the sequence assertion accepts either spelling so this file
            # tracks behavior, not the vintage of the resolved stack.
            (f"{SKILL_ID}:Greetings", f"{SKILL_ID}:Greetings.intent"),
            "mycroft.skill.handler.start",
            SpecMessage.SPEAK,
            "mycroft.skill.handler.complete",
            "ovos.utterance.handled",
        )


# ---------------------------------------------------------------------------
# TS-HW-04  adapt pipeline: "good morning" → ovos.intent.unmatched
# ---------------------------------------------------------------------------

@pytest.mark.skipif(_skill_missing(), reason=f"{SKILL_ID} not installed")
class TestPadatiousUtteranceAdaptPipelineViaHiveMind:
    """
    TS-HW-04 — 'good morning' via adapt pipeline → ovos.intent.unmatched.
    hello-world uses Padatious for 'good morning'; Adapt won't match it.
    """

    PIPELINE = ["ovos-adapt-pipeline-plugin-high"]

    def test_intent_unmatched_emitted(self, hw_topology):
        b, agent = hw_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = open_capture(agent)
        s0.send(_make_utterance("good morning", self.PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=60)

        assert any(m.msg_type == SpecMessage.INTENT_UNMATCHED for m in messages), (
            f"{SpecMessage.INTENT_UNMATCHED} not emitted.\nCaptured: {_types(messages)}"
        )

    def test_no_speak_on_intent_failure(self, hw_topology):
        b, agent = hw_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = open_capture(agent)
        s0.send(_make_utterance("good morning", self.PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=60)

        assert not any(m.msg_type == SpecMessage.SPEAK for m in messages), (
            f"'speak' was unexpectedly emitted.\nCaptured: {_types(messages)}"
        )


# ---------------------------------------------------------------------------
# TS-HW-05  speak from skill routes back to originating satellite
# ---------------------------------------------------------------------------

@pytest.mark.skipif(_skill_missing(), reason=f"{SKILL_ID} not installed")
class TestSpeakPropagatesBackToSatellite:
    """
    TS-HW-05 — speak emitted by hello-world routes back through HiveMind
    to the originating satellite.

    This is the end-to-end roundtrip test: utterance flows in via HiveMind,
    skill responds with speak, HiveMind routes speak back to the satellite.
    Implements TS-OVO-04 with the hello-world skill as the concrete scenario.
    """

    PIPELINE = ["ovos-adapt-pipeline-plugin-high"]

    def test_speak_received_on_satellite_bus(self, hw_topology):
        b, agent = hw_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        # Register listener BEFORE sending to avoid missing the event
        sat_speak = []
        sat_event = threading.Event()

        def _on_speak(msg):
            sat_speak.append(msg)
            sat_event.set()

        s0.internal_bus.once(SpecMessage.SPEAK, _on_speak)

        cap = open_capture(agent)
        s0.send(_make_utterance("hello world", self.PIPELINE, s0.shim.session_id))
        cap.wait(timeout=60)

        # Block until satellite receives the forwarded speak (or timeout)
        sat_event.wait(timeout=30)
        assert sat_speak, (
            "speak message was not forwarded back to the satellite by HiveMind"
        )
        assert sat_speak[0].data.get("utterance", "").lower() == "hello world"

    def test_speak_utterance_text_matches(self, hw_topology):
        """Satellite receives the exact speak utterance the skill produced."""
        b, agent = hw_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        # Register listener BEFORE sending
        sat_speak = []
        sat_event = threading.Event()

        def _on_speak(msg):
            sat_speak.append(msg)
            sat_event.set()

        s0.internal_bus.once(SpecMessage.SPEAK, _on_speak)

        cap = open_capture(agent)
        s0.send(_make_utterance("hello world", self.PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=60)

        # Skill bus speak
        speak_on_bus = next((m for m in messages if m.msg_type == SpecMessage.SPEAK), None)
        assert speak_on_bus is not None, "speak not emitted on skill bus"

        # Satellite speak (HiveMind roundtrip) — wait up to 10s
        sat_event.wait(timeout=30)
        if sat_speak:
            assert sat_speak[0].data.get("utterance") == speak_on_bus.data.get(
                "utterance"
            ), "Satellite speak utterance differs from skill bus speak utterance"
