"""
Multi-skill E2E tests routing utterances through HiveMind satellite → hub → MiniCroft.

Prerequisites
-------------
* Skills installed: ovos-skill-date-time, ovos-skill-personal, ovos-skill-naptime,
  ovos-skill-fallback-unknown, ovos-skill-easter-eggs, ovos-skill-spelling
* ovoscope installed

Test IDs
--------
TS-SK-01 through TS-SK-11
"""
import threading
import time

import pytest
from ovos_bus_client.message import Message

from hivescope.plugins.ovoscope_agent import OvoscopeAgentProtocol
from hivescope.topology import TopologyBuilder
from tests.conftest import (
    SKILL_DATETIME, SKILL_PERSONAL, SKILL_NAPTIME, SKILL_FALLBACK,
    SKILL_EASTER_EGGS, SKILL_SPELLING,
    skill_missing, make_utterance, assert_types_in_order,
    wait_for_satellite_message,
)

# MiniCroft boot alone can take up to MINICROFT_READY_TIMEOUT (180s), and skill
# handlers run serially after that, so the repo-wide 30s default is far too
# tight for this module.
pytestmark = pytest.mark.timeout(300)

# Use full default pipeline for most tests
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

ALL_SKILLS = [SKILL_DATETIME, SKILL_PERSONAL, SKILL_NAPTIME, SKILL_FALLBACK,
              SKILL_EASTER_EGGS, SKILL_SPELLING]


@pytest.fixture(scope="module")
def skills_topology():
    """Boot MiniCroft with multiple skills, connect one satellite."""
    agent = OvoscopeAgentProtocol(skill_ids=ALL_SKILLS)

    # Wait for skills to register intents (up to 120s). date-time registers
    # padatious .intent handlers (e.g. what.time.is.it.intent), not an adapt
    # HandleTimeIntent — wait for the real intent so we don't burn the full
    # 120s deadline before every run.
    b = TopologyBuilder()
    try:
        _deadline = time.monotonic() + 120
        while time.monotonic() < _deadline:
            listeners = agent.bus.ee.listeners(
                f"{SKILL_DATETIME}:what.time.is.it.intent")
            if len(listeners) > 0:
                break
            time.sleep(0.5)
        else:
            # Sibling e2e modules all skip here; this one silently carried on
            # and every test then failed for the wrong reason. The finally
            # block shuts the already-booted MiniCroft down.
            pytest.skip("Date-time skill intents not registered within 120s")

        b.add_master("M0", agent_protocol=agent)
        b.add_satellite("S0", upstream=b.get_master("M0"),
                        allowed_types=["recognizer_loop:utterance"])
        b.start_all()
        yield b, agent
    finally:
        b.stop_all()
        agent.shutdown()


# ---------------------------------------------------------------------------
# TS-SK-01..03  Date-Time Skill
# ---------------------------------------------------------------------------

# ovos-skill-date-time ships locale .intent templates that combine optional
# [brackets] with {slot} placeholders (e.g. "[do you have the] hour in
# {location}"). ovos-padatious' bracket-expansion mangles several of them into
# "unbalanced or nested braces" and the affected intents — what.time.is.it,
# what.time.will.it.be, weekday.*, current_date among them — fail to train.
# Which intents survive training is nondeterministic across boots, so even
# "what is the date" matches only intermittently. The HiveMind→skill→speak path
# itself is solid (proven deterministically green by test_helloworld_hivemind,
# whose adapt + clean padatious intents always match); these failures are an
# upstream skill/padatious defect, not a harness regression. Mark the class
# xfail(strict=False) so it surfaces as xpass when training happens to succeed
# and xfail when it doesn't, without flaking the suite red.
_DATETIME_PADATIOUS_FLAKY = (
    "upstream ovos-skill-date-time/ovos-padatious bracket-expansion bug: "
    "[..]{slot} templates fail padatious training ('unbalanced or nested "
    "braces'), so date/time intents match only intermittently"
)


@pytest.mark.xfail(reason=_DATETIME_PADATIOUS_FLAKY, strict=False)
@pytest.mark.skipif(skill_missing(SKILL_DATETIME), reason="ovos-skill-date-time not installed")
class TestDateTimeSkill:
    """TS-SK-01..03 — date-time skill via HiveMind."""

    def test_time_intent_fires(self, skills_topology):
        """TS-SK-01 — 'what time is it' produces speak with time."""
        b, agent = skills_topology
        agent.clear()
        s0 = b.get_satellite("S0")
        cap = agent.new_capture()
        s0.send(make_utterance("what time is it", DEFAULT_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=60)
        speak = next((m for m in messages if m.msg_type == "speak"), None)
        assert speak is not None, f"'speak' not emitted.\nCaptured: {[m.msg_type for m in messages]}"

    def test_date_intent_fires(self, skills_topology):
        """TS-SK-02 — 'what is the date' produces speak."""
        b, agent = skills_topology
        agent.clear()
        s0 = b.get_satellite("S0")
        cap = agent.new_capture()
        s0.send(make_utterance("what is the date", DEFAULT_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=60)
        speak = next((m for m in messages if m.msg_type == "speak"), None)
        assert speak is not None, f"'speak' not emitted.\nCaptured: {[m.msg_type for m in messages]}"

    def test_speak_routes_to_satellite(self, skills_topology):
        """TS-SK-03 — speak from date-time arrives on satellite bus."""
        b, agent = skills_topology
        agent.clear()
        s0 = b.get_satellite("S0")
        cap = agent.new_capture()
        s0.send(make_utterance("what is the date", DEFAULT_PIPELINE, s0.shim.session_id))
        cap.wait(timeout=60)
        msg = wait_for_satellite_message(s0, "speak", timeout=30)
        assert msg is not None, "speak not forwarded to satellite"


# ---------------------------------------------------------------------------
# TS-SK-04..05  Personal Skill
# ---------------------------------------------------------------------------

@pytest.mark.skipif(skill_missing(SKILL_PERSONAL), reason="ovos-skill-personal not installed")
class TestPersonalSkill:
    """TS-SK-04..05 — personal skill via HiveMind."""

    def test_name_intent(self, skills_topology):
        """TS-SK-04 — 'what is your name' produces speak."""
        b, agent = skills_topology
        agent.clear()
        s0 = b.get_satellite("S0")
        cap = agent.new_capture()
        s0.send(make_utterance("what is your name", DEFAULT_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=60)
        speak = next((m for m in messages if m.msg_type == "speak"), None)
        assert speak is not None, f"'speak' not emitted.\nCaptured: {[m.msg_type for m in messages]}"

    def test_who_made_you(self, skills_topology):
        """TS-SK-05 — 'who made you' produces speak."""
        b, agent = skills_topology
        agent.clear()
        s0 = b.get_satellite("S0")
        cap = agent.new_capture()
        s0.send(make_utterance("who made you", DEFAULT_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=60)
        speak = next((m for m in messages if m.msg_type == "speak"), None)
        assert speak is not None, f"'speak' not emitted.\nCaptured: {[m.msg_type for m in messages]}"


# ---------------------------------------------------------------------------
# TS-SK-06..07  Naptime Skill
# ---------------------------------------------------------------------------

@pytest.mark.skipif(skill_missing(SKILL_NAPTIME), reason="ovos-skill-naptime not installed")
class TestNaptimeSkill:
    """TS-SK-06..07 — naptime skill via HiveMind."""

    def test_go_to_sleep(self, skills_topology):
        """TS-SK-06 — 'go to sleep' emits recognizer_loop:sleep."""
        b, agent = skills_topology
        agent.clear()
        s0 = b.get_satellite("S0")
        cap = agent.new_capture(
            eof_msgs=["ovos.utterance.handled", "recognizer_loop:sleep"]
        )
        s0.send(make_utterance("go to sleep", DEFAULT_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=60)
        assert any(m.msg_type == "recognizer_loop:sleep" for m in messages), (
            f"recognizer_loop:sleep not emitted.\nCaptured: {[m.msg_type for m in messages]}"
        )

    def test_wake_up(self, skills_topology):
        """TS-SK-07 — 'wake up' emits recognizer_loop:wake_up."""
        b, agent = skills_topology
        agent.clear()
        s0 = b.get_satellite("S0")
        cap = agent.new_capture(
            eof_msgs=["ovos.utterance.handled", "recognizer_loop:wake_up"]
        )
        s0.send(make_utterance("wake up", DEFAULT_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=60)
        assert any(m.msg_type == "recognizer_loop:wake_up" for m in messages), (
            f"recognizer_loop:wake_up not emitted.\nCaptured: {[m.msg_type for m in messages]}"
        )


# ---------------------------------------------------------------------------
# TS-SK-08..09  Fallback Skill
# ---------------------------------------------------------------------------

@pytest.mark.skipif(skill_missing(SKILL_FALLBACK), reason="ovos-skill-fallback-unknown not installed")
class TestFallbackSkill:
    """TS-SK-08..09 — fallback unknown skill via HiveMind."""

    def test_unknown_utterance(self, skills_topology):
        """TS-SK-08 — unrecognized utterance triggers fallback speak."""
        b, agent = skills_topology
        agent.clear()
        s0 = b.get_satellite("S0")
        cap = agent.new_capture()
        s0.send(make_utterance("xyzzy foobar gibberish nonsense", DEFAULT_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=60)
        speak = next((m for m in messages if m.msg_type == "speak"), None)
        assert speak is not None, f"Fallback speak not emitted.\nCaptured: {[m.msg_type for m in messages]}"

    def test_fallback_routes_to_satellite(self, skills_topology):
        """TS-SK-09 — fallback speak arrives on satellite bus."""
        b, agent = skills_topology
        agent.clear()
        s0 = b.get_satellite("S0")
        cap = agent.new_capture()
        s0.send(make_utterance("xyzzy foobar gibberish nonsense", DEFAULT_PIPELINE, s0.shim.session_id))
        cap.wait(timeout=60)
        msg = wait_for_satellite_message(s0, "speak", timeout=10)
        assert msg is not None, "Fallback speak not forwarded to satellite"


# ---------------------------------------------------------------------------
# TS-SK-10  Easter Eggs Skill
# ---------------------------------------------------------------------------

@pytest.mark.skipif(skill_missing(SKILL_EASTER_EGGS), reason="ovos-skill-easter-eggs not installed")
class TestEasterEggs:
    """TS-SK-10 — easter eggs skill via HiveMind."""

    def test_pod_bay_doors(self, skills_topology):
        """TS-SK-10 — 'open the pod bay doors' triggers HAL response."""
        b, agent = skills_topology
        agent.clear()
        s0 = b.get_satellite("S0")
        cap = agent.new_capture()
        s0.send(make_utterance("open the pod bay doors", DEFAULT_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=60)
        speak = next((m for m in messages if m.msg_type == "speak"), None)
        assert speak is not None, f"'speak' not emitted.\nCaptured: {[m.msg_type for m in messages]}"


# ---------------------------------------------------------------------------
# TS-SK-11  Spelling Skill
# ---------------------------------------------------------------------------

@pytest.mark.skipif(skill_missing(SKILL_SPELLING), reason="ovos-skill-spelling not installed")
class TestSpelling:
    """TS-SK-11 — spelling skill via HiveMind."""

    def test_spell_word(self, skills_topology):
        """TS-SK-11 — 'spell hello' produces speak with letters."""
        b, agent = skills_topology
        agent.clear()
        s0 = b.get_satellite("S0")
        cap = agent.new_capture()
        s0.send(make_utterance("spell hello", DEFAULT_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=60)
        speak = next((m for m in messages if m.msg_type == "speak"), None)
        assert speak is not None, f"'speak' not emitted.\nCaptured: {[m.msg_type for m in messages]}"
