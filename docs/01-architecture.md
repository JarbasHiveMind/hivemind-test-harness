# Architecture: HiveMind Test Harness

## Core Principle

The test harness validates **protocol behaviour**, not network transport.
All message delivery is in-process via direct function calls.
No sockets, no ports, no WebSocket framing.

The harness achieves this by implementing the three plugin interfaces from
`hivemind_plugin_manager.protocols` as dedicated test doubles:

| Plugin interface | Production impl | Test impl |
|---|---|---|
| `AgentProtocol` | `OVOSProtocol` (connects to OVOS messagebus) | `TestAgentProtocol` (FakeBus, records injected messages) |
| `NetworkProtocol` | `HiveMindWebsocketProtocol` (WebSocket server) | `TestNetworkProtocol` (in-process wiring, no sockets) |
| `BinaryDataHandlerProtocol` | `AudioBinaryProtocol` | `TestBinaryProtocol` (records every handler call) |

Everything that matters — `HiveMindListenerProtocol.handle_message()`, all routing logic,
encryption/decryption, handshake, session management, ACL — runs exactly as in production.
Only the byte transport is replaced.

---

## Plugin Architecture (Production Reference)

```
HiveMindService
├── AgentProtocol           ← handles Message (OVOS bus) objects
│     └── bus: MessageBusClient / FakeBus
├── BinaryDataHandlerProtocol  ← handles binary HiveMessage payloads
└── NetworkProtocol         ← transports HiveMessage across the wire
      └── run()             ← abstract; starts listener (WS, HTTP, etc.)

NetworkProtocol owns HiveMindListenerProtocol, which owns:
├── handle_message(HiveMessage, HiveMindClientConnection)
├── handle_new_client(HiveMindClientConnection)
└── handle_client_disconnected(HiveMindClientConnection)

HiveMindClientConnection holds:
├── send_msg: Callable[[str | bytes, bool], None]   ← write to transport
├── disconnect: Callable[[], None]
└── decode(payload) -> HiveMessage                   ← parse from transport
```

---

## Test Plugin Design

### `TestNetworkProtocol(NetworkProtocol)`

Replaces WebSocket with an in-process call gate.

```
TestNetworkProtocol
├── run() → no-op (nothing to bind)
└── connect_satellite(satellite) → wires satellite ↔ hm_protocol directly
      - creates HiveMindClientConnection where:
          send_msg = satellite._receive_raw     ← master's outbound goes to satellite
          disconnect = satellite._on_disconnect
      - calls hm_protocol.handle_new_client(connection)
      - stores connection in satellite for upward sends
```

When a satellite wants to send to its master:
```python
satellite.send(message)
  → connection.send_msg is NOT used here
  → instead: hm_protocol.handle_message(message, connection)  # direct call
```

When the master sends back to the satellite:
```python
# Inside HiveMindClientConnection.send(message):
#   calls self.send_msg(payload, is_binary)
#   send_msg = satellite._receive_raw
satellite._receive_raw(payload, is_binary)
  → decrypts if needed
  → satellite.recorder.record("in", ...)
  → satellite.emitter.emit(msg_type, message)  # triggers registered handlers
```

This path exercises:
- `HiveMindClientConnection.send()` including encryption and blacklist checks ✅
- `HiveMindClientConnection.decode()` including decryption ✅
- All of `HiveMindListenerProtocol` routing logic ✅
- `HiveMindSlaveProtocol` handler callbacks on the satellite side ✅

What it skips:
- WebSocket framing — intentional, not under test
- TCP/TLS — intentional

### `TestAgentProtocol(AgentProtocol)`

```python
@dataclass
class TestAgentProtocol(AgentProtocol):
    bus: FakeBus = field(default_factory=FakeBus)
    injected: List[Message] = field(default_factory=list)

    # bus.emit() is recorded transparently via FakeBus handlers
    # tests can assert on self.injected or register handlers on self.bus
```

Gives tests a real `FakeBus` to:
- Assert that a satellite's BUS message arrived on the agent bus
- Emit responses back (simulating what OVOS/a skill would do)
- Verify session context, blacklists, peer routing are correct

`TestAgentProtocol` also implements **reverse routing** — messages emitted on the OVOS bus
are forwarded back to the originating satellite peer, exactly as the production `OVOSProtocol`
does in a live deployment. The routing logic is ported verbatim from
`ovos-bus-client/ovos_bus_client/hpm.py`. See [07-message-routing.md](07-message-routing.md)
for the full context-key mechanism and session_id lifecycle.

### `TestBinaryProtocol(BinaryDataHandlerProtocol)`

```python
@dataclass
class TestBinaryProtocol(BinaryDataHandlerProtocol):
    calls: List[BinaryCall] = field(default_factory=list)

    def handle_microphone_input(self, bin_data, sr, sw, client):
        self.calls.append(BinaryCall("microphone_input", bin_data, sr=sr, sw=sw))

    def handle_stt_transcribe_request(self, bin_data, sr, sw, lang, client):
        self.calls.append(BinaryCall("stt_transcribe", bin_data, lang=lang))

    # ... all other handlers record similarly
```

Tests assert `binary_protocol.calls` instead of inspecting log output.

---

## Component Map

```
┌───────────────────────────────────────────────────────────────┐
│                         TestHarness                           │
│                                                               │
│  ┌──────────────┐  wires  ┌──────────────────────────────┐   │
│  │TopologyBuilder│────────►│         MasterNode           │   │
│  └──────────────┘         │                              │   │
│         │                 │  HiveMindListenerProtocol    │   │
│         │                 │  ├── TestAgentProtocol       │   │
│         │                 │  │     └── FakeBus           │   │
│         │                 │  ├── TestBinaryProtocol      │   │
│         │                 │  └── TestNetworkProtocol     │   │
│         │                 │        └── run() → no-op     │   │
│         │                 └──────────────────────────────┘   │
│         │                              ▲                      │
│         │                 direct call  │  direct call         │
│         │                              ▼                      │
│         │                 ┌──────────────────────────────┐   │
│         └────────────────►│        SatelliteNode         │   │
│                           │                              │   │
│                           │  HiveMindSlaveProtocol       │   │
│                           │  ├── internal FakeBus        │   │
│                           │  └── connection (no socket)  │   │
│                           └──────────────────────────────┘   │
│                                                               │
│  ┌──────────────┐  attached to every node                    │
│  │MessageRecorder│                                            │
│  └──────────────┘                                            │
└───────────────────────────────────────────────────────────────┘
```

---

## Message Flow (In-Process)

### Satellite → Master (e.g. BUS message)

```
satellite.send(HiveMessage(BUS, payload))
  └─► connection.hm_protocol.handle_message(message, connection)
            └─► handle_bus_message(message, client)
                    └─► agent_protocol.bus.emit(ovos_message)
                              └─► TestAgentProtocol.injected.append(...)
                                  test asserts here
```

### Master → Satellite (e.g. BROADCAST)

```
master.send_to_satellite(peer, HiveMessage(BROADCAST, ...))
  └─► hm_protocol.clients[peer].send(message)
            └─► HiveMindClientConnection.send()
                    ├─► encrypt(payload)          [if crypto_key set]
                    └─► self.send_msg(payload, is_bin)
                              └─► satellite._receive_raw(payload, is_bin)
                                      ├─► decrypt(payload)
                                      ├─► recorder.record("in", ...)
                                      └─► slave_protocol.handle_broadcast(message)
                                                test asserts here
```

### Handshake (still exercised fully)

The handshake runs through `HiveMindListenerProtocol.handle_handshake_message()` and
`HiveMindSlaveProtocol.handle_handshake()` exactly as production.
`poorman_handshake` key exchange is performed in-memory. The derived `crypto_key` is
set on both the `HiveMindClientConnection` and the satellite.
All subsequent messages are encrypted/decrypted through the real crypto path.

---

## `MessageRecorder`

Attached to every `MasterNode` and `SatelliteNode`. Intercepts every message
at the point it enters or leaves the protocol layer.

```python
@dataclass
class RecordedMessage:
    direction: Literal["in", "out", "bus_inject", "bus_emit"]
    msg_type: str           # HiveMessageType or OVOS message type
    payload: Any
    peer: str
    timestamp: float

class MessageRecorder:
    records: List[RecordedMessage]

    def wait_for(self, msg_type, direction="in", timeout=5.0) -> RecordedMessage
    def assert_received(self, msg_type, count=1, direction=None) -> None
    def assert_not_received(self, msg_type, direction=None) -> None
    def received(self, msg_type, direction=None) -> List[RecordedMessage]
    def clear() -> None
```

Instrumentation points:
- **MasterNode inbound**: override `HiveMindListenerProtocol.handle_message()`
- **MasterNode outbound**: wrap `send_msg` callable in `HiveMindClientConnection`
- **MasterNode bus inject**: `FakeBus.on("message", recorder.record)` on agent bus
- **SatelliteNode inbound**: `_receive_raw()` records before dispatching
- **SatelliteNode outbound**: `HiveMindSlaveProtocol` send path

---

## Topology Wiring

```python
# Connecting a satellite to a master — all in-process:

def connect(satellite: SatelliteNode, master: MasterNode):
    # 1. Create client connection object (normally created by websocket on_open)
    client_conn = HiveMindClientConnection(
        key=satellite.identity.access_key,
        send_msg=satellite._receive_raw,   # master → satellite delivery
        disconnect=satellite._on_disconnect,
        hm_protocol=master.hm_protocol,
    )
    # 2. Trigger the full server-side connection lifecycle
    master.hm_protocol.handle_new_client(client_conn)   # sends HELLO + HANDSHAKE
    # 3. Satellite processes HELLO + HANDSHAKE and responds (in-process)
    satellite._complete_handshake(client_conn)            # sends HELLO back
    # 4. Store connection reference for upward sends
    satellite._connection = client_conn
    satellite._master = master
```

The handshake exchange happens synchronously through direct calls — no threads needed,
no timeouts to tune, deterministic every run.
