"""
OvoScope × HiveMind integration tests.

These tests wire a HiveMind topology (master + satellite) so that utterances
from the satellite flow through HiveMind routing into a live MiniCroft instance
(OVOS IntentService + optional skill plugins).  The skill-bus responses are
then verified both at the OVOS-bus level (OvoscopeAgentProtocol) and at the
HiveMind-satellite level (SatelliteNode.assert_received).

Prerequisites
-------------
* ``ovoscope`` installed: ``uv pip install ovoscope``   (pulls ovos-core)
* Run from the hivemind-test-harness directory or with it on sys.path.

Architecture
------------
The module-scoped ``ovoscope_topology`` fixture boots MiniCroft ONCE for all
tests in this file (MiniCroft startup takes ~2 minutes on first run).  Each
individual test calls ``agent.clear()`` to reset recorded messages, then sends
its utterance through the shared satellite connection.

Test IDs
--------
TS-OVO-01   Utterance reaches MiniCroft bus via HiveMind satellite
TS-OVO-02   complete_intent_failure emitted when no skills installed
TS-OVO-03   CaptureSession records the full OVOS message sequence
TS-OVO-04   speak message from skill propagates back to satellite   [PLANNED]
TS-OVO-05   Multi-utterance: second utterance reuses same session   [PLANNED]
TS-OVO-06   Session lang propagated from satellite context          [PLANNED]
TS-OVO-07   Skill activation state tracked correctly                [PLANNED]
TS-OVO-08   OvoscopeAgentProtocol.spoken_utterances() helper       [PLANNED]
TS-OVO-09   shared_bus satellite sees skill speak via propagation   [PLANNED]
TS-OVO-10   Relay topology: utterance escalates to OVOS master     [PLANNED]
"""
import pytest
from ovos_bus_client.message import Message
from ovos_bus_client.session import Session

from hivemind_test_harness.plugins.ovoscope_agent import OvoscopeAgentProtocol
from hivemind_test_harness.topology import TopologyBuilder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _skill_missing(skill_id: str) -> bool:
    """Return True if the skill plugin is not installed in the current env."""
    try:
        from ovos_plugin_manager.skills import find_skill_plugins
        return skill_id not in find_skill_plugins()
    except Exception:
        return True


def _make_utterance(text: str, lang: str = "en-us") -> Message:
    sess = Session("test-session")
    sess.lang = lang
    return Message(
        "recognizer_loop:utterance",
        {"utterances": [text], "lang": lang},
        {"session": sess.serialize(), "source": "sat", "destination": "master"},
    )


# ---------------------------------------------------------------------------
# Fixtures — module-scoped so MiniCroft boots exactly once
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def ovoscope_topology():
    """
    Module-scoped: start MiniCroft once, connect one satellite, run all tests,
    then teardown.  MiniCroft boot takes ~2 min on first run; subsequent calls
    (warm cache) are fast.
    """
    agent = OvoscopeAgentProtocol(skill_ids=[])
    b = TopologyBuilder()
    b.add_master("M0", agent_protocol=agent)
    b.add_satellite("S0", upstream=b.get_master("M0"))
    b.start_all()
    yield b, agent
    b.stop_all()
    agent.shutdown()


# ---------------------------------------------------------------------------
# TS-OVO-01  Utterance reaches MiniCroft bus via HiveMind
# ---------------------------------------------------------------------------

class TestUtteranceRoutingToMiniCroft:
    """TS-OVO-01 — satellite utterance is injected into the OVOS bus."""

    def test_utterance_emitted_on_ovos_bus(self, ovoscope_topology):
        b, agent = ovoscope_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        s0.send(_make_utterance("hello"))

        agent.wait_for_skill_emission("recognizer_loop:utterance")

    def test_utterance_text_preserved(self, ovoscope_topology):
        b, agent = ovoscope_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        s0.send(_make_utterance("what is the capital of France?"))

        msg = agent.wait_last_injected("recognizer_loop:utterance")
        assert msg is not None, "recognizer_loop:utterance was not emitted on agent bus"
        # OVOS utterance normalizer strips trailing punctuation (e.g. "?" → ""),
        # which is expected production behaviour.  Check key content is preserved.
        utterances = msg.data.get("utterances", [])
        assert any("capital" in u and "France" in u for u in utterances), (
            f"Expected utterance containing 'capital' and 'France', got: {utterances}"
        )

    def test_utterance_handled_event_fired(self, ovoscope_topology):
        b, agent = ovoscope_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        s0.send(_make_utterance("ping"))

        # ovos.utterance.handled is the EOF marker — always emitted
        agent.wait_for_skill_emission("ovos.utterance.handled")


# ---------------------------------------------------------------------------
# TS-OVO-02  complete_intent_failure when no skills installed
# ---------------------------------------------------------------------------

class TestCompleteIntentFailure:
    """TS-OVO-02 — with no skills, every utterance triggers intent failure."""

    def test_complete_intent_failure_emitted(self, ovoscope_topology):
        b, agent = ovoscope_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        s0.send(_make_utterance("unrecognised utterance xyz"))

        agent.wait_for_skill_emission("complete_intent_failure")

    def test_no_speak_on_intent_failure(self, ovoscope_topology):
        b, agent = ovoscope_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        s0.send(_make_utterance("unrecognised again"))

        agent.wait_for_skill_emission("ovos.utterance.handled")  # ensure processing done
        agent.assert_skill_not_emitted("speak")


# ---------------------------------------------------------------------------
# TS-OVO-03  CaptureSession records the full OVOS message sequence
# ---------------------------------------------------------------------------

class TestCaptureSession:
    """TS-OVO-03 — CaptureSession gives ordered list of all OVOS bus messages."""

    def test_capture_contains_utterance(self, ovoscope_topology):
        b, agent = ovoscope_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture()
        s0.send(_make_utterance("test capture"))
        messages = cap.wait(timeout=10)

        types = [m.msg_type for m in messages]
        assert "recognizer_loop:utterance" in types

    def test_capture_ends_with_handled(self, ovoscope_topology):
        b, agent = ovoscope_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture()
        s0.send(_make_utterance("another capture test"))
        messages = cap.wait(timeout=10)

        types = [m.msg_type for m in messages]
        assert "ovos.utterance.handled" in types

    def test_capture_message_order(self, ovoscope_topology):
        """utterance must arrive before handled."""
        b, agent = ovoscope_topology
        agent.clear()
        s0 = b.get_satellite("S0")

        cap = agent.new_capture()
        s0.send(_make_utterance("order check"))
        messages = cap.wait(timeout=10)

        types = [m.msg_type for m in messages]
        utt_idx = next((i for i, t in enumerate(types)
                        if t == "recognizer_loop:utterance"), None)
        handled_idx = next((i for i, t in enumerate(types)
                            if t == "ovos.utterance.handled"), None)
        assert utt_idx is not None
        assert handled_idx is not None
        assert utt_idx < handled_idx


# ---------------------------------------------------------------------------
# PLANNED TESTS  (TS-OVO-04 through TS-OVO-10)
# Each is a concrete specification for future implementation.
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="PLANNED TS-OVO-04: requires a skill that emits speak")
class TestSpeakPropagatesBackToSatellite:
    """
    TS-OVO-04 — speak emitted by a skill is routed by HiveMind back to the
    originating satellite.

    Implementation notes
    --------------------
    * Load a skill that responds to a known utterance with a ``speak`` message
      (e.g. skill-ovos-hello-world.openvoiceos).
    * Send the utterance from the satellite.
    * Assert:
        - agent.wait_for_skill_emission("speak")
        - s0.assert_received("speak", timeout=5)          ← HiveMind roundtrip
        - agent.spoken_utterances()[0] matches expected text
    * The key assertion is ``s0.assert_received("speak")`` — this proves
      HiveMind routed the bus event back over the wire to the satellite.

    Skill needed: skill-ovos-hello-world.openvoiceos
    """

    @pytest.mark.skipif(
        _skill_missing("skill-ovos-hello-world.openvoiceos"),
        reason="skill-ovos-hello-world not installed"
    )
    def test_speak_received_by_satellite(self):
        agent = OvoscopeAgentProtocol(
            skill_ids=["skill-ovos-hello-world.openvoiceos"]
        )
        b = TopologyBuilder()
        b.add_master("M0", agent_protocol=agent)
        b.add_satellite("S0", upstream=b.get_master("M0"))
        b.start_all()
        try:
            s0 = b.get_satellite("S0")
            s0.send(_make_utterance("hello"))
            agent.wait_for_skill_emission("speak")
            s0.assert_received("speak", timeout=5)
        finally:
            b.stop_all()
            agent.shutdown()


@pytest.mark.skip(reason="PLANNED TS-OVO-05: requires session continuity logic")
class TestMultiUtteranceSessionContinuity:
    """
    TS-OVO-05 — second utterance reuses session state from first reply.

    OVOS propagates session updates in message.context; the satellite should
    forward the updated session on subsequent utterances.  This mirrors
    OvoScope's End2EndTest multi-message behaviour (source_message=[msg1, msg2]).

    Implementation notes
    --------------------
    * Send utterance 1 from satellite, collect reply, extract session from reply.
    * Send utterance 2 with the updated session in context.
    * Assert session_id is the same across both exchanges.
    * Demonstrates HiveMind preserves OVOS session continuity end-to-end.
    """
    pass


@pytest.mark.skip(reason="PLANNED TS-OVO-06: requires lang in session context")
class TestSessionLangPropagated:
    """
    TS-OVO-06 — satellite lang context reaches IntentService unchanged.

    Send an utterance with ``session.lang = "de-de"``; assert that
    ``recognizer_loop:utterance`` arriving on the OVOS bus carries the same lang.
    This validates that HiveMind doesn't strip or overwrite session data.

    Implementation notes
    --------------------
    * Use ``_make_utterance("hallo welt", lang="de-de")``.
    * Call ``agent.wait_last_injected("recognizer_loop:utterance")``.
    * Assert ``msg.data["lang"] == "de-de"``.
    """
    pass


@pytest.mark.skip(reason="PLANNED TS-OVO-07: requires skill with activation")
class TestSkillActivationState:
    """
    TS-OVO-07 — skill activation state tracked correctly across an exchange.

    OvoScope exposes ``End2EndTest.activation_points`` / ``deactivation_points``.
    The harness integration should let you assert that a converse-capable skill
    activates after matching an intent and deactivates after the exchange ends.

    Implementation notes
    --------------------
    * Load a skill with a ``converse()`` handler.
    * Send an intent-matched utterance.
    * Assert skill is active (in session.active_skills).
    * Send an unrelated utterance.
    * Assert skill is deactivated.
    """
    pass


@pytest.mark.skip(reason="PLANNED TS-OVO-08: spoken_utterances() helper")
class TestSpokenUtterancesHelper:
    """
    TS-OVO-08 — OvoscopeAgentProtocol.spoken_utterances() returns all TTS text.

    With a skill that produces multiple speak messages in one exchange,
    spoken_utterances() should return them in emission order.

    Implementation notes
    --------------------
    * Create a skill stub that emits two ``speak`` messages.
    * Assert: spoken_utterances() == ["first line", "second line"]
    * This tests the helper method independent of HiveMind routing.
    """
    pass


@pytest.mark.skip(reason="PLANNED TS-OVO-09: shared_bus satellite sees speak")
class TestSharedBusPropagation:
    """
    TS-OVO-09 — a SHARED_BUS satellite passively receives speak events.

    Topology: M0 (MiniCroft) ← S0 (primary, sends utterance) + S1 (shared_bus).
    When a skill on M0 emits speak, S1 should receive it via SHARED_BUS forwarding.

    Implementation notes
    --------------------
    * Use ``b.add_satellite("S1", ..., shared_bus=True)``.
    * Send utterance from S0.
    * Assert S1.assert_received("speak", timeout=5).
    * This validates HiveMind's SHARED_BUS broadcast interacts correctly with
      real skill responses on the OVOS bus.
    """
    pass


@pytest.mark.skip(reason="PLANNED TS-OVO-10: relay topology escalation")
class TestRelayTopologyEscalation:
    """
    TS-OVO-10 — utterance from downstream satellite escalates to OVOS master.

    Topology: S_down → relay (sat+master) → M0 (MiniCroft)
    An utterance from S_down should escalate through the relay to M0's MiniCroft.

    Implementation notes
    --------------------
    * Use ``b.add_relay()``.
    * Send utterance from S_down.
    * Assert M0's OvoscopeAgentProtocol recorded ``recognizer_loop:utterance``.
    * Assert ``complete_intent_failure`` is emitted (no skills loaded).
    * This validates the ESCALATE chain works with a real OVOS bus at the top.
    """
    pass
