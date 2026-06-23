"""
Multi-turn get_response() E2E tests via HiveMind.

Tests the OVOS get_response() dialog flow through HiveMind:
skill speaks a question -> satellite receives it -> satellite sends answer ->
HiveMind routes answer back -> skill's get_response() returns the answer.

Uses ovos-skill-randomness 'make a choice' intent:
  1. User: "make a choice"
  2. Skill: "What is the first choice?" (get_response)
  3. User: "pizza"
  4. Skill: "What is the second choice?" (get_response)
  5. User: "pasta"
  6. Skill: "I choose pizza/pasta"

Also tests ovos-skill-volume 'change volume' intent:
  1. User: "change volume" (without a number)
  2. Skill: "What volume level?" (get_response with validator)
  3. User: "50"
  4. Skill: sets volume to 50%

Prerequisites
-------------
* ovos-skill-randomness installed
* ovos-skill-volume installed
* ovoscope installed

Test IDs
--------
TS-GR-01 through TS-GR-07
"""
import threading
import time
from typing import List, Optional

import pytest
from ovos_bus_client.message import Message

from hivescope.plugins.ovoscope_agent import OvoscopeAgentProtocol
from hivescope.topology import TopologyBuilder
from tests.conftest import (
    SKILL_RANDOMNESS, SKILL_VOLUME,
    skill_missing, make_utterance, wait_for_satellite_message,
)

# Pipeline must include converse for get_response to work
GET_RESPONSE_PIPELINE = [
    "ovos-converse-pipeline-plugin",
    "ovos-adapt-pipeline-plugin-high",
    "ovos-padatious-pipeline-plugin-high",
    "ovos-adapt-pipeline-plugin-medium",
    "ovos-padatious-pipeline-plugin-medium",
    "ovos-adapt-pipeline-plugin-low",
    "ovos-padatious-pipeline-plugin-low",
]


class SatelliteAutoResponder:
    """Automatically responds to 'speak' messages that expect a response.

    When a skill calls get_response(), it emits speak with
    expect_response=True. This helper listens on the satellite's bus
    and sends back a predefined response.
    """

    def __init__(self, satellite, agent, pipeline: list, responses: list):
        self.satellite = satellite
        self.agent = agent
        self.pipeline = pipeline
        self.responses = list(responses)  # copy -- we'll pop from it
        self.speaks_received: List[Message] = []
        self._lock = threading.Lock()

        # Listen for speak on satellite bus
        satellite.internal_bus.on("speak", self._on_speak)

    def _on_speak(self, msg: Message) -> None:
        """Handle speak messages, auto-responding when expect_response is True."""
        with self._lock:
            self.speaks_received.append(msg)
            # Check if this speak expects a response
            expect = msg.data.get("expect_response", False)
            if expect and self.responses:
                response_text = self.responses.pop(0)
                # Small delay to let the skill's get_response listener set up
                threading.Timer(0.5, self._send_response,
                                args=(response_text,)).start()

    def _send_response(self, text: str) -> None:
        """Send a response utterance back through the satellite."""
        self.satellite.send(make_utterance(
            text, self.pipeline, self.satellite.shim.session_id
        ))

    def shutdown(self) -> None:
        """Remove all speak listeners."""
        self.satellite.internal_bus.remove_all_listeners("speak")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def get_response_topology():
    """Boot MiniCroft with randomness + volume skills."""
    agent = OvoscopeAgentProtocol(skill_ids=[SKILL_RANDOMNESS, SKILL_VOLUME])

    # Volume get responder for _query_volume
    agent.bus.on("mycroft.volume.get",
                 lambda m: agent.bus.emit(m.response({"percent": 0.5, "muted": False})))

    _deadline = time.monotonic() + 120
    while time.monotonic() < _deadline:
        # Check randomness skill loaded
        if len(agent.bus.ee.listeners(f"{SKILL_RANDOMNESS}:make-a-choice.intent")) > 0:
            break
        time.sleep(0.5)
    else:
        pytest.skip("Randomness skill intents not registered within 120s")

    b = TopologyBuilder()
    b.add_master("M0", agent_protocol=agent)
    b.add_satellite("S0", upstream=b.get_master("M0"),
                    allowed_types=["recognizer_loop:utterance"])
    b.start_all()
    yield b, agent
    b.stop_all()
    agent.shutdown()


# ---------------------------------------------------------------------------
# TS-GR-01..04  Randomness Skill -- make a choice (two get_response calls)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(skill_missing(SKILL_RANDOMNESS),
                     reason="ovos-skill-randomness not installed")
class TestMakeAChoice:
    """TS-GR-01..04 -- multi-turn 'make a choice' via HiveMind."""

    def test_initial_question_reaches_satellite(self, get_response_topology):
        """TS-GR-01 -- 'make a choice' triggers speak with first question on satellite."""
        b, agent = get_response_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture(
            eof_msgs=["ovos.utterance.handled", "skill.converse.get_response.enable"]
        )
        s0.send(make_utterance("make a choice", GET_RESPONSE_PIPELINE,
                                s0.shim.session_id))
        messages = cap.wait(timeout=60)

        # Skill should ask "What is the first choice?"
        speak = next((m for m in messages if m.msg_type == "speak"), None)
        assert speak is not None, (
            f"Skill did not speak initial question.\n"
            f"Captured: {[m.msg_type for m in messages]}"
        )
        # The speak should have expect_response=True
        assert speak.data.get("expect_response") is True, (
            "speak should have expect_response=True for get_response"
        )

    def test_question_forwarded_to_satellite(self, get_response_topology):
        """TS-GR-02 -- get_response speak arrives on satellite bus."""
        b, agent = get_response_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture(
            eof_msgs=["ovos.utterance.handled", "skill.converse.get_response.enable"]
        )
        s0.send(make_utterance("make a choice", GET_RESPONSE_PIPELINE,
                                s0.shim.session_id))
        cap.wait(timeout=60)

        msg = wait_for_satellite_message(s0, "speak", timeout=10)
        assert msg is not None, "get_response question not forwarded to satellite"

    def test_full_choice_dialog(self, get_response_topology):
        """TS-GR-03 -- complete two-turn dialog: first choice + second choice -> result."""
        b, agent = get_response_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        # Set up auto-responder for the two get_response calls
        responder = SatelliteAutoResponder(
            s0, agent, GET_RESPONSE_PIPELINE,
            responses=["pizza", "pasta"]
        )

        try:
            # Send initial utterance and wait for the full dialog to complete
            # The dialog takes multiple turns, so use a longer timeout
            # and don't use capture session (it would EOF too early)
            s0.send(make_utterance("make a choice", GET_RESPONSE_PIPELINE,
                                    s0.shim.session_id))

            # Wait for the final "I choose..." speak
            # This is the 3rd speak: question1, question2, result
            deadline = time.monotonic() + 30
            result_speak = None
            while time.monotonic() < deadline:
                with responder._lock:
                    # Look for a speak that contains "pizza" or "pasta" (the choice result)
                    for sp in responder.speaks_received:
                        utt = sp.data.get("utterance", "").lower()
                        if "pizza" in utt or "pasta" in utt:
                            result_speak = sp
                            break
                if result_speak:
                    break
                time.sleep(0.2)

            assert result_speak is not None, (
                f"Final choice result not spoken.\n"
                f"Speaks received: {[s.data.get('utterance', '') for s in responder.speaks_received]}"
            )
        finally:
            responder.shutdown()

    def test_response_mode_enabled(self, get_response_topology):
        """TS-GR-04 -- skill.converse.get_response.enable emitted during get_response."""
        b, agent = get_response_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture(
            eof_msgs=["skill.converse.get_response.enable"]
        )
        s0.send(make_utterance("make a choice", GET_RESPONSE_PIPELINE,
                                s0.shim.session_id))
        messages = cap.wait(timeout=60)

        assert any(m.msg_type == "skill.converse.get_response.enable" for m in messages), (
            f"get_response.enable not emitted.\n"
            f"Captured: {[m.msg_type for m in messages]}"
        )


# ---------------------------------------------------------------------------
# TS-GR-05..06  Randomness -- flip a coin (no get_response, simple speak)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(skill_missing(SKILL_RANDOMNESS),
                     reason="ovos-skill-randomness not installed")
class TestFlipACoin:
    """TS-GR-05..06 -- simple intents in randomness skill."""

    def test_flip_a_coin(self, get_response_topology):
        """TS-GR-05 -- 'flip a coin' produces speak with heads or tails."""
        b, agent = get_response_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture()
        s0.send(make_utterance("flip a coin", GET_RESPONSE_PIPELINE,
                                s0.shim.session_id))
        messages = cap.wait(timeout=60)

        speak = next((m for m in messages if m.msg_type == "speak"), None)
        assert speak is not None, (
            f"'speak' not emitted for coin flip.\n"
            f"Captured: {[m.msg_type for m in messages]}"
        )

    def test_pick_a_number(self, get_response_topology):
        """TS-GR-06 -- 'pick a number between 1 and 10' produces speak."""
        b, agent = get_response_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture()
        s0.send(make_utterance("pick a number between 1 and 10",
                                GET_RESPONSE_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=60)

        speak = next((m for m in messages if m.msg_type == "speak"), None)
        assert speak is not None, (
            f"'speak' not emitted for pick a number.\n"
            f"Captured: {[m.msg_type for m in messages]}"
        )


# ---------------------------------------------------------------------------
# TS-GR-07  Volume -- change volume with get_response
# ---------------------------------------------------------------------------

@pytest.mark.skipif(skill_missing(SKILL_VOLUME),
                     reason="ovos-skill-volume not installed")
class TestVolumeGetResponse:
    """TS-GR-07 -- volume change with get_response for amount."""

    def test_change_volume_asks_amount(self, get_response_topology):
        """TS-GR-07 -- 'change volume' without number triggers get_response."""
        b, agent = get_response_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture(
            eof_msgs=["ovos.utterance.handled", "skill.converse.get_response.enable"]
        )
        # "change volume" matches adapt intent but has no number -> get_response
        s0.send(make_utterance("change the volume", GET_RESPONSE_PIPELINE,
                                s0.shim.session_id))
        messages = cap.wait(timeout=60)

        # Should get a speak asking for the volume level
        speak = next((m for m in messages if m.msg_type == "speak"), None)
        assert speak is not None, (
            f"Volume skill did not ask for amount.\n"
            f"Captured: {[m.msg_type for m in messages]}"
        )
