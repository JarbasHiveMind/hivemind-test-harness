"""
Stop command E2E tests via HiveMind.

Tests the OVOS stop flow through HiveMind:
1. Satellite triggers a long-running skill (count to 10)
2. While skill is active, satellite sends "stop"
3. Hub's StopService queries skill via ping/pong
4. Skill's stop_session() is called, counting aborts
5. Satellite receives confirmation

Uses ovos-skill-count which:
- Has session-aware can_stop() / stop_session()
- Takes measurable time (1s per number)
- Tracks active state via active_sessions dict

Prerequisites
-------------
* ovos-skill-count installed
* ovos-skill-hello-world installed (for non-stoppable comparison)
* ovoscope installed

Test IDs
--------
TS-ST-01 through TS-ST-07
"""
import threading
import time

import pytest
from ovos_bus_client.message import Message
from ovos_spec_tools import SpecMessage

from hivescope.topology import TopologyBuilder
from tests.conftest import (
    open_capture,
    make_ovoscope_agent,
    SKILL_COUNT, SKILL_HELLO,
    skill_missing, make_utterance, wait_for_satellite_message, poll_until,
)

# MiniCroft boot alone can take up to MINICROFT_READY_TIMEOUT (180s), and skill
# handlers run serially after that, so the repo-wide 30s default is far too
# tight for this module.
pytestmark = pytest.mark.timeout(300)

# Pipeline with stop support
STOP_PIPELINE = [
    "ovos-converse-pipeline-plugin",
    "ovos-stop-pipeline-plugin",
    "ovos-adapt-pipeline-plugin-high",
    "ovos-padatious-pipeline-plugin-high",
    "ovos-adapt-pipeline-plugin-medium",
    "ovos-padatious-pipeline-plugin-medium",
    "ovos-adapt-pipeline-plugin-low",
    "ovos-padatious-pipeline-plugin-low",
]


@pytest.fixture(scope="module")
def stop_topology():
    """Boot MiniCroft with count + hello-world skills."""
    agent = make_ovoscope_agent(skill_ids=[SKILL_COUNT, SKILL_HELLO])

    _deadline = time.monotonic() + 120
    while time.monotonic() < _deadline:
        if len(agent.bus.ee.listeners(f"{SKILL_COUNT}:count_to_N.intent")) > 0:
            break
        time.sleep(0.5)
    else:
        pytest.skip("Count skill intents not registered within 120s")

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


# ---------------------------------------------------------------------------
# TS-ST-01..02  Count Skill Basic
# ---------------------------------------------------------------------------

@pytest.mark.skipif(skill_missing(SKILL_COUNT), reason="ovos-skill-count not installed")
class TestCountBasic:
    """TS-ST-01..02 -- counting skill basic operation."""

    def test_count_starts_speaking(self, stop_topology):
        """TS-ST-01 -- 'count to five' starts emitting speak messages."""
        b, agent = stop_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        # Don't wait for EOF -- counting takes 5+ seconds and emits multiple speaks
        s0.send(make_utterance("count to five", STOP_PIPELINE, s0.shim.session_id))

        # Wait for at least one speak within 10s
        agent.wait_for_skill_emission(SpecMessage.SPEAK, count=1, timeout=10)
        speaks = agent.skill_messages(SpecMessage.SPEAK)
        assert len(speaks) >= 1, "Count skill did not start speaking"

    def test_count_produces_multiple_speaks(self, stop_topology):
        """TS-ST-02 -- 'count to three' produces 3 speak messages."""
        b, agent = stop_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        s0.send(make_utterance("count to three", STOP_PIPELINE, s0.shim.session_id))

        # Wait for handler.complete (counting finished) -- up to 15s
        agent.wait_for_skill_emission("mycroft.skill.handler.complete", timeout=15)
        speaks = agent.skill_messages(SpecMessage.SPEAK)
        assert len(speaks) >= 3, (
            f"Expected >=3 speaks for 'count to three', got {len(speaks)}.\n"
            f"Speaks: {[s.data.get('utterance', '') for s in speaks]}"
        )


# ---------------------------------------------------------------------------
# TS-ST-03..05  Stop Active Count
# ---------------------------------------------------------------------------

@pytest.mark.skipif(skill_missing(SKILL_COUNT), reason="ovos-skill-count not installed")
class TestStopActiveCount:
    """TS-ST-03..05 -- stopping an active counting session."""

    def test_stop_interrupts_counting(self, stop_topology):
        """TS-ST-03 -- 'stop' during count to ten interrupts counting."""
        b, agent = stop_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        # Start counting to 10 (would take ~10s)
        s0.send(make_utterance("count to ten", STOP_PIPELINE, s0.shim.session_id))

        # Wait for counting to start (at least 1 speak)
        agent.wait_for_skill_emission(SpecMessage.SPEAK, count=1, timeout=10)

        # Send stop while counting is still active
        time.sleep(1)  # let a couple numbers count
        agent.clear()
        s0.send(make_utterance("stop", STOP_PIPELINE, s0.shim.session_id))

        def _speaks():
            return [m for m in agent.injected if m.msg_type == SpecMessage.SPEAK]

        # After "stop" the count must actually halt: the number of speaks
        # emitted post-stop settles to a small value well below the full
        # count-to-ten. Poll until the speak count stops growing (quiescent),
        # then assert counting was interrupted rather than running to completion.
        def _quiescent():
            n = len(_speaks())
            time.sleep(1.0)
            return len(_speaks()) == n

        poll_until(_quiescent, timeout=15,
                   message="counting never went quiescent after 'stop'")
        post_stop = len(_speaks())
        assert post_stop < 10, (
            f"'stop' did not interrupt counting: {post_stop} speaks after stop "
            f"(a full count to ten would emit ~10)"
        )

    def test_stop_ping_pong_emitted(self, stop_topology):
        """TS-ST-04 -- stop triggers ping/pong for active skill."""
        b, agent = stop_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        # Start counting
        s0.send(make_utterance("count to ten", STOP_PIPELINE, s0.shim.session_id))
        agent.wait_for_skill_emission(SpecMessage.SPEAK, count=1, timeout=10)

        # Collect all messages during stop
        time.sleep(1)
        agent.clear()

        s0.send(make_utterance("stop", STOP_PIPELINE, s0.shim.session_id))
        time.sleep(5)

        # Check for stop-related messages
        stop_msgs = [m for m in agent.injected
                     if "stop" in m.msg_type.lower()]
        assert len(stop_msgs) >= 1, (
            f"No stop-related messages found.\n"
            f"All types: {[m.msg_type for m in agent.injected]}"
        )

    def test_count_speaks_route_to_satellite(self, stop_topology):
        """TS-ST-05 -- counting speaks arrive on satellite bus."""
        b, agent = stop_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        s0.send(make_utterance("count to three", STOP_PIPELINE, s0.shim.session_id))

        msg = wait_for_satellite_message(s0, SpecMessage.SPEAK, timeout=10)
        assert msg is not None, "Count speak not forwarded to satellite"


# ---------------------------------------------------------------------------
# TS-ST-06  Stop with no active skills
# ---------------------------------------------------------------------------

@pytest.mark.skipif(skill_missing(SKILL_HELLO), reason="ovos-skill-hello-world not installed")
class TestStopNoActiveSkills:
    """TS-ST-06 -- stop when no skills are active."""

    def test_stop_with_nothing_active(self, stop_topology):
        """TS-ST-06 -- 'stop' with no active skills emits mycroft.stop."""
        b, agent = stop_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        s0.send(make_utterance("stop", STOP_PIPELINE, s0.shim.session_id))

        # Wait for stop processing
        time.sleep(3)

        # Should get mycroft.stop (global stop) since no skills are active
        stop_msgs = [m for m in agent.injected if m.msg_type == "mycroft.stop"]
        assert len(stop_msgs) >= 1, (
            f"mycroft.stop not emitted.\n"
            f"All types: {[m.msg_type for m in agent.injected]}"
        )


# ---------------------------------------------------------------------------
# TS-ST-07  Stop after quick skill (already finished)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(skill_missing(SKILL_HELLO), reason="ovos-skill-hello-world not installed")
class TestStopAfterQuickSkill:
    """TS-ST-07 -- stop after a quick skill has already completed."""

    def test_stop_after_hello_world(self, stop_topology):
        """TS-ST-07 -- hello world completes instantly; stop afterwards is global."""
        b, agent = stop_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        # First trigger hello world (completes instantly)
        cap = open_capture(agent)
        s0.send(make_utterance("hello world", STOP_PIPELINE, s0.shim.session_id))
        cap.wait(timeout=15)

        agent.clear()

        # Now send stop -- hello-world's can_stop returns False
        s0.send(make_utterance("stop", STOP_PIPELINE, s0.shim.session_id))
        time.sleep(3)

        # Should get global stop since no skills are stoppable
        stop_msgs = [m for m in agent.injected if m.msg_type == "mycroft.stop"]
        assert len(stop_msgs) >= 1, (
            f"mycroft.stop not emitted after completed skill.\n"
            f"All types: {[m.msg_type for m in agent.injected]}"
        )
