"""
Pytest fixtures for hivescope e2e tests.

Standard fixtures that can be imported and used in test files:

  @pytest.fixture
  def topology():
      '''TopologyBuilder auto-started and auto-stopped.'''

  @pytest.fixture
  def master_node(topology):
      '''Pre-configured master in a simple topology.'''

  @pytest.fixture
  def satellite_node(master_node, topology):
      '''Pre-configured satellite connected to master.'''

Usage in a test file:

  import pytest
  from hivemind_test_harness.pytest_fixtures import *  # noqa

  def test_handshake(master_node, satellite_node):
      assert master_node is not None
      assert satellite_node is not None

Or import directly in conftest.py:

  # tests/conftest.py
  pytest_plugins = ['hivescope.pytest_fixtures']
"""

import pytest
from typing import Optional

from hivemind_test_harness.topology import TopologyBuilder
from hivemind_test_harness.node import MasterNode, SatelliteNode
from hivemind_bus_client.identity import NodeIdentity


@pytest.fixture(scope="function")
def topology() -> TopologyBuilder:
    """
    A fresh TopologyBuilder auto-started and auto-stopped.

    Use to build test topologies:

      def test_with_topology(topology):
          m = topology.add_master("M0")
          s = topology.add_satellite("S0", upstream=m)
          topology.start_all()

          # ... assertions ...

          topology.stop_all()  # called auto-magically on fixture teardown
    """
    builder = TopologyBuilder()
    yield builder
    # Auto-stop on teardown
    try:
        builder.stop_all()
    except Exception:
        pass


@pytest.fixture(scope="function")
def master_node(topology: TopologyBuilder) -> MasterNode:
    """
    A pre-configured master node in a simple single-master topology.

    Automatically started and stopped.

    Use to test satellite interactions:

      def test_master_receives_message(master_node, satellite_node):
          master_node.register_satellite("key1", password="pwd1")
          satellite = topology.add_satellite("S0", upstream=master_node)
          satellite.connect(master_node)
          satellite.wait_for_handshake(timeout=5)
    """
    m = topology.add_master("M0")
    topology.start_all()
    yield m
    # Auto-stop on teardown
    try:
        topology.stop_all()
    except Exception:
        pass


@pytest.fixture(scope="function")
def satellite_node(
    master_node: MasterNode,
    topology: TopologyBuilder
) -> SatelliteNode:
    """
    A pre-configured satellite connected and handshook with master_node.

    Automatically started and connected.

    Use to test satellite behavior:

      def test_satellite_sends_message(satellite_node):
          from ovos_bus_client.message import Message
          satellite_node.send(Message("test:message", {"data": "value"}))
    """
    # Register the satellite in the master's DB
    master_node.register_satellite("test-key", password="test-password")

    # Create and connect the satellite
    s = topology.add_satellite("S0", upstream=master_node)
    s.connect(master_node)
    s.wait_for_handshake(timeout=10)

    yield s

    # Auto-stop on teardown
    try:
        topology.stop_all()
    except Exception:
        pass


@pytest.fixture(scope="function")
def admin_satellite(
    master_node: MasterNode,
    topology: TopologyBuilder
) -> SatelliteNode:
    """
    A pre-configured admin satellite with broadcast/propagate permissions.

    Useful for testing admin-level operations (broadcast, propagate, etc).
    """
    master_node.register_satellite(
        "admin-key",
        password="admin-password",
        is_admin=True,
        can_propagate=True
    )

    s = topology.add_satellite("S_ADMIN", upstream=master_node)
    s.connect(master_node)
    s.wait_for_handshake(timeout=10)

    yield s

    try:
        topology.stop_all()
    except Exception:
        pass


@pytest.fixture(scope="function")
def restricted_satellite(
    master_node: MasterNode,
    topology: TopologyBuilder
) -> SatelliteNode:
    """
    A pre-configured non-admin satellite with minimal permissions.

    Useful for testing ACL enforcement.
    """
    master_node.register_satellite(
        "restricted-key",
        password="restricted-password",
        is_admin=False,
        can_propagate=False,
        can_escalate=False,
        allowed_types=["recognizer_loop:utterance"]
    )

    s = topology.add_satellite("S_RESTRICTED", upstream=master_node)
    s.connect(master_node)
    s.wait_for_handshake(timeout=10)

    yield s

    try:
        topology.stop_all()
    except Exception:
        pass
