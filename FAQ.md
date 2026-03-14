
# HiveMind Test Harness — FAQ

---

## General

### What is this repository for?
`hivemind-test-harness` is an in-process protocol testing framework for HiveMind. It lets you
spin up MasterNode / SatelliteNode pairs that communicate via direct function calls — no network,
no sockets. You can test the full HiveMind protocol (ESCALATE, PROPAGATE, BROADCAST, ACL,
handshake, binary payloads) deterministically in plain `pytest` tests.

### How do I create a minimal topology?
```python
from hivemind_test_harness import TopologyBuilder

b = TopologyBuilder()
b.add_master("M0")
b.add_satellite("S0", upstream=b.get_master("M0"))
b.start_all()
s0 = b.get_satellite("S0")
s0.send(Message("recognizer_loop:utterance", {"utterances": ["hello"]}))
b.get_master("M0").wait_for("BUS")
b.stop_all()
```

### What fixtures are available in conftest.py?
- `minimal_topology` (T1) — 1 master, 1 satellite
- `star_topology(N)` (T2) — 1 master, N satellites (default 3)
- `admin_star_topology` (T2a) — 1 master, 3 satellites (S0 is admin)
- `chain_topology` (T3) — M0 → relay R1 → S0
- `deep_chain_topology` (T3a) — M0 → R1 → R2 → S0
- `huge_hive_topology` (T4) — 10 relay masters, 37 leaf satellites (seeded-random, `@pytest.mark.slow`)
- `chaotic_hive_topology` (T5) — multi-level irregular tree, 11 nodes (`@pytest.mark.slow`)
- `asymmetric_hive_topology` (T6) — depth-10 arm + 3 short arms (`@pytest.mark.slow`)

---

## Running Tests

### How do I run the full test suite?
```bash
"/home/miro/PycharmProjects/HiveMind Workspace/.venv/bin/python" -m pytest tests/ -v
```

### Why do all tests fail with `ImportError: cannot import name 'JsonConfigXDG'`?
Three packages must be installed as non-editable wheels, not with `-e`:
- `json_database`, `z85base91`, `poorman_handshake`

Fix (run once after any `uv sync`):
```bash
uv pip install --python .venv/bin/python json_database z85base91 poorman_handshake
```

### Why do 12 OvoScope/HelloWorld tests fail on my system?
Two environment-specific causes:

1. **Wrong language (`lang: pt-PT`)**: Your OVOS config has a non-English language. Skills only
   load vocab for the configured language, so Adapt can't match English utterances.
   Fix: Set `lang: en-US` in `~/.config/mycroft/mycroft.conf`.

2. **Persona pipeline installed**: `ovos-persona-pipeline-plugin` (e.g., Gemma) intercepts all
   unmatched utterances, preventing `complete_intent_failure` from firing.
   Fix: Disable the persona pipeline for tests, or wait for `OvoscopeAgentProtocol` to accept
   a `lang` parameter (see `SUGGESTIONS.md:SG-1`).

### Why do I see sklearn `InconsistentVersionWarning` during OvoScope tests?
Padatious models were trained with sklearn 1.6.1 but the workspace uses 1.8.0.
Fix: Delete `~/.local/share/mycroft/intent_cache/` to retrain models with current sklearn.

---

## Architecture

### How does in-process communication work?
`TestNetworkProtocol.connect_satellite()` calls `HiveMindListenerProtocol.handle_new_connection()`
directly, bypassing WebSocket. `InProcessHiveShim.emit()` routes messages to the master's
`_receive_from_client()` handler without serialization. The result: full handshake and all
protocol logic runs synchronously in-process.

### How does `MasterNode.wait_for()` / `SatelliteNode.wait_for()` work?
`MessageRecorder` maintains a dict of `threading.Event` waiters per `msg_type`. When
`record()` is called with a matching type, the event is set. `wait_for()` waits on that event
with a timeout. This is thread-safe via `threading.Lock`.

### What is `_instrument_master()` in `node.py`?
`_instrument_master()` (line 407–458) monkey-patches the master's `handle_new_connection()`
and each client's `send_msg` callable to transparently record every inbound/outbound
HiveMessage without modifying the protocol plugins.

### What is a relay node?
In HiveMind, node roles are **not mutually exclusive**:
- **Master**: any node running `HiveMindListenerProtocol` (accepts downstream satellites)
- **Satellite**: any node connected to a master via `HiveMindSlaveProtocol`
- **Relay**: a node that is simultaneously a satellite (upstream connection) AND a master (downstream listener), sharing **one** `TestAgentProtocol`/`FakeBus`

### How does relay topology work?
`TopologyBuilder.add_relay()` creates one shared `TestAgentProtocol` and passes it to both:
1. A `SatelliteNode` (upstream connection to parent master) — uses `shared_bus`
2. A `MasterNode` (downstream listener for child satellites) — uses `shared_agent`

Use `builder.get_relay(name)` to access the combined `RelayNode` view with convenience properties
(`hm_protocol`, `slave_protocol`, `peer`, `identity`).

### Does PING propagate through relay nodes?
Yes. When a relay's satellite side receives `PROPAGATE(PING)`, `HiveMindSlaveProtocol.handle_propagate()`
emits `hive.send.downstream` on the shared bus. `TestAgentProtocol.handle_send` picks this up and
re-broadcasts `PROPAGATE(PING)` to all downstream satellites of the relay's master side.
A single `M0.send_to_all(PROPAGATE(PING))` therefore populates `M0.hive_mapper` with **every node
in the entire hive tree** — not just direct children.

---

## Protocol Coverage

### Which HiveMessage types are tested?
All 9 implemented types: BUS, BROADCAST, ESCALATE, PROPAGATE, INTERCOM, SHARED_BUS,
HELLO, GOODBYE, BINARY.

### Which types are NOT yet implemented in hivemind-core?
QUERY, CASCADE, RENDEZVOUS. Stub tests in `test_unimplemented_types.py` verify they raise
`NotImplementedError`. PING and PONG are fully implemented — see `test_ping_pong.py`.

### How do I test binary protocol handlers (audio, images)?
Use `TestBinaryProtocol` (available as `master.binary_protocol`):
```python
m = b.get_master("M0")
s0.send(HiveMessage(HiveMessageType.BINARY, payload=audio_bytes))
m.binary_protocol.assert_called("handle_microphone_input", count=1)
call = m.binary_protocol.last_call("handle_microphone_input")
```

---

## Topology Plots

### How do I generate PNG topology diagrams?
Install optional dependencies first:
```bash
uv pip install --python ".venv/bin/python" matplotlib networkx
```
Then use the plotting utilities:
```python
from hivemind_test_harness.topology_plot import (
    plot_topology_builder,       # static wiring diagram
    plot_hive_mapper,            # live PING/PONG discovery graph
    plot_topology_and_discovery, # both in one call
)
plot_topology_builder(builder, "topology.png", title="My topology", layout="spring")
```

### How do I regenerate the docs images?
```bash
cd "HiveMind Workspace"
.venv/bin/python -m pytest hivemind-test-harness/tests/test_topology_plots.py -v
```
PNG files are written to `hivemind-core/docs/img/`.

### What layouts are available?
`"spring"` (default), `"kamada_kawai"`, `"shell"`, `"circular"`, `"spectral"`.

---

## OvoScope Integration

### How do I use OvoscopeAgentProtocol?
```python
from hivemind_test_harness.plugins.ovoscope_agent import OvoscopeAgentProtocol

agent = OvoscopeAgentProtocol(skill_ids=["ovos-skill-hello-world.openvoiceos"])
b = TopologyBuilder()
b.add_master("M0", agent_protocol=agent)
b.add_satellite("S0", upstream=b.get_master("M0"))
b.start_all()

cap = agent.new_capture()
b.get_satellite("S0").send(Message("recognizer_loop:utterance", {"utterances": ["hello world"]}))
messages = cap.wait(timeout=15)
assert any(m.msg_type == "speak" for m in messages)

b.stop_all()
agent.shutdown()
```

### OvoScope tests hang for 2 minutes on first run — is this normal?
Yes. MiniCroft needs to train Padatious intent models on first run. Subsequent runs use
cached models and start in seconds.

### How do I test for `complete_intent_failure` without skill interference?
Use `OvoscopeAgentProtocol(skill_ids=[])` (no skills) and ensure `ovos-persona-pipeline-plugin`
is not intercepting utterances (see FAQ: "Why do 12 tests fail?").
