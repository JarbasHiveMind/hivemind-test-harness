# Message Routing & Session Flow

This document explains the end-to-end path that messages travel through HiveMind and OVOS,
the context keys that drive routing, and how `session_id` is preserved across the roundtrip.
It is the reference for writing test assertions about routing behaviour.

---

## 1. The Full Roundtrip

A satellite sends an utterance and receives a skill response back. Here is every hop in the chain,
with the responsible code at each step.

```
[Satellite]                     [HiveMind Master]                    [OVOS Bus / MiniCroft]
     │                                  │                                       │
     │ satellite.send(                  │                                       │
     │   HiveMessage(BUS, utterance))   │                                       │
     │─────────────────────────────────►│                                       │
     │                                  │ HiveMindListenerProtocol              │
     │                                  │   .handle_bus_message()               │
     │                                  │   ._update_blacklist()                │
     │                                  │     context["session"] = sess (L903)  │
     │                                  │   .handle_inject_agent_msg()          │
     │                                  │     context["destination"] = "skills" │
     │                                  │         (or peer if already set)      │
     │                                  │     context["peer"] = client.peer     │
     │                                  │     context["source"] = client.peer   │
     │                                  │         (protocol.py:942-950)         │
     │                                  │─────────────────────────────────────►│
     │                                  │   bus.emit(message)                   │
     │                                  │                                       │ IntentService
     │                                  │                                       │   .handle_utterance()
     │                                  │                                       │   sess = _validate_session()
     │                                  │                                       │   pipeline match → skill
     │                                  │                                       │   sess.activate_skill(id)
     │                                  │                                       │
     │                                  │                                       │ skill.speak("hello world")
     │                                  │                                       │   reply = msg.reply("speak", ...)
     │                                  │                                       │   ← source ↔ destination swapped
     │                                  │                                       │   destination = original source
     │                                  │                                       │             = client.peer
     │                                  │                                       │   reply.context["session"] = updated_sess
     │                                  │                                       │   bus.emit(reply)
     │                                  │                                       │
     │                                  │◄─────────────────────────────────────│
     │                                  │ TestAgentProtocol                     │
     │                                  │   .handle_internal_mycroft(message)   │
     │                                  │   target_peers = context["destination"]
     │                                  │   for peer in target_peers:           │
     │                                  │     context["source"] = "hive"        │
     │                                  │     wrap in HiveMessage(BUS, ...)     │
     │                                  │     client.send(msg)                  │
     │◄─────────────────────────────────│                                       │
     │ satellite._receive_raw()         │                                       │
     │   decrypt + record               │                                       │
     │   slave_protocol dispatch        │                                       │
     │   internal_bus.emit("speak")     │                                       │
```

In tests, assertions happen at two points:
- **Master side**: `agent_protocol.injected`: what the OVOS bus received
- **Satellite side**: `satellite.internal_bus` or `satellite.recorder`: what the satellite received back

---

## 2. `Message.reply()`: The Root Mechanism

The entire reverse routing mechanism rests on a single method in `ovos-bus-client`:

```python
# ovos-bus-client/ovos_bus_client/message.py  lines 195-199

if 'source' in new_context and 'destination' in new_context:
    s = new_context['destination']
    new_context['destination'] = new_context['source']
    new_context['source'] = s
return Message(msg_type, data, context=new_context)
```

Every OVOS skill response is created via `message.reply(...)`. When HiveMind injects an utterance
it sets:

```python
context["source"] = client.peer          # e.g. "HiveMindV0.0@127.0.0.1:8222/0"
context["destination"] = "skills"        # or [peer] if already set upstream
```

When `IntentService` emits the match message as a reply, the swap fires:
- `destination` becomes `"HiveMindV0.0@127.0.0.1:8222/0"` (the satellite peer ID)
- `source` becomes `"skills"`

Every subsequent message in the chain: `speak`, `mycroft.skill.handler.complete`,
`ovos.utterance.handled`: is also a `.reply()` of a `.reply()`, so the destination keeps pointing
at the peer that originated the utterance.

`TestAgentProtocol.handle_internal_mycroft()` (ported from `OVOSProtocol.handle_internal_mycroft()`
in `ovos-bus-client/ovos_bus_client/hpm.py:79-102`) reads `context["destination"]` and routes
the message to the matching peer connection. No explicit registration per message type: the
destination context key is the sole routing mechanism.

---

## 3. Context Keys Reference

All keys that participate in routing, with the exact location they are set:

| Key | Set by | File:Line | Value | Role |
|-----|--------|-----------|-------|------|
| `context["peer"]` | `handle_inject_agent_msg` | `hivemind_core/protocol.py:949` | Satellite peer ID (e.g. `"HiveMindV0.0@127.0.0.1:8222/0"`) | Identifies which satellite sent this; test assertions use this to verify routing |
| `context["source"]` (inbound) | `handle_inject_agent_msg` | `protocol.py:949` | Same as `peer` | OVOS origin identifier; becomes `destination` after `Message.reply()` swap |
| `context["destination"]` (inbound) | `handle_inject_agent_msg` | `protocol.py:942-945` | `"skills"` (default), or `["audio"]` for injected `speak` | Prevents message being treated as broadcast by OVOS |
| `context["session"]` | `_update_blacklist` | `protocol.py:903` | `client.sess.serialize()`: full `Session` dict | Per-satellite session state injected before OVOS sees the message |
| `context["session"]["session_id"]` | `Session.__init__` | `ovos-bus-client/session.py:311` | UUID (or explicit string e.g. `"test-session"`) | Stable identifier that persists across utterances in the same session |
| `context["session"]["blacklisted_skills"]` | `_update_blacklist` | `protocol.py:918` | List of skill IDs from DB + session | ACL enforcement: IntentService skips these skills |
| `context["session"]["blacklisted_intents"]` | `_update_blacklist` | `protocol.py:921` | List of intent names | ACL enforcement: IntentService skips these intents |
| `context["destination"]` (outbound) | `Message.reply()` swap | `message.py:195-198` | Peer ID (swapped from `source`) | **This is what `handle_internal_mycroft` reads to route back** |
| `context["source"]` (outbound) | `handle_internal_mycroft` | `hpm.py:95` / `agent.py:123` | `"hive"` | Marks the message as coming from HiveMind master |

### Key diagnostic: what to assert in tests

```python
# On the master side: what arrived at the OVOS bus
msg = master.agent_protocol.last_injected("recognizer_loop:utterance")
assert msg.context["peer"] == satellite._connection.peer  # routing identity
assert msg.context["source"] == satellite._connection.peer  # same
assert msg.context["session"]["session_id"] == "expected-session-id"
assert "bad-skill.openvoiceos" not in msg.context["session"]["blacklisted_skills"]

# On the satellite side: what came back from a skill
# (requires a real OVOS skill or manual bus.emit on the master)
received = []
satellite.internal_bus.once("speak", received.append)
# ... trigger utterance ...
assert received[0].context["source"] == "hive"  # HiveMind stamped it
```

---

## 4. Session ID: Full Lifecycle

`session_id` is the stable identifier that lets OVOS maintain conversational state across
multiple utterances from the same satellite.

### Step-by-step

**1. Created on the satellite**

```python
# tests/test_helloworld_hivemind.py, tests/test_ovoscope_integration.py
from ovos_bus_client.session import Session

sess = Session("test-session")          # explicit ID, or omit for UUID
message = Message(
    "recognizer_loop:utterance",
    {"utterances": ["hello"], "lang": "en-us"},
    {"session": sess.serialize(), "source": "sat", "destination": "master"}
)
satellite.send(message)
```

**2. Extracted and normalised at master (`protocol.py:903`)**

`_update_blacklist()` replaces `context["session"]` with a fresh serialization of
`client.sess`: the master's per-connection session tracking object. This ensures the
master's DB-enforced blacklists are always injected, and that `session_id` from the
satellite's payload is honoured (HiveMind copies the satellite's session into `client.sess`
at line 604-606 during `handle_bus_message`).

**3. Read by OVOS IntentService**

```python
# ovos-core/ovos_core/intent_services/service.py
def _validate_session(self, message, lang):
    sess = SessionManager.sessions.get(message.context["session"]["session_id"])
    if sess is None:
        sess = Session.from_message(message)  # deserialize from context
    sess.lang = lang
    return sess
```

**4. Updated by IntentService after a match**

```python
sess.activate_skill(match.skill_id)            # added to active_skills
reply.context["session"] = sess.serialize()    # packed back into reply context
```

The skill receives the reply message: which includes the updated session: and can read
`Session.from_message(message)` to get the current session state.

**5. Returned to satellite in the `speak` reply**

The speak message (and all subsequent replies) carry `context["session"]` with the updated
session. The satellite can inspect `msg.context["session"]["session_id"]` to confirm identity,
or `msg.context["session"]["active_skills"]` to see which skill is currently active.

**6. Persisting across exchanges**

Pass the same `session_id` in all utterances for that conversation:

```python
sess = Session("my-conversation-123")
satellite.send(Message("recognizer_loop:utterance",
                       {"utterances": ["hello"]},
                       {"session": sess.serialize()}))
# receive speak, extract updated session:
speak_msg = received_speak_messages[0]
updated_sess = Session.deserialize(speak_msg.context["session"])
# send follow-up with same session_id
satellite.send(Message("recognizer_loop:utterance",
                       {"utterances": ["what did you say?"]},
                       {"session": updated_sess.serialize()}))
```

### Session fields relevant to routing

| Field | Type | Notes |
|-------|------|-------|
| `session_id` | `str` | UUID or explicit string; stable key |
| `lang` | `str` | BCP-47 tag; controls which skill vocab loads |
| `active_skills` | `list[list]` | `[[skill_id, timestamp], ...]`; updated on each intent match |
| `utterance_states` | `dict` | `{skill_id: "response" | "intent"}`; tracks `get_response()` state |
| `blacklisted_skills` | `list[str]` | Merged from session + HiveMind DB ACL |
| `blacklisted_intents` | `list[str]` | Merged from session + HiveMind DB ACL |
| `pipeline` | `list[str]` | Intent pipeline order for this session |

---

## 5. `TestAgentProtocol` as Production-Parity Layer

`TestAgentProtocol` is **not a mock**. Its reverse routing logic is ported verbatim from
`OVOSProtocol` in `ovos-bus-client/ovos_bus_client/hpm.py`, which is the agent protocol used
in every real HiveMind deployment.

| Aspect | `TestAgentProtocol` (harness) | `OVOSProtocol` (production) |
|--------|-------------------------------|------------------------------|
| Bus backend | `FakeBus`: in-process, synchronous | `MessageBusClient`: TCP to ovos-messagebus on port 8181 |
| Message recording | `self.injected: List[Message]` | None: live system does not record |
| `register_bus_handlers()` | Identical: subscribes `hive.send.downstream` + `message` | Identical |
| `handle_send()` | Verbatim port (`agent.py:74-102`) | `hpm.py:39-77` |
| `handle_internal_mycroft()` | Verbatim port (`agent.py:104-130`) | `hpm.py:79-102` |
| Client isolation | `destination` context key → only target peer receives | Same |
| BROADCAST/PROPAGATE routing | Routes to all `self.clients` | Same |
| `ESCALATE` | Silently ignored (master cannot escalate) | Same |

The implication: **any test that passes against `TestAgentProtocol` is exercising the same
routing paths as a live OVOS deployment.** If `speak` routes back to the satellite in a test,
it will also route back in production.

### Why `FakeBus` is sufficient

`FakeBus` (from `ovos-utils`) is a synchronous in-process event emitter. When the test harness
calls `bus.emit("speak", reply)`, the `message` catch-all listener in
`register_bus_handlers()` fires synchronously. There is no network latency or thread scheduling
to contend with. This makes test assertions on routing deterministic.

The only path `FakeBus` cannot cover is the TCP socket connecting to a real OVOS messagebus : 
but that socket is exactly what `OvoscopeAgentProtocol` adds (see section 6).

---

## 6. `OvoscopeAgentProtocol`: Live OVOS Integration

`OvoscopeAgentProtocol` extends `TestAgentProtocol` by attaching a real **MiniCroft** instance
(from the `ovoscope` library) as the agent bus backend. MiniCroft runs a full OVOS
IntentService and optionally loads real skill plugins.

```python
# hivemind_test_harness/plugins/ovoscope_agent.py
class OvoscopeAgentProtocol(TestAgentProtocol):
    def __post_init__(self):
        self.minicroft = get_minicroft(self.skill_ids, isolate_config=True)
        self.bus = self.minicroft.bus         # FakeBus connected to real IntentService
        super().__post_init__()               # wires recording + reverse routing handlers
```

The same `handle_internal_mycroft()` path runs. The difference is that `bus.emit()` now
triggers the full OVOS intent pipeline: transformer plugins, Adapt, Padatious, skills : 
before any skill `speak` reply fires back through the routing handler.

**`isolate_config=True` (ovoscope ≥ 0.7.2)** is critical: it clears the host OVOS config
(`Configuration.xdg_configs = []`) and sets a deterministic pipeline before starting
MiniCroft. Without this, the host's `lang: pt-PT` or installed persona plugins can
intercept utterances and prevent `complete_intent_failure` from firing.

### Key methods on `OvoscopeAgentProtocol`

| Method | Purpose |
|--------|---------|
| `wait_for_skill_emission(msg_type, timeout=10)` | Block until OVOS bus emits `msg_type`; returns the message |
| `wait_last_injected(msg_type, timeout=10)` | Wait then return the last `injected` message of that type |
| `new_capture()` → `CaptureSession` | Record all bus messages until `ovos.utterance.handled`; `cap.wait()` returns ordered list |
| `assert_skill_not_emitted(msg_type)` | Assert that `msg_type` was never emitted on the OVOS bus |
| `clear()` | Reset `injected` list between test cases |
| `shutdown()` | Stop MiniCroft (called in module-scoped fixture teardown) |

### When to use `OvoscopeAgentProtocol`

Use it when the test must verify that:
- A specific skill intent was matched (e.g. HelloWorld Adapt intent)
- `speak` text content matches expectations
- Session state (active skills) is updated correctly by the real IntentService
- `complete_intent_failure` fires for truly unknown utterances

Use `TestAgentProtocol` (with manual `bus.emit`) when:
- Testing routing mechanics independent of OVOS (handshake, ACL, relay, binary)
- Running in CI without OVOS installed
- The test is not about intent matching

---

## 7. `ovos.utterance.handled`: The EOF Marker

Every utterance processing path in `IntentService` ends with:

```python
self.bus.emit(message.reply("ovos.utterance.handled", {}))
```

This happens whether the utterance matched a skill, triggered `complete_intent_failure`,
or was cancelled. It is the reliable EOF signal for all test synchronisation:

```python
# Pattern: wait for processing to complete before asserting
agent.wait_for_skill_emission("ovos.utterance.handled")
agent.assert_skill_not_emitted("speak")           # no speak after intent failure
```

`CaptureSession.wait()` uses `ovos.utterance.handled` as its termination condition.

---

## 8. Practical Test Patterns

### Pattern A: Verify utterance arrived with correct peer context

```python
def test_peer_context_set(simple_topology):
    b, agent = simple_topology
    s0 = b.get_satellite("S0")

    s0.send(Message("recognizer_loop:utterance",
                    {"utterances": ["hello"]},
                    {"session": Session("s1").serialize()}))

    msg = agent.last_injected("recognizer_loop:utterance")
    assert msg is not None
    assert msg.context["peer"] == s0._connection.peer
    assert msg.context["source"] == s0._connection.peer
    assert msg.context["session"]["session_id"] == "s1"
```

### Pattern B: Verify skill response routed back to satellite

```python
def test_speak_reverse_routed(ovoscope_topology):
    b, agent = ovoscope_topology
    s0 = b.get_satellite("S0")
    agent.clear()

    received_on_satellite = []
    s0.internal_bus.once("speak", received_on_satellite.append)

    s0.send(_make_utterance("hello"))           # triggers HelloWorld skill
    agent.wait_for_skill_emission("speak")      # skill emitted speak on OVOS bus

    assert received_on_satellite, "speak was not reverse-routed to satellite"
    speak = received_on_satellite[0]
    assert speak.context["source"] == "hive"    # HiveMind stamped it
```

### Pattern C: Verify session_id is stable across two exchanges

```python
def test_session_continuity(ovoscope_topology):
    b, agent = ovoscope_topology
    s0 = b.get_satellite("S0")

    sess = Session("conversation-42")

    agent.clear()
    s0.send(Message("recognizer_loop:utterance",
                    {"utterances": ["hello"], "lang": "en-us"},
                    {"session": sess.serialize()}))
    msg1 = agent.wait_last_injected("recognizer_loop:utterance")
    assert msg1.context["session"]["session_id"] == "conversation-42"

    agent.clear()
    s0.send(Message("recognizer_loop:utterance",
                    {"utterances": ["goodbye"], "lang": "en-us"},
                    {"session": sess.serialize()}))
    msg2 = agent.wait_last_injected("recognizer_loop:utterance")
    assert msg2.context["session"]["session_id"] == "conversation-42"
```

### Pattern D: Assert ACL blacklist injected into session

```python
def test_skill_blacklist_injected(b, master, satellite):
    # Register satellite with a blacklisted skill
    master.hm_protocol.db.get_client_by_api_key(satellite.identity.access_key)
    # ... update skill_blacklist in DB ...

    satellite.send(Message("recognizer_loop:utterance", {"utterances": ["hello"]}))

    msg = master.agent_protocol.last_injected("recognizer_loop:utterance")
    assert "bad-skill.openvoiceos" in msg.context["session"]["blacklisted_skills"]
```

---

## 9. What `handle_inject_agent_msg` Does (Full Summary)

Location: `hivemind-core/hivemind_core/protocol.py:926-956`

1. **ACL authorization** (`client.authorize(message)`): checks `allowed_types` and `msg_blacklist`; drops message if not authorized (line 936-938)
2. **Session injection** (`_update_blacklist(message, client)`): replaces `context["session"]` with `client.sess.serialize()` and appends DB-sourced blacklists (lines 941, 903-924)
3. **Destination normalisation**: sets `context["destination"] = "skills"` if not already set, or `["audio"]` for injected speak commands (lines 942-945)
4. **Peer stamping**: sets `context["peer"] = context["source"] = client.peer` (line 949); this is the peer ID that `Message.reply()` will later place into `destination`
5. **Bus emit**: `bus.emit(message)` injects the message into the OVOS bus (line 953)
6. **Agent bus callback**: if `agent_bus_callback` is set (live HiveMind only), calls it (lines 955-956)

After step 5 the message is in OVOS territory. HiveMind's job is done until a reply comes back
through `handle_internal_mycroft()`.

---
[← E2E Skill Tests — Real OVOS Skills Through HiveMind](06-e2e-skill-tests.md) · [Home](index.md) · [API Reference: hivemind-test-harness →](api.md)
