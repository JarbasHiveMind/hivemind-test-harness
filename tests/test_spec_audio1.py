"""
HIVEMIND-AUDIO-1 conformance — the binary-payload MUSTs a bare node owns.

Scope, stated plainly. AUDIO-1's conformance section binds an implementation
that *services* audio payloads, which is ``hivemind-audio-binary-protocol``.
That package is not installed in this environment, and the always-streaming
binary audio path it implements is homelab-scoped rather than a production
path, so nothing here tries to speak for it. What a bare ``hivemind-core``
does own — and what is pinned below — is the dispatch contract: which handler
a payload reaches, and which metadata that handler is given.

Pinned here:

  * AUDIO-1 §3   — an STT payload is exactly one complete utterance: one
                   payload produces exactly one handler call, and there is no
                   separate end-of-utterance message
  * AUDIO-1 §3   — ``STT_AUDIO_TRANSCRIBE`` and ``STT_AUDIO_HANDLE`` are
                   distinct destinations, not two names for one
  * AUDIO-1 §4/§5 — ``TTS_AUDIO`` carries ``lang`` and ``utterance``
  * AUDIO-1 §5   — ``FILE`` carries ``file_name``, and the receiver refuses a
                   traversing one; ``NUMPY_IMAGE`` carries ``camera_id``
  * AUDIO-1 §5   — payload bytes are never treated as self-describing: the
                   same bytes under two tags reach two different handlers

Not pinned, with the reason, at the bottom of this file.
"""
import pytest
from hivemind_bus_client.message import HiveMessage, HiveMessageType
from hivemind_bus_client.serialization import HiveMindBinaryPayloadType

from tests.conftest import poll_until


AUDIO = b"\x00\x01" * 1024
IMAGE = b"\xff\xd8" * 256


def _send(topology, bin_type, metadata, payload=AUDIO):
    b = topology
    b.get_satellite("S0").send(HiveMessage(
        HiveMessageType.BINARY, payload=payload,
        bin_type=bin_type, metadata=metadata))
    return b.get_master("M0")


# ---------------------------------------------------------------------------
# AUDIO-1 §3 — one payload is one utterance
# ---------------------------------------------------------------------------

class TestOneSttPayloadIsOneUtterance:
    """AUDIO-1 §3 — 'an STT payload is exactly one complete utterance; there is
    no separate end-of-utterance message.'

    This is what lets a satellite that does its own endpointing talk to a
    server that does not. If a payload were treated as a fragment awaiting a
    terminator, every such satellite would hang after its first utterance; if
    one payload produced two calls, every utterance would be transcribed and
    handled twice.
    """

    def test_one_payload_produces_exactly_one_handler_call(self, minimal_topology):
        m0 = _send(minimal_topology,
                   HiveMindBinaryPayloadType.STT_AUDIO_HANDLE,
                   {"lang": "en-us", "sample_rate": 16000, "sample_width": 2})

        poll_until(lambda: m0.binary_protocol.last_call("stt_handle"), timeout=3,
                   message="the STT payload never reached the STT stage")
        m0.binary_protocol.assert_called("stt_handle", count=1)
        assert m0.binary_protocol.last_call("stt_handle").data == AUDIO, (
            "the handler must receive the whole utterance, not a fragment")

    def test_two_payloads_produce_two_independent_utterances(self, minimal_topology):
        """The control: 'one payload, one call' must not be achieved by
        coalescing or by dropping the second."""
        b = minimal_topology
        for _ in range(2):
            _send(b, HiveMindBinaryPayloadType.STT_AUDIO_HANDLE,
                  {"lang": "en-us"})
        m0 = b.get_master("M0")
        poll_until(lambda: len([c for c in m0.binary_protocol.calls
                                if c.handler == "stt_handle"]) >= 2,
                   timeout=3,
                   message="the second utterance was swallowed")
        m0.binary_protocol.assert_called("stt_handle", count=2)

    def test_transcribe_and_handle_are_distinct_destinations(self, minimal_topology):
        """AUDIO-1 §3 — ``STT_AUDIO_TRANSCRIBE`` returns the transcript and
        does nothing further; ``STT_AUDIO_HANDLE`` injects it. Collapsing the
        two would make a satellite that only wanted a transcription
        unintentionally command the assistant."""
        b = minimal_topology
        m0 = _send(b, HiveMindBinaryPayloadType.STT_AUDIO_TRANSCRIBE,
                   {"lang": "en-us"})
        poll_until(lambda: m0.binary_protocol.last_call("stt_transcribe"),
                   timeout=3, message="TRANSCRIBE never reached its handler")
        m0.binary_protocol.assert_not_called("stt_handle")


# ---------------------------------------------------------------------------
# AUDIO-1 §5 — the metadata each tag must carry
# ---------------------------------------------------------------------------

class TestPayloadMetadataReachesTheHandler:
    """AUDIO-1 §4 / §5 — the per-tag metadata table.

    Every one of these fields is information the bytes cannot supply. A
    ``TTS_AUDIO`` frame without ``lang``/``utterance`` cannot be captioned or
    cached; a ``NUMPY_IMAGE`` without ``camera_id`` cannot be attributed to a
    camera in a multi-camera site.
    """

    def test_tts_audio_carries_lang_and_utterance(self, minimal_topology):
        m0 = _send(minimal_topology, HiveMindBinaryPayloadType.TTS_AUDIO,
                   {"lang": "pt-pt", "utterance": "olá mundo",
                    "file_name": "greeting.wav"})
        call = poll_until(lambda: m0.binary_protocol.last_call("receive_tts"),
                          timeout=3, message="TTS_AUDIO never reached its handler")
        assert call.meta.get("lang") == "pt-pt"
        assert call.meta.get("utterance") == "olá mundo", (
            f"AUDIO-1 §5 requires the utterance metadata; got {call.meta}")

    def test_numpy_image_carries_camera_id(self, minimal_topology):
        m0 = _send(minimal_topology, HiveMindBinaryPayloadType.NUMPY_IMAGE,
                   {"camera_id": "front-door"}, payload=IMAGE)
        call = poll_until(lambda: m0.binary_protocol.last_call("numpy_image"),
                          timeout=3, message="NUMPY_IMAGE never reached its handler")
        assert call.meta.get("camera_id") == "front-door", (
            f"AUDIO-1 §5 requires camera_id; got {call.meta}")

    def test_file_carries_a_file_name(self, minimal_topology):
        m0 = _send(minimal_topology, HiveMindBinaryPayloadType.FILE,
                   {"file_name": "notes.txt"}, payload=b"hello file")
        call = poll_until(lambda: m0.binary_protocol.last_call("receive_file"),
                          timeout=3, message="FILE never reached its handler")
        assert call.meta.get("file_name") == "notes.txt"


class TestFileNameIsNotAPathFromThePeer:
    """AUDIO-1 §5 — ``FILE`` carries a *file name*. hivemind-core additionally
    strips any directory component before handing it on, which the spec does
    not yet say but should.

    ``file_name`` is entirely peer-supplied and lands in a download directory.
    Without the strip, ``"../../../etc/cron.d/anything"`` is an authenticated
    peer writing outside its sandbox — this is the one clause in AUDIO-1 with
    a direct remote-write consequence, so it is pinned harder than the rest.
    """

    @pytest.mark.parametrize("hostile,expected", [
        ("../../etc/passwd", "passwd"),
        ("/etc/shadow", "shadow"),
        ("subdir/../../escape.txt", "escape.txt"),
    ])
    def test_a_traversing_file_name_is_reduced_to_its_base_name(
            self, minimal_topology, hostile, expected):
        m0 = _send(minimal_topology, HiveMindBinaryPayloadType.FILE,
                   {"file_name": hostile}, payload=b"payload")
        call = poll_until(lambda: m0.binary_protocol.last_call("receive_file"),
                          timeout=3, message="FILE never reached its handler")
        assert call.meta.get("file_name") == expected, (
            "a peer-supplied file name MUST be reduced to a base name before "
            f"it reaches storage; {hostile!r} arrived as "
            f"{call.meta.get('file_name')!r}")

    @pytest.mark.parametrize("useless", ["", "..", ".", "/", "../"])
    def test_a_file_name_that_reduces_to_nothing_is_refused(
            self, minimal_topology, useless):
        """Stripping is not enough on its own: a name that reduces to ``""``,
        ``"."`` or ``".."`` names a directory, not a file, and MUST be refused
        rather than handed on as a base name."""
        m0 = _send(minimal_topology, HiveMindBinaryPayloadType.FILE,
                   {"file_name": useless}, payload=b"payload")
        m0.binary_protocol.assert_not_called("receive_file")

    def test_a_file_with_no_file_name_at_all_is_refused(self, minimal_topology):
        m0 = _send(minimal_topology, HiveMindBinaryPayloadType.FILE, {},
                   payload=b"payload")
        m0.binary_protocol.assert_not_called("receive_file")


# ---------------------------------------------------------------------------
# AUDIO-1 §5 — the bytes are not self-describing
# ---------------------------------------------------------------------------

class TestPayloadBytesAreNeverSelfDescribing:
    """AUDIO-1 §5 — 'a receiver MUST NOT treat payload bytes as
    self-describing.'

    Sniffing the body — a RIFF header, a JPEG magic number — is the classic
    way this rule gets broken, and it turns a content-type decision into
    something a peer controls by crafting bytes. The tag and the metadata are
    the only inputs to the dispatch decision, which this test states by
    sending byte-identical payloads under two tags.
    """

    def test_identical_bytes_under_two_tags_reach_two_handlers(self, minimal_topology):
        b = minimal_topology
        _send(b, HiveMindBinaryPayloadType.RAW_AUDIO,
              {"sample_rate": 16000, "sample_width": 2})
        _send(b, HiveMindBinaryPayloadType.NUMPY_IMAGE, {"camera_id": "cam"})

        m0 = b.get_master("M0")
        poll_until(lambda: m0.binary_protocol.last_call("microphone_input")
                   and m0.binary_protocol.last_call("numpy_image"), timeout=3,
                   message="one of the two tagged payloads was not dispatched")
        assert m0.binary_protocol.last_call("microphone_input").data == AUDIO
        assert m0.binary_protocol.last_call("numpy_image").data == AUDIO, (
            "dispatch MUST follow the tag, not the bytes — the identical "
            "payloads were routed differently")


# ---------------------------------------------------------------------------
# NOT pinned here, and why
# ---------------------------------------------------------------------------
#
# * AUDIO-1 §2 "utterance endpointing for a RAW_AUDIO stream is the server's
#   responsibility" — the VAD/endpointing path lives in
#   ``hivemind-audio-binary-protocol``, which is not installed in this venv and
#   is homelab-scoped. A bare hivemind-core hands RAW_AUDIO to
#   ``handle_microphone_input`` and holds no opinion about endpointing, so
#   there is nothing here to hold in place. Pinning it needs the plugin
#   installed and a real streaming fixture.
#
# * AUDIO-1 §2 "reject a payload whose stated format the receiver cannot
#   process" — UNIMPLEMENTED (no format validation before the STT stage). A
#   test would be asserting behaviour the code does not have; it belongs with
#   the change that implements it.
#
# * AUDIO-1 §3 "signal unsuccessful recognition rather than inject an empty
#   utterance" — also in the uninstalled plugin, and unverified in the
#   conformance baseline. Not guessed at here.
