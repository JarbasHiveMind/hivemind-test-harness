# Protocol Coverage

Test coverage status for all HiveMind protocol message types, binary payload types,
and related features. **326 tests across 32 files, all passing.**

---

## Message Types (`HiveMessageType`)

| Type | Value | Direction | Description | Status | Test file(s) |
|---|---|---|---|---|---|
| HANDSHAKE | `shake` | both | PAKE key exchange | ✅ tested | `test_handshake.py`, `test_handshake_edge_cases.py` |
| HELLO | `hello` | both | Session sync after handshake | ✅ tested | `test_handshake.py` |
| BUS | `bus` | both | OVOS bus message injection | ✅ tested | `test_bus.py`, `test_utterance_flow.py` |
| SHARED_BUS | `shared_bus` | satellite→master | Passive bus monitoring | ✅ tested | `test_shared_bus.py` |
| BROADCAST | `broadcast` | admin-satellite→master→all | Fan-out to all connected nodes | ✅ tested | `test_broadcast.py` |
| PROPAGATE | `propagate` | satellite→master→siblings+upstream | Fan-out + escalate combined | ✅ tested | `test_propagate.py` |
| ESCALATE | `escalate` | satellite→masters-only | Forward up authority chain | ✅ tested | `test_escalate.py` |
| INTERCOM | `intercom` | satellite→master→target-satellite | RSA-encrypted peer-to-peer routing | ✅ tested | `test_intercom.py` |
| PING | `ping` | both | Network topology discovery | ✅ tested | `test_ping_pong.py` (49 tests) |
| QUERY | `query` | upstream | Like ESCALATE but stops at first responder | ⚠️ not implemented | `test_unimplemented_types.py` |
| CASCADE | `cascade` | both | Like PROPAGATE but expects responses | ⚠️ not implemented | `test_unimplemented_types.py` |
| RENDEZVOUS | `rendezvous` | reserved | Rendezvous-node peer discovery | ⚠️ not implemented | `test_unimplemented_types.py` |
| THIRDPRTY | `3rdparty` | both | User-land free-form message | ✅ tested | `test_propagate.py`, `test_unimplemented_types.py` |
| BINARY | `bin` | satellite→master | Binary data container (7 subtypes) | ✅ tested | `test_binary.py`, `test_binary_flow.py`, `test_binarize_e2e.py` |

### Not-yet-implemented types (QUERY / CASCADE / RENDEZVOUS)

These three types are defined in `HiveMessageType` but `HiveMindListenerProtocol.handle_message`
routes them to `handle_unknown_message()` (an empty stub). The stub tests in
`test_unimplemented_types.py` verify no crash occurs and the master records the inbound message.

---

## Binary Payload Types (`HiveMindBinaryPayloadType`) — all 7 covered

| Type | Value | Direction | Test |
|---|---|---|---|
| UNDEFINED | 0 | satellite→master | ✅ `test_binary.py::TestUndefinedBinary` |
| RAW_AUDIO | 1 | satellite→master | ✅ `test_binary.py::TestRawAudio` |
| NUMPY_IMAGE | 2 | satellite→master | ✅ `test_binary.py::TestNumpyImage` |
| FILE | 3 | satellite→master | ✅ `test_binary.py::TestFileTransfer` |
| STT_AUDIO_TRANSCRIBE | 4 | satellite→master | ✅ `test_binary.py::TestSttTranscribe` |
| STT_AUDIO_HANDLE | 5 | satellite→master | ✅ `test_binary.py::TestSttHandle` |
| TTS_AUDIO | 6 | master→satellite | ✅ `test_binary.py::TestReceiveTts` |

---

## Handshake Variants

| Variant | Status | Notes |
|---|---|---|
| Password-based PAKE | ✅ tested | Default mode; all tests use this |
| RSA pubkey exchange | partial | `connect()` falls back to `start_handshake()` for no-password mode; not exercised by dedicated test |
| Pre-shared key | not tested | Requires `crypto_key` set in identity; no dedicated test |
| No crypto | not tested | Requires `require_crypto=False`; no dedicated test |

---

## Routing & Access Control

| Feature | Status | Test |
|---|---|---|
| `can_escalate=False` | ✅ tested | `test_escalate.py::TestEscalateRespectsCantEscalate` |
| `can_propagate=False` | ✅ tested | `test_propagate.py::TestPropagateCannotPropagate` |
| `is_admin` broadcast check | ✅ tested | `test_broadcast.py::TestBroadcastFromNonAdmin` |
| `msg_blacklist` | ✅ tested | `test_acl.py::TestMessageBlacklist` |
| `skill_blacklist` | ✅ tested | `test_acl.py::TestSkillBlacklist` |
| `intent_blacklist` | ✅ tested | `test_acl.py::TestIntentBlacklist` |
| `allowed_types` | ✅ tested | `test_bus.py::TestAllowedTypes`, `test_acl.py::TestAllowedTypes` |
| `target_site_id` (BROADCAST) | ✅ tested | `test_broadcast.py::TestBroadcastTargetSiteId` |
| `target_pubkey` (INTERCOM) | ✅ tested | `test_intercom.py::TestIntercomNoEncryption` |

---

## Relay Chain Routing

| Scenario | Status | Test |
|---|---|---|
| ESCALATE climbs 1 relay | ✅ tested | `test_escalate.py::TestEscalateChain` |
| ESCALATE climbs 2 relays | ✅ tested | `test_routing.py::TestDeepChainEscalate` |
| PROPAGATE crosses 1 relay | ✅ tested | `test_propagate.py::TestPropagateChain` |
| PROPAGATE crosses 2 relays | ✅ tested | `test_routing.py::TestDeepChainPropagate` |
| BROADCAST downstream through relay | ⚠️ not wired | Slave protocol's `handle_broadcast` does not forward to downstream relay masters; no test |
| BUS upstream through relay | ✅ implicit | ESCALATE/PROPAGATE cover cross-boundary routing |

---

## Session Management

| Behaviour | Status | Test |
|---|---|---|
| Each satellite gets unique `session_id` | ✅ tested | `test_handshake.py::TestMultipleSatellites` |
| Non-admin cannot use `session_id="default"` | ✅ tested | `test_handshake.py::TestAdminDefaultSession` |
| Admin can use any session | ✅ tested | `test_handshake.py::TestAdminDefaultSession` |
| Session context propagated through BUS | ✅ tested | `test_bus.py::TestSatelliteInjectsBus` |
| Session IDs don't bleed across satellites | ✅ tested | `test_bus.py::TestMultipleSatellitesBus` |

---

## All Test Files (32 files, 326 tests)

| File | Tests | Purpose |
|---|---|---|
| `test_acl.py` | 6 | Message/skill/intent blacklists, allowed_types |
| `test_all_topologies.py` | 27 | Cross-topology protocol validation |
| `test_audio_transformers.py` | 8 | Audio pipeline transformer integration |
| `test_binarize_e2e.py` | 7 | Binary bitstring encoding roundtrip (Protocol V2) |
| `test_binary_flow.py` | 9 | Binary data flow through relay chains |
| `test_binary.py` | 7 | All 7 binary payload types |
| `test_broadcast.py` | 6 | Admin/non-admin broadcast, target_site_id |
| `test_bus.py` | 9 | BUS inject, reply, allowed_types, multi-satellite |
| `test_embedded_clients.py` | 11 | Embedded (ESP32/MicroPython) client protocol |
| `test_embedded_comprehensive.py` | 16 | Comprehensive embedded client scenarios |
| `test_embedded_interop.py` | 10 | Embedded ↔ standard client interoperability |
| `test_escalate.py` | 6 | Upstream-only routing, can_escalate ACL |
| `test_handshake_edge_cases.py` | 5 | Handshake failure modes and edge cases |
| `test_handshake.py` | 12 | PAKE handshake, session setup, multi-satellite |
| `test_helloworld_hivemind.py` | 15 | Full utterance→skill→speak roundtrip via ovoscope |
| `test_hivemind_bus_client_e2e.py` | 5 | hivemind-bus-client integration |
| `test_intercom.py` | 4 | Peer-to-peer RSA-encrypted routing |
| `test_js_e2e.py` | 3 | JavaScript client protocol compatibility |
| `test_micropython_e2e.py` | 4 | MicroPython client protocol compatibility |
| `test_ovoscope_integration.py` | 9 | OvoscopeAgentProtocol with real IntentService |
| `test_ping_pong.py` | 49 | PING network topology discovery |
| `test_propagate.py` | 6 | Fan-out + escalate, can_propagate ACL |
| `test_protocol_fixes.py` | 8 | Regression tests for bugs fixed in core/client |
| `test_protocol_rules.py` | 14 | Protocol invariant validation |
| `test_routing.py` | 7 | Deep chain escalate/propagate, hop tracking |
| `test_shared_bus.py` | 4 | Passive bus monitoring (share_bus flag) |
| `test_solver_harness.py` | 5 | Solver plugin harness integration |
| `test_topology_plots.py` | 12 | Topology visualization/validation |
| `test_unimplemented_types.py` | 5 | QUERY/CASCADE/RENDEZVOUS stub safety |
| `test_utterance_flow.py` | 6 | End-to-end utterance processing flow |
| `test_voice_pe_protocol.py` | 18 | Voice PE protocol message handling |
| `test_voice_pe_satellite.py` | 13 | Voice PE satellite integration |

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
