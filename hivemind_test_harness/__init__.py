"""HiveMind Test Harness.

This repo owns cross-repo integration tests (topology, stress, skills e2e).
The in-process simulator — TopologyBuilder, MasterNode, SatelliteNode, RelayNode,
MessageRecorder, the protocol plugins, fixtures, assertions and scenarios — lives
in ``hivescope``. It is re-exported here for backwards compatibility; new code
should import from ``hivescope`` directly.
"""
from hivescope.topology import TopologyBuilder, RelayNode
from hivescope.node import MasterNode, SatelliteNode

__all__ = ["TopologyBuilder", "RelayNode", "MasterNode", "SatelliteNode"]
