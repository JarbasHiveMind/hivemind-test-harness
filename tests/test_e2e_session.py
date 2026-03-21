"""
Session state E2E tests — session propagation through HiveMind.

Verifies that session attributes (lang, pipeline, session_id) are correctly
propagated from satellite through HiveMind to MiniCroft and back.

Prerequisites
-------------
* ovos-skill-hello-world installed
* ovos-skill-date-time installed
* ovoscope installed

Test IDs
--------
TS-SE-01 through TS-SE-04
"""
import time

import pytest
from ovos_bus_client.message import Message
from ovos_bus_client.session import Session

from hivemind_test_harness.plugins.ovoscope_agent import OvoscopeAgentProtocol
from hivemind_test_harness.topology import TopologyBuilder
from tests.conftest import (
    SKILL_HELLO, SKILL_DATETIME,
    skill_missing, make_utterance,
)

ADAPT_PIPELINE = ["ovos-adapt-pipeline-plugin-high"]
PADATIOUS_PIPELINE = ["ovos-padatious-pipeline-plugin-high"]


@pytest.fixture(scope="module")
def session_topology():
    """M0(MiniCroft with hello-world + date-time) + S0."""
    agent = OvoscopeAgentProtocol(skill_ids=[SKILL_HELLO, SKILL_DATETIME])

    _deadline = time.monotonic() + 120
    while time.monotonic() < _deadline:
        if len(agent.bus.ee.listeners(f"{SKILL_HELLO}:HelloWorldIntent")) > 0:
            break
        time.sleep(0.5)
    else:
        pytest.skip("HelloWorldIntent not registered within 120s")

    b = TopologyBuilder()
    b.add_master("M0", agent_protocol=agent)
    b.add_satellite("S0", upstream=b.get_master("M0"))
    b.start_all()
    yield b, agent
    b.stop_all()
    agent.shutdown()


@pytest.mark.skipif(skill_missing(SKILL_HELLO, SKILL_DATETIME),
                     reason="required skills not installed")
class TestLangPropagation:
    """TS-SE-01 — language propagation through HiveMind."""

    def test_lang_propagation(self, session_topology):
        """TS-SE-01 — session with lang reaches MiniCroft."""
        b, agent = session_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture()
        # Send with explicit lang — even if no de-de intent exists,
        # we verify the lang arrives at the hub
        s0.send(make_utterance("hello world", ADAPT_PIPELINE, s0.shim.session_id,
                                lang="en-US"))
        messages = cap.wait(timeout=15)

        utterance = next(
            (m for m in messages if m.msg_type == "recognizer_loop:utterance"),
            None,
        )
        assert utterance is not None
        assert utterance.data.get("lang") == "en-US"


@pytest.mark.skipif(skill_missing(SKILL_HELLO), reason="ovos-skill-hello-world not installed")
class TestSessionIdPreserved:
    """TS-SE-02 — session ID preserved through HiveMind."""

    def test_session_id_preserved(self, session_topology):
        """TS-SE-02 — response session_id matches satellite's session."""
        b, agent = session_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture()
        s0.send(make_utterance("hello world", ADAPT_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=15)

        speak = next((m for m in messages if m.msg_type == "speak"), None)
        assert speak is not None
        speak_session = speak.context.get("session", {})
        assert speak_session.get("session_id") == s0.shim.session_id, (
            f"Session ID mismatch: expected {s0.shim.session_id}, "
            f"got {speak_session.get('session_id')}"
        )


@pytest.mark.skipif(skill_missing(SKILL_HELLO), reason="ovos-skill-hello-world not installed")
class TestPipelineOverride:
    """TS-SE-03 — pipeline override through HiveMind."""

    def test_pipeline_override(self, session_topology):
        """TS-SE-03 — padatious-only pipeline: adapt 'hello world' fails."""
        b, agent = session_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture()
        s0.send(make_utterance("hello world", PADATIOUS_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=15)

        # hello-world uses Adapt for "hello world" — padatious won't match
        assert any(m.msg_type == "complete_intent_failure" for m in messages), (
            f"Expected complete_intent_failure with padatious-only pipeline.\n"
            f"Captured: {[m.msg_type for m in messages]}"
        )


@pytest.mark.skipif(skill_missing(SKILL_HELLO), reason="ovos-skill-hello-world not installed")
class TestMultiTurnSession:
    """TS-SE-04 — multi-turn session continuity."""

    def test_multi_turn_session(self, session_topology):
        """TS-SE-04 — second utterance carries session from first."""
        b, agent = session_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        # First utterance
        cap1 = agent.new_capture()
        s0.send(make_utterance("hello world", ADAPT_PIPELINE, s0.shim.session_id))
        messages1 = cap1.wait(timeout=15)
        speak1 = next((m for m in messages1 if m.msg_type == "speak"), None)
        assert speak1 is not None, "First utterance did not produce speak"

        agent.clear()

        # Second utterance with same session
        cap2 = agent.new_capture()
        s0.send(make_utterance("hello world", ADAPT_PIPELINE, s0.shim.session_id))
        messages2 = cap2.wait(timeout=15)
        speak2 = next((m for m in messages2 if m.msg_type == "speak"), None)
        assert speak2 is not None, "Second utterance did not produce speak"

        # Both should have the same session_id
        s1 = speak1.context.get("session", {}).get("session_id")
        s2 = speak2.context.get("session", {}).get("session_id")
        assert s1 == s2 == s0.shim.session_id
