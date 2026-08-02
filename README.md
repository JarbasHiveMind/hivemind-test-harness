[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/JarbasHiveMind/hivemind-test-harness)

# HiveMind Test Harness

> Central HiveMind integration test suite for topology, stress, and cross-repo scenarios.

This repo owns tests that span multiple HiveMind repositories, complex network topologies,
sustained-load scenarios, and real OVOS skill execution through HiveMind.
Single-repo protocol tests belong in the owning repo's `tests/e2e/` using
[hivescope](https://github.com/JarbasHiveMind/hivescope) directly.

The in-process simulator (TopologyBuilder, MasterNode, SatelliteNode, RelayNode, fixtures,
assertions, preset scenarios) lives in hivescope.

---

## What Lives Here

The `tests/` directory covers:

- **Protocol mechanics**: handshake, ACL, routing, BUS/BROADCAST/PROPAGATE/ESCALATE/
  SHARED_BUS/PING, QUERY/CASCADE, INTERCOM, BINARY, unimplemented-type handling
- **Topology tests**: multi-relay chains, nested hubs (`test_all_topologies.py`,
  `test_routing.py`, relay ACL and skill tests)
- **Stress**: concurrent connections, large satellite fan-out (marked `@pytest.mark.slow`)
- **Cross-repo interop**: embedded clients (`test_embedded_*.py`), JavaScript e2e
  (`test_js_e2e.py`), MicroPython (`test_micropython_e2e.py`), audio transformers
  (`test_audio_transformers.py`)
- **Skills e2e**: real OVOS skill execution through HiveMind using
  `OvoscopeAgentProtocol` (live MiniCroft): `test_e2e_skills.py`,
  `test_e2e_converse.py`, `test_e2e_get_response.py`, `test_e2e_session.py`,
  `test_e2e_ocp.py`, `test_e2e_relay_skills.py`, and more

---

## Dependency: hivescope

All test infrastructure (`TopologyBuilder`, `MasterNode`, `SatelliteNode`, `RelayNode`,
`MessageRecorder`, fixtures, assertion helpers, and preset scenarios) is provided by
[hivescope](https://github.com/JarbasHiveMind/hivescope). API reference for those classes
belongs in hivescope's documentation.

---

## Install

```bash
pip install "hivescope @ git+https://github.com/JarbasHiveMind/hivescope@dev"
pip install -e ".[dev]"
```

For skill e2e tests (requires a live OVOS installation):

```bash
pip install -e ".[dev,ovos]"
```

---

## Quick Example

```python
from hivescope.scenarios import single_satellite
from hivescope.assertions import assert_handshake_complete

def test_handshake():
    builder = single_satellite()
    builder.start_all()
    try:
        assert_handshake_complete(
            builder.get_master("M0"),
            builder.get_satellite("S0"),
        )
    finally:
        builder.stop_all()
```

For a topology test with a relay chain:

```python
from hivemind_bus_client.message import HiveMessage, HiveMessageType
from ovos_bus_client.message import Message

def test_escalate_reaches_top_master(chain_topology):
    b = chain_topology          # M0 → R1(relay) → S0
    s0 = b.get_satellite("S0")
    m0 = b.get_master("M0")

    s0.send(HiveMessage(HiveMessageType.ESCALATE,
                        payload=HiveMessage(HiveMessageType.BUS,
                                            payload=Message("some.event", {}))))
    m0.recorder.assert_received(HiveMessageType.ESCALATE)
    s0.recorder.assert_not_received(HiveMessageType.ESCALATE, direction="in")
```

---

## Running Tests

```bash
# Standard run: excludes slow/stress
pytest tests/ -v --timeout=60 -m "not slow"

# Include stress and large-topology tests
pytest tests/ -v --timeout=120

# Protocol mechanics only
pytest tests/test_handshake.py tests/test_acl.py tests/test_routing.py -v

# Skill e2e (requires live OVOS)
pytest tests/test_e2e_skills.py -v
```

The JavaScript e2e test (`test_js_e2e.py`) requires Node.js and uses
`test_helpers/js_e2e_driver.mjs`.

---

## When to Add a Test Here vs in the Owning Repo

| Scenario | Location |
|---|---|
| Tests a single repo's message handling | That repo's `tests/e2e/` using hivescope directly |
| Requires >1 relay hop or a complex topology | This repo's `tests/` |
| Requires a real OVOS skill to run | `tests/test_e2e_*.py` here |
| Cross-language or embedded client interop | `tests/test_embedded_*.py` / `test_js_e2e.py` etc. |
| Sustained load (50+ satellites) | Marked `@pytest.mark.slow`, lives here |

---

## Documentation

| Document | Purpose |
|---|---|
| [docs/index.md](docs/index.md) | This harness's doc index and navigation |
| [docs/03-topologies.md](docs/03-topologies.md) | Topology catalogue referenced by topology tests |
| [docs/04-test-scenarios.md](docs/04-test-scenarios.md) | Scenario catalogue |
| [docs/06-e2e-skill-tests.md](docs/06-e2e-skill-tests.md) | Skill e2e coverage details |
| [docs/07-message-routing.md](docs/07-message-routing.md) | Context keys and session_id lifecycle |

---

## License

Apache-2.0
