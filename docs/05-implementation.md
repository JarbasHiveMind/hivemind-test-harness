# Implementation Plan

## Repository Layout

```
hivemind-test-harness/
├── README.md
├── docs/
│   └── ...
├── pyproject.toml
├── requirements-dev.txt
│
├── hivemind_test_harness/          ← installable package (the harness itself)
│   ├── __init__.py
│   │
│   ├── plugins/                    ← the three test plugin implementations
│   │   ├── __init__.py
│   │   ├── agent.py                # TestAgentProtocol(AgentProtocol)
│   │   ├── network.py              # TestNetworkProtocol(NetworkProtocol)
│   │   └── binary.py              # TestBinaryProtocol(BinaryDataHandlerProtocol)
│   │
│   ├── recorder.py                 # MessageRecorder, RecordedMessage
│   ├── node.py                     # MasterNode, SatelliteNode
│   ├── topology.py                 # TopologyBuilder + pre-built named topologies
│   └── utils.py                    # make_identity(), make_db(), etc.
│
└── tests/
    ├── conftest.py                  # pytest fixtures (all topologies)
    ├── test_handshake.py
    ├── test_bus.py
    ├── test_shared_bus.py
    ├── test_broadcast.py
    ├── test_propagate.py
    ├── test_escalate.py
    ├── test_intercom.py
    ├── test_binary.py
    ├── test_ping_cascade_query.py
    ├── test_acl.py
    ├── test_session.py
    ├── test_routes.py
    └── test_stress.py
```

---

## Plugin Implementations

### `TestAgentProtocol` (`plugins/agent.py`)

Production-parity agent protocol: records injected messages AND implements reverse routing
(OVOS bus → satellite), ported verbatim from `OVOSProtocol` in
`ovos-bus-client/ovos_bus_client/hpm.py`.

```python
from dataclasses import dataclass, field
from typing import List, Optional
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
        # Mirror OVOSProtocol.register_bus_handlers(): enables reverse routing
        self.register_bus_handlers()

    # -----------------------------------------------------------------------
    # Reverse routing: ported verbatim from OVOSProtocol in hpm.py
    # -----------------------------------------------------------------------

    def register_bus_handlers(self) -> None:
        """Subscribe to the agent bus for downstream routing.

        Exact port of OVOSProtocol.register_bus_handlers().
        Two paths:
        1. ``hive.send.downstream``: explicit routing request from an OVOS component.
        2. ``message`` (catch-all): route any message whose ``destination`` context
           matches a connected satellite peer back to that peer.
        """
        self.bus.on("hive.send.downstream", self.handle_send)
        self.bus.on("message", self.handle_internal_mycroft)

    def handle_send(self, message: Message) -> None:
        """Exact port of OVOSProtocol.handle_send()."""
        payload = message.data.get("payload")
        peer = message.data.get("peer")
        msg_type = message.data["msg_type"]
        hmessage = HiveMessage(msg_type, payload=payload, target_peers=[peer])
        if msg_type in [HiveMessageType.PROPAGATE, HiveMessageType.BROADCAST]:
            for p, client in self.clients.items():
                client.send(hmessage)
        elif msg_type == HiveMessageType.ESCALATE:
            pass
        elif peer:
            if peer in self.clients:
                self.clients[peer].send(hmessage)

    def handle_internal_mycroft(self, message: str) -> None:
        """Forward OVOS bus messages to satellite clients when they are the destination.

        Exact port of OVOSProtocol.handle_internal_mycroft().
        The ``message`` bus event carries the raw serialised JSON string.
        """
        message = Message.deserialize(message)
        target_peers = message.context.get("destination") or []
        if not isinstance(target_peers, list):
            target_peers = [target_peers]
        if target_peers:
            for peer, client in self.clients.items():
                if peer in target_peers:
                    message.context["source"] = "hive"
                    msg = HiveMessage(HiveMessageType.BUS, source_peer=peer,
                                     target_peers=target_peers, payload=message)
                    client.send(msg)

    # -----------------------------------------------------------------------
    # Assertion helpers
    # -----------------------------------------------------------------------

    def last_injected(self, msg_type: str) -> Optional[Message]:
        matches = [m for m in self.injected if m.msg_type == msg_type]
        return matches[-1] if matches else None

    def assert_injected(self, msg_type: str, count: int = 1):
        matches = [m for m in self.injected if m.msg_type == msg_type]
        assert len(matches) == count, (
            f"Expected {count}x '{msg_type}' on agent bus, got {len(matches)}"
        )

    def assert_not_injected(self, msg_type: str):
        matches = [m for m in self.injected if m.msg_type == msg_type]
        assert not matches, f"Expected '{msg_type}' NOT on agent bus, but got {len(matches)}"

    def clear(self):
        self.injected.clear()
```

### `TestNetworkProtocol` (`plugins/network.py`)

```python
from dataclasses import dataclass, field
from hivemind_plugin_manager.protocols import NetworkProtocol
from hivemind_core.protocol import HiveMindClientConnection, HiveMindListenerProtocol


@dataclass
class TestNetworkProtocol(NetworkProtocol):
    """
    NetworkProtocol with no sockets.
    Provides connect_satellite() to wire nodes in-process.
    """

    def run(self):
        # No server to start: topology wiring happens through connect_satellite()
        pass

    def connect_satellite(self,
                          satellite: 'SatelliteNode',
                          db_key: str) -> HiveMindClientConnection:
        """
        Create a HiveMindClientConnection whose send_msg routes directly to
        satellite._receive_raw(). Then trigger the full server-side
        connection lifecycle (HELLO + HANDSHAKE).

        Returns the connection object so the satellite can call
        hm_protocol.handle_message() on it directly.
        """
        hm_proto: HiveMindListenerProtocol = self.hm_protocol

        client_conn = HiveMindClientConnection(
            key=db_key,
            send_msg=satellite._receive_raw,   # master → satellite in-process delivery
            disconnect=satellite._on_disconnect,
            hm_protocol=hm_proto,
        )

        # Triggers HELLO + HANDSHAKE to be sent (via send_msg → satellite._receive_raw)
        hm_proto.handle_new_client(client_conn)
        return client_conn
```

### `TestBinaryProtocol` (`plugins/binary.py`)

```python
from dataclasses import dataclass, field
from typing import List, Any
from hivemind_plugin_manager.protocols import BinaryDataHandlerProtocol


@dataclass
class BinaryCall:
    handler: str
    data: bytes
    meta: dict = field(default_factory=dict)


@dataclass
class TestBinaryProtocol(BinaryDataHandlerProtocol):
    """
    Records all binary handler invocations so tests can assert on them.
    """
    calls: List[BinaryCall] = field(default_factory=list)

    def handle_microphone_input(self, bin_data, sample_rate, sample_width, client):
        self.calls.append(BinaryCall("microphone_input", bin_data,
                                     {"sample_rate": sample_rate, "sample_width": sample_width}))

    def handle_stt_transcribe_request(self, bin_data, sample_rate, sample_width, lang, client):
        self.calls.append(BinaryCall("stt_transcribe", bin_data,
                                     {"lang": lang, "sample_rate": sample_rate}))

    def handle_stt_handle_request(self, bin_data, sample_rate, sample_width, lang, client):
        self.calls.append(BinaryCall("stt_handle", bin_data,
                                     {"lang": lang, "sample_rate": sample_rate}))

    def handle_numpy_image(self, bin_data, camera_id, client):
        self.calls.append(BinaryCall("numpy_image", bin_data, {"camera_id": camera_id}))

    def handle_receive_tts(self, bin_data, utterance, lang, file_name, client):
        self.calls.append(BinaryCall("receive_tts", bin_data,
                                     {"utterance": utterance, "lang": lang, "file_name": file_name}))

    def handle_receive_file(self, bin_data, file_name, client):
        self.calls.append(BinaryCall("receive_file", bin_data, {"file_name": file_name}))

    # --- assertion helpers ---

    def assert_called(self, handler: str, count: int = 1):
        matches = [c for c in self.calls if c.handler == handler]
        assert len(matches) == count, (
            f"Expected {count}x '{handler}', got {len(matches)}: {self.calls}"
        )

    def last_call(self, handler: str) -> BinaryCall | None:
        matches = [c for c in self.calls if c.handler == handler]
        return matches[-1] if matches else None

    def clear(self):
        self.calls.clear()
```

---

## `MasterNode` (`node.py`)

```python
@dataclass
class MasterNode:
    name: str
    identity: NodeIdentity
    db: ClientDatabase
    agent_protocol: TestAgentProtocol
    binary_protocol: TestBinaryProtocol
    network_protocol: TestNetworkProtocol
    hm_protocol: HiveMindListenerProtocol
    recorder: MessageRecorder

    @classmethod
    def create(cls, name: str, **kwargs) -> 'MasterNode':
        identity = make_identity(name)
        db = make_test_db()
        agent = TestAgentProtocol()
        binary = TestBinaryProtocol(agent_protocol=agent)
        hm_proto = HiveMindListenerProtocol(
            identity=identity,
            db=db,
            agent_protocol=agent,
            binary_data_protocol=binary,
            **kwargs,
        )
        network = TestNetworkProtocol(hm_protocol=hm_proto)
        recorder = MessageRecorder(name=name)
        # instrument hm_proto
        _instrument_master(hm_proto, recorder)
        return cls(name=name, identity=identity, db=db,
                   agent_protocol=agent, binary_protocol=binary,
                   network_protocol=network, hm_protocol=hm_proto,
                   recorder=recorder)

    def register_satellite(self,
                           key: str,
                           password: str = None,
                           is_admin: bool = False,
                           can_escalate: bool = True,
                           can_propagate: bool = True,
                           allowed_types: List[str] = None,
                           msg_blacklist: List[str] = None,
                           skill_blacklist: List[str] = None,
                           intent_blacklist: List[str] = None) -> None:
        """Pre-populate db so the satellite with this key can connect."""
        with self.db as db:
            db.add_client(
                name="test-satellite",
                access_key=key,
                password=password,
                is_admin=is_admin,
                can_escalate=can_escalate,
                can_propagate=can_propagate,
                allowed_types=allowed_types or [],
                message_blacklist=msg_blacklist or [],
                skill_blacklist=skill_blacklist or [],
                intent_blacklist=intent_blacklist or [],
            )

    def send_to_satellite(self, peer: str, message: HiveMessage) -> None:
        """Directly call send() on a connected client connection."""
        conn = self.hm_protocol.clients.get(peer)
        if conn is None:
            raise KeyError(f"No connected client with peer '{peer}'")
        conn.send(message)

    def emit_on_bus(self, message: Message) -> None:
        """Simulate an OVOS skill emitting a response."""
        self.agent_protocol.bus.emit(message)

    def wait_for(self, msg_type: str,
                 direction: str = "in",
                 timeout: float = 5.0) -> RecordedMessage:
        return self.recorder.wait_for(msg_type, direction=direction, timeout=timeout)
```

---

## `SatelliteNode` (`node.py`)

```python
@dataclass
class SatelliteNode:
    name: str
    identity: NodeIdentity
    internal_bus: FakeBus
    slave_protocol: HiveMindSlaveProtocol
    recorder: MessageRecorder

    # set after connect()
    _connection: HiveMindClientConnection = None
    _master: MasterNode = None

    @classmethod
    def create(cls, name: str, **kwargs) -> 'SatelliteNode':
        identity = make_identity(name)
        bus = FakeBus()
        recorder = MessageRecorder(name=name)
        # HiveMindSlaveProtocol needs a HiveMessageBusClient-like object
        # We provide a minimal shim that routes through the recorder
        hm_shim = _InProcessHiveShim(identity=identity, recorder=recorder)
        slave = HiveMindSlaveProtocol(hm=hm_shim, identity=identity)
        slave.bind(bus)
        return cls(name=name, identity=identity, internal_bus=bus,
                   slave_protocol=slave, recorder=recorder)

    def connect(self, master: MasterNode) -> None:
        """Wire this satellite to master in-process and complete handshake."""
        master.register_satellite(key=self.identity.access_key,
                                   password=self.identity.password)
        conn = master.network_protocol.connect_satellite(self, self.identity.access_key)
        # Satellite processes the queued HELLO + HANDSHAKE it received via _receive_raw
        # (those calls already fired synchronously during handle_new_client)
        self._connection = conn
        self._master = master

    def send(self, message: Union[HiveMessage, Message]) -> None:
        """Send a message upstream to the connected master."""
        if isinstance(message, Message):
            message = HiveMessage(HiveMessageType.BUS, payload=message)
        self.recorder.record("out", message.msg_type, message.payload, "self")
        # Deliver directly to master's protocol, no socket involved
        self._master.hm_protocol.handle_message(message, self._connection)

    def _receive_raw(self, payload: Union[str, bytes], is_binary: bool) -> None:
        """Called by master's send_msg when sending downstream to this satellite."""
        # Decrypt using connection's crypto_key (same path as real client)
        message = self._connection.decode(payload)
        self.recorder.record("in", message.msg_type, message.payload,
                              self._connection.peer if self._connection else "master")
        # Dispatch through slave protocol handlers (handle_bus, handle_broadcast, etc.)
        self.slave_protocol.hm.emitter.emit(message.msg_type, message)

    def _on_disconnect(self) -> None:
        self._connection = None

    def wait_for(self, msg_type: str, timeout: float = 5.0) -> RecordedMessage:
        return self.recorder.wait_for(msg_type, direction="in", timeout=timeout)

    def wait_for_bus(self, ovos_type: str, timeout: float = 5.0) -> Message:
        """Wait for an OVOS message to arrive on internal_bus."""
        event = threading.Event()
        result = []
        def handler(msg):
            result.append(msg)
            event.set()
        self.internal_bus.once(ovos_type, handler)
        event.wait(timeout=timeout)
        return result[0] if result else None
```

---

## `MessageRecorder` (`recorder.py`)

```python
@dataclass
class RecordedMessage:
    direction: str   # "in" | "out" | "bus_inject"
    msg_type: str
    payload: Any
    peer: str
    timestamp: float = field(default_factory=time.monotonic)

class MessageRecorder:
    def __init__(self, name: str):
        self.name = name
        self.records: List[RecordedMessage] = []
        self._waiters: Dict[str, List[threading.Event]] = defaultdict(list)
        self._lock = threading.Lock()

    def record(self, direction, msg_type, payload, peer):
        entry = RecordedMessage(direction, msg_type, payload, peer)
        with self._lock:
            self.records.append(entry)
            for ev in self._waiters.get(msg_type, []):
                ev.set()

    def wait_for(self, msg_type, direction=None, timeout=5.0) -> RecordedMessage | None:
        # Check if already recorded
        existing = self._find(msg_type, direction)
        if existing:
            return existing
        ev = threading.Event()
        with self._lock:
            self._waiters[msg_type].append(ev)
        ev.wait(timeout=timeout)
        with self._lock:
            self._waiters[msg_type].remove(ev)
        return self._find(msg_type, direction)

    def assert_received(self, msg_type, count=1, direction=None):
        matches = self._find_all(msg_type, direction)
        assert len(matches) == count, (
            f"[{self.name}] Expected {count}x '{msg_type}' (dir={direction}), got {len(matches)}"
        )

    def assert_not_received(self, msg_type, direction=None):
        matches = self._find_all(msg_type, direction)
        assert not matches, (
            f"[{self.name}] Expected '{msg_type}' NOT received, but got {len(matches)}"
        )

    def _find(self, msg_type, direction) -> RecordedMessage | None:
        results = self._find_all(msg_type, direction)
        return results[-1] if results else None

    def _find_all(self, msg_type, direction) -> List[RecordedMessage]:
        with self._lock:
            return [r for r in self.records
                    if r.msg_type == msg_type and (direction is None or r.direction == direction)]

    def clear(self):
        with self._lock:
            self.records.clear()
```

---

## `TopologyBuilder` (`topology.py`)

```python
class TopologyBuilder:
    def __init__(self):
        self._masters: Dict[str, MasterNode] = {}
        self._satellites: Dict[str, SatelliteNode] = {}
        self._connections: List[Tuple[str, str]] = []  # (satellite_name, master_name)

    def add_master(self, name: str, **kwargs) -> MasterNode:
        node = MasterNode.create(name, **kwargs)
        self._masters[name] = node
        return node

    def add_satellite(self, name: str, upstream: MasterNode, **kwargs) -> SatelliteNode:
        node = SatelliteNode.create(name, **kwargs)
        self._satellites[name] = node
        self._connections.append((name, upstream.name))
        return node

    def add_relay(self, name: str, upstream: MasterNode | RelayNode,
                  **connect_kwargs) -> RelayNode:
        """Node that is both satellite (to upstream) and master (to its own downstreams).

        Returns a RelayNode. Read ``relay.listener`` for the master side that
        downstream satellites and relays attach to.
        """
        ...

    def start_all(self):
        """Connect all satellites to their masters in-process."""
        for sat_name, master_name in self._connections:
            sat = self._satellites[sat_name]
            master = self._masters[master_name]
            sat.connect(master)

    def stop_all(self):
        """Disconnect all satellites and clean up."""
        for sat in self._satellites.values():
            if sat._connection:
                sat._master.hm_protocol.handle_client_disconnected(sat._connection)

    def get_master(self, name: str) -> MasterNode:
        return self._masters[name]

    def get_satellite(self, name: str) -> SatelliteNode:
        return self._satellites[name]
```

---

## Instrumentation Hooks

Master instrumentation (wraps `handle_message` and `send_msg`):

```python
def _instrument_master(hm_proto: HiveMindListenerProtocol, recorder: MessageRecorder):
    _orig_handle = hm_proto.handle_message

    def _recorded_handle(message, client):
        recorder.record("in", message.msg_type, message.payload, client.peer)
        _orig_handle(message, client)

    hm_proto.handle_message = _recorded_handle

    # Wrap send_msg for all new clients
    _orig_new_client = hm_proto.handle_new_client

    def _recorded_new_client(client):
        _orig_send = client.send_msg

        def _recorded_send(payload, is_bin):
            # Decode msg_type for recording (best-effort, before encryption)
            recorder.record("out", "_raw_send", payload, client.peer)
            _orig_send(payload, is_bin)

        client.send_msg = _recorded_send
        _orig_new_client(client)

    hm_proto.handle_new_client = _recorded_new_client
```

For cleaner outbound recording, override at the `HiveMindClientConnection.send()` level instead
(before encryption) to get the actual `HiveMessageType`.

---

## Pytest Fixtures (`tests/conftest.py`)

```python
import pytest
from hivemind_test_harness.topology import TopologyBuilder

@pytest.fixture
def minimal_topology():
    """T1: 1 master, 1 satellite."""
    b = TopologyBuilder()
    b.add_master("M0")
    b.add_satellite("S0", upstream=b.get_master("M0"))
    b.start_all()
    yield b
    b.stop_all()

@pytest.fixture
def star_topology(request):
    """T2: 1 master, N satellites (default 3)."""
    n = getattr(request, "param", 3)
    b = TopologyBuilder()
    b.add_master("M0")
    for i in range(n):
        b.add_satellite(f"S{i}", upstream=b.get_master("M0"))
    b.start_all()
    yield b
    b.stop_all()

@pytest.fixture
def chain_topology(request):
    """T3: M0 → relay R1 → satellite S0."""
    b = TopologyBuilder()
    b.add_master("M0")
    relay_master = b.add_relay("R1", upstream=b.get_master("M0")).listener
    b.add_satellite("S0", upstream=relay_master)
    b.start_all()
    yield b
    b.stop_all()
```

---

## Example Test

```python
# tests/test_bus.py

def test_satellite_injects_bus_message(minimal_topology):
    b = minimal_topology
    s0 = b.get_satellite("S0")
    m0 = b.get_master("M0")

    s0.send(Message("recognizer_loop:utterance", {"utterances": ["hello world"]}))

    m0.agent_protocol.assert_injected("recognizer_loop:utterance", count=1)
    msg = m0.agent_protocol.last_injected("recognizer_loop:utterance")
    assert msg.data["utterances"] == ["hello world"]
    assert msg.context["peer"] == s0._connection.peer


def test_blacklisted_type_not_sent_to_satellite(minimal_topology):
    b = minimal_topology
    s0 = b.get_satellite("S0")
    m0 = b.get_master("M0")

    # Update satellite's db entry to blacklist "speak"
    with m0.db as db:
        client = db.get_client_by_api_key(s0.identity.access_key)
        client.message_blacklist = ["speak"]
        db.update_item(client)

    m0.send_to_satellite(
        s0._connection.peer,
        HiveMessage(HiveMessageType.BUS,
                    payload=Message("speak", {"utterance": "hello"}))
    )

    # S0 should not receive it: blacklist enforced in HiveMindClientConnection.send()
    s0.recorder.assert_not_received("speak")
```

---

## Dependencies

```toml
# pyproject.toml
[project]
name = "hivemind-test-harness"
dependencies = []   # all deps come from the workspace installs below

[project.optional-dependencies]
dev = [
    "pytest>=7.0",
    "pytest-timeout",
]
```

Install workspace packages as editable:
```bash
pip install -e ../hivemind-core
pip install -e ../hivemind-websocket-client
pip install -e ../hivemind-plugin-manager
pip install -e ../poorman_handshake
pip install -e .
```

No WebSocket library needed for running tests. `websocket-client` is still pulled in
transitively by `hivemind-websocket-client`, but the test harness never calls `run_forever()`.

---
[← Test Scenarios](04-test-scenarios.md) · [Home](index.md) · [E2E Skill Tests: Real OVOS Skills Through HiveMind →](06-e2e-skill-tests.md)
