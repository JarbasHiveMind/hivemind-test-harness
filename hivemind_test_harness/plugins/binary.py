"""
TestBinaryProtocol — BinaryDataHandlerProtocol that records every handler call
so tests can assert on what binary data was received.
"""
from dataclasses import dataclass, field
from typing import List, Optional

from hivemind_plugin_manager.protocols import BinaryDataHandlerProtocol


@dataclass
class BinaryCall:
    handler: str
    data: bytes
    meta: dict = field(default_factory=dict)

    def __repr__(self):
        return f"BinaryCall({self.handler!r}, {len(self.data)} bytes, meta={self.meta})"


@dataclass
class TestBinaryProtocol(BinaryDataHandlerProtocol):
    calls: List[BinaryCall] = field(default_factory=list)

    def handle_microphone_input(self, bin_data, sample_rate, sample_width, client):
        self.calls.append(BinaryCall(
            "microphone_input", bin_data,
            {"sample_rate": sample_rate, "sample_width": sample_width,
             "peer": client.peer}))

    def handle_stt_transcribe_request(self, bin_data, sample_rate, sample_width, lang, client):
        self.calls.append(BinaryCall(
            "stt_transcribe", bin_data,
            {"lang": lang, "sample_rate": sample_rate, "sample_width": sample_width,
             "peer": client.peer}))

    def handle_stt_handle_request(self, bin_data, sample_rate, sample_width, lang, client):
        self.calls.append(BinaryCall(
            "stt_handle", bin_data,
            {"lang": lang, "sample_rate": sample_rate, "sample_width": sample_width,
             "peer": client.peer}))

    def handle_numpy_image(self, bin_data, camera_id, client):
        self.calls.append(BinaryCall(
            "numpy_image", bin_data,
            {"camera_id": camera_id, "peer": client.peer}))

    def handle_receive_tts(self, bin_data, utterance, lang, file_name, client):
        self.calls.append(BinaryCall(
            "receive_tts", bin_data,
            {"utterance": utterance, "lang": lang,
             "file_name": file_name, "peer": client.peer}))

    def handle_receive_file(self, bin_data, file_name, client):
        self.calls.append(BinaryCall(
            "receive_file", bin_data,
            {"file_name": file_name, "peer": client.peer}))

    # --- assertion helpers ---

    def assert_called(self, handler: str, count: int = 1):
        matches = [c for c in self.calls if c.handler == handler]
        assert len(matches) == count, (
            f"Expected {count}x '{handler}', got {len(matches)}: {self.calls}"
        )

    def last_call(self, handler: str) -> Optional[BinaryCall]:
        matches = [c for c in self.calls if c.handler == handler]
        return matches[-1] if matches else None

    def assert_not_called(self, handler: str):
        matches = [c for c in self.calls if c.handler == handler]
        assert not matches, f"Expected '{handler}' NOT called, but got {len(matches)}."

    def clear(self):
        self.calls.clear()
