"""
Volume skill E2E tests with satellite-side mock PHAL.

The volume *skill* runs on the hub (MiniCroft). It emits mycroft.volume.*
messages on the bus. HiveMind routes these back to the originating satellite.
A MockVolumePHAL on the satellite's internal_bus captures them, proving the
full satellite → hub → skill → satellite round-trip for hardware control.

Prerequisites
-------------
* ovos-skill-volume installed
* ovoscope installed

Test IDs
--------
TS-VP-01 through TS-VP-13
"""
import threading
import time
from typing import List, Optional

import pytest
from ovos_bus_client.message import Message
from ovos_spec_tools import SpecMessage

from hivescope.topology import TopologyBuilder
from tests.conftest import (
    open_capture,
    make_ovoscope_agent,
    VOICE_TYPES,
    SKILL_VOLUME, skill_missing, make_utterance, assert_types_in_order,
    wait_for_satellite_message,
)

# MiniCroft boot alone can take up to MINICROFT_READY_TIMEOUT (180s), and skill
# handlers run serially after that, so the repo-wide 30s default is far too
# tight for this module.
pytestmark = pytest.mark.timeout(300)

DEFAULT_PIPELINE = [
    "ovos-adapt-pipeline-plugin-high",
    "ovos-padatious-pipeline-plugin-high",
    "ovos-adapt-pipeline-plugin-medium",
    "ovos-padatious-pipeline-plugin-medium",
    "ovos-adapt-pipeline-plugin-low",
    "ovos-padatious-pipeline-plugin-low",
]


class MockVolumePHAL:
    """Records mycroft.volume.* messages arriving on a satellite's internal_bus.

    Simulates what a real PHAL volume plugin (e.g. ovos-PHAL-plugin-alsa) would
    do: listen for volume control messages on the local bus.
    """

    VOLUME_EVENTS = [
        "mycroft.volume.set",
        "mycroft.volume.increase",
        "mycroft.volume.decrease",
        "mycroft.volume.mute",
        "mycroft.volume.unmute",
        "mycroft.volume.mute.toggle",
        "mycroft.volume.get",
    ]

    def __init__(self, bus) -> None:
        self.bus = bus
        self.received: List[Message] = []
        self._lock = threading.Lock()
        for evt in self.VOLUME_EVENTS:
            bus.on(evt, self._on_volume)

    def _on_volume(self, msg: Message) -> None:
        with self._lock:
            self.received.append(msg)

    def wait_for(self, msg_type: str, timeout: float = 10.0) -> Optional[Message]:
        """Poll until a message of msg_type is received or timeout."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                match = next((m for m in self.received if m.msg_type == msg_type), None)
            if match is not None:
                return match
            time.sleep(0.05)
        return None

    def clear(self) -> None:
        """Reset recorded messages."""
        with self._lock:
            self.received.clear()

    def received_types(self) -> List[str]:
        """Return list of received message types."""
        with self._lock:
            return [m.msg_type for m in self.received]


def _install_volume_responder(bus) -> None:
    """Register a mock mycroft.volume.get responder on the hub's MiniCroft bus.

    The volume skill's _query_volume() calls bus.wait_for_response() for
    mycroft.volume.get. Without this responder, increase/decrease intents
    will timeout.
    """
    def _respond(msg):
        bus.emit(msg.response({"percent": 0.5, "muted": False}))

    bus.on("mycroft.volume.get", _respond)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def volume_topology():
    """Boot MiniCroft with volume skill, connect one satellite with MockVolumePHAL."""
    agent = make_ovoscope_agent(skill_ids=[SKILL_VOLUME])

    # Install volume.get responder on hub bus (skill calls _query_volume)
    _install_volume_responder(agent.bus)

    b = TopologyBuilder()
    try:
        # Wait for volume skill intents to register
        _deadline = time.monotonic() + 120
        while time.monotonic() < _deadline:
            # change_volume is an adapt intent that should be registered
            if len(agent.bus.ee.listeners(f"{SKILL_VOLUME}:change_volume")) > 0:
                break
            time.sleep(0.5)
        else:
            # MiniCroft is already booted — the finally block below shuts it
            # down, so the skip does not leak the agent.
            pytest.skip("Volume skill intents not registered within 120s")

        b.add_master("M0", agent_protocol=agent)
        b.add_satellite("S0", upstream=b.get_master("M0"),
                         allowed_types=VOICE_TYPES)
        b.start_all()

        s0 = b.get_satellite("S0")
        mock_phal = MockVolumePHAL(s0.internal_bus)

        yield b, agent, mock_phal
    finally:
        b.stop_all()
        agent.shutdown()


# ---------------------------------------------------------------------------
# TS-VP-01  max volume — hub bus
# ---------------------------------------------------------------------------

@pytest.mark.skipif(skill_missing(SKILL_VOLUME), reason="ovos-skill-volume not installed")
class TestMaxVolume:
    """TS-VP-01..02 — 'maximum volume' sets volume to 1.0."""

    def test_max_volume_on_hub_bus(self, volume_topology):
        """TS-VP-01 — mycroft.volume.set with percent=1.0 emitted on hub bus."""
        b, agent, mock_phal = volume_topology
        agent.clear()
        mock_phal.clear()
        s0 = b.get_satellite("S0")

        cap = open_capture(agent)
        s0.send(make_utterance("maximum volume", DEFAULT_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=15)

        vol_set = next(
            (m for m in messages if m.msg_type == "mycroft.volume.set"), None
        )
        assert vol_set is not None, (
            f"mycroft.volume.set not emitted on hub bus.\n"
            f"Captured: {[m.msg_type for m in messages]}"
        )
        assert vol_set.data.get("percent") == 1.0

    def test_max_volume_reaches_satellite(self, volume_topology):
        """TS-VP-02 — mycroft.volume.set arrives on satellite's internal_bus."""
        b, agent, mock_phal = volume_topology
        agent.clear()
        mock_phal.clear()
        s0 = b.get_satellite("S0")

        cap = open_capture(agent)
        s0.send(make_utterance("maximum volume", DEFAULT_PIPELINE, s0.shim.session_id))
        cap.wait(timeout=15)

        msg = mock_phal.wait_for("mycroft.volume.set", timeout=10)
        assert msg is not None, (
            f"mycroft.volume.set not received on satellite bus.\n"
            f"MockPHAL received: {mock_phal.received_types()}"
        )
        assert msg.data.get("percent") == 1.0


# ---------------------------------------------------------------------------
# TS-VP-03..04  mute / unmute
# ---------------------------------------------------------------------------

@pytest.mark.skipif(skill_missing(SKILL_VOLUME), reason="ovos-skill-volume not installed")
class TestMuteUnmute:
    """TS-VP-03..04 — mute/unmute via HiveMind."""

    def test_mute(self, volume_topology):
        """TS-VP-03 — 'mute' sends mycroft.volume.mute to satellite."""
        b, agent, mock_phal = volume_topology
        agent.clear()
        mock_phal.clear()
        s0 = b.get_satellite("S0")

        cap = open_capture(agent)
        s0.send(make_utterance("mute", DEFAULT_PIPELINE, s0.shim.session_id))
        cap.wait(timeout=15)

        msg = mock_phal.wait_for("mycroft.volume.mute", timeout=10)
        assert msg is not None, (
            f"mycroft.volume.mute not on satellite bus.\n"
            f"MockPHAL received: {mock_phal.received_types()}"
        )

    def test_unmute(self, volume_topology):
        """TS-VP-04 — 'unmute' sends mycroft.volume.unmute to satellite."""
        b, agent, mock_phal = volume_topology
        agent.clear()
        mock_phal.clear()
        s0 = b.get_satellite("S0")

        cap = open_capture(agent)
        s0.send(make_utterance("unmute", DEFAULT_PIPELINE, s0.shim.session_id))
        cap.wait(timeout=15)

        msg = mock_phal.wait_for("mycroft.volume.unmute", timeout=10)
        assert msg is not None, (
            f"mycroft.volume.unmute not on satellite bus.\n"
            f"MockPHAL received: {mock_phal.received_types()}"
        )


# ---------------------------------------------------------------------------
# TS-VP-05..06  increase / decrease
# ---------------------------------------------------------------------------

@pytest.mark.skipif(skill_missing(SKILL_VOLUME), reason="ovos-skill-volume not installed")
class TestIncreaseDecrease:
    """TS-VP-05..06 — increase/decrease volume via HiveMind."""

    def test_increase_volume(self, volume_topology):
        """TS-VP-05 — 'increase the volume' sends mycroft.volume.increase."""
        b, agent, mock_phal = volume_topology
        agent.clear()
        mock_phal.clear()
        s0 = b.get_satellite("S0")

        cap = open_capture(agent)
        s0.send(make_utterance("increase the volume", DEFAULT_PIPELINE, s0.shim.session_id))
        cap.wait(timeout=15)

        msg = mock_phal.wait_for("mycroft.volume.increase", timeout=10)
        assert msg is not None, (
            f"mycroft.volume.increase not on satellite bus.\n"
            f"MockPHAL received: {mock_phal.received_types()}"
        )

    def test_decrease_volume(self, volume_topology):
        """TS-VP-06 — 'decrease the volume' sends mycroft.volume.decrease."""
        b, agent, mock_phal = volume_topology
        agent.clear()
        mock_phal.clear()
        s0 = b.get_satellite("S0")

        cap = open_capture(agent)
        s0.send(make_utterance("decrease the volume", DEFAULT_PIPELINE, s0.shim.session_id))
        cap.wait(timeout=15)

        msg = mock_phal.wait_for("mycroft.volume.decrease", timeout=10)
        assert msg is not None, (
            f"mycroft.volume.decrease not on satellite bus.\n"
            f"MockPHAL received: {mock_phal.received_types()}"
        )


# ---------------------------------------------------------------------------
# TS-VP-07..09  preset volumes
# ---------------------------------------------------------------------------

@pytest.mark.skipif(skill_missing(SKILL_VOLUME), reason="ovos-skill-volume not installed")
class TestPresetVolumes:
    """TS-VP-07..09 — preset volume levels via HiveMind."""

    def test_default_volume(self, volume_topology):
        """TS-VP-07 — 'default volume' sets percent=0.7."""
        b, agent, mock_phal = volume_topology
        agent.clear()
        mock_phal.clear()
        s0 = b.get_satellite("S0")

        cap = open_capture(agent)
        s0.send(make_utterance("default volume", DEFAULT_PIPELINE, s0.shim.session_id))
        cap.wait(timeout=15)

        msg = mock_phal.wait_for("mycroft.volume.set", timeout=10)
        assert msg is not None, f"mycroft.volume.set not on satellite bus."
        assert msg.data.get("percent") == 0.7

    def test_low_volume(self, volume_topology):
        """TS-VP-08 — 'low volume' sets percent=0.3."""
        b, agent, mock_phal = volume_topology
        agent.clear()
        mock_phal.clear()
        s0 = b.get_satellite("S0")

        cap = open_capture(agent)
        s0.send(make_utterance("low volume", DEFAULT_PIPELINE, s0.shim.session_id))
        cap.wait(timeout=15)

        msg = mock_phal.wait_for("mycroft.volume.set", timeout=10)
        assert msg is not None, f"mycroft.volume.set not on satellite bus."
        assert msg.data.get("percent") == 0.3

    def test_high_volume(self, volume_topology):
        """TS-VP-09 — 'high volume' sets percent=0.9."""
        b, agent, mock_phal = volume_topology
        agent.clear()
        mock_phal.clear()
        s0 = b.get_satellite("S0")

        cap = open_capture(agent)
        s0.send(make_utterance("high volume", DEFAULT_PIPELINE, s0.shim.session_id))
        cap.wait(timeout=15)

        msg = mock_phal.wait_for("mycroft.volume.set", timeout=10)
        assert msg is not None, f"mycroft.volume.set not on satellite bus."
        assert msg.data.get("percent") == 0.9


# ---------------------------------------------------------------------------
# TS-VP-10  mute toggle
# ---------------------------------------------------------------------------

@pytest.mark.skipif(skill_missing(SKILL_VOLUME), reason="ovos-skill-volume not installed")
class TestMuteToggle:
    """TS-VP-10 — mute toggle via HiveMind."""

    def test_mute_toggle(self, volume_topology):
        """TS-VP-10 — 'toggle mute' sends mycroft.volume.mute.toggle."""
        b, agent, mock_phal = volume_topology
        agent.clear()
        mock_phal.clear()
        s0 = b.get_satellite("S0")

        cap = open_capture(agent)
        s0.send(make_utterance("toggle mute", DEFAULT_PIPELINE, s0.shim.session_id))
        cap.wait(timeout=15)

        msg = mock_phal.wait_for("mycroft.volume.mute.toggle", timeout=10)
        assert msg is not None, (
            f"mycroft.volume.mute.toggle not on satellite bus.\n"
            f"MockPHAL received: {mock_phal.received_types()}"
        )


# ---------------------------------------------------------------------------
# TS-VP-11  query volume
# ---------------------------------------------------------------------------

@pytest.mark.skipif(skill_missing(SKILL_VOLUME), reason="ovos-skill-volume not installed")
class TestQueryVolume:
    """TS-VP-11 — query current volume via HiveMind."""

    def test_query_volume(self, volume_topology):
        """TS-VP-11 — 'what is the volume' triggers speak with level."""
        b, agent, mock_phal = volume_topology
        agent.clear()
        mock_phal.clear()
        s0 = b.get_satellite("S0")

        cap = open_capture(agent)
        s0.send(make_utterance("what is the volume", DEFAULT_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=15)

        speak = next((m for m in messages if m.msg_type == SpecMessage.SPEAK), None)
        assert speak is not None, (
            f"'speak' not emitted for volume query.\n"
            f"Captured: {[m.msg_type for m in messages]}"
        )


# ---------------------------------------------------------------------------
# TS-VP-12  speak also routes back
# ---------------------------------------------------------------------------

@pytest.mark.skipif(skill_missing(SKILL_VOLUME), reason="ovos-skill-volume not installed")
class TestVolumeSpeakRoutes:
    """TS-VP-12 — volume skill's speak also arrives on satellite."""

    def test_volume_speak_also_routes(self, volume_topology):
        """TS-VP-12 — speak from volume skill arrives on satellite bus."""
        b, agent, mock_phal = volume_topology
        agent.clear()
        mock_phal.clear()
        s0 = b.get_satellite("S0")

        cap = open_capture(agent)
        s0.send(make_utterance("maximum volume", DEFAULT_PIPELINE, s0.shim.session_id))
        cap.wait(timeout=15)

        msg = wait_for_satellite_message(s0, SpecMessage.SPEAK, timeout=10)
        assert msg is not None, "speak from volume skill not forwarded to satellite"


# ---------------------------------------------------------------------------
# TS-VP-13  full roundtrip sequence
# ---------------------------------------------------------------------------

@pytest.mark.skipif(skill_missing(SKILL_VOLUME), reason="ovos-skill-volume not installed")
class TestVolumeFullSequence:
    """TS-VP-13 — full message sequence for volume increase."""

    def test_full_roundtrip_sequence(self, volume_topology):
        """TS-VP-13 — message order for 'increase the volume'."""
        b, agent, mock_phal = volume_topology
        agent.clear()
        mock_phal.clear()
        s0 = b.get_satellite("S0")

        cap = open_capture(agent)
        s0.send(make_utterance("increase the volume", DEFAULT_PIPELINE, s0.shim.session_id))
        messages = cap.wait(timeout=15)

        assert_types_in_order(
            messages,
            "recognizer_loop:utterance",
            "mycroft.skill.handler.start",
            "mycroft.volume.increase",
            SpecMessage.SPEAK,
            "mycroft.skill.handler.complete",
            "ovos.utterance.handled",
        )
