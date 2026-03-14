"""
TS-BUS-01..04 — BUS message scenarios.
"""
import pytest
from ovos_bus_client.message import Message
from hivemind_bus_client.message import HiveMessage, HiveMessageType


class TestSatelliteInjectsBus:
    """TS-BUS-01 — satellite injects a BUS message into master's agent bus."""

    def test_message_arrives_on_agent_bus(self, minimal_topology):
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        s0.send(Message("recognizer_loop:utterance", {"utterances": ["hello world"]}))

        m0.agent_protocol.assert_injected("recognizer_loop:utterance", count=1)

    def test_message_data_preserved(self, minimal_topology):
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        s0.send(Message("recognizer_loop:utterance", {"utterances": ["test phrase"]}))

        msg = m0.agent_protocol.last_injected("recognizer_loop:utterance")
        assert msg is not None
        assert msg.data["utterances"] == ["test phrase"]

    def test_message_context_has_peer(self, minimal_topology):
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        s0.send(Message("recognizer_loop:utterance", {"utterances": ["hi"]}))

        msg = m0.agent_protocol.last_injected("recognizer_loop:utterance")
        assert msg is not None
        assert msg.context.get("peer") == s0.peer

    def test_session_context_injected(self, minimal_topology):
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        s0.send(Message("recognizer_loop:utterance", {"utterances": ["session test"]}))

        msg = m0.agent_protocol.last_injected("recognizer_loop:utterance")
        assert msg is not None
        sess = msg.context.get("session") or {}
        assert sess.get("session_id") == s0.shim.session_id


class TestMasterReplyToSatellite:
    """TS-BUS-02 — master emits a BUS reply targeted at a specific satellite."""

    def test_satellite_receives_reply(self, minimal_topology):
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        # Satellite waits for an inbound message from master
        reply_msg = None

        def capture(msg):
            nonlocal reply_msg
            reply_msg = msg

        s0.internal_bus.on("speak", capture)

        # Master sends BUS message to satellite
        m0.send_to_satellite(
            s0.peer,
            HiveMessage(HiveMessageType.BUS,
                        payload=Message("speak", {"utterance": "hello back"},
                                        {"destination": s0.peer}))
        )

        # Give the synchronous call chain a moment — it's all in-process so
        # the handler fires during send_to_satellite. Check directly.
        assert reply_msg is not None, "Satellite did not receive the reply"
        assert reply_msg.data["utterance"] == "hello back"


class TestAllowedTypes:
    """TS-BUS-03 — only message types in allowed_types pass through."""

    def test_allowed_type_is_injected(self, minimal_topology):
        b = minimal_topology
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")

        # recognizer_loop:utterance is in the default allowed_types list
        s0.send(Message("recognizer_loop:utterance", {"utterances": ["hi"]}))
        m0.agent_protocol.assert_injected("recognizer_loop:utterance")

    def test_unauthorized_type_is_dropped(self):
        b = TopologyBuilder()
        b.add_master("M0")
        b.add_satellite("S0", upstream=b.get_master("M0"),
                        allowed_types=["recognizer_loop:utterance"])
        b.start_all()

        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")

        s0.send(Message("some.unauthorized.message", {}))
        m0.agent_protocol.assert_not_injected("some.unauthorized.message")
        b.stop_all()


class TestMultipleSatellitesBus:
    """Multiple satellites inject independently; each is isolated."""

    def test_each_satellite_injects_independently(self, star_topology):
        b = star_topology
        m0 = b.get_master("M0")

        for i in range(3):
            b.get_satellite(f"S{i}").send(
                Message("recognizer_loop:utterance", {"utterances": [f"from S{i}"]})
            )

        m0.agent_protocol.assert_injected("recognizer_loop:utterance", count=3)

    def test_sessions_do_not_bleed_across_satellites(self, star_topology):
        b = star_topology
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")
        s1 = b.get_satellite("S1")

        s0.send(Message("recognizer_loop:utterance", {"utterances": ["s0 msg"]}))
        s1.send(Message("recognizer_loop:utterance", {"utterances": ["s1 msg"]}))

        msgs = m0.agent_protocol.injected
        by_session = {}
        for m in msgs:
            sid = (m.context.get("session") or {}).get("session_id")
            by_session.setdefault(sid, []).append(m)

        assert s0.shim.session_id in by_session
        assert s1.shim.session_id in by_session
        assert s0.shim.session_id != s1.shim.session_id


# resolve forward reference used in test_unauthorized_type_is_dropped
from hivemind_test_harness.topology import TopologyBuilder
