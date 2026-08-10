# API Reference: hivemind-test-harness

> These classes live in the [hivescope](https://github.com/JarbasHiveMind/hivescope)
> package (`hivescope>=0.6.2a1`, see `pyproject.toml`), not in this repo. This
> page mirrors them here for convenience; hivescope's own docs are the source
> of truth and take precedence on any conflict.

---

## `TopologyBuilder`

`class TopologyBuilder`: `hivescope/topology.py`

Assembles and wires `MasterNode`, `SatelliteNode`, and `RelayNode` instances into a testable network.

| Method | Signature | Description |
|---|---|---|
| `add_master` | `(name: str, **kwargs) -> MasterNode` | Create and register a master node |
| `add_satellite` | `(name: str, upstream: MasterNode \| RelayNode, shared_bus=False, **kwargs) -> SatelliteNode` | Create a satellite wired to `upstream` |
| `add_relay` | `(name: str, upstream: MasterNode \| RelayNode, **kwargs) -> RelayNode` | Create a dual-role relay node. Read `relay.listener` for the master side that downstream nodes attach to |
| `get_master` | `(name: str) -> MasterNode` | Retrieve master by name |
| `get_satellite` | `(name: str) -> SatelliteNode` | Retrieve satellite by name |
| `get_relay` | `(name: str) -> RelayNode` | Retrieve relay by name |
| `start_all` | `() -> None` | Connect all registered satellites to their masters (triggers handshake) |
| `stop_all` | `() -> None` | Disconnect all satellites and clean up |

| Attribute | Type | Description |
|---|---|---|
| `masters` | `list[MasterNode]` | Registered masters |
| `satellites` | `list[SatelliteNode]` | Registered satellites |
| `relays` | `list[RelayNode]` | Registered relays |

---

## `MasterNode`

`class MasterNode`: `hivescope/node.py`

Wraps a real `HiveMindListenerProtocol` with test plugin backends.

| Attribute | Type | Description |
|---|---|---|
| `name` | `str` | Node identifier |
| `identity` | `NodeIdentity` | Cryptographic identity |
| `db` | `ClientDatabase` | In-memory client database |
| `agent_protocol` | `TestAgentProtocol` | FakeBus-backed agent protocol |
| `binary_protocol` | `TestBinaryProtocol` | Binary handler recorder |
| `network_protocol` | `TestNetworkProtocol` | Socketless network protocol |
| `hm_protocol` | `HiveMindListenerProtocol` | The real protocol under test |
| `recorder` | `MessageRecorder` | Records all messages through this node |

| Method | Signature | Description |
|---|---|---|
| `create` | `(name: str, **kwargs) -> MasterNode` | Factory. Creates a fully instrumented node |
| `register_satellite` | `(key, password=None, is_admin=False, can_escalate=True, can_propagate=True, can_broadcast=True, allowed_types=None, msg_blacklist=None, skill_blacklist=None, intent_blacklist=None, crypto_key=None) -> None` | Pre-populate the database for satellite authentication. **`msg_blacklist` is accepted for API compatibility and ignored** — hivemind-core is whitelist-only, so admission is decided by `allowed_types` alone. `skill_blacklist` / `intent_blacklist` are a different mechanism that still works: they are stamped onto the session and enforced downstream by OVOS. See the ACL note in [03-topologies.md](03-topologies.md). |
| `send_to_satellite` | `(peer: str, message: HiveMessage) -> None` | Send directly to a connected client |
| `emit_on_bus` | `(message: Message) -> None` | Simulate OVOS skill emitting a response |
| `wait_for` | `(msg_type: str, direction="in", timeout=5.0) -> RecordedMessage` | Block until message recorded |
| `send_to_all` | `(message: HiveMessage) -> None` | Send to every connected client |
| `connected_peers` | `() -> list[str]` | Peer ids of the live connections |
| `cleanup` | `() -> None` | Tear the node down |

---

## `SatelliteNode`

`class SatelliteNode`: `hivescope/node.py`

Simulates a HiveMind satellite client.

| Attribute | Type | Description |
|---|---|---|
| `name` | `str` | Node identifier |
| `identity` | `NodeIdentity` | Cryptographic identity (includes `access_key`, `password`) |
| `internal_bus` | `FakeBus` | Internal OVOS bus for receiving skill responses |
| `slave_protocol` | `HiveMindSlaveProtocol` | The real slave protocol under test |
| `recorder` | `MessageRecorder` | Records all messages through this node |
| `_connection` | `HiveMindClientConnection` | Set after `connect()`. The upstream connection |
| `_master` | `MasterNode` | Set after `connect()`. The upstream master |

| Method | Signature | Description |
|---|---|---|
| `create` | `(name: str, **kwargs) -> SatelliteNode` | Factory. Creates a node with FakeBus and recorder |
| `connect` | `(master: MasterNode) -> None` | Wire to master in-process and complete handshake |
| `send` | `(message: HiveMessage | Message) -> None` | Send upstream. `Message` is auto-wrapped as BUS |
| `wait_for` | `(msg_type: str, timeout=5.0) -> RecordedMessage` | Block until inbound message recorded |
| `wait_for_bus` | `(ovos_type: str, timeout=5.0) -> Message` | Block until OVOS message arrives on `internal_bus` |
| `wait_for_handshake` | `(timeout=5.0) -> None` | Block until the handshake completes |
| `disconnect` | `() -> None` | Drop the upstream connection |
| `cleanup` | `() -> None` | Tear the node down |

**Properties via `_connection`:**
- `satellite.peer`: the peer ID assigned by the master after handshake
- `satellite.identity.access_key`: the API key used for authentication

---

## `RelayNode`

`class RelayNode`: `hivescope/topology.py`

Dual-role node: satellite (upstream) + master (downstream). `TopologyBuilder.add_relay()`
returns one.

| Attribute | Type | Description |
|---|---|---|
| `listener` | `HiveMindListenerProtocol` owner | The master side. Pass it as `upstream` for downstream nodes |
| `slave_protocol` | `HiveMindSlaveProtocol` | The satellite side, pointed at the upstream master |
| `hm_protocol` | `HiveMindListenerProtocol` | The relay's own listener protocol |
| `identity` | `NodeIdentity` | Cryptographic identity |
| `peer` | `str` | Peer id of the relay |

```python
relay = builder.add_relay("R1", upstream=builder.get_master("M0"))
builder.add_satellite("S0", upstream=relay.listener)
```

---

## `MessageRecorder`

`class MessageRecorder`: `hivescope/recorder.py`

Attached to every node. Captures all messages at protocol entry/exit points.

| Method | Signature | Description |
|---|---|---|
| `record` | `(direction, msg_type, payload, peer) -> None` | Add a record (called by instrumentation hooks) |
| `wait_for` | `(msg_type, direction=None, timeout=5.0) -> RecordedMessage | None` | Block until matching record appears |
| `assert_received` | `(msg_type, count=1, direction=None) -> None` | Assert exactly `count` matches exist |
| `assert_not_received` | `(msg_type, direction=None) -> None` | Assert no matches exist |
| `received` | `(msg_type, direction=None) -> list[RecordedMessage]` | Return all matching records |
| `clear` | `() -> None` | Reset all records |
| `snapshot` | `() -> list[RecordedMessage]` | Copy of the records as they stand now |

| Attribute | Type | Description |
|---|---|---|
| `messages` | `list[RecordedMessage]` | All captured records. `records` is an alias |
| `name` | `str` | Node name (for assertion error messages) |

### `RecordedMessage`

| Field | Type | Description |
|---|---|---|
| `direction` | `str` | `"in"`, `"out"`, `"bus_inject"`, or `"bus_emit"` |
| `msg_type` | `str` | `HiveMessageType` value or OVOS message type |
| `payload` | `Any` | Message payload |
| `peer` | `str` | Peer identifier |
| `timestamp` | `float` | `time.monotonic()` value |

---

## `TestAgentProtocol`

`class TestAgentProtocol`: `hivescope/plugins/agent.py`

`AgentProtocol` backed by `FakeBus`. Records injected messages and implements reverse routing (ported verbatim from `OVOSProtocol`).

| Attribute | Type | Description |
|---|---|---|
| `bus` | `FakeBus` | The agent bus. Messages emitted here trigger reverse routing |
| `injected` | `list[Message]` | All messages that passed through `bus.emit()` |

| Method | Signature | Description |
|---|---|---|
| `handle_send` | `(message: Message) -> None` | Route `hive.send.downstream` to clients |
| `handle_internal_mycroft` | `(message: str) -> None` | Route OVOS bus messages back to satellite by `destination` context |
| `last_injected` | `(msg_type: str) -> Message | None` | Last injected message of given type |
| `assert_injected` | `(msg_type: str, count=1) -> None` | Assert count of injected messages |
| `assert_not_injected` | `(msg_type: str) -> None` | Assert message type was NOT injected |
| `clear` | `() -> None` | Reset `injected` list |
| `natural_language_query` | `(utterance: str, lang: str) -> Iterator[str \| None]` | QUERY/CASCADE seam. Yields answer chunks, then `None` |
| `answer_query` | `(utterance, answers)` | Queue the answers a later QUERY gets |
| `shutdown` | `() -> None` | Tear the agent down |

---

## `TestBinaryProtocol`

`class TestBinaryProtocol`: `hivescope/plugins/binary.py`

Records all binary handler invocations.

| Attribute | Type | Description |
|---|---|---|
| `calls` | `list[BinaryCall]` | All recorded handler invocations |

| Method | Signature | Description |
|---|---|---|
| `handle_microphone_input` | `(bin_data, sample_rate, sample_width, client)` | Records `"microphone_input"` |
| `handle_stt_transcribe_request` | `(bin_data, sample_rate, sample_width, lang, client)` | Records `"stt_transcribe"` |
| `handle_stt_handle_request` | `(bin_data, sample_rate, sample_width, lang, client)` | Records `"stt_handle"` |
| `handle_numpy_image` | `(bin_data, camera_id, client)` | Records `"numpy_image"` |
| `handle_receive_tts` | `(bin_data, utterance, lang, file_name, client)` | Records `"receive_tts"` |
| `handle_receive_file` | `(bin_data, file_name, client)` | Records `"receive_file"` |
| `assert_called` | `(handler: str, count=1) -> None` | Assert handler was called `count` times |
| `last_call` | `(handler: str) -> BinaryCall \| None` | Last call to named handler |
| `assert_not_called` | `(handler: str) -> None` | Assert the handler was never called |
| `clear` | `() -> None` | Reset `calls` list |

### `BinaryCall`

| Field | Type | Description |
|---|---|---|
| `handler` | `str` | Handler name (e.g. `"microphone_input"`) |
| `data` | `bytes` | Raw binary data |
| `meta` | `dict` | Handler-specific metadata (e.g. `{"sample_rate": 16000}`) |

---

## `TestNetworkProtocol`

`class TestNetworkProtocol`: `hivescope/plugins/network.py`

Socketless `NetworkProtocol`. `run()` is a no-op.

| Method | Signature | Description |
|---|---|---|
| `run` | `() -> None` | No-op (nothing to bind) |
| `connect_satellite` | `(satellite: SatelliteNode, db_key: str) -> HiveMindClientConnection` | Wire satellite in-process. Triggers HELLO + HANDSHAKE |

---

## `InProcessHiveShim`

`class InProcessHiveShim`: `hivescope/node.py`

Minimal stand-in for `HiveMessageBusClient` that allows `HiveMindSlaveProtocol` to operate in-process without a WebSocket connection.

---
[← Message Routing & Session Flow](07-message-routing.md) · [Home](index.md) · [Node Implementations →](nodes.md)
