"""
Preset topology scenarios for common e2e test patterns.

Functions that return pre-configured TopologyBuilder instances, reducing
boilerplate in test files. Each scenario represents a common network
topology used in testing.

Common scenarios:
  - single_satellite(): T1 — 1 master, 1 satellite
  - three_satellites(): T2 — 1 master, 3 satellites
  - with_relay(): T9 — 1 master, relay, satellites under relay
  - chain_topology(): T3 — Master → Relay → Satellites
  - star_topology(): Radial topology with one central master
  - with_acl_enforcement(): Topology with ACL rules pre-configured

Usage:

  from hivemind_test_harness.scenarios import single_satellite

  def test_something():
      b = single_satellite()
      m = b.get_master("M0")
      s = b.get_satellite("S0")
      b.start_all()
      # ... assertions ...
      b.stop_all()
"""

from typing import Optional, Dict, Any

from hivemind_test_harness.topology import TopologyBuilder
from hivemind_test_harness.plugins.agent import TestAgentProtocol


def single_satellite() -> TopologyBuilder:
    """
    Simple T1 topology: 1 master, 1 satellite.

    Pre-registered satellite with key="test-key", password="test-password".

    Perfect for testing basic handshake, encryption, message routing.
    """
    b = TopologyBuilder()
    m = b.add_master("M0")
    m.register_satellite("test-key", password="test-password")
    b.add_satellite("S0", upstream=m)
    return b


def three_satellites() -> TopologyBuilder:
    """
    T2 topology: 1 master, 3 satellites.

    Pre-registered satellites S0, S1, S2 with test credentials.

    Perfect for testing broadcast, propagate, peer-to-peer routing.
    """
    b = TopologyBuilder()
    m = b.add_master("M0")

    for i in range(3):
        key = f"test-key-{i}"
        m.register_satellite(key, password=f"test-password-{i}")
        b.add_satellite(f"S{i}", upstream=m)

    return b


def with_relay() -> TopologyBuilder:
    """
    T9 topology: 1 master, 1 relay, 2 satellites under relay.

    Tests message routing through a dual-role relay node.

    Topology:
      M0
      └── R0 (relay: master facing upstream, satellite facing downstream)
          ├── S0
          └── S1
    """
    b = TopologyBuilder()
    m = b.add_master("M0")

    # Register relay as a satellite under the master
    m.register_satellite("relay-key", password="relay-password")
    r = b.add_relay("R0", upstream=m)

    # Register satellites under the relay
    r.register_satellite("sat-0-key", password="sat-0-password")
    r.register_satellite("sat-1-key", password="sat-1-password")
    b.add_satellite("S0", upstream=r)
    b.add_satellite("S1", upstream=r)

    return b


def chain_topology() -> TopologyBuilder:
    """
    T3 topology: M0 → R0 → S0 (chain of 3 nodes).

    Tests multi-hop escalate, propagate, and message routing.

    Topology:
      M0
      └── R0 (relay)
          └── S0
    """
    b = TopologyBuilder()
    m = b.add_master("M0")
    m.register_satellite("relay-key", password="relay-password")
    r = b.add_relay("R0", upstream=m)

    r.register_satellite("sat-key", password="sat-password")
    b.add_satellite("S0", upstream=r)

    return b


def star_topology(num_satellites: int = 5) -> TopologyBuilder:
    """
    Star topology: 1 central master, N satellites all connected directly.

    Tests broadcast/propagate to many peers simultaneously.

    Args:
      num_satellites: Number of satellites (default 5)
    """
    b = TopologyBuilder()
    m = b.add_master("M0")

    for i in range(num_satellites):
        key = f"sat-{i}-key"
        m.register_satellite(key, password=f"sat-{i}-password")
        b.add_satellite(f"S{i}", upstream=m)

    return b


def with_acl_enforcement() -> TopologyBuilder:
    """
    Topology with fine-grained ACL rules for testing permission enforcement.

    Includes:
    - Admin satellite with full permissions
    - Restricted satellite with message type blacklist
    - Restricted satellite with skill blacklist
    """
    b = TopologyBuilder()
    m = b.add_master("M0")

    # Admin satellite (can do everything)
    m.register_satellite(
        "admin-key",
        password="admin-password",
        is_admin=True,
        can_propagate=True,
        can_escalate=True
    )
    b.add_satellite("S_ADMIN", upstream=m)

    # Restricted satellite (message type blacklist)
    m.register_satellite(
        "restricted-msg-key",
        password="restricted-msg-password",
        is_admin=False,
        msg_blacklist=["speak", "notification"]
    )
    b.add_satellite("S_RESTRICTED_MSG", upstream=m)

    # Restricted satellite (skill blacklist)
    m.register_satellite(
        "restricted-skill-key",
        password="restricted-skill-password",
        is_admin=False,
        skill_blacklist=["mycroft.volume.skill"]
    )
    b.add_satellite("S_RESTRICTED_SKILL", upstream=m)

    return b


def hierarchical_hubs(num_levels: int = 3, sats_per_relay: int = 2) -> TopologyBuilder:
    """
    Hierarchical topology: nested masters/relays for testing deep routing.

    Args:
      num_levels: Depth of hierarchy (2 = M0→R0, 3 = M0→R0→R1, etc.)
      sats_per_relay: Satellites per relay node
    """
    b = TopologyBuilder()
    m = b.add_master("M0")

    # Build relay chain
    prev_node = m
    relays = []
    for level in range(1, num_levels):
        relay_key = f"relay-{level}-key"
        prev_node.register_satellite(relay_key, password=f"relay-{level}-password")
        r = b.add_relay(f"R{level}", upstream=prev_node)
        relays.append(r)
        prev_node = r

    # Add satellites at the last level
    for i in range(sats_per_relay):
        sat_key = f"leaf-{i}-key"
        prev_node.register_satellite(sat_key, password=f"leaf-{i}-password")
        b.add_satellite(f"S{i}", upstream=prev_node)

    return b


def with_multiple_agent_protocols() -> TopologyBuilder:
    """
    Topology where master uses a custom agent protocol.

    Useful for testing agent protocol implementations.

    (Default: TestAgentProtocol; can be overridden by caller)
    """
    b = TopologyBuilder()
    m = b.add_master("M0")
    m.register_satellite("test-key", password="test-password")
    b.add_satellite("S0", upstream=m)
    return b
