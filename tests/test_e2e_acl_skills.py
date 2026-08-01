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
from ovos_spec_tools import SpecMessage

from hivescope.topology import TopologyBuilder
from tests.conftest import (
    open_capture,
    make_ovoscope_agent,
    VOICE_TYPES,
    SKILL_HELLO, SKILL_DATETIME, SKILL_VOLUME, SKILL_FALLBACK,
    skill_missing, make_utterance, assert_types_in_order,
    wait_for_satellite_message,
)

# MiniCroft boot alone can take up to MINICROFT_READY_TIMEOUT (180s), and skill
# handlers run serially after that, so the repo-wide 30s default is far too
# tight for this module.
pytestmark = pytest.mark.timeout(300)

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
    agent = make_ovoscope_agent(
        skill_ids=[SKILL_HELLO, SKILL_DATETIME, SKILL_VOLUME, SKILL_FALLBACK]
    )
    try:
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
    finally:
        agent.shutdown()


@pytest.fixture(scope="module")
def skill_blacklist_topology(acl_agent):
    """S0 has hello-world blacklisted; S1 has no restrictions."""
    b = TopologyBuilder()
    try:
        b.add_master("M0", agent_protocol=acl_agent)
        b.add_satellite("S0", upstream=b.get_master("M0"),
                         skill_blacklist=[SKILL_HELLO],
                         allowed_types=VOICE_TYPES)
        b.add_satellite("S1", upstream=b.get_master("M0"),
                         allowed_types=VOICE_TYPES)
        b.start_all()
        yield b, acl_agent
    finally:
        b.stop_all()


@pytest.fixture(scope="module")
def intent_blacklist_topology(acl_agent):
    """S0 has HelloWorldIntent blacklisted."""
    b = TopologyBuilder()
    try:
        b.add_master("M0", agent_protocol=acl_agent)
        b.add_satellite("S0", upstream=b.get_master("M0"),
                         intent_blacklist=[f"{SKILL_HELLO}:HelloWorldIntent"],
                         allowed_types=VOICE_TYPES)
        b.start_all()
        yield b, acl_agent
    finally:
        b.stop_all()


@pytest.fixture(scope="module")
def msg_blacklist_topology(acl_agent):
    """S0 has 'speak' blacklisted — skill runs but speak not delivered."""
    b = TopologyBuilder()
    try:
        b.add_master("M0", agent_protocol=acl_agent)
        b.add_satellite("S0", upstream=b.get_master("M0"),
                         msg_blacklist=[SpecMessage.SPEAK],
                         allowed_types=VOICE_TYPES)
        b.start_all()
        yield b, acl_agent
    finally:
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

        cap = open_capture(agent)
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

        cap = open_capture(agent)
        s0.send(make_utterance("what time is it", DEFAULT_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=15)

        speak = next((m for m in messages if m.msg_type == SpecMessage.SPEAK), None)
        assert speak is not None, (
            f"Non-blacklisted skill did not speak.\n"
            f"Captured: {[m.msg_type for m in messages]}"
        )

    def test_unrestricted_satellite_unaffected(self, skill_blacklist_topology):
        """TS-ACL-03 — S1 (no blacklist) can still use hello-world."""
        b, agent = skill_blacklist_topology
        agent.clear()
        s1 = b.get_satellite("S1")

        cap = open_capture(agent)
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

        cap = open_capture(agent)
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
        cap = open_capture(agent)
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

@pytest.mark.skipif(skill_missing(SKILL_HELLO, SKILL_VOLUME),
                     reason="ovos-skill-hello-world / ovos-skill-volume not installed")
class TestMsgBlacklist:
    """TS-ACL-06..08 — msg_blacklist blocks message delivery to satellite.

    The two tests below fail for *different* reasons — do not merge them:

    * TS-ACL-06 is an upstream gap. ``msg_blacklist`` no longer exists as a
      data-model field: ``hivescope/database.py`` accepts the kwarg for API
      compatibility and drops it, because hivemind-core is whitelist-only
      (``allowed_types``, deny-by-default). Nothing filters the downlink.
    * TS-ACL-08 is NOT a downlink gap. The downlink works; the test used to
      run without the skill that produces the message it waits for.

    The downlink rule itself is one line: the hub sends a bus message to a
    satellite if, and only if, ``message.context["destination"]`` names that
    peer (``hivescope/plugins/agent.py::handle_internal_mycroft``, a verbatim
    port of ``hivemind_ovos_agent_plugin``). The rule is type-blind — a
    destination-addressed ``speak`` and a destination-addressed
    ``mycroft.volume.set`` are both delivered, and neither is filtered.
    """

    @pytest.mark.xfail(strict=True, reason=(
        "Upstream (hivemind-core / hivemind-ovos-agent-plugin): there is no "
        "outbound message blacklist, and msg_blacklist is not even stored — "
        "hivescope/database.py accepts the kwarg and drops it because "
        "hivemind-core is whitelist-only (allowed_types, deny-by-default). "
        "The downlink delivers any bus message whose context['destination'] "
        "names the peer, with no type filter "
        "(hivescope/plugins/agent.py::handle_internal_mycroft), so the speak "
        "arrives. Blocking it needs an outbound ACL upstream; nothing in this "
        "harness can fix it. See the class docstring for the shared downlink "
        "rule and why TS-ACL-08 is a separate, harness-side problem."))
    def test_speak_blacklisted_not_delivered(self, msg_blacklist_topology):
        """TS-ACL-06 — 'speak' blacklisted: skill runs but satellite gets no speak."""
        b, agent = msg_blacklist_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = open_capture(agent)
        s0.send(make_utterance("hello world", ADAPT_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=15)

        # Skill should still fire on hub
        assert any(
            m.msg_type == f"{SKILL_HELLO}:HelloWorldIntent" for m in messages
        ), f"Skill should still execute on hub.\nCaptured: {[m.msg_type for m in messages]}"

        # But satellite should NOT receive speak
        time.sleep(1)
        msg = wait_for_satellite_message(s0, SpecMessage.SPEAK, timeout=2)
        assert msg is None, "speak should be blacklisted from delivery to satellite"

    def test_skill_execution_confirmed_on_hub(self, msg_blacklist_topology):
        """TS-ACL-07 — skill executes normally on hub despite msg_blacklist."""
        b, agent = msg_blacklist_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = open_capture(agent)
        s0.send(make_utterance("hello world", ADAPT_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=15)

        speak = next((m for m in messages if m.msg_type == SpecMessage.SPEAK), None)
        assert speak is not None, "speak should still be emitted on hub bus"

    def test_non_blacklisted_msg_still_delivered(self, msg_blacklist_topology):
        """TS-ACL-08 — non-blacklisted messages still reach satellite.

        This test was strict-xfailed as a "downlink gap". It is not one. The
        class only skipped on ovos-skill-hello-world, but the utterance needs
        ovos-skill-volume, which the ``ovos`` extra does not install. Without
        that skill nothing ever emits mycroft.volume.set, so the wait below
        timed out on the hub side, not the downlink. The class skipif now
        covers ovos-skill-volume, and the hub-side assertion below tells the
        two failure modes apart.
        """
        b, agent = msg_blacklist_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        # Volume messages are not blacklisted
        cap = open_capture(agent)
        s0.send(make_utterance("maximum volume", DEFAULT_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=15)

        # Hub side first: if the skill never ran, the downlink is not on trial.
        assert any(m.msg_type == "mycroft.volume.set" for m in messages), (
            f"Volume skill did not emit mycroft.volume.set on the hub bus, so "
            f"this test cannot say anything about the downlink.\n"
            f"Captured: {[m.msg_type for m in messages]}"
        )

        msg = wait_for_satellite_message(s0, "mycroft.volume.set", timeout=10)
        assert msg is not None, (
            "Non-blacklisted message should still reach satellite"
        )
