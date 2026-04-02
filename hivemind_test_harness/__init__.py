"""
Hivescope: E2E Testing Library for HiveMind

A reusable pytest-based framework for writing end-to-end tests of HiveMind
protocol implementations. Provides stable APIs for topology simulation,
message routing verification, and protocol-level assertions.

Public API — stable across versions:
  - TopologyBuilder: Builder for test network topologies
  - MasterNode, SatelliteNode, RelayNode: Network node types
  - TestAgentProtocol: Agent protocol backed by FakeBus (fast, deterministic)
  - OvoscopeAgentProtocol: Agent protocol backed by MiniCroft (realistic, slow)
  - TestBinaryProtocol: Binary data handler stub for testing
  - TestNetworkProtocol: Network protocol stub for in-process testing
  - MessageRecorder, RecordedMessage: Message recording and inspection
  - InMemoryClientDatabase: In-memory credential store for testing

Fixtures & Helpers (use via conftest.py):
  - pytest fixtures: topology, master_node, satellite_node, etc.
  - Assertion helpers: assert_handshake_complete(), assert_message_routed(), etc.
  - Preset topologies: single_satellite(), three_satellites(), with_relay(), etc.

Example:
  from hivescope import TopologyBuilder
  from hivescope.scenarios import single_satellite

  # Build and run a simple test topology
  b = TopologyBuilder()
  m = b.add_master("M0")
  m.register_satellite("test-key", password="test-password")
  b.start_all()
  ...
  b.stop_all()
"""

from hivemind_test_harness.topology import TopologyBuilder, RelayNode
from hivemind_test_harness.node import MasterNode, SatelliteNode
from hivemind_test_harness.recorder import MessageRecorder, RecordedMessage
from hivemind_test_harness.database import InMemoryClientDatabase
from hivemind_test_harness.plugins.agent import TestAgentProtocol
from hivemind_test_harness.plugins.binary import TestBinaryProtocol
from hivemind_test_harness.plugins.network import TestNetworkProtocol

# Optional: OvoscopeAgentProtocol requires ovoscope+ovos-core
try:
    from hivemind_test_harness.plugins.ovoscope_agent import OvoscopeAgentProtocol
except ImportError:
    OvoscopeAgentProtocol = None

__all__ = [
    # Core topology classes (stable)
    "TopologyBuilder",
    "MasterNode",
    "SatelliteNode",
    "RelayNode",
    # Protocol plugins (stable)
    "TestAgentProtocol",
    "TestBinaryProtocol",
    "TestNetworkProtocol",
    "OvoscopeAgentProtocol",
    # Message recording (stable)
    "MessageRecorder",
    "RecordedMessage",
    # Database (stable)
    "InMemoryClientDatabase",
]
