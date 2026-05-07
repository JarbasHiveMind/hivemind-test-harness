# HiveMind Test Harness

Central HiveMind integration test suite. After the 2026-05-07 refactor the in-process simulator library moved to [hivescope](https://github.com/JarbasHiveMind/hivescope); this repo keeps only tests that span multiple repos, large topologies, or sustained-load scenarios.

## What Lives Here

| Suite | Directory | When to add a test |
|---|---|---|
| Topology | `tests/topology/` | Multi-relay chains, deep nesting, mixed-protocol, routing that requires >1 relay |
| Stress | `tests/stress/` | 50+ satellites, sustained-load, protocol-limit scenarios |
| Cross-repo | `tests/cross_repo/` | Embedded clients, micropython, JS, audio transformers, interop between two or more repos |
| Skills e2e | `tests/skills_e2e/` | Real OVOS skill execution through HiveMind (OvoscopeAgentProtocol) |
| Pending migration | `tests/_pending_migration/` | Single-repo tests running here temporarily; see migration table |

Tests that cover a single repo's behaviour belong in that repo's `tests/e2e/` directory, using hivescope directly. See the [test strategy document](https://github.com/JarbasHiveMind/hivemind-test-harness/blob/master/HIVEMIND_TEST_STRATEGY.md) for the full decision table.

## Dependency: hivescope

All test infrastructure (topology builder, node types, fixtures, assertions, preset scenarios) is provided by hivescope. Install it from:

```
pip install "hivescope @ git+https://github.com/JarbasHiveMind/hivescope@dev"
```

API reference for `TopologyBuilder`, `MasterNode`, `SatelliteNode`, `RelayNode`, `MessageRecorder`, fixtures, and preset scenarios belongs in hivescope's documentation.

## Documentation

| Document | Purpose |
|---|---|
| [03-topologies.md](03-topologies.md) | Network topology definitions (T1–T9) referenced by topology/ tests |
| [04-test-scenarios.md](04-test-scenarios.md) | Scenario catalogue for topology/ and stress/ suites |
| [06-e2e-skill-tests.md](06-e2e-skill-tests.md) | Skill e2e test coverage details |
| [07-message-routing.md](07-message-routing.md) | Context keys, session_id lifecycle, reverse routing |

The following docs describe the old library that now lives in hivescope and are retained only for historical reference until hivescope's own docs cover the same ground:

| Document | Status |
|---|---|
| [01-architecture.md](01-architecture.md) | Describes hivescope internals — see hivescope repo |
| [02-protocol-coverage.md](02-protocol-coverage.md) | Coverage checklist — partially superseded by pending migration |
| [05-implementation.md](05-implementation.md) | Class designs — moved to hivescope |
| [api.md](api.md) | API reference — moved to hivescope |
| [nodes.md](nodes.md) | Node implementation details — moved to hivescope |
