
# HiveMind Test Harness — Suggestions

---

## SG-1 — Fix OvoScope tests: pass lang/config to MiniCroft (HIGH PRIORITY)

**Problem:**
`OvoscopeAgentProtocol` creates MiniCroft using the host OVOS configuration, which on this
system has `lang: pt-PT`. The hello-world skill only loads vocabulary for the configured
language, so Adapt cannot match English utterances. Additionally, `ovos-persona-pipeline-plugin`
(Gemma) intercepts all unmatched utterances, preventing `complete_intent_failure` from firing.
This causes 12 test failures in `test_helloworld_hivemind.py` and `test_ovoscope_integration.py`.

**Evidence:** `AUDIT.md:ENV-1`; root cause confirmed by querying `Configuration().get('lang')` →
`'pt-PT'` and inspecting `register_vocab` messages (only pt-PT vocab registered for HelloWorld).

**Proposed solution:**
Add a `lang` (and optionally `config`) parameter to `OvoscopeAgentProtocol.__post_init__()`.
Before calling `get_minicroft()`, temporarily patch `ovos_config.config.Configuration` to
inject the test language. Use `unittest.mock.patch.dict` or `ovos_config.config.update_mycroft_config`.

```python
# In OvoscopeAgentProtocol.__post_init__:
if lang is not None:
    from unittest.mock import patch
    with patch.object(Configuration, '__getitem__', ...):
        self.minicroft = get_minicroft(self.skill_ids, ...)
```

Alternatively, have the test fixture pass an explicit `config` dict to MiniCroft (if/when
MiniCroft adds that parameter upstream in ovoscope).

**Estimated impact:** Fixes 12 failing tests; makes OvoScope tests environment-independent.

---

## SG-2 — Wire BROADCAST forwarding through relay chains (MEDIUM PRIORITY)

**Problem:**
In a relay topology (M0 → R1_master ← R1_sat → S0), when M0 sends BROADCAST, it reaches
R1_master's direct satellites but does NOT reach S0 (downstream of the relay). The
`TopologyBuilder.add_relay()` method wraps ESCALATE/PROPAGATE but not BROADCAST downstream.

**Evidence:** `AUDIT.md:DESIGN-1`; `PROTOCOL_AUDIT.md:DESIGN-3`; no test exercises this path.

**Proposed solution:**
In `hivemind_test_harness/topology.py:add_relay()`, after wiring the relay's upstream
connection, also register a handler on `R1_master`'s internal protocol for
`HiveMessageType.BROADCAST` that forwards to its satellites:

```python
# After creating relay_master:
original_handle = relay_master.protocol.handle_broadcast
def forwarding_handle_broadcast(msg, client):
    original_handle(msg, client)
    for peer_client in relay_master.protocol.clients.values():
        relay_master.send_to_satellite(peer_client.key, msg)
relay_master.protocol.handle_broadcast = forwarding_handle_broadcast
```

**Estimated impact:** Closes protocol coverage gap for relay topologies; enables tests that
validate end-to-end BROADCAST in mesh networks.

---

## SG-3 — Add handshake mode tests (LOW PRIORITY)

**Problem:**
Only password-based handshake is tested. Three variants lack explicit coverage:
1. RSA-only (no password) mode — `require_crypto=True`, no password
2. No-crypto mode — `require_crypto=False`
3. Pre-shared key (PSK) — `crypto_key` path

**Evidence:** `AUDIT.md:DESIGN-2`, `AUDIT.md:DESIGN-3`

**Proposed solution:**
Parametrize `tests/test_handshake.py` with `pytest.mark.parametrize`:

```python
@pytest.mark.parametrize("use_password,require_crypto", [
    (True, True),    # current default
    (False, True),   # RSA-only
    (False, False),  # no crypto
])
def test_handshake_variants(use_password, require_crypto):
    ...
```

Add a PSK test case using `register_satellite(..., crypto_key=b"test_key_32bytes_padded000000000")`.

**Estimated impact:** Low — these are edge cases not exercised in production, but having coverage
prevents silent regressions if the handshake logic changes.

---

## SG-4 — Fix namespace package re-emergence with a workspace-level install script

**Problem:**
Three packages (`z85base91`, `poorman_handshake`, `json_database`) have a recurring namespace
package conflict when installed as editable (`-e`). They must be reinstalled as non-editable
wheels after `uv sync` or any full environment rebuild. This is undocumented and easy to miss.

**Evidence:** `AUDIT.md:DEP-1`, `DEP-2`, `DEP-3`; same issue documented in workspace MEMORY.md.

**Proposed solution:**
1. Add a workspace-level `setup-dev-env.sh` script that runs `uv sync` then reinstalls the
   three packages as wheels:
   ```bash
   uv pip install --python .venv/bin/python z85base91 poorman_handshake json_database
   ```
2. Document this in `AGENTS.md` under Development Environment section.
3. Long-term: fix each repo's `pyproject.toml` so that editable install does not create a
   namespace package conflict (typically by ensuring `src/` layout or adding `__init__.py`
   to the repo root to prevent namespace package discovery).

**Estimated impact:** Eliminates the "tests suddenly break with ImportError" recurring issue
that affects all repos using these three packages.

---

## SG-5 — Add `OvoscopeAgentProtocol.lang` parameter to public API

**Problem:**
There is no way to specify the OVOS language for a MiniCroft-backed test without patching
the global config. This couples OvoScope tests to the host system's language configuration.

**Evidence:** Confirmed during audit — `Configuration().get('lang')` returns `'pt-PT'` on this
system; all en-US skill vocab silently not loaded.

**Proposed solution:**
Add `lang: str = "en-US"` field to `OvoscopeAgentProtocol` (dataclass). In `__post_init__`,
before calling `get_minicroft`, patch the Configuration at the right level to inject the test
language. This is a minor API addition with no breaking changes (default `"en-US"` matches
the existing test expectations).

**Estimated impact:** Directly fixes 9 test_helloworld_hivemind failures; makes harness
portable to non-English OVOS installs.

---

## SG-6 — Implement QUERY, CASCADE, RENDEZVOUS message types in hivemind-core — PING DONE

**Problem:**
Three `HiveMessageType` values are defined in the protocol spec but not implemented in
`hivemind-core`. Stub tests in `test_unimplemented_types.py` verify they raise
`NotImplementedError`. Until these are implemented, they represent protocol gaps.

**Evidence:** `AUDIT.md:DESIGN-4`; `PROTOCOL_AUDIT.md:DESIGN-1` through `DESIGN-4`.

**Status:** PING and PONG are fully implemented in `hivemind_core/protocol.py` (via
`handle_ping_message` / `handle_pong_message` / `HiveMapper`) and tested in
`tests/test_ping_pong.py` (49 tests, all pass). Three types remain:

**Proposed solution:**
Implement each in `hivemind_core/protocol.py`:
- **QUERY**: Directed query to a specific peer; route reply back to origin.
- **CASCADE**: Fanout broadcast with hop counter; stop when counter reaches 0.
- **RENDEZVOUS**: Peer discovery; respond with known peer list.

After implementation, expand `test_unimplemented_types.py` to behavioral tests.

**Estimated impact:** Closes 3 remaining protocol design gaps; enables advanced mesh use cases.

---

## SG-8 — Implement reverse routing: OVOS bus → satellite — **IMPLEMENTED 2026-03-09**

**Problem:**
`test_speak_received_on_satellite_bus` fails because the harness has no mechanism to route
messages emitted by skills on MiniCroft's FakeBus back to the originating satellite. In
production, the satellite gets all bus messages via its WebSocket subscription. In the harness,
nothing bridges MiniCroft's FakeBus → `SatelliteNode.internal_bus`.

**Evidence:** `AUDIT.md:ENV-3`; `hivemind_core.protocol.py:232` — `agent_bus_callback = None`

**Proposed solution:**
Override `TestAgentProtocol.inject()` (or add a new method) to register a one-shot listener
on `agent.bus` for the expected response messages, then route them back via the client's
`send_msg`:

```python
def inject(self, message: Message, client: HiveMindClientConnection):
    super().inject(message, client)
    # Subscribe to outgoing messages and route them back to the satellite
    def _on_bus_response(resp: Message):
        payload = HiveMessage(HiveMessageType.BUS, resp)
        client.send_msg(payload)
    for msg_type in client.allowed_types or OUTGOING_TYPES:
        self.bus.on(msg_type, _on_bus_response)
```

Where `OUTGOING_TYPES` = `["speak", "speak:b64_audio", "recognizer_loop:b64_transcribe", ...]`.

This connects MiniCroft's FakeBus output to the satellite's `_receive_raw()`, which will
deserialize and emit it on `SatelliteNode.internal_bus`.

**Implemented:** `register_bus_handlers()`, `handle_send()`, and `handle_internal_mycroft()`
ported verbatim from `OVOSProtocol` into `TestAgentProtocol`. `test_speak_received_on_satellite_bus`
now passes. All 115 tests pass.

---

## SG-7 — Address sklearn version mismatch for Padatious (LOW PRIORITY)

**Problem:**
Padatious intent models were pre-trained with sklearn 1.6.1 but the workspace uses 1.8.0.
This generates `InconsistentVersionWarning` on every MiniCroft startup and may produce
incorrect intent matching results in edge cases.

**Evidence:** `AUDIT.md:QA-3`; warnings visible in all OvoScope test runs.

**Proposed solution:**
Either:
1. Retrain Padatious models: delete `~/.local/share/mycroft/intent_cache` and restart —
   models will retrain with current sklearn version.
2. Or pin sklearn to 1.6.1 in the workspace dev dependencies.

**Estimated impact:** Eliminates warnings; ensures Padatious matching accuracy.
