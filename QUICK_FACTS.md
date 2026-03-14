
# HiveMind Test Harness — Quick Facts

## Package Identity

| Key | Value |
|---|---|
| Package name | `hivemind-test-harness` |
| Import name | `hivemind_test_harness` |
| Version | 0.1.0 |
| Python requires | >=3.9 |
| Build backend | setuptools |
| License | Not specified |
| Entry points | None (library only) |

## Public API

| Class | Module | Purpose |
|---|---|---|
| `TopologyBuilder` | `hivemind_test_harness.topology` | Builder for assembling test topologies |
| `RelayNode` | `hivemind_test_harness.topology` | Dual-role node (satellite + master sharing one agent bus) |
| `MasterNode` | `hivemind_test_harness.node` | Wraps `HiveMindListenerProtocol` with test plugins |
| `SatelliteNode` | `hivemind_test_harness.node` | Wraps `HiveMindSlaveProtocol` via in-process shim |
| `InMemoryClientDatabase` | `hivemind_test_harness.database` | Drop-in DB backed by plain dict |
| `MessageRecorder` | `hivemind_test_harness.recorder` | Records all inbound/outbound HiveMessages |
| `RecordedMessage` | `hivemind_test_harness.recorder` | Dataclass: direction, msg_type, payload, peer |
| `TestAgentProtocol` | `hivemind_test_harness.plugins.agent` | `AgentProtocol` backed by `FakeBus` |
| `TestBinaryProtocol` | `hivemind_test_harness.plugins.binary` | Binary handler recording stub |
| `TestNetworkProtocol` | `hivemind_test_harness.plugins.network` | In-process network wiring (no sockets) |
| `OvoscopeAgentProtocol` | `hivemind_test_harness.plugins.ovoscope_agent` | Agent backed by live MiniCroft |

## Key Methods

| Class | Method | Description |
|---|---|---|
| `TopologyBuilder` | `add_master(name, **kwargs)` | Create and register a MasterNode |
| `TopologyBuilder` | `add_satellite(name, upstream, **kwargs)` | Create and connect a SatelliteNode |
| `TopologyBuilder` | `add_relay(name, upstream, **kwargs)` | Create dual-role relay (shared `TestAgentProtocol`) |
| `TopologyBuilder` | `get_relay(name)` | Return `RelayNode` for a dual-role node |
| `TopologyBuilder` | `start_all() / stop_all()` | Lifecycle management |
| `MasterNode` | `register_satellite(key, password, ...)` | Pre-register a satellite in the DB |
| `MasterNode` | `wait_for(msg_type, direction, timeout)` | Block until message recorded |
| `SatelliteNode` | `send(message)` | Send HiveMessage or OVOS Message |
| `SatelliteNode` | `connect(master, ...)` | Wire satellite to master (calls handshake) |
| `SatelliteNode` | `wait_for(msg_type, direction, timeout)` | Block until message recorded |
| `MessageRecorder` | `assert_received(msg_type, count)` | Raise if count doesn't match |
| `MessageRecorder` | `clear()` | Reset recorded messages |

## Test Counts (2026-03-09)

| Category | Tests | Status |
|---|---|---|
| Protocol (bus, acl, broadcast, escalate, propagate, intercom, handshake, shared_bus, routing) | 50 | All pass |
| Binary protocols | 7 | All pass |
| Audio transformers | 8 | All pass |
| Protocol fixes | 8 | All pass |
| Unimplemented types | 5 | All pass (verify NotImplementedError) |
| Solver harness | 5 | All pass |
| PING/PONG + HiveMapper (fast) | 32 | All pass |
| PING/PONG huge/chaotic/asymmetric (slow) | 17 | All pass |
| Topology plots | 12 | All pass |
| OvoScope integration | 9 | All pass |
| HelloWorld skill | 15 | All pass |
| **Total** | **168** | **166 pass, 0 fail, 2 skip** |

## Runtime Dependencies (via workspace editable installs)

| Package | Purpose |
|---|---|
| `hivemind-core` | `HiveMindListenerProtocol` |
| `hivemind-websocket-client` | `HiveMindSlaveProtocol`, `HiveMessage`, `HiveMessageType` |
| `hivemind-plugin-manager` | `AgentProtocol`, `BinaryDataHandlerProtocol`, `NetworkProtocol` |
| `ovos-bus-client` | `Message`, `Session` |
| `ovos-utils` | `FakeBus`, `LOG` |
| `poorman_handshake` | `HandShake`, `PasswordHandShake` |
| `ovoscope` (optional) | `MiniCroft`, `CaptureSession` — for `OvoscopeAgentProtocol` |
| `ovos-core` (optional) | Transitive via ovoscope |

## Known Namespace Package Issues (must install as non-editable wheels)

| Package | Symptom |
|---|---|
| `z85base91` | `ImportError: cannot import name 'Z85B'` |
| `poorman_handshake` | `ImportError: cannot import name 'HandShake'` |
| `json_database` | `ImportError: cannot import name 'JsonConfigXDG'` |

Fix: `uv pip install --python .venv/bin/python <package>` (without `-e`)
