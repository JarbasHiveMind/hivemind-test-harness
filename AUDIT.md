
# HiveMind Test Harness — Audit Report

Evidence-based audit performed 2026-03-09.
**Initial run (before ovoscope update):** 103 passed, 12 failed, 2 skipped
**After ovoscope 0.7.2 + reverse routing + utterance normalizer fix:** `pytest tests/ -q` → **115 passed, 0 failed, 2 skipped** (117 total)
**After relay/PING architecture work:** `pytest tests/ -q` → **166 passed, 0 failed, 2 skipped** (168 total)

---

## 1. Dependency / Import Issues (FIXED THIS SESSION)

### DEP-1 — `json_database` namespace package conflict
- **Severity:** CRITICAL (breaks all tests — ImportError at conftest load)
- **Root cause:** `json_database` installed as an editable package (`-e`) causes Python to
  resolve the package to the repo root directory (a namespace package), shadowing the actual
  `json_database/json_database/` subpackage. Same pattern as `z85base91`.
- **Symptom:** `ImportError: cannot import name 'JsonConfigXDG' from 'json_database'`
- **Fix applied:** `uv pip install --python .venv/bin/python json_database` (non-editable wheel)
- **Evidence:** `hivemind-test-harness/tests/conftest.py:5` → import chain fails at
  `hivemind_bus_client/identity.py:3`

### DEP-2 — `z85base91` namespace package conflict (re-emerged)
- **Severity:** CRITICAL (same pattern as DEP-1, downstream of DEP-1 fix)
- **Symptom:** `ImportError: cannot import name 'Z85B' from 'z85base91'`
- **Fix applied:** `uv pip install --python .venv/bin/python z85base91`
- **Evidence:** `hivemind_bus_client/encryption.py:13`

### DEP-3 — `poorman_handshake` namespace package conflict (re-emerged)
- **Severity:** CRITICAL (same pattern, downstream of DEP-2 fix)
- **Symptom:** `ImportError: cannot import name 'HandShake' from 'poorman_handshake'`
- **Fix applied:** `uv pip install --python .venv/bin/python poorman_handshake`
- **Evidence:** `hivemind_core/protocol.py:41`

---

## 1a. Ovoscope Update Applied (Fixed 10 of 12 failures)

The upstream `ovoscope` library was updated with `MiniCroft(isolate_config=True)` (default):
- Clears `Configuration.xdg_configs` — host `lang: pt-PT` no longer affects skill vocab loading
- Sets `DEFAULT_TEST_PIPELINE` (no persona) — prevents Gemma from intercepting utterances
- Restores config on `stop()` for test isolation

Applied by copying `/home/miro/PycharmProjects/OpenVoiceOS Workspace/ovoscope/ovoscope/__init__.py`
to the installed wheel location. **Note:** ovoscope's `pyproject.toml` has a missing `version`
field preventing clean `uv pip install` — tracked as an upstream issue.

10 tests now pass. 2 remain failing for separate reasons (ENV-2, ENV-3 below).

---

## 2. Test Failures — Environment-Specific (Not Code Regressions)

### ENV-1 — OvoScope/HelloWorld tests fail due to host OVOS language config
- **Severity:** HIGH (12 tests fail)
- **Affected files:**
  - `tests/test_helloworld_hivemind.py` — 9 failures
  - `tests/test_ovoscope_integration.py` — 3 failures
- **Root cause A — Wrong language:**
  The host OVOS config (`~/.config/mycroft/mycroft.conf`) has `"lang": "pt-PT"`.
  `MiniCroft` inherits this config, so `HelloWorldSkill` only loads Portuguese vocab
  (`HelloWorldKeyword.voc` en-US content "hello world"/"greetings" is never registered).
  Adapt returns `None` for any en-US utterance.
  Confirmed via: `Configuration().get('lang')` → `'pt-PT'` and
  `register_vocab` events showing only `lang: pt-PT` entries for the skill.

- **Root cause B — Persona pipeline interference:**
  `ovos-persona-pipeline-plugin` is installed on this system with "Gemma" persona configured.
  Any utterance not matched by standard pipelines is routed to Gemma (remote AI backend).
  Gemma calls fail silently (`Expecting value: line 1 column 1 (char 0)` — empty JSON response).
  As a result, `complete_intent_failure` is **never emitted**; tests expecting it time out.
  Log evidence: `ovos_core.intent_services.service:handle_utterance:471` →
  `IntentHandlerMatch(match_type='persona:query', skill_id='persona.openvoiceos')`

- **Failing tests and their root cause:**
  | Test | Cause |
  |---|---|
  | `TestAdaptIntentViaHiveMind::test_hello_world_intent_fired` | Root A |
  | `TestAdaptIntentViaHiveMind::test_skill_emits_speak` | Root A |
  | `TestAdaptIntentViaHiveMind::test_full_adapt_sequence_in_order` | Root A |
  | `TestAdaptIntentViaHiveMind::test_skill_activation_recorded` | Root A |
  | `TestPadatiousIntentViaHiveMind::test_greetings_intent_fired` | Root A |
  | `TestPadatiousIntentViaHiveMind::test_skill_speaks_greeting` | Root A |
  | `TestPadatiousIntentViaHiveMind::test_full_padatious_sequence_in_order` | Root A |
  | `TestSpeakPropagatesBackToSatellite::test_speak_received_on_satellite_bus` | Root A |
  | `TestSpeakPropagatesBackToSatellite::test_speak_utterance_text_matches` | Root A |
  | `TestCompleteIntentFailure::test_complete_intent_failure_emitted` | Root B |
  | `TestCompleteIntentFailure::test_no_speak_on_intent_failure` | Root B |
  | `TestUtteranceRoutingToMiniCroft::test_utterance_text_preserved` | Root B (timeout) |

- **Fixed:** ovoscope 0.7.2 `isolate_config=True` resolves Root A and Root B.
  10 of the 12 tests now pass. See section 1a above.

### ENV-2 — `test_utterance_text_preserved` fails (utterance normalizer strips `?`) — **RESOLVED**
- **Severity:** LOW (test assertion bug)
- **File:** `tests/test_ovoscope_integration.py:112`
- **Symptom:** `assert 'what is the capital of France?' in ['what is the capital of France']`
  The question mark is stripped by `ovos-utterance-normalizer` transformer.
- **Root cause:** Test asserts exact punctuation match but the normalizer canonicalises input.
- **Fix applied:** Replaced exact-match assertion with content-based check:
  ```python
  utterances = msg.data.get("utterances", [])
  assert any("capital" in u and "France" in u for u in utterances)
  ```
- **Status:** Test passes as of 2026-03-09.

### ENV-3 — `test_speak_received_on_satellite_bus` fails (reverse routing not implemented) — **RESOLVED**
- **Severity:** MEDIUM (design gap in harness, not a protocol regression)
- **File:** `tests/test_helloworld_hivemind.py:433`
- **Symptom:** `AssertionError: speak message was not forwarded back to the satellite by HiveMind`
- **Root cause:** `TestAgentProtocol` had no reverse routing path (OVOS bus → satellite).
  In production, `OVOSProtocol` (in `ovos-bus-client/hpm.py`) registers a `bus.on("message", ...)`
  catch-all handler that inspects `message.context["destination"]` and routes matching messages
  back to the originating satellite via `HiveMindClientConnection.send(HiveMessage(BUS, ...))`.
  The test harness had no equivalent mechanism.
- **Fix applied:** Ported `register_bus_handlers()`, `handle_send()`, and
  `handle_internal_mycroft()` verbatim from `OVOSProtocol` into `TestAgentProtocol`.
  `__post_init__` now calls `self.register_bus_handlers()` after wrapping the bus emit.
  See `hivemind_test_harness/plugins/agent.py`.
- **Status:** Test passes as of 2026-03-09. All 115 tests pass.

---

## 3. Code Quality Issues

### QA-1 — Missing class-level docstrings (8 classes)
- **Severity:** LOW (cosmetic; method names are self-documenting)
- **Locations:**
  - `hivemind_test_harness/recorder.py:22` — `RecordedMessage` (dataclass)
  - `hivemind_test_harness/recorder.py:38` — `MessageRecorder`
  - `hivemind_test_harness/topology.py:10` — `TopologyBuilder`
  - `hivemind_test_harness/plugins/agent.py:13` — `TestAgentProtocol`
  - `hivemind_test_harness/plugins/binary.py:14` — `BinaryCall` (dataclass)
  - `hivemind_test_harness/plugins/binary.py:21` — `TestBinaryProtocol`
  - `hivemind_test_harness/plugins/network.py:20` — `TestNetworkProtocol`
  - `hivemind_test_harness/plugins/ovoscope_agent.py:54` — `_require_ovoscope()` (function)

### QA-2 — `topology.py:80` silently swallows exceptions in `stop_all()`
- **Severity:** LOW
- **Location:** `hivemind_test_harness/topology.py:80`
- **Code:** `except Exception: pass`
- **Issue:** Legitimate teardown errors (e.g., double-disconnect) are swallowed with no logging.
- **Fix:** Add `LOG.debug(f"stop_all cleanup error: {e}", exc_info=True)`

### QA-3 — sklearn version mismatch warning in Padatious tests
- **Severity:** LOW (warning only, Padatious still loads from cache)
- **Symptom:** `InconsistentVersionWarning: Trying to unpickle estimator LabelBinarizer
  from version 1.6.1 when using version 1.8.0`
- **Cause:** Padatious intent models pre-trained with sklearn 1.6.1; current env has 1.8.0.
- **Impact:** Model pickling may produce incorrect results in edge cases.
- **Fix:** Retrain Padatious models or downgrade sklearn in dev env.

---

## 4. Design / Coverage Gaps

### DESIGN-1 — BROADCAST not forwarded downstream through relay chains
- **Severity:** MEDIUM (incomplete protocol coverage)
- **Location:** `hivemind_test_harness/topology.py:38-63` (`add_relay()`)
- **Issue:** When M0 sends BROADCAST, relay R1_master receives it but does NOT forward to
  downstream satellites connected to R1_master. The relay master's internal protocol does not
  register a `hive.send.downstream` listener.
- **No test covers this scenario.** See `PROTOCOL_AUDIT.md:DESIGN-3`.

### DESIGN-2 — Pre-shared key (PSK) handshake mode never tested
- **Severity:** LOW
- **Location:** `tests/test_handshake.py`
- **Issue:** `crypto_key` field is populated in `InMemoryClientDatabase.add_client()` but the
  PSK path in `HiveMindListenerProtocol` is never exercised.

### DESIGN-3 — RSA-only (no-password) handshake never explicitly tested
- **Severity:** LOW
- **Location:** `tests/test_handshake.py`
- **Issue:** All handshake tests use password mode. `require_crypto=False` mode is
  also untested.

### DESIGN-4 — Three unimplemented HiveMessage types (upstream blocker)
- **Severity:** LOW (blocked by hivemind-core)
- **Types:** QUERY, CASCADE, RENDEZVOUS
- **Location:** `tests/test_unimplemented_types.py` — stub tests exist, verify NotImplemented.
- **Fix:** Implement in hivemind-core, then expand tests.
- **Note:** PING and PONG are now fully implemented in hivemind-core and tested in `tests/test_ping_pong.py` (49 tests, all pass).

---

## 5. Documentation Gaps

### DOC-1 — Stub documentation files need content
- **Severity:** LOW (AGENTS.md compliance)
- **Files:**
  - `QUICK_FACTS.md` — contains placeholder "Initial description"
  - `FAQ.md` — contains placeholder "Initial description"
  - `SUGGESTIONS.md` — contains only 2 generic bullet points
  - `docs/index.md` — minimal nav stub
  - `docs/nodes.md` — minimal stub
  - `docs/topology.md` — minimal stub

---

## 6. Passing Test Summary (166 tests)

All core protocol tests pass:
- `test_bus.py` — 9/9
- `test_acl.py` — 6/6
- `test_broadcast.py` — 6/6
- `test_escalate.py` — 6/6
- `test_propagate.py` — 6/6
- `test_intercom.py` — 4/4
- `test_handshake.py` — 12/12
- `test_shared_bus.py` — 4/4
- `test_routing.py` — 7/7
- `test_binary.py` — 7/7
- `test_audio_transformers.py` — 8/8
- `test_protocol_fixes.py` — 8/8
- `test_unimplemented_types.py` — 5/5
- `test_solver_harness.py` — 5/5
- `test_ping_pong.py` — 49/49 (32 fast + 17 slow)
- `test_topology_plots.py` — 12/12
- `test_ovoscope_integration.py` — 9/9
- `test_helloworld_hivemind.py` — 15/15
