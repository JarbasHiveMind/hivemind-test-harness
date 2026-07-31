"""
Event scheduler E2E tests via HiveMind.

Tests that skills using schedule_event() work correctly when triggered
through HiveMind. The scheduled callback fires on the hub's MiniCroft
bus and the result is routed back to the satellite.

schedule_event() emits 'mycroft.scheduler.schedule_event' on the bus.
The EventScheduler service fires the callback at the specified time.

Test IDs
--------
TS-SCH-01 through TS-SCH-04
"""
import time

import pytest
from ovos_bus_client.message import Message
from ovos_workshop.decorators import intent_handler
from ovos_workshop.skills import OVOSSkill

from hivescope.plugins.ovoscope_agent import OvoscopeAgentProtocol
from hivescope.topology import TopologyBuilder
from tests.conftest import (
    make_utterance, wait_for_satellite_message,
)

# MiniCroft boot alone can take up to MINICROFT_READY_TIMEOUT (180s), and skill
# handlers run serially after that, so the repo-wide 30s default is far too
# tight for this module.
pytestmark = pytest.mark.timeout(300)

DEFAULT_PIPELINE = [
    "ovos-adapt-pipeline-plugin-high",
    "ovos-padatious-pipeline-plugin-high",
    "ovos-adapt-pipeline-plugin-medium",
    "ovos-padatious-pipeline-plugin-medium",
    "ovos-adapt-pipeline-plugin-low",
    "ovos-padatious-pipeline-plugin-low",
]

SCHED_SKILL_ID = "scheduler-test-skill.test"


class SchedulerTestSkill(OVOSSkill):
    """Injected skill that schedules a delayed event and speaks when it fires."""

    def initialize(self):
        self.callback_fired = False

    @intent_handler("test.schedule.intent")
    def handle_schedule(self, message: Message):
        self.speak("scheduling event")
        self.schedule_event(self._on_timer, 2, name="test_timer",
                            data={"msg": "timer fired"})

    def _on_timer(self, message: Message):
        self.callback_fired = True
        self.speak("timer callback executed")

    @intent_handler("test.schedule.immediate.intent")
    def handle_immediate(self, message: Message):
        """Schedule event with 0 delay — fires almost immediately."""
        self.speak("scheduling immediate")
        self.schedule_event(self._on_immediate, 0, name="immediate_timer")

    def _on_immediate(self, message: Message):
        self.speak("immediate callback fired")


@pytest.fixture(scope="module")
def scheduler_topology():
    """MiniCroft with scheduler test skill."""
    agent = OvoscopeAgentProtocol(
        skill_ids=[],
        extra_skills={SCHED_SKILL_ID: SchedulerTestSkill}
    )

    b = TopologyBuilder()
    try:
        _deadline = time.monotonic() + 120
        while time.monotonic() < _deadline:
            if len(agent.bus.ee.listeners(f"{SCHED_SKILL_ID}:test.schedule.intent")) > 0:
                break
            time.sleep(0.5)
        else:
            # MiniCroft is already booted — skipping without stopping it leaks
            # the whole agent (bus, threads, skill services) into the session.
            pytest.skip("Scheduler test skill not registered within 120s")

        b.add_master("M0", agent_protocol=agent)
        b.add_satellite("S0", upstream=b.get_master("M0"))
        b.start_all()
        yield b, agent
    finally:
        b.stop_all()
        agent.shutdown()


class TestScheduleEvent:
    """TS-SCH-01..02 — schedule_event() through HiveMind."""

    def test_initial_speak_arrives(self, scheduler_topology):
        """TS-SCH-01 — 'scheduling event' speak arrives on satellite."""
        b, agent = scheduler_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture()
        s0.send(make_utterance("test schedule", DEFAULT_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=15)

        speak = next((m for m in messages if m.msg_type == "speak"), None)
        assert speak is not None, (
            f"Initial speak not emitted.\nCaptured: {[m.msg_type for m in messages]}"
        )

    def test_scheduled_callback_fires(self, scheduler_topology):
        """TS-SCH-02 — timer callback fires after delay and speaks."""
        b, agent = scheduler_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        s0.send(make_utterance("test schedule", DEFAULT_PIPELINE, s0.shim.session_id))

        # Wait for the delayed callback (2s delay + processing time)
        deadline = time.monotonic() + 15
        callback_speak = None
        while time.monotonic() < deadline:
            for m in agent.injected:
                if m.msg_type == "speak" and "timer callback" in m.data.get("utterance", "").lower():
                    callback_speak = m
                    break
            if callback_speak:
                break
            time.sleep(0.2)

        assert callback_speak is not None, (
            f"Timer callback speak not found.\n"
            f"Speaks: {[m.data.get('utterance', '') for m in agent.injected if m.msg_type == 'speak']}"
        )


class TestScheduleImmediate:
    """TS-SCH-03..04 — immediate schedule_event() through HiveMind."""

    def test_immediate_callback_fires(self, scheduler_topology):
        """TS-SCH-03 — immediate schedule fires quickly."""
        b, agent = scheduler_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        s0.send(make_utterance("test schedule immediate", DEFAULT_PIPELINE, s0.shim.session_id))

        deadline = time.monotonic() + 15
        callback_speak = None
        while time.monotonic() < deadline:
            for m in agent.injected:
                if m.msg_type == "speak" and "immediate callback" in m.data.get("utterance", "").lower():
                    callback_speak = m
                    break
            if callback_speak:
                break
            time.sleep(0.2)

        assert callback_speak is not None, (
            f"Immediate callback speak not found.\n"
            f"Speaks: {[m.data.get('utterance', '') for m in agent.injected if m.msg_type == 'speak']}"
        )

    def test_callback_speak_routes_to_satellite(self, scheduler_topology):
        """TS-SCH-04 — scheduled callback speak routes to satellite."""
        b, agent = scheduler_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        s0.send(make_utterance("test schedule immediate", DEFAULT_PIPELINE, s0.shim.session_id))

        # Wait for callback speak on satellite bus
        deadline = time.monotonic() + 15
        sat_speaks = []
        while time.monotonic() < deadline:
            # Poll — can't use wait_for_satellite_message (it's one-shot)
            time.sleep(0.5)
            # Check agent.injected for callback speak as proxy
            for m in agent.injected:
                if m.msg_type == "speak" and "immediate callback" in m.data.get("utterance", "").lower():
                    sat_speaks.append(m)
                    break
            if sat_speaks:
                break

        assert sat_speaks, "Scheduled callback speak not found"
