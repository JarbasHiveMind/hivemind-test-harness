# HiveMind Test Harness — E2E Test Coverage TODO

## Fully Covered
- [x] Connection & Handshake (password PAKE, crypto, session sync, admin enforcement)
- [x] BUS messages (injection, destination routing, session context, peer stamping, allowed_types)
- [x] BROADCAST (admin-only, target_site_id, fan-out, illegal callback)
- [x] PROPAGATE (fan-out + relays, can_propagate ACL, upstream forwarding)
- [x] ESCALATE (upstream-only, can_escalate ACL, relay chain)
- [x] PING flood discovery (responsive PING, dedup, HiveMapper, relay forwarding, RTT, 6 topologies)
- [x] Utterance→Speak lifecycle (injection, speak routing, peer context, session roundtrip, multi-sat isolation)
- [x] Binary protocol upstream (RAW_AUDIO, STT_AUDIO_HANDLE, STT_AUDIO_TRANSCRIBE, FILE)
- [x] Binary protocol downstream (TTS_AUDIO with metadata)
- [x] Access control (message/skill/intent blacklists, allowed_types)
- [x] SHARED_BUS monitoring (passive callback, disabled by default)
- [x] Protocol rules (payload vs transport, forwarding semantics, illegal action disconnect)
- [x] Deep topology routing (chain 2-hop, deep chain 3-hop, peer tracking)
- [x] Embedded client interop (MicroPython crypto, binary encoding)

- [x] Binarize mode E2E (bitstring encoding path, 7 tests)
- [x] Bitstring cross-repo interop (Python↔JS↔C↔MicroPython vectors, 30+ tests across repos)
- [x] NUMPY_IMAGE binary type (upstream dispatch + metadata, 2 tests)
- [x] JS client E2E (session fix, utterance+speak flow, 3 tests)
- [x] Invalid handshake scenarios (password mismatch, plaintext rejection, default session, key rotation, last-seen, 5 tests)

### E2E Skill Tests (81 tests, 11 files)
- [x] Multi-skill round-trip (date-time, personal, naptime, fallback, easter-eggs, spelling — 11 tests)
- [x] Volume skill + satellite-side MockVolumePHAL (max, mute, unmute, increase, decrease, presets, toggle, query — 13 tests)
- [x] Relay chain topology with real skills (chain, deep chain, volume through relay — 5 tests)
- [x] Multi-satellite response isolation (originator-only routing, concurrent utterances — 3 tests)
- [x] Session state propagation (lang, session_id, pipeline override, multi-turn — 4 tests)
- [x] ACL with real skills (skill_blacklist, intent_blacklist, msg_blacklist — 8 tests)
- [x] Converse mode (parrot skill repeat, parrot mode, stop parrot — 5 tests)
- [x] Misc skills + edge cases (IP, count, empty utterances, rapid fire — 8 tests)
- [x] Multi-turn get_response() (randomness "make a choice", volume "change volume" — 7 tests)
- [x] Stop command (interrupt counting, ping/pong, global stop — 7 tests)
- [x] ask_yesno + ask_selection (injected test skills, easter-eggs sing, numeric selection — 10 tests)
- [x] OCP messages routing (injected OCPTestSkill, search results + track info — 5 tests)
- [x] Shared bus mode (shared_bus=True satellite mirroring — 4 tests)
- [x] Admin broadcast (admin fan-out, non-admin rejection — 4 tests)
- [x] Relay ACL stacking (relay-level + leaf-level blacklists — 5 tests)
- [x] Language-dependent intent matching (en-US, de-DE, fr-FR — 5 tests)
- [x] Advanced converse: cancel, timeout, concurrent get_response, dictation (7 tests)
- [x] Event scheduler through HiveMind (schedule_event callback — 4 tests)
- [x] Binary audio + skill pipeline (RAW_AUDIO delivery, mixed binary+BUS — 5 tests)

## Missing — Priority 1 (testable now)
- [ ] INTERCOM routing — inner message dispatch after RSA decryption (returns False; incomplete impl)
- [ ] Protocol version rejection — min/max version mismatch disconnection
- [ ] RSA key exchange path — only password PAKE tested; RSA pubkey handshake untested

## Missing — Priority 2 (architectural gaps / stubs)
- [ ] Broadcast downstream through relays — M0→R1_sat works, but NOT S0 behind R1
- [ ] QUERY handler — enum defined, handler is stub, only no-crash test
- [ ] CASCADE handler — enum defined, handler is stub, only no-crash test
- [ ] RENDEZVOUS handler — enum defined, handler is stub, only no-crash test
- [ ] Database live-reload — blacklist updates via DB sync during active connection
- [ ] Reconnection — satellite reconnect-after-disconnect
- [ ] Concurrent message ordering — no ordering guarantees tested under load
