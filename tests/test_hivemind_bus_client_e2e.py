"""
End-to-end tests for HiveMessageBusClient (hivemind-websocket-client) against
the loopback WebSocket harness.

Tests the production Python satellite client through a real WebSocket connection:
handshake, encryption negotiation, BUS message injection, binary data transfer,
and session management.

Reference: HiveMessageBusClient — hivemind_bus_client/client.py:93
"""
import threading
import time

import pytest
from ovos_bus_client.message import Message
from ovos_utils.fakebus import FakeBus

from hivemind_bus_client.client import HiveMessageBusClient
from hivemind_bus_client.identity import NodeIdentity
from hivemind_bus_client.message import HiveMessage, HiveMessageType
from hivemind_test_harness.topology import TopologyBuilder


def _extract_host_port(url: str):
    """Extract (host, port) from ws://127.0.0.1:PORT/."""
    # url is like "ws://127.0.0.1:12345/"
    parts = url.replace("ws://", "").replace("wss://", "").rstrip("/").split(":")
    return parts[0], int(parts[1])


def _make_client(url: str, key: str, password: str,
                 name: str = "test-client") -> HiveMessageBusClient:
    """Create a HiveMessageBusClient pointing at the loopback server."""
    host, port = _extract_host_port(url)
    identity = NodeIdentity()
    identity.access_key = key
    identity.password = password
    identity.default_master = f"ws://{host}"
    identity.default_port = port
    identity.name = name
    identity.site_id = f"{name}-site"

    return HiveMessageBusClient(
        key=key,
        password=password,
        host=f"ws://{host}",
        port=port,
        useragent=name,
        self_signed=False,
        identity=identity,
    )


class TestHiveMessageBusClientHandshake:
    """Handshake + encryption negotiation via real WebSocket."""

    def test_client_connects_and_handshakes(self):
        """Client reaches handshake completion against loopback hub."""
        b = TopologyBuilder()
        m = b.add_master("M0", use_loopback=True)
        m.register_satellite("test-key", password="test-password")
        b.start_all()

        try:
            client = _make_client(m.network_protocol.url, "test-key", "test-password")
            client.connect(site_id="loopback-site")
            client.wait_for_handshake(timeout=10)
            assert client.handshake_event.is_set(), \
                "Handshake did not complete within 10s"

            # Client should have a crypto key after handshake
            assert client.crypto_key is not None, "crypto_key not set after handshake"

            # Wait for encrypted HELLO to be processed (registers client)
            time.sleep(1)
            assert len(m.connected_peers()) == 1, \
                f"Expected 1 peer, got {m.connected_peers()}"
        finally:
            try:
                client.close()
            except Exception:
                pass
            b.stop_all()

    def test_two_clients_connect_independently(self):
        """Two clients connect and get independent sessions."""
        b = TopologyBuilder()
        m = b.add_master("M0", use_loopback=True)
        m.register_satellite("key-1", password="pwd-1")
        m.register_satellite("key-2", password="pwd-2")
        b.start_all()

        try:
            c1 = _make_client(m.network_protocol.url, "key-1", "pwd-1", "client-1")
            c2 = _make_client(m.network_protocol.url, "key-2", "pwd-2", "client-2")

            c1.connect(site_id="site-1")
            c2.connect(site_id="site-2")

            c1.wait_for_handshake(timeout=10)
            c2.wait_for_handshake(timeout=10)
            time.sleep(1)  # wait for encrypted HELLO processing

            peers = m.connected_peers()
            assert len(peers) == 2, f"Expected 2 peers, got {peers}"

            # Independent crypto keys
            assert c1.crypto_key != c2.crypto_key, \
                "Clients must have different session crypto keys"
        finally:
            for c in [c1, c2]:
                try:
                    c.close()
                except Exception:
                    pass
            b.stop_all()


class TestHiveMessageBusClientBusMessages:
    """BUS message injection and response routing."""

    def test_send_utterance_arrives_at_hub(self):
        """Client sends recognizer_loop:utterance, hub receives it."""
        b = TopologyBuilder()
        m = b.add_master("M0", use_loopback=True)
        m.register_satellite("test-key", password="test-password")
        b.start_all()

        try:
            client = _make_client(m.network_protocol.url, "test-key", "test-password")
            client.connect(site_id="loopback-site")
            client.wait_for_handshake(timeout=10)

            # Send utterance via HiveMessage
            msg = Message("recognizer_loop:utterance",
                          {"utterances": ["hello world"]})
            hmsg = HiveMessage(HiveMessageType.BUS, payload=msg)
            client.emit(hmsg)

            # Wait for hub to receive
            time.sleep(1)

            m.agent_protocol.assert_injected(
                "recognizer_loop:utterance", count=1
            )
        finally:
            try:
                client.close()
            except Exception:
                pass
            b.stop_all()

    def test_hub_sends_speak_to_client(self):
        """Hub sends speak BUS message, client receives it on internal bus."""
        b = TopologyBuilder()
        m = b.add_master("M0", use_loopback=True)
        m.register_satellite("test-key", password="test-password")
        b.start_all()

        try:
            client = _make_client(m.network_protocol.url, "test-key", "test-password")
            client.connect(site_id="loopback-site")
            client.wait_for_handshake(timeout=10)

            # Register handler on client's internal bus
            received = []
            client.internal_bus.on("speak", lambda msg: received.append(msg))

            # Send speak from hub to the connected client
            time.sleep(0.5)  # ensure registration completes
            peers = m.connected_peers()
            assert len(peers) > 0

            speak = HiveMessage(
                HiveMessageType.BUS,
                payload=Message("speak", {"utterance": "hello from hub"}),
            )
            m.send_to_satellite(peers[0], speak)

            # Wait for client to receive
            time.sleep(1)
            assert len(received) > 0, f"Client did not receive speak message"
            assert received[0].data["utterance"] == "hello from hub"
        finally:
            try:
                client.close()
            except Exception:
                pass
            b.stop_all()


class TestHiveMessageBusClientBinaryData:
    """Binary data transfer (audio frames, TTS)."""

    @pytest.mark.xfail(reason="Binary frame decode through loopback needs investigation")
    def test_send_raw_audio_to_hub(self):
        """Client sends RAW_AUDIO binary, hub's binary protocol receives it."""
        from hivemind_bus_client.message import HiveMindBinaryPayloadType

        b = TopologyBuilder()
        m = b.add_master("M0", use_loopback=True)
        m.register_satellite("test-key", password="test-password")
        b.start_all()

        try:
            client = _make_client(m.network_protocol.url, "test-key", "test-password")
            client.connect(site_id="loopback-site")
            client.wait_for_handshake(timeout=10)

            # Send binary audio frame
            audio_data = b"\x00\x01\x02\x03" * 100  # 400 bytes of fake audio
            bin_msg = HiveMessage(
                HiveMessageType.BINARY,
                payload=audio_data,
                bin_type=HiveMindBinaryPayloadType.RAW_AUDIO,
                metadata={"sample_rate": 16000, "sample_width": 2},
            )
            client.emit(bin_msg)

            time.sleep(1)

            calls = m.binary_protocol.calls
            audio_calls = [c for c in calls if c.bin_type.name == "RAW_AUDIO"]
            assert len(audio_calls) > 0, \
                f"No RAW_AUDIO calls on hub. All calls: {[c.bin_type for c in calls]}"
        finally:
            try:
                client.close()
            except Exception:
                pass
            b.stop_all()
