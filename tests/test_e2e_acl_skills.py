"""
ACL E2E tests with real OVOS skills.

Verifies that HiveMind's access control (skill_blacklist, intent_blacklist,
msg_blacklist) correctly blocks or allows skill execution when utterances
are routed through the full satellite → hub → MiniCroft pipeline.

Prerequisites
-------------
* ovos-skill-hello-world installed
* ovos-skill-date-time installed
* ovos-skill-volume installed
* ovoscope installed

Test IDs
--------
TS-ACL-01 through TS-ACL-08
"""
import threading
import time

import pytest
from ovos_bus_client.message import Message

from hivescope.plugins.ovoscope_agent import OvoscopeAgentProtocol
from hivescope.topology import TopologyBuilder
from tests.conftest import (
    SKILL_HELLO, SKILL_DATETIME, SKILL_VOLUME, SKILL_FALLBACK,
    skill_missing, make_utterance, assert_types_in_order,
    wait_for_satellite_message,
)

ADAPT_PIPELINE = ["ovos-adapt-pipeline-plugin-high"]
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


# ---------------------------------------------------------------------------
# Fixtures — each ACL scenario needs its own topology because ACL is set
# at satellite registration time (immutable per connection)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def acl_agent():
    """Shared MiniCroft with multiple skills — reused across ACL fixtures."""
    agent = OvoscopeAgentProtocol(
        skill_ids=[SKILL_HELLO, SKILL_DATETIME, SKILL_VOLUME, SKILL_FALLBACK]
    )
    # Volume get responder
    agent.bus.on("mycroft.volume.get",
                 lambda m: agent.bus.emit(m.response({"percent": 0.5, "muted": False})))

    _deadline = time.monotonic() + 120
    while time.monotonic() < _deadline:
        if len(agent.bus.ee.listeners(f"{SKILL_HELLO}:HelloWorldIntent")) > 0:
            break
        time.sleep(0.5)
    else:
        pytest.skip("Skills not registered within 120s")
    yield agent
    agent.shutdown()


@pytest.fixture(scope="module")
def skill_blacklist_topology(acl_agent):
    """S0 has hello-world blacklisted; S1 has no restrictions."""
    b = TopologyBuilder()
    b.add_master("M0", agent_protocol=acl_agent)
    b.add_satellite("S0", upstream=b.get_master("M0"),
                     skill_blacklist=[SKILL_HELLO])
    b.add_satellite("S1", upstream=b.get_master("M0"))
    b.start_all()
    yield b, acl_agent
    b.stop_all()


@pytest.fixture(scope="module")
def intent_blacklist_topology(acl_agent):
    """S0 has HelloWorldIntent blacklisted."""
    b = TopologyBuilder()
    b.add_master("M0", agent_protocol=acl_agent)
    b.add_satellite("S0", upstream=b.get_master("M0"),
                     intent_blacklist=[f"{SKILL_HELLO}:HelloWorldIntent"])
    b.start_all()
    yield b, acl_agent
    b.stop_all()


@pytest.fixture(scope="module")
def msg_blacklist_topology(acl_agent):
    """S0 has 'speak' blacklisted — skill runs but speak not delivered."""
    b = TopologyBuilder()
    b.add_master("M0", agent_protocol=acl_agent)
    b.add_satellite("S0", upstream=b.get_master("M0"),
                     msg_blacklist=["speak"])
    b.start_all()
    yield b, acl_agent
    b.stop_all()


# ---------------------------------------------------------------------------
# TS-ACL-01..03  Skill Blacklist
# ---------------------------------------------------------------------------

@pytest.mark.skipif(skill_missing(SKILL_HELLO, SKILL_DATETIME, SKILL_FALLBACK),
                     reason="required skills not installed")
class TestSkillBlacklist:
    """TS-ACL-01..03 — skill_blacklist prevents skill execution."""

    def test_blacklisted_skill_not_executed(self, skill_blacklist_topology):
        """TS-ACL-01 — hello-world blacklisted: 'hello world' falls through."""
        b, agent = skill_blacklist_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture()
        s0.send(make_utterance("hello world", DEFAULT_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=15)

        # HelloWorldIntent should NOT fire — should get fallback or intent failure
        assert not any(
            m.msg_type == f"{SKILL_HELLO}:HelloWorldIntent" for m in messages
        ), f"Blacklisted skill executed.\nCaptured: {[m.msg_type for m in messages]}"

    def test_non_blacklisted_skill_works(self, skill_blacklist_topology):
        """TS-ACL-02 — date-time not blacklisted: still works for S0."""
        b, agent = skill_blacklist_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture()
        s0.send(make_utterance("what time is it", DEFAULT_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=15)

        speak = next((m for m in messages if m.msg_type == "speak"), None)
        assert speak is not None, (
            f"Non-blacklisted skill did not speak.\n"
            f"Captured: {[m.msg_type for m in messages]}"
        )

    def test_unrestricted_satellite_unaffected(self, skill_blacklist_topology):
        """TS-ACL-03 — S1 (no blacklist) can still use hello-world."""
        b, agent = skill_blacklist_topology
        agent.clear()
        s1 = b.get_satellite("S1")

        cap = agent.new_capture()
        s1.send(make_utterance("hello world", ADAPT_PIPELINE, s1.shim.session_id))
        messages = cap.wait(timeout=15)

        assert any(
            m.msg_type == f"{SKILL_HELLO}:HelloWorldIntent" for m in messages
        ), f"Unrestricted satellite should trigger hello-world.\nCaptured: {[m.msg_type for m in messages]}"


# ---------------------------------------------------------------------------
# TS-ACL-04..05  Intent Blacklist
# ---------------------------------------------------------------------------

@pytest.mark.skipif(skill_missing(SKILL_HELLO, SKILL_FALLBACK),
                     reason="required skills not installed")
class TestIntentBlacklist:
    """TS-ACL-04..05 — intent_blacklist blocks specific intents."""

    def test_blacklisted_intent_blocked(self, intent_blacklist_topology):
        """TS-ACL-04 — HelloWorldIntent blacklisted: 'hello world' fails."""
        b, agent = intent_blacklist_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture()
        s0.send(make_utterance("hello world", ADAPT_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=15)

        assert not any(
            m.msg_type == f"{SKILL_HELLO}:HelloWorldIntent" for m in messages
        ), f"Blacklisted intent was executed.\nCaptured: {[m.msg_type for m in messages]}"

    def test_other_intents_in_same_skill_work(self, intent_blacklist_topology):
        """TS-ACL-05 — Greetings.intent not blacklisted: 'good morning' works."""
        b, agent = intent_blacklist_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        padatious = ["ovos-padatious-pipeline-plugin-high"]
        cap = agent.new_capture()
        s0.send(make_utterance("good morning", padatious, s0.shim.session_id))
        messages = cap.wait(timeout=15)

        intent = next(
            (m for m in messages if m.msg_type == f"{SKILL_HELLO}:Greetings.intent"),
            None,
        )
        assert intent is not None, (
            f"Non-blacklisted intent in same skill should still work.\n"
            f"Captured: {[m.msg_type for m in messages]}"
        )


# ---------------------------------------------------------------------------
# TS-ACL-06..08  Message Blacklist
# ---------------------------------------------------------------------------

@pytest.mark.skipif(skill_missing(SKILL_HELLO),
                     reason="ovos-skill-hello-world not installed")
class TestMsgBlacklist:
    """TS-ACL-06..08 — msg_blacklist blocks message delivery to satellite."""

    def test_speak_blacklisted_not_delivered(self, msg_blacklist_topology):
        """TS-ACL-06 — 'speak' blacklisted: skill runs but satellite gets no speak."""
        b, agent = msg_blacklist_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture()
        s0.send(make_utterance("hello world", ADAPT_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=15)

        # Skill should still fire on hub
        assert any(
            m.msg_type == f"{SKILL_HELLO}:HelloWorldIntent" for m in messages
        ), f"Skill should still execute on hub.\nCaptured: {[m.msg_type for m in messages]}"

        # But satellite should NOT receive speak
        time.sleep(1)
        msg = wait_for_satellite_message(s0, "speak", timeout=2)
        assert msg is None, "speak should be blacklisted from delivery to satellite"

    def test_skill_execution_confirmed_on_hub(self, msg_blacklist_topology):
        """TS-ACL-07 — skill executes normally on hub despite msg_blacklist."""
        b, agent = msg_blacklist_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture()
        s0.send(make_utterance("hello world", ADAPT_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=15)

        speak = next((m for m in messages if m.msg_type == "speak"), None)
        assert speak is not None, "speak should still be emitted on hub bus"

    def test_non_blacklisted_msg_still_delivered(self, msg_blacklist_topology):
        """TS-ACL-08 — non-blacklisted messages still reach satellite."""
        b, agent = msg_blacklist_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        # Volume messages are not blacklisted
        cap = agent.new_capture()
        s0.send(make_utterance("maximum volume", DEFAULT_PIPELINE, s0.shim.session_id))
        cap.wait(timeout=15)

        msg = wait_for_satellite_message(s0, "mycroft.volume.set", timeout=10)
        assert msg is not None, (
            "Non-blacklisted message should still reach satellite"
        )
