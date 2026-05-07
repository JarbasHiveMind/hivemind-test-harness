"""
Binary audio → skill response E2E tests via HiveMind.

Tests the chain: satellite sends raw audio (BINARY) → master's binary
protocol receives it → we verify delivery → then manually inject the
utterance that STT would produce → skill responds → speak routes back.

This tests the complete binary + skill pipeline minus the actual STT
engine (which requires hardware/model).

Test IDs
--------
TS-BIN-01 through TS-BIN-05
"""
import time

import pytest
from ovos_bus_client.message import Message
from hivemind_bus_client.message import HiveMessage, HiveMessageType
from hivemind_bus_client.serialization import HiveMindBinaryPayloadType

from hivescope.plugins.ovoscope_agent import OvoscopeAgentProtocol
from hivescope.topology import TopologyBuilder
from tests.conftest import (
    SKILL_HELLO,
    skill_missing, make_utterance, wait_for_satellite_message,
)

ADAPT_PIPELINE = ["ovos-adapt-pipeline-plugin-high"]

# Fake audio data for binary tests
FAKE_AUDIO = b"\x00\x01\x02\x03" * 1000  # 4KB fake WAV


@pytest.fixture(scope="module")
def binary_skill_topology():
    """MiniCroft with hello-world + binary protocol."""
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
    b.add_satellite("S0", upstream=b.get_master("M0"))
    b.start_all()
    yield b, agent
    b.stop_all()
    agent.shutdown()


@pytest.mark.skipif(skill_missing(SKILL_HELLO), reason="ovos-skill-hello-world not installed")
class TestBinaryAudioDelivery:
    """TS-BIN-01..02 — binary audio delivery to master."""

    def test_raw_audio_reaches_master(self, binary_skill_topology):
        """TS-BIN-01 — satellite sends RAW_AUDIO binary; master binary protocol receives it."""
        b, agent = binary_skill_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")
        m0.binary_protocol.clear()

        msg = HiveMessage(
            HiveMessageType.BINARY,
            payload=FAKE_AUDIO,
            bin_type=HiveMindBinaryPayloadType.RAW_AUDIO,
            metadata={"sample_rate": 16000, "sample_width": 2}
        )
        s0.send(msg)

        time.sleep(2)
        m0.binary_protocol.assert_called("microphone_input")
        call = m0.binary_protocol.last_call("microphone_input")
        assert call.data == FAKE_AUDIO
        assert call.meta["sample_rate"] == 16000

    def test_stt_audio_reaches_master(self, binary_skill_topology):
        """TS-BIN-02 — satellite sends STT_AUDIO_TRANSCRIBE; master receives it."""
        b, agent = binary_skill_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")
        m0.binary_protocol.clear()

        msg = HiveMessage(
            HiveMessageType.BINARY,
            payload=FAKE_AUDIO,
            bin_type=HiveMindBinaryPayloadType.STT_AUDIO_TRANSCRIBE,
            metadata={"lang": "en-US", "sample_rate": 16000, "sample_width": 2}
        )
        s0.send(msg)

        time.sleep(2)
        m0.binary_protocol.assert_called("stt_transcribe")
        call = m0.binary_protocol.last_call("stt_transcribe")
        assert call.meta["lang"] == "en-US"


@pytest.mark.skipif(skill_missing(SKILL_HELLO), reason="ovos-skill-hello-world not installed")
class TestBinaryThenSkill:
    """TS-BIN-03..04 — binary delivery followed by skill response."""

    def test_binary_then_utterance_produces_skill_response(self, binary_skill_topology):
        """TS-BIN-03 — after binary audio, inject utterance manually; skill responds."""
        b, agent = binary_skill_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        # Step 1: Send binary audio (simulates microphone capture)
        msg = HiveMessage(
            HiveMessageType.BINARY,
            payload=FAKE_AUDIO,
            bin_type=HiveMindBinaryPayloadType.RAW_AUDIO,
            metadata={"sample_rate": 16000, "sample_width": 2}
        )
        s0.send(msg)
        time.sleep(1)

        # Step 2: Send utterance (simulates STT result)
        cap = agent.new_capture()
        s0.send(make_utterance("hello world", ADAPT_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=15)

        speak = next((m for m in messages if m.msg_type == "speak"), None)
        assert speak is not None, (
            f"Skill did not respond after binary + utterance.\n"
            f"Captured: {[m.msg_type for m in messages]}"
        )

    def test_skill_response_routes_to_satellite_after_binary(self, binary_skill_topology):
        """TS-BIN-04 — speak from skill routes back to satellite that sent binary."""
        b, agent = binary_skill_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture()
        s0.send(make_utterance("hello world", ADAPT_PIPELINE, s0.shim.session_id))
        cap.wait(timeout=15)

        msg = wait_for_satellite_message(s0, "speak", timeout=10)
        assert msg is not None, "speak not routed to satellite"


@pytest.mark.skipif(skill_missing(SKILL_HELLO), reason="ovos-skill-hello-world not installed")
class TestBinaryMixedTraffic:
    """TS-BIN-05 — binary and BUS messages interleaved."""

    def test_mixed_binary_and_bus(self, binary_skill_topology):
        """TS-BIN-05 — interleaved binary + BUS messages don't interfere."""
        b, agent = binary_skill_topology
        agent.clear()
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")
        m0.binary_protocol.clear()

        # Send binary
        bin_msg = HiveMessage(
            HiveMessageType.BINARY,
            payload=FAKE_AUDIO,
            bin_type=HiveMindBinaryPayloadType.RAW_AUDIO,
            metadata={"sample_rate": 16000, "sample_width": 2}
        )
        s0.send(bin_msg)

        # Immediately send BUS utterance
        cap = agent.new_capture()
        s0.send(make_utterance("hello world", ADAPT_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=15)

        # Both should work
        m0.binary_protocol.assert_called("microphone_input")
        speak = next((m for m in messages if m.msg_type == "speak"), None)
        assert speak is not None, "Skill should respond despite interleaved binary"
