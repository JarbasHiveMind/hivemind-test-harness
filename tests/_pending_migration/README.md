# Pending Migration

These tests were authored when this repo owned both the library and the
central test suite. After the 2026-05-07 refactor, the library lives in
[hivescope](https://github.com/JarbasHiveMind/hivescope) and this repo
keeps only topology, stress, and cross-repo integration scenarios.

The files here are **owned by another repo** and should be migrated
there as part of that repo's `tests/e2e/` suite. They still run today
(and are picked up by pytest) so coverage is not lost; the migration is
about ownership, not behaviour.

## Target repos

| File | Move to |
|------|---------|
| `test_acl.py` | HiveMind-core |
| `test_binary.py` | HiveMind-core |
| `test_broadcast.py` | HiveMind-core |
| `test_bus.py` | HiveMind-core |
| `test_cascade.py` | HiveMind-core |
| `test_escalate.py` | HiveMind-core |
| `test_handshake.py` | HiveMind-core |
| `test_intercom.py` | HiveMind-core |
| `test_ping_exactly_once.py` | HiveMind-core |
| `test_ping_pong.py` | HiveMind-core |
| `test_propagate.py` | HiveMind-core |
| `test_protocol_fixes.py` | HiveMind-core |
| `test_protocol_rules.py` | HiveMind-core |
| `test_query.py` | HiveMind-core |
| `test_route_metadata.py` | HiveMind-core |
| `test_shared_bus.py` | HiveMind-core |
| `test_unimplemented_types.py` | HiveMind-core |
| `test_hivemind_bus_client_e2e.py` | hivemind-websocket-client |

## Process per file

1. Open a PR on the target repo against `dev`, copying the file to `tests/e2e/`.
2. Remove the file here in the same PR (or follow-up).
3. Update this README's table.
