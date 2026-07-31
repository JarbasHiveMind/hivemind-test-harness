# Protocol Coverage

Test coverage status for all HiveMind protocol message types, binary payload
types, and related features.

**403 tests across 46 modules** collect in a plain checkout. Four embedded
modules (68 more tests) skip themselves unless the MicroPython client checkout
is present — point `HIVEMIND_MICROPYTHON_CLIENT` at it, or CI's
`micropython-e2e` job supplies it. Regenerate the counts below with::

    python -m pytest tests --collect-only -q

---

## Message Types (`HiveMessageType`)

| Type | Value | Direction | Description | Status | Test file(s) |
|---|---|---|---|---|---|
| HANDSHAKE | `shake` | both | Password/RSA key exchange (v0-v2), Noise XXpsk2 (v3) | tested | `test_handshake.py`, `test_protocol_v3_noise.py` |
| HELLO | `hello` | both | Session sync after handshake | tested | `test_handshake.py` |
| BUS | `bus` | both | OVOS bus message injection | tested | `test_bus.py`, `test_route_metadata.py`, the `test_e2e_*` suite |
| SHARED_BUS | `shared_bus` | satellite→master | Passive bus monitoring | tested | `test_shared_bus.py` |
| BROADCAST | `broadcast` | admin-satellite→master→all | Fan-out to all connected nodes | tested | `test_broadcast.py` |
| PROPAGATE | `propagate` | satellite→master→siblings+upstream | Fan-out + escalate combined | tested | `test_propagate.py` |
| ESCALATE | `escalate` | satellite→masters-only | Forward up authority chain | tested | `test_escalate.py` |
| INTERCOM | `intercom` | satellite→master→target-satellite | RSA-encrypted peer-to-peer routing | tested | `test_intercom.py` |
| PING | `ping` | both | Network topology discovery | tested | `test_ping_pong.py` (49 tests), `test_ping_exactly_once.py` (11 tests) |
| QUERY | `query` | upstream+response | Like ESCALATE but stops at first responder | tested | `test_query.py` (6 tests) |
| CASCADE | `cascade` | both+response | Like PROPAGATE but expects responses from all | tested | `test_cascade.py` (8 tests) |
| RENDEZVOUS | `rendezvous` | reserved | Rendezvous-node peer discovery | not implemented | `test_unimplemented_types.py` |
| THIRDPRTY | `3rdparty` | both | User-land free-form message | tested | `test_propagate.py`, `test_unimplemented_types.py` |
| BINARY | `bin` | satellite→master | Binary data container (7 subtypes) | tested | `test_binary.py`, `test_e2e_binary_skill.py`. **Gap:** the binarize (bitstring) encoding is not covered — see [FAQ](../FAQ.md) |

### Not-yet-implemented types (RENDEZVOUS)

RENDEZVOUS is defined in `HiveMessageType` but `HiveMindListenerProtocol.handle_message`
routes it to `handle_unknown_message()` (an empty stub). The stub test in
`test_unimplemented_types.py` verifies no crash occurs and the master records the inbound message.

QUERY and CASCADE are now fully implemented with dedicated handlers and test suites.

---

## Binary Payload Types (`HiveMindBinaryPayloadType`): all 7 covered

| Type | Value | Direction | Test |
|---|---|---|---|
| UNDEFINED | 0 | satellite→master | `test_binary.py::TestUndefinedBinary` |
| RAW_AUDIO | 1 | satellite→master | `test_binary.py::TestRawAudio` |
| NUMPY_IMAGE | 2 | satellite→master | `test_binary.py::TestNumpyImage` |
| FILE | 3 | satellite→master | `test_binary.py::TestFileTransfer` |
| STT_AUDIO_TRANSCRIBE | 4 | satellite→master | `test_binary.py::TestSttTranscribe` |
| STT_AUDIO_HANDLE | 5 | satellite→master | `test_binary.py::TestSttHandle` |
| TTS_AUDIO | 6 | master→satellite | `test_binary.py::TestReceiveTts` |

---

## Handshake Variants

| Variant | Status | Notes |
|---|---|---|
| Password-based PAKE | tested | Default mode. All tests use this |
| RSA pubkey exchange | partial | `connect()` falls back to `start_handshake()` for no-password mode. Not exercised by a dedicated test |
| Pre-shared key | not tested | Requires `crypto_key` set in identity. No dedicated test |
| No crypto | not tested | Requires `require_crypto=False`. No dedicated test |

---

## Routing & Access Control

| Feature | Status | Test |
|---|---|---|
| `can_escalate=False` | tested | `test_escalate.py::TestEscalateRespectsCantEscalate` |
| `can_propagate=False` | tested | `test_propagate.py::TestPropagateCannotPropagate` |
| `is_admin` broadcast check | tested | `test_broadcast.py::TestBroadcastFromNonAdmin` |
| `msg_blacklist` (outbound) | removed upstream | hivemind-core is whitelist-only; `Client.message_blacklist` no longer exists. `register_satellite(msg_blacklist=...)` is accepted and ignored. The old `test_acl.py::TestMessageBlacklist` asserted removed behaviour and was deleted (see the comment at `tests/test_acl.py:10`). |
| `skill_blacklist` (downstream delivery) | tested | `test_acl.py::TestSkillBlacklist` (session injection) + `test_e2e_acl_skills.py` (live OVOS enforcement) |
| `intent_blacklist` (downstream delivery) | tested | `test_acl.py::TestIntentBlacklist` (session injection) + `test_e2e_acl_skills.py` (live OVOS enforcement) |
| `allowed_types` (admission whitelist) | tested | `test_bus.py::TestAllowedTypes`, `test_acl.py::TestAllowedTypes` — including the `hive.policy.denied` response with deny code `acl_disallowed_type` |
| `target_site_id` (BROADCAST) | tested | `test_broadcast.py::TestBroadcastTargetSiteId` |
| `target_pubkey` (INTERCOM) | tested | `test_intercom.py::TestIntercomNoEncryption` |

---

## Relay Chain Routing

| Scenario | Status | Test |
|---|---|---|
| ESCALATE climbs 1 relay | tested | `test_escalate.py::TestEscalateChain` |
| ESCALATE climbs 2 relays | tested | `test_routing.py::TestDeepChainEscalate` |
| PROPAGATE crosses 1 relay | tested | `test_propagate.py::TestPropagateChain` |
| PROPAGATE crosses 2 relays | tested | `test_routing.py::TestDeepChainPropagate` |
| BROADCAST downstream through relay | not wired | Slave protocol's `handle_broadcast` does not forward to downstream relay masters. No test |
| QUERY through relay (no answer → escalate) | tested | `test_query.py::TestQueryEscalateOnTimeout::test_query_escalates_through_relay` |
| CASCADE through relay | tested | `test_cascade.py::TestCascadeRelayForwarding::test_cascade_reaches_top_master` |
| CASCADE select callback | tested | `test_cascade.py::TestCascadeSelectCallback` (2 tests) |
| BUS upstream through relay | implicit | ESCALATE/PROPAGATE cover cross-boundary routing |

---

## Session Management

| Behaviour | Status | Test |
|---|---|---|
| Each satellite gets unique `session_id` | tested | `test_handshake.py::TestMultipleSatellites` |
| Non-admin cannot use `session_id="default"` | tested | `test_handshake.py::TestAdminDefaultSession` |
| Admin can use any session | tested | `test_handshake.py::TestAdminDefaultSession` |
| Session context propagated through BUS | tested | `test_bus.py::TestSatelliteInjectsBus` |
| Session IDs don't bleed across satellites | tested | `test_bus.py::TestMultipleSatellitesBus` |

---

## All Test Modules

Counts from `pytest tests --collect-only -q`.

### In-process protocol layer (CI job: `harness-tests`)

| File | Tests | Purpose |
|---|---|---|
| `test_acl.py` | 5 | Skill/intent blacklist injection, allowed_types admission + deny code |
| `test_all_topologies.py` | 27 | Cross-topology protocol validation |
| `test_audio_transformers.py` | 8 | Audio pipeline transformer integration |
| `test_binary.py` | 7 | All 7 binary payload types |
| `test_broadcast.py` | 6 | Admin/non-admin broadcast, target_site_id |
| `test_bus.py` | 9 | BUS inject, reply, allowed_types, multi-satellite |
| `test_cascade.py` | 8 | CASCADE responses, star forwarding, relay, ACL, disambiguation |
| `test_escalate.py` | 6 | Upstream-only routing, can_escalate ACL |
| `test_handshake.py` | 12 | Handshake, session setup, multi-satellite |
| `test_hivemind_bus_client_e2e.py` | 5 | Real hivemind-bus-client over a loopback websocket |
| `test_intercom.py` | 4 | Peer-to-peer RSA-encrypted routing |
| `test_ping_exactly_once.py` | 11 | PING delivered exactly once per node across relays |
| `test_ping_pong.py` | 49 | PING network topology discovery |
| `test_propagate.py` | 6 | Fan-out + escalate, can_propagate ACL |
| `test_protocol_fixes.py` | 8 | Regression tests for bugs fixed in core/client |
| `test_protocol_rules.py` | 14 | Protocol invariant validation |
| `test_query.py` | 6 | QUERY local answer, escalate on timeout, relay chain, ACL |
| `test_route_metadata.py` | 8 | Route/hop metadata carried on forwarded messages |
| `test_routing.py` | 7 | Deep chain escalate/propagate, hop tracking |
| `test_shared_bus.py` | 4 | Passive bus monitoring (share_bus flag) |
| `test_unimplemented_types.py` | 5 | RENDEZVOUS / unknown-type stub safety |
| `test_voice_pe_protocol.py` | 18 | Voice PE protocol message handling |

### Live OVOS skill execution (CI job: `ovos-e2e`)

| File | Tests | Purpose |
|---|---|---|
| `test_e2e_acl_skills.py` | 8 | Downstream skill/intent blacklist ENFORCEMENT by OVOS |
| `test_e2e_admin_broadcast.py` | 4 | Admin broadcast reaching live skills |
| `test_e2e_ask_yesno_selection.py` | 10 | ask_yesno / ask_selection dialogs over HiveMind |
| `test_e2e_binary_skill.py` | 5 | Binary payload delivered to a skill |
| `test_e2e_converse.py` | 5 | Converse loop over HiveMind |
| `test_e2e_converse_advanced.py` | 7 | Cancel, timeout and concurrent get_response |
| `test_e2e_get_response.py` | 7 | get_response round-trips through a satellite |
| `test_e2e_lang.py` | 5 | Per-session language routing |
| `test_e2e_misc_skills.py` | 8 | IP/count skills and utterance edge cases |
| `test_e2e_multi_satellite.py` | 3 | Two satellites, independent sessions |
| `test_e2e_ocp.py` | 5 | OCP media pipeline over HiveMind |
| `test_e2e_relay_acl.py` | 5 | Blacklists compounding through a relay chain |
| `test_e2e_relay_skills.py` | 5 | Skill execution through 1 and 2 relays |
| `test_e2e_scheduler.py` | 4 | schedule_event callbacks reaching the satellite |
| `test_e2e_session.py` | 4 | Session identity end-to-end |
| `test_e2e_shared_bus.py` | 4 | shared_bus monitoring with a live agent |
| `test_e2e_skills.py` | 11 | Date-time / personal / naptime / fallback / spelling skills |
| `test_e2e_stop.py` | 7 | Stop handling over HiveMind |
| `test_e2e_volume_phal.py` | 13 | Volume skill against a mock PHAL on the satellite |
| `test_helloworld_hivemind.py` | 15 | Full utterance→skill→speak round-trip |
| `test_ovoscope_integration.py` | 9 | OvoscopeAgentProtocol with a real IntentService |
| `test_protocol_v3_noise.py` | 7 | Protocol v3 Noise XXpsk2 against a REAL hivemind-core subprocess |
| `test_solver_harness.py` | 5 | HiveMindSolver plugin harness |
| `test_topology_plots.py` | 12 | Topology + discovery plot rendering |

### Other runtimes

| File | Tests | CI job | Purpose |
|---|---|---|---|
| `test_js_e2e.py` | 3 | `js-e2e` | JavaScript client protocol compatibility (skips without `node`) |
| `test_embedded_interop.py` | 37 | `embedded-tests` | Embedded ↔ standard client crypto interoperability |
| `test_embedded_comprehensive.py` | 16 | `embedded-tests` | MicroPython crypto + binary protocol |
| `test_embedded_clients.py` | 11 | `micropython-e2e` | Embedded client protocol against an in-process hub |
| `test_micropython_e2e.py` | 4 | `micropython-e2e` | MicroPython client over a loopback websocket |

The four embedded modules call `pytest.importorskip` on the MicroPython client
package, so they skip cleanly when that checkout is absent.

---

## Bugs Fixed in External Repos (with regression tests)

All bugs were fixed directly in the source repos, not monkey-patched in the test harness.

| Bug | Repo | File | Regression test |
|---|---|---|---|
| `bool(s.read(1))` always True (should be `.bool`) | hivemind-websocket-client | `serialization.py:103` | `test/test_serialization.py::TestDecodeBitstringRegressions` |
| `HiveMessage.__init__` missing nested-HiveMessage normalization | hivemind-websocket-client | `message.py` | `test/test_message.py::TestHiveMessageInitNormalization` |
| `message.serialize()` returns str but MycroftMessage requires dict | hivemind-websocket-client | `protocol.py:248,271` | `test/test_message.py` |
| `clients = {}` class-level dict shared across all instances | hivemind-core | `protocol.py:218` | `test/unittests/test_protocol_regressions.py::TestClientsInstanceDict` |
| `payload` (HiveMessage) passed as Message `data=` (requires dict) | hivemind-core | `protocol.py:725,679` | `test/unittests/test_protocol_regressions.py` |
| `message.payload.serialize()` fails for dict payload | hivemind-core | `protocol.py:150` | `test/unittests/test_protocol_regressions.py` |
| `handle_broadcast_message` forwarded inner payload instead of BROADCAST wrapper | hivemind-core | `protocol.py` | `test/unittests/test_protocol_regressions.py::TestHandleBroadcastForwardsBroadcastWrapper` |
| `handle_propagate_message` forwarded inner payload instead of PROPAGATE wrapper | hivemind-core | `protocol.py` | `test/unittests/test_protocol_regressions.py::TestHandlePropagateForwardsPropagateWrapper` |
| `handle_broadcast`/`handle_propagate` passed outer wrapper to `handle_intercom` (recursion) | hivemind-websocket-client | `protocol.py:237,256` | `test/test_protocol_regressions.py::TestHandleBroadcastIntercomRecursion` |

---
[← Architecture: HiveMind Test Harness](01-architecture.md) · [Home](index.md) · [Network Topologies →](03-topologies.md)
