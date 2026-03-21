"""
E2E protocol tests for Voice PE satellite message flows.

Validates all message patterns used by the Voice PE satellite:
  - STT: binary streaming, base64 batch
  - TTS: binary chunks, base64 request/response
  - Lifecycle: record_begin/end, speak, utterance, wakeword

Uses test harness in-process topology — no real hardware needed.
"""
import base64
import struct
import threading

import pytest

from hivemind_bus_client.message import HiveMessage, HiveMessageType
from hivemind_bus_client.serialization import HiveMindBinaryPayloadType
from ovos_bus_client.message import Message


def _make_wav_bytes(num_samples=480):
    """Build a minimal 16-bit mono 16 kHz WAV."""
    pcm = b'\x00\x01' * num_samples
    data_bytes = num_samples * 2
    hdr = bytearray(44)
    struct.pack_into('<4sI4s', hdr, 0, b'RIFF', data_bytes + 36, b'WAVE')
    struct.pack_into('<4sIHHIIHH', hdr, 12,
                     b'fmt ', 16, 1, 1, 16000, 32000, 2, 16)
    struct.pack_into('<4sI', hdr, 36, b'data', data_bytes)
    return bytes(hdr) + pcm


FAKE_AUDIO = b"\x00\x01" * 480


def _wait_bus_msg(satellite, msg_type, timeout=5.0):
    """Register handler BEFORE any send, then wait for delivery."""
    event = threading.Event()
    result = []

    def handler(msg):
        result.append(msg)
        event.set()

    satellite.internal_bus.once(msg_type, handler)
    return event, result


# ═══════════════════════════════════════════════════════════════════
#  STT Binary Streaming
# ═══════════════════════════════════════════════════════════════════

class TestSTTBinaryStreaming:

    def test_raw_audio_reaches_master(self, minimal_topology):
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        s0.send(HiveMessage(
            HiveMessageType.BINARY, payload=FAKE_AUDIO,
            bin_type=HiveMindBinaryPayloadType.RAW_AUDIO,
            metadata={"sample_rate": 16000, "sample_width": 2},
        ))

        m0.binary_protocol.assert_called("microphone_input")

    def test_stt_handle_reaches_master(self, minimal_topology):
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        s0.send(HiveMessage(
            HiveMessageType.BINARY, payload=FAKE_AUDIO,
            bin_type=HiveMindBinaryPayloadType.STT_AUDIO_HANDLE,
        ))

        m0.binary_protocol.assert_called("stt_handle")

    def test_multiple_chunks_all_received(self, minimal_topology):
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        for i in range(5):
            s0.send(HiveMessage(
                HiveMessageType.BINARY, payload=bytes([i & 0xFF]) * 960,
                bin_type=HiveMindBinaryPayloadType.RAW_AUDIO,
                metadata={"sample_rate": 16000, "sample_width": 2},
            ))

        m0.binary_protocol.assert_called("microphone_input", count=5)


# ═══════════════════════════════════════════════════════════════════
#  STT Base64
# ═══════════════════════════════════════════════════════════════════

class TestSTTBase64:

    def test_b64_transcribe_reaches_master(self, minimal_topology):
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        wav = _make_wav_bytes(16000)
        b64_audio = base64.b64encode(wav).decode("utf-8")

        s0.send(Message("recognizer_loop:b64_transcribe", {
            "audio": b64_audio, "lang": "en-us"
        }))

        m0.agent_protocol.assert_injected("recognizer_loop:b64_transcribe")

    def test_b64_transcribe_response_reaches_satellite(self, minimal_topology):
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        # Register handler BEFORE sending
        event, result = _wait_bus_msg(s0, "recognizer_loop:b64_transcribe.response")

        m0.send_to_satellite(s0.peer,
                              HiveMessage(HiveMessageType.BUS,
                                           payload=Message("recognizer_loop:b64_transcribe.response", {
                                               "transcriptions": [["hello world", 0.95]]
                                           })))

        event.wait(timeout=5.0)
        assert len(result) > 0, "Satellite should receive transcription response"
        assert result[0].data["transcriptions"][0][0] == "hello world"


# ═══════════════════════════════════════════════════════════════════
#  TTS Binary
# ═══════════════════════════════════════════════════════════════════

class TestTTSBinary:

    def test_tts_audio_reaches_satellite(self, minimal_topology):
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        m0.send_to_satellite(s0.peer,
                              HiveMessage(HiveMessageType.BINARY,
                                           payload=b'\x00\x02' * 480,
                                           bin_type=HiveMindBinaryPayloadType.TTS_AUDIO))

        received = s0.recorder.wait_for(HiveMessageType.BINARY, direction="in", timeout=5.0)
        assert received is not None


# ═══════════════════════════════════════════════════════════════════
#  TTS Base64
# ═══════════════════════════════════════════════════════════════════

class TestTTSBase64:

    def test_speak_b64_audio_request(self, minimal_topology):
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        s0.send(Message("speak:b64_audio", {
            "utterance": "hello world", "listen": False
        }))

        m0.agent_protocol.assert_injected("speak:b64_audio")

    def test_speak_b64_audio_response(self, minimal_topology):
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        wav = _make_wav_bytes(8000)
        b64_audio = base64.b64encode(wav).decode("utf-8")

        event, result = _wait_bus_msg(s0, "speak:b64_audio.response")

        m0.send_to_satellite(s0.peer,
                              HiveMessage(HiveMessageType.BUS,
                                           payload=Message("speak:b64_audio.response", {
                                               "audio": b64_audio, "utterance": "hello"
                                           })))

        event.wait(timeout=5.0)
        assert len(result) > 0
        decoded = base64.b64decode(result[0].data["audio"])
        assert decoded[:4] == b'RIFF'


# ═══════════════════════════════════════════════════════════════════
#  Lifecycle Messages
# ═══════════════════════════════════════════════════════════════════

class TestLifecycleMessages:

    def test_record_begin_reaches_master(self, minimal_topology):
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")
        s0.send(Message("recognizer_loop:record_begin"))
        m0.agent_protocol.assert_injected("recognizer_loop:record_begin")

    def test_record_end_reaches_master(self, minimal_topology):
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")
        s0.send(Message("recognizer_loop:record_end"))
        m0.agent_protocol.assert_injected("recognizer_loop:record_end")

    def test_speak_reaches_satellite(self, minimal_topology):
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        event, result = _wait_bus_msg(s0, "speak")
        m0.send_to_satellite(s0.peer,
                              HiveMessage(HiveMessageType.BUS,
                                           payload=Message("speak", {"utterance": "The time is 3 PM"})))
        event.wait(timeout=5.0)
        assert len(result) > 0
        assert result[0].data["utterance"] == "The time is 3 PM"

    def test_utterance_reaches_master(self, minimal_topology):
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")
        s0.send(Message("recognizer_loop:utterance", {
            "utterances": ["what time is it"], "lang": "en-us"
        }))
        m0.agent_protocol.assert_injected("recognizer_loop:utterance")

    def test_wakeword_reaches_satellite(self, minimal_topology):
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        event, result = _wait_bus_msg(s0, "recognizer_loop:wakeword")
        m0.send_to_satellite(s0.peer,
                              HiveMessage(HiveMessageType.BUS,
                                           payload=Message("recognizer_loop:wakeword")))
        event.wait(timeout=5.0)
        assert len(result) > 0

    def test_audio_output_end_reaches_satellite(self, minimal_topology):
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        event, result = _wait_bus_msg(s0, "recognizer_loop:audio_output_end")
        m0.send_to_satellite(s0.peer,
                              HiveMessage(HiveMessageType.BUS,
                                           payload=Message("recognizer_loop:audio_output_end")))
        event.wait(timeout=5.0)
        assert len(result) > 0

    def test_utterance_handled_reaches_satellite(self, minimal_topology):
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        event, result = _wait_bus_msg(s0, "ovos.utterance.handled")
        m0.send_to_satellite(s0.peer,
                              HiveMessage(HiveMessageType.BUS,
                                           payload=Message("ovos.utterance.handled")))
        event.wait(timeout=5.0)
        assert len(result) > 0


class TestSpeakSynth:

    def test_speak_synth_request(self):
        """speak:synth requires explicit allowed_types since it's non-standard."""
        from hivemind_test_harness.topology import TopologyBuilder
        b = TopologyBuilder()
        b.add_master("M0")
        b.add_satellite("S0", upstream=b.get_master("M0"),
                         allowed_types=["recognizer_loop:utterance",
                                         "recognizer_loop:record_begin",
                                         "recognizer_loop:record_end",
                                         "recognizer_loop:b64_transcribe",
                                         "speak:synth", "speak:b64_audio"])
        b.start_all()
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")
        s0.send(Message("speak:synth", {"utterance": "test", "lang": "en-us"}))
        m0.agent_protocol.assert_injected("speak:synth")
        b.stop_all()


# ═══════════════════════════════════════════════════════════════════
#  Full Flow Tests
# ═══════════════════════════════════════════════════════════════════

class TestFullFlows:

    def test_vad_mode_full_flow(self, minimal_topology):
        """VAD mode: record_begin → audio chunks → record_end → speak → TTS."""
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        s0.send(Message("recognizer_loop:record_begin"))
        for _ in range(3):
            s0.send(HiveMessage(HiveMessageType.BINARY, payload=FAKE_AUDIO,
                                 bin_type=HiveMindBinaryPayloadType.RAW_AUDIO,
                                 metadata={"sample_rate": 16000, "sample_width": 2}))
        s0.send(Message("recognizer_loop:record_end"))

        m0.binary_protocol.assert_called("microphone_input", count=3)

        event, result = _wait_bus_msg(s0, "speak")
        m0.send_to_satellite(s0.peer,
                              HiveMessage(HiveMessageType.BUS,
                                           payload=Message("speak", {"utterance": "It is 3 PM"})))
        event.wait(timeout=5.0)
        assert len(result) > 0
        assert result[0].data["utterance"] == "It is 3 PM"

    def test_relay_mode_full_flow(self, minimal_topology):
        """Relay mode: b64_transcribe → response → speak → b64 TTS."""
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        wav = _make_wav_bytes(16000)
        b64 = base64.b64encode(wav).decode("utf-8")
        s0.send(Message("recognizer_loop:b64_transcribe", {"audio": b64, "lang": "en-us"}))
        m0.agent_protocol.assert_injected("recognizer_loop:b64_transcribe")

        # Register handlers before sending responses
        ev1, r1 = _wait_bus_msg(s0, "recognizer_loop:b64_transcribe.response")
        m0.send_to_satellite(s0.peer,
                              HiveMessage(HiveMessageType.BUS,
                                           payload=Message("recognizer_loop:b64_transcribe.response", {
                                               "transcriptions": [["what time is it", 0.9]]
                                           })))
        ev1.wait(timeout=5.0)
        assert len(r1) > 0

        ev2, r2 = _wait_bus_msg(s0, "speak")
        m0.send_to_satellite(s0.peer,
                              HiveMessage(HiveMessageType.BUS,
                                           payload=Message("speak", {"utterance": "It is 3 PM"})))
        ev2.wait(timeout=5.0)
        assert len(r2) > 0

        tts_wav = _make_wav_bytes(8000)
        tts_b64 = base64.b64encode(tts_wav).decode("utf-8")
        ev3, r3 = _wait_bus_msg(s0, "speak:b64_audio.response")
        m0.send_to_satellite(s0.peer,
                              HiveMessage(HiveMessageType.BUS,
                                           payload=Message("speak:b64_audio.response", {
                                               "audio": tts_b64, "utterance": "It is 3 PM"
                                           })))
        ev3.wait(timeout=5.0)
        assert len(r3) > 0
