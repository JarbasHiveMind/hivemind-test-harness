# Contributing

## Setup

```bash
uv venv
uv pip install --prerelease=allow -e ".[dev]"
```

`--prerelease=allow` is required: this harness floors specific prerelease
versions of the HiveMind stack (see the comments next to each dependency in
`pyproject.toml`) to pick up fixes that have not had a stable release yet.

For the OVOS skill-execution e2e tests:

```bash
uv pip install --prerelease=allow -e ".[dev,ovos]"
```

## Running tests

```bash
# Fast, in-process suite (what PR CI runs)
pytest tests/ -m "not slow" \
  --ignore-glob='tests/test_e2e_*.py' \
  --ignore-glob='tests/test_embedded_*.py'

# Large-topology stress tests only (what nightly CI runs); needs the plot
# extra too — tests/test_topology_plots.py imports hivescope.topology_plot
uv pip install --prerelease=allow -e ".[dev,plot]"
pytest tests/ -m slow
```

Tests that spin up large topologies are marked `@pytest.mark.slow` and are
deselected from PR CI to keep it fast; they run on the nightly schedule in
`.github/workflows/nightly-slow.yml` instead. Mark a new stress/large-topology
test `slow` if it takes noticeably longer than the rest of the suite.

## Scope

This repo owns tests that span multiple HiveMind repositories, complex
network topologies, sustained-load scenarios, and real OVOS skill execution
through HiveMind — see `README.md#what-lives-here`. The in-process simulator
(`TopologyBuilder`, `MasterNode`, `SatelliteNode`, `RelayNode`, fixtures,
assertions, preset scenarios) lives in
[hivescope](https://github.com/JarbasHiveMind/hivescope), not here; changes to
that layer belong in hivescope's own PR.

## Pull requests

- Target the `dev` branch.
- Use [Conventional Commits](https://www.conventionalcommits.org/) for commit
  messages (`fix:`, `feat:`, `chore:`, `docs:`, `test:`, ...).
- Open PRs as drafts until CI is green and the change is ready for review.
- Keep test-logic changes and packaging/CI/docs changes in separate PRs where
  practical — it makes both easier to review.

## Code of conduct

Be respectful and constructive. Issues and PRs that are abusive or
off-topic will be closed.
