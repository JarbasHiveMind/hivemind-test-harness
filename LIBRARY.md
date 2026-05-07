# LIBRARY.md — Moved to hivescope

The in-process test library (TopologyBuilder, MasterNode, SatelliteNode, RelayNode,
TestAgentProtocol, TestNetworkProtocol, TestBinaryProtocol, MessageRecorder, fixtures,
assertions, preset scenarios) moved out of this repo on 2026-05-07.

The library now lives at: https://github.com/JarbasHiveMind/hivescope

Install:

```bash
pip install "hivescope @ git+https://github.com/JarbasHiveMind/hivescope@dev"
```

This repo (`hivemind-test-harness`) is now a test-only consumer of hivescope.
It owns topology, stress, cross-repo, and skill e2e tests — not library code.

See [README.md](README.md) for the current role of this repo.
