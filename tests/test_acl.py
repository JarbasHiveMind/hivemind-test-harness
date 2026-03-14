"""
TS-ACL-01..03 — Access Control & Blacklisting scenarios.
"""
import pytest
from ovos_bus_client.message import Message
from hivemind_bus_client.message import HiveMessage, HiveMessageType
from hivemind_test_harness.topology import TopologyBuilder


class TestMessageBlacklist:
    """TS-ACL-01 — blacklisted OVOS message types are silently dropped."""

    def test_blacklisted_type_not_sent_to_satellite(self, minimal_topology):
        b = minimal_topology
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")

        # Update the satellite's DB entry to blacklist "speak"
        db_client = m0.db.get_client_by_api_key(s0.identity.access_key)
        db_client.message_blacklist = ["speak"]
        m0.db.update_item(db_client)
        # Also update the live connection object
        conn = m0.hm_protocol.clients[s0.peer]
        conn.msg_blacklist = ["speak"]

        speak_received = []
        s0.shim.emitter.on(HiveMessageType.BUS, lambda msg: speak_received.append(msg)
                           if isinstance(msg.payload, Message) and
                           msg.payload.msg_type == "speak" else None)

        m0.send_to_satellite(
            s0.peer,
            HiveMessage(HiveMessageType.BUS,
                        payload=Message("speak", {"utterance": "you should not see this"}))
        )

        assert len(speak_received) == 0, \
            "Blacklisted message type 'speak' must be dropped before delivery"

    def test_non_blacklisted_type_still_delivered(self, minimal_topology):
        b = minimal_topology
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")

        conn = m0.hm_protocol.clients[s0.peer]
        conn.msg_blacklist = ["speak"]

        received = []
        s0.shim.emitter.on(HiveMessageType.BUS, received.append)

        m0.send_to_satellite(
            s0.peer,
            HiveMessage(HiveMessageType.BUS,
                        payload=Message("recognizer_loop:utterance", {"utterances": ["hi"]}))
        )

        assert len(received) == 1, "Non-blacklisted type must still be delivered"


class TestSkillBlacklist:
    """TS-ACL-02 — skill blacklist is injected into session context."""

    def test_skill_blacklist_in_session(self):
        b = TopologyBuilder()
        b.add_master("M0")
        b.add_satellite("S0", upstream=b.get_master("M0"),
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
