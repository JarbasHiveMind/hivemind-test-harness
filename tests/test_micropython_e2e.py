"""
End-to-end tests for MicroPython client against loopback harness master.

These tests use LoopbackNetworkProtocol (real WebSocket server on localhost:0)
to verify that the MicroPython HiveMindClient can perform full handshake,
encryption negotiation, and message exchange with an in-process hub.

Test IDs:
- MPY-E2E-01: MicroPython client handshake with loopback hub
- MPY-E2E-02: MicroPython client sends utterance, hub receives BUS message
- MPY-E2E-03: MicroPython client receives speak response from hub
- MPY-E2E-04: Multiple MicroPython clients in star topology
"""

import asyncio
import sys
from pathlib import Path

import pytest

# Import MicroPython client
_MP_CLIENT_ROOT = Path(__file__).resolve().parents[2] / "hivemind-micropython-client"
if str(_MP_CLIENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_MP_CLIENT_ROOT))

from hivemind.client import HiveMindClient, STATE_READY, STATE_DISCONNECTED
from hivemind_test_harness.topology import TopologyBuilder
from ovos_bus_client.message import Message
from hivemind_bus_client.message import HiveMessage, HiveMessageType


class TestMicroPythonE2E:
    """MicroPython client E2E tests using loopback harness."""

    @pytest.mark.asyncio
    async def test_micropython_handshake(self):
        """MPY-E2E-01: MicroPython client handshake with loopback hub.

        Verifies that the client can perform full HELLO -> SHAKE -> key derivation
        handshake and reach STATE_READY.
        """
        # Setup: Create topology with loopback master
        b = TopologyBuilder()
        m = b.add_master("M0", use_loopback=True)
        m.register_satellite("mpy-key", password="mpy-password")
        b.start_all()

        try:
            # Create MicroPython client
            client = HiveMindClient(
                host="127.0.0.1",
                port=int(m.network_protocol.url.split(":")[-1].rstrip("/")),
                username="mpy-sat",
                access_key="mpy-key",
                password="mpy-password",
            )

            # Connect and reach ready state
            assert client.state == STATE_DISCONNECTED
            await asyncio.wait_for(client.connect(), timeout=5)
            assert client.state == STATE_READY, f"Expected STATE_READY, got {client.state}"

        finally:
            b.stop_all()

    @pytest.mark.asyncio
    async def test_micropython_utterance_roundtrip(self):
        """MPY-E2E-02: MicroPython client sends utterance, hub receives BUS message.

        Verifies that a client can send an utterance and the hub records it
        as a BUS message type.
        """
        # Setup
        b = TopologyBuilder()
        m = b.add_master("M0", use_loopback=True)
        m.register_satellite("mpy-key", password="mpy-password")
        b.start_all()

        try:
            # Create and connect client
            client = HiveMindClient(
                host="127.0.0.1",
                port=int(m.network_protocol.url.split(":")[-1].rstrip("/")),
                username="mpy-sat",
                access_key="mpy-key",
                password="mpy-password",
            )
            await asyncio.wait_for(client.connect(), timeout=5)

            # Send utterance
            await asyncio.wait_for(
                client.send_utterance("hello world"),
                timeout=5
            )

            # Hub should have recorded a BUS message
            assert len(m.recorder.messages) > 0, "No messages recorded on hub"

            # Find the utterance message
            bus_messages = [
                msg for msg in m.recorder.messages
                if msg.msg_type == HiveMessageType.BUS
            ]
            assert len(bus_messages) > 0, "No BUS messages received"

            # Verify message is recognizer_loop:utterance
            first_bus = bus_messages[0]
            assert first_bus.payload is not None
            payload = first_bus.payload
            assert payload.msg_type == "recognizer_loop:utterance"

        finally:
            b.stop_all()

    @pytest.mark.asyncio
    async def test_micropython_receive_speak_response(self):
        """MPY-E2E-03: Hub sends speak message, client receives it.

        Verifies that the client can receive and process a speak response
        from the hub.
        """
        # Setup
        b = TopologyBuilder()
        m = b.add_master("M0", use_loopback=True)
        m.register_satellite("mpy-key", password="mpy-password")
        b.start_all()

        try:
            # Create client with message capture
            speak_messages = []

            def on_bus_message(msg_type: str, data: dict, context: dict):
                if msg_type == "speak":
                    speak_messages.append({
                        "type": msg_type,
                        "data": data,
                        "context": context,
                    })

            client = HiveMindClient(
                host="127.0.0.1",
                port=int(m.network_protocol.url.split(":")[-1].rstrip("/")),
                username="mpy-sat",
                access_key="mpy-key",
                password="mpy-password",
            )
            client.on_bus_message = on_bus_message
            await asyncio.wait_for(client.connect(), timeout=5)

            # Get the peer ID of the connected client
            peers = m.connected_peers()
            assert len(peers) > 0, "No connected peers on hub"
            peer = peers[0]

            # Send a speak message from hub to client
            speak_msg = Message("speak", {"utterance": "hello from hub"})
            hm_msg = HiveMessage(HiveMessageType.BUS, payload=speak_msg)
            m.send_to_satellite(peer, hm_msg)

            # Client should receive it
            await asyncio.wait_for(
                asyncio.sleep(1),  # Give client time to process
                timeout=5
            )
            assert len(speak_messages) > 0, "Client did not receive speak message"
            assert speak_messages[0]["type"] == "speak"

        finally:
            b.stop_all()

    @pytest.mark.asyncio
    async def test_multiple_micropython_clients(self):
        """MPY-E2E-04: Multiple MicroPython clients in star topology.

        Verifies that multiple clients can connect simultaneously and
        exchange messages independently.
        """
        # Setup
        b = TopologyBuilder()
        m = b.add_master("M0", use_loopback=True)
        m.register_satellite("mpy-1", password="pwd1")
        m.register_satellite("mpy-2", password="pwd2")
        b.start_all()

        try:
            port = int(m.network_protocol.url.split(":")[-1].rstrip("/"))

            # Create first client
            client1 = HiveMindClient(
                host="127.0.0.1",
                port=port,
                username="mpy-1",
                access_key="mpy-1",
                password="pwd1",
            )

            # Create second client
            client2 = HiveMindClient(
                host="127.0.0.1",
                port=port,
                username="mpy-2",
                access_key="mpy-2",
                password="pwd2",
            )

            # Connect both
            await asyncio.wait_for(
                asyncio.gather(
                    client1.connect(),
                    client2.connect(),
                ),
                timeout=5
            )

            assert client1.state == STATE_READY
            assert client2.state == STATE_READY

            # Hub should have two connected peers
            peers = m.connected_peers()
            assert len(peers) >= 2, f"Expected 2+ peers, got {len(peers)}"

            # Send utterances from both
            await asyncio.wait_for(
                asyncio.gather(
                    client1.send_utterance("message from client 1"),
                    client2.send_utterance("message from client 2"),
                ),
                timeout=5
            )

            # Hub should have recorded messages from both
            bus_messages = [
                msg for msg in m.recorder.messages
                if msg.msg_type == HiveMessageType.BUS
            ]
            assert len(bus_messages) >= 2, "Not all messages received on hub"

        finally:
            b.stop_all()
