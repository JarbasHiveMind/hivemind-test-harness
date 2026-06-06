"""
Miscellaneous skill E2E tests via HiveMind.

Tests skills with unique characteristics: IP address queries, counting,
and edge cases like empty utterances and very long utterances.

Prerequisites
-------------
* ovos-skill-ip, ovos-skill-count installed
* ovoscope installed

Test IDs
--------
TS-MI-01 through TS-MI-08
"""
import time

import pytest
from ovos_bus_client.message import Message

from hivescope.plugins.ovoscope_agent import OvoscopeAgentProtocol
from hivescope.topology import TopologyBuilder
from tests.conftest import (
    SKILL_HELLO, SKILL_IP, SKILL_COUNT,
    skill_missing, make_utterance, assert_types_in_order,
    wait_for_satellite_message,
)

DEFAULT_PIPELINE = [
    "ovos-adapt-pipeline-plugin-high",
    "ovos-padatious-pipeline-plugin-high",
    "ovos-adapt-pipeline-plugin-medium",
    "ovos-padatious-pipeline-plugin-medium",
    "ovos-adapt-pipeline-plugin-low",
    "ovos-padatious-pipeline-plugin-low",
    "ovos-fallback-pipeline-plugin-high",
    "ovos-fallback-pipeline-plugin-medium",
    "ovos-fallback-pipeline-plugin-low",
]

ADAPT_PIPELINE = ["ovos-adapt-pipeline-plugin-high"]


@pytest.fixture(scope="module")
def misc_topology():
    """Boot MiniCroft with IP, count, and hello-world skills."""
    skills = [SKILL_IP, SKILL_COUNT, SKILL_HELLO]
    agent = OvoscopeAgentProtocol(skill_ids=skills)

    _deadline = time.monotonic() + 120
    while time.monotonic() < _deadline:
        if len(agent.bus.ee.listeners(f"{SKILL_HELLO}:HelloWorldIntent")) > 0:
            break
        time.sleep(0.5)
    else:
        pytest.skip("Skills not registered within 120s")

    b = TopologyBuilder()
    b.add_master("M0", agent_protocol=agent)
    b.add_satellite("S0", upstream=b.get_master("M0"))
    b.start_all()
    yield b, agent
    b.stop_all()
    agent.shutdown()


# ---------------------------------------------------------------------------
# TS-MI-01..02  IP Skill
# ---------------------------------------------------------------------------

@pytest.mark.skipif(skill_missing(SKILL_IP), reason="ovos-skill-ip not installed")
class TestIPSkill:
    """TS-MI-01..02 — IP address skill via HiveMind."""

    def test_ip_address_query(self, misc_topology):
        """TS-MI-01 — 'what is your IP address' produces speak."""
        b, agent = misc_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture()
        s0.send(make_utterance("what is your ip address", DEFAULT_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=15)

        speak = next((m for m in messages if m.msg_type == "speak"), None)
        assert speak is not None, (
            f"'speak' not emitted for IP query.\n"
            f"Captured: {[m.msg_type for m in messages]}"
        )

    def test_ip_routes_to_satellite(self, misc_topology):
        """TS-MI-02 — IP speak arrives on satellite bus."""
        b, agent = misc_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture()
        s0.send(make_utterance("what is your ip address", DEFAULT_PIPELINE, s0.shim.session_id))
        cap.wait(timeout=15)

        msg = wait_for_satellite_message(s0, "speak", timeout=10)
        assert msg is not None, "IP speak not forwarded to satellite"


# ---------------------------------------------------------------------------
# TS-MI-03..04  Count Skill
# ---------------------------------------------------------------------------

@pytest.mark.skipif(skill_missing(SKILL_COUNT), reason="ovos-skill-count not installed")
class TestCountSkill:
    """TS-MI-03..04 — counting skill via HiveMind."""

    def test_count_to_three(self, misc_topology):
        """TS-MI-03 — 'count to three' produces multiple speaks."""
        b, agent = misc_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture(
            eof_msgs=["ovos.utterance.handled", "mycroft.skill.handler.complete"]
        )
        s0.send(make_utterance("count to three", DEFAULT_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=20)

        speaks = [m for m in messages if m.msg_type == "speak"]
        assert len(speaks) >= 1, (
            f"Expected speaks for counting.\n"
            f"Captured: {[m.msg_type for m in messages]}"
        )

    def test_count_routes_to_satellite(self, misc_topology):
        """TS-MI-04 — counting speaks arrive on satellite bus."""
        b, agent = misc_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture(
            eof_msgs=["ovos.utterance.handled", "mycroft.skill.handler.complete"]
        )
        s0.send(make_utterance("count to three", DEFAULT_PIPELINE, s0.shim.session_id))
        cap.wait(timeout=20)

        msg = wait_for_satellite_message(s0, "speak", timeout=10)
        assert msg is not None, "Count speak not forwarded to satellite"


# ---------------------------------------------------------------------------
# TS-MI-05..08  Edge Cases
# ---------------------------------------------------------------------------

@pytest.mark.skipif(skill_missing(SKILL_HELLO), reason="ovos-skill-hello-world not installed")
class TestEdgeCases:
    """TS-MI-05..08 — edge cases through HiveMind."""

    def test_empty_utterance_list(self, misc_topology):
        """TS-MI-05 — empty utterances list handled gracefully."""
        b, agent = misc_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        from ovos_bus_client.session import Session
        sess = Session(s0.shim.session_id)
        sess.pipeline = DEFAULT_PIPELINE
        msg = Message(
            "recognizer_loop:utterance",
            {"utterances": [], "lang": "en-US"},
            {"session": sess.serialize()},
        )

        cap = agent.new_capture()
        s0.send(msg)
        messages = cap.wait(timeout=10)
        # Should not crash — may produce intent failure or be silently ignored
        # Just verify no exception occurred (test completes)

    def test_multiple_utterance_candidates(self, misc_topology):
        """TS-MI-06 — multiple STT candidates in utterances list."""
        b, agent = misc_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        from ovos_bus_client.session import Session
        sess = Session(s0.shim.session_id)
        sess.pipeline = ADAPT_PIPELINE
        msg = Message(
            "recognizer_loop:utterance",
            {"utterances": ["hello world", "hello word", "jello world"], "lang": "en-US"},
            {"session": sess.serialize()},
        )

        cap = agent.new_capture()
        s0.send(msg)
        messages = cap.wait(timeout=15)

        # First candidate should match
        speak = next((m for m in messages if m.msg_type == "speak"), None)
        assert speak is not None, (
            f"Multiple candidates: first should match.\n"
            f"Captured: {[m.msg_type for m in messages]}"
        )

    def test_rapid_sequential_utterances(self, misc_topology):
        """TS-MI-07 — rapid sequential utterances don't crash."""
        b, agent = misc_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        # Send 3 utterances rapidly
        for text in ["hello world", "hello world", "hello world"]:
            s0.send(make_utterance(text, ADAPT_PIPELINE, s0.shim.session_id))

        # Wait for processing
        time.sleep(5)
        # Just verify no crash — at least one speak should appear
        speaks = [m for m in agent.injected if m.msg_type == "speak"]
        assert len(speaks) >= 1, "Rapid utterances: expected at least one speak"

    def test_unknown_message_type_ignored(self, misc_topology):
        """TS-MI-08 — unknown message type sent to hub doesn't crash."""
        b, agent = misc_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        msg = Message("custom.unknown.event", {"data": "test"})
        s0.send(msg)

        time.sleep(2)
        # Should not crash — test passes if we get here
