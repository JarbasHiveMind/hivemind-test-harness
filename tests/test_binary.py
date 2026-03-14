"""
TS-BIN-01..07 — Binary payload scenarios.
All use the minimal topology (1 master, 1 satellite).
"""
import pytest
from hivemind_bus_client.message import HiveMessage, HiveMessageType
from hivemind_bus_client.serialization import HiveMindBinaryPayloadType


FAKE_AUDIO = b"\x00\x01" * 2048   # 4096 bytes of fake audio
FAKE_IMAGE = b"\xff\xd8" * 512     # 1024 bytes of fake image
FAKE_FILE  = b"hello file content"


class TestRawAudio:
    """TS-BIN-01 — RAW_AUDIO binary upload."""

    def test_handle_microphone_input_called(self, minimal_topology):
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        msg = HiveMessage(
            HiveMessageType.BINARY,
            payload=FAKE_AUDIO,
            bin_type=HiveMindBinaryPayloadType.RAW_AUDIO,
            metadata={"sample_rate": 16000, "sample_width": 2},
        )
        s0.send(msg)

        m0.binary_protocol.assert_called("microphone_input")
        call = m0.binary_protocol.last_call("microphone_input")
        assert call.data == FAKE_AUDIO
        assert call.meta["sample_rate"] == 16000
        assert call.meta["sample_width"] == 2


class TestSttTranscribe:
    """TS-BIN-02 — STT_AUDIO_TRANSCRIBE binary upload."""

    def test_handle_stt_transcribe_called(self, minimal_topology):
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        msg = HiveMessage(
            HiveMessageType.BINARY,
            payload=FAKE_AUDIO,
            bin_type=HiveMindBinaryPayloadType.STT_AUDIO_TRANSCRIBE,
            metadata={"lang": "en-us", "sample_rate": 16000, "sample_width": 2},
        )
        s0.send(msg)

        m0.binary_protocol.assert_called("stt_transcribe")
        call = m0.binary_protocol.last_call("stt_transcribe")
        assert call.meta["lang"] == "en-us"


class TestSttHandle:
    """TS-BIN-03 — STT_AUDIO_HANDLE binary upload."""

    def test_handle_stt_handle_called(self, minimal_topology):
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        msg = HiveMessage(
            HiveMessageType.BINARY,
            payload=FAKE_AUDIO,
            bin_type=HiveMindBinaryPayloadType.STT_AUDIO_HANDLE,
            metadata={"lang": "en-us", "sample_rate": 16000, "sample_width": 2},
        )
        s0.send(msg)

        m0.binary_protocol.assert_called("stt_handle")


class TestReceiveTts:
    """TS-BIN-04 — TTS_AUDIO download (master → satellite)."""

    def test_satellite_receives_tts(self, minimal_topology):
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        tts_received = []

        original_handler = s0.slave_protocol.hm.emitter.listeners(HiveMessageType.BINARY)
        s0.shim.emitter.on(HiveMessageType.BINARY, tts_received.append)

        msg = HiveMessage(
            HiveMessageType.BINARY,
            payload=FAKE_AUDIO,
            bin_type=HiveMindBinaryPayloadType.TTS_AUDIO,
            metadata={"utterance": "hello world", "lang": "en-us",
                      "file_name": "tts_output.wav"},
        )
        m0.send_to_satellite(s0.peer, msg)

        assert len(tts_received) == 1, "Satellite should receive TTS binary"
        received = tts_received[0]
        assert received.bin_type == HiveMindBinaryPayloadType.TTS_AUDIO
        assert received.metadata["utterance"] == "hello world"


class TestFileTransfer:
    """TS-BIN-05 — FILE binary upload."""

    def test_handle_receive_file_called(self, minimal_topology):
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        msg = HiveMessage(
            HiveMessageType.BINARY,
            payload=FAKE_FILE,
            bin_type=HiveMindBinaryPayloadType.FILE,
            metadata={"file_name": "test.txt"},
        )
        s0.send(msg)

        m0.binary_protocol.assert_called("receive_file")
        call = m0.binary_protocol.last_call("receive_file")
        assert call.data == FAKE_FILE
        assert call.meta["file_name"] == "test.txt"


class TestNumpyImage:
    """TS-BIN-06 — NUMPY_IMAGE upload."""

    def test_handle_numpy_image_called(self, minimal_topology):
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        msg = HiveMessage(
            HiveMessageType.BINARY,
            payload=FAKE_IMAGE,
            bin_type=HiveMindBinaryPayloadType.NUMPY_IMAGE,
            metadata={"camera_id": "front"},
        )
        s0.send(msg)

        m0.binary_protocol.assert_called("numpy_image")
        call = m0.binary_protocol.last_call("numpy_image")
        assert call.meta["camera_id"] == "front"


class TestUndefinedBinary:
    """TS-BIN-07 — UNDEFINED binary is accepted without crash."""

    def test_undefined_binary_does_not_crash(self, minimal_topology):
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        msg = HiveMessage(
            HiveMessageType.BINARY,
            payload=b"\xde\xad\xbe\xef",
            bin_type=HiveMindBinaryPayloadType.UNDEFINED,
        )
        # Should not raise
        s0.send(msg)

        # No specific handler should have been called
        for handler in ("microphone_input", "stt_transcribe", "stt_handle",
                        "numpy_image", "receive_tts", "receive_file"):
            m0.binary_protocol.assert_not_called(handler)
