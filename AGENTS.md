# AGENTS.md — hivemind-test-harness

Central HiveMind integration test suite (topology, stress, cross-repo, skill e2e). Test-only consumer of [hivescope](https://github.com/JarbasHiveMind/hivescope) — ships no library code.

## Setup

```bash
pip install "hivescope @ git+https://github.com/JarbasHiveMind/hivescope@dev"
pip install -e .[dev]
```

Skill e2e tests need the OVOS extra:

```bash
pip install -e .[ovos]    # ovoscope, ovos-core
```

Topology plot tests need the plot extra (`matplotlib`, `networkx`).

## Test

```bash
# Fast run (skips slow large-topology tests)
pytest tests/ -v --timeout=60 -m "not slow"

# Include slow/stress
pytest tests/ -v --timeout=120

# Single suite
pytest tests/topology/ -v
pytest tests/cross_repo/ -v
pytest tests/skills_e2e/ -v
```

Default per-test timeout is 30s (`tool.pytest.ini_options`). The `slow` marker tags tests that spin up large topologies (T4/T5/T6).

## Lint/Typecheck

Ruff via CI (`OpenVoiceOS/gh-automations/.github/workflows/lint.yml@dev`, `ruff: true`, `pre_commit: false`). No local lint config committed; no typecheck configured.

## Layout

- `tests/topology/` — multi-relay chains, deep nesting, ACL across relay boundaries, routing correctness.
- `tests/stress/` — high-cardinality fan-out, sustained load, protocol limits (mostly `slow`).
- `tests/cross_repo/` — embedded/micropython clients, JS e2e, audio transformers, interop.
- `tests/skills_e2e/` — real OVOS skills through HiveMind via `OvoscopeAgentProtocol` (MiniCroft): converse, get_response, session, stop, PHAL, scheduler, OCP, ACL, lang.
- `tests/_pending_migration/` — single-repo protocol tests awaiting move to their owning repos (mostly HiveMind-core; one to hivemind-websocket-client). See its `README.md` for the table.
- `tests/conftest.py` — all topology fixtures (T1–T6: minimal, star, admin_star, chain, deep_chain, huge_hive, chaotic_hive, asymmetric_hive) plus shared skill IDs and e2e helpers. Topologies are built with `hivescope.topology.TopologyBuilder`.
- `test_helpers/js_e2e_driver.mjs` — Node driver for cross-language JS e2e.

No package/entry points — this is not a plugin/skill; nothing is installed into an entry-point group.

## Conventions

- Branches: `dev` (work), `master` (stable). NEVER `main`.
- Never edit `version.py` or bump versions by hand; gh-automations bumps semver from conventional-commit prefixes (`feat:`/`fix:`/`feat!:`).
- New repos private by default.
- Commit identity: JarbasAi <jarbasai@mailfence.com>.
- Reference `OpenVoiceOS/gh-automations` reusable workflows at `@dev`.
- No Neon / `neon-*` references.
- No meta-commentary (no history, no dates, no "design mistake" narration) in code, docs, commits, or PRs.
- CI is provided by OpenVoiceOS/gh-automations.

## Gotchas

- The simulator API (TopologyBuilder, MasterNode, SatelliteNode, RelayNode, fixtures, assertions, presets) lives in hivescope, not here — API reference belongs there. This repo only consumes it.
- `relay` = dual-role node (a satellite upstream AND a master accepting downstream satellites, sharing one agent bus). `TopologyBuilder.add_relay` returns `(relay, relay_master)`; attach downstream satellites to `relay_master`.
- `_pending_migration/` tests still run under pytest so coverage is not lost; the migration is about ownership, not behaviour.
- hivescope is git-only (not on PyPI); the license-check workflow excludes `hivemind-core`, `hivemind-test-harness`, and `hivescope` because they do not resolve on PyPI.
- Skill e2e tests skip automatically when required OVOS skills are not installed (`skill_missing()` in conftest).
