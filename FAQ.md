
# HiveMind Test Harness — FAQ

---

## General

### What is this repository for?
In-process protocol testing framework for HiveMind. Spins up MasterNode/SatelliteNode pairs communicating via direct function calls (no network). Tests the full protocol (ESCALATE, PROPAGATE, BROADCAST, ACL, handshake, binary) deterministically in `pytest`.

### What fixtures are available in conftest.py?
- `minimal_topology` (T1) — 1 master, 1 satellite
- `star_topology(N)` (T2) — 1 master, N satellites (default 3)
- `admin_star_topology` (T2a) — 1 master, 3 satellites (S0 is admin)
- `chain_topology` (T3) — M0 -> relay R1 -> S0
- `deep_chain_topology` (T3a) — M0 -> R1 -> R2 -> S0
- `huge_hive_topology` (T4) — 10 relay masters, 37 leaf satellites (`@pytest.mark.slow`)
- `chaotic_hive_topology` (T5) — multi-level irregular tree, 11 nodes (`@pytest.mark.slow`)
- `asymmetric_hive_topology` (T6) — depth-10 arm + 3 short arms (`@pytest.mark.slow`)

---

## Running Tests

### How do I run the full test suite?
```bash
"/home/miro/PycharmProjects/HiveMind Workspace/.venv/bin/python" -m pytest tests/ -v
```

### Why do all tests fail with `ImportError: cannot import name 'JsonConfigXDG'`?
Three packages must be installed as non-editable wheels:
```bash
uv pip install --python .venv/bin/python json_database z85base91 poorman_handshake
```

### Why do 12 OvoScope/HelloWorld tests fail on my system?
1. **Wrong language (`lang: pt-PT`)**: Skills only load vocab for the configured language. Fix: Set `lang: en-US` in `~/.config/mycroft/mycroft.conf`.
2. **Persona pipeline installed**: `ovos-persona-pipeline-plugin` intercepts unmatched utterances. Fix: Disable for tests.

### Why do I see sklearn `InconsistentVersionWarning` during OvoScope tests?
Padatious models were trained with a different sklearn version. Fix: Delete `~/.local/share/mycroft/intent_cache/` to retrain.

---

## Architecture

### How does in-process communication work?
`TestNetworkProtocol.connect_satellite()` calls `HiveMindListenerProtocol.handle_new_connection()` directly. `InProcessHiveShim.emit()` routes messages to `_receive_from_client()` without serialization. Full handshake and protocol logic runs synchronously in-process.

### What is a relay node?
A node that is simultaneously a satellite (upstream) AND a master (downstream), sharing one `TestAgentProtocol`/`FakeBus`. `TopologyBuilder.add_relay()` creates one shared agent for both sides.

### Does PING flood propagate through relay nodes?
Yes. The relay's satellite side receives `PROPAGATE(PING)`, emits `hive.send.downstream` on the shared bus, which re-broadcasts to all downstream satellites. Relay nodes share `_seen_flood_ids` between master and satellite sides to prevent duplicate announcements.

---

## Protocol Coverage

### Which HiveMessage types are tested?
All 9 implemented types: BUS, BROADCAST, ESCALATE, PROPAGATE, INTERCOM, SHARED_BUS, HELLO, GOODBYE, BINARY.

### Is binarize mode tested?
Yes. `test_binarize_e2e.py` (7 tests) covers handshake negotiation (both enabled/disabled), BUS message downstream roundtrip, and BINARY payload downstream through the bitstring encode/decode path. Patch `get_server_config` to return `binarize: True`. Note: upstream (satellite-to-master) bypasses serialization in the in-process shim, so binarize encoding is only exercised on the downstream path.

### Which types are NOT yet implemented in hivemind-core?
QUERY, CASCADE, RENDEZVOUS. Stub tests in `test_unimplemented_types.py` verify they raise `NotImplementedError`.

---

## Topology Plots

### How do I generate PNG topology diagrams?
```bash
uv pip install --python ".venv/bin/python" matplotlib networkx
```
Use `plot_topology_builder`, `plot_hive_mapper`, or `plot_topology_and_discovery` from `hivemind_test_harness.topology_plot`.

### How do I regenerate the docs images?
```bash
cd "HiveMind Workspace"
.venv/bin/python -m pytest hivemind-test-harness/tests/test_topology_plots.py -v
```
PNGs are written to `hivemind-core/docs/img/`.

---

## OvoScope Integration

### OvoScope tests hang for 2 minutes on first run — is this normal?
Yes. MiniCroft trains Padatious intent models on first run. Subsequent runs use cached models.

### How do I test for `complete_intent_failure` without skill interference?
Use `OvoscopeAgentProtocol(skill_ids=[])` and ensure `ovos-persona-pipeline-plugin` is not intercepting utterances.
