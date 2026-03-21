# E2E Skill Tests — Real OVOS Skills Through HiveMind

End-to-end tests that route utterances through the full HiveMind pipeline with real OVOS skill plugins running in MiniCroft. 81 tests across 11 files.

## How It Works

### Architecture

```
Satellite (FakeBus)              Hub (MiniCroft FakeBus)
┌───────────────────┐           ┌────────────────────────┐
│ SatelliteNode     │           │ MasterNode             │
│  ├─ internal_bus  │  HiveMind │  ├─ OvoscopeAgentProto │
│  ├─ MockVolumePHAL│◄─────────►│  │   ├─ MiniCroft     │
│  └─ shim          │  BUS msgs │  │   ├─ IntentService  │
│                   │           │  │   └─ Real Skills    │
│ [user speaks]     │           │  └─ MessageRecorder    │
│  → utterance ────────────────►│  → intent match        │
│                   │           │  → skill handler       │
│  ← speak    ◄─────────────────│  ← speak / volume.set │
└───────────────────┘           └────────────────────────┘
```

1. **Satellite** sends `recognizer_loop:utterance` via `SatelliteNode.send()`
2. HiveMind wraps it in `HiveMessage(BUS)`, routes to master
3. Master's `OvoscopeAgentProtocol` injects it on MiniCroft's FakeBus
4. MiniCroft's IntentService matches the intent, invokes the skill handler
5. Skill emits `speak` (or `mycroft.volume.set`, etc.) on the bus
6. HiveMind's reverse routing sends the response back to the originating satellite
7. Satellite's `internal_bus` receives the message

### OvoscopeAgentProtocol

`OvoscopeAgentProtocol` — `plugins/ovoscope_agent.py` — replaces `TestAgentProtocol`'s bare FakeBus with a live MiniCroft instance that runs real OVOS skills.

Key methods:
- `new_capture()` → `_HarnessCaptureSession` — records all bus messages until EOF (`ovos.utterance.handled`)
- `wait_for_skill_emission(msg_type, timeout)` — polls until a message type appears
- `spoken_utterances()` → list of all speak texts
- `clear()` — resets recorded messages between tests
- `shutdown()` — stops MiniCroft

### Session Management

Every satellite has a stable `shim.session_id`. When sending utterances, the session must include:
- `session_id` — matches the satellite's shim session (so HiveMind doesn't reject it)
- `pipeline` — list of intent pipeline plugins to use
- `lang` — language code (default `en-US`)

The `make_utterance()` helper in `conftest.py` builds correctly-formed messages.

---

## Continuous Dialog Through HiveMind

OVOS skills support multi-turn dialog via three methods, all of which work through HiveMind because they use the same converse/response_mode session mechanism.

### get_response()

The skill asks a follow-up question and waits for the user's answer.

```
Satellite                    Hub (MiniCroft)
   │                            │
   │ "make a choice" ──────────►│ skill.handle_make_a_choice()
   │                            │   get_response("first-choice")
   │◄── speak("What is the     │     enable_response_mode(skill_id)
   │     first choice?")        │     speak_dialog(expect_response=True)
   │     expect_response=True   │     _wait_response() [blocking]
   │                            │
   │ "pizza" ──────────────────►│   converse pipeline intercepts
   │                            │   response_mode active → deliver to skill
   │                            │   get_response returns "pizza"
   │                            │   get_response("second-choice")
   │◄── speak("What is the     │     ...same flow...
   │     second choice?")       │
   │ "pasta" ──────────────────►│   get_response returns "pasta"
   │                            │   choice = random(pizza, pasta)
   │◄── speak("I choose pizza")│
```

**How it works under HiveMind:**
1. Skill calls `get_response("dialog")` → calls `session.enable_response_mode(skill_id)`
2. Skill speaks the question with `expect_response=True`
3. HiveMind routes the speak to the satellite (with session context intact)
4. Satellite sends the next utterance with the **same session_id**
5. HiveMind routes it to the hub → MiniCroft's converse pipeline intercepts it
6. Because `response_mode` is active for this session, the utterance goes directly to the skill's waiting `get_response()` instead of re-running intent matching
7. Skill gets the answer, continues execution

**Pipeline requirement:** The session's pipeline list MUST include `"ovos-converse-pipeline-plugin"` for get_response/converse to work.

**Testing pattern:** `SatelliteAutoResponder` listens for `speak` with `expect_response=True` on the satellite bus and auto-sends predefined responses back through HiveMind.

### ask_yesno()

Wraps `get_response()` with yes/no answer processing.

```python
answer = self.ask_yesno("do you like pizza")  # → "yes", "no", or None
```

Internally: calls `get_response(dialog)` → passes result through `YesNoSolver` plugin → returns `"yes"`, `"no"`, or the raw response string.

The HiveMind flow is identical to `get_response()` — the answer processing happens entirely on the hub side after the user's response arrives.

### ask_selection()

Wraps `get_response()` with option matching.

```python
choice = self.ask_selection(["red", "green", "blue"], "pick a color")
```

Flow:
1. Skill speaks each option (or a comma-separated list)
2. Skill calls `get_response()` for the user's choice
3. Passes result through `OptionMatcherEngine` plugin (fuzzy match)
4. Returns the matched option string or `None`

With `numeric=True`, options are spoken as a numbered menu ("1, red. 2, green. 3, blue.") and the user can respond with a number.

### Testing Injected Skills

For `ask_yesno` and `ask_selection`, the test suite uses **injected test skills** — lightweight `OVOSSkill` subclasses defined directly in the test file and loaded via `extra_skills`:

```python
agent = OvoscopeAgentProtocol(
    skill_ids=["ovos-skill-easter-eggs.openvoiceos"],
    extra_skills={
        "ask-yesno-test-skill.test": AskYesNoTestSkill,
        "ask-selection-test-skill.test": AskSelectionTestSkill,
    }
)
```

This avoids depending on complex skills (like alerts) that have many dependencies.

---

## Stop Command Through HiveMind

The stop flow involves a ping/pong protocol between the hub's StopService and active skills.

```
Satellite                    Hub (MiniCroft)
   │                            │
   │ "count to ten" ───────────►│ CountSkill.handle_count()
   │◄── speak("one")           │   active_sessions[sess] = True
   │◄── speak("two")           │   loop: speak(n), sleep(1)
   │                            │
   │ "stop" ───────────────────►│ StopService receives utterance
   │                            │   → {skill_id}.stop.ping to all active skills
   │                            │   ← skill.stop.pong (can_stop=True)
   │                            │   → {skill_id}.stop
   │                            │   CountSkill.stop_session()
   │                            │     active_sessions[sess] = False
   │                            │   loop exits on next iteration
   │                            │   ← {skill_id}.stop.response(result=True)
```

**Pipeline requirement:** Session pipeline MUST include `"ovos-stop-pipeline-plugin"` for stop to work.

**Session-aware stopping:** Skills like `ovos-skill-count` track active state per session_id in `active_sessions` dict. When stopped, only the requesting session's counting loop exits — other sessions are unaffected.

---

## Satellite-Side PHAL (Volume Control)

PHAL plugins handle hardware I/O on the satellite. The key insight: the **skill** runs on the hub, but the **PHAL handler** runs on the satellite.

```
Satellite                    Hub (MiniCroft)
   │                            │
   │ "maximum volume" ─────────►│ VolumeSkill.handle_max_volume()
   │                            │   bus.emit("mycroft.volume.set", percent=1.0)
   │                            │   speak("Volume max")
   │                            │
   │◄── mycroft.volume.set     │ HiveMind routes volume msg back
   │    (percent=1.0)           │
   │    MockVolumePHAL records  │
   │                            │
   │◄── speak("Volume max")    │ HiveMind routes speak back
```

### MockVolumePHAL

In production, `ovos-PHAL-plugin-alsa` (or `ovos-PHAL-plugin-pulseaudio`) listens for `mycroft.volume.*` messages and controls the hardware mixer. In tests, `MockVolumePHAL` registers the same handlers on `satellite.internal_bus`:

```python
class MockVolumePHAL:
    VOLUME_EVENTS = [
        "mycroft.volume.set", "mycroft.volume.increase",
        "mycroft.volume.decrease", "mycroft.volume.mute",
        "mycroft.volume.unmute", "mycroft.volume.mute.toggle",
        "mycroft.volume.get",
    ]
    def __init__(self, bus):
        for evt in self.VOLUME_EVENTS:
            bus.on(evt, self._on_volume)
```

This proves that HiveMind correctly delivers volume control messages to the satellite where the PHAL plugin would be running.

### Hub-Side Volume Mock

The volume skill's `handle_increase_volume_intent` and `handle_less_volume_intent` call `_query_volume()` which does `bus.wait_for_response("mycroft.volume.get")` on the **hub's** MiniCroft bus (not the satellite). Without a mock responder, these intents timeout:

```python
agent.bus.on("mycroft.volume.get",
             lambda m: agent.bus.emit(m.response({"percent": 0.5, "muted": False})))
```

### Volume Messages Reference

| Utterance | Skill Intent | Bus Message | Data |
|-----------|-------------|-------------|------|
| "maximum volume" | volume.max.intent | `mycroft.volume.set` | `percent=1.0` |
| "high volume" | volume.high.intent | `mycroft.volume.set` | `percent=0.9` |
| "default volume" | volume.default.intent | `mycroft.volume.set` | `percent=0.7` |
| "low volume" | volume.low.intent | `mycroft.volume.set` | `percent=0.3` |
| "increase the volume" | increase_volume | `mycroft.volume.increase` | `percent=0.1` |
| "decrease the volume" | less_volume | `mycroft.volume.decrease` | `percent=0.1` |
| "mute" | volume.mute.intent | `mycroft.volume.mute` | — |
| "unmute" | volume.unmute.intent | `mycroft.volume.unmute` | — |
| "toggle mute" | volume.mute.toggle.intent | `mycroft.volume.mute.toggle` | — |
| "what is the volume" | current_volume | `mycroft.volume.get` | — |

---

## ACL with Real Skills

Access control lists are set at satellite registration time and cannot change during a connection.

### skill_blacklist

Blocks an entire skill from executing for a satellite. The skill_id is injected into `session.blacklisted_skills` — MiniCroft's IntentService skips matching against blacklisted skills.

```python
b.add_satellite("S0", upstream=master, skill_blacklist=["ovos-skill-hello-world.openvoiceos"])
# S0's "hello world" → complete_intent_failure (hello-world skipped)
# S1's "hello world" → HelloWorldIntent (no restriction)
```

### intent_blacklist

Blocks specific intents while allowing other intents in the same skill. Injected into `session.blacklisted_intents`.

```python
b.add_satellite("S0", upstream=master,
                intent_blacklist=["ovos-skill-hello-world.openvoiceos:HelloWorldIntent"])
# S0's "hello world" → fails (adapt HelloWorldIntent blocked)
# S0's "good morning" → Greetings.intent (padatious intent not blocked)
```

### msg_blacklist

Blocks specific message types from being delivered to the satellite. The skill still executes on the hub — only the response delivery is filtered.

```python
b.add_satellite("S0", upstream=master, msg_blacklist=["speak"])
# S0's "hello world" → skill fires, speak emitted on hub bus
# But satellite never receives the speak message
```

---

## Multi-Satellite Response Isolation

In a star topology (M0 + S0, S1, S2), responses route **only** to the originating satellite:

- S0 sends utterance → only S0 receives the `speak` response
- S1 and S2 are unaffected
- Volume messages (`mycroft.volume.mute`) also route only to the sender

This is enforced by HiveMind's peer tracking in `message.context["destination"]`.

---

## OCP (Open Common Play)

OCP skills use `@ocp_search()` decorators to handle media queries. They emit `ovos.common_play.query.response` with search results and `ovos.common_play.track_info` with playback metadata. These messages route through HiveMind like any other bus message.

Tested via an injected `OCPTestSkill` that emits OCP messages when triggered, verifying they arrive on the satellite bus with correct data.

---

## Shared Bus Mode

Satellites created with `shared_bus=True` passively mirror **all** internal bus traffic upstream as `SHARED_BUS` HiveMessages. This gives the master visibility into satellite-side events (e.g., local sensor data, PHAL events).

```
Satellite (shared_bus=True)         Master
┌────────────────────┐             ┌──────────────┐
│ internal_bus.emit  │             │              │
│   ("sensor.data")  │─SHARED_BUS─►│ shared_bus   │
│                    │             │  _callback() │
└────────────────────┘             └──────────────┘
```

Normal (non-shared) satellites do NOT mirror bus events.

---

## Admin Broadcast

Admin satellites can broadcast messages to all sibling satellites. Non-admin broadcasts are rejected and the offending client is disconnected.

---

## Relay ACL Stacking

When a message traverses a relay chain (S0 → R1 → M0), blacklists are applied at **each hop**:

1. R1 applies its ACL for S0 (as S0's direct master)
2. M0 applies its ACL for R1 (as R1's master)

This means: if R1 has `skill_blacklist=["hello-world"]` as a client of M0, then **all** satellites behind R1 inherit that restriction, even if they have no individual blacklists.

---

## Language-Dependent Intent Matching

The session `lang` field propagates through HiveMind. Skills only match intents for languages they have locale files for. An English-only skill (hello-world) won't match German utterances sent with `lang="de-DE"`.

---

## Event Scheduler

Skills call `self.schedule_event(handler, delay)` to fire a callback after a delay. The scheduler runs on the hub's MiniCroft bus via `mycroft.scheduler.schedule_event`. When the callback fires, any `speak` or bus messages it emits route back through HiveMind to the originating satellite.

---

## Binary Audio + Skill Pipeline

The full audio pipeline: satellite sends `BINARY(RAW_AUDIO)` → master's binary protocol dispatches to `handle_microphone_input()` → STT engine transcribes → `recognizer_loop:utterance` emitted → skill matches → `speak` routes back.

In tests, actual STT is not available, so binary delivery and utterance injection are tested as linked steps.

---

## Dictation Skill (Converse + Stop)

The dictation skill combines converse mode with stop capability:
1. "start dictation" → activates converse mode (`can_converse()` returns True)
2. All subsequent utterances are captured by `converse()` instead of intent matching
3. "stop dictation" → deactivates converse, saves captured text

This tests the most complex OVOS skill interaction pattern through HiveMind: a skill that hijacks the entire conversation pipeline.

---

## Test File Reference

| File | Tests | Skills | Pattern |
|------|-------|--------|---------|
| `test_e2e_skills.py` | 11 | date-time, personal, naptime, fallback, easter-eggs, spelling | Basic utterance → speak |
| `test_e2e_volume_phal.py` | 13 | volume | MockVolumePHAL on satellite bus |
| `test_e2e_relay_skills.py` | 5 | hello-world, volume | Chain/deep relay topologies |
| `test_e2e_multi_satellite.py` | 3 | hello-world, volume | Star topology isolation |
| `test_e2e_session.py` | 4 | hello-world, date-time | Session state propagation |
| `test_e2e_acl_skills.py` | 8 | hello-world, date-time, volume | skill/intent/msg blacklists |
| `test_e2e_converse.py` | 5 | parrot | Converse mode |
| `test_e2e_misc_skills.py` | 8 | IP, count, hello-world | Edge cases |
| `test_e2e_get_response.py` | 7 | randomness, volume | Multi-turn get_response() |
| `test_e2e_stop.py` | 7 | count, hello-world | Stop command + ping/pong |
| `test_e2e_ask_yesno_selection.py` | 10 | easter-eggs + injected | ask_yesno, ask_selection |
| `test_e2e_ocp.py` | 5 | injected OCPTestSkill | OCP search results + track info |
| `test_e2e_shared_bus.py` | 4 | hello-world | shared_bus=True mirroring |
| `test_e2e_admin_broadcast.py` | 4 | hello-world | Admin broadcast + rejection |
| `test_e2e_relay_acl.py` | 5 | hello-world, date-time | Relay ACL stacking |
| `test_e2e_lang.py` | 5 | hello-world + injected | Lang propagation + mismatch |
| `test_e2e_converse_advanced.py` | 7 | randomness, dictation + injected | Cancel, timeout, concurrent, dictation |
| `test_e2e_scheduler.py` | 4 | injected SchedulerTestSkill | schedule_event() callback |
| `test_e2e_binary_skill.py` | 5 | hello-world | Binary audio + skill response |

---

## Running E2E Skill Tests

### Prerequisites

Install required skills:
```bash
cd "HiveMind Workspace"
for skill in hello-world date-time personal naptime volume easter-eggs spelling \
             ip count parrot randomness fallback-unknown; do
    uv pip install -e "../../OpenVoiceOS Workspace/Skills/ovos-skill-${skill}"
done
```

### Run all E2E tests
```bash
cd "HiveMind Workspace/hivemind-test-harness"
uv run pytest tests/test_e2e_*.py -v --timeout=300
```

### Run a specific test file
```bash
uv run pytest tests/test_e2e_volume_phal.py -v --timeout=120
```

### Skip tests for uninstalled skills
Tests auto-skip via `@pytest.mark.skipif(skill_missing(...))` when a skill is not installed.

### First run is slow
MiniCroft trains Padatious intent models on first boot (~2 minutes). Subsequent runs use cached models from `~/.local/share/mycroft/intent_cache/`.
