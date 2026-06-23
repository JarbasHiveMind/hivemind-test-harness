"""
End-to-end tests for MicroPython client against loopback harness master.

Uses LoopbackNetworkProtocol (real WebSocket server on localhost:0) to verify
that the MicroPython HiveMindClient can perform handshake, encryption, and
message exchange with an in-process hub.

Test IDs:
- MPY-E2E-01: Handshake reaches STATE_READY
- MPY-E2E-02: Client sends utterance, hub receives BUS message
- MPY-E2E-03: Hub sends speak message, client receives it
- MPY-E2E-04: Multiple clients connect independently
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest


def _find_mp_client_root() -> Path:
    """Locate the hivemind-micropython-client checkout (see env override)."""
    env = os.environ.get("HIVEMIND_MICROPYTHON_CLIENT")
    if env:
        return Path(env).resolve()
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / "hivemind-micropython-client",
        here.parents[3] / "clients" / "hivemind-micropython-client",
        here.parents[2] / "clients" / "hivemind-micropython-client",
    ]
    for cand in candidates:
        if (cand / "hivemind" / "client.py").exists():
            return cand
    return candidates[0]


_MP_CLIENT_ROOT = _find_mp_client_root()
if str(_MP_CLIENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_MP_CLIENT_ROOT))

from hivemind.client import HiveMindClient, STATE_READY, STATE_DISCONNECTED
from hivescope.topology import TopologyBuilder
from ovos_bus_client.message import Message
from hivemind_bus_client.message import HiveMessage, HiveMessageType


def _extract_port(url: str) -> int:
    """Extract port from ws://127.0.0.1:PORT/."""
    return int(url.split(":")[-1].rstrip("/"))


async def _connect_and_wait(client: HiveMindClient, timeout: float = 10) -> None:
    """Launch client.connect() as background task and wait for STATE_READY."""
    task = asyncio.ensure_future(client.connect())
    try:
        deadline = asyncio.get_event_loop().time() + timeout
        while client.state != STATE_READY:
            if asyncio.get_event_loop().time() > deadline:
                raise TimeoutError(
                    f"Client did not reach STATE_READY within {timeout}s "
                    f"(stuck in state {client.state})"
                )
            await asyncio.sleep(0.1)
    except Exception:
        task.cancel()
        raise


class TestMicroPythonE2E:
    """MicroPython client E2E tests using loopback WebSocket harness."""

    @pytest.mark.asyncio
    async def test_micropython_handshake(self):
        """MPY-E2E-01: Client performs handshake and reaches STATE_READY."""
        b = TopologyBuilder()
        m = b.add_master("M0", use_loopback=True)
        m.register_satellite("mpy-key", password="mpy-password")
        b.start_all()

        try:
            client = HiveMindClient(
                host="127.0.0.1",
                port=_extract_port(m.network_protocol.url),
                username="mpy-sat",
                access_key="mpy-key",
                password="mpy-password",
                reconnect_ms=0,
            )
            assert client.state == STATE_DISCONNECTED
            await _connect_and_wait(client)
            assert client.state == STATE_READY
        finally:
            b.stop_all()

    @pytest.mark.asyncio
    async def test_micropython_utterance_roundtrip(self):
        """MPY-E2E-02: Client sends utterance, hub records BUS message."""
        b = TopologyBuilder()
        m = b.add_master("M0", use_loopback=True)
        # hivemind-core is whitelist-only: grant the type the client injects.
        m.register_satellite(
            "mpy-key", password="mpy-password",
            allowed_types=["recognizer_loop:utterance"],
        )
        b.start_all()

        try:
            client = HiveMindClient(
                host="127.0.0.1",
                port=_extract_port(m.network_protocol.url),
                username="mpy-sat",
                access_key="mpy-key",
                password="mpy-password",
                reconnect_ms=0,
            )
            await _connect_and_wait(client)
            await client.send_utterance("hello world")

            # Give hub time to process
            await asyncio.sleep(0.5)

            bus_msgs = [
                msg for msg in m.recorder.records
                if msg.msg_type == HiveMessageType.BUS
            ]
            assert len(bus_msgs) > 0, (
                f"No BUS messages on hub. All recorded: "
                f"{[(msg.msg_type, msg.direction) for msg in m.recorder.records]}"
            )
        finally:
            b.stop_all()

    @pytest.mark.asyncio
    async def test_micropython_receive_speak_response(self):
        """MPY-E2E-03: Hub sends speak, client receives it."""
        b = TopologyBuilder()
        m = b.add_master("M0", use_loopback=True)
        m.register_satellite("mpy-key", password="mpy-password")
        b.start_all()

        try:
            received = []

            def on_bus(msg_type: str, data: dict, context: dict):
                received.append({"type": msg_type, "data": data})

            client = HiveMindClient(
                host="127.0.0.1",
                port=_extract_port(m.network_protocol.url),
                username="mpy-sat",
                access_key="mpy-key",
                password="mpy-password",
                reconnect_ms=0,
            )
            client.on_bus_message = on_bus
            await _connect_and_wait(client)

            # Wait for client to be registered in master's clients dict
            await asyncio.sleep(0.5)
            peers = m.connected_peers()
            assert len(peers) > 0, "No peers connected"
            speak = HiveMessage(
                HiveMessageType.BUS,
                payload=Message("speak", {"utterance": "hello from hub"}),
            )
            m.send_to_satellite(peers[0], speak)

            # Wait for client to receive
            await asyncio.sleep(1)
            speak_msgs = [r for r in received if r["type"] == "speak"]
            assert len(speak_msgs) > 0, f"Client got no speak. Received: {received}"
        finally:
            b.stop_all()

    @pytest.mark.asyncio
    async def test_multiple_micropython_clients(self):
        """MPY-E2E-04: Two clients connect and send independently."""
        b = TopologyBuilder()
        m = b.add_master("M0", use_loopback=True)
        m.register_satellite(
            "mpy-1", password="pwd1",
            allowed_types=["recognizer_loop:utterance"],
        )
        m.register_satellite(
            "mpy-2", password="pwd2",
            allowed_types=["recognizer_loop:utterance"],
        )
        b.start_all()

        try:
            port = _extract_port(m.network_protocol.url)

            c1 = HiveMindClient(
                host="127.0.0.1", port=port,
                username="c1", access_key="mpy-1", password="pwd1",
                reconnect_ms=0,
            )
            c2 = HiveMindClient(
                host="127.0.0.1", port=port,
                username="c2", access_key="mpy-2", password="pwd2",
                reconnect_ms=0,
            )

            await asyncio.gather(
                _connect_and_wait(c1),
                _connect_and_wait(c2),
            )
            assert c1.state == STATE_READY
            assert c2.state == STATE_READY
            # Wait for HELLO processing to register clients
            await asyncio.sleep(0.5)
            assert len(m.connected_peers()) >= 2

            await c1.send_utterance("from client 1")
            await c2.send_utterance("from client 2")
            await asyncio.sleep(0.5)

            bus_msgs = [
                msg for msg in m.recorder.records
                if msg.msg_type == HiveMessageType.BUS
            ]
            assert len(bus_msgs) >= 2, f"Expected 2+ BUS, got {len(bus_msgs)}"
        finally:
            b.stop_all()
