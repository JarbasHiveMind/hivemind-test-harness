"""
Shared bus mode E2E tests via HiveMind.

Tests that satellites with shared_bus=True passively forward all
internal bus messages upstream to the master as SHARED_BUS HiveMessages.

When a skill response (speak) is routed to a shared_bus satellite,
the satellite's internal bus activity is mirrored back upstream,
giving the master visibility into satellite-side events.

Test IDs
--------
TS-SB-01 through TS-SB-04
"""
import threading
import time

import pytest
from ovos_bus_client.message import Message
from hivemind_bus_client.message import HiveMessage, HiveMessageType

from hivescope.plugins.ovoscope_agent import OvoscopeAgentProtocol
from hivescope.topology import TopologyBuilder
from tests.conftest import (
    SKILL_HELLO,
    skill_missing, make_utterance, wait_for_satellite_message,
)

ADAPT_PIPELINE = ["ovos-adapt-pipeline-plugin-high"]


@pytest.fixture(scope="module")
def shared_bus_topology():
    """M0 with hello-world + S0(shared_bus=True) + S1(normal)."""
    agent = OvoscopeAgentProtocol(skill_ids=[SKILL_HELLO])

    _deadline = time.monotonic() + 120
    while time.monotonic() < _deadline:
        if len(agent.bus.ee.listeners(f"{SKILL_HELLO}:HelloWorldIntent")) > 0:
            break
        time.sleep(0.5)
    else:
        pytest.skip("HelloWorldIntent not registered within 120s")

    b = TopologyBuilder()
    b.add_master("M0", agent_protocol=agent)
    b.add_satellite("S0", upstream=b.get_master("M0"), shared_bus=True)
    b.add_satellite("S1", upstream=b.get_master("M0"))
    b.start_all()
    yield b, agent
    b.stop_all()
    agent.shutdown()


@pytest.mark.skipif(skill_missing(SKILL_HELLO), reason="ovos-skill-hello-world not installed")
class TestSharedBusBasic:
    """TS-SB-01..02 — shared bus satellite mirrors bus activity."""

    def test_shared_bus_mirrors_events(self, shared_bus_topology):
        """TS-SB-01 — satellite internal bus events appear as SHARED_BUS on master."""
        b, agent = shared_bus_topology
        agent.clear()
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        # Record SHARED_BUS messages on master
        shared_received = []
        m0.recorder.clear()

        # Emit a custom event on S0's internal bus
        s0.internal_bus.emit(Message("test.shared.event", {"data": "hello"}))

        time.sleep(2)
        # Check master received SHARED_BUS
        shared_msgs = m0.recorder.received(HiveMessageType.SHARED_BUS)
        assert len(shared_msgs) >= 1, (
            f"SHARED_BUS not received on master.\n"
            f"Recorder: {[(r.msg_type, r.direction) for r in m0.recorder._records]}"
        )

    def test_non_shared_bus_satellite_does_not_mirror(self, shared_bus_topology):
        """TS-SB-02 — normal satellite does NOT mirror internal events."""
        b, agent = shared_bus_topology
        agent.clear()
        s1 = b.get_satellite("S1")
        m0 = b.get_master("M0")
        m0.recorder.clear()

        # Emit on S1's internal bus (non-shared)
        s1.internal_bus.emit(Message("test.normal.event", {"data": "nope"}))

        time.sleep(2)
        shared_msgs = m0.recorder.received(HiveMessageType.SHARED_BUS)
        # Filter to only messages from S1
        # Shared bus messages should NOT come from S1
        assert len(shared_msgs) == 0, (
            "Non-shared satellite should not mirror events"
        )


@pytest.mark.skipif(skill_missing(SKILL_HELLO), reason="ovos-skill-hello-world not installed")
class TestSharedBusWithSkills:
    """TS-SB-03..04 — shared bus with real skill responses."""

    def test_skill_response_mirrored(self, shared_bus_topology):
        """TS-SB-03 — skill speak arriving on shared satellite is mirrored back."""
        b, agent = shared_bus_topology
        agent.clear()
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")
        m0.recorder.clear()

        cap = agent.new_capture()
        s0.send(make_utterance("hello world", ADAPT_PIPELINE, s0.shim.session_id))
        cap.wait(timeout=15)

        # Wait for speak to arrive on satellite then mirror back
        time.sleep(3)

        # Master should see SHARED_BUS from speak being re-emitted on satellite bus
        shared_msgs = m0.recorder.received(HiveMessageType.SHARED_BUS)
        # The speak message arriving on satellite's bus triggers SHARED_BUS upstream
        # Note: this depends on the speak being injected on internal_bus first
        assert len(shared_msgs) >= 1, (
            f"Skill response not mirrored via SHARED_BUS.\n"
            f"All master records: {[(r.msg_type, r.direction) for r in m0.recorder._records]}"
        )

    def test_shared_bus_does_not_affect_skill_execution(self, shared_bus_topology):
        """TS-SB-04 — shared_bus does not interfere with normal skill flow."""
        b, agent = shared_bus_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture()
        s0.send(make_utterance("hello world", ADAPT_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=15)

        speak = next((m for m in messages if m.msg_type == "speak"), None)
        assert speak is not None, "Skill should still work with shared_bus satellite"
