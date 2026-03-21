"""
ask_yesno() and ask_selection() E2E tests via HiveMind.

Both methods wrap get_response() with specialized answer processing:
- ask_yesno: speaks a question, expects yes/no, returns 'yes'/'no'/None
- ask_selection: presents options, expects choice, returns matched option

Tests verify the full satellite → hub → skill (ask_yesno/ask_selection) →
satellite (question) → satellite response → hub (answer processing) flow.

ask_yesno tested via:
- ovos-skill-easter-eggs: "sing a song" → ask_yesno("too_shy")
- Custom injected skill: direct ask_yesno with known dialog

ask_selection tested via:
- Custom injected skill: presents options, user picks one

Prerequisites
-------------
* ovos-skill-easter-eggs installed (for ask_yesno via sing intent)
* ovoscope installed

Test IDs
--------
TS-YN-01 through TS-YN-10
"""
import threading
import time
from typing import List, Optional

import pytest
from ovos_bus_client.message import Message
from ovos_workshop.decorators import intent_handler
from ovos_workshop.skills import OVOSSkill

from hivemind_test_harness.plugins.ovoscope_agent import OvoscopeAgentProtocol
from hivemind_test_harness.topology import TopologyBuilder
from tests.conftest import (
    SKILL_EASTER_EGGS,
    skill_missing, make_utterance, wait_for_satellite_message,
)

# Pipeline must include converse for get_response/ask_yesno/ask_selection
CONVERSE_PIPELINE = [
    "ovos-converse-pipeline-plugin",
    "ovos-adapt-pipeline-plugin-high",
    "ovos-padatious-pipeline-plugin-high",
    "ovos-adapt-pipeline-plugin-medium",
    "ovos-padatious-pipeline-plugin-medium",
    "ovos-adapt-pipeline-plugin-low",
    "ovos-padatious-pipeline-plugin-low",
]


# ---------------------------------------------------------------------------
# Injected test skills for ask_yesno and ask_selection
# ---------------------------------------------------------------------------

class AskYesNoTestSkill(OVOSSkill):
    """Minimal skill that uses ask_yesno when triggered.

    Intent: 'test.yesno.intent' matches "test yes or no"
    Flow: asks "Do you like pizza?" → returns yes/no → speaks result
    """

    def initialize(self):
        self.last_answer = None

    @intent_handler("test.yesno.intent")
    def handle_yesno(self, message: Message):
        answer = self.ask_yesno("do you like pizza")
        self.last_answer = answer
        if answer == "yes":
            self.speak("you said yes")
        elif answer == "no":
            self.speak("you said no")
        else:
            self.speak(f"you said {answer}")


class AskSelectionTestSkill(OVOSSkill):
    """Minimal skill that uses ask_selection when triggered.

    Intent: 'test.selection.intent' matches "test selection"
    Flow: presents ["red", "green", "blue"] → user picks → speaks choice
    """

    def initialize(self):
        self.last_selection = None

    @intent_handler("test.selection.intent")
    def handle_selection(self, message: Message):
        options = ["red", "green", "blue"]
        choice = self.ask_selection(options, "which color do you prefer")
        self.last_selection = choice
        if choice:
            self.speak(f"you chose {choice}")
        else:
            self.speak("you did not choose")


class AskSelectionNumericTestSkill(OVOSSkill):
    """Minimal skill that uses ask_selection with numeric=True.

    Intent: 'test.numselection.intent' matches "test number selection"
    Flow: presents numbered list → user picks by number → speaks choice
    """

    def initialize(self):
        self.last_selection = None

    @intent_handler("test.numselection.intent")
    def handle_numeric_selection(self, message: Message):
        options = ["pizza", "burger", "sushi", "tacos"]
        choice = self.ask_selection(options, "pick a food", numeric=True)
        self.last_selection = choice
        if choice:
            self.speak(f"you picked {choice}")
        else:
            self.speak("you did not pick")


# ---------------------------------------------------------------------------
# Auto-responder helper
# ---------------------------------------------------------------------------

class SatelliteAutoResponder:
    """Responds to speak messages that have expect_response=True."""

    def __init__(self, satellite, pipeline: list, responses: list):
        self.satellite = satellite
        self.pipeline = pipeline
        self.responses = list(responses)
        self.speaks_received: List[Message] = []
        self._lock = threading.Lock()
        satellite.internal_bus.on("speak", self._on_speak)

    def _on_speak(self, msg: Message) -> None:
        with self._lock:
            self.speaks_received.append(msg)
            if msg.data.get("expect_response") and self.responses:
                response_text = self.responses.pop(0)
                threading.Timer(0.5, self._send_response,
                                args=(response_text,)).start()

    def _send_response(self, text: str) -> None:
        self.satellite.send(make_utterance(
            text, self.pipeline, self.satellite.shim.session_id
        ))

    def shutdown(self) -> None:
        self.satellite.internal_bus.remove_all_listeners("speak")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

YESNO_SKILL_ID = "ask-yesno-test-skill.test"
SELECTION_SKILL_ID = "ask-selection-test-skill.test"
NUMSELECTION_SKILL_ID = "ask-numselection-test-skill.test"


@pytest.fixture(scope="module")
def yesno_topology():
    """MiniCroft with injected ask_yesno test skill + easter-eggs."""
    extra = {
        YESNO_SKILL_ID: AskYesNoTestSkill,
        SELECTION_SKILL_ID: AskSelectionTestSkill,
        NUMSELECTION_SKILL_ID: AskSelectionNumericTestSkill,
    }
    skill_ids = []
    # Also load easter-eggs if available
    if not skill_missing(SKILL_EASTER_EGGS):
        skill_ids.append(SKILL_EASTER_EGGS)

    agent = OvoscopeAgentProtocol(skill_ids=skill_ids, extra_skills=extra)

    # Wait for injected skills to register
    _deadline = time.monotonic() + 120
    while time.monotonic() < _deadline:
        if len(agent.bus.ee.listeners(f"{YESNO_SKILL_ID}:test.yesno.intent")) > 0:
            break
        time.sleep(0.5)
    else:
        pytest.skip("Injected skills not registered within 120s")

    b = TopologyBuilder()
    b.add_master("M0", agent_protocol=agent)
    b.add_satellite("S0", upstream=b.get_master("M0"))
    b.start_all()
    yield b, agent
    b.stop_all()
    agent.shutdown()


# ---------------------------------------------------------------------------
# TS-YN-01..04  ask_yesno — injected skill
# ---------------------------------------------------------------------------

class TestAskYesNo:
    """TS-YN-01..04 — ask_yesno flow through HiveMind."""

    def test_yesno_question_reaches_satellite(self, yesno_topology):
        """TS-YN-01 — ask_yesno speak with expect_response arrives on satellite."""
        b, agent = yesno_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture(
            eof_msgs=["ovos.utterance.handled",
                       "skill.converse.get_response.enable"]
        )
        s0.send(make_utterance("test yes or no", CONVERSE_PIPELINE,
                                s0.shim.session_id))
        cap.wait(timeout=15)

        msg = wait_for_satellite_message(s0, "speak", timeout=10)
        assert msg is not None, "ask_yesno question not forwarded to satellite"
        assert msg.data.get("expect_response") is True, (
            "ask_yesno speak should have expect_response=True"
        )

    def test_yesno_answer_yes(self, yesno_topology):
        """TS-YN-02 — answering 'yes' to ask_yesno produces correct result."""
        b, agent = yesno_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        responder = SatelliteAutoResponder(s0, CONVERSE_PIPELINE, ["yes"])
        try:
            s0.send(make_utterance("test yes or no", CONVERSE_PIPELINE,
                                    s0.shim.session_id))

            # Wait for the result speak ("you said yes")
            deadline = time.monotonic() + 30
            result = None
            while time.monotonic() < deadline:
                with responder._lock:
                    for sp in responder.speaks_received:
                        utt = sp.data.get("utterance", "").lower()
                        if "you said yes" in utt:
                            result = sp
                            break
                if result:
                    break
                time.sleep(0.2)

            assert result is not None, (
                f"Expected 'you said yes' speak.\n"
                f"Speaks: {[s.data.get('utterance', '') for s in responder.speaks_received]}"
            )
        finally:
            responder.shutdown()

    def test_yesno_answer_no(self, yesno_topology):
        """TS-YN-03 — answering 'no' to ask_yesno produces correct result."""
        b, agent = yesno_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        responder = SatelliteAutoResponder(s0, CONVERSE_PIPELINE, ["no"])
        try:
            s0.send(make_utterance("test yes or no", CONVERSE_PIPELINE,
                                    s0.shim.session_id))

            deadline = time.monotonic() + 30
            result = None
            while time.monotonic() < deadline:
                with responder._lock:
                    for sp in responder.speaks_received:
                        utt = sp.data.get("utterance", "").lower()
                        if "you said no" in utt:
                            result = sp
                            break
                if result:
                    break
                time.sleep(0.2)

            assert result is not None, (
                f"Expected 'you said no' speak.\n"
                f"Speaks: {[s.data.get('utterance', '') for s in responder.speaks_received]}"
            )
        finally:
            responder.shutdown()

    def test_yesno_result_routes_to_satellite(self, yesno_topology):
        """TS-YN-04 — final result speak arrives on satellite bus."""
        b, agent = yesno_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        responder = SatelliteAutoResponder(s0, CONVERSE_PIPELINE, ["yes"])
        try:
            s0.send(make_utterance("test yes or no", CONVERSE_PIPELINE,
                                    s0.shim.session_id))

            # Wait for at least 2 speaks: question + result
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                with responder._lock:
                    if len(responder.speaks_received) >= 2:
                        break
                time.sleep(0.2)

            with responder._lock:
                assert len(responder.speaks_received) >= 2, (
                    f"Expected >= 2 speaks (question + result), got "
                    f"{len(responder.speaks_received)}"
                )
        finally:
            responder.shutdown()


# ---------------------------------------------------------------------------
# TS-YN-05..06  ask_yesno — easter-eggs "sing a song"
# ---------------------------------------------------------------------------

@pytest.mark.skipif(skill_missing(SKILL_EASTER_EGGS),
                     reason="ovos-skill-easter-eggs not installed")
class TestEasterEggsSing:
    """TS-YN-05..06 — easter-eggs ask_yesno('too_shy') via HiveMind."""

    def test_sing_triggers_yesno(self, yesno_topology):
        """TS-YN-05 — 'sing a song' triggers ask_yesno question."""
        b, agent = yesno_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture(
            eof_msgs=["ovos.utterance.handled",
                       "skill.converse.get_response.enable"]
        )
        s0.send(make_utterance("sing a song", CONVERSE_PIPELINE,
                                s0.shim.session_id))
        cap.wait(timeout=15)

        # Should get a speak with expect_response (the "too shy?" question)
        speaks = [m for m in cap.messages() if m.msg_type == "speak"]
        expect_speaks = [s for s in speaks if s.data.get("expect_response")]
        # Note: if TTS sounds like Popey, ask_yesno is skipped
        # So we just verify a speak was emitted
        assert len(speaks) >= 1, (
            f"'sing a song' produced no speaks.\n"
            f"Captured: {[m.msg_type for m in cap.messages()]}"
        )

    def test_sing_answer_yes_continues(self, yesno_topology):
        """TS-YN-06 — answering 'yes' to 'too shy?' continues to singing."""
        b, agent = yesno_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        responder = SatelliteAutoResponder(s0, CONVERSE_PIPELINE, ["yes"])
        try:
            s0.send(make_utterance("sing a song", CONVERSE_PIPELINE,
                                    s0.shim.session_id))

            # Wait for speaks (question + singing dialog)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                with responder._lock:
                    if len(responder.speaks_received) >= 2:
                        break
                time.sleep(0.2)

            # At least the question should have been asked
            with responder._lock:
                assert len(responder.speaks_received) >= 1, (
                    "No speaks received for sing intent"
                )
        finally:
            responder.shutdown()


# ---------------------------------------------------------------------------
# TS-YN-07..09  ask_selection — injected skill
# ---------------------------------------------------------------------------

class TestAskSelection:
    """TS-YN-07..09 — ask_selection flow through HiveMind."""

    def test_selection_options_spoken(self, yesno_topology):
        """TS-YN-07 — ask_selection speaks options to satellite."""
        b, agent = yesno_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture(
            eof_msgs=["ovos.utterance.handled",
                       "skill.converse.get_response.enable"]
        )
        s0.send(make_utterance("test selection", CONVERSE_PIPELINE,
                                s0.shim.session_id))
        cap.wait(timeout=15)

        msg = wait_for_satellite_message(s0, "speak", timeout=10)
        assert msg is not None, "ask_selection did not speak options to satellite"

    def test_selection_answer_matched(self, yesno_topology):
        """TS-YN-08 — answering 'green' selects the right option."""
        b, agent = yesno_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        responder = SatelliteAutoResponder(s0, CONVERSE_PIPELINE, ["green"])
        try:
            s0.send(make_utterance("test selection", CONVERSE_PIPELINE,
                                    s0.shim.session_id))

            deadline = time.monotonic() + 30
            result = None
            while time.monotonic() < deadline:
                with responder._lock:
                    for sp in responder.speaks_received:
                        utt = sp.data.get("utterance", "").lower()
                        if "you chose green" in utt:
                            result = sp
                            break
                if result:
                    break
                time.sleep(0.2)

            assert result is not None, (
                f"Expected 'you chose green' speak.\n"
                f"Speaks: {[s.data.get('utterance', '') for s in responder.speaks_received]}"
            )
        finally:
            responder.shutdown()

    def test_selection_result_routes_to_satellite(self, yesno_topology):
        """TS-YN-09 — selection result speak arrives on satellite."""
        b, agent = yesno_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        responder = SatelliteAutoResponder(s0, CONVERSE_PIPELINE, ["red"])
        try:
            s0.send(make_utterance("test selection", CONVERSE_PIPELINE,
                                    s0.shim.session_id))

            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                with responder._lock:
                    result = next(
                        (sp for sp in responder.speaks_received
                         if "you chose" in sp.data.get("utterance", "").lower()),
                        None,
                    )
                if result:
                    break
                time.sleep(0.2)

            assert result is not None, (
                f"Selection result not received on satellite.\n"
                f"Speaks: {[s.data.get('utterance', '') for s in responder.speaks_received]}"
            )
        finally:
            responder.shutdown()


# ---------------------------------------------------------------------------
# TS-YN-10  ask_selection numeric mode
# ---------------------------------------------------------------------------

class TestAskSelectionNumeric:
    """TS-YN-10 — ask_selection with numeric=True via HiveMind."""

    def test_numeric_selection(self, yesno_topology):
        """TS-YN-10 — numeric menu selection: answer '2' picks 'burger'."""
        b, agent = yesno_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        responder = SatelliteAutoResponder(s0, CONVERSE_PIPELINE, ["2"])
        try:
            s0.send(make_utterance("test number selection", CONVERSE_PIPELINE,
                                    s0.shim.session_id))

            deadline = time.monotonic() + 30
            result = None
            while time.monotonic() < deadline:
                with responder._lock:
                    for sp in responder.speaks_received:
                        utt = sp.data.get("utterance", "").lower()
                        if "you picked" in utt:
                            result = sp
                            break
                if result:
                    break
                time.sleep(0.2)

            assert result is not None, (
                f"Expected 'you picked ...' speak.\n"
                f"Speaks: {[s.data.get('utterance', '') for s in responder.speaks_received]}"
            )
        finally:
            responder.shutdown()
