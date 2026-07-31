# Node Implementations

The test harness provides two main actor types that simulate HiveMind protocol participants.

- **Source File**: `hivemind-test-harness/hivemind_test_harness/node.py`

## 1. `MasterNode`
Wraps a real `HiveMindListenerProtocol` but swaps the standard network and agent backends for test versions.
- **`create()`**: Factory method that instantiates the node with `TestAgentProtocol`, `TestBinaryProtocol`, and `TestNetworkProtocol`.
- **`register_satellite()`**: Pre-populates the node's `InMemoryClientDatabase` so that specific satellite keys are authorized to connect.
- **`recorder`**: An instance of `MessageRecorder` that captures every message passing through this node.
- **Source**: `MasterNode` class.

## 2. `SatelliteNode`
Simulates a HiveMind satellite device.
- **`InProcessHiveShim`**: Instead of a WebSocket client, this node uses a "shim" that calls the master node's methods directly. This shim maintains compatibility with the `HiveMindSlaveProtocol` used by real satellites.
- **`send()`**: Delivers a `HiveMessage` to the connected master.
- **`on_mycroft()`**: Registers a callback for AI messages received from the master.
- **Source**: `SatelliteNode` and `InProcessHiveShim` classes.

## In-Process Communication
Because both nodes reside in the same memory space, tests can easily inspect the state of the master's database, the recorder's logs, or the internal bus of the mock agent during execution.
- **Instrumentation**: The `_instrument_master()` helper function hooks into the protocol's message handlers to feed the `recorder`.

---
[← API Reference: hivemind-test-harness](api.md) · [Home](index.md)
