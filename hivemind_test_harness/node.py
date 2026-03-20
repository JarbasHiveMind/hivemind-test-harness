"""
MasterNode and SatelliteNode — the two actor types in the test harness.

InProcessHiveShim acts as the HiveMessageBusClient-compatible object that
HiveMindSlaveProtocol requires, without any WebSocket involvement.
"""
import json
import threading
from typing import Any, Dict, List, Optional, Union
from uuid import uuid4

from ovos_bus_client.message import Message
from ovos_bus_client.session import Session
from ovos_utils.fakebus import FakeBus
from pyee.base import EventEmitter

from hivemind_bus_client.encryption import SupportedEncodings, SupportedCiphers
from hivemind_bus_client.identity import NodeIdentity
from hivemind_bus_client.message import HiveMessage, HiveMessageType
from hivemind_bus_client.serialization import decode_bitstring  # used in _instrument_master
from hivemind_core.protocol import (
    HiveMindClientConnection,
    HiveMindListenerProtocol,
)
from hivemind_plugin_manager.protocols import ClientCallbacks

from hivemind_test_harness.database import InMemoryClientDatabase
from hivemind_test_harness.plugins.agent import TestAgentProtocol
from hivemind_test_harness.plugins.binary import TestBinaryProtocol
from hivemind_test_harness.plugins.network import TestNetworkProtocol
from hivemind_test_harness.recorder import MessageRecorder, RecordedMessage
from hivemind_test_harness.utils import make_identity


# ---------------------------------------------------------------------------
# InProcessHiveShim
# ---------------------------------------------------------------------------

class InProcessHiveShim:
    """
    Minimal stand-in for HiveMessageBusClient as required by HiveMindSlaveProtocol.

    - emit(HiveMessage)  → routes upstream to the master's handle_message()
    - on(event, func)    → registers on the internal EventEmitter
    - emitter            → the EventEmitter that dispatches inbound messages
    - crypto_key, cipher, json_encoding, handshake_event, session_id  — all
      the attributes SlaveProtocol sets/reads during the handshake
    """

    def __init__(self, identity: NodeIdentity, satellite_ref: "SatelliteNode"):
        self.identity = identity
        self._satellite = satellite_ref
        self.emitter = EventEmitter()
        self.crypto_key: Optional[str] = None
        self.json_encoding: SupportedEncodings = SupportedEncodings.JSON_HEX
        self.cipher: SupportedCiphers = SupportedCiphers.AES_GCM
        self.handshake_event = threading.Event()
        self._session_id: str = str(uuid4())

    # --- properties accessed by HiveMindSlaveProtocol ---

    @property
    def useragent(self) -> str:
        return self.identity.name

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def site_id(self) -> Optional[str]:
        return self.identity.site_id

    @site_id.setter
    def site_id(self, val: str):
        self.identity.site_id = val

    # --- message API ---

    def emit(self, message: Union[HiveMessage, Message]):
        """Send a HiveMessage upstream to the master."""
        if isinstance(message, Message):
            message = HiveMessage(HiveMessageType.BUS, payload=message)
        self._satellite.send(message)

    def on(self, event_name: str, func):
        """Register an inbound-message handler."""
        self.emitter.on(event_name, func)

    def remove(self, event_name: str, func):
        self.emitter.remove_listener(event_name, func)


# ---------------------------------------------------------------------------
# MasterNode
# ---------------------------------------------------------------------------

class MasterNode:
    """
    Wraps a HiveMindListenerProtocol with TestAgentProtocol, TestBinaryProtocol,
    and TestNetworkProtocol.  No network server is started.
    """

    def __init__(self,
                 name: str,
                 identity: NodeIdentity,
                 db: InMemoryClientDatabase,
                 agent_protocol: TestAgentProtocol,
                 binary_protocol: TestBinaryProtocol,
                 network_protocol: TestNetworkProtocol,
                 hm_protocol: HiveMindListenerProtocol,
                 recorder: MessageRecorder):
        self.name = name
        self.identity = identity
        self.db = db
        self.agent_protocol = agent_protocol
        self.binary_protocol = binary_protocol
        self.network_protocol = network_protocol
        self.hm_protocol = hm_protocol
        self.recorder = recorder

    @classmethod
    def create(cls,
               name: str,
               require_crypto: bool = True,
               handshake_enabled: bool = True,
               agent_protocol: "TestAgentProtocol" = None,
               use_loopback: bool = False,
               **kwargs) -> "MasterNode":
        identity = make_identity(name)
        db = InMemoryClientDatabase()
        agent = agent_protocol if agent_protocol is not None else TestAgentProtocol()
        binary = TestBinaryProtocol(agent_protocol=agent)
        hm_proto = HiveMindListenerProtocol(
            identity=identity,
            db=db,
            agent_protocol=agent,
            binary_data_protocol=binary,
            require_crypto=require_crypto,
            handshake_enabled=handshake_enabled,
            peer=f"{name}:0.0.0.0",
        )
        # Use LoopbackNetworkProtocol (real WebSocket) if use_loopback=True,
        # otherwise use TestNetworkProtocol (in-process wiring)
        if use_loopback:
            from hivemind_test_harness.plugins.loopback import LoopbackNetworkProtocol
            network = LoopbackNetworkProtocol(hm_protocol=hm_proto)
        else:
            network = TestNetworkProtocol(hm_protocol=hm_proto)
        recorder = MessageRecorder(name=name)
        _instrument_master(hm_proto, recorder)
        return cls(name=name, identity=identity, db=db,
                   agent_protocol=agent, binary_protocol=binary,
                   network_protocol=network, hm_protocol=hm_proto,
                   recorder=recorder)

    # --- satellite management ---

    def register_satellite(self,
                           key: str,
                           password: Optional[str] = None,
                           is_admin: bool = False,
                           can_escalate: bool = True,
                           can_propagate: bool = True,
                           can_broadcast: bool = True,
                           allowed_types: Optional[List[str]] = None,
                           msg_blacklist: Optional[List[str]] = None,
                           skill_blacklist: Optional[List[str]] = None,
                           intent_blacklist: Optional[List[str]] = None,
                           crypto_key: Optional[str] = None):
        """Pre-populate the DB so a satellite with this key can connect."""
        self.db.add_client(
            name="test-satellite",
            key=key,
            password=password,
            admin=is_admin,
            can_escalate=can_escalate,
            can_propagate=can_propagate,
            can_broadcast=can_broadcast,
            allowed_types=allowed_types,
            message_blacklist=msg_blacklist,
            skill_blacklist=skill_blacklist,
            intent_blacklist=intent_blacklist,
            crypto_key=crypto_key,
        )

    # --- sending to connected satellites ---

    def send_to_satellite(self, peer: str, message: HiveMessage):
        """Directly send a HiveMessage to a connected satellite by peer id."""
        conn = self.hm_protocol.clients.get(peer)
        if conn is None:
            raise KeyError(
                f"No connected client with peer '{peer}'. "
                f"Connected peers: {list(self.hm_protocol.clients)}"
            )
        conn.send(message)

    def send_to_all(self, message: HiveMessage):
        """Broadcast a HiveMessage to all currently connected satellites."""
        for peer, conn in list(self.hm_protocol.clients.items()):
            conn.send(message)

    def emit_on_bus(self, message: Message):
        """Emit an OVOS message on the internal agent bus (simulates a skill response)."""
        self.agent_protocol.bus.emit(message)

    # --- waiting / assertion ---

    def wait_for(self, msg_type: str,
                 direction: Optional[str] = None,
                 timeout: float = 5.0) -> Optional[RecordedMessage]:
        return self.recorder.wait_for(msg_type, direction=direction, timeout=timeout)

    def connected_peers(self) -> List[str]:
        return list(self.hm_protocol.clients.keys())


# ---------------------------------------------------------------------------
# SatelliteNode
# ---------------------------------------------------------------------------

class SatelliteNode:
    """
    Wraps HiveMindSlaveProtocol via an InProcessHiveShim.
    No WebSocket client is created.
    """

    def __init__(self,
                 name: str,
                 identity: NodeIdentity,
                 internal_bus: FakeBus,
                 shim: InProcessHiveShim,
                 slave_protocol,            # HiveMindSlaveProtocol
                 recorder: MessageRecorder):
        self.name = name
        self.identity = identity
        self.internal_bus = internal_bus
        self.shim = shim
        self.slave_protocol = slave_protocol
        self.recorder = recorder

        # Set by connect()
        self._connection: Optional[HiveMindClientConnection] = None
        self._master: Optional[MasterNode] = None

    @classmethod
    def create(cls, name: str, site_id: Optional[str] = None,
               shared_bus: bool = False,
               bus: Optional[FakeBus] = None) -> "SatelliteNode":
        # Import here to avoid circular import at module level
        from hivemind_bus_client.protocol import HiveMindSlaveProtocol

        identity = make_identity(name, site_id=site_id or f"{name}-site")
        bus = bus or FakeBus()
        recorder = MessageRecorder(name=name)

        # shim acts as the HiveMessageBusClient for the slave protocol
        sat_ref_holder = [None]  # forward reference trick
        shim = InProcessHiveShim(identity=identity, satellite_ref=None)

        slave = HiveMindSlaveProtocol(
            hm=shim,
            identity=identity,
            shared_bus=shared_bus,
            site_id=identity.site_id or "unknown",
        )
        slave.bind(bus)
        # Propagate shared_bus to the internal protocol (HiveMindSlaveProtocol.bind()
        # creates internal_protocol but does not forward shared_bus to it)
        slave.internal_protocol.share_bus = shared_bus

        node = cls(name=name, identity=identity, internal_bus=bus,
                   shim=shim, slave_protocol=slave, recorder=recorder)
        shim._satellite = node  # now wire the back-reference
        return node

    # --- connection ---

    def connect(self, master: MasterNode,
                is_admin: bool = False,
                can_escalate: bool = True,
                can_propagate: bool = True,
                can_broadcast: bool = True,
                allowed_types: Optional[List[str]] = None,
                msg_blacklist: Optional[List[str]] = None,
                skill_blacklist: Optional[List[str]] = None,
                intent_blacklist: Optional[List[str]] = None):
        """
        Wire this satellite to master in-process and complete the handshake.
        After this call the satellite is fully connected and crypto is active.
        """
        # Store master reference BEFORE connect_satellite so that send()
        # works during the synchronous handshake exchange that follows.
        self._master = master

        master.register_satellite(
            key=self.identity.access_key,
            password=self.identity.password,
            is_admin=is_admin,
            can_escalate=can_escalate,
            can_propagate=can_propagate,
            can_broadcast=can_broadcast,
            allowed_types=allowed_types,
            msg_blacklist=msg_blacklist,
            skill_blacklist=skill_blacklist,
            intent_blacklist=intent_blacklist,
        )

        # connect_satellite sets self._connection and calls handle_new_client;
        # the password handshake completes synchronously inside that call.
        master.network_protocol.connect_satellite(satellite=self)

        # For RSA-only mode (no password), slave never calls start_handshake
        # automatically — do it here as the real client would via wait_for_handshake.
        if not self.shim.handshake_event.is_set():
            self.slave_protocol.start_handshake()

        if not self.shim.handshake_event.is_set():
            raise RuntimeError(
                f"Handshake did not complete for satellite {self.name!r}. "
                "Ensure the satellite has a password set (password-based handshake) "
                "or that the master has handshake_enabled=True (RSA handshake)."
            )

    def disconnect(self):
        """Disconnect from the master."""
        if self._connection and self._master:
            self._master.hm_protocol.handle_client_disconnected(self._connection)

    # --- sending ---

    def send(self, message: Union[HiveMessage, Message]):
        """Send a message upstream to the connected master."""
        if isinstance(message, Message):
            # Inject session so master doesn't reject it as the 'default' session
            if "session" not in message.context:
                sess = Session(session_id=self.shim.session_id,
                               site_id=self.identity.site_id or "unknown")
                message.context["session"] = sess.serialize()
            message = HiveMessage(HiveMessageType.BUS, payload=message)
        self.recorder.record("out", message.msg_type, message.payload, "master")
        if self._master is None or self._connection is None:
            raise RuntimeError(
                f"Satellite {self.name!r} is not connected to any master."
            )
        self._master.hm_protocol.handle_message(message, self._connection)

    # --- receiving (called by master's send_msg) ---

    def _receive_raw(self, payload: Union[str, bytes], is_binary: bool):
        """
        Called by HiveMindClientConnection.send_msg when master sends downstream.
        Decodes, records, then dispatches through the slave protocol's handlers.
        """
        if self._connection is None:
            # Connection not yet established (shouldn't normally happen)
            return

        try:
            message = self._connection.decode(payload)
        except Exception as exc:
            import traceback
            print(f"[{self.name}] _receive_raw decode error: {exc}")
            traceback.print_exc()
            return

        self.recorder.record("in", message.msg_type, message.payload,
                             self._connection.peer if self._connection else "master")

        # Dispatch through the slave protocol's registered handlers
        self.shim.emitter.emit(message.msg_type, message)

    def _on_disconnect(self):
        conn = self._connection
        master = self._master
        self._connection = None
        self._master = None
        # Mirror production behavior: WebSocket on_close → handle_client_disconnected.
        # Guard against re-entry (handle_client_disconnected calls client.disconnect()
        # at the end, which would call _on_disconnect again; by the time that second
        # call arrives, _connection/_master are already None so the guard is False).
        if conn and master and conn.peer in master.hm_protocol.clients:
            master.hm_protocol.handle_client_disconnected(conn)

    # --- waiting / assertion ---

    def wait_for(self, msg_type: str,
                 direction: str = "in",
                 timeout: float = 5.0) -> Optional[RecordedMessage]:
        return self.recorder.wait_for(msg_type, direction=direction, timeout=timeout)

    def wait_for_bus(self, ovos_type: str, timeout: float = 5.0) -> Optional[Message]:
        """Wait for an OVOS message type to arrive on the internal bus."""
        event = threading.Event()
        result: List[Message] = []

        def handler(msg):
            result.append(msg)
            event.set()

        self.internal_bus.once(ovos_type, handler)
        event.wait(timeout=timeout)
        return result[0] if result else None

    @property
    def peer(self) -> Optional[str]:
        return self._connection.peer if self._connection else None


# ---------------------------------------------------------------------------
# Instrumentation helpers
# ---------------------------------------------------------------------------

def _instrument_master(hm_proto: HiveMindListenerProtocol,
                       recorder: MessageRecorder):
    """
    Monkey-patch HiveMindListenerProtocol to record every inbound message
    and every outbound send to a satellite.
    """
    _orig_handle = hm_proto.handle_message

    def _recording_handle(message: HiveMessage, client: HiveMindClientConnection):
        recorder.record("in", message.msg_type, message.payload, client.peer)
        _orig_handle(message, client)

    hm_proto.handle_message = _recording_handle

    _orig_new_client = hm_proto.handle_new_client

    def _recording_new_client(client: HiveMindClientConnection):
        # Wrap send_msg so outbound messages are recorded with actual HiveMessage type.
        # We do this before handle_new_client so the HELLO/HANDSHAKE are captured.
        _orig_send_msg = client.send_msg

        def _recording_send_msg(payload, is_bin):
            # Best-effort decode to get msg_type for the record.
            try:
                if isinstance(payload, bytes):
                    msg = decode_bitstring(payload)
                    msg_type = msg.msg_type
                else:
                    if "ciphertext" in payload:
                        msg_type = "_encrypted"
                    else:
                        parsed = json.loads(payload)
                        msg_type = parsed.get("msg_type", "_unknown")
            except Exception:
                msg_type = "_raw"
            recorder.record("out", msg_type, payload, client.peer)
            _orig_send_msg(payload, is_bin)

        client.send_msg = _recording_send_msg
        _orig_new_client(client)

    hm_proto.handle_new_client = _recording_new_client

    # Record bus injections separately
    _orig_inject = hm_proto.handle_inject_agent_msg

    def _recording_inject(message: Message, client: HiveMindClientConnection):
        recorder.record("bus_inject", message.msg_type, message, client.peer)
        _orig_inject(message, client)

    hm_proto.handle_inject_agent_msg = _recording_inject
