"""
TestAgentProtocol — AgentProtocol backed by a FakeBus.

Records every Message injected into the 'OVOS bus' so tests can assert on it.

Reverse routing
---------------
In a live deployment the agent protocol (OVOSProtocol in ovos-bus-client/hpm.py)
subscribes to the OVOS bus and routes outgoing messages back to the originating
satellite by inspecting ``message.context["destination"]``.

TestAgentProtocol replicates this behaviour verbatim so that test assertions
against ``SatelliteNode.internal_bus`` match what a real satellite would receive.

Reference: ovos-bus-client/ovos_bus_client/hpm.py — OVOSProtocol.register_bus_handlers(),
           OVOSProtocol.handle_send(), OVOSProtocol.handle_internal_mycroft()
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ovos_bus_client.message import Message
from ovos_utils.fakebus import FakeBus
from ovos_utils.log import LOG

from hivemind_bus_client.message import HiveMessage, HiveMessageType
from hivemind_plugin_manager.protocols import AgentProtocol


@dataclass
class TestAgentProtocol(AgentProtocol):
    """AgentProtocol backed by FakeBus; records injected messages and provides
    downstream routing so satellites receive skill responses via HiveMind."""

    bus: FakeBus = field(default_factory=FakeBus)
    injected: List[Message] = field(default_factory=list)

    def __post_init__(self):
        _orig_emit = self.bus.emit

        def _recording_emit(msg):
            if isinstance(msg, str):
                try:
                    msg = Message.deserialize(msg)
                except Exception:
                    pass
            if isinstance(msg, Message):
                self.injected.append(msg)
            _orig_emit(msg)

        self.bus.emit = _recording_emit

        # Mirror OVOSProtocol.register_bus_handlers() — enables reverse routing
        # (OVOS bus messages → satellite) matching live deployment behaviour.
        self.register_bus_handlers()

    # -----------------------------------------------------------------------
    # Reverse routing — ported verbatim from
    # ovos-bus-client/ovos_bus_client/hpm.py (OVOSProtocol)
    # -----------------------------------------------------------------------

    def register_bus_handlers(self) -> None:
        """Subscribe to the agent bus for downstream routing.

        Exact port of OVOSProtocol.register_bus_handlers().
        Two paths:
        1. ``hive.send.downstream`` — explicit routing request from an OVOS component.
        2. ``message`` (catch-all) — route any message whose ``destination`` context
           matches a connected satellite peer back to that peer.
        """
        LOG.debug("TestAgentProtocol: registering bus handlers for downstream routing")
        self.bus.on("hive.send.downstream", self.handle_send)
        self.bus.on("message", self.handle_internal_mycroft)

    def handle_send(self, message: Message) -> None:
        """Route an explicit ``hive.send.downstream`` request.

        Exact port of OVOSProtocol.handle_send() from ovos-bus-client/hpm.py.
        """
        payload = message.data.get("payload")
        peer = message.data.get("peer")
        msg_type = message.data["msg_type"]

        hmessage = HiveMessage(msg_type, payload=payload, target_peers=[peer])

        if msg_type in [HiveMessageType.PROPAGATE, HiveMessageType.BROADCAST,
                        HiveMessageType.CASCADE]:
            # Broadcast to all connected satellites
            for p, client in self.clients.items():
                client.send(hmessage)
        elif msg_type in [HiveMessageType.ESCALATE, HiveMessageType.QUERY]:
            # Only slaves can escalate; ignore silently when we are the master
            pass
        elif peer:
            if peer in self.clients:
                self.clients[peer].send(hmessage)
            else:
                LOG.error(f"hive.send.downstream: peer '{peer}' is not connected")
                self.bus.emit(
                    message.forward(
                        "hive.client.send.error",
                        {"error": "That client is not connected", "peer": peer},
                    )
                )

    def handle_internal_mycroft(self, message: str) -> None:
        """Forward OVOS bus messages to satellite clients when they are the destination.

        Exact port of OVOSProtocol.handle_internal_mycroft() from ovos-bus-client/hpm.py.

        The ``message`` bus event carries the raw serialised JSON string (FakeBus
        emits ``ee.emit("message", message.serialize())``).  Client isolation is
        enforced here: each satellite only receives messages whose ``destination``
        context matches its peer ID.
        """
        message = Message.deserialize(message)
        target_peers = message.context.get("destination") or []
        if not isinstance(target_peers, list):
            target_peers = [target_peers]

        if target_peers:
            for peer, client in self.clients.items():
                if peer in target_peers:
                    LOG.debug(f"TestAgentProtocol: routing {message.msg_type} → {peer}")
                    message.context["source"] = "hive"
                    msg = HiveMessage(
                        HiveMessageType.BUS,
                        source_peer=peer,
                        target_peers=target_peers,
                        payload=message,
                    )
                    client.send(msg)

    # -----------------------------------------------------------------------
    # Assertion helpers
    # -----------------------------------------------------------------------

    def last_injected(self, msg_type: str) -> Optional[Message]:
        """Return the last Message of ``msg_type`` seen on the agent bus."""
        matches = [m for m in self.injected if m.msg_type == msg_type]
        return matches[-1] if matches else None

    def assert_injected(self, msg_type: str, count: int = 1):
        """Assert exactly ``count`` messages of ``msg_type`` on the agent bus."""
        matches = [m for m in self.injected if m.msg_type == msg_type]
        assert len(matches) == count, (
            f"Expected {count}x '{msg_type}' on agent bus, got {len(matches)}. "
            f"All injected: {[m.msg_type for m in self.injected]}"
        )

    def assert_not_injected(self, msg_type: str):
        """Assert ``msg_type`` was never emitted on the agent bus."""
        matches = [m for m in self.injected if m.msg_type == msg_type]
        assert not matches, (
            f"Expected '{msg_type}' NOT on agent bus, but got {len(matches)}."
        )

    def clear(self):
        """Reset all recorded messages."""
        self.injected.clear()
