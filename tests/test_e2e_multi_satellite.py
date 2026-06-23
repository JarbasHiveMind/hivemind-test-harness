"""
Multi-satellite E2E tests — star topology with response isolation.

Verifies that skill responses route only to the originating satellite,
not to all connected satellites.

Prerequisites
-------------
* ovos-skill-hello-world installed
* ovoscope installed

Test IDs
--------
TS-MS-01 through TS-MS-03
"""
import threading
import time

import pytest
from ovos_bus_client.message import Message

from hivescope.plugins.ovoscope_agent import OvoscopeAgentProtocol
from hivescope.topology import TopologyBuilder
from tests.conftest import (
    SKILL_HELLO, SKILL_VOLUME,
    skill_missing, make_utterance, wait_for_satellite_message,
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
def star_skill_topology():
    """Star: M0(MiniCroft) with S0, S1, S2."""
    agent = OvoscopeAgentProtocol(skill_ids=[SKILL_HELLO, SKILL_VOLUME])

    # Install volume.get responder
    agent.bus.on("mycroft.volume.get",
                 lambda m: agent.bus.emit(m.response({"percent": 0.5, "muted": False})))

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
    b.add_satellite("S1", upstream=b.get_master("M0"))
    b.add_satellite("S2", upstream=b.get_master("M0"))
    b.start_all()
    yield b, agent
    b.stop_all()
    agent.shutdown()


@pytest.mark.skipif(skill_missing(SKILL_HELLO), reason="ovos-skill-hello-world not installed")
class TestResponseIsolation:
    """TS-MS-01 — responses route only to originator."""

    def test_response_routed_to_originator_only(self, star_skill_topology):
        """TS-MS-01 — S0 sends utterance; only S0 gets speak, not S1/S2."""
        b, agent = star_skill_topology
        agent.clear()
        s0 = b.get_satellite("S0")
        s1 = b.get_satellite("S1")
        s2 = b.get_satellite("S2")

        # Register listeners on all satellites
        s0_speak = []
        s1_speak = []
        s2_speak = []
        s0_evt = threading.Event()

        s0.internal_bus.once("speak", lambda m: (s0_speak.append(m), s0_evt.set()))
        s1.internal_bus.once("speak", lambda m: s1_speak.append(m))
        s2.internal_bus.once("speak", lambda m: s2_speak.append(m))

        cap = agent.new_capture()
        s0.send(make_utterance("hello world", ADAPT_PIPELINE, s0.shim.session_id))
        cap.wait(timeout=15)

        s0_evt.wait(timeout=10)
        assert s0_speak, "S0 did not receive speak"

        # Give S1/S2 a moment to (not) receive
        time.sleep(1)
        assert not s1_speak, "S1 incorrectly received speak"
        assert not s2_speak, "S2 incorrectly received speak"


@pytest.mark.skipif(skill_missing(SKILL_HELLO), reason="ovos-skill-hello-world not installed")
class TestConcurrentUtterances:
    """TS-MS-02 — concurrent utterances from different satellites."""

    def test_concurrent_utterances(self, star_skill_topology):
        """TS-MS-02 — S0 and S1 send different utterances; each gets response."""
        b, agent = star_skill_topology
        agent.clear()
        s0 = b.get_satellite("S0")
        s1 = b.get_satellite("S1")

        s0_speak = []
        s1_speak = []
        s0_evt = threading.Event()
        s1_evt = threading.Event()

        s0.internal_bus.once("speak", lambda m: (s0_speak.append(m), s0_evt.set()))
        s1.internal_bus.once("speak", lambda m: (s1_speak.append(m), s1_evt.set()))

        # Send from both satellites
        s0.send(make_utterance("hello world", ADAPT_PIPELINE, s0.shim.session_id))
        s1.send(make_utterance("hello world", ADAPT_PIPELINE, s1.shim.session_id))

        s0_evt.wait(timeout=15)
        s1_evt.wait(timeout=15)

        assert s0_speak, "S0 did not receive speak"
        assert s1_speak, "S1 did not receive speak"


@pytest.mark.skipif(skill_missing(SKILL_HELLO, SKILL_VOLUME),
                     reason="required skills not installed")
class TestVolumeIsolation:
    """TS-MS-03 — volume messages isolated per satellite."""

    def test_volume_isolated_per_satellite(self, star_skill_topology):
        """TS-MS-03 — S0 sends 'mute'; only S0 gets mycroft.volume.mute."""
        b, agent = star_skill_topology
        agent.clear()
        s0 = b.get_satellite("S0")
        s1 = b.get_satellite("S1")

        s0_mute = []
        s1_mute = []
        s0_evt = threading.Event()

        s0.internal_bus.once("mycroft.volume.mute",
                              lambda m: (s0_mute.append(m), s0_evt.set()))
        s1.internal_bus.once("mycroft.volume.mute",
                              lambda m: s1_mute.append(m))

        cap = agent.new_capture()
        s0.send(make_utterance("mute", DEFAULT_PIPELINE, s0.shim.session_id))
        cap.wait(timeout=15)

        s0_evt.wait(timeout=10)
        assert s0_mute, "S0 did not receive mycroft.volume.mute"

        time.sleep(1)
        assert not s1_mute, "S1 incorrectly received mycroft.volume.mute"
