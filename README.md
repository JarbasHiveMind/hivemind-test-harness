# HiveMind Test Harness

Central HiveMind integration test suite for topology, stress, and cross-repo scenarios. Built on [hivescope](https://github.com/JarbasHiveMind/hivescope).

## Role

This repo owns tests that span multiple HiveMind repos, large network topologies, or sustained-load scenarios. It does **not** ship library code.

- **Protocol and single-repo e2e tests** belong in the owning repo's `tests/e2e/`, using hivescope directly.
- **Multi-repo, topology, stress, and cross-language scenarios** live here.

See [HIVEMIND_TEST_STRATEGY.md](https://github.com/JarbasHiveMind/hivemind-test-harness/blob/master/HIVEMIND_TEST_STRATEGY.md) for the full decision table.

## Test Layout

```
tests/
├── topology/           # multi-relay chains, deep nesting, mixed-protocol topologies
├── stress/             # 50+ satellites, sustained-load, protocol-limit scenarios
├── cross_repo/         # embedded clients, audio transformers, micropython, interop
├── skills_e2e/         # real OVOS skills through HiveMind (OvoscopeAgentProtocol)
└── _pending_migration/ # single-repo tests awaiting move to their owning repos
```

### topology/

Multi-hop relay chains, hierarchical hubs, ACL across relay boundaries, and routing correctness for complex topologies that cannot be expressed as a single-repo unit test.

### stress/

High-cardinality scenarios: large satellite fan-out, concurrent connections, protocol limits.

### cross_repo/

Interoperability tests: embedded clients, micropython clients, JS e2e, audio transformer pipelines. These require more than one repo to reproduce.

### skills_e2e/

End-to-end skill execution through HiveMind using `OvoscopeAgentProtocol` (real MiniCroft). Tests cover utterance routing, converse, get_response, session, stop, PHAL, scheduler, and OCP.

### _pending_migration/

Single-repo protocol tests that still run here while their target repos adopt hivescope. See [`tests/_pending_migration/README.md`](tests/_pending_migration/README.md) for the migration table.

## Install

```bash
pip install "hivescope @ git+https://github.com/JarbasHiveMind/hivescope@dev"
pip install -e .[dev]
```

For skill e2e tests (optional):

```bash
pip install -e .[ovos]
```

## Running Tests

```bash
# Standard run (excludes slow/stress)
pytest tests/ -v --timeout=60 -m "not slow"

# Include stress tests
pytest tests/ -v --timeout=120

# One suite
pytest tests/topology/ -v
pytest tests/cross_repo/ -v
pytest tests/skills_e2e/ -v
```

## Dependency

The in-process simulator (TopologyBuilder, MasterNode, SatelliteNode, RelayNode, fixtures, assertions, preset scenarios) lives in [hivescope](https://github.com/JarbasHiveMind/hivescope). API reference for those classes belongs in hivescope's documentation.

## License

AGPL-3.0
