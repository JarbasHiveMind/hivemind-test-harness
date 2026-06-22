"""
TS-ACL-01..03 — Access Control & Blacklisting scenarios.
"""
import pytest
from ovos_bus_client.message import Message
from hivemind_bus_client.message import HiveMessage, HiveMessageType
from hivescope.topology import TopologyBuilder


# NOTE: outbound message-type blacklisting (the old Client.message_blacklist /
# conn.msg_blacklist) was removed from hivemind-core — it is whitelist-only by
# design (admission via allowed_types, see TestAllowedTypes). The former
# TestMessageBlacklist scenarios asserted removed behaviour and were dropped.


class TestSkillBlacklist:
    """TS-ACL-02 — skill blacklist is injected into session context."""

    def test_skill_blacklist_in_session(self):
        b = TopologyBuilder()
        b.add_master("M0")
        b.add_satellite("S0", upstream=b.get_master("M0"),
                        allowed_types=["recognizer_loop:utterance"],
                        skill_blacklist=["mycroft.volume.skill"])
        b.start_all()

        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")

        s0.send(Message("recognizer_loop:utterance", {"utterances": ["volume up"]}))

        msg = m0.agent_protocol.last_injected("recognizer_loop:utterance")
        assert msg is not None
        session = msg.context.get("session") or {}
        blacklisted = session.get("blacklisted_skills", [])
        assert "mycroft.volume.skill" in blacklisted, \
            "Skill blacklist must be injected into session context"
        b.stop_all()


class TestIntentBlacklist:
    """TS-ACL-03 — intent blacklist is injected into session context."""

    def test_intent_blacklist_in_session(self):
        b = TopologyBuilder()
        b.add_master("M0")
        b.add_satellite("S0", upstream=b.get_master("M0"),
                        allowed_types=["recognizer_loop:utterance"],
                        intent_blacklist=["mycroft.volume.skill:set.volume"])
        b.start_all()

        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")

        s0.send(Message("recognizer_loop:utterance", {"utterances": ["set volume"]}))

        msg = m0.agent_protocol.last_injected("recognizer_loop:utterance")
        assert msg is not None
        session = msg.context.get("session") or {}
        blacklisted = session.get("blacklisted_intents", [])
        assert "mycroft.volume.skill:set.volume" in blacklisted, \
            "Intent blacklist must be injected into session context"
        b.stop_all()


class TestAllowedTypes:
    """TS-BUS-03 — only allowed_types pass through from satellite to master bus."""

    def test_allowed_type_injected(self):
        b = TopologyBuilder()
        b.add_master("M0")
        b.add_satellite("S0", upstream=b.get_master("M0"),
                        allowed_types=["recognizer_loop:utterance"])
        b.start_all()

        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")

        s0.send(Message("recognizer_loop:utterance", {"utterances": ["allowed"]}))
        m0.agent_protocol.assert_injected("recognizer_loop:utterance")
        b.stop_all()

    def test_disallowed_type_dropped(self):
        b = TopologyBuilder()
        b.add_master("M0")
        b.add_satellite("S0", upstream=b.get_master("M0"),
                        allowed_types=["recognizer_loop:utterance"])
        b.start_all()

        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")

        s0.send(Message("some.forbidden.type", {}))
        m0.agent_protocol.assert_not_injected("some.forbidden.type")
        b.stop_all()
