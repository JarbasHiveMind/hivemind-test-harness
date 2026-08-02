# Hivescope: E2E Testing Library for HiveMind

A reusable pytest-based framework for writing end-to-end tests of HiveMind protocol implementations. Like [ovoscope](https://github.com/OpenVoiceOS/ovoscope) for OVOS, hivescope provides stable APIs for topology simulation, message routing verification, and protocol-level assertions.

**Table of Contents**
- [Overview](#overview)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [API Reference](#api-reference)
- [Common Patterns](#common-patterns)
- [Known Limitations & Gotchas](#known-limitations--gotchas)
- [Integration Guide](#integration-guide)

---

## Overview

Hivescope enables HiveMind repos (and external deployments) to write dedicated e2e test suites that verify protocol behavior in isolation. Key features:

- **Topology Builder**: Assemble complex network topologies in-process (no sockets, no real network)
- **Protocol Stubs**: TestAgentProtocol (FakeBus), TestBinaryProtocol, TestNetworkProtocol for fast deterministic testing
- **Message Recording**: All HiveMessages are recorded and inspectable for assertions
- **Fixtures & Helpers**: Pytest fixtures and assertion helpers reduce boilerplate
- **Optional Real Skills**: OvoscopeAgentProtocol integrates a real MiniCroft (OVOS skills) for realistic testing
- **Preset Topologies**: Common topologies (single satellite, relay chains, hierarchical hubs) provided

---

## Installation

### As a library (for external use)

```bash
pip install hivescope
```

With OVOS integration (for skill-level testing):

```bash
pip install hivescope[ovos]
```

### From workspace (for development)

```bash
cd /path/to/HiveMind/hivemind-test-harness
pip install -e .
```

### Workspace vs PyPI

When working in the HiveMind workspace, all 19 MAINTAIN repos install hivescope from local editable sources. External deployments install from PyPI.

---

## Quick Start

### 1. Simple Topology Test

```python
# tests/e2e/test_handshake.py
from hivescope import TopologyBuilder, TestAgentProtocol

def test_satellite_handshakes_with_master():
    """Test that satellite handshakes with master."""
    b = TopologyBuilder()
    m = b.add_master("M0")
    m.register_satellite("test-key", password="test-password")
    s = b.add_satellite("S0", upstream=m)
    
    b.start_all()
    try:
        s.connect(m)
        s.wait_for_handshake(timeout=5)
        
        assert s.crypto_key is not None
        assert len(m.connected_peers()) == 1
    finally:
        b.stop_all()
```

### 2. Using Fixtures

```python
# tests/conftest.py
pytest_plugins = ['hivescope.pytest_fixtures']

# tests/e2e/test_with_fixtures.py
def test_with_master_and_satellite(master_node, satellite_node):
    """
    master_node and satellite_node fixtures auto-start and auto-stop.
    """
    from ovos_bus_client.message import Message
    
    satellite_node.send(Message("test:message", {"data": "value"}))
    master_node.recorder.assert_received("test:message", count=1)
```

### 3. Using Preset Topologies

```python
from hivescope.scenarios import three_satellites

def test_broadcast_to_three_satellites():
    """Test broadcast reaches all 3 satellites."""
    b = three_satellites()
    b.start_all()
    try:
        m = b.get_master("M0")
        s0 = b.get_satellite("S0")
        s1 = b.get_satellite("S1")
        s2 = b.get_satellite("S2")
        
        # ... assertions ...
    finally:
        b.stop_all()
```

---

## API Reference

### Core Classes

#### `TopologyBuilder`

Builder for assembling test topologies.

**Methods:**
- `add_master(name, **kwargs) → MasterNode`  
  Create and register a master node.
  
- `add_satellite(name, upstream, **kwargs) → SatelliteNode`  
  Create and register a satellite under an upstream node.
  
- `add_relay(name, upstream, **kwargs) → RelayNode`  
  Create a dual-role relay: a satellite facing *upstream* that is also a master
  for its own downstream satellites. Both sides share one agent bus.
  Returns a `RelayNode` with:
  - `relay.upstream` — the `SatelliteNode` side (registered as `{name}_sat`)
  - `relay.listener` — the `MasterNode` side (registered as `{name}_master`).
    **This is the node to hang downstream satellites off.**
  - `relay.bus` — the shared `FakeBus`

  ```python
  r1 = b.add_relay("R1", upstream=b.get_master("M0"))
  b.add_satellite("S0", upstream=r1.listener)
  # add_satellite/add_relay also accept the RelayNode itself as `upstream`:
  r2 = b.add_relay("R2", upstream=r1)
  ```
  
- `get_master(name) → MasterNode`  
  Retrieve a registered master. Note a relay's master side is named
  `{name}_master`, so `b.get_master("R1_master")`.
  
- `get_satellite(name) → SatelliteNode`  
  Retrieve a registered satellite. A relay's satellite side is `{name}_sat`.
  
- `get_relay(name) → RelayNode`  
  Retrieve the combined `RelayNode` that `add_relay(name, ...)` returned —
  the same object, keyed by the plain name (`b.get_relay("R1")`), not by the
  `_sat` / `_master` internal names.
  
- `start_all()`  
  Start all nodes in the topology (spawn threads, bind ports, etc).
  
- `stop_all()`  
  Stop all nodes and clean up resources.

#### `MasterNode`

A master (hub) in the HiveMind network.

**Methods:**
- `register_satellite(key, password, **kwargs)`  
  Pre-register a satellite in the master's database before it connects.
  
- `connected_peers() → list[str]`  
  Return list of currently connected peer identifiers.

**Attributes:**
- `recorder: MessageRecorder`  
  All inbound/outbound HiveMessages for this master.
  
- `crypto_key: str | None`  
  Negotiated session key (after handshake).

#### `SatelliteNode`

A satellite (device) in the HiveMind network.

**Methods:**
- `connect(master, **kwargs)`  
  Connect to a master and perform handshake.
  
- `wait_for_handshake(timeout=10)`  
  Block until handshake completes or timeout.
  
- `send(message: HiveMessage | Message)`  
  Send a message to the master.

**Attributes:**
- `recorder: MessageRecorder`  
  All inbound/outbound messages for this satellite.
  
- `crypto_key: str | None`  
  Negotiated session key (after handshake).
  
- `peer: str`  
  This satellite's peer identifier (e.g., "S0").

#### `RelayNode`

A dual-role relay node (master facing upstream, satellite facing downstream).

Acts as both `MasterNode` (for downstream satellites) and `SatelliteNode` (for upstream master).

### Protocol Plugins

#### `TestAgentProtocol`

Agent protocol backed by FakeBus (fast, deterministic). Default for all topologies.

#### `OvoscopeAgentProtocol` (optional)

Agent protocol backed by a live MiniCroft (OVOS intent service, real skills).

**Requires:** `pip install hivescope[ovos]`

#### `TestBinaryProtocol`

Binary data handler stub (records binary messages, no-op processing).

#### `TestNetworkProtocol`

Network protocol stub for in-process wiring (no sockets, no real network).

### Message Recording

#### `MessageRecorder`

Records all HiveMessages passing through a node.

**Methods:**
- `messages() → list[RecordedMessage]`  
  All recorded messages.
  
- `clear()`  
  Reset recorded messages.
  
- `assert_received(msg_type, count) → bool`  
  Raise AssertionError if count doesn't match.
  
- `wait_for(msg_type, timeout=5) → RecordedMessage`  
  Block until message received or timeout.

#### `RecordedMessage`

A recorded HiveMessage.

**Attributes:**
- `direction: "inbound" | "outbound"`
- `msg_type: str` (e.g., "HELLO", "BUS", "BROADCAST")
- `payload: Any`
- `peer: str`
- `timestamp: float`

### Fixtures

**See `hivescope.pytest_fixtures`:**
- `topology`: Fresh TopologyBuilder with auto-start/stop
- `master_node`: Pre-configured master in simple topology
- `satellite_node`: Pre-configured satellite connected to master
- `admin_satellite`: Admin satellite with full permissions
- `restricted_satellite`: Non-admin satellite with ACL restrictions

### Assertion Helpers

**See `hivescope.assertions`:**
- `assert_handshake_complete(master, satellite, timeout=5)`
- `assert_message_routed(node, msg_type, count, direction, timeout)`
- `assert_acl_enforced(master, satellite, msg_type, allowed=False)` (placeholder)
- `assert_encryption_match(master, satellite)`
- `assert_client_registered(master, peer)`
- `assert_client_not_registered(master, peer)`
- `assert_message_received_by(node, msg_type, count)`
- `assert_message_sent_by(node, msg_type, count)`

### Preset Topologies

**See `hivescope.scenarios`:**
- `single_satellite()`: T1 — 1 master, 1 satellite
- `three_satellites()`: T2 — 1 master, 3 satellites
- `with_relay()`: T9 — 1 master, relay, satellites under relay
- `chain_topology()`: T3 — Master → Relay → Satellites
- `star_topology(num=5)`: Central master with N satellites
- `with_acl_enforcement()`: Topology with ACL rules
- `hierarchical_hubs(levels=3, sats_per_relay=2)`: Deep nested topology
- `with_multiple_agent_protocols()`: Custom protocol topology

---

## Common Patterns

### Pattern 1: Test Protocol Correctness

```python
def test_message_routing_respects_blacklist():
    from hivescope import TopologyBuilder
    from hivescope.assertions import assert_message_routed
    
    b = TopologyBuilder()
    m = b.add_master("M0")
    m.register_satellite("sat-key", msg_blacklist=["speak"])
    s = b.add_satellite("S0", upstream=m)
    
    b.start_all()
    try:
        # Satellite tries to send "speak" — should be blocked
        s.recorder.clear()
        # ... send speak ...
        
        assert_message_routed(m, "speak", count=0)
    finally:
        b.stop_all()
```

### Pattern 2: Test Encryption Negotiation

```python
def test_encryption_negotiation():
    from hivescope.scenarios import single_satellite
    from hivescope.assertions import assert_handshake_complete, assert_encryption_match
    
    b = single_satellite()
    b.start_all()
    try:
        m = b.get_master("M0")
        s = b.get_satellite("S0")
        
        s.connect(m)
        s.wait_for_handshake(timeout=5)
        
        assert_handshake_complete(m, s)
        assert_encryption_match(m, s)
    finally:
        b.stop_all()
```

### Pattern 3: Test Multi-Hop Routing

```python
def test_escalate_reaches_top_master():
    from hivescope.scenarios import hierarchical_hubs
    
    b = hierarchical_hubs(num_levels=3)
    b.start_all()
    try:
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")
        
        s0.connect(b.get_relay("R1").listener)  # attach to the relay's master side
        
        # Send ESCALATE from S0
        # Should reach M0 through: S0 → R1 → R0 → M0
        
        # ... assertions on m0.recorder ...
    finally:
        b.stop_all()
```

### Pattern 4: Test Skill Execution (with OvoscopeAgentProtocol)

```python
def test_hello_world_skill():
    from hivescope import TopologyBuilder, OvoscopeAgentProtocol
    from ovos_bus_client.message import Message
    
    if OvoscopeAgentProtocol is None:
        pytest.skip("ovoscope not installed")
    
    agent = OvoscopeAgentProtocol(skill_ids=["skill-ovos-hello-world"])
    b = TopologyBuilder()
    m = b.add_master("M0", agent_protocol=agent)
    m.register_satellite("test-key")
    s = b.add_satellite("S0", upstream=m)
    
    b.start_all()
    try:
        s.connect(m)
        s.wait_for_handshake()
        
        # Send utterance to trigger skill
        s.send(Message("recognizer_loop:utterance", {"utterances": ["hello"]}))
        
        # Wait for speak response
        agent.assert_skill_emitted("speak", timeout=10)
    finally:
        b.stop_all()
        agent.shutdown()
```

---

## Known Limitations & Gotchas

### FakeBus vs Real Bus

- **FakeBus (TestAgentProtocol)**: Messages intercepted at protocol level, never reach a real OVOS IntentService. Fast, deterministic, but doesn't test actual skill behavior.
- **MiniCroft (OvoscopeAgentProtocol)**: Routes to a real OVOS intent pipeline with skill plugins. Realistic, but slower and requires OVOS to be installed.

**Gotcha**: A test passing with FakeBus may fail with MiniCroft if the skill plugin isn't installed or has bugs.

**Fix**: Use FakeBus for protocol-level tests, OvoscopeAgentProtocol only for skill-level tests.

### Handshake Timing

Handshake is async; don't send messages before calling `wait_for_handshake()`.

```python
# WRONG
s.connect(m)
s.send(Message(...))  # Fails: satellite not yet registered

# RIGHT
s.connect(m)
s.wait_for_handshake(timeout=5)
s.send(Message(...))  # OK: satellite is now registered
```

### Message Recorder Stale State

If you don't clear the recorder between assertions, old messages pollute results.

```python
# WRONG
m.recorder.assert_received("HELLO", count=1)
# ... later ...
m.recorder.assert_received("BUS", count=1)  # Fails: HELLO still in recorder

# RIGHT
m.recorder.clear()
m.recorder.assert_received("HELLO", count=1)
m.recorder.clear()
m.recorder.assert_received("BUS", count=1)  # OK
```

### Encryption Key Mismatch

If master and satellite have different cipher/encoding settings, decryption fails silently (logged, not raised). Test may hang instead of failing clearly.

**Fix**: Use `assert_encryption_match(master, satellite)` before sending encrypted messages.

### Relay Message Forwarding

RelayNode has two separate agents (one upstream-facing, one downstream-facing). Messages don't auto-cross between them. The relay's TopologyBuilder setup must be correct for forwarding to work.

---

## Integration Guide

### Adding Hivescope to Your Repo

**For MAINTAIN repos in the HiveMind workspace:**

1. Create `tests/e2e/` directory
2. Create `tests/e2e/conftest.py`:
   ```python
   pytest_plugins = ['hivescope.pytest_fixtures']
   ```
3. Create `tests/e2e/test_*.py` files using fixtures and scenarios

**For external HiveMind deployments:**

1. `pip install hivescope[ovos]` (or just `hivescope` if you don't need skill testing)
2. Same steps as above

### GitHub Actions CI Integration

Add to your `.github/workflows/tests.yml`:

```yaml
- name: Run e2e tests
  run: pytest tests/e2e/ -v
```

---

## Testing Hivescope Itself

Hivescope has a central test suite at `tests/` with 168+ tests covering:
- Protocol correctness (handshake, encryption, routing, ACL)
- Binary protocols (audio, files, images)
- Topologies (relay chains, hierarchical hubs, stress tests)
- Ovoscope integration (real skill execution)

Run them:

```bash
cd hivemind-test-harness
pytest tests/ -v
```

---

## Support

- **Questions?** Check the docstrings in each module.
- **Bugs?** Open an issue on [GitHub](https://github.com/JarbasHiveMind/hivemind-test-harness).
- **Want to contribute?** HiveMind is Apache-2.0; contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

