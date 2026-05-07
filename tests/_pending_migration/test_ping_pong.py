"""
Last Edit: Claude Opus 4.6 - 2026-03-21 - Motive: Rewrite for PING-only discovery protocol — no more PONG messages; satellites respond with their own PING (same flood_id).

TS-PING-01..11 — PING-only flood discovery / HiveMap end-to-end scenarios.

Coverage
────────
  TS-PING-01  PROPAGATE(PING) from master reaches satellite(s)
  TS-PING-02  Satellite auto-replies with responsive PING (same flood_id)
  TS-PING-03  Master bus fires hive.ping.received (satellite-originated)
  TS-PING-04  Master bus fires hive.ping.received after flood discovery cycle
  TS-PING-05  HiveMapper state after full ping flood cycle
  TS-PING-06  All satellites in a star topology respond
  TS-PING-07  Chain / deep-chain: PING propagates through relay nodes to leaf sats
  TS-PING-08  Huge hive — M0 discovers all nodes through relay forwarding
  TS-PING-09  Chaotic hive — M0 discovers all nodes through relay forwarding
  TS-PING-10  Silent nodes — nodes that skip responsive PING are absent from HiveMapper
  TS-PING-11  Asymmetric hive — M0 discovers entire long arm via relay forwarding

Relay (dual-role node) propagation
────────────────────────────────────
A relay node is simultaneously a satellite (upstream connection) and a master
(downstream listener) sharing ONE agent bus.  When the slave protocol receives
a PROPAGATE message it emits ``hive.send.downstream`` on the shared bus.
The TestAgentProtocol (modelling OVOSProtocol / HiveMindListenerInternalProtocol)
picks this up and broadcasts the PROPAGATE to all downstream satellites.

This means PING *does* propagate through relay nodes — exactly as in a real
HiveMind deployment.  Consequence: a single PING from M0 reaches ALL nodes in
the hive tree; M0's HiveMapper is populated with the ENTIRE reachable graph,
not just its direct children.

Each relay master's own HiveMapper captures the view from its vantage point.

Protocol change (PING-only discovery):
Satellites no longer send PONG.  Instead, they respond with their own PING
carrying the same ``flood_id``.  The master's ``HiveMapper.on_ping()`` handles
deduplication via ``_seen_pings``.  The bus event is ``hive.ping.received``
for all discovery responses.
"""

import json as _json
import random as _random
import time
import uuid
from unittest.mock import patch

import pytest
from hivemind_bus_client.message import HiveMessage, HiveMessageType
from hivescope.topology import TopologyBuilder

# Mirror of conftest._HUGE_HIVE_COUNTS — same seed so the counts are identical.
_HUGE_HIVE_SEED = 2026
_HUGE_HIVE_RNG = _random.Random(_HUGE_HIVE_SEED)
_HUGE_HIVE_COUNTS = [_HUGE_HIVE_RNG.randint(2, 6) for _ in range(10)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ping(peer: str, site_id: str = "test-site") -> HiveMessage:
    """Return a PROPAGATE(PING) ready to be sent from master to satellites."""
    ping_inner = HiveMessage(
        HiveMessageType.PING,
        payload={
            "flood_id": str(uuid.uuid4()),
            "timestamp": time.time(),
            "peer": peer,
            "site_id": site_id,
        },
    )
    return HiveMessage(HiveMessageType.PROPAGATE, payload=ping_inner)


def _flood_id(outer: HiveMessage) -> str:
    """Extract the flood_id from a PROPAGATE(PING) outer message."""
    return outer.payload.payload["flood_id"]


def _do_ping(master_node) -> HiveMessage:
    """Send a PING from master_node to all its direct clients and return the
    outer PROPAGATE(PING) message (for flood_id retrieval)."""
    ping_msg = _make_ping(peer=master_node.hm_protocol.peer)
    fid = _flood_id(ping_msg)
    master_node.hm_protocol.hive_mapper.start_ping(fid)
    # Mark this flood_id as seen so the master doesn't re-announce when
    # responsive PINGs come back (it already announced by sending the original)
    master_node.hm_protocol._seen_flood_ids.add(fid)
    master_node.send_to_all(ping_msg)
    return ping_msg


# ---------------------------------------------------------------------------
# TS-PING-01 — PROPAGATE(PING) from master reaches satellite(s)
# ---------------------------------------------------------------------------

class TestPingReachesSatellite:
    """TS-PING-01 — master's PROPAGATE(PING) is delivered to every direct satellite."""

    def test_satellite_receives_propagate(self, minimal_topology):
        b = minimal_topology
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")

        received = []
        s0.shim.emitter.on(HiveMessageType.PROPAGATE, received.append)
        m0.send_to_all(_make_ping(peer=m0.hm_protocol.peer))

        assert len(received) >= 1, "Satellite must receive the PROPAGATE from master"

    def test_inner_type_is_ping(self, minimal_topology):
        b = minimal_topology
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")

        received = []
        s0.shim.emitter.on(HiveMessageType.PROPAGATE, received.append)
        m0.send_to_all(_make_ping(peer=m0.hm_protocol.peer))

        ping_wraps = [m for m in received
                      if m.payload.msg_type == HiveMessageType.PING]
        assert len(ping_wraps) >= 1, "At least one PROPAGATE(PING) should arrive"

    def test_all_satellites_receive_ping_star(self, star_topology):
        b = star_topology
        m0 = b.get_master("M0")

        counters = {i: [] for i in range(3)}
        for i in range(3):
            b.get_satellite(f"S{i}").shim.emitter.on(
                HiveMessageType.PROPAGATE, counters[i].append
            )
        m0.send_to_all(_make_ping(peer=m0.hm_protocol.peer))

        for i in range(3):
            ping_wraps = [m for m in counters[i]
                          if m.payload.msg_type == HiveMessageType.PING]
            assert len(ping_wraps) >= 1, f"S{i} should receive at least one PING"


# ---------------------------------------------------------------------------
# TS-PING-02 — Satellite auto-replies with responsive PING (same flood_id)
# ---------------------------------------------------------------------------

class TestSatelliteRespondsToPing:
    """TS-PING-02 — satellite auto-replies with its own PING when it receives a PING."""

    def test_responsive_ping_updates_hive_mapper(self, minimal_topology):
        b = minimal_topology
        m0 = b.get_master("M0")
        _do_ping(m0)
        assert len(m0.hm_protocol.hive_mapper.nodes) >= 1, \
            "HiveMapper must register at least one node after responsive PING"

    def test_responsive_ping_registers_satellite_peer(self, minimal_topology):
        b = minimal_topology
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")
        _do_ping(m0)
        mapper_peers = list(m0.hm_protocol.hive_mapper.nodes.keys())
        assert s0.peer in mapper_peers, \
            f"Satellite peer {s0.peer!r} not in mapper nodes: {mapper_peers}"

    def test_responsive_ping_carries_correct_site_id(self, minimal_topology):
        b = minimal_topology
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")
        _do_ping(m0)
        node_info = m0.hm_protocol.hive_mapper.nodes.get(s0.peer)
        assert node_info is not None, f"No NodeInfo for peer {s0.peer!r}"
        assert node_info.site_id == s0.identity.site_id, \
            f"Expected site_id={s0.identity.site_id!r}, got {node_info.site_id!r}"


# ---------------------------------------------------------------------------
# TS-PING-03 — bus event hive.ping.received on master (satellite-originated)
# ---------------------------------------------------------------------------

class TestHivePingReceivedBusEvent:
    """TS-PING-03 — master's bus fires hive.ping.received when a satellite sends PING."""

    def test_event_emitted(self, minimal_topology):
        b = minimal_topology
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")

        events = []
        m0.agent_protocol.bus.on("hive.ping.received", events.append)

        ping_inner = HiveMessage(HiveMessageType.PING, payload={
            "flood_id": "sat-originated-ping",
            "timestamp": time.time(),
            "peer": s0.peer,
            "site_id": s0.identity.site_id or "",
        })
        s0.send(HiveMessage(HiveMessageType.PROPAGATE, payload=ping_inner))

        assert len(events) >= 1, "hive.ping.received must fire on master bus"

    def test_event_data_contains_flood_id(self, minimal_topology):
        b = minimal_topology
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")

        events = []
        m0.agent_protocol.bus.on("hive.ping.received", events.append)

        flood_id = "check-flood-id-propagation"
        ping_inner = HiveMessage(HiveMessageType.PING, payload={
            "flood_id": flood_id,
            "timestamp": time.time(),
            "peer": s0.peer,
            "site_id": s0.identity.site_id or "",
        })
        s0.send(HiveMessage(HiveMessageType.PROPAGATE, payload=ping_inner))

        assert events, "hive.ping.received must fire"
        assert events[0].data["flood_id"] == flood_id, \
            f"Expected flood_id={flood_id!r}, got {events[0].data.get('flood_id')!r}"


# ---------------------------------------------------------------------------
# TS-PING-04 — bus event hive.ping.received after flood discovery cycle
# ---------------------------------------------------------------------------

class TestHivePingReceivedFloodEvent:
    """TS-PING-04 — master's bus fires hive.ping.received for every responsive PING
    from satellites during a flood discovery cycle."""

    def test_event_emitted(self, minimal_topology):
        b = minimal_topology
        m0 = b.get_master("M0")
        events = []
        m0.agent_protocol.bus.on("hive.ping.received", events.append)
        _do_ping(m0)
        assert len(events) >= 1, "hive.ping.received must fire on master bus"

    def test_event_data_has_correct_peer(self, minimal_topology):
        b = minimal_topology
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")

        events = []
        m0.agent_protocol.bus.on("hive.ping.received", events.append)
        _do_ping(m0)

        assert events, "hive.ping.received must fire"
        assert "peer" in events[0].data, "Event data must include 'peer'"
        assert events[0].data["peer"] == s0.peer, \
            f"Expected peer={s0.peer!r}, got {events[0].data.get('peer')!r}"


# ---------------------------------------------------------------------------
# TS-PING-05 — HiveMapper state after a full ping flood cycle
# ---------------------------------------------------------------------------

class TestHiveMapperIntegration:
    """TS-PING-05 — HiveMapper reflects accurate topology after ping flood discovery."""

    def test_to_dict_contains_satellite_node(self, minimal_topology):
        b = minimal_topology
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")
        _do_ping(m0)

        result = m0.hm_protocol.hive_mapper.to_dict()
        peers_in_map = [n["peer"] for n in result["nodes"]]
        assert s0.peer in peers_in_map, \
            f"Expected {s0.peer!r} in mapper nodes, got {peers_in_map}"

    def test_to_json_is_valid_json(self, minimal_topology):
        b = minimal_topology
        m0 = b.get_master("M0")
        _do_ping(m0)
        parsed = _json.loads(m0.hm_protocol.hive_mapper.to_json())
        assert "nodes" in parsed and "edges" in parsed

    def test_to_ascii_renders_root_peer(self, minimal_topology):
        """to_ascii(root_peer=…) always renders the root even when edges are empty.

        In a direct 1-hop topology the responsive PING carries no route hops
        (no relay called update_hop_data), so no edges are registered.  The
        root is still rendered under the ``[self]`` label.
        """
        b = minimal_topology
        m0 = b.get_master("M0")
        _do_ping(m0)
        ascii_map = m0.hm_protocol.hive_mapper.to_ascii(root_peer=m0.hm_protocol.peer)
        assert m0.hm_protocol.peer in ascii_map, \
            f"ASCII map should contain root peer.\nGot:\n{ascii_map}"
        assert "[self]" in ascii_map, \
            f"Root peer should be labelled [self].\nGot:\n{ascii_map}"

    def test_node_info_rtt_available(self, minimal_topology):
        b = minimal_topology
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")
        _do_ping(m0)
        node_info = m0.hm_protocol.hive_mapper.nodes.get(s0.peer)
        assert node_info is not None
        assert node_info.rtt_ms is not None, \
            "RTT should be computable when both timestamps are set"
        assert node_info.rtt_ms >= 0, "RTT must be non-negative"

    def test_clear_resets_mapper(self, minimal_topology):
        b = minimal_topology
        m0 = b.get_master("M0")
        _do_ping(m0)
        assert len(m0.hm_protocol.hive_mapper.nodes) >= 1
        m0.hm_protocol.hive_mapper.clear()
        assert len(m0.hm_protocol.hive_mapper.nodes) == 0
        assert len(m0.hm_protocol.hive_mapper.edges) == 0


# ---------------------------------------------------------------------------
# TS-PING-06 — All satellites in a star topology respond
# ---------------------------------------------------------------------------

class TestPingAllSatellitesStar:
    """TS-PING-06 — every satellite in a star topology responds to a single PING."""

    def test_all_satellites_respond(self, star_topology):
        b = star_topology
        m0 = b.get_master("M0")
        _do_ping(m0)
        n_nodes = len(m0.hm_protocol.hive_mapper.nodes)
        assert n_nodes == 3, \
            f"Expected 3 responsive PINGs (one per satellite), got {n_nodes}"

    def test_all_satellite_peers_in_mapper(self, star_topology):
        b = star_topology
        m0 = b.get_master("M0")
        _do_ping(m0)
        mapper_peers = set(m0.hm_protocol.hive_mapper.nodes.keys())
        for i in range(3):
            sat_peer = b.get_satellite(f"S{i}").peer
            assert sat_peer in mapper_peers, \
                f"S{i} peer {sat_peer!r} missing from HiveMapper: {mapper_peers}"

    def test_deduplication_across_multiple_pings(self, star_topology):
        """Same flood_id sent twice must not double-count satellites."""
        b = star_topology
        m0 = b.get_master("M0")
        fixed_id = "dedup-test-id"
        m0.hm_protocol.hive_mapper.start_ping(fixed_id)

        for _ in range(2):
            ping_inner = HiveMessage(HiveMessageType.PING, payload={
                "flood_id": fixed_id,
                "timestamp": time.time(),
                "peer": m0.hm_protocol.peer,
                "site_id": "hub",
            })
            m0.send_to_all(HiveMessage(HiveMessageType.PROPAGATE, payload=ping_inner))

        n_nodes = len(m0.hm_protocol.hive_mapper.nodes)
        assert n_nodes == 3, \
            f"Deduplication failed: expected 3 unique nodes, got {n_nodes}"

    def test_hive_ping_received_fires_per_satellite(self, star_topology):
        b = star_topology
        m0 = b.get_master("M0")
        events = []
        m0.agent_protocol.bus.on("hive.ping.received", events.append)
        _do_ping(m0)
        assert len(events) == 3, \
            f"Expected 3 hive.ping.received events (one per satellite), got {len(events)}"


# ---------------------------------------------------------------------------
# TS-PING-07 — Chain and deep-chain topologies
# ---------------------------------------------------------------------------

class TestPingChainTopology:
    """TS-PING-07a — chain topology (M0 → relay R1 → S0).

    R1 is a dual-role node (satellite of M0 AND master for S0) sharing one
    agent bus.  When M0's PING reaches R1's satellite side, R1 forwards it
    downstream to S0 via ``hive.send.downstream``.  S0's responsive PING is
    relayed back to M0.  So M0's HiveMapper ends up with BOTH R1_sat AND S0.

    The relay master (R1_master) also has its own HiveMapper; pinging it
    directly yields S0 only (1 node) from R1_master's direct-child perspective.
    """

    def test_relay_satellite_and_downstream_respond(self, chain_topology):
        """M0's PING reaches R1_sat directly AND is forwarded to S0 by the relay."""
        b = chain_topology
        m0 = b.get_master("M0")
        _do_ping(m0)
        # Both R1_sat (direct response) and S0 (forwarded via relay) appear.
        assert len(m0.hm_protocol.hive_mapper.nodes) == 2, \
            ("M0 should see R1_sat + S0 (forwarded via relay). "
             f"Got {list(m0.hm_protocol.hive_mapper.nodes.keys())}")

    def test_relay_peer_is_in_m0_mapper(self, chain_topology):
        b = chain_topology
        m0 = b.get_master("M0")
        r1_sat = b.get_satellite("R1_sat")
        _do_ping(m0)
        assert r1_sat.peer in m0.hm_protocol.hive_mapper.nodes, \
            f"R1_sat peer {r1_sat.peer!r} should be in M0's HiveMapper"

    def test_deep_satellite_reachable_via_relay(self, chain_topology):
        """S0 is behind relay R1 but the relay forwards PING downstream, so M0
        discovers S0 in its HiveMapper."""
        b = chain_topology
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")
        _do_ping(m0)
        assert s0.peer in m0.hm_protocol.hive_mapper.nodes, \
            ("S0's responsive PING is relayed back to M0 via R1; it must appear in M0's mapper. "
             f"nodes={list(m0.hm_protocol.hive_mapper.nodes)}")

    def test_relay_master_can_ping_its_own_satellite(self, chain_topology):
        """R1_master pings S0 independently; its own HiveMapper shows just S0."""
        b = chain_topology
        r1_master = b.get_master("R1_master")
        s0 = b.get_satellite("S0")
        _do_ping(r1_master)
        assert s0.peer in r1_master.hm_protocol.hive_mapper.nodes, \
            f"S0 should appear in R1_master's HiveMapper after its own PING"

    def test_relay_ping_emits_bus_event(self, chain_topology):
        """Two hive.ping.received events fire on M0: one for R1_sat, one for S0."""
        b = chain_topology
        m0 = b.get_master("M0")
        events = []
        m0.agent_protocol.bus.on("hive.ping.received", events.append)
        _do_ping(m0)
        assert len(events) == 2, \
            f"Expected 2 hive.ping.received events on M0 (R1_sat + S0 via relay), got {len(events)}"


class TestPingDeepChainTopology:
    """TS-PING-07b — deep chain (M0 → R1 → R2 → S0, depth 3).

    Each relay node shares one agent bus and forwards PING downstream.
    So M0's PING propagates: M0→R1_sat, R1 forwards→R2_sat, R2 forwards→S0.
    All three nodes respond with their own PING, relayed up the chain to M0.
    M0's mapper ends up with all 3 nodes (R1_sat, R2_sat, S0).

    R1_master's mapper (when it pings independently) shows: R2_sat + S0 (2).
    R2_master's mapper shows: S0 only (1).
    """

    def test_m0_sees_all_three_nodes(self, deep_chain_topology):
        """M0's PING propagates through both relay levels; all 3 nodes respond."""
        b = deep_chain_topology
        m0 = b.get_master("M0")
        r1_sat = b.get_satellite("R1_sat")
        r2_sat = b.get_satellite("R2_sat")
        s0 = b.get_satellite("S0")
        _do_ping(m0)
        nodes = m0.hm_protocol.hive_mapper.nodes
        assert len(nodes) == 3, \
            f"Expected 3 nodes (R1_sat, R2_sat, S0), got {len(nodes)}: {list(nodes)}"
        assert r1_sat.peer in nodes
        assert r2_sat.peer in nodes
        assert s0.peer in nodes

    def test_r1_master_sees_r2_and_s0(self, deep_chain_topology):
        """R1_master pings: R2_sat responds directly, R2 forwards to S0."""
        b = deep_chain_topology
        r1_master = b.get_master("R1_master")
        r2_sat = b.get_satellite("R2_sat")
        s0 = b.get_satellite("S0")
        _do_ping(r1_master)
        nodes = r1_master.hm_protocol.hive_mapper.nodes
        assert len(nodes) == 2, \
            f"Expected 2 nodes (R2_sat + S0), got {len(nodes)}: {list(nodes)}"
        assert r2_sat.peer in nodes
        assert s0.peer in nodes

    def test_r2_master_sees_s0(self, deep_chain_topology):
        b = deep_chain_topology
        r2_master = b.get_master("R2_master")
        s0 = b.get_satellite("S0")
        _do_ping(r2_master)
        assert s0.peer in r2_master.hm_protocol.hive_mapper.nodes


# ---------------------------------------------------------------------------
# TS-PING-08 — Huge hive topology
# ---------------------------------------------------------------------------

class TestPingHugeHive:
    """TS-PING-08 — 10 relay masters each with seeded-random satellites.

    M0 sends PING; relay forwarding causes PING to reach ALL nodes in the tree.
    M0's mapper ends up with 10 relay-sats + sum(_HUGE_HIVE_COUNTS) leaf sats.

    Each relay master, when pinged independently, sees only its own N leaf sats
    (those are pure satellites with no downstream, so no further forwarding).
    """

    @pytest.mark.slow
    def test_m0_sees_all_nodes(self, huge_hive_topology):
        """M0's PING propagates through all 10 relays to every leaf satellite."""
        b = huge_hive_topology
        m0 = b.get_master("M0")
        _do_ping(m0)
        n = len(m0.hm_protocol.hive_mapper.nodes)
        expected = 10 + sum(_HUGE_HIVE_COUNTS)  # 10 relay sats + all leaf sats
        assert n == expected, \
            (f"M0 should see {expected} total nodes (relay sats + leaf sats), got {n}. "
             f"nodes={list(m0.hm_protocol.hive_mapper.nodes.keys())}")

    @pytest.mark.slow
    def test_all_relay_sat_peers_in_m0_mapper(self, huge_hive_topology):
        """All relay satellite peers appear in M0's HiveMapper."""
        b = huge_hive_topology
        m0 = b.get_master("M0")
        _do_ping(m0)
        mapper_peers = set(m0.hm_protocol.hive_mapper.nodes.keys())
        for i in range(10):
            rsat = b.get_satellite(f"RM{i}_sat")
            assert rsat.peer in mapper_peers, \
                f"RM{i}_sat peer missing from M0's HiveMapper"

    @pytest.mark.slow
    @pytest.mark.timeout(600)
    def test_each_relay_master_sees_its_own_satellites(self, huge_hive_topology):
        """Each relay master independently pings its own leaf satellites.

        Leaf satellites have no downstream, so no further forwarding occurs —
        each relay master sees exactly its own N leaf sats.
        """
        b = huge_hive_topology
        for relay_idx, n_sats in enumerate(_HUGE_HIVE_COUNTS):
            rm = b.get_master(f"RM{relay_idx}_master")
            _do_ping(rm)
            n = len(rm.hm_protocol.hive_mapper.nodes)
            assert n == n_sats, \
                f"RM{relay_idx}_master expected {n_sats} responsive PINGs (leaf sats only), got {n}"

    @pytest.mark.slow
    def test_ping_events_count_m0(self, huge_hive_topology):
        """hive.ping.received fires once per discovered node (all nodes in hive)."""
        b = huge_hive_topology
        m0 = b.get_master("M0")
        events = []
        m0.agent_protocol.bus.on("hive.ping.received", events.append)
        _do_ping(m0)
        expected = 10 + sum(_HUGE_HIVE_COUNTS)
        assert len(events) == expected, \
            f"Expected {expected} hive.ping.received events on M0, got {len(events)}"


# ---------------------------------------------------------------------------
# TS-PING-09 — Chaotic hive topology
# ---------------------------------------------------------------------------

class TestPingChaoticHive:
    """TS-PING-09 — complex multi-level tree; M0 discovers ALL nodes via relay forwarding.

    Relay nodes forward PING downstream, so M0's single PING reaches every node:
    R1_sat, R2_sat, S6 (direct) + S0, S1, S2 (via R1) + S3, R3_sat (via R2)
    + S4, S5 (via R3 via R2) = 10 total nodes in M0's mapper.
    """

    @pytest.mark.slow
    def test_m0_sees_all_nodes(self, chaotic_hive_topology):
        """M0's PING propagates through all relay levels; 10 total nodes respond."""
        b = chaotic_hive_topology
        m0 = b.get_master("M0")
        _do_ping(m0)
        n = len(m0.hm_protocol.hive_mapper.nodes)
        # R1_sat, R2_sat, S6 (direct) + S0..S5 (forwarded via relays) + R3_sat = 10
        assert n == 10, \
            (f"M0 should see 10 nodes (all via relay forwarding), got {n}. "
             f"nodes={list(m0.hm_protocol.hive_mapper.nodes.keys())}")

    @pytest.mark.slow
    def test_m0_direct_children_are_in_mapper(self, chaotic_hive_topology):
        """Direct children of M0 (R1_sat, R2_sat, S6) are in M0's mapper."""
        b = chaotic_hive_topology
        m0 = b.get_master("M0")
        _do_ping(m0)
        mapper_peers = set(m0.hm_protocol.hive_mapper.nodes.keys())
        for name in ("R1_sat", "R2_sat", "S6"):
            node = b.get_satellite(name)
            assert node.peer in mapper_peers, \
                f"{name} peer {node.peer!r} missing from M0's HiveMapper: {mapper_peers}"

    @pytest.mark.slow
    def test_r1_master_sees_its_three_satellites(self, chaotic_hive_topology):
        """R1_master pings: S0, S1, S2 are leaf satellites — no further forwarding."""
        b = chaotic_hive_topology
        r1_master = b.get_master("R1_master")
        _do_ping(r1_master)
        n = len(r1_master.hm_protocol.hive_mapper.nodes)
        assert n == 3, \
            f"R1_master should see 3 responsive PINGs (S0, S1, S2 — leaf sats), got {n}"

    @pytest.mark.slow
    def test_r2_master_sees_its_whole_subtree(self, chaotic_hive_topology):
        """R2_master pings: S3 responds directly; R3 forwards to S4 and S5 — 4 total."""
        b = chaotic_hive_topology
        r2_master = b.get_master("R2_master")
        _do_ping(r2_master)
        n = len(r2_master.hm_protocol.hive_mapper.nodes)
        assert n == 4, \
            f"R2_master should see 4 responsive PINGs (S3, R3_sat, S4, S5 via relay), got {n}"

    @pytest.mark.slow
    def test_r3_master_sees_s4_and_s5(self, chaotic_hive_topology):
        """R3_master pings: S4, S5 are leaf satellites."""
        b = chaotic_hive_topology
        r3_master = b.get_master("R3_master")
        _do_ping(r3_master)
        n = len(r3_master.hm_protocol.hive_mapper.nodes)
        assert n == 2, \
            f"R3_master should see 2 responsive PINGs (S4, S5), got {n}"

    @pytest.mark.slow
    def test_full_network_reachable_from_m0(self, chaotic_hive_topology):
        """M0's single PING covers the entire hive via relay forwarding."""
        b = chaotic_hive_topology
        m0 = b.get_master("M0")
        _do_ping(m0)
        all_discovered = set(m0.hm_protocol.hive_mapper.nodes.keys())

        all_nodes = ["S0", "S1", "S2", "S3", "S4", "S5", "S6",
                     "R1_sat", "R2_sat", "R3_sat"]
        for name in all_nodes:
            node = b.get_satellite(name)
            assert node.peer in all_discovered, \
                f"{name} peer {node.peer!r} not in M0's HiveMapper after single PING"


# ---------------------------------------------------------------------------
# TS-PING-10 — Silent nodes
# ---------------------------------------------------------------------------

class TestSilentNodes:
    """TS-PING-10 — nodes that do not respond to PING are absent from HiveMapper.

    Three mechanisms are tested:
      a) Monkey-patching _handle_ping to be a no-op (clean, no side effects)
      b) All satellites silent — empty HiveMapper
      c) Mixed: some respond, some don't — only responders appear
    """

    def test_silent_satellite_absent_from_mapper(self, minimal_topology):
        """Single satellite silenced — HiveMapper stays empty."""
        b = minimal_topology
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")

        # Make S0 skip responsive PING entirely
        original_handle_ping = s0.slave_protocol._handle_ping
        s0.slave_protocol._handle_ping = lambda msg: None

        try:
            _do_ping(m0)
            assert len(m0.hm_protocol.hive_mapper.nodes) == 0, \
                "HiveMapper should be empty when the only satellite is silent"
        finally:
            s0.slave_protocol._handle_ping = original_handle_ping

    def test_silent_satellite_absent_partial_star(self, star_topology):
        """Star with 3 satellites; S1 silenced — only S0 and S2 appear in mapper."""
        b = star_topology
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")
        s1 = b.get_satellite("S1")
        s2 = b.get_satellite("S2")

        original = s1.slave_protocol._handle_ping
        s1.slave_protocol._handle_ping = lambda msg: None

        try:
            _do_ping(m0)
            mapper_peers = set(m0.hm_protocol.hive_mapper.nodes.keys())
            assert s0.peer in mapper_peers, "S0 (not silenced) should be in mapper"
            assert s2.peer in mapper_peers, "S2 (not silenced) should be in mapper"
            assert s1.peer not in mapper_peers, "S1 (silenced) must NOT be in mapper"
            assert len(mapper_peers) == 2, \
                f"Expected exactly 2 nodes in mapper, got {len(mapper_peers)}"
        finally:
            s1.slave_protocol._handle_ping = original

    def test_all_silent_mapper_empty(self, star_topology):
        """All three satellites silenced — empty HiveMapper."""
        b = star_topology
        m0 = b.get_master("M0")

        originals = {}
        for i in range(3):
            s = b.get_satellite(f"S{i}")
            originals[i] = s.slave_protocol._handle_ping
            s.slave_protocol._handle_ping = lambda msg: None

        try:
            _do_ping(m0)
            assert len(m0.hm_protocol.hive_mapper.nodes) == 0, \
                "Mapper should be empty when all satellites are silent"
        finally:
            for i in range(3):
                b.get_satellite(f"S{i}").slave_protocol._handle_ping = originals[i]

    def test_partial_response_only_responders_in_mapper(self):
        """Freshly built star with 5 satellites; 2 silenced — exactly 3 respond."""
        b = TopologyBuilder()
        b.add_master("M0")
        for i in range(5):
            b.add_satellite(f"S{i}", upstream=b.get_master("M0"))
        b.start_all()

        m0 = b.get_master("M0")
        silent_indices = {1, 3}
        originals = {}
        for i in silent_indices:
            s = b.get_satellite(f"S{i}")
            originals[i] = s.slave_protocol._handle_ping
            s.slave_protocol._handle_ping = lambda msg: None

        try:
            _do_ping(m0)
            mapper_peers = set(m0.hm_protocol.hive_mapper.nodes.keys())
            assert len(mapper_peers) == 3, \
                f"Expected 3 responders, got {len(mapper_peers)}"
            for i in range(5):
                peer = b.get_satellite(f"S{i}").peer
                if i in silent_indices:
                    assert peer not in mapper_peers, f"Silent S{i} must not appear"
                else:
                    assert peer in mapper_peers, f"Responding S{i} must appear"
        finally:
            for i, orig in originals.items():
                b.get_satellite(f"S{i}").slave_protocol._handle_ping = orig
            b.stop_all()

    def test_reconnected_satellite_responds_after_silence(self, minimal_topology):
        """A silenced satellite that has its handler restored responds on the next PING."""
        b = minimal_topology
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")

        original = s0.slave_protocol._handle_ping
        s0.slave_protocol._handle_ping = lambda msg: None

        # First PING — silent
        _do_ping(m0)
        assert len(m0.hm_protocol.hive_mapper.nodes) == 0

        # Restore handler
        s0.slave_protocol._handle_ping = original

        # Second PING — should respond
        m0.hm_protocol.hive_mapper.clear()
        _do_ping(m0)
        assert s0.peer in m0.hm_protocol.hive_mapper.nodes, \
            "Satellite should appear after handler is restored"


# ---------------------------------------------------------------------------
# TS-PING-11 — Asymmetric hive topology
# ---------------------------------------------------------------------------

class TestPingAsymmetricHive:
    """TS-PING-11 — long arm (depth 10) vs short arms (depth 1).

    Relay forwarding propagates M0's PING through all 10 relay levels.
    M0's mapper ends up with all 14 nodes:
      - RA0_sat … RA9_sat (10 relay satellite sides, one per relay level)
      - S_deep (deepest leaf, relayed up 10 levels)
      - S_short0, S_short1, S_short2 (3 direct short-arm satellites)
    """

    @pytest.mark.slow
    def test_m0_sees_all_nodes(self, asymmetric_hive_topology):
        """M0's PING propagates through all 10 relay levels reaching S_deep."""
        b = asymmetric_hive_topology
        m0 = b.get_master("M0")
        _do_ping(m0)
        n = len(m0.hm_protocol.hive_mapper.nodes)
        # 10 relay sats + S_deep + 3 short-arm sats = 14
        assert n == 14, \
            (f"M0 should see 14 nodes (all via relay forwarding), got {n}. "
             f"nodes={list(m0.hm_protocol.hive_mapper.nodes.keys())}")

    @pytest.mark.slow
    def test_m0_sees_long_arm_head(self, asymmetric_hive_topology):
        b = asymmetric_hive_topology
        m0 = b.get_master("M0")
        ra0_sat = b.get_satellite("RA0_sat")
        _do_ping(m0)
        assert ra0_sat.peer in m0.hm_protocol.hive_mapper.nodes, \
            "RA0_sat (head of long arm) must be in M0's HiveMapper"

    @pytest.mark.slow
    def test_m0_sees_all_short_arms(self, asymmetric_hive_topology):
        b = asymmetric_hive_topology
        m0 = b.get_master("M0")
        _do_ping(m0)
        mapper_peers = set(m0.hm_protocol.hive_mapper.nodes.keys())
        for i in range(3):
            peer = b.get_satellite(f"S_short{i}").peer
            assert peer in mapper_peers, \
                f"S_short{i} missing from M0's HiveMapper"

    @pytest.mark.slow
    def test_s_deep_reachable_from_m0_via_relay_chain(self, asymmetric_hive_topology):
        """S_deep (10 hops away) is discovered by M0 via relay forwarding."""
        b = asymmetric_hive_topology
        m0 = b.get_master("M0")
        s_deep = b.get_satellite("S_deep")
        _do_ping(m0)
        assert s_deep.peer in m0.hm_protocol.hive_mapper.nodes, \
            "S_deep must appear in M0's HiveMapper (reached via 10-level relay chain)"

    @pytest.mark.slow
    def test_each_relay_in_long_arm_sees_subtree(self, asymmetric_hive_topology):
        """RA_i_master pings: sees RA_(i+1)_sat + deeper nodes forwarded further down."""
        b = asymmetric_hive_topology
        for i in range(9):  # RA0..RA8 each has a relay child
            rm = b.get_master(f"RA{i}_master")
            child_sat = b.get_satellite(f"RA{i+1}_sat")
            _do_ping(rm)
            assert child_sat.peer in rm.hm_protocol.hive_mapper.nodes, \
                f"RA{i}_master should see RA{i+1}_sat in its mapper"
            # Also S_deep must appear (forwarded all the way down and back up)
            s_deep = b.get_satellite("S_deep")
            assert s_deep.peer in rm.hm_protocol.hive_mapper.nodes, \
                f"RA{i}_master should see S_deep (relayed via deeper nodes)"

    @pytest.mark.slow
    def test_deepest_relay_sees_s_deep(self, asymmetric_hive_topology):
        """RA9_master (deepest relay) sees S_deep's responsive PING directly."""
        b = asymmetric_hive_topology
        ra9_master = b.get_master("RA9_master")
        s_deep = b.get_satellite("S_deep")
        _do_ping(ra9_master)
        assert s_deep.peer in ra9_master.hm_protocol.hive_mapper.nodes, \
            "S_deep must appear in RA9_master's HiveMapper"

    @pytest.mark.slow
    def test_full_long_arm_reachable_from_m0(self, asymmetric_hive_topology):
        """M0's single PING covers every node in the long arm and short arms."""
        b = asymmetric_hive_topology
        m0 = b.get_master("M0")
        _do_ping(m0)
        all_discovered = set(m0.hm_protocol.hive_mapper.nodes.keys())

        for i in range(10):
            rsat = b.get_satellite(f"RA{i}_sat")
            assert rsat.peer in all_discovered, \
                f"RA{i}_sat missing from M0's HiveMapper"

        s_deep = b.get_satellite("S_deep")
        assert s_deep.peer in all_discovered, "S_deep missing from M0's HiveMapper"

        for i in range(3):
            peer = b.get_satellite(f"S_short{i}").peer
            assert peer in all_discovered, f"S_short{i} missing from M0's HiveMapper"
