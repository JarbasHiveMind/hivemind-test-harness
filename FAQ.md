
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
No. This is a known coverage gap. Nothing under `tests/` exercises the binarize
(bitstring) encoding path — `grep -r binarize tests/` returns nothing. An
earlier version of this FAQ pointed at `test_binarize_e2e.py`, a file that does
not exist in this repo.

What IS covered: `tests/test_binary.py` covers all seven
`HiveMindBinaryPayloadType` values over the normal JSON encoding, and
`tests/test_e2e_binary_skill.py` covers a binary payload reaching an OVOS skill.
Neither negotiates `binarize: True`.

Closing the gap needs a test that patches `get_server_config` to advertise
`binarize: True`, then asserts a BUS message and a BINARY payload round-trip
downstream through the bitstring encode/decode path. Note that upstream
(satellite-to-master) bypasses serialization in the in-process shim, so only
the downstream direction can be exercised in-process.

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

---

## E2E Skill Tests (81 tests)

### How do I run the E2E skill tests?
```bash
cd "HiveMind Workspace/hivemind-test-harness"
uv run pytest tests/test_e2e_*.py -v --timeout=300
```
Tests auto-skip if required skills are not installed.

### What skills do I need to install?
```bash
for skill in hello-world date-time personal naptime volume easter-eggs spelling \
             ip count parrot randomness fallback-unknown; do
    uv pip install -e "../OpenVoiceOS Workspace/Skills/ovos-skill-${skill}"
done
```

### How does get_response() work through HiveMind?
The skill calls `get_response("dialog")` → enables `response_mode` on the session → speaks a question with `expect_response=True`. HiveMind routes the speak to the satellite. When the satellite sends the next utterance with the same `session_id`, HiveMind routes it back to the hub. MiniCroft's converse pipeline intercepts it (because `response_mode` is active) and delivers it to the skill's waiting `get_response()`. **Requirement:** pipeline must include `"ovos-converse-pipeline-plugin"`.

### How does ask_yesno() differ from get_response()?
`ask_yesno()` wraps `get_response()` — it speaks the question, gets the response, then passes it through a `YesNoSolver` plugin to normalize to `"yes"`, `"no"`, or the raw string. The HiveMind flow is identical.

### How does ask_selection() work through HiveMind?
`ask_selection()` speaks each option (or a comma-separated list), then calls `get_response()` for the user's choice. The response is matched against options via `OptionMatcherEngine` plugin (fuzzy match). With `numeric=True`, options are spoken as a numbered menu.

### How do PHAL volume tests work if there's no real audio hardware?
`MockVolumePHAL` registers handlers on `satellite.internal_bus` for `mycroft.volume.*` messages. This proves HiveMind delivers volume control messages to the satellite where the real PHAL plugin would run. The volume *skill* runs on the hub; the PHAL *handler* runs on the satellite.

### Why do volume increase/decrease tests need a hub-side mock?
The volume skill's `_query_volume()` calls `bus.wait_for_response("mycroft.volume.get")` on the hub's MiniCroft bus to get the current volume before adjusting it. Without a mock responder on `agent.bus`, the call times out. Register one in the fixture: `agent.bus.on("mycroft.volume.get", lambda m: agent.bus.emit(m.response({"percent": 0.5})))`.

### How does the stop command flow through HiveMind?
Satellite sends "stop" → hub's StopService sends `{skill_id}.stop.ping` to each active skill → skill responds with `skill.stop.pong` (can_stop=True/False) → StopService sends `{skill_id}.stop` → skill's `stop_session()` sets active flag to False → counting loop exits. **Requirement:** pipeline must include `"ovos-stop-pipeline-plugin"`.

### How do I test a custom skill without packaging it?
Use `extra_skills` to inject skill classes directly into MiniCroft:
```python
agent = OvoscopeAgentProtocol(
    skill_ids=[],
    extra_skills={"my-test-skill.test": MyTestSkillClass}
)
```

### Why do ACL tests need separate fixtures?
ACL (`allowed_types`, plus `skill_blacklist` and `intent_blacklist`) is set at satellite registration time and is fixed for the life of the connection. Each ACL scenario needs its own topology with different satellite registrations. `msg_blacklist` is accepted by `register_satellite` for API compatibility and is ignored: hivemind-core is whitelist-only.

### How does multi-satellite response isolation work?
HiveMind tracks the originating satellite via `message.context["destination"]`. Responses route only to the satellite that sent the utterance — other satellites are unaffected. Tested in `test_e2e_multi_satellite.py`.

### What is SatelliteAutoResponder?
A test helper that listens for `speak` messages with `expect_response=True` on the satellite's `internal_bus` and automatically sends predefined responses back through HiveMind. Used for testing multi-turn dialogs (get_response, ask_yesno, ask_selection).

### How does shared_bus mode work with real skills?
Satellites created with `shared_bus=True` passively mirror all internal bus events upstream as `SHARED_BUS` HiveMessages. When a skill response (speak) arrives on a shared satellite's bus, the master sees it mirrored back. Normal satellites do NOT mirror. Tested in `test_e2e_shared_bus.py`.

### How does relay ACL stacking work?
Blacklists apply at each hop. If relay R1 has `skill_blacklist=["hello-world"]` as a client of M0, all leaf satellites behind R1 inherit that restriction. R1's ACL for S0 and M0's ACL for R1 are applied independently.

### How do OCP messages route through HiveMind?
OCP messages (`ovos.common_play.query.response`, `ovos.common_play.track_info`) are standard bus messages — they route through HiveMind like `speak`. Tested via injected `OCPTestSkill`.

### How does schedule_event() work through HiveMind?
Skills call `schedule_event(handler, delay)` on the hub. The callback fires on the hub's bus after the delay. Any `speak` from the callback routes back to the satellite via HiveMind's reverse routing. Tested with injected `SchedulerTestSkill`.

### How does binary audio relate to skill responses?
Satellite sends `BINARY(RAW_AUDIO)` → master's binary protocol handles it → STT transcribes → `recognizer_loop:utterance` → skill matches → speak routes back. In tests, STT is not available so binary delivery and utterance injection are tested as linked steps.

### How does the dictation skill test converse + stop?
Dictation activates converse mode — all subsequent utterances are captured (not intent-matched). Stop deactivates it. This tests the most complex OVOS skill interaction: a skill that hijacks the entire conversation pipeline through HiveMind.

### Does language affect intent matching through HiveMind?
Yes. The session `lang` propagates through HiveMind. English-only skills (hello-world) won't match German utterances sent with `lang="de-DE"`.

### What shared helpers are in conftest.py?
- `skill_missing(*skill_ids)` — check if skills are installed (for skipif)
- `make_utterance(text, pipeline, session_id, lang)` — build correctly-formed utterance messages
- `assert_types_in_order(messages, *types)` — assert message types appear in sequence
- `wait_for_satellite_message(satellite, msg_type, timeout)` — block until message arrives on satellite bus
- Skill ID constants: `SKILL_HELLO`, `SKILL_DATETIME`, `SKILL_VOLUME`, `SKILL_PERSONAL`, `SKILL_NAPTIME`, `SKILL_FALLBACK`, `SKILL_EASTER_EGGS`, `SKILL_SPELLING`, `SKILL_IP`, `SKILL_COUNT`, `SKILL_PARROT`, `SKILL_RANDOMNESS`, `SKILL_DICTATION`

### What does `test_route_metadata.py` cover?
8 integration tests (TS-ROUTE-HOP-01..08): BUS hop data, ESCALATE/PROPAGATE through relays, route preservation through `_unpack_message`, hop structure validation, QUERY/CASCADE response route carriage, PING route feeding HiveMapper.
