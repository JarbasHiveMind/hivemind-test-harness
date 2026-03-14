# Topology Builder

The `TopologyBuilder` class is the primary interface for assembling HiveMind test networks.

- **Source File**: `hivemind-test-harness/hivemind_test_harness/topology.py`
- **Primary Class**: `TopologyBuilder`

## Wiring Nodes

### 1. `add_master(name)`
Creates and stores a `MasterNode` instance.
- **Source**: `TopologyBuilder.add_master()`

### 2. `add_satellite(name, upstream)`
Creates a `SatelliteNode` and registers a future connection to the specified `upstream` master.
- **`shared_bus`**: Boolean flag to enable passive message monitoring.
- **Source**: `TopologyBuilder.add_satellite()`

### 3. `add_relay(name, upstream)`
Creates a "Relay" node, which is both a satellite of `upstream` and a master to its own downstream children.
- **Implementation**: It creates two internal nodes (`{name}_sat` and `{name}_master`) and wires them so that `ESCALATE` or `PROPAGATE` messages are forwarded between them.
- **Source**: `TopologyBuilder.add_relay()`

## Lifecycle Management

- **`start_all()`**: Iterates through all registered connections and performs the `poorman_handshake` for each satellite-master pair.
- **`stop_all()`**: Gracefully disconnects all nodes.
- **Source**: `TopologyBuilder.start_all()` and `stop_all()`
