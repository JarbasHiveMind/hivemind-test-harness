# HiveMind Test Harness

End-to-end protocol test framework for HiveMind. Simulates N satellites and N masters across
various network topologies and validates that all HiveMind message types are correctly routed,
encrypted, and delivered.

## Key Design Decision

The harness tests **protocol behaviour**, not the network layer.
All message delivery is in-process — no sockets, no WebSocket servers, no port management.

This is achieved by implementing the three HiveMind plugin interfaces as test doubles:

| Plugin | Test Implementation | Purpose |
|---|---|---|
| `AgentProtocol` | `TestAgentProtocol` | FakeBus that records every injected OVOS message |
| `NetworkProtocol` | `TestNetworkProtocol` | In-process wiring; `run()` is a no-op |
| `BinaryDataHandlerProtocol` | `TestBinaryProtocol` | Records every binary handler call |

Everything that matters runs exactly as in production:
`HiveMindListenerProtocol`, `HiveMindSlaveProtocol`, handshake crypto (`poorman_handshake`),
encryption/decryption, session management, ACL, routing, hop tracking.

## Documentation

| Document | Purpose |
|---|---|
| [docs/01-architecture.md](docs/01-architecture.md) | Plugin design, in-process message flow |
| [docs/02-protocol-coverage.md](docs/02-protocol-coverage.md) | Full protocol coverage checklist |
| [docs/03-topologies.md](docs/03-topologies.md) | Network topology definitions (T1–T9) |
| [docs/04-test-scenarios.md](docs/04-test-scenarios.md) | Individual test scenario catalogue (~56 tests) |
| [docs/05-implementation.md](docs/05-implementation.md) | Code structure and class designs |
| [docs/06-roadmap.md](docs/06-roadmap.md) | Phased delivery roadmap |

## Quick Example

```python
def test_escalate_reaches_top_master(chain_topology):
    b = chain_topology          # M0 → R1(relay) → S0
    s0 = b.get_satellite("S0")
    m0 = b.get_master("M0")
    r1 = b.get_master("R1_master")

    s0.send(HiveMessage(HiveMessageType.ESCALATE,
                        payload=HiveMessage(HiveMessageType.BUS,
                                            payload=Message("some.event", {}))))

    # Relay forwarded it upstream
    r1.recorder.assert_received(HiveMessageType.ESCALATE)
    # Top master received it
    m0.recorder.assert_received(HiveMessageType.ESCALATE)
    # No downstream delivery — ESCALATE goes up only
    s0.recorder.assert_not_received(HiveMessageType.ESCALATE, direction="in")
```

## Running Tests

```bash
pip install -e ../hivemind-core
pip install -e ../hivemind-websocket-client
pip install -e ../hivemind-plugin-manager
pip install -e ../poorman_handshake
pip install -e .[dev]

pytest tests/
pytest tests/ -m "not slow"   # skip stress tests
```
