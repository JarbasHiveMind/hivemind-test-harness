# Roadmap

Phased delivery. Each phase is independently runnable and produces value before the next begins.

---

## Phase 1 · Foundation (harness skeleton + plugins)

**Goal**: The test harness compiles and runs one test end-to-end using in-process plugins.

### Deliverables

- [ ] `hivemind_test_harness/plugins/agent.py` — `TestAgentProtocol` (FakeBus + injection recorder)
- [ ] `hivemind_test_harness/plugins/network.py` — `TestNetworkProtocol` (`run()` no-op, `connect_satellite()`)
- [ ] `hivemind_test_harness/plugins/binary.py` — `TestBinaryProtocol` (records all handler calls)
- [ ] `hivemind_test_harness/recorder.py` — `MessageRecorder`, `RecordedMessage`
- [ ] `hivemind_test_harness/utils.py` — `make_identity()`, `make_test_db()`, `wait_event()` helpers
- [ ] `hivemind_test_harness/node.py` — `MasterNode.create()`, `SatelliteNode.create()`, `connect()`
- [ ] `hivemind_test_harness/topology.py` — `TopologyBuilder` with `add_master`, `add_satellite`, `add_relay`, `start_all`, `stop_all`
- [ ] `tests/conftest.py` — `minimal_topology` fixture (T1)
- [ ] `tests/test_handshake.py` — TS-CONN-01 (RSA), TS-CONN-02 (Password), TS-CONN-03 (pre-shared)

**Success criterion**: `pytest tests/test_handshake.py` passes for all three handshake variants.
No sockets used anywhere in the test run.

---

## Phase 2 · Core Message Types

**Goal**: BUS, SHARED_BUS, BROADCAST, ESCALATE, PROPAGATE all validated.

### Deliverables

- [ ] `tests/test_bus.py` — TS-BUS-01..04
- [ ] `tests/test_shared_bus.py` — TS-SBUS-01..02
- [ ] `tests/test_broadcast.py` — TS-BC-01..04
- [ ] `tests/test_escalate.py` — TS-ESC-01..03
- [ ] `tests/test_propagate.py` — TS-PROP-01..04
- [ ] `tests/conftest.py` — `star_topology` fixture (T2), `chain_topology` fixture (T3)

**Success criterion**: All Phase 2 tests pass on T1, T2, T3 topologies.

---

## Phase 3 · Routing & Access Control

**Goal**: INTERCOM, ACL, session management, route tracking.

### Deliverables

- [ ] `tests/test_intercom.py` — TS-IC-01..03
- [ ] `tests/test_acl.py` — TS-ACL-01..03
- [ ] `tests/test_session.py` — TS-CONN-07, TS-CONN-08, TS-BUS-04
- [ ] `tests/test_routes.py` — TS-ROUTE-01..03
- [ ] `tests/conftest.py` — `relay_topology` fixture (T9)

**Success criterion**: Routing and ACL tests pass; route tracking validated on chain topology.

---

## Phase 4 · Binary Protocol

**Goal**: All binary payload types exercised on both JSON and bitstring transport.

### Deliverables

- [ ] `tests/test_binary.py` — TS-BIN-01..08
- [ ] `harness/node.py` — `MockBinaryDataHandlerProtocol` (captures binary calls for assertion)
- [ ] `tests/conftest.py` — `binary_topology` fixture (T8, protocol V2)

**Success criterion**: All binary payload types tested; bitstring codec round-trip verified.

---

## Phase 5 · Complex Topologies

**Goal**: Tree, diamond, mesh topologies exercised.

### Deliverables

- [ ] `tests/conftest.py` — `tree_topology` (T4), `diamond_topology` (T5), `mesh_topology` (T6)
- [ ] `tests/test_escalate.py` extended — TS-ESC-03 on tree
- [ ] `tests/test_broadcast.py` extended — TS-BC-04 on tree
- [ ] `tests/test_ping_cascade_query.py` — TS-PING-01, TS-CASC-01, TS-QUERY-01

**Success criterion**: Multi-hop message delivery validated across all complex topologies.

---

## Phase 6 · Stress & Load

**Goal**: No message loss at scale.

### Deliverables

- [ ] `harness/topology.py` — `InProcessTransport` shim for high-node-count tests
- [ ] `tests/conftest.py` — `stress_topology` fixture (T7, 50 satellites)
- [ ] `tests/test_stress.py` — TS-LOAD-01..03
- [ ] Pytest markers: `@pytest.mark.slow` on stress tests; excluded from default run

**Success criterion**: 50-satellite BROADCAST zero-loss; 1000-message sustained throughput stable.

---

## Phase 7 · CI Integration

**Goal**: The test suite runs automatically on every push.

### Deliverables

- [ ] `pyproject.toml` with pytest config, markers, timeout defaults
- [ ] GitHub Actions workflow (or equivalent CI) that:
  - Installs all HiveMind workspace packages as editable
  - Runs `pytest -m "not slow"` on every PR
  - Runs `pytest -m slow` on nightly schedule
- [ ] Coverage report (target: >90% of `hivemind_core/protocol.py` lines)
- [ ] Badge in README

---

## Backlog (Future)

- **Chaos tests** — disconnect mid-handshake, drop connection mid-broadcast, verify recovery
- **Fuzz testing** — send malformed payloads and truncated bitstrings; verify no crash, graceful error handling
- **Protocol downgrade attack** — satellite claims wrong protocol version; verify rejection
- **RENDEZVOUS stub tests** — once the type is fully specified
- **Cipher × encoding matrix** — exhaustive test of all `SupportedCiphers` × `SupportedEncodings` combos
- **Key rotation** — satellite sends new HANDSHAKE on existing connection; verify new key used
- **Integration smoke test** (optional, separate suite) — real WebSocket using `hivemind-websocket-protocol` to confirm transport layer; gated behind `pytest -m integration`

---

## Phase Dependencies

```
Phase 1 (Foundation)
    └── Phase 2 (Core Messages)
            ├── Phase 3 (Routing & ACL)
            │       └── Phase 5 (Complex Topologies)
            │               └── Phase 6 (Stress)
            └── Phase 4 (Binary)

Phase 7 (CI) can start in parallel with Phase 3+
```

---

## Estimated Test Count by Phase

| Phase | New Tests | Cumulative |
|---|---|---|
| 1 · Foundation | 8 | 8 |
| 2 · Core Messages | 15 | 23 |
| 3 · Routing & ACL | 12 | 35 |
| 4 · Binary | 8 | 43 |
| 5 · Complex Topologies | 10 | 53 |
| 6 · Stress | 3 | 56 |
| **Total** | | **~56 test functions** (~84 assertions across them) |
