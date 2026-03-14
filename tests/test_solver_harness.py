"""
Tests for HiveMindSolver against an in-process HiveMind topology.

These tests wire HiveMindSolver to a mock HiveMessageBusClient shim
backed by a minimal harness topology, verifying that the solver correctly
emits utterances to the master and returns spoken answers.

Note: Full integration tests require a live HiveMind server. These tests
mock the network layer to verify the solver's message routing logic.
"""
import threading
import unittest
from unittest.mock import MagicMock


class TestHiveMindSolverHarness(unittest.TestCase):
    """Wire HiveMindSolver to a mocked HM bus and test end-to-end message flow."""

    def _make_solver_with_mock_bus(self):
        """Create a HiveMindSolver with a fully mocked HiveMessageBusClient."""
        from ovos_hivemind_solver import HiveMindSolver
        solver = HiveMindSolver.__new__(HiveMindSolver)
        solver.config = {}
        solver._response = threading.Event()
        solver._responses = []
        solver._extend_timeout = False

        mock_hm = MagicMock()
        solver.hm = mock_hm
        return solver, mock_hm

    def test_solver_sends_utterance_to_master(self):
        """get_spoken_answer() emits recognizer_loop:utterance to the HM bus."""
        solver, mock_hm = self._make_solver_with_mock_bus()

        # Simulate immediate response so we don't wait
        def emit_and_respond(msg):
            solver._responses = ["The answer is 42"]
            solver._response.set()

        mock_hm.emit_mycroft.side_effect = emit_and_respond

        solver.get_spoken_answer("what is 6 times 7?")

        mock_hm.emit_mycroft.assert_called_once()
        emitted_msg = mock_hm.emit_mycroft.call_args.args[0]
        self.assertEqual(emitted_msg.msg_type, "recognizer_loop:utterance")
        self.assertIn("what is 6 times 7?", emitted_msg.data.get("utterances", []))

    def test_solver_receives_speak_response(self):
        """When master emits speak, solver's _receive_answer() captures it and returns text."""
        solver, mock_hm = self._make_solver_with_mock_bus()

        def emit_and_respond(msg):
            from ovos_bus_client.message import Message
            speak_msg = Message("speak", {"utterance": "The answer is 42"})
            solver._receive_answer(speak_msg)
            solver._end_of_response(Message("ovos.utterance.handled", {}))

        mock_hm.emit_mycroft.side_effect = emit_and_respond

        result = solver.get_spoken_answer("what is 6 times 7?")
        self.assertEqual(result, "The answer is 42")

    def test_solver_timeout_returns_none(self):
        """If master sends nothing within timeout, solver returns None."""
        solver, mock_hm = self._make_solver_with_mock_bus()
        # emit_mycroft does nothing — no response
        mock_hm.emit_mycroft = MagicMock()

        result = solver.get_spoken_answer("unanswerable query", timeout=0.01)
        self.assertIsNone(result)

    def test_solver_merges_multiple_speak_messages(self):
        """Multiple speak messages from master are merged into one answer."""
        solver, mock_hm = self._make_solver_with_mock_bus()

        def emit_and_respond(msg):
            from ovos_bus_client.message import Message
            solver._receive_answer(Message("speak", {"utterance": "First part."}))
            solver._receive_answer(Message("speak", {"utterance": "Second part."}))
            solver._end_of_response(Message("ovos.utterance.handled", {}))

        mock_hm.emit_mycroft.side_effect = emit_and_respond

        result = solver.get_spoken_answer("complex query?")
        self.assertEqual(result, "First part.\nSecond part.")

    def test_solver_forwards_lang_from_context(self):
        """Language from context is forwarded to the emitted utterance message."""
        solver, mock_hm = self._make_solver_with_mock_bus()

        def emit_and_respond(msg):
            solver._responses = ["Bonjour"]
            solver._response.set()

        mock_hm.emit_mycroft.side_effect = emit_and_respond

        solver.get_spoken_answer("salut", context={"lang": "fr-fr"})

        emitted_msg = mock_hm.emit_mycroft.call_args.args[0]
        self.assertEqual(emitted_msg.data.get("lang"), "fr-fr")


if __name__ == "__main__":
    unittest.main()
