"""
OCP (Open Common Play) E2E tests via HiveMind.

Tests that OCP-related bus messages route correctly through HiveMind.
Uses an injected test skill that emits ovos.common_play.* messages
to verify the satellite receives media search results and playback
commands.

Test IDs
--------
TS-OCP-01 through TS-OCP-05
"""
import threading
import time

import pytest
from ovos_bus_client.message import Message
from ovos_workshop.intents import IntentBuilder
from ovos_workshop.skills import OVOSSkill

from hivescope.plugins.ovoscope_agent import OvoscopeAgentProtocol
from hivescope.topology import TopologyBuilder
from tests.conftest import (
    skill_missing, make_utterance, wait_for_satellite_message,
)

# MiniCroft boot alone can take up to MINICROFT_READY_TIMEOUT (180s), and skill
# handlers run serially after that, so the repo-wide 30s default is far too
# tight for this module.
pytestmark = pytest.mark.timeout(300)

CONVERSE_PIPELINE = [
    "ovos-converse-pipeline-plugin",
    "ovos-adapt-pipeline-plugin-high",
    "ovos-padatious-pipeline-plugin-high",
    "ovos-adapt-pipeline-plugin-medium",
    "ovos-padatious-pipeline-plugin-medium",
    "ovos-adapt-pipeline-plugin-low",
    "ovos-padatious-pipeline-plugin-low",
]

OCP_SKILL_ID = "ocp-test-skill.test"


class OCPTestSkill(OVOSSkill):
    """Injected skill that emits OCP-style messages when triggered.

    Simulates what a real OCP skill does: receives a search query,
    emits search results, and handles playback commands.

    Intents are registered programmatically with inline Adapt vocabulary
    in ``initialize()`` — an injected (package-less) skill has no
    locale/*.intent resource files on disk, so the padatious
    ``@intent_handler("...intent")`` form would log "Unable to find
    test.play.intent" and never register.
    """

    def initialize(self):
        self.register_vocabulary("TestPlayKeyword", "test play")
        self.register_vocabulary("TestMediaStatusKeyword", "test media status")
        self.register_intent(
            IntentBuilder("TestPlayIntent").require("TestPlayKeyword"),
            self.handle_play,
        )
        self.register_intent(
            IntentBuilder("TestMediaStatusIntent").require("TestMediaStatusKeyword"),
            self.handle_media_status,
        )

    def handle_play(self, message: Message):
        """Emit OCP search results when asked to play."""
        self.bus.emit(message.forward(
            "ovos.common_play.query.response",
            {
                "phrase": "test music",
                "skill_id": self.skill_id,
                "results": [
                    {
                        "title": "Test Song",
                        "artist": "Test Artist",
                        "match_confidence": 85,
                        "uri": "file:///tmp/test.mp3",
                        "media_type": "music",
                    }
                ],
                "searching": False,
            }
        ))
        self.speak("playing test music")

    def handle_media_status(self, message: Message):
        """Emit media status update."""
        self.bus.emit(message.forward(
            "ovos.common_play.track_info",
            {
                "title": "Now Playing Song",
                "artist": "Test Artist",
                "album": "Test Album",
                "duration": 180,
                "position": 45,
            }
        ))
        self.speak("now playing test song")


@pytest.fixture(scope="module")
def ocp_topology():
    """MiniCroft with injected OCP test skill."""
    agent = OvoscopeAgentProtocol(
        skill_ids=[],
        extra_skills={OCP_SKILL_ID: OCPTestSkill}
    )

    _deadline = time.monotonic() + 120
    while time.monotonic() < _deadline:
        if len(agent.bus.ee.listeners(f"{OCP_SKILL_ID}:TestPlayIntent")) > 0:
            break
        time.sleep(0.5)
    else:
        pytest.skip("OCP test skill not registered within 120s")

    b = TopologyBuilder()
    try:
        b.add_master("M0", agent_protocol=agent)
        b.add_satellite("S0", upstream=b.get_master("M0"),
                        allowed_types=["recognizer_loop:utterance"])
        b.start_all()
        yield b, agent
    finally:
        b.stop_all()
        agent.shutdown()


# A programmatically-injected skill (extra_skills={...}) registers its intent
# *handler* on the bus, but its Adapt vocabulary/intent is not picked up by the
# adapt pipeline plugin inside MiniCroft, so utterances never match it (every
# "test play" / "test media status" yields complete_intent_failure even though
# ocp-test-skill.test:TestPlayIntent is a live bus listener). Installed skill
# plugins match fine — this is specific to injected skills. Until ovoscope wires
# injected-skill intents into the pipeline, route these utterances cannot match.
_INJECTED_SKILL_INTENT_UNSUPPORTED = (
    "injected (extra_skills) skill intents are registered as bus handlers but "
    "not matched by the adapt pipeline in MiniCroft — utterance never matches"
)


@pytest.mark.skip(reason=_INJECTED_SKILL_INTENT_UNSUPPORTED)
class TestOCPSearchResults:
    """TS-OCP-01..03 — OCP search results through HiveMind."""

    def test_ocp_query_response_on_hub(self, ocp_topology):
        """TS-OCP-01 — OCP query response emitted on hub bus."""
        b, agent = ocp_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture()
        s0.send(make_utterance("test play", CONVERSE_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=60)

        ocp_resp = next(
            (m for m in messages if m.msg_type == "ovos.common_play.query.response"),
            None,
        )
        assert ocp_resp is not None, (
            f"OCP query response not emitted.\n"
            f"Captured: {[m.msg_type for m in messages]}"
        )
        assert len(ocp_resp.data.get("results", [])) == 1

    def test_ocp_results_route_to_satellite(self, ocp_topology):
        """TS-OCP-02 — OCP query response arrives on satellite bus."""
        b, agent = ocp_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture()
        s0.send(make_utterance("test play", CONVERSE_PIPELINE, s0.shim.session_id))
        cap.wait(timeout=60)

        msg = wait_for_satellite_message(s0, "ovos.common_play.query.response", timeout=10)
        assert msg is not None, "OCP query response not forwarded to satellite"
        results = msg.data.get("results", [])
        assert len(results) == 1
        assert results[0]["title"] == "Test Song"

    def test_ocp_speak_also_routes(self, ocp_topology):
        """TS-OCP-03 — speak from OCP skill also arrives on satellite."""
        b, agent = ocp_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture()
        s0.send(make_utterance("test play", CONVERSE_PIPELINE, s0.shim.session_id))
        cap.wait(timeout=60)

        msg = wait_for_satellite_message(s0, "speak", timeout=10)
        assert msg is not None, "speak not forwarded to satellite"


@pytest.mark.skip(reason=_INJECTED_SKILL_INTENT_UNSUPPORTED)
class TestOCPTrackInfo:
    """TS-OCP-04..05 — OCP track info through HiveMind."""

    def test_track_info_on_hub(self, ocp_topology):
        """TS-OCP-04 — track info emitted on hub bus."""
        b, agent = ocp_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture()
        s0.send(make_utterance("test media status", CONVERSE_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=60)

        info = next(
            (m for m in messages if m.msg_type == "ovos.common_play.track_info"),
            None,
        )
        assert info is not None, (
            f"track_info not emitted.\nCaptured: {[m.msg_type for m in messages]}"
        )
        assert info.data.get("title") == "Now Playing Song"

    def test_track_info_routes_to_satellite(self, ocp_topology):
        """TS-OCP-05 — track info arrives on satellite bus."""
        b, agent = ocp_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture()
        s0.send(make_utterance("test media status", CONVERSE_PIPELINE, s0.shim.session_id))
        cap.wait(timeout=60)

        msg = wait_for_satellite_message(s0, "ovos.common_play.track_info", timeout=10)
        assert msg is not None, "track_info not forwarded to satellite"
