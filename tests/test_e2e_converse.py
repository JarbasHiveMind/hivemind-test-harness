"""
Multi-turn converse E2E tests via HiveMind.

Uses ovos-skill-parrot which has converse capability — once parrot mode is
active, subsequent utterances are echoed back without intent matching.

Tests verify that converse state persists correctly through HiveMind's
session management across multiple satellite → hub round-trips.

Prerequisites
-------------
* ovos-skill-parrot installed
* ovoscope installed

Test IDs
--------
TS-CV-01 through TS-CV-05
"""
import time

import pytest
from ovos_bus_client.message import Message

from hivescope.plugins.ovoscope_agent import OvoscopeAgentProtocol
from hivescope.topology import TopologyBuilder
from tests.conftest import (
    SKILL_PARROT,
    skill_missing, make_utterance, wait_for_satellite_message,
)

# MiniCroft boot alone can take up to MINICROFT_READY_TIMEOUT (180s), and skill
# handlers run serially after that, so the repo-wide 30s default is far too
# tight for this module.
pytestmark = pytest.mark.timeout(300)

DEFAULT_PIPELINE = [
    "ovos-converse-pipeline-plugin",
    "ovos-adapt-pipeline-plugin-high",
    "ovos-padatious-pipeline-plugin-high",
    "ovos-adapt-pipeline-plugin-medium",
    "ovos-padatious-pipeline-plugin-medium",
    "ovos-adapt-pipeline-plugin-low",
    "ovos-padatious-pipeline-plugin-low",
]


@pytest.fixture(scope="module")
def converse_topology():
    """Boot MiniCroft with parrot skill, connect one satellite."""
    agent = OvoscopeAgentProtocol(skill_ids=[SKILL_PARROT])

    _deadline = time.monotonic() + 120
    while time.monotonic() < _deadline:
        # Wait for parrot intents
        if len(agent.bus.ee.listeners(f"{SKILL_PARROT}:speak.intent")) > 0:
            break
        time.sleep(0.5)
    else:
        pytest.skip("Parrot skill intents not registered within 120s")

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


@pytest.mark.skipif(skill_missing(SKILL_PARROT), reason="ovos-skill-parrot not installed")
class TestParrotRepeat:
    """TS-CV-01..02 — basic parrot repeat functionality."""

    def test_say_something(self, converse_topology):
        """TS-CV-01 — 'say hello world' triggers speak with 'hello world'."""
        b, agent = converse_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture()
        s0.send(make_utterance("say hello world", DEFAULT_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=60)

        speak = next((m for m in messages if m.msg_type == "speak"), None)
        assert speak is not None, (
            f"'speak' not emitted.\nCaptured: {[m.msg_type for m in messages]}"
        )
        assert "hello world" in speak.data.get("utterance", "").lower(), (
            f"Expected 'hello world' in speak, got: {speak.data.get('utterance')}"
        )

    def test_repeat_routes_to_satellite(self, converse_topology):
        """TS-CV-02 — repeated speech arrives on satellite bus."""
        b, agent = converse_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture()
        s0.send(make_utterance("say testing one two three", DEFAULT_PIPELINE, s0.shim.session_id))
        cap.wait(timeout=60)

        msg = wait_for_satellite_message(s0, "speak", timeout=10)
        assert msg is not None, "speak not forwarded to satellite"


@pytest.mark.skipif(skill_missing(SKILL_PARROT), reason="ovos-skill-parrot not installed")
class TestParrotMode:
    """TS-CV-03..05 — parrot mode converse through HiveMind."""

    def test_start_parrot_mode(self, converse_topology):
        """TS-CV-03 — 'start parrot mode' activates parrot."""
        b, agent = converse_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture()
        s0.send(make_utterance("start parrot mode", DEFAULT_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=60)

        speak = next((m for m in messages if m.msg_type == "speak"), None)
        assert speak is not None, (
            f"Parrot mode start did not produce speak.\n"
            f"Captured: {[m.msg_type for m in messages]}"
        )

    def test_parrot_echoes_in_converse(self, converse_topology):
        """TS-CV-04 — after parrot mode, next utterance is echoed via converse."""
        b, agent = converse_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        # Start parrot mode
        cap1 = agent.new_capture()
        s0.send(make_utterance("start parrot mode", DEFAULT_PIPELINE, s0.shim.session_id))
        cap1.wait(timeout=60)
        agent.clear()

        # Now send arbitrary text — should be echoed via converse
        cap2 = agent.new_capture()
        s0.send(make_utterance("the quick brown fox", DEFAULT_PIPELINE, s0.shim.session_id))
        messages = cap2.wait(timeout=60)

        speak = next((m for m in messages if m.msg_type == "speak"), None)
        assert speak is not None, (
            f"Parrot mode did not echo via converse.\n"
            f"Captured: {[m.msg_type for m in messages]}"
        )

    def test_stop_parrot_mode(self, converse_topology):
        """TS-CV-05 — 'stop parrot' deactivates parrot mode."""
        b, agent = converse_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        # Start then stop
        cap1 = agent.new_capture()
        s0.send(make_utterance("start parrot mode", DEFAULT_PIPELINE, s0.shim.session_id))
        cap1.wait(timeout=60)
        agent.clear()

        cap2 = agent.new_capture()
        s0.send(make_utterance("stop parrot", DEFAULT_PIPELINE, s0.shim.session_id))
        messages = cap2.wait(timeout=60)

        speak = next((m for m in messages if m.msg_type == "speak"), None)
        assert speak is not None, (
            f"Stop parrot did not produce speak.\n"
            f"Captured: {[m.msg_type for m in messages]}"
        )
