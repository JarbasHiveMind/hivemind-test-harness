"""
TS-ACL-01..03 — Access Control & Blacklisting scenarios.
"""
import pytest
from ovos_bus_client.message import Message
from hivemind_bus_client.message import HiveMessage, HiveMessageType
from hivescope.assertions import assert_policy_denied
from hivescope.topology import TopologyBuilder


# NOTE: outbound message-type blacklisting (the old Client.message_blacklist /
# conn.msg_blacklist) was removed from hivemind-core — it is whitelist-only by
# design (admission via allowed_types, see TestAllowedTypes). The former
# TestMessageBlacklist scenarios asserted removed behaviour and were dropped.
# The remaining downstream *delivery* blacklist is a different mechanism and is
# covered end-to-end in tests/test_e2e_acl_skills.py.


class TestSkillBlacklist:
    """TS-ACL-02 — skill blacklist is injected into session context.

    This class asserts the *injection* only: that the master stamps the
    blacklist onto the session it hands the agent. Whether OVOS then actually
    refuses to run the blacklisted skill is enforcement, and is asserted
    against a live MiniCroft in tests/test_e2e_acl_skills.py.
    """

    def test_skill_blacklist_in_session(self):
        b = TopologyBuilder()
        try:
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
        finally:
            b.stop_all()


class TestIntentBlacklist:
    """TS-ACL-03 — intent blacklist is injected into session context.

    Injection only — see TestSkillBlacklist; enforcement lives in
    tests/test_e2e_acl_skills.py.
    """

    def test_intent_blacklist_in_session(self):
        b = TopologyBuilder()
        try:
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
        finally:
            b.stop_all()


class TestAllowedTypes:
    """TS-BUS-03 — only allowed_types pass through from satellite to master bus."""

    def test_allowed_type_injected(self):
        b = TopologyBuilder()
        try:
            b.add_master("M0")
            b.add_satellite("S0", upstream=b.get_master("M0"),
                            allowed_types=["recognizer_loop:utterance"])
            b.start_all()

            m0 = b.get_master("M0")
            s0 = b.get_satellite("S0")

            s0.send(Message("recognizer_loop:utterance", {"utterances": ["allowed"]}))
            m0.agent_protocol.assert_injected("recognizer_loop:utterance")
        finally:
            b.stop_all()

    def test_disallowed_type_dropped(self):
        b = TopologyBuilder()
        try:
            b.add_master("M0")
            b.add_satellite("S0", upstream=b.get_master("M0"),
                            allowed_types=["recognizer_loop:utterance"])
            b.start_all()

            m0 = b.get_master("M0")
            s0 = b.get_satellite("S0")

            s0.send(Message("some.forbidden.type", {}))
            m0.agent_protocol.assert_not_injected("some.forbidden.type")
        finally:
            b.stop_all()

    def test_disallowed_type_is_denied_with_acl_code(self):
        """The drop is not silent: the satellite gets hive.policy.denied.

        ``assert_policy_denied`` correlates on the echoed OVOS inner type
        (``some.forbidden.type``) — never a HiveMessageType value — and
        ``strict=True`` requires that correlation, so an unrelated denial
        cannot satisfy the assertion.
        """
        b = TopologyBuilder()
        try:
            b.add_master("M0")
            b.add_satellite("S0", upstream=b.get_master("M0"),
                            allowed_types=["recognizer_loop:utterance"])
            b.start_all()

            m0 = b.get_master("M0")
            s0 = b.get_satellite("S0")

            s0.send(Message("some.forbidden.type", {}))
            assert_policy_denied(m0, s0, "some.forbidden.type",
                                 deny_code="acl_disallowed_type", strict=True)
        finally:
            b.stop_all()
