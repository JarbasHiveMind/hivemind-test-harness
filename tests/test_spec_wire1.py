"""
HIVEMIND-WIRE-1 conformance — the frame-format and encoding MUSTs that had no
pinning test.

WIRE-1 is the one spec in the suite whose clauses are *frozen wire contracts*:
every requirement here is something an ESP32 (C), MicroPython or JS decoder was
built against and cannot be renegotiated. A silent regression in this file is a
silent interop break with peers that cannot be updated in lockstep, which is
exactly why these clauses deserve a test even though the assertions look small.

Covered here:

  * §2   — the protocol-version registry is 0-3
  * §2   — a server MUST reject a handshake that *completes* below its
           configured floor, not merely one that declares a low capability
  * §3   — every registered text encoding is implemented and round-trips
  * §3   — the encoding choice is not security-relevant: the same ciphertext
           material decrypts to the same plaintext under every encoding
  * §4.1 — a receiver MUST reject a frame whose frame-format version it does
           not implement
  * §4.2 — the 5-bit message-type code assignment is frozen
  * §4.2 — a receiver MUST reject an unassigned code as malformed
  * §5   — the binary payload-tag registry is frozen at 0-6
  * §6   — metadata and payload are compressed independently with zlib when
           the flag is set, and the auto mode never enlarges a frame

Deliberately NOT covered here (see the PR description):

  * §4.1 the exact header bit layout — the honest test for "front zero-padded,
    1 versioned bit, 8 version bits, 5 type bits, ..." is a cross-runtime
    decode by the C/JS/MicroPython decoders. Asserting the layout by
    re-encoding it with the same Python encoder proves only that the encoder
    agrees with itself. ``hivemind-websocket-client``'s own
    ``tests/test_serialization.py`` pins the Python round-trip; the interop
    proof belongs in the JS/MicroPython e2e drivers.
  * §4.1 the ">255 bytes of metadata" fallback — the spec's "shorten, compress
    or send as text" behaviour is NOT implemented (``bitstring`` raises), so
    there is no conformant behaviour to pin. It is a code gap, not a test gap.
  * §4.2 code 11 / THIRDPRTY — in flux across the installed and dev versions of
    ``hivemind_bus_client`` (11 is THIRDPRTY in 0.11.x, removed on the client's
    dev branch). Tests below therefore derive "unassigned" from ``_INT2TYPE``
    itself rather than hardcoding 11, so they pin the *rule* under both.
  * §6 the CRIME guard — unimplemented.
  * §4.3 INTERCOM-on-a-binarize-connection — a known open CODE defect
    (hivemind-core sends it as a bitstring and raises); pinning it would pin
    the bug. It belongs to the fix PR.
"""
import json

import pytest
from bitstring import BitArray

from hivemind_bus_client.encryption import (
    SupportedCiphers,
    SupportedEncodings,
    decrypt_from_json,
    encrypt_as_json,
    get_decoder,
    get_encoder,
)
from hivemind_bus_client.exceptions import UnsupportedProtocolVersion
from hivemind_bus_client.message import (
    HiveMessage,
    HiveMessageType,
    HiveMindBinaryPayloadType,
)
from hivemind_bus_client.serialization import (
    _INT2TYPE,
    _TYPE2INT,
    decode_bitstring,
    get_bitstring,
)
from hivemind_core.protocol import ProtocolVersion
from ovos_bus_client.message import Message

from hivescope.node import MasterNode, SatelliteNode


# ---------------------------------------------------------------------------
# WIRE-1 §2 — the protocol-version registry
# ---------------------------------------------------------------------------

class TestProtocolVersionRegistry:
    """WIRE-1 §2 — 'Four protocol versions are defined: 0, 1, 2 and 3.'

    The advertised ``min_protocol_version``/``max_protocol_version`` are raw
    integers on the wire, so the set of legal values is part of the contract a
    non-Python peer implements against.
    """

    def test_exactly_four_versions_numbered_zero_to_three(self):
        assert sorted(int(v) for v in ProtocolVersion) == [0, 1, 2, 3], (
            "WIRE-1 §2 defines exactly protocol versions 0-3; adding or "
            "removing one changes what every peer must negotiate against"
        )


class TestProtocolFloorJudgedOnCompletedHandshake:
    """WIRE-1 §2 — 'A server MUST reject a handshake that completes below its
    configured minimum protocol version.'

    The subtle part, and the reason this needs a test: the capability check at
    HELLO time (``min_version > max_version`` in ``handle_new_client``) only
    refuses a client that *cannot* reach the floor. A password-capable client
    advertises v3 capability and so passes that check, then completes a legacy
    v2 password handshake instead — silently under the floor. hivemind-core
    closes this in ``handle_handshake_message`` by judging the version actually
    performed. Nothing pinned it.
    """

    def _floor(self, monkeypatch, version: int):
        """Force the operator-configured protocol floor to ``version``."""
        import hivemind_core.protocol as core_protocol
        monkeypatch.setattr(core_protocol, "get_server_config",
                            lambda: {"min_protocol_version": version})

    def test_password_handshake_below_floor_is_refused(self, monkeypatch):
        # Floor 3 (Noise only). A password client is v3-*capable*, so the
        # HELLO-time capability check lets it in, but the legacy password
        # handshake it actually performs is v2 and MUST be refused.
        self._floor(monkeypatch, 3)
        master = MasterNode.create("M_floor3")
        sat = SatelliteNode.create("S_floor3")
        try:
            # The server refuses the sub-floor handshake by dropping the
            # connection, so connect() cannot complete. The exact exception is
            # an artefact of *where* the teardown lands; the conformance
            # assertions below are on the observable end state.
            with pytest.raises(RuntimeError):
                sat.connect(master)

            assert not sat.shim.handshake_event.is_set(), (
                "a handshake performed below the configured protocol floor "
                "MUST NOT complete")
            assert master.hm_protocol.clients == {}, (
                "a client refused for being below the protocol floor MUST NOT "
                "be left registered and reachable")
            assert sat._connection is None or not sat._connection.crypto_key, (
                "a refused handshake MUST NOT leave a usable session key behind")
        finally:
            sat.cleanup()
            master.cleanup()

    def test_password_handshake_at_or_above_floor_completes(self, monkeypatch):
        # Control: the same connection at floor 2 completes. Without this the
        # test above would also pass if connect() were broken for any reason.
        self._floor(monkeypatch, 2)
        master = MasterNode.create("M_floor2")
        sat = SatelliteNode.create("S_floor2")
        try:
            sat.connect(master)
            assert sat.shim.handshake_event.is_set()
            assert sat._connection.crypto_key, (
                "a handshake at the configured floor must establish a session key")
        finally:
            sat.cleanup()
            master.cleanup()


# ---------------------------------------------------------------------------
# WIRE-1 §3 — text encodings
# ---------------------------------------------------------------------------

_SPEC_ENCODINGS = {
    "JSON-B91", "JSON-Z85B", "JSON-Z85P", "JSON-B64",
    "JSON-URLSAFE-B64", "JSON-B32", "JSON-HEX",
}


class TestRegisteredTextEncodings:
    """WIRE-1 §3 — the seven registered encodings, and 'a node MUST NOT offer
    an encoding it does not implement.'

    The encoding name is negotiated by literal string in the HANDSHAKE payload,
    so both the name set and the fact that every advertised name resolves to a
    working codec are wire contract.
    """

    def test_registry_contains_exactly_the_seven_spec_names(self):
        assert {e.value for e in SupportedEncodings} == _SPEC_ENCODINGS

    @pytest.mark.parametrize("encoding", list(SupportedEncodings), ids=lambda e: e.value)
    def test_every_offered_encoding_actually_round_trips(self, encoding):
        """MUST NOT offer an encoding it does not implement — proven by using
        it, not by asserting a lookup table is populated."""
        payload = b"hivemind wire-1 \xc2\xa73 codec probe \x00\x01\xff"
        assert get_decoder(encoding)(get_encoder(encoding)(payload)) == payload


class TestEncodingIsNotSecurityRelevant:
    """WIRE-1 §3 — 'A node MUST NOT treat the encoding choice as
    security-relevant.'

    Observable form: the encoding is a transport alphabet over the same AEAD
    output. The same key and plaintext must survive every encoding, and no
    encoding may be a path that skips encryption (the JSON envelope always
    carries a ciphertext field, never the plaintext).
    """

    KEY = "0123456789ABCDEF"

    @pytest.mark.parametrize("encoding", list(SupportedEncodings), ids=lambda e: e.value)
    def test_plaintext_survives_every_encoding_and_is_never_in_the_clear(self, encoding):
        plaintext = json.dumps({"msg_type": "bus", "secret": "hunter2"})
        blob = encrypt_as_json(key=self.KEY, plaintext=plaintext,
                               cipher=SupportedCiphers.AES_GCM, encoding=encoding)
        assert "hunter2" not in blob, (
            f"encoding {encoding.value} leaked the plaintext into the JSON "
            "envelope — an encoding is an alphabet, never a crypto opt-out")
        assert decrypt_from_json(key=self.KEY, ciphertext_json=blob,
                                 encoding=encoding,
                                 cipher=SupportedCiphers.AES_GCM) == plaintext


# ---------------------------------------------------------------------------
# WIRE-1 §4.1 — frame-format version
# ---------------------------------------------------------------------------

def _frame_with_format_version(version: int) -> bytes:
    """Hand-build a versioned frame header declaring ``version``.

    Deliberately NOT built with ``get_bitstring``: the point is to present a
    receiver with a frame-format version the encoder cannot produce, which is
    exactly the situation a newer peer creates.
    """
    s = BitArray()
    s.append("uint:1=1")            # end of the front zero padding
    s.append("uint:1=1")            # versioned
    s.append(f"uint:8={version}")   # frame-format version
    s.append("uint:5=1")            # BUS, so a version-blind decoder would proceed
    s.append("uint:1=0")            # not compressed
    s.append("uint:8=2")            # 2 metadata bytes
    s.append(b"{}")
    s.append(b'{"type":"x","data":{},"context":{}}')
    while len(s) % 8 != 0:
        s.insert("uint:1=0", 0)
    return s.bytes


class TestUnknownFrameFormatVersionRejected:
    """WIRE-1 §4.1 — 'A receiver MUST reject a frame whose frame-format
    version it does not implement.'

    A version-blind decoder would read the *next* version's header with this
    version's layout and hand a plausible-looking but wrong message upstream.
    Rejecting is the only safe behaviour.
    """

    def test_future_frame_format_version_is_rejected(self):
        with pytest.raises(UnsupportedProtocolVersion):
            decode_bitstring(_frame_with_format_version(2))

    def test_current_frame_format_version_still_decodes(self):
        # Control: the same hand-built header at the implemented version parses,
        # so the rejection above is about the version and not about the frame
        # being malformed in some other way.
        msg = decode_bitstring(_frame_with_format_version(1))
        assert msg.msg_type == HiveMessageType.BUS


# ---------------------------------------------------------------------------
# WIRE-1 §4.2 — the 5-bit message-type code registry
# ---------------------------------------------------------------------------

# The codes deployed ESP32/MicroPython/JS decoders are built against. Frozen:
# changing any one of these silently mis-routes every frame of that type on
# peers that cannot be updated in lockstep. THIRDPRTY/11 is deliberately absent
# — it is being retired and its code must simply never be reused.
_FROZEN_CODES = {
    HiveMessageType.HANDSHAKE: 0,
    HiveMessageType.BUS: 1,
    HiveMessageType.SHARED_BUS: 2,
    HiveMessageType.BROADCAST: 3,
    HiveMessageType.PROPAGATE: 4,
    HiveMessageType.ESCALATE: 5,
    HiveMessageType.HELLO: 6,
    HiveMessageType.QUERY: 7,
    HiveMessageType.CASCADE: 8,
    HiveMessageType.PING: 9,
    HiveMessageType.RENDEZVOUS: 10,
    HiveMessageType.BINARY: 12,
}


class TestFrozenWireCodeAssignment:
    """WIRE-1 §4.2 — the 5-bit code assignment is frozen."""

    def test_every_frozen_type_keeps_its_code(self):
        actual = {t: _TYPE2INT[t] for t in _FROZEN_CODES if t in _TYPE2INT}
        assert actual == _FROZEN_CODES, (
            "WIRE-1 §4.2 codes are frozen for interop with the deployed "
            "ESP32/MicroPython/JS decoders; a renumbering here mis-routes "
            "every frame of the changed type on peers that cannot be updated")

    @pytest.mark.parametrize("hive_type", sorted(_FROZEN_CODES, key=lambda t: t.value))
    def test_each_frozen_code_round_trips_to_its_own_type(self, hive_type):
        """The registry is only meaningful if a frame encoded under a code
        decodes back as the same type — the table and the codec must not
        drift apart."""
        if hive_type == HiveMessageType.BINARY:
            frame = get_bitstring(hive_type=hive_type, payload=b"\x01\x02",
                                  binary_type=HiveMindBinaryPayloadType.RAW_AUDIO)
        else:
            frame = get_bitstring(hive_type=hive_type,
                                  payload={"probe": hive_type.value})
        assert decode_bitstring(frame.bytes).msg_type == hive_type


class TestUnassignedWireCodeRejected:
    """WIRE-1 §4.2 — 'A receiver MUST reject a frame carrying an unassigned or
    reserved value as malformed.'

    The failure this prevents is not theoretical: the decoder used to coerce any
    unknown 5-bit value to a real type, so a corrupt or forged frame arrived
    looking like a legitimate message of the wrong kind.

    "Unassigned" is derived from ``_INT2TYPE`` rather than hardcoded so this
    test keeps pinning the rule as the registry grows.
    """

    def _unassigned_code(self) -> int:
        free = [c for c in range(32) if c not in _INT2TYPE]
        assert free, "the 5-bit code space is full — the registry outgrew WIRE-1 §4.2"
        return free[-1]

    def test_frame_with_unassigned_type_code_is_rejected(self):
        code = self._unassigned_code()
        s = BitArray()
        s.append("uint:1=1")
        s.append("uint:1=0")          # not versioned
        s.append(f"uint:5={code}")
        s.append("uint:1=0")
        s.append("uint:8=2")
        s.append(b"{}")
        s.append(b'{"type":"x","data":{},"context":{}}')
        while len(s) % 8 != 0:
            s.insert("uint:1=0", 0)

        with pytest.raises(ValueError):
            decode_bitstring(s.bytes)

    def test_sender_refuses_to_binarize_a_type_with_no_code(self):
        """The mirror-image MUST: a type with no assigned code cannot be sent
        as a binary frame. INTERCOM is the only such type today; encoding it
        anyway used to put it on the wire mislabelled as another type."""
        assert HiveMessageType.INTERCOM not in _TYPE2INT
        with pytest.raises(ValueError):
            get_bitstring(hive_type=HiveMessageType.INTERCOM,
                          payload={"ciphertext": "x", "signature": "y"})


# ---------------------------------------------------------------------------
# WIRE-1 §5 — the binary payload-tag registry
# ---------------------------------------------------------------------------

class TestBinaryPayloadTagRegistry:
    """WIRE-1 §5 — seven binary payload tags, values 0-6.

    The tag is 4 raw bits on the wire; a satellite that sends RAW_AUDIO=1 and a
    server that reads 1 as something else mis-handles every audio frame.
    """

    def test_tag_values_are_frozen(self):
        assert {t.name: int(t) for t in HiveMindBinaryPayloadType} == {
            "UNDEFINED": 0,
            "RAW_AUDIO": 1,
            "NUMPY_IMAGE": 2,
            "FILE": 3,
            "STT_AUDIO_TRANSCRIBE": 4,
            "STT_AUDIO_HANDLE": 5,
            "TTS_AUDIO": 6,
        }

    @pytest.mark.parametrize("tag", list(HiveMindBinaryPayloadType),
                             ids=lambda t: t.name)
    def test_tag_survives_a_frame_round_trip(self, tag):
        frame = get_bitstring(hive_type=HiveMessageType.BINARY,
                              payload=b"\xde\xad\xbe\xef", binary_type=tag)
        decoded = decode_bitstring(frame.bytes)
        assert int(decoded.bin_type) == int(tag)
        assert decoded.payload == b"\xde\xad\xbe\xef"


# ---------------------------------------------------------------------------
# WIRE-1 §6 — compression
# ---------------------------------------------------------------------------

class TestFrameCompression:
    """WIRE-1 §6 — 'metadata and payload are each compressed independently with
    zlib when the compression flag is set', and 'a sender SHOULD set the flag
    only when it is worthwhile'.
    """

    def _big_payload(self):
        return Message("speak", {"utterance": "compress me. " * 40})

    def test_compressed_frame_round_trips_metadata_and_payload_independently(self):
        meta = {"query_id": "q-1", "note": "metadata compresses on its own. " * 10}
        frame = get_bitstring(hive_type=HiveMessageType.BUS,
                              payload=self._big_payload(),
                              hivemeta=meta, compressed=True)
        decoded = decode_bitstring(frame.bytes)
        assert decoded.metadata == meta, (
            "WIRE-1 §6: metadata is compressed as its own zlib stream and must "
            "come back byte-identical")
        assert decoded.payload.data["utterance"] == \
            self._big_payload().data["utterance"]

    def test_compression_flag_is_honoured_not_guessed(self):
        """A compressed and an uncompressed frame of the same message differ on
        the wire and both decode correctly — the receiver reads the flag rather
        than sniffing the bytes."""
        msg = self._big_payload()
        comp = get_bitstring(hive_type=HiveMessageType.BUS, payload=msg,
                             compressed=True).bytes
        unc = get_bitstring(hive_type=HiveMessageType.BUS, payload=msg,
                            compressed=False).bytes
        assert comp != unc
        assert len(comp) < len(unc), "a repetitive payload must actually shrink"
        for frame in (comp, unc):
            assert decode_bitstring(frame).payload.data["utterance"] == \
                msg.data["utterance"]

    @pytest.mark.parametrize("payload,label", [
        (Message("speak", {"utterance": "hi"}), "tiny"),
        (Message("speak", {"utterance": "compress me. " * 40}), "large"),
    ], ids=["tiny", "large"])
    def test_auto_mode_never_enlarges_a_frame(self, payload, label):
        """WIRE-1 §6 SHOULD: the sender sets the flag only when worthwhile.
        The observable form is that the auto-chosen framing is never bigger
        than the uncompressed one — including for a tiny payload, where zlib
        framing overhead makes compression a net loss."""
        auto = get_bitstring(hive_type=HiveMessageType.BUS, payload=payload,
                             compressed=None)
        unc = get_bitstring(hive_type=HiveMessageType.BUS, payload=payload,
                            compressed=False)
        assert len(auto) <= len(unc), (
            f"auto compression enlarged the {label} frame: "
            f"{len(auto)} bits vs {len(unc)} uncompressed")
        assert decode_bitstring(auto.bytes).payload.data == payload.data
