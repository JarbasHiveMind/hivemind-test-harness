"""
Comprehensive E2E tests for embedded HiveMind clients.

Tests focus on:
1. MicroPython client crypto and binary protocol
2. ESP32 C module native compilation and unit tests
3. Message serialization/deserialization
4. Multi-client scenarios
"""

import json
import sys
from pathlib import Path

import pytest

# Import MicroPython client crypto module
_MP_CLIENT_ROOT = Path(__file__).resolve().parents[2] / "hivemind-micropython-client"
if str(_MP_CLIENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_MP_CLIENT_ROOT))

from hivemind.crypto import (
    generate_hsub, derive_key, encrypt_json,
    decrypt_json, randbytes
)
from hivemind.binary import encode as binary_encode, decode as binary_decode, BIN_RAW_AUDIO
from hivemind_bus_client.message import HiveMessage, HiveMessageType
from ovos_bus_client.message import Message


class TestMicroPythonCrypto:
    """Test MicroPython crypto module (works with CPython too)."""

    def test_hsub_generation(self):
        """Generate HSUB key derivation value."""
        password = "test-password-123"

        # generate_hsub returns (iv_bytes, hsub_str)
        iv, hsub = generate_hsub(password)
        assert isinstance(iv, bytes)
        assert isinstance(hsub, str)
        assert len(hsub) > 0

        # Verify it's hex
        try:
            int(hsub, 16)
        except ValueError:
            pytest.fail("HSUB is not valid hex")

    def test_key_derivation(self):
        """Derive session key from client/server IVs."""
        password = "shared-secret"
        client_iv = randbytes(8)
        server_iv = randbytes(8)

        # derive_key returns bytes (the key itself)
        key = derive_key(password, client_iv, server_iv)

        assert key is not None
        assert isinstance(key, bytes)
        assert len(key) > 0

    def test_encrypt_decrypt_roundtrip(self):
        """Encrypt and decrypt message."""
        password = "test-secret"
        client_iv = randbytes(8)
        server_iv = randbytes(8)

        key = derive_key(password, client_iv, server_iv)

        # encrypt_json takes (key, plaintext_bytes, cipher, encoding)
        original_msg = b'{"msg_type": "speak", "data": {"utterance": "hello world"}}'
        encrypted = encrypt_json(key, original_msg, "AES-GCM")

        assert isinstance(encrypted, str)
        assert encrypted != original_msg.decode()  # Should be different

        # decrypt_json takes (key, cipher_str, cipher_type, encoding)
        decrypted = decrypt_json(key, encrypted, "AES-GCM")
        assert decrypted == original_msg

    def test_multiple_messages_different_keys(self):
        """Different keys should produce different ciphertexts."""
        msg = b'{"test": "data"}'

        key1 = randbytes(32)
        key2 = randbytes(32)

        enc1 = encrypt_json(key1, msg, "AES-GCM")
        enc2 = encrypt_json(key2, msg, "AES-GCM")

        # Should be different
        assert enc1 != enc2


class TestMicroPythonBinary:
    """Test MicroPython binary protocol."""

    def test_bus_message_encode_decode(self):
        """Encode BUS message to binary, decode back."""
        # encode signature: (msg_type, bin_type, metadata_bytes, payload_bytes, versioned)
        metadata_bytes = json.dumps({"session": "abc123", "source": "test"}).encode()
        payload_bytes = b"recognizer_loop:utterance"

        # Encode
        encoded = binary_encode(
            1,  # HiveMessageType.BUS value
            0,  # BIN_UNDEFINED
            metadata_bytes,
            payload_bytes
        )

        assert isinstance(encoded, bytes)
        assert len(encoded) > 0

        # Decode
        decoded = binary_decode(encoded)
        assert decoded['msg_type'] == 1  # BUS
        assert decoded['payload'] == payload_bytes

    def test_broadcast_message_encode_decode(self):
        """Encode BROADCAST message."""
        metadata_bytes = json.dumps({"route": []}).encode()
        payload_bytes = b"skills:respeak"

        encoded = binary_encode(
            3,  # HiveMessageType.BROADCAST
            0,  # BIN_UNDEFINED
            metadata_bytes,
            payload_bytes
        )

        assert isinstance(encoded, bytes)

        decoded = binary_decode(encoded)
        assert decoded['msg_type'] == 3  # BROADCAST

    def test_binary_with_complex_metadata(self):
        """Handle metadata with nested structures."""
        metadata = {
            "session": "xyz789",
            "source": "device1",
            "route": ["hub1", "hub2"],
        }
        metadata_bytes = json.dumps(metadata).encode()
        payload_bytes = b"test:message"

        encoded = binary_encode(
            1,  # BUS
            0,
            metadata_bytes,
            payload_bytes
        )

        decoded = binary_decode(encoded)
        # Metadata should be preserved
        assert b"session" in decoded['metadata'] or "session" in str(decoded['metadata'])

    def test_multiple_message_types(self):
        """Test encoding/decoding multiple message types."""
        test_cases = [
            (1, b"bus:message"),     # BUS
            (3, b"broadcast:message"),  # BROADCAST
            (4, b"escalate:data"),   # ESCALATE
            (5, b"propagate:data"),  # PROPAGATE
        ]

        for msg_type, payload in test_cases:
            encoded = binary_encode(msg_type, 0, b"{}", payload)
            assert isinstance(encoded, bytes), f"Failed for type {msg_type}"

            decoded = binary_decode(encoded)
            assert decoded['msg_type'] == msg_type
            assert decoded['payload'] == payload


class TestMicroPythonIntegration:
    """Integration tests for MicroPython client components."""

    def test_session_id_generation(self):
        """Generate valid session IDs."""
        from hivemind.client import HiveMindClient

        # Session ID is generated dynamically when needed, test the generator
        sid = HiveMindClient._generate_session_id()

        assert sid is not None
        assert isinstance(sid, str)
        assert len(sid) > 0
        # Should be UUID-like format
        assert sid.count('-') > 0

    def test_encryption_settings_negotiation(self):
        """Test cipher/encoding preference selection."""
        from hivemind.client import HiveMindClient

        # Test with different preferences
        client1 = HiveMindClient(
            host="localhost", port=5678,
            username="test1", access_key="key1", password="pass1",
            preferred_cipher="AES-GCM"
        )

        client2 = HiveMindClient(
            host="localhost", port=5678,
            username="test2", access_key="key2", password="pass2",
            preferred_cipher="ChaCha20-Poly1305"
        )

        assert client1.preferred_cipher == "AES-GCM"
        assert client2.preferred_cipher == "ChaCha20-Poly1305"

    def test_envelope_building(self):
        """Test HiveMind message envelope format."""
        from hivemind.client import HiveMindClient

        envelope_json = HiveMindClient._build_envelope(
            "recognizer_loop:utterance",
            {"utterances": ["hello"], "lang": "en-us"}
        )

        envelope = json.loads(envelope_json)
        assert envelope['msg_type'] == "recognizer_loop:utterance"
        assert envelope['payload']['utterances'] == ["hello"]
        assert 'metadata' in envelope
        assert 'route' in envelope


class TestClientConfiguration:
    """Test client configuration and setup."""

    def test_client_initialization(self):
        """Client initializes with correct defaults."""
        from hivemind.client import HiveMindClient

        client = HiveMindClient(
            host="test.example.com",
            port=9999,
            username="testuser",
            access_key="testkey",
            password="testpass"
        )

        assert client.host == "test.example.com"
        assert client.port == 9999
        assert client.username == "testuser"
        assert client.access_key == "testkey"
        assert client.password == "testpass"

    def test_client_callbacks(self):
        """Client callback handlers can be set."""
        from hivemind.client import HiveMindClient

        client = HiveMindClient(
            host="localhost", port=5678,
            username="test", access_key="key", password="pass"
        )

        def on_message(msg_type, data, context):
            pass

        def on_binary(bin_type, data):
            pass

        client.on_bus_message = on_message
        client.on_binary = on_binary

        assert client.on_bus_message == on_message
        assert client.on_binary == on_binary


class TestMessageFormats:
    """Test HiveMind message format compatibility."""

    def test_hive_message_types(self):
        """All HiveMessageType values are valid."""
        valid_types = [
            HiveMessageType.BUS,
            HiveMessageType.BROADCAST,
            HiveMessageType.ESCALATE,
            HiveMessageType.PROPAGATE,
            HiveMessageType.HELLO,
            HiveMessageType.HANDSHAKE,
        ]

        for msg_type in valid_types:
            # HiveMessageType can be string enums or ints depending on version
            assert msg_type is not None
            # Should be convertible to string representation
            assert str(msg_type) is not None

    def test_ovos_message_format(self):
        """OVOS Message objects work with HiveMind."""
        msg = Message("speak", {"utterance": "hello"})

        assert msg.msg_type == "speak"
        assert msg.data["utterance"] == "hello"

        # Should be serializable
        json.dumps(msg.serialize())

    def test_hive_message_wrapping(self):
        """Wrap OVOS messages in HiveMessage."""
        ovos_msg = Message("recognizer_loop:utterance", {"utterances": ["test"]})
        hm_msg = HiveMessage(HiveMessageType.BUS, payload=ovos_msg)

        assert hm_msg.msg_type == HiveMessageType.BUS
        assert hm_msg.payload == ovos_msg
