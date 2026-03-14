"""
OvoscopeAgentProtocol — AgentProtocol backed by a live MiniCroft instance.

Replaces the bare FakeBus in TestAgentProtocol with a MiniCroft that runs
real OVOS IntentService and skill plugins.  Every utterance injected by
HiveMind is processed by the full OVOS intent pipeline; all bus messages
produced by skills are recorded and available for assertion.

Usage
-----
    from hivemind_test_harness.plugins.ovoscope_agent import OvoscopeAgentProtocol

    agent = OvoscopeAgentProtocol(skill_ids=["skill-ovos-hello-world.openvoiceos"])
    b = TopologyBuilder()
    b.add_master("M0", agent_protocol=agent)
    b.add_satellite("S0", upstream=b.get_master("M0"))
    b.start_all()

    s0 = b.get_satellite("S0")
    s0.send(Message("recognizer_loop:utterance", {"utterances": ["hello"]}))
    s0.assert_received("speak", timeout=5)

    agent.assert_skill_emitted("speak")
    agent.assert_skill_emitted("ovos.utterance.handled")

    b.stop_all()
    agent.shutdown()

Integration with CaptureSession
--------------------------------
    from ovoscope import CaptureSession

    cap = agent.new_capture()
    s0.send(Message("recognizer_loop:utterance", {"utterances": ["hello"]}))
    messages = cap.wait()                  # blocks until eof_msg or timeout
    assert any(m.msg_type == "speak" for m in messages)
"""
from __future__ import annotations

import threading
from copy import deepcopy
from dataclasses import dataclass, field
from typing import List, Optional, Union

from ovos_bus_client.message import Message
from ovos_utils.log import LOG

from hivemind_test_harness.plugins.agent import TestAgentProtocol


# ---------------------------------------------------------------------------
# Lazy import guard — ovoscope / ovos_core are optional
# ---------------------------------------------------------------------------
def _require_ovoscope():
    try:
        from ovoscope import MiniCroft, get_minicroft, CaptureSession
        return MiniCroft, get_minicroft, CaptureSession
    except ImportError as e:
        raise ImportError(
            "ovoscope is not installed. "
            "Run: uv pip install ovoscope (requires ovos-core)"
        ) from e


# ---------------------------------------------------------------------------
# Thin CaptureSession wrapper that works without a source_message emit
# ---------------------------------------------------------------------------
class _HarnessCaptureSession:
    """
    Wraps ovoscope.CaptureSession so the harness can start/stop a capture
    independently of emitting a message (HiveMind does the emit via satellite).
    """

    def __init__(self, minicroft, eof_msgs=None, ignore_messages=None):
        _, _, CaptureSession = _require_ovoscope()
        from ovoscope import DEFAULT_EOF, DEFAULT_IGNORED
        self._cap = CaptureSession(
            minicroft=minicroft,
            eof_msgs=eof_msgs or DEFAULT_EOF,
            ignore_messages=ignore_messages or DEFAULT_IGNORED,
        )

    def wait(self, timeout: float = 10.0) -> List[Message]:
        """Block until EOF message received or timeout, then return messages."""
        self._cap.done.wait(timeout=timeout)
        return self._cap.finish()

    def messages(self) -> List[Message]:
        """Return messages collected so far without stopping the capture."""
        return list(self._cap.responses)


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------
@dataclass
class OvoscopeAgentProtocol(TestAgentProtocol):
    """
    AgentProtocol that wires HiveMind's agent bus to a live MiniCroft instance.

    Parameters
    ----------
    skill_ids : list[str]
        Entry-point names of OVOS skill plugins to load.  Pass [] to run with
        IntentService only (no skills — useful for testing complete_intent_failure).
    extra_skills : dict, optional
        Map of skill_id → OVOSSkill instance for programmatically injected skills.
    minicroft : MiniCroft, optional
        Bring your own pre-started MiniCroft.  If provided, ``skill_ids`` and
        ``extra_skills`` are ignored.
    """
    skill_ids: List[str] = field(default_factory=list)
    extra_skills: Optional[dict] = field(default_factory=dict)
    minicroft: Optional[object] = field(default=None)   # MiniCroft | None

    def __post_init__(self):
        MiniCroft, get_minicroft, _ = _require_ovoscope()

        if self.minicroft is None:
            LOG.debug(
                f"OvoscopeAgentProtocol: starting MiniCroft "
                f"with skills={self.skill_ids}"
            )
            self.minicroft = get_minicroft(
                self.skill_ids,
                extra_skills=self.extra_skills or {},
            )

        # Point the agent bus at MiniCroft's FakeBus
        self.bus = self.minicroft.bus

        # Let TestAgentProtocol install its recording wrapper on top
        super().__post_init__()

    # ------------------------------------------------------------------
    # Assertion helpers — skill-bus level
    # ------------------------------------------------------------------

    def assert_skill_emitted(self, msg_type: str, count: int = 1):
        """
        Assert that the OVOS skill bus emitted ``msg_type`` exactly ``count`` times.
        Equivalent to TestAgentProtocol.assert_injected but named to distinguish
        "skill bus" from "HiveMind inject" semantics.
        """
        self.assert_injected(msg_type, count=count)

    def assert_skill_not_emitted(self, msg_type: str):
        """Assert that the OVOS skill bus never emitted ``msg_type``."""
        self.assert_not_injected(msg_type)

    def skill_messages(self, msg_type: str) -> List[Message]:
        """Return all skill-bus messages of a given type."""
        return [m for m in self.injected if m.msg_type == msg_type]

    def last_speak(self) -> Optional[Message]:
        """Convenience: return the last ``speak`` message emitted by a skill."""
        return self.last_injected("speak")

    def spoken_utterances(self) -> List[str]:
        """Return all utterances from ``speak`` messages, in order."""
        return [
            m.data.get("utterance", "")
            for m in self.injected
            if m.msg_type == "speak"
        ]

    # ------------------------------------------------------------------
    # Timed assertion helpers — needed because MiniCroft's IntentService
    # processes messages in background threads
    # ------------------------------------------------------------------

    def wait_for_skill_emission(
        self,
        msg_type: str,
        count: int = 1,
        timeout: float = 10.0,
    ):
        """
        Poll until ``msg_type`` has been emitted ``count`` times (or timeout).
        Raises AssertionError if the count is not reached within ``timeout`` seconds.
        """
        import time
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            matches = [m for m in self.injected if m.msg_type == msg_type]
            if len(matches) >= count:
                return
            time.sleep(0.05)
        # Final check with proper error message
        self.assert_injected(msg_type, count=count)

    def wait_last_injected(
        self,
        msg_type: str,
        timeout: float = 10.0,
    ):
        """
        Poll until at least one ``msg_type`` message is in injected, then return it.
        Returns None if timeout is reached.
        """
        import time
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = self.last_injected(msg_type)
            if msg is not None:
                return msg
            time.sleep(0.05)
        return self.last_injected(msg_type)

    # ------------------------------------------------------------------
    # CaptureSession integration
    # ------------------------------------------------------------------

    def new_capture(
        self,
        eof_msgs: Optional[List[str]] = None,
        ignore_messages: Optional[List[str]] = None,
    ) -> _HarnessCaptureSession:
        """
        Open a new OvoScope CaptureSession on MiniCroft's bus.

        The satellite sends the utterance via HiveMind as normal; this capture
        session records every bus message produced on the skill side until the
        EOF message arrives (default: ``ovos.utterance.handled``).

        Example::

            cap = agent.new_capture()
            s0.send(Message("recognizer_loop:utterance",
                            {"utterances": ["what time is it?"]}))
            messages = cap.wait(timeout=10)
            assert any(m.msg_type == "speak" for m in messages)
        """
        return _HarnessCaptureSession(
            minicroft=self.minicroft,
            eof_msgs=eof_msgs,
            ignore_messages=ignore_messages,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def shutdown(self):
        """Stop MiniCroft and release resources."""
        if self.minicroft is not None:
            try:
                self.minicroft.stop()
            except Exception as e:
                LOG.warning(f"OvoscopeAgentProtocol.shutdown(): {e}")
            self.minicroft = None
