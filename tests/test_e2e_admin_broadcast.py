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
from ovos_spec_tools import SpecMessage
from hivemind_bus_client.message import HiveMessage, HiveMessageType

from hivescope.topology import TopologyBuilder
from tests.conftest import (
    open_capture,
    make_ovoscope_agent,
    VOICE_TYPES,
    SKILL_HELLO,
    skill_missing, make_utterance, wait_for_satellite_message,
)

# MiniCroft boot alone can take up to MINICROFT_READY_TIMEOUT (180s), and skill
# handlers run serially after that, so the repo-wide 30s default is far too
# tight for this module.
pytestmark = pytest.mark.timeout(300)

ADAPT_PIPELINE = ["ovos-adapt-pipeline-plugin-high"]


@pytest.fixture(scope="module")
def admin_broadcast_topology():
    """M0 with hello-world, S0(admin), S1, S2."""
    agent = make_ovoscope_agent(skill_ids=[SKILL_HELLO])

    _deadline = time.monotonic() + 120
    while time.monotonic() < _deadline:
        if len(agent.bus.ee.listeners(f"{SKILL_HELLO}:HelloWorldIntent")) > 0:
            break
        time.sleep(0.5)
    else:
        pytest.skip("HelloWorldIntent not registered within 120s")

    b = TopologyBuilder()
    try:
        b.add_master("M0", agent_protocol=agent)
        b.add_satellite("S0", upstream=b.get_master("M0"), is_admin=True,
                         allowed_types=VOICE_TYPES)
        b.add_satellite("S1", upstream=b.get_master("M0"),
                         allowed_types=VOICE_TYPES)
        b.add_satellite("S2", upstream=b.get_master("M0"),
                         allowed_types=VOICE_TYPES)
        b.start_all()
        yield b, agent
    finally:
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

        # hivemind-core keeps the envelope when it forwards (_rewrap,
        # HIVEMIND-NODE-1 §3.3), so siblings see the BROADCAST wrapper with the
        # BUS content still inside it (see TS-BC-01 in tests/test_broadcast.py).
        s1.shim.emitter.on(HiveMessageType.BROADCAST, _on_s1)
        s2.shim.emitter.on(HiveMessageType.BROADCAST, _on_s2)

        # Admin broadcasts a custom event. A BROADCAST payload is a nested
        # HiveMessage, not a raw bus Message: hivemind-core reads
        # ``message.payload.msg_type`` when it unpacks a broadcast, and
        # HiveMessage.payload rebuilds a nested HiveMessage from the stored
        # dict. Handing it a bare Message stores {"type": ...} and the rebuild
        # raises TypeError before the broadcast is ever routed.
        inner = HiveMessage(HiveMessageType.BUS,
                            Message("custom.broadcast.event", {"source": "admin"}))
        s0.send(HiveMessage(HiveMessageType.BROADCAST, payload=inner))

        s1_evt.wait(timeout=10)
        s2_evt.wait(timeout=10)

        assert len(s1_received) >= 1, "S1 did not receive admin broadcast"
        assert len(s2_received) >= 1, "S2 did not receive admin broadcast"
        for got in (s1_received[0], s2_received[0]):
            assert got.payload.msg_type == HiveMessageType.BUS, \
                f"inner payload must stay a BUS, got {got.payload.msg_type}"

    def test_non_admin_broadcast_rejected(self, admin_broadcast_topology):
        """TS-AB-02 — non-admin S1 cannot broadcast."""
        b, agent = admin_broadcast_topology
        s1 = b.get_satellite("S1")
        s0 = b.get_satellite("S0")

        s0_received = []
        s0.shim.emitter.on(HiveMessageType.BUS, lambda m: s0_received.append(m))

        inner = HiveMessage(HiveMessageType.BUS,
                            Message("illegal.broadcast", {"source": "non-admin"}))
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

        cap = open_capture(agent)
        s0.send(make_utterance("hello world", ADAPT_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=15)

        speak = next((m for m in messages if m.msg_type == SpecMessage.SPEAK), None)
        assert speak is not None, "Admin satellite utterance should trigger skill"

    def test_admin_speak_routes_back(self, admin_broadcast_topology):
        """TS-AB-04 — speak from skill routes back to admin satellite."""
        b, agent = admin_broadcast_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = open_capture(agent)
        s0.send(make_utterance("hello world", ADAPT_PIPELINE, s0.shim.session_id))
        cap.wait(timeout=15)

        msg = wait_for_satellite_message(s0, SpecMessage.SPEAK, timeout=10)
        assert msg is not None, "speak not forwarded to admin satellite"
