"""
Common assertion helpers for hivescope e2e tests.

Simplify protocol-level assertions with helpers like:
  - assert_handshake_complete(master, satellite)
  - assert_message_routed(master, msg_type, count)
  - assert_acl_enforced(master, satellite, msg_type)

Each helper performs a specific protocol check and raises AssertionError
with detailed failure messages if the check fails.

Usage:

  from hivemind_test_harness.assertions import (
      assert_handshake_complete,
      assert_message_routed,
  )

  def test_handshake(master_node, satellite_node):
      satellite_node.connect(master_node)
      satellite_node.wait_for_handshake(timeout=5)

      assert_handshake_complete(master_node, satellite_node)
      assert_message_routed(master_node, "HELLO", count=1)
"""

from typing import Optional, Any
from hivemind_test_harness.node import MasterNode, SatelliteNode
from hivemind_bus_client.message import HiveMessageType


def assert_handshake_complete(
    master: MasterNode,
    satellite: SatelliteNode,
    timeout: float = 5.0
) -> None:
    """
    Assert that satellite has completed handshake with master.

    Checks:
    - satellite.crypto_key is not None
    - satellite.handshake_event is set
    - master has registered the satellite peer

    Raises AssertionError with diagnostic details if any check fails.
    """
    errors = []

    if satellite.crypto_key is None:
        errors.append("satellite.crypto_key is None (no crypto negotiated)")

    if not satellite.handshake_event.is_set():
        errors.append("satellite.handshake_event not set (handshake not complete)")

    connected_peers = master.connected_peers()
    if satellite.peer not in connected_peers:
        errors.append(
            f"satellite peer '{satellite.peer}' not in master's connected_peers: {connected_peers}"
        )

    if errors:
        raise AssertionError(
            f"Handshake not complete:\n  " + "\n  ".join(errors)
        )


def assert_message_routed(
    node,
    msg_type: str,
    count: int = 1,
    direction: Optional[str] = None,
    timeout: float = 2.0
) -> None:
    """
    Assert that a specific message type was routed through a node.

    Checks:
    - message was recorded by node.recorder
    - count matches expected number

    Args:
      node: MasterNode or SatelliteNode with MessageRecorder
      msg_type: HiveMessageType name (e.g., "HELLO", "BUS", "BROADCAST")
      count: Expected number of messages
      direction: Optional "inbound" or "outbound" filter
      timeout: Wait time for message to appear (not implemented yet)

    Raises AssertionError if count doesn't match.
    """
    messages = node.recorder.messages

    if direction:
        messages = [m for m in messages if m.direction == direction]

    matching = [m for m in messages if m.msg_type == msg_type]
    actual_count = len(matching)

    if actual_count != count:
        raise AssertionError(
            f"Expected {count} '{msg_type}' messages, got {actual_count}.\n"
            f"All messages: {[m.msg_type for m in node.recorder.messages]}"
        )


def assert_acl_enforced(
    master: MasterNode,
    satellite: SatelliteNode,
    msg_type: str,
    allowed: bool = False
) -> None:
    """
    Assert that ACL is enforced for a message type on a satellite.

    If allowed=False, verify that sending msg_type to satellite is blocked.
    If allowed=True, verify that msg_type is allowed through.

    This is a placeholder for more complex ACL assertions.
    """
    # TODO: Implement after ACL enforcement logic is fully understood
    pass


def assert_encryption_match(
    master: MasterNode,
    satellite: SatelliteNode
) -> None:
    """
    Assert that master and satellite have matching encryption settings.

    Checks:
    - cipher type matches
    - json_encoding matches

    Raises AssertionError if settings don't match.
    """
    errors = []

    if master.cipher != satellite.cipher:
        errors.append(
            f"cipher mismatch: master={master.cipher}, satellite={satellite.cipher}"
        )

    if master.json_encoding != satellite.json_encoding:
        errors.append(
            f"json_encoding mismatch: master={master.json_encoding}, "
            f"satellite={satellite.json_encoding}"
        )

    if errors:
        raise AssertionError(
            f"Encryption settings don't match:\n  " + "\n  ".join(errors)
        )


def assert_client_registered(
    master: MasterNode,
    peer: str
) -> None:
    """
    Assert that a client is registered in master's connected_peers.

    Raises AssertionError if peer is not registered.
    """
    connected = master.connected_peers()
    if peer not in connected:
        raise AssertionError(
            f"Peer '{peer}' not registered in master. "
            f"Connected peers: {connected}"
        )


def assert_client_not_registered(
    master: MasterNode,
    peer: str
) -> None:
    """
    Assert that a client is NOT registered in master's connected_peers.

    Raises AssertionError if peer is registered.
    """
    connected = master.connected_peers()
    if peer in connected:
        raise AssertionError(
            f"Peer '{peer}' is registered in master. "
            f"Connected peers: {connected}"
        )


def assert_message_received_by(
    node,
    msg_type: str,
    count: int = 1
) -> None:
    """
    Assert that node's recorder has received a message type.

    Convenience wrapper for assert_message_routed with direction='inbound'.
    """
    assert_message_routed(node, msg_type, count=count, direction="inbound")


def assert_message_sent_by(
    node,
    msg_type: str,
    count: int = 1
) -> None:
    """
    Assert that node's recorder has sent a message type.

    Convenience wrapper for assert_message_routed with direction='outbound'.
    """
    assert_message_routed(node, msg_type, count=count, direction="outbound")
