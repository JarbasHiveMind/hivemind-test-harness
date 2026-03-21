# Test Scenarios

Each scenario is a named test case with:
- **Topology** (from `03-topologies.md`)
- **Setup** – how nodes are configured
- **Action** – what message is sent and by whom
- **Expected** – what each node should observe

---

## Group 1: Connection & Handshake

### TS-CONN-01 · RSA Handshake
- **Topology**: T1
- **Setup**: No password, handshake enabled, `require_crypto=True`
- **Action**: S0 connects
- **Expected**:
  - M0 sends HELLO (pubkey, peer, node_id)
  - M0 sends HANDSHAKE (handshake=True, preshared_key=False)
  - S0 replies HANDSHAKE with its pubkey
  - M0 replies HANDSHAKE with RSA envelope
  - S0 replies HELLO with session + site_id
  - `M0.clients[S0.peer]` exists; `crypto_key` is non-null

### TS-CONN-02 · Password PAKE Handshake
- **Topology**: T1
- **Setup**: Password set on both ends; `handshake_enabled=True`
- **Action**: S0 connects
- **Expected**:
  - HANDSHAKE payload contains `password: True`
  - S0 sends envelope from `PasswordHandShake.generate_handshake()`
  - M0 validates, derives shared secret
  - All subsequent messages encrypted with derived key

### TS-CONN-03 · Pre-shared Key (V0)
- **Topology**: T1
- **Setup**: `crypto_key` pre-set on both master db entry and satellite identity
- **Action**: S0 connects
- **Expected**:
  - HANDSHAKE payload has `preshared_key: True`
  - No RSA exchange required
  - Messages encrypted immediately after HELLO

### TS-CONN-04 · Invalid API Key
- **Topology**: T1
- **Setup**: S0 connects with a key not in M0's database
- **Action**: S0 connects
- **Expected**:
  - `M0.callbacks.on_invalid_key` fires
  - `hive.client.connection.error` emitted on M0's FakeBus
  - S0 socket is closed

### TS-CONN-05 · No Crypto Allowed (require_crypto=False)
- **Topology**: T1
- **Setup**: `require_crypto=False`, S0 has no crypto_key
- **Action**: S0 connects, sends BUS message
- **Expected**: M0 accepts and processes message unencrypted

### TS-CONN-06 · Reconnect After Disconnect
- **Topology**: T1
- **Action**: S0 connects → S0 disconnects → S0 reconnects
- **Expected**: Fresh handshake on reconnect; new session or same session_id?

### TS-CONN-07 · Client Requests Default Session (Non-Admin)
- **Topology**: T1
- **Setup**: S0 `is_admin=False`
- **Action**: S0 sends HELLO with `session_id="default"`
- **Expected**: M0 disconnects S0

### TS-CONN-08 · Admin Client Uses Default Session
- **Topology**: T1
- **Setup**: S0 `is_admin=True`
- **Action**: S0 sends HELLO with `session_id="default"`
- **Expected**: S0 remains connected and registered

---

## Group 2: BUS Messages

### TS-BUS-01 · Satellite Injects BUS Message
- **Topology**: T1
- **Action**: S0 emits `Message("recognizer_loop:utterance", {"utterances": ["hello"]})`
- **Expected**:
  - M0's FakeBus receives `recognizer_loop:utterance`
  - Message context has `peer=S0.peer`, `source=S0.peer`
  - Session context includes S0's session_id and site_id

### TS-BUS-02 · Master Replies to Satellite via BUS
- **Topology**: T1
- **Action**: M0's FakeBus emits a Message destined for S0 (`destination=S0.peer`)
- **Expected**: S0's internal FakeBus receives the message

### TS-BUS-03 · BUS Message Respects `allowed_types`
- **Topology**: T1
- **Setup**: S0's `allowed_types = ["recognizer_loop:utterance"]` only
- **Action**: S0 sends `Message("speak", {"utterance": "hello"})`
- **Expected**: M0 drops it (not authorized), does NOT emit to FakeBus

### TS-BUS-04 · BUS Message with Default Session (Non-Admin)
- **Topology**: T1
- **Setup**: S0 `is_admin=False`
- **Action**: S0 sends BUS with `session_id="default"` in context
- **Expected**: M0 disconnects S0

---

## Group 3: SHARED_BUS

### TS-SBUS-01 · Satellite Shares Bus Passively
- **Topology**: T1
- **Setup**: S0 created with `share_bus=True`
- **Action**: S0's internal FakeBus emits any message
- **Expected**: M0's `shared_bus_callback` is invoked with that message

### TS-SBUS-02 · No Sharing When share_bus=False
- **Topology**: T1
- **Setup**: S0 `share_bus=False`
- **Action**: S0's FakeBus emits messages
- **Expected**: M0 never receives SHARED_BUS

---

## Group 4: BROADCAST

### TS-BC-01 · Admin Broadcasts to All Satellites
- **Topology**: T2 (1 master, 3 satellites, S0 is admin)
- **Action**: S0 sends BROADCAST wrapping a BUS message
- **Expected**: S1 and S2 receive the wrapped BUS message; M0 `broadcast_callback` fires

### TS-BC-02 · Non-Admin BROADCAST is Rejected
- **Topology**: T2
- **Setup**: S0 `is_admin=False`
- **Action**: S0 sends BROADCAST
- **Expected**: M0 fires `illegal_callback`; S1, S2 do NOT receive anything

### TS-BC-03 · BROADCAST with target_site_id
- **Topology**: T2 (S1 has `site_id="living-room"`)
- **Action**: M0 broadcasts with `target_site_id="living-room"`
- **Expected**: Only S1 receives and injects to its bus; S0, S2 receive the frame but do not inject

### TS-BC-04 · BROADCAST Descends Through Relay
- **Topology**: T9 (relay R0 between M0 and S0, S1)
- **Action**: M0 broadcasts a message
- **Expected**: R0 receives + forwards downstream; S0 and S1 receive

---

## Group 5: PROPAGATE

### TS-PROP-01 · PROPAGATE Fan-Out
- **Topology**: T2
- **Action**: S0 sends PROPAGATE wrapping a BUS message
- **Expected**: M0 forwards to S1, S2 (all siblings); M0 emits `hive.send.upstream` on FakeBus

### TS-PROP-02 · PROPAGATE Respects can_propagate=False
- **Topology**: T2
- **Setup**: S0 `can_propagate=False`
- **Action**: S0 sends PROPAGATE
- **Expected**: M0 fires `illegal_callback`; no forwarding

### TS-PROP-03 · PROPAGATE Crosses Master Boundary
- **Topology**: T3 (M0 → M1 → S0)
- **Action**: S0 sends PROPAGATE
- **Expected**: M1 forwards upstream (emits `hive.send.upstream`); M0 receives

### TS-PROP-04 · PROPAGATE Does Not Loop
- **Topology**: T5 (diamond)
- **Action**: S0 sends PROPAGATE to M0
- **Expected**: M0 forwards; message does not return to S0

---

## Group 6: ESCALATE

### TS-ESC-01 · ESCALATE Goes Upstream Only
- **Topology**: T3 (M0 → M1 → S0)
- **Action**: S0 sends ESCALATE
- **Expected**: M1 receives + emits `hive.send.upstream`; M0 receives; S0 does NOT receive

### TS-ESC-02 · ESCALATE Respects can_escalate=False
- **Topology**: T1
- **Setup**: S0 `can_escalate=False`
- **Action**: S0 sends ESCALATE
- **Expected**: `illegal_callback` fires; not forwarded

### TS-ESC-03 · ESCALATE Stops at Top Master
- **Topology**: T4 (tree)
- **Action**: S0 sends ESCALATE
- **Expected**: S0 → M1 → M0; M0 has no upstream, stops; other satellites (S2..S5) never receive

---

## Group 7: INTERCOM

### TS-IC-01 · Satellite-to-Satellite via Same Master
- **Topology**: T2
- **Action**: S0 sends INTERCOM encrypted with S1's public key
- **Expected**: M0 receives, decryption fails (wrong key), routes to S1; S1 decrypts successfully

### TS-IC-02 · INTERCOM with Wrong Key Silently Ignored
- **Topology**: T2
- **Action**: S0 sends INTERCOM encrypted with S1's public key; S2 also receives it
- **Expected**: S2 cannot decrypt (returns False), message discarded at S2

### TS-IC-03 · INTERCOM via Relay
- **Topology**: T9
- **Action**: S0 (under R0) sends INTERCOM to S2 (under different relay R1)
- **Expected**: Travels S0 → R0 → M0 → R1 → S2; each relay passes it without decrypting

---

## Group 8: BINARY

### TS-BIN-01 · RAW_AUDIO Upload
- **Topology**: T1
- **Action**: S0 sends BINARY with `bin_type=RAW_AUDIO`, 4096 bytes of fake audio, metadata `sample_rate=16000`
- **Expected**: M0's `binary_data_protocol.handle_microphone_input` called with correct bytes + SR

### TS-BIN-02 · STT_AUDIO_TRANSCRIBE
- **Topology**: T1
- **Action**: S0 sends BINARY `STT_AUDIO_TRANSCRIBE` with `lang="en-us"`
- **Expected**: M0 calls `handle_stt_transcribe_request(bin_data, 16000, 2, "en-us", client)`

### TS-BIN-03 · STT_AUDIO_HANDLE
- **Topology**: T1
- **Action**: S0 sends BINARY `STT_AUDIO_HANDLE`
- **Expected**: `handle_stt_handle_request` invoked

### TS-BIN-04 · TTS_AUDIO Download (Master → Satellite)
- **Topology**: T1
- **Action**: M0 sends BINARY `TTS_AUDIO` to S0 with utterance metadata
- **Expected**: S0's `bin_callbacks.handle_receive_tts` invoked with correct data

### TS-BIN-05 · FILE Transfer
- **Topology**: T1
- **Action**: S0 sends BINARY `FILE` with `file_name="test.txt"` and payload
- **Expected**: `handle_receive_file` called with correct filename and bytes

### TS-BIN-06 · NUMPY_IMAGE
- **Topology**: T1
- **Action**: S0 sends BINARY `NUMPY_IMAGE` with `camera_id="front"`
- **Expected**: `handle_numpy_image` called

### TS-BIN-07 · UNDEFINED Binary (Warning Path)
- **Topology**: T1
- **Action**: S0 sends BINARY `UNDEFINED`
- **Expected**: Warning logged, no crash; no handler invoked

### TS-BIN-08 · Binary via Binarize Mode (Protocol V2)
- **Topology**: T8 (S2 is binary mode)
- **Action**: S2 sends a BUS message via binary bitstring encoding
- **Expected**: M0 decodes bitstring → same message as JSON equivalent

---

## Group 9: PING

### TS-PING-01 · Ping Maps the Network
- **Topology**: T4 (tree)
- **Action**: M0 sends PING
- **Expected**: All nodes respond; M0 collects route data for all 6 satellites + 3 sub-masters

See `test_ping_pong.py` for 49 comprehensive PING tests covering topology discovery,
response aggregation, and multi-hop scenarios.

---

## Group 10: Access Control & Blacklisting

### TS-ACL-01 · Message Type Blacklist
- **Topology**: T1
- **Setup**: S0's `msg_blacklist = ["speak"]`
- **Action**: M0 sends BUS with `type="speak"` to S0
- **Expected**: `send()` returns early; S0 never receives the message

### TS-ACL-02 · Skill Blacklist Injected into Session
- **Topology**: T1
- **Setup**: S0's `skill_blacklist = ["mycroft.volume.skill"]`
- **Action**: S0 sends a BUS message
- **Expected**: The message context forwarded to M0's FakeBus has `blacklisted_skills=["mycroft.volume.skill"]`

### TS-ACL-03 · Intent Blacklist
- **Topology**: T1
- **Setup**: S0's `intent_blacklist = ["mycroft.volume.skill:set.volume"]`
- **Action**: S0 sends BUS
- **Expected**: `blacklisted_intents` populated in session context

---

## Group 11: Stress & Load

### TS-LOAD-01 · 50 Satellites, 1 Broadcast
- **Topology**: T7
- **Action**: M0 broadcasts 1 message
- **Expected**: All 50 satellites receive within 10s; 0 missed

### TS-LOAD-02 · 50 Satellites Simultaneously Send BUS
- **Topology**: T7
- **Action**: All 50 satellites send 1 BUS message each at the same time
- **Expected**: M0's FakeBus receives exactly 50 messages; no duplicates; no drops

### TS-LOAD-03 · Sustained Message Rate
- **Topology**: T2
- **Action**: S0 sends 1000 BUS messages as fast as possible
- **Expected**: All 1000 arrive at M0's FakeBus; no message lost; memory stable

---

## Group 12: Route & Hop Tracking

### TS-ROUTE-01 · Route Populated on Multi-Hop ESCALATE
- **Topology**: T3
- **Action**: S0 sends ESCALATE
- **Expected**: When M0 receives, `message.route` has 2 entries: `[S0→M1, M1→M0]`

### TS-ROUTE-02 · source_peer Updated at Each Hop
- **Topology**: T3
- **Action**: S0 sends PROPAGATE
- **Expected**: M0 sees `source_peer=M1.peer` (the last forwarder), not `S0.peer`

### TS-ROUTE-03 · target_peers Trimmed After Delivery
- **Topology**: T2
- **Action**: M0 sends BROADCAST targeting S0 and S1
- **Expected**: When S1 receives, S0 is no longer in `target_peers`

---

## Additional Test Groups (not individually catalogued)

The following test files cover scenarios beyond the numbered groups above.
See `02-protocol-coverage.md` for the complete file listing.

- **Protocol rules** (`test_protocol_rules.py`, 14 tests) — protocol invariant validation
- **Protocol fixes** (`test_protocol_fixes.py`, 8 tests) — regression tests for bugs fixed upstream
- **Embedded clients** (`test_embedded_*.py`, 37 tests) — ESP32/MicroPython client compatibility
- **Voice PE** (`test_voice_pe_*.py`, 31 tests) — Voice PE protocol and satellite integration
- **All topologies** (`test_all_topologies.py`, 27 tests) — cross-topology protocol validation
- **Ovoscope integration** (`test_ovoscope_integration.py`, 9 tests) — real IntentService roundtrip
- **HelloWorld** (`test_helloworld_hivemind.py`, 15 tests) — full utterance→skill→speak via ovoscope
- **Utterance flow** (`test_utterance_flow.py`, 6 tests) — end-to-end utterance processing
- **Solver harness** (`test_solver_harness.py`, 5 tests) — solver plugin integration
- **Topology plots** (`test_topology_plots.py`, 12 tests) — topology visualization
