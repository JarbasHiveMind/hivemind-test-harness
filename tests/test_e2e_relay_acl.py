"""
Relay ACL stacking E2E tests.

Tests that access control blacklists compound through relay chains.
When S0 → R1 → M0, blacklists on both the R1-to-M0 connection and
the S0-to-R1 connection should apply.

Test IDs
--------
TS-RACL-01 through TS-RACL-05
"""
import time

import pytest
from ovos_bus_client.message import Message
from ovos_spec_tools import SpecMessage

from hivescope.topology import TopologyBuilder
from tests.conftest import (
    open_capture,
    VOICE_TYPES,
    make_ovoscope_agent,
    SKILL_HELLO, SKILL_DATETIME, SKILL_FALLBACK,
    skill_missing, make_utterance,
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
    "ovos-fallback-pipeline-plugin-high",
    "ovos-fallback-pipeline-plugin-medium",
    "ovos-fallback-pipeline-plugin-low",
]
ADAPT_PIPELINE = ["ovos-adapt-pipeline-plugin-high"]


@pytest.fixture(scope="module")
def relay_acl_agent():
    """Shared MiniCroft with multiple skills."""
    agent = make_ovoscope_agent(
        skill_ids=[SKILL_HELLO, SKILL_DATETIME, SKILL_FALLBACK]
    )
    try:
        _deadline = time.monotonic() + 120
        while time.monotonic() < _deadline:
            if len(agent.bus.ee.listeners(f"{SKILL_HELLO}:HelloWorldIntent")) > 0:
                break
            time.sleep(0.5)
        else:
            pytest.skip("Skills not registered within 120s")
        yield agent
    finally:
        agent.shutdown()


@pytest.fixture(scope="module")
def relay_skill_blacklist_topology(relay_acl_agent):
    """Chain: M0 ← R1(hello-world blacklisted as client of M0) ← S0.
    S0 has no extra restrictions at the R1 level."""
    b = TopologyBuilder()
    try:
        b.add_master("M0", agent_protocol=relay_acl_agent)
        # R1 connects to M0 with hello-world blacklisted
        r1_master = b.add_relay("R1", upstream=b.get_master("M0"),
                                    skill_blacklist=[SKILL_HELLO]).listener
        b.add_satellite("S0", upstream=r1_master,
                         allowed_types=VOICE_TYPES)
        b.start_all()
        yield b, relay_acl_agent
    finally:
        b.stop_all()


@pytest.fixture(scope="module")
def relay_leaf_blacklist_topology(relay_acl_agent):
    """Chain: M0 ← R1(no restrictions) ← S0(date-time blacklisted at R1 level)."""
    b = TopologyBuilder()
    try:
        b.add_master("M0", agent_protocol=relay_acl_agent)
        r1_master = b.add_relay("R1", upstream=b.get_master("M0")).listener
        b.add_satellite("S0", upstream=r1_master,
                         skill_blacklist=[SKILL_DATETIME],
                         allowed_types=VOICE_TYPES)
        b.start_all()
        yield b, relay_acl_agent
    finally:
        b.stop_all()


@pytest.mark.skipif(skill_missing(SKILL_HELLO, SKILL_DATETIME, SKILL_FALLBACK),
                     reason="required skills not installed")
class TestRelayBlacklistPropagation:
    """TS-RACL-01..02 — relay-level blacklist propagates to leaf satellites."""

    def test_relay_blacklist_blocks_leaf(self, relay_skill_blacklist_topology):
        """TS-RACL-01 — R1 has hello-world blacklisted; S0's 'hello world' fails."""
        b, agent = relay_skill_blacklist_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = open_capture(agent)
        s0.send(make_utterance("hello world", DEFAULT_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=15)

        # hello-world should be blocked at the relay level
        assert not any(
            m.msg_type == f"{SKILL_HELLO}:HelloWorldIntent" for m in messages
        ), f"Blacklisted skill at relay level should not execute.\nCaptured: {[m.msg_type for m in messages]}"

    def test_non_blacklisted_skill_through_relay(self, relay_skill_blacklist_topology):
        """TS-RACL-02 — date-time not blacklisted at relay; still works for S0."""
        b, agent = relay_skill_blacklist_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = open_capture(agent)
        s0.send(make_utterance("what time is it", DEFAULT_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=15)

        speak = next((m for m in messages if m.msg_type == SpecMessage.SPEAK), None)
        assert speak is not None, (
            f"Non-blacklisted skill should work through relay.\n"
            f"Captured: {[m.msg_type for m in messages]}"
        )


@pytest.mark.skipif(skill_missing(SKILL_HELLO, SKILL_DATETIME, SKILL_FALLBACK),
                     reason="required skills not installed")
class TestLeafBlacklistAtRelay:
    """TS-RACL-03..05 — leaf satellite blacklist at relay level."""

    def test_leaf_blacklist_blocks_skill(self, relay_leaf_blacklist_topology):
        """TS-RACL-03 — S0 has date-time blacklisted at R1; 'what time' fails."""
        b, agent = relay_leaf_blacklist_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = open_capture(agent)
        s0.send(make_utterance("what time is it", DEFAULT_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=15)

        # Date-time should be blocked for S0
        speaks = [m for m in messages if m.msg_type == SpecMessage.SPEAK]
        # Should get fallback or intent failure, not date-time response
        if speaks:
            # If there's a speak, it should be from fallback, not date-time
            for s in speaks:
                meta = s.data.get("meta", {})
                assert meta.get("skill") != SKILL_DATETIME, (
                    "Date-time skill should be blacklisted for S0"
                )

    def test_non_blacklisted_skill_at_leaf(self, relay_leaf_blacklist_topology):
        """TS-RACL-04 — hello-world not blacklisted for S0; works normally."""
        b, agent = relay_leaf_blacklist_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = open_capture(agent)
        s0.send(make_utterance("hello world", ADAPT_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=15)

        assert any(
            m.msg_type == f"{SKILL_HELLO}:HelloWorldIntent" for m in messages
        ), f"Hello-world should work for S0.\nCaptured: {[m.msg_type for m in messages]}"

    def test_response_routes_through_relay(self, relay_leaf_blacklist_topology):
        """TS-RACL-05 — speak routes back through relay to leaf satellite."""
        b, agent = relay_leaf_blacklist_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        from tests.conftest import wait_for_satellite_message

        cap = open_capture(agent)
        s0.send(make_utterance("hello world", ADAPT_PIPELINE, s0.shim.session_id))
        cap.wait(timeout=15)

        msg = wait_for_satellite_message(s0, SpecMessage.SPEAK, timeout=10)
        assert msg is not None, "speak not routed through relay to leaf"
