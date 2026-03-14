
# HiveMind Protocol Audit

Full analysis of the HiveMind protocol as implemented across:
- `hivemind-core/hivemind_core/protocol.py` — master-side handler
- `hivemind-websocket-client/hivemind_bus_client/protocol.py` — satellite-side handler
- `hivemind-websocket-client/hivemind_bus_client/message.py` — message types and serialization

Audit date: 2026-03-07. Test harness: 72/72 tests passing.

---

## Summary

| Severity | Count | Notes |
|---|---|---|
| Critical | 3 | Password auth broken, INTERCOM signature disabled, INTERCOM dispatch never fires |
| High | 7 | Unencrypted messages accepted, binary ACL bypass, session spoofing, illegal clients not kicked |
| Medium | 6 | File path traversal, route loops, missing error handling, DB write per message |
| Design gaps | 9 | 4 unimplemented message types, relay gap, PROPAGATE injection disabled, pub_key not persisted, NodeType unused |

---

## Critical Issues

### CRIT-1: Password authentication is not validated

**File:** `hivemind-core/hivemind_core/protocol.py` line 516–522

In `handle_handshake_message()`, the password PAKE flow calls `receive_handshake(envelope)` but
the verification call is commented out:

```python
# if not client.pswd_handshake.receive_and_verify(envelope):
#     self.handle_invalid_key_connected(client)
#     client.disconnect()
#     return
```

The result: a client with the wrong password derives a different `crypto_key` than the server, but
the handshake completes without rejection. The connection only breaks silently on the first
real message when decryption fails with a wrong key. There is no explicit rejection or audit log
at handshake time — the protocol just derives a mismatched key and continues.

**Impact:** An attacker can connect without knowing the password. If they can observe or guess
the expected message format, they can brute-force offline.

**Fix:** Restore `receive_and_verify()`. PAKE is designed to detect mismatch; the check just needs
to be un-commented and the failure path wired to `handle_invalid_key_connected()`.

---

### CRIT-2: INTERCOM RSA signature verification disabled

**Files:**
- `hivemind-core/hivemind_core/protocol.py` line 755
- `hivemind-websocket-client/hivemind_bus_client/protocol.py` line 290

Both master and slave `handle_intercom_message()` include:

```python
# TODO - allow verifying, we need to store trusted pubkeys before this can be done
# pub = ""
# verified = verify_RSA(pub, ciphertext, signature)
```

The `signature` field is decoded from the INTERCOM payload but never checked. Any node that
can reach the master can send an RSA-encrypted INTERCOM message claiming to be from any peer.
The sender's identity is unverifiable.

**Impact:** INTERCOM is designed as authenticated peer-to-peer messaging. Without signature
verification it provides confidentiality (the ciphertext can only be read by the target) but
no authentication (the sender is not proven to be who they say).

**Fix:** Store received public keys (from HELLO) in the database. Verify INTERCOM signature
against the stored pubkey of the sending peer. Until pubkeys are persisted (see DESIGN-2),
this cannot be fully implemented.

---

### CRIT-3: INTERCOM post-decrypt dispatch never fires

**Files:**
- `hivemind-core/hivemind_core/protocol.py` lines 762–789
- `hivemind-websocket-client/hivemind_bus_client/protocol.py` lines 298–315

After RSA decryption, the inner HiveMessage is stored in `message._payload`:

```python
message._payload = HiveMessage.deserialize(decrypted)
```

But the dispatch that follows checks `message.msg_type`, which is still `INTERCOM` (the outer
type). The inner type is in `message.payload.msg_type`. As written:

```python
if message.msg_type == HiveMessageType.BUS:   # always False — outer type is INTERCOM
    self.handle_bus_message(message, client)
    return True
# ... all other checks also fail ...
return False  # always reached
```

**Impact:** RSA-encrypted INTERCOM messages are decrypted but the inner message is never
dispatched. Sending `INTERCOM(encrypted BUS)` appears to work (no crash) but the BUS message
is silently dropped.

**Fix:** Dispatch on `message.payload.msg_type` (the inner type after decryption):

```python
inner = message.payload  # HiveMessage after _payload was set
if inner.msg_type == HiveMessageType.BUS:
    self.handle_bus_message(inner, client)
    return True
```

Test: `test_intercom.py::TestIntercomRSA::test_intercom_targeting_master_does_not_crash`
currently only verifies no crash — not that the inner BUS is actually delivered. A behavioral
test for post-decrypt delivery would catch this regression.

---

## High-Severity Issues

### HIGH-1: Unencrypted messages accepted after handshake

**File:** `hivemind-core/hivemind_core/protocol.py` lines 173–176

```python
else:
    LOG.warning("Message was unencrypted")
    # TODO - some error if crypto is required
```

```python
else:
    pass  # TODO - reject anything except HELLO and HANDSHAKE
```

When `require_crypto=True` (the default), messages that arrive unencrypted after the handshake
should be rejected. Currently they are accepted with a log warning.

**Impact:** A passive attacker who strips encryption can inject arbitrary messages. A client
with a wrong key that fails decryption would be silently accepted if the transport allows
plain text.

---

### HIGH-2: Binary messages bypass all ACL

**File:** `hivemind-core/hivemind_core/protocol.py` lines 438–471

`handle_binary_message()` routes directly to the binary data protocol handlers with no
authorization checks. Any authenticated client (regardless of `allowed_types`, `can_escalate`,
`is_admin`) can upload arbitrary audio, files, or images.

```python
def handle_binary_message(self, message, client):
    # no client.authorize() call
    if message.bin_type == HiveMindBinaryPayloadType.FILE:
        file_name = message.metadata.get("file_name")
        self.binary_data_protocol.handle_receive_file(bin_data, file_name, client)
```

**Impact:** Any connected node can send arbitrary binary data. The `file_name` from metadata
is passed directly with no path sanitization.

---

### HIGH-3: Illegal action clients are not disconnected

**File:** `hivemind-core/hivemind_core/protocol.py` lines 606, 656, 710

Non-admin BROADCAST, `can_propagate=False` PROPAGATE, and `can_escalate=False` ESCALATE all
share the same pattern:

```python
LOG.warning("Received broadcast message from downstream, illegal action")
if self.illegal_callback:
    self.illegal_callback(payload)
# TODO kick client for misbehaviour so it stops doing that?
return
```

The client remains connected after a protocol violation and can repeat the illegal action
indefinitely.

**Contrast:** `handle_bus_message()` calls `client.disconnect()` for a default-session
violation. Broadcast/propagate/escalate violations are treated more leniently with no
consistency rationale.

---

### HIGH-4: No session isolation between clients

**File:** `hivemind-core/hivemind_core/protocol.py` lines 580–588

`handle_bus_message()` accepts and updates `client.sess` if the session ID in the message
matches the client's existing session. But there is no check that the session ID belongs
to this client and no one else. A client that knows another client's session ID could send
a BUS message with that session ID and — if it passes the "default" check — potentially
update that session or inject messages under another client's session.

---

### HIGH-5: INTERCOM with no target key is accepted and processed

**File:** `hivemind-core/hivemind_core/protocol.py` line 744–747

```python
k = message.target_public_key
if k and k != self.identity.public_key:
    return False  # not for us
```

When `target_public_key` is absent (`k` is None/empty), the condition `if k` is False, so
the check is skipped and processing continues. An unencrypted INTERCOM with no target key
is treated as "for us". Combined with CRIT-3, this means the decrypted inner message
would be dispatched even without any encryption or target specification.

---

### HIGH-6: Pre-shared key path is skipped

**Files:**
- `hivemind-core/hivemind_core/protocol.py` (implicit — no explicit pre-shared key flow)
- `hivemind-websocket-client/hivemind_bus_client/protocol.py` lines 204–207

```python
if message.payload.get("crypto_key") and self.hm.crypto_key:
    pass
    # we can use the pre-shared key instead of handshake
    # TODO - flag to give preference to pre-shared key over handshake
```

Pre-shared keys are detected but ignored. The `HiveMindClientConnection.crypto_key` field
is populated from the database but is never wired into the satellite-side `handle_handshake`
path. Pre-shared key authentication is non-functional.

---

### HIGH-7: RSA handshake not triggered on satellite for pubkey-only masters

**File:** `hivemind-websocket-client/hivemind_bus_client/protocol.py` lines 210–214

The satellite's `handle_handshake` only calls `start_handshake()` (which sends a PAKE
envelope) when the server reports `password=True`. If the master uses RSA-only mode
(no password), the satellite never initiates a handshake — the connection stalls.

The RSA handshake flow requires the satellite to call `start_handshake()` which sends its
public key. Without this, the master waits indefinitely for a handshake response.

---

## Medium-Severity Issues

### MED-1: File names from metadata not sanitized

**File:** `hivemind-core/hivemind_core/protocol.py` lines 463–464

```python
file_name = message.metadata.get("file_name")
self.binary_data_protocol.handle_receive_file(bin_data, file_name, client)
```

The `file_name` is from untrusted client metadata. If the binary data protocol
implementation writes to disk using this path, path traversal is possible
(e.g., `../../etc/cron.d/evil`).

---

### MED-2: Route loops not detected

**File:** `hivemind-core/hivemind_core/protocol.py` line 399

```python
message.update_hop_data()
```

Route is updated on every hop. There is no check for cycles (the same peer appearing
multiple times in the route) or for excessive depth. A circular relay configuration
would allow a message to bounce indefinitely.

---

### MED-3: DB write on every message

**File:** `hivemind-core/hivemind_core/protocol.py` lines 304–310

`update_last_seen()` is called on every processed message (line 426). It opens a DB
context, fetches the client, updates `last_seen`, and writes back. Under load, this
causes one DB write per message per client.

---

### MED-4: `update_last_seen()` will crash if client key not in DB

**File:** `hivemind-core/hivemind_core/protocol.py` lines 307–309

```python
user = self.db.get_client_by_api_key(client.key)
user.last_seen = time.time()  # AttributeError if user is None
```

If the client key was deleted from the database while the client is connected, this
crashes with `AttributeError: 'NoneType' object has no attribute 'last_seen'`.

---

### MED-5: Bare `except:` catches KeyboardInterrupt

**Files:** Multiple locations in both protocol files

```python
except:
    if k:
        LOG.error("failed to decrypt message!")
    ...
    return False
```

Bare `except:` catches `BaseException`, including `KeyboardInterrupt` and `SystemExit`.
These should be `except Exception:`.

---

### MED-6: DB blacklist sync on every message

**File:** `hivemind-core/hivemind_core/protocol.py` lines 797–801

`_update_blacklist()` is called inside `handle_inject_agent_msg()`, which calls
`self.db.sync()` on every BUS message. This re-reads the blacklist from the database
on every message injection to catch runtime changes, but it blocks and adds DB latency
to every injection.

---

## Design Gaps

### DESIGN-1: Four message types are unimplemented stubs

All four fall to `handle_unknown_message()`, an empty stub:

| Type | Defined intent | Status |
|---|---|---|
| QUERY | Like ESCALATE but stops at first node that can respond | Empty stub |
| CASCADE | Like PROPAGATE but gathers responses from all nodes | Empty stub |
| **PING** | **Network topology/latency discovery** | **Design complete — implementation pending** |
| **PONG** | **Discovery reply to PING** | **Design complete — implementation pending** |
| RENDEZVOUS | Reserved for peer discovery / NAT hole-punching | Empty stub |

**QUERY** would require: a response message type (or repurposing BUS), timeout, routing
state to track which node responded, and a way to prevent other nodes from also responding.

**CASCADE** would require: correlation ID, timeout, response aggregation, response message
type, and a way to mark "I responded" in the route.

**PING / PONG — design is now complete** (2026-03-09). See:
- `HiveMind-community-docs/docs/20_network_discovery.md` — full wire format and flow spec
- `hivemind-core/docs/hive_map.md` — `HiveMapper` class specification
- `hivemind-core/docs/protocol.md` — `handle_ping_message()` / `handle_pong_message()` spec

Implementation checklist:
- [ ] Add `PONG = "pong"` to `HiveMessageType` enum in `hivemind_bus_client/message.py`
- [ ] Add `handle_ping_message()` in `hivemind_core/protocol.py` (relay sends PONG + relays PING; master MAY drop by policy)
- [ ] Add `handle_pong_message()` in `hivemind_core/protocol.py` (feed HiveMapper + relay)
- [ ] Add PING sender / PONG responder in `hivemind_bus_client/protocol.py` (satellite side)
- [ ] Implement `HiveMapper` in `hivemind_core/hive_map.py`
- [ ] Add `hivemind-client ping` CLI command in `hivemind_bus_client/scripts/`
- [ ] Replace `test_unimplemented_types.py` PING/PONG stubs with behavioral tests

**RENDEZVOUS** is entirely unspecified in the codebase.

Until QUERY/CASCADE/RENDEZVOUS are implemented, test stubs remain in `test_unimplemented_types.py`.
Replace PING/PONG stubs with behavioral tests once the handlers above are added.

---

### DESIGN-2: Client public key not stored in database

**File:** `hivemind-core/hivemind_core/protocol.py` line 83

```python
pub_key: Optional[str] = None  # TODO add field to database
```

The client's RSA public key (received in HELLO, line 557) is stored in
`HiveMindClientConnection.pub_key` but never written to the database. On reconnect,
the pubkey is lost. This blocks:

- INTERCOM signature verification (CRIT-2)
- Future features that depend on knowing a peer's public key
- Ability to address a peer by pubkey without it being connected

**Fix:** Add `pub_key` column to the Client dataclass and database schema. Persist on
`handle_hello_message()`.

---

### DESIGN-3: BROADCAST does not propagate downstream through relay chains

**Files:**
- `hivemind-websocket-client/hivemind_bus_client/protocol.py` lines 246–250
- `hivemind-core/hivemind_core/protocol.py` lines 626–629 (master-side fan-out)

When a satellite in a relay chain receives a BROADCAST, it emits `hive.send.downstream`
on its internal bus. The `HiveMindListenerInternalProtocol` (in `ovos-bus-client/hpm.py`)
listens for `hive.send.downstream` and forwards the message downstream to the master's
connected satellites.

However, in the test harness and presumably in production relay setups, the relay master's
`HiveMindListenerInternalProtocol` is not wired by default. The relay master's agent bus
is a `FakeBus` that no component listens on for `hive.send.downstream`.

**Result:** In a topology M0→R1(relay)→S0, a BROADCAST from M0 reaches R1's satellite side
but is not forwarded to S0.

**This is a topology wiring issue, not a protocol design issue.** The protocol supports
downstream BROADCAST — the relay implementation just needs to register the handler.

Documented in `docs/02-protocol-coverage.md` as a known gap.

---

### DESIGN-4: PROPAGATE site-targeted BUS injection is disabled

**File:** `hivemind-websocket-client/hivemind_bus_client/protocol.py` lines 262–267

```python
if message.payload.msg_type == HiveMessageType.BUS:
    site = message.target_site_id
    if site and site == self.site_id:
        # might originate from untrusted satellite anywhere in the hive
        # do not inject by default
        pass  # TODO - when to inject ? add list of trusted peers?
        # self.handle_bus(message.payload)
```

Site-targeted BUS injection works for BROADCAST (only admin satellites can broadcast)
but is intentionally disabled for PROPAGATE because any satellite can PROPAGATE.
The trust model for propagate-targeted injection is unresolved.

**Current behavior:** `target_site_id` on a PROPAGATE is silently ignored for bus
injection. The PROPAGATE message is still forwarded to all peers.

---

### DESIGN-5: `HiveMindNodeType` is defined but never assigned

**File:** `hivemind-core/hivemind_core/protocol.py` line 78

```python
node_type: HiveMindNodeType = HiveMindNodeType.CANDIDATE_NODE
```

The `node_type` field on `HiveMindClientConnection` always stays as `CANDIDATE_NODE`.
No handler promotes it to `NODE`, `SLAVE`, `TERMINAL`, etc. The seven node types are
defined but unused in any routing or authorization logic.

---

### DESIGN-6: NUMPY_IMAGE bytes not converted to numpy array

**File:** `hivemind-core/hivemind_core/protocol.py` line 466

```python
# TODO - convert to numpy array
camera_id = message.metadata.get("camera_id")
self.binary_data_protocol.handle_numpy_image(bin_data, camera_id, client)
```

The handler receives raw bytes. Implementations of `BinaryDataHandlerProtocol` would
need to call `numpy.frombuffer()` themselves, without a specified dtype or shape. The
metadata field spec (how to communicate shape and dtype) is not defined.

---

### DESIGN-7: RAW_AUDIO format is unspecified

**File:** `hivemind-websocket-client/hivemind_bus_client/message.py` line 36

```python
RAW_AUDIO = 1  # binary content is raw audio  (TODO spec exactly what "raw audio" means)
```

The metadata fields `sample_rate` and `sample_width` are used in `handle_binary_message`
(master protocol line 444–445), but `channels`, `encoding` (PCM/float), and `endianness`
are not specified. Interoperability between satellite implementations is not guaranteed.

---

### DESIGN-8: HELLO pubkey rotation is ignored after first connection

**File:** `hivemind-websocket-client/hivemind_bus_client/protocol.py` lines 130–138

```python
def handle_hello(self, message: HiveMessage):
    if not self.node_id:  # only on first HELLO
        self.mpubkey = message.payload.get("pubkey")
        node_id = message.payload.get("node_id", "")
        self.internal_protocol.node_id = node_id
```

Subsequent HELLO messages (e.g., after key rotation) are silently ignored on the
satellite side. The master may rotate its RSA key (e.g., after identity refresh) and
send a new HELLO, but the satellite keeps the old pubkey.

---

### DESIGN-9: `HiveMindSlaveInternalProtocol.handle_send` silently drops BROADCAST

**File:** `hivemind-websocket-client/hivemind_bus_client/protocol.py` lines 44–51

```python
def handle_send(self, message: Message):
    msg_type = message.data["msg_type"]
    hmessage = HiveMessage(msg_type, payload=payload)
    if msg_type == HiveMessageType.BROADCAST:
        # only masters can broadcast, ignore silently
        pass
    else:
        self.hm_bus.emit(hmessage)
```

When a relay device's internal OVOS bus emits `hive.send.upstream` with `msg_type=BROADCAST`,
it is silently discarded. This prevents a device acting as both master and satellite from
using `HiveMindListenerInternalProtocol` to forward BROADCAST upstream. The comment says
"only masters can broadcast" but this blocks legitimate relay-master → upstream-master
broadcast forwarding.

---

## Handshake Variant Coverage

| Mode | Master supports | Satellite supports | Test coverage |
|---|---|---|---|
| Password PAKE | Yes | Yes (if server reports `password=True`) | Full — all 72 tests use this |
| RSA pubkey exchange | Yes (server side) | Partial (never auto-initiated) | Not tested (HIGH-7) |
| Pre-shared key | Detected, skipped | Detected, skipped | Not tested (HIGH-6) |
| No crypto | Accepted with warning | N/A | Not tested |

---

## ACL and Authorization Matrix

| Action | Check | Enforced | Kick on violation |
|---|---|---|---|
| BUS injection — type | `allowed_types` | Yes | No (silent drop) |
| BUS injection — intent/skill | TODO | No | No |
| BUS injection — session "default" | `is_admin` | Yes | **Yes (disconnect)** |
| BROADCAST | `is_admin` | Yes | No (callback only) |
| PROPAGATE | `can_propagate` | Yes | No (callback only) |
| ESCALATE | `can_escalate` | Yes | No (callback only) |
| BINARY upload | None | No | No |
| INTERCOM target | `target_public_key` match | Partial | No |
| SHARED_BUS receive | None | No | No |

---

## Message Type Handler Coverage

| Type | Master handler | Satellite handler | Notes |
|---|---|---|---|
| HANDSHAKE | Full — PAKE + RSA | Partial — PAKE only auto-triggers | See HIGH-7 |
| HELLO | Full | Full (first connection only) | See DESIGN-8 |
| BUS | Full | Full | ACL gaps — see HIGH-2 area |
| SHARED_BUS | Callback-based | Fires on every internal bus event if `share_bus=True` | |
| BROADCAST | Full with admin check | Full — forwards downstream | Relay gap — see DESIGN-3 |
| PROPAGATE | Full with can_propagate check | Full — forwards downstream | Injection disabled — DESIGN-4 |
| ESCALATE | Full with can_escalate check | Registered as illegal (server→client direction) | |
| INTERCOM | Decrypt + dispatch (broken) | Decrypt + dispatch (broken) | See CRIT-3 |
| BINARY | All 7 subtypes dispatched | Not received by satellite | ACL gap — see HIGH-2 |
| QUERY | Empty stub | Not received | Not implemented |
| CASCADE | Empty stub | Not received | Not implemented |
| PING | Empty stub | Not received | Not implemented |
| RENDEZVOUS | Empty stub | Not received | Not implemented |
| THIRDPRTY | Empty stub (top-level) | Not received (top-level) | Works as inner payload |

---

## Recommended Fix Priority

### Immediate (correctness/security)
1. **CRIT-1**: Restore password `receive_and_verify()` — one line uncomment + wiring
2. **CRIT-3**: Fix INTERCOM dispatch to check inner type, not outer type — 6-line change
3. **HIGH-1**: Reject unencrypted messages when `require_crypto=True`
4. **HIGH-3**: Disconnect clients that send illegal BROADCAST/PROPAGATE/ESCALATE
5. **MED-4**: Guard `update_last_seen()` against None client

### Short-term (design completeness)
6. **CRIT-2 / DESIGN-2**: Add `pub_key` to database, persist on HELLO; enables signature verification
7. **HIGH-2**: Add ACL check before binary message routing
8. **MED-1**: Sanitize `file_name` from metadata before passing to handler
9. **HIGH-6/7**: Wire pre-shared key path; fix RSA-only satellite handshake
10. **MED-2**: Add route depth limit and cycle detection

### Long-term (features)
11. **DESIGN-1**: Implement QUERY, CASCADE, PING (RENDEZVOUS awaits external design)
12. **DESIGN-3**: Wire `hive.send.downstream` in relay master to forward BROADCAST
13. **DESIGN-4**: Define trust policy for PROPAGATE site-targeted injection
14. **DESIGN-5**: Use `HiveMindNodeType` in routing and authorization decisions
15. **DESIGN-6/7**: Specify numpy image and raw audio metadata schemas

---

## Test Harness Regression Tests

Tests added during coverage review that document current behavior (some capture bugs as
"no crash" stubs pending implementation):

| Test | Documents | Should be upgraded when |
|---|---|---|
| `test_intercom.py::TestIntercomRSA` | No crash on decrypt | CRIT-3 fixed — add delivery assertion |
| `test_unimplemented_types.py` | No crash on QUERY/CASCADE/PING/RENDEZVOUS/THIRDPRTY | Handlers implemented |
| `test_broadcast.py::TestBroadcastTargetSiteId` | Site-targeted BUS injection | Behavioral correctness confirmed |
| `test_routing.py::TestDeepChainEscalate` | Two-relay ESCALATE routing | Relay gap (DESIGN-3) fixed |
