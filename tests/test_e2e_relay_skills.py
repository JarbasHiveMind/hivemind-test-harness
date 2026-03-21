"""
Relay topology E2E tests — skill responses through relay chains.

Tests that utterances from a leaf satellite traverse relay nodes to reach
the hub's MiniCroft, and that responses route back through the chain.

Prerequisites
-------------
* ovos-skill-hello-world installed
* ovos-skill-volume installed
* ovoscope installed

Test IDs
--------
TS-RL-01 through TS-RL-05
"""
import threading
import time

import pytest
from ovos_bus_client.message import Message

from hivemind_test_harness.plugins.ovoscope_agent import OvoscopeAgentProtocol
from hivemind_test_harness.topology import TopologyBuilder
from tests.conftest import (
    SKILL_HELLO, SKILL_VOLUME,
    skill_missing, make_utterance, assert_types_in_order,
    wait_for_satellite_message,
)

ADAPT_PIPELINE = ["ovos-adapt-pipeline-plugin-high"]
DEFAULT_PIPELINE = [
    "ovos-adapt-pipeline-plugin-high",
    "ovos-padatious-pipeline-plugin-high",
    "ovos-adapt-pipeline-plugin-medium",
    "ovos-padatious-pipeline-plugin-medium",
    "ovos-adapt-pipeline-plugin-low",
    "ovos-padatious-pipeline-plugin-low",
]


@pytest.fixture(scope="module")
def relay_topology():
    """Chain: M0(MiniCroft) ← R1(relay) ← S0(satellite)."""
    agent = OvoscopeAgentProtocol(skill_ids=[SKILL_HELLO, SKILL_VOLUME])

    # Install volume.get responder
    agent.bus.on("mycroft.volume.get",
                 lambda m: agent.bus.emit(m.response({"percent": 0.5, "muted": False})))

    # Wait for hello-world intent
    _deadline = time.monotonic() + 120
    while time.monotonic() < _deadline:
        if len(agent.bus.ee.listeners(f"{SKILL_HELLO}:HelloWorldIntent")) > 0:
            break
        time.sleep(0.5)
    else:
        pytest.skip("HelloWorldIntent not registered within 120s")

    b = TopologyBuilder()
    b.add_master("M0", agent_protocol=agent)
    _, r1_master = b.add_relay("R1", upstream=b.get_master("M0"))
    b.add_satellite("S0", upstream=r1_master)
    b.start_all()
    yield b, agent
    b.stop_all()
    agent.shutdown()


@pytest.fixture(scope="module")
def deep_relay_topology():
    """Deep chain: M0(MiniCroft) ← R1 ← R2 ← S0."""
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
    _, r1_master = b.add_relay("R1", upstream=b.get_master("M0"))
    _, r2_master = b.add_relay("R2", upstream=r1_master)
    b.add_satellite("S0", upstream=r2_master)
    b.start_all()
    yield b, agent
    b.stop_all()
    agent.shutdown()


@pytest.mark.skipif(skill_missing(SKILL_HELLO), reason="ovos-skill-hello-world not installed")
class TestRelayUtterance:
    """TS-RL-01..02 — utterances and responses through relay."""

    def test_utterance_through_relay(self, relay_topology):
        """TS-RL-01 — 'hello world' through R1 produces speak on hub bus."""
        b, agent = relay_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture()
        s0.send(make_utterance("hello world", ADAPT_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=15)

        speak = next((m for m in messages if m.msg_type == "speak"), None)
        assert speak is not None, (
            f"'speak' not emitted.\nCaptured: {[m.msg_type for m in messages]}"
        )

    def test_speak_returns_through_relay(self, relay_topology):
        """TS-RL-02 — speak from skill arrives on leaf satellite S0."""
        b, agent = relay_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture()
        s0.send(make_utterance("hello world", ADAPT_PIPELINE, s0.shim.session_id))
        cap.wait(timeout=15)

        msg = wait_for_satellite_message(s0, "speak", timeout=10)
        assert msg is not None, "speak not forwarded through relay to satellite"
        assert msg.data.get("utterance", "").lower() == "hello world"


@pytest.mark.skipif(skill_missing(SKILL_HELLO), reason="ovos-skill-hello-world not installed")
class TestDeepChain:
    """TS-RL-03 — utterances through deep relay chain (M0←R1←R2←S0)."""

    def test_deep_chain(self, deep_relay_topology):
        """TS-RL-03 — 'hello world' through 2 relays still works."""
        b, agent = deep_relay_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture()
        s0.send(make_utterance("hello world", ADAPT_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=15)

        speak = next((m for m in messages if m.msg_type == "speak"), None)
        assert speak is not None, (
            f"'speak' not emitted in deep chain.\n"
            f"Captured: {[m.msg_type for m in messages]}"
        )


@pytest.mark.skipif(skill_missing(SKILL_HELLO, SKILL_VOLUME),
                     reason="required skills not installed")
class TestVolumeRelay:
    """TS-RL-04 — volume messages through relay."""

    def test_volume_through_relay(self, relay_topology):
        """TS-RL-04 — 'maximum volume' through relay delivers volume.set to S0."""
        b, agent = relay_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        sat_vol = []
        evt = threading.Event()

        def _on_vol(msg):
            sat_vol.append(msg)
            evt.set()

        s0.internal_bus.once("mycroft.volume.set", _on_vol)

        cap = agent.new_capture()
        s0.send(make_utterance("maximum volume", DEFAULT_PIPELINE, s0.shim.session_id))
        cap.wait(timeout=15)

        evt.wait(timeout=10)
        assert sat_vol, "mycroft.volume.set not delivered to satellite through relay"


@pytest.mark.skipif(skill_missing(SKILL_HELLO), reason="ovos-skill-hello-world not installed")
class TestIntentFailureRelay:
    """TS-RL-05 — intent failure through relay."""

    def test_intent_failure_through_relay(self, relay_topology):
        """TS-RL-05 — unmatched utterance produces complete_intent_failure."""
        b, agent = relay_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture()
        s0.send(make_utterance("xyzzy gibberish", ADAPT_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=15)

        assert any(m.msg_type == "complete_intent_failure" for m in messages), (
            f"complete_intent_failure not emitted.\n"
            f"Captured: {[m.msg_type for m in messages]}"
        )
