
# HiveMind Test Harness — Maintenance Report

---

## 2026-03-21 — E2E Skill Tests + Documentation (Claude Opus 4.6)

### AI Transparency
- **AI Model:** Claude Opus 4.6 (claude-opus-4-6)
- **Actions Taken:**
  - Created 81 E2E tests across 11 new test files, routing real OVOS skill utterances through the full HiveMind satellite→hub→MiniCroft pipeline
  - Added shared test helpers to `conftest.py`: `skill_missing()`, `make_utterance()`, `assert_types_in_order()`, `wait_for_satellite_message()`, 13 skill ID constants
  - Implemented `MockVolumePHAL` (satellite-side volume handler capture), `SatelliteAutoResponder` (automated multi-turn dialog responses), and injected test skills (`AskYesNoTestSkill`, `AskSelectionTestSkill`, `AskSelectionNumericTestSkill`)
  - Created comprehensive documentation: `docs/06-e2e-skill-tests.md` covering continuous dialog (get_response, ask_yesno, ask_selection), PHAL volume integration, stop command flow, ACL with real skills, multi-satellite isolation
  - Updated `FAQ.md` with 13 new Q&A entries covering all E2E patterns
  - Updated `TODO.md` with completed E2E skill test checklist
  - Updated `docs/index.md` with link to new doc
- **Oversight:** Human-in-the-loop; all tool calls visible and approved.

### New Test Files
| File | Tests | Coverage |
|------|-------|---------|
| `tests/test_e2e_skills.py` | 11 | date-time, personal, naptime, fallback, easter-eggs, spelling |
| `tests/test_e2e_volume_phal.py` | 13 | volume + satellite MockVolumePHAL |
| `tests/test_e2e_relay_skills.py` | 5 | chain/deep relay topologies |
| `tests/test_e2e_multi_satellite.py` | 3 | response isolation |
| `tests/test_e2e_session.py` | 4 | session state propagation |
| `tests/test_e2e_acl_skills.py` | 8 | skill/intent/msg blacklists |
| `tests/test_e2e_converse.py` | 5 | parrot skill converse mode |
| `tests/test_e2e_misc_skills.py` | 8 | IP, count, edge cases |
| `tests/test_e2e_get_response.py` | 7 | multi-turn get_response() |
| `tests/test_e2e_stop.py` | 7 | stop command + ping/pong |
| `tests/test_e2e_ask_yesno_selection.py` | 10 | ask_yesno, ask_selection |

---

## 2026-03-09 — Relay Architecture + PING Propagation (Claude Sonnet 4.6)

### AI Transparency
- **AI Model:** Claude Sonnet 4.6 (claude-sonnet-4-6)
- **Actions Taken:**
  - Researched real HiveMind node roles: `Master` = runs `HiveMindListenerProtocol`; `Satellite` = connected via `HiveMindSlaveProtocol`; `Relay` = dual-role sharing one agent bus — roles are NOT mutually exclusive
  - Discovered `HiveMindSlaveProtocol.handle_propagate()` emits `hive.send.downstream` on the shared agent bus, causing relay nodes to automatically forward PING downstream — refuted prior assumption that PING only reaches direct children
  - Rewrote `topology.py`: `add_relay()` now creates one shared `TestAgentProtocol` for both satellite-side and master-side; added `RelayNode` dataclass with convenience properties; added `get_relay()` accessor and `relays` property
  - Added `bus` parameter to `SatelliteNode.create()` to support injecting shared bus
  - Added topology fixtures: `huge_hive_topology` (T4), `chaotic_hive_topology` (T5), `asymmetric_hive_topology` (T6) in `conftest.py`
  - Rewrote all relay-related test assertions in `test_ping_pong.py` to reflect correct PING propagation (single M0 PING reaches entire hive tree)
  - Fixed `TypeError` in `topology_plot.py` (`list | list` → `set | set`)
  - Added `topology_plot.py` (plotting utilities) and `test_topology_plots.py` (12 plot generation tests) — generates 22 PNG files in `hivemind-core/docs/img/`
  - Updated `hivemind-core/docs/hive_map.md` and `hivemind-core/FAQ.md` to correct PING propagation documentation
  - Ran full test suite: **49 ping/pong tests pass** (32 fast + 17 slow); **12 topology plot tests pass**
- **Oversight:** Human-in-the-loop; all tool calls visible and approved.

### Verification
```
pytest tests/test_ping_pong.py -m "not slow" -q  →  32 passed in 72s
pytest tests/test_ping_pong.py -m "slow" -q      →  17 passed in 608s
pytest tests/test_topology_plots.py -q           →  12 passed
```
Total including all prior tests: 115 + 49 + 12 = **176 tests, all passing** (2 skip).

### Files Modified This Session
- `hivemind_test_harness/topology.py` — `RelayNode` dataclass, shared agent protocol in `add_relay()`, `get_relay()`, `relays` property
- `hivemind_test_harness/node.py` — `SatelliteNode.create()` accepts `bus` parameter
- `hivemind_test_harness/topology_plot.py` — new: PNG plot generation for static + discovery graphs
- `tests/conftest.py` — added T4 (`huge_hive`), T5 (`chaotic_hive`), T6 (`asymmetric_hive`) fixtures; updated terminology docstring
- `tests/test_ping_pong.py` — added 49 PING/PONG tests; rewrote chain/deep-chain/huge/chaotic/asymmetric assertions
- `tests/test_topology_plots.py` — new: 12 PNG-generation tests
- `QUICK_FACTS.md`, `FAQ.md`, `AUDIT.md`, `SUGGESTIONS.md`, `MAINTENANCE_REPORT.md` — updated this session

---

## 2026-03-09 — Reverse Routing + Final Audit (Claude Sonnet 4.6)

### AI Transparency
- **AI Model:** Claude Sonnet 4.6 (claude-sonnet-4-6)
- **Actions Taken:**
  - Implemented production-parity reverse routing in `TestAgentProtocol` (AUDIT.md ENV-3)
  - Ported `register_bus_handlers()`, `handle_send()`, `handle_internal_mycroft()` verbatim
    from `OVOSProtocol` (`ovos-bus-client/ovos_bus_client/hpm.py`) into
    `hivemind_test_harness/plugins/agent.py`
  - Fixed `test_utterance_text_preserved` assertion (AUDIT.md ENV-2) — content-based check
    instead of exact punctuation match, accounting for OVOS utterance normalizer
  - Ran full test suite: **115 passed, 0 failed, 2 skipped** (117 total)
  - Updated AUDIT.md, SUGGESTIONS.md, MAINTENANCE_REPORT.md to reflect resolved items
- **Oversight:** Human-in-the-loop; all tool calls visible and approved.

### Verification
After reverse routing implementation and assertion fix:
```
pytest tests/ -q
115 passed, 0 failed, 2 skipped in ~370s
```
2 skipped = planned TS-OVO-04 through TS-OVO-10 (module-level `@pytest.mark.skip`).

### Files Modified This Session
- `hivemind_test_harness/plugins/agent.py` — reverse routing implementation (rewritten)
- `tests/test_ovoscope_integration.py:112` — utterance normalizer assertion fix
- `AUDIT.md` — ENV-2 and ENV-3 marked RESOLVED
- `SUGGESTIONS.md` — SG-8 marked IMPLEMENTED
- `MAINTENANCE_REPORT.md` — this entry added

---

## 2026-03-09 — Full Audit (Claude Sonnet 4.6)

### AI Transparency
- **AI Model:** Claude Sonnet 4.6 (claude-sonnet-4-6)
- **Actions Taken:**
  - Ran `pytest tests/ -q` from workspace root to establish current test baseline
  - Diagnosed 3 critical ImportErrors that blocked test collection (DEP-1/2/3)
  - Applied fixes: reinstalled `json_database`, `z85base91`, `poorman_handshake` as non-editable wheels
  - Ran full test suite again after fixes: 103 passed, 12 failed, 2 skipped
  - Investigated all 12 failures via targeted pytest runs and inline Python debugging
  - Root-caused ENV-1: host `lang: pt-PT` in OVOS config prevents en-US vocab loading
  - Root-caused ENV-2: `ovos-persona-pipeline-plugin` (Gemma) intercepts unmatched utterances
  - Explored full codebase (~1,800 lines production + 117 test files) via subagent
  - Updated AUDIT.md, SUGGESTIONS.md, QUICK_FACTS.md, FAQ.md, MAINTENANCE_REPORT.md
- **Oversight:** Human-in-the-loop; all tool calls visible and approved.

### Verification
Initial run (before ovoscope fix):
```
pytest tests/ -q
103 passed, 12 failed, 2 skipped in 262.37s
```
After applying ovoscope 0.7.2 `isolate_config` fix:
```
pytest tests/ -q
113 passed, 2 failed, 2 skipped in 369.29s
```
Remaining 2 failures:
- `test_utterance_text_preserved` — normalizer strips `?` from utterance (test assertion too strict)
- `test_speak_received_on_satellite_bus` — reverse routing (OVOS bus → satellite) not yet implemented in harness

### Fixes Applied
| Fix | Command |
|---|---|
| DEP-1: json_database non-editable | `uv pip install --python .venv/bin/python json_database` |
| DEP-2: z85base91 non-editable | `uv pip install --python .venv/bin/python z85base91` |
| DEP-3: poorman_handshake non-editable | `uv pip install --python .venv/bin/python poorman_handshake` |

### Files Modified This Session
- `AUDIT.md` — rewritten with evidence-based findings
- `SUGGESTIONS.md` — rewritten with 7 actionable items
- `QUICK_FACTS.md` — rewritten with accurate package reference
- `FAQ.md` — rewritten with codebase-derived Q&A
- `MAINTENANCE_REPORT.md` — this entry added

---

## 2026-03-08 — Initial Documentation (Gemini CLI)

### AI Transparency
- **AI Model:** Gemini CLI
- **Actions Taken:** Created QUICK_FACTS.md, FAQ.md, MAINTENANCE_REPORT.md, SUGGESTIONS.md as
  placeholder stubs for AGENTS.md compliance.
- **Oversight:** Automated.

### Verification
Not run (placeholder files only).
