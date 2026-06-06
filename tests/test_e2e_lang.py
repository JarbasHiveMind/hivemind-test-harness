"""
Language-dependent intent matching E2E tests via HiveMind.

Tests that session language propagation through HiveMind affects
intent matching correctly. Skills only match intents for languages
they have locale files for.

Test IDs
--------
TS-LANG-01 through TS-LANG-05
"""
import time

import pytest
from ovos_bus_client.message import Message
from ovos_workshop.decorators import intent_handler
from ovos_workshop.skills import OVOSSkill

from hivescope.plugins.ovoscope_agent import OvoscopeAgentProtocol
from hivescope.topology import TopologyBuilder
from tests.conftest import (
    SKILL_HELLO,
    skill_missing, make_utterance,
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

MULTILANG_SKILL_ID = "multilang-test-skill.test"


class MultiLangTestSkill(OVOSSkill):
    """Injected skill that responds differently based on language.

    Has no locale files — uses code-based language detection.
    """

    @intent_handler("test.lang.intent")
    def handle_lang_test(self, message: Message):
        lang = self.lang
        self.speak(f"language is {lang}")


@pytest.fixture(scope="module")
def lang_topology():
    """MiniCroft with hello-world + multilang test skill."""
    agent = OvoscopeAgentProtocol(
        skill_ids=[SKILL_HELLO],
        extra_skills={MULTILANG_SKILL_ID: MultiLangTestSkill}
    )

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


@pytest.mark.skipif(skill_missing(SKILL_HELLO), reason="ovos-skill-hello-world not installed")
class TestLangPropagation:
    """TS-LANG-01..02 — language propagation through HiveMind."""

    def test_english_utterance_matches(self, lang_topology):
        """TS-LANG-01 — en-US utterance matches hello-world intent."""
        b, agent = lang_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture()
        s0.send(make_utterance("hello world", ADAPT_PIPELINE, s0.shim.session_id,
                                lang="en-US"))
        messages = cap.wait(timeout=15)

        assert any(
            m.msg_type == f"{SKILL_HELLO}:HelloWorldIntent" for m in messages
        ), f"English utterance should match.\nCaptured: {[m.msg_type for m in messages]}"

    def test_lang_preserved_in_hub_message(self, lang_topology):
        """TS-LANG-02 — lang field preserved when message reaches hub bus."""
        b, agent = lang_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture()
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
class TestLangMismatch:
    """TS-LANG-03..04 — wrong language does not match English-only skill."""

    def test_german_utterance_no_match(self, lang_topology):
        """TS-LANG-03 — German 'hallo welt' does not match en-US hello-world."""
        b, agent = lang_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture()
        s0.send(make_utterance("hallo welt", ADAPT_PIPELINE, s0.shim.session_id,
                                lang="de-DE"))
        messages = cap.wait(timeout=15)

        # Should NOT match HelloWorldIntent (English-only vocab)
        assert not any(
            m.msg_type == f"{SKILL_HELLO}:HelloWorldIntent" for m in messages
        ), f"German utterance should not match English intent.\nCaptured: {[m.msg_type for m in messages]}"

    def test_french_utterance_no_match(self, lang_topology):
        """TS-LANG-04 — French 'bonjour le monde' does not match en-US hello-world."""
        b, agent = lang_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture()
        s0.send(make_utterance("bonjour le monde", ADAPT_PIPELINE, s0.shim.session_id,
                                lang="fr-FR"))
        messages = cap.wait(timeout=15)

        assert not any(
            m.msg_type == f"{SKILL_HELLO}:HelloWorldIntent" for m in messages
        ), f"French utterance should not match English intent.\nCaptured: {[m.msg_type for m in messages]}"


@pytest.mark.skipif(skill_missing(SKILL_HELLO), reason="ovos-skill-hello-world not installed")
class TestLangInResponse:
    """TS-LANG-05 — skill response carries the correct language."""

    def test_response_lang_matches_request(self, lang_topology):
        """TS-LANG-05 — speak message session lang matches request lang."""
        b, agent = lang_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture()
        s0.send(make_utterance("hello world", ADAPT_PIPELINE, s0.shim.session_id,
                                lang="en-US"))
        messages = cap.wait(timeout=15)

        speak = next((m for m in messages if m.msg_type == "speak"), None)
        assert speak is not None
        sess = speak.context.get("session", {})
        assert sess.get("lang") == "en-US", (
            f"Response lang should be en-US, got {sess.get('lang')}"
        )
