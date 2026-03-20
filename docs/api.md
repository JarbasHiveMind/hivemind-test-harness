# API Reference — hivemind-test-harness

The `hivemind-test-harness` provides an in-process testing environment for HiveMind protocols, allowing for complex multi-node topology testing without network overhead.

---

## Core Nodes

### `MasterNode`
`class MasterNode — hivemind_test_harness/node.py:84`

The Master node represents a HiveMind Hub (running `hivemind-core`). It handles incoming satellite connections and manages the agent/binary/network protocols.

#### `create(name, ...)`
`MasterNode.create — hivemind_test_harness/node.py:102`
Static method to create a fully instrumented MasterNode.

---

### `SatelliteNode`
`class SatelliteNode — hivemind_test_harness/node.py:171`

The Satellite node represents a HiveMind client (running `hivemind-bus-client`). It connects to a `MasterNode` and can send/receive messages.

#### `create(name, ...)`
`SatelliteNode.create — hivemind_test_harness/node.py:192`
Static method to create a SatelliteNode with its own internal bus and recorder.

---

### `RelayNode`
`class RelayNode — hivemind_test_harness/topology.py:46`

A dual-role node that acts as both a satellite (connecting upstream) and a master (accepting downstream connections).

---

## Topology Management

### `TopologyBuilder`
`class TopologyBuilder — hivemind_test_harness/topology.py:87`

Assembles and wires `MasterNode`, `SatelliteNode`, and `RelayNode` instances into a testable network.

#### `add_master(name, ...)`
`TopologyBuilder.add_master — hivemind_test_harness/topology.py:98`

#### `add_satellite(name, upstream, ...)`
`TopologyBuilder.add_satellite — hivemind_test_harness/topology.py:103`

#### `add_relay(name, upstream, ...)`
`TopologyBuilder.add_relay — hivemind_test_harness/topology.py:116`

#### `start_all()`
`TopologyBuilder.start_all — hivemind_test_harness/topology.py:155`
Connects all registered satellites to their respective masters.

---

## Instrumentation & Utilities

### `InProcessHiveShim`
`class InProcessHiveShim — hivemind_test_harness/node.py:35`
A minimal stand-in for `HiveMessageBusClient` that allows `HiveMindSlaveProtocol` to operate in-process.

### `TestAgentProtocol`
`class TestAgentProtocol — hivemind_test_harness/plugins/agent.py:27`
An `AgentProtocol` backed by a `FakeBus` for recording and asserting on injected messages.

### `TestBinaryProtocol`
`class TestBinaryProtocol — hivemind_test_harness/plugins/binary.py:23`
A protocol for handling and recording binary data transmissions (audio, files, etc.).

### `TestNetworkProtocol`
`class TestNetworkProtocol — hivemind_test_harness/plugins/network.py:18`
A socketless `NetworkProtocol` that wires `SatelliteNode` instances directly to `MasterNode` listeners.
