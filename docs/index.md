# HiveMind Test Harness

The HiveMind Test Harness is a library for simulating complex HiveMind network topologies in-process, without requiring actual network sockets or external AI backends. it is used for protocol verification, integration testing, and performance benchmarking.

## Documentation Guides

- [Topology Builder](topology.md) - Creating and wiring complex network setups.
- [Node Implementation](nodes.md) - Understanding Master and Satellite nodes.
- [Message Routing & Session Flow](07-message-routing.md) - How context keys propagate end-to-end, how `session_id` flows through HiveMind and OVOS, and how `TestAgentProtocol` achieves production parity.

## Overview

The harness provides "mock" implementations of network, agent, and database protocols. It allows you to programmatically define a hierarchy of Minds and Satellites, inject messages, and record all traffic for assertions.

## Features

- **In-Process Communication**: Uses shims to bypass the WebSocket layer, making tests fast and deterministic.
- **Topology DSL**: Easily define Master-Slave-Satellite relationships.
- **Message Recording**: Every `HiveMessage` sent or received can be captured by a `MessageRecorder`.
- **Mock Plugins**: Includes `TestAgentProtocol`, `TestNetworkProtocol`, and `InMemoryClientDatabase`.

## Installation

```bash
pip install -e hivemind-test-harness/
```
