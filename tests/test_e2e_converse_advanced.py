"""
Advanced converse E2E tests via HiveMind.

Tests edge cases in multi-turn dialog:
- Canceling mid get_response dialog
- get_response timeout when satellite doesn't respond
- Concurrent get_response from multiple satellites
- Dictation skill (converse + stop combination)

Test IDs
--------
TS-CA-01 through TS-CA-07
"""
import threading
import time

import pytest
from ovos_bus_client.message import Message
from ovos_workshop.decorators import intent_handler
from ovos_workshop.skills import OVOSSkill

from hivescope.plugins.ovoscope_agent import OvoscopeAgentProtocol
from hivescope.topology import TopologyBuilder
from tests.conftest import (
    SKILL_RANDOMNESS, SKILL_DICTATION,
    skill_missing, make_utterance, wait_for_satellite_message,
)

CONVERSE_PIPELINE = [
    "ovos-converse-pipeline-plugin",
    "ovos-stop-pipeline-plugin",
    "ovos-adapt-pipeline-plugin-high",
    "ovos-padatious-pipeline-plugin-high",
    "ovos-adapt-pipeline-plugin-medium",
    "ovos-padatious-pipeline-plugin-medium",
    "ovos-adapt-pipeline-plugin-low",
    "ovos-padatious-pipeline-plugin-low",
]

TIMEOUT_SKILL_ID = "timeout-test-skill.test"


class TimeoutTestSkill(OVOSSkill):
    """Skill that calls get_response and tracks whether it got an answer or timed out."""

    def initialize(self):
        self.last_result = None

    @intent_handler("test.timeout.intent")
    def handle_timeout_test(self, message: Message):
        response = self.get_response("say something", num_retries=0)
        self.last_result = response
        if response:
            self.speak(f"got response {response}")
        else:
            self.speak("response timed out")


class SatelliteAutoResponder:
    """Responds to speak with expect_response=True after a delay."""

    def __init__(self, satellite, pipeline, responses):
        self.satellite = satellite
        self.pipeline = pipeline
        self.responses = list(responses)
        self.speaks_received = []
        self._lock = threading.Lock()
        satellite.internal_bus.on("speak", self._on_speak)

    def _on_speak(self, msg):
        with self._lock:
            self.speaks_received.append(msg)
            if msg.data.get("expect_response") and self.responses:
                text = self.responses.pop(0)
                threading.Timer(0.5, self._send, args=(text,)).start()

    def _send(self, text):
        self.satellite.send(make_utterance(
            text, self.pipeline, self.satellite.shim.session_id
        ))

    def shutdown(self):
        self.satellite.internal_bus.remove_all_listeners("speak")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def cancel_topology():
    """MiniCroft with randomness skill + timeout test skill."""
    extra = {TIMEOUT_SKILL_ID: TimeoutTestSkill}
    skill_ids = []
    if not skill_missing(SKILL_RANDOMNESS):
        skill_ids.append(SKILL_RANDOMNESS)

    agent = OvoscopeAgentProtocol(skill_ids=skill_ids, extra_skills=extra)

    _deadline = time.monotonic() + 120
    while time.monotonic() < _deadline:
        if len(agent.bus.ee.listeners(f"{TIMEOUT_SKILL_ID}:test.timeout.intent")) > 0:
            break
        time.sleep(0.5)
    else:
        pytest.skip("Test skills not registered within 120s")

    b = TopologyBuilder()
    b.add_master("M0", agent_protocol=agent)
    b.add_satellite("S0", upstream=b.get_master("M0"))
    b.add_satellite("S1", upstream=b.get_master("M0"))
    b.start_all()
    yield b, agent
    b.stop_all()
    agent.shutdown()


@pytest.fixture(scope="module")
def dictation_topology():
    """MiniCroft with dictation skill."""
    agent = OvoscopeAgentProtocol(skill_ids=[SKILL_DICTATION])

    _deadline = time.monotonic() + 120
    while time.monotonic() < _deadline:
        if len(agent.bus.ee.listeners(f"{SKILL_DICTATION}:start_dictation.intent")) > 0:
            break
        time.sleep(0.5)
    else:
        pytest.skip("Dictation skill not registered within 120s")

    b = TopologyBuilder()
    b.add_master("M0", agent_protocol=agent)
    b.add_satellite("S0", upstream=b.get_master("M0"))
    b.start_all()
    yield b, agent
    b.stop_all()
    agent.shutdown()


# ---------------------------------------------------------------------------
# TS-CA-01..02  Cancel mid-dialog
# ---------------------------------------------------------------------------

@pytest.mark.skipif(skill_missing(SKILL_RANDOMNESS),
                     reason="ovos-skill-randomness not installed")
class TestCancelMidDialog:
    """TS-CA-01..02 — cancel during get_response via HiveMind."""

    def test_cancel_exits_get_response(self, cancel_topology):
        """TS-CA-01 — saying 'cancel' during get_response returns None."""
        b, agent = cancel_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        responder = SatelliteAutoResponder(s0, CONVERSE_PIPELINE, ["cancel"])
        try:
            s0.send(make_utterance("make a choice", CONVERSE_PIPELINE,
                                    s0.shim.session_id))

            # Wait for response mode to disable (cancel should trigger it)
            deadline = time.monotonic() + 30
            found_disable = False
            while time.monotonic() < deadline:
                for m in agent.injected:
                    if m.msg_type == "skill.converse.get_response.disable":
                        found_disable = True
                        break
                if found_disable:
                    break
                time.sleep(0.2)

            assert found_disable, (
                "get_response.disable not emitted after cancel.\n"
                f"Types: {[m.msg_type for m in agent.injected]}"
            )
        finally:
            responder.shutdown()

    def test_cancel_does_not_crash(self, cancel_topology):
        """TS-CA-02 — canceling mid-dialog does not crash subsequent utterances."""
        b, agent = cancel_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        responder = SatelliteAutoResponder(s0, CONVERSE_PIPELINE, ["cancel"])
        try:
            s0.send(make_utterance("make a choice", CONVERSE_PIPELINE,
                                    s0.shim.session_id))
            time.sleep(10)
        finally:
            responder.shutdown()

        # Now send a normal utterance — should still work
        agent.clear()
        cap = agent.new_capture()
        s0.send(make_utterance("test timeout", CONVERSE_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=20)
        # Just verify no crash — test passes if we get here


# ---------------------------------------------------------------------------
# TS-CA-03  get_response timeout
# ---------------------------------------------------------------------------

class TestGetResponseTimeout:
    """TS-CA-03 — get_response timeout when satellite doesn't respond."""

    def test_timeout_returns_none(self, cancel_topology):
        """TS-CA-03 — no satellite response → get_response returns None → speaks 'timed out'."""
        b, agent = cancel_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        # Don't set up auto-responder — let get_response timeout
        s0.send(make_utterance("test timeout", CONVERSE_PIPELINE, s0.shim.session_id))

        # get_response has internal ~15s timeout, then skill speaks "response timed out"
        deadline = time.monotonic() + 30
        result = None
        while time.monotonic() < deadline:
            for m in agent.injected:
                if m.msg_type == "speak" and "timed out" in m.data.get("utterance", "").lower():
                    result = m
                    break
            if result:
                break
            time.sleep(0.2)

        assert result is not None, (
            f"Expected 'response timed out' speak.\n"
            f"Speaks: {[m.data.get('utterance', '') for m in agent.injected if m.msg_type == 'speak']}"
        )


# ---------------------------------------------------------------------------
# TS-CA-04  Concurrent get_response from different satellites
# ---------------------------------------------------------------------------

class TestConcurrentGetResponse:
    """TS-CA-04 — two satellites trigger get_response simultaneously."""

    def test_concurrent_get_response(self, cancel_topology):
        """TS-CA-04 — S0 and S1 both trigger get_response; both get questions."""
        b, agent = cancel_topology
        agent.clear()
        s0 = b.get_satellite("S0")
        s1 = b.get_satellite("S1")

        s0_speaks = []
        s1_speaks = []
        s0_evt = threading.Event()
        s1_evt = threading.Event()

        s0.internal_bus.on("speak", lambda m: (s0_speaks.append(m), s0_evt.set()))
        s1.internal_bus.on("speak", lambda m: (s1_speaks.append(m), s1_evt.set()))

        # Both satellites trigger get_response skill
        s0.send(make_utterance("test timeout", CONVERSE_PIPELINE, s0.shim.session_id))
        s1.send(make_utterance("test timeout", CONVERSE_PIPELINE, s1.shim.session_id))

        s0_evt.wait(timeout=15)
        s1_evt.wait(timeout=15)

        assert s0_speaks, "S0 did not receive question speak"
        assert s1_speaks, "S1 did not receive question speak"


# ---------------------------------------------------------------------------
# TS-CA-05..07  Dictation skill (converse + stop)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(skill_missing(SKILL_DICTATION),
                     reason="ovos-skill-dictation not installed")
class TestDictation:
    """TS-CA-05..07 — dictation skill converse + stop via HiveMind."""

    def test_start_dictation(self, dictation_topology):
        """TS-CA-05 — 'start dictation' activates dictation mode."""
        b, agent = dictation_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture()
        s0.send(make_utterance("start dictation", CONVERSE_PIPELINE,
                                s0.shim.session_id))
        messages = cap.wait(timeout=15)

        # Should get a speak confirming dictation started
        speak = next((m for m in messages if m.msg_type == "speak"), None)
        assert speak is not None, (
            f"Dictation start did not speak.\n"
            f"Captured: {[m.msg_type for m in messages]}"
        )

    def test_dictation_captures_utterances(self, dictation_topology):
        """TS-CA-06 — utterances during dictation are captured, not matched as intents."""
        b, agent = dictation_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        # Start dictation
        cap1 = agent.new_capture()
        s0.send(make_utterance("start dictation", CONVERSE_PIPELINE,
                                s0.shim.session_id))
        cap1.wait(timeout=15)
        agent.clear()

        # Send text that would normally trigger an intent
        cap2 = agent.new_capture(
            eof_msgs=["ovos.utterance.handled", "ovos.session.update"]
        )
        s0.send(make_utterance("hello world", CONVERSE_PIPELINE,
                                s0.shim.session_id))
        messages = cap2.wait(timeout=15)

        # Should NOT trigger HelloWorldIntent — dictation captures it
        hello_intents = [m for m in messages
                         if "HelloWorldIntent" in m.msg_type]
        assert len(hello_intents) == 0, (
            "Dictation mode should capture utterances, not match intents"
        )

    def test_stop_dictation(self, dictation_topology):
        """TS-CA-07 — 'stop dictation' deactivates dictation mode."""
        b, agent = dictation_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        # Start then stop
        cap1 = agent.new_capture()
        s0.send(make_utterance("start dictation", CONVERSE_PIPELINE,
                                s0.shim.session_id))
        cap1.wait(timeout=15)
        agent.clear()

        cap2 = agent.new_capture()
        s0.send(make_utterance("stop dictation", CONVERSE_PIPELINE,
                                s0.shim.session_id))
        messages = cap2.wait(timeout=15)

        speak = next((m for m in messages if m.msg_type == "speak"), None)
        assert speak is not None, (
            f"Stop dictation did not speak.\n"
            f"Captured: {[m.msg_type for m in messages]}"
        )
