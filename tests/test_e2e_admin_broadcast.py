"""
Admin broadcast E2E tests with real OVOS skills.

Tests that an admin satellite can broadcast messages through HiveMind
and that skill responses route back to the correct satellite(s).

Test IDs
--------
TS-AB-01 through TS-AB-04
"""
import threading
import time

import pytest
from ovos_bus_client.message import Message
from hivemind_bus_client.message import HiveMessage, HiveMessageType

from hivescope.plugins.ovoscope_agent import OvoscopeAgentProtocol
from hivescope.topology import TopologyBuilder
from tests.conftest import (
    SKILL_HELLO,
    skill_missing, make_utterance, wait_for_satellite_message,
)

ADAPT_PIPELINE = ["ovos-adapt-pipeline-plugin-high"]


@pytest.fixture(scope="module")
def admin_broadcast_topology():
    """M0 with hello-world, S0(admin), S1, S2."""
    agent = OvoscopeAgentProtocol(skill_ids=[SKILL_HELLO])

    _deadline = time.monotonic() + 120
    while time.monotonic() < _deadline:
        if len(agent.bus.ee.listeners(f"{SKILL_HELLO}:HelloWorldIntent")) > 0:
            break
        time.sleep(0.5)
    else:
        pytest.skip("HelloWorldIntent not registered within 120s")

    b = TopologyBuilder()
    b.add_master("M0", agent_protocol=agent)
    b.add_satellite("S0", upstream=b.get_master("M0"), is_admin=True)
    b.add_satellite("S1", upstream=b.get_master("M0"))
    b.add_satellite("S2", upstream=b.get_master("M0"))
    b.start_all()
    yield b, agent
    b.stop_all()
    agent.shutdown()


@pytest.mark.skipif(skill_missing(SKILL_HELLO), reason="ovos-skill-hello-world not installed")
class TestAdminBroadcast:
    """TS-AB-01..02 — admin broadcast bus message delivery."""

    def test_admin_broadcast_reaches_siblings(self, admin_broadcast_topology):
        """TS-AB-01 — admin S0 broadcasts; S1 and S2 receive."""
        b, agent = admin_broadcast_topology
        s0 = b.get_satellite("S0")
        s1 = b.get_satellite("S1")
        s2 = b.get_satellite("S2")

        s1_received = []
        s2_received = []
        s1_evt = threading.Event()
        s2_evt = threading.Event()

        def _on_s1(msg):
            s1_received.append(msg)
            s1_evt.set()

        def _on_s2(msg):
            s2_received.append(msg)
            s2_evt.set()

        # Listen for broadcast on siblings' shim emitters
        s1.shim.emitter.on(HiveMessageType.BROADCAST, _on_s1)
        s2.shim.emitter.on(HiveMessageType.BROADCAST, _on_s2)

        # Admin broadcasts a custom event
        inner = Message("custom.broadcast.event", {"source": "admin"})
        s0.send(HiveMessage(HiveMessageType.BROADCAST, payload=inner))

        s1_evt.wait(timeout=10)
        s2_evt.wait(timeout=10)

        assert len(s1_received) >= 1, "S1 did not receive admin broadcast"
        assert len(s2_received) >= 1, "S2 did not receive admin broadcast"

    def test_non_admin_broadcast_rejected(self, admin_broadcast_topology):
        """TS-AB-02 — non-admin S1 cannot broadcast."""
        b, agent = admin_broadcast_topology
        s1 = b.get_satellite("S1")
        s0 = b.get_satellite("S0")

        s0_received = []
        s0.shim.emitter.on(HiveMessageType.BROADCAST, lambda m: s0_received.append(m))

        inner = Message("illegal.broadcast", {"source": "non-admin"})
        s1.send(HiveMessage(HiveMessageType.BROADCAST, payload=inner))

        time.sleep(2)
        assert len(s0_received) == 0, "Non-admin broadcast should be rejected"


@pytest.mark.skipif(skill_missing(SKILL_HELLO), reason="ovos-skill-hello-world not installed")
class TestAdminBroadcastWithSkill:
    """TS-AB-03..04 — admin broadcasts utterance, skill responds."""

    def test_admin_broadcasts_utterance_to_hub(self, admin_broadcast_topology):
        """TS-AB-03 — admin sends utterance via BUS (not broadcast); skill responds."""
        b, agent = admin_broadcast_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture()
        s0.send(make_utterance("hello world", ADAPT_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=15)

        speak = next((m for m in messages if m.msg_type == "speak"), None)
        assert speak is not None, "Admin satellite utterance should trigger skill"

    def test_admin_speak_routes_back(self, admin_broadcast_topology):
        """TS-AB-04 — speak from skill routes back to admin satellite."""
        b, agent = admin_broadcast_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture()
        s0.send(make_utterance("hello world", ADAPT_PIPELINE, s0.shim.session_id))
        cap.wait(timeout=15)

        msg = wait_for_satellite_message(s0, "speak", timeout=10)
        assert msg is not None, "speak not forwarded to admin satellite"
