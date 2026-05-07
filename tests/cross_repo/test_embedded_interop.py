"""Test cross-implementation crypto compatibility between hivemind-micropython-client
and hivemind-websocket-client (the production Python server/client).

These tests prove that an ESP32/MicroPython satellite can communicate with a
standard HiveMind hub — the crypto is wire-compatible.

Known issues discovered by this test suite:
- Z85P encoding: intermittent decode failures due to upstream z85base91 bug.
"""
import json
import os
import sys
from pathlib import Path

import pytest

# Ensure the MicroPython client package is importable (no pyproject.toml to install)
_MP_CLIENT_ROOT = Path(__file__).resolve().parents[2] / "hivemind-micropython-client"
if str(_MP_CLIENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_MP_CLIENT_ROOT))

# MicroPython client crypto (pure Python implementation)
from hivemind.crypto import (
    generate_hsub,
    extract_iv,
    derive_key,
    encrypt_json,
    decrypt_json,
    AesGcm,
    ChaCha20Poly1305,
    sha256,
    hmac_sha256,
    pbkdf2_hmac_sha256,
)
from hivemind.binary import encode as mp_binary_encode, decode as mp_binary_decode

# Production server crypto
from hivemind_bus_client.encryption import (
    encrypt_as_json,
    decrypt_from_json,
    encrypt_bin,
    decrypt_bin,
    SupportedEncodings,
    SupportedCiphers,
)

# Production binary protocol
from hivemind_bus_client.serialization import get_bitstring, decode_bitstring
from hivemind_bus_client.message import HiveMessage, HiveMessageType

# Production handshake
from poorman_handshake import PasswordHandShake

# ---------------------------------------------------------------------------
# Shared test constants
# ---------------------------------------------------------------------------

TEST_PASSWORD = "test_password"
TEST_KEY = derive_key(TEST_PASSWORD, b"\xaa" * 8, b"\xbb" * 8)
TEST_PLAINTEXT = '{"type": "speak", "data": {"utterance": "hello world"}}'

CIPHERS = [
    ("AES-GCM", SupportedCiphers.AES_GCM),
    ("ChaCha20-Poly1305", SupportedCiphers.CHACHA20_POLY1305),
]

ENCODINGS = [
    ("JSON-HEX", SupportedEncodings.JSON_HEX),
    ("JSON-B64", SupportedEncodings.JSON_B64),
    ("JSON-B32", SupportedEncodings.JSON_B32),
    ("JSON-URLSAFE-B64", SupportedEncodings.JSON_URLSAFE_B64),
]

# Z85/B91 encodings — only available if z85base91 is installed
try:
    from z85base91 import Z85B, Z85P, B91  # noqa: F401
    ENCODINGS += [
        ("JSON-Z85B", SupportedEncodings.JSON_Z85B),
        ("JSON-Z85P", SupportedEncodings.JSON_Z85P),
        ("JSON-B91", SupportedEncodings.JSON_B91),
    ]
    _HAVE_Z85B91 = True
except ImportError:
    _HAVE_Z85B91 = False


# ---------------------------------------------------------------------------
# TestHsubInterop
# ---------------------------------------------------------------------------

class TestHsubInterop:
    """Verify that MicroPython-generated hsubs are accepted by the server."""

    def test_micropython_hsub_validates_on_server(self) -> None:
        """Server PasswordHandShake.verify() accepts a MicroPython-generated hsub."""
        password = "shared_secret_42"
        _iv, hsub_hex = generate_hsub(password)

        server = PasswordHandShake(password)
        assert server.verify(hsub_hex), (
            "Server rejected MicroPython hsub"
        )

    def test_key_derivation_matches(self) -> None:
        """Both sides derive the same symmetric key after exchanging hsubs."""
        password = "interop_password"

        # Server side
        server = PasswordHandShake(password)
        server_hsub = server.generate_handshake()

        # MicroPython side
        client_iv, client_hsub = generate_hsub(password)

        # Exchange: server receives client hsub, client receives server hsub
        server.receive_and_verify(client_hsub)
        server_key = server.secret

        # MicroPython derives key using both IVs
        server_iv = extract_iv(server_hsub)
        mp_key = derive_key(password, client_iv, server_iv)

        assert server_key == mp_key, (
            f"Key mismatch: server={server_key.hex()}, mp={mp_key.hex()}"
        )


# ---------------------------------------------------------------------------
# TestEncryptionInterop
# ---------------------------------------------------------------------------

class TestEncryptionInterop:
    """Verify encrypt/decrypt interop for all cipher x encoding combinations."""

    @pytest.mark.parametrize(
        "cipher_name, server_cipher",
        CIPHERS,
        ids=[c[0] for c in CIPHERS],
    )
    @pytest.mark.parametrize(
        "encoding_name, server_encoding",
        ENCODINGS,
        ids=[e[0] for e in ENCODINGS],
    )
    def test_micropython_encrypt_server_decrypt(
        self,
        cipher_name: str,
        server_cipher: SupportedCiphers,
        encoding_name: str,
        server_encoding: SupportedEncodings,
    ) -> None:
        """MicroPython encrypts, production server decrypts."""
        ct_json = encrypt_json(
            TEST_KEY,
            TEST_PLAINTEXT.encode("utf-8"),
            cipher=cipher_name,
            encoding=encoding_name,
        )
        result = decrypt_from_json(
            TEST_KEY,
            ct_json,
            cipher=server_cipher,
            encoding=server_encoding,
        )
        assert result == TEST_PLAINTEXT

    @pytest.mark.parametrize(
        "cipher_name, server_cipher",
        CIPHERS,
        ids=[c[0] for c in CIPHERS],
    )
    @pytest.mark.parametrize(
        "encoding_name, server_encoding",
        ENCODINGS,
        ids=[e[0] for e in ENCODINGS],
    )
    def test_server_encrypt_micropython_decrypt(
        self,
        cipher_name: str,
        server_cipher: SupportedCiphers,
        encoding_name: str,
        server_encoding: SupportedEncodings,
    ) -> None:
        """Production server encrypts, MicroPython decrypts."""
        ct_json = encrypt_as_json(
            TEST_KEY,
            TEST_PLAINTEXT,
            cipher=server_cipher,
            encoding=server_encoding,
        )
        result = decrypt_json(
            TEST_KEY,
            ct_json,
            cipher=cipher_name,
            encoding=encoding_name,
        )
        assert result.decode("utf-8") == TEST_PLAINTEXT

    def test_large_payload_roundtrip(self) -> None:
        """Verify interop with a larger payload (4 KB random JSON) using ChaCha20."""
        payload = json.dumps({"data": os.urandom(3000).hex()})
        ct = encrypt_json(
            TEST_KEY,
            payload.encode("utf-8"),
            cipher="ChaCha20-Poly1305",
            encoding="JSON-B64",
        )
        result = decrypt_from_json(
            TEST_KEY,
            ct,
            cipher=SupportedCiphers.CHACHA20_POLY1305,
            encoding=SupportedEncodings.JSON_B64,
        )
        assert result == payload


# ---------------------------------------------------------------------------
# TestBinaryProtocolInterop
# ---------------------------------------------------------------------------

class TestBinaryProtocolInterop:
    """Verify binary frame encoding/decoding between MicroPython and server."""

    # Message type mappings (must match both implementations)
    _TYPE_BUS = 1

    def test_micropython_encode_server_decode(self) -> None:
        """MicroPython encodes a BUS frame, server decodes it."""
        payload_str = json.dumps({
            "type": "speak",
            "data": {"utterance": "hello from ESP32"},
            "context": {},
        })
        meta = json.dumps({}).encode("utf-8")

        frame = mp_binary_encode(
            msg_type=self._TYPE_BUS,
            bin_type=0,
            metadata=meta,
            payload=payload_str.encode("utf-8"),
            versioned=True,
        )

        decoded = decode_bitstring(frame)
        assert decoded.msg_type == HiveMessageType.BUS
        # Server deserializes BUS payload into a Message object; check via serialize()
        payload_data = decoded.payload
        if hasattr(payload_data, "serialize"):
            assert "hello from ESP32" in payload_data.serialize()
        else:
            assert "hello from ESP32" in str(payload_data)

    def test_server_encode_micropython_decode(self) -> None:
        """Server encodes a BUS frame, MicroPython decodes it."""
        from ovos_bus_client.message import Message

        inner = Message("speak", {"utterance": "hello from hub"})
        frame = get_bitstring(
            hive_type=HiveMessageType.BUS,
            payload=inner,
            compressed=False,
            hivemeta={},
        )
        frame_bytes = frame.bytes

        decoded = mp_binary_decode(frame_bytes)
        assert decoded["msg_type"] == self._TYPE_BUS
        payload_str = decoded["payload"].decode("utf-8")
        assert "hello from hub" in payload_str

    def test_binary_payload_roundtrip(self) -> None:
        """Raw binary payload survives MicroPython encode -> server decode."""
        from hivemind.binary import MSG_BINARY, BIN_RAW_AUDIO

        raw_audio = os.urandom(256)
        meta = b"{}"

        frame = mp_binary_encode(
            msg_type=MSG_BINARY,
            bin_type=BIN_RAW_AUDIO,
            metadata=meta,
            payload=raw_audio,
            versioned=True,
        )

        decoded = decode_bitstring(frame)
        assert decoded.msg_type == HiveMessageType.BINARY
        assert decoded.payload == raw_audio


# ---------------------------------------------------------------------------
# TestFullHandshakeInterop
# ---------------------------------------------------------------------------

class TestFullHandshakeInterop:
    """Simulate a complete PAKE handshake between MicroPython client and server."""

    def test_full_pake_handshake(self) -> None:
        """Full handshake: hsub exchange, key derivation, encrypted message roundtrip.

        Uses ChaCha20-Poly1305 to avoid the known AES-GCM nonce mismatch.
        """
        password = "full_handshake_test_pw"

        # Step 1: Server generates its hsub
        server = PasswordHandShake(password)
        server_hsub = server.generate_handshake()

        # Step 2: MicroPython client generates its hsub
        client_iv, client_hsub = generate_hsub(password)

        # Step 3: Exchange hsubs
        assert server.receive_and_verify(client_hsub), "Server rejected client hsub"

        # Step 4: Both sides derive keys
        server_key = server.secret
        server_iv = extract_iv(server_hsub)
        client_key = derive_key(password, client_iv, server_iv)

        assert server_key == client_key, "Derived keys do not match"

        # Step 5: MicroPython encrypts a test message, server decrypts
        msg = '{"type": "recognizer_loop:utterance", "data": {"utterances": ["turn on the lights"]}}'
        ct = encrypt_json(
            client_key, msg.encode("utf-8"),
            cipher="ChaCha20-Poly1305", encoding="JSON-HEX",
        )
        decrypted = decrypt_from_json(
            server_key, ct,
            cipher=SupportedCiphers.CHACHA20_POLY1305,
            encoding=SupportedEncodings.JSON_HEX,
        )
        assert decrypted == msg

        # Step 6: Server encrypts a response, MicroPython decrypts
        response = '{"type": "speak", "data": {"utterance": "lights are on"}}'
        ct2 = encrypt_as_json(
            server_key, response,
            cipher=SupportedCiphers.CHACHA20_POLY1305,
            encoding=SupportedEncodings.JSON_HEX,
        )
        decrypted2 = decrypt_json(
            client_key, ct2,
            cipher="ChaCha20-Poly1305", encoding="JSON-HEX",
        )
        assert decrypted2.decode("utf-8") == response

    @pytest.mark.parametrize(
        "cipher_name, server_cipher",
        CIPHERS,
        ids=[c[0] for c in CIPHERS],
    )
    def test_handshake_with_each_cipher(
        self,
        cipher_name: str,
        server_cipher: SupportedCiphers,
    ) -> None:
        """Full handshake works with each supported cipher."""
        password = "cipher_test_pw"

        server = PasswordHandShake(password)
        server_hsub = server.generate_handshake()

        client_iv, client_hsub = generate_hsub(password)
        server.receive_and_verify(client_hsub)

        server_key = server.secret
        client_key = derive_key(password, client_iv, extract_iv(server_hsub))
        assert server_key == client_key

        msg = '{"test": true}'
        ct = encrypt_json(
            client_key, msg.encode("utf-8"),
            cipher=cipher_name, encoding="JSON-B64",
        )
        result = decrypt_from_json(
            server_key, ct,
            cipher=server_cipher,
            encoding=SupportedEncodings.JSON_B64,
        )
        assert result == msg


# ---------------------------------------------------------------------------
# TestTopologyWithMicroPythonCrypto
# ---------------------------------------------------------------------------

# TODO: Testing a full topology with MicroPython crypto injected would require
# monkey-patching the satellite's encryption layer to use hivemind.crypto instead
# of hivemind_bus_client.encryption. This is feasible but would couple the test
# tightly to internal protocol details. Deferring until the test harness gains
# a crypto-provider abstraction (plugin interface for encryption backends).
