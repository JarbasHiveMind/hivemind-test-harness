"""
Tests for AudioBinaryProtocol transformer services.

These tests verify that utterance/dialog transformer services pass through
correctly when no plugins are configured, and that the transformer hooks
are wired correctly on AudioBinaryProtocol.
"""
import unittest
from unittest.mock import MagicMock, patch


class TestAudioBinaryProtocolTransformers(unittest.TestCase):
    """Tests for transform_utterances() and transform_dialogs() passthrough logic."""

    def _make_protocol(self):
        from hivemind_audio_binary_protocol.protocol import AudioBinaryProtocol
        proto = AudioBinaryProtocol.__new__(AudioBinaryProtocol)
        proto.config = {}
        proto.utterance_transformers = None
        proto.dialog_transformers = None
        proto.metadata_transformers = None
        # Provide a mock bus
        proto.bus = MagicMock()
        return proto

    def test_transform_utterances_passthrough_when_no_plugins(self):
        """transform_utterances with no enabled plugins returns utterances unchanged."""
        from hivemind_audio_binary_protocol.transformers import UtteranceTransformersService
        svc = MagicMock(spec=UtteranceTransformersService)
        svc.transform.side_effect = lambda utterances, context: (utterances, context or {})

        proto = self._make_protocol()
        proto.utterance_transformers = svc

        result_utts, result_ctx = proto.transform_utterances(["hello world"], "en-us")
        self.assertIn("hello world", result_utts)

    def test_transform_dialogs_passthrough_when_no_plugins(self):
        """transform_dialogs with no enabled plugins returns dialog unchanged."""
        from hivemind_audio_binary_protocol.transformers import DialogTransformersService
        svc = MagicMock(spec=DialogTransformersService)
        svc.transform.side_effect = lambda dialog, context=None, sess=None: (dialog, context or {})

        proto = self._make_protocol()
        proto.dialog_transformers = svc

        result_text, result_ctx = proto.transform_dialogs("it will be sunny", "en-us")
        self.assertEqual(result_text, "it will be sunny")

    def test_transform_utterances_calls_service(self):
        """transform_utterances should delegate to utterance_transformers.transform()."""
        from hivemind_audio_binary_protocol.transformers import UtteranceTransformersService
        svc = MagicMock(spec=UtteranceTransformersService)
        svc.transform.return_value = (["modified query"], {"modified": True})

        proto = self._make_protocol()
        proto.utterance_transformers = svc

        result_utts, result_ctx = proto.transform_utterances(["original query"], "en-us")
        svc.transform.assert_called_once()
        self.assertIn("modified query", result_utts)

    def test_transform_dialogs_calls_service(self):
        """transform_dialogs should delegate to dialog_transformers.transform()."""
        from hivemind_audio_binary_protocol.transformers import DialogTransformersService
        svc = MagicMock(spec=DialogTransformersService)
        svc.transform.return_value = ("MODIFIED RESPONSE", {})

        proto = self._make_protocol()
        proto.dialog_transformers = svc

        result_text, _ = proto.transform_dialogs("original response", "en-us")
        svc.transform.assert_called_once()
        self.assertEqual(result_text, "MODIFIED RESPONSE")


class TestUtteranceTransformersService(unittest.TestCase):
    """Tests for UtteranceTransformersService with mocked plugin loading."""

    def test_transform_no_plugins_returns_unchanged(self):
        """With no loaded plugins, transform() returns original utterances."""
        with patch("hivemind_audio_binary_protocol.transformers.find_utterance_transformer_plugins",
                   return_value={}):
            from hivemind_audio_binary_protocol.transformers import UtteranceTransformersService
            bus = MagicMock()
            svc = UtteranceTransformersService(bus, enabled_plugins=[])
            utts, ctx = svc.transform(["hello"], {})
            self.assertEqual(utts, ["hello"])

    def test_shutdown_called_on_plugins(self):
        """shutdown() should call shutdown on each loaded plugin."""
        with patch("hivemind_audio_binary_protocol.transformers.find_utterance_transformer_plugins",
                   return_value={}):
            from hivemind_audio_binary_protocol.transformers import UtteranceTransformersService
            bus = MagicMock()
            svc = UtteranceTransformersService(bus, enabled_plugins=[])
            # Inject a mock plugin
            mock_plugin = MagicMock()
            svc.loaded_plugins["test-plugin"] = mock_plugin
            svc.shutdown()
            mock_plugin.shutdown.assert_called_once()


class TestDialogTransformersService(unittest.TestCase):
    """Tests for DialogTransformersService with mocked plugin loading."""

    def test_transform_no_plugins_returns_unchanged(self):
        """With no loaded plugins, transform() returns original dialog."""
        with patch("hivemind_audio_binary_protocol.transformers.find_dialog_transformer_plugins",
                   return_value={}):
            from hivemind_audio_binary_protocol.transformers import DialogTransformersService
            bus = MagicMock()
            svc = DialogTransformersService(bus, enabled_plugins=[])
            text, ctx = svc.transform("sunny day", {})
            self.assertEqual(text, "sunny day")

    def test_shutdown_called_on_plugins(self):
        """shutdown() should call shutdown on each loaded plugin."""
        with patch("hivemind_audio_binary_protocol.transformers.find_dialog_transformer_plugins",
                   return_value={}):
            from hivemind_audio_binary_protocol.transformers import DialogTransformersService
            bus = MagicMock()
            svc = DialogTransformersService(bus, enabled_plugins=[])
            mock_plugin = MagicMock()
            svc.loaded_plugins["test-plugin"] = mock_plugin
            svc.shutdown()
            mock_plugin.shutdown.assert_called_once()


if __name__ == "__main__":
    unittest.main()
