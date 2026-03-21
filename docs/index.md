# HiveMind Test Harness

In-process protocol test framework for HiveMind. Simulates complex network topologies without sockets or external AI backends. Used for protocol verification, integration testing, and performance benchmarking.

**326 tests across 32 files.**

## Documentation Guides

| Document | Purpose |
|---|---|
| [01-architecture.md](01-architecture.md) | Plugin design, in-process message flow, component map |
| [02-protocol-coverage.md](02-protocol-coverage.md) | Full protocol coverage checklist with test file mapping |
| [03-topologies.md](03-topologies.md) | Network topology definitions (T1–T9) with fixture summary |
| [04-test-scenarios.md](04-test-scenarios.md) | Individual test scenario catalogue (Groups 1–12) |
| [05-implementation.md](05-implementation.md) | Code structure, class designs, pytest fixtures |
| [07-message-routing.md](07-message-routing.md) | Context keys, session_id lifecycle, reverse routing, `TestAgentProtocol` production parity |
| [api.md](api.md) | API reference for all harness classes |
| [nodes.md](nodes.md) | `MasterNode` and `SatelliteNode` implementation details |

## Key Concepts

- **In-Process Communication**: `TestNetworkProtocol` bypasses WebSocket; tests are fast and deterministic.
- **Topology DSL**: `TopologyBuilder` defines Master-Satellite-Relay hierarchies programmatically.
- **Message Recording**: `MessageRecorder` captures every `HiveMessage` for assertions.
- **Production Parity**: `TestAgentProtocol` reverse routing is ported verbatim from `OVOSProtocol`.
