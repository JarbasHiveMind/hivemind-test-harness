"""
TS-PING-ONCE — Every node receives a PING from every other node exactly once.

Validates the core PING flood invariant: when one node initiates a PING,
every reachable node in the hive should receive exactly one copy of each
peer's responsive PING.  No duplicates, no missing nodes.

Approach: instrument every master's ``handle_ping_message`` and every
satellite's ``_handle_ping`` to record (flood_id, peer) tuples.  After
a flood completes, assert each (flood_id, peer) pair was seen exactly
once per receiving node.
"""
import time
import uuid
from collections import defaultdict

import pytest
from hivemind_bus_client.message import HiveMessage, HiveMessageType
from hivemind_test_harness.topology import TopologyBuilder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ping(peer: str, site_id: str = "test-site") -> HiveMessage:
    """Build a PROPAGATE(PING) ready to send."""
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
    return outer.payload.payload["flood_id"]


def _do_ping(master_node) -> HiveMessage:
    """Initiate a PING flood from a master node."""
    ping_msg = _make_ping(peer=master_node.hm_protocol.peer)
    fid = _flood_id(ping_msg)
    master_node.hm_protocol.hive_mapper.start_ping(fid)
    master_node.hm_protocol._seen_flood_ids.add(fid)
    master_node.send_to_all(ping_msg)
    return ping_msg


def _instrument_masters(topology, recorder: dict):
    """Wrap handle_ping_message on every master to record (flood_id, peer) arrivals."""
    for name in list(topology._masters):
        master = topology.get_master(name)
        _orig = master.hm_protocol.handle_ping_message

        def _recording(message, client, _name=name, _orig=_orig):
            payload = message.payload if isinstance(message.payload, dict) else {}
            fid = payload.get("flood_id", "")
            peer = payload.get("peer", "")
            if fid and peer:
                recorder[_name].append((fid, peer))
            _orig(message, client)

        master.hm_protocol.handle_ping_message = _recording


def _instrument_satellites(topology, recorder: dict):
    """Wrap _handle_ping on every satellite to record (flood_id, peer) arrivals."""
    for name in list(topology._satellites):
        sat = topology.get_satellite(name)
        _orig = sat.slave_protocol._handle_ping

        def _recording(message, _name=name, _orig=_orig):
            inner = message.payload
            payload = inner.payload if isinstance(inner.payload, dict) else {}
            fid = payload.get("flood_id", "")
            peer = payload.get("peer", "")
            if fid and peer:
                recorder[_name].append((fid, peer))
            _orig(message)

        sat.slave_protocol._handle_ping = _recording


# ---------------------------------------------------------------------------
# Fixtures (self-contained, no dependency on conftest topologies)
# ---------------------------------------------------------------------------

@pytest.fixture
def minimal():
    """1 master, 1 satellite."""
    b = TopologyBuilder()
    b.add_master("M0")
    b.add_satellite("S0", upstream=b.get_master("M0"))
    b.start_all()
    yield b
    b.stop_all()


@pytest.fixture
def star3():
    """1 master, 3 satellites."""
    b = TopologyBuilder()
    b.add_master("M0")
    for i in range(3):
        b.add_satellite(f"S{i}", upstream=b.get_master("M0"))
    b.start_all()
    yield b
    b.stop_all()


@pytest.fixture
def chain():
    """M0 → relay R1 → S0."""
    b = TopologyBuilder()
    b.add_master("M0")
    _, r1_master = b.add_relay("R1", upstream=b.get_master("M0"))
    b.add_satellite("S0", upstream=r1_master)
    b.start_all()
    yield b
    b.stop_all()


@pytest.fixture
def deep_chain():
    """M0 → R1 → R2 → S0."""
    b = TopologyBuilder()
    b.add_master("M0")
    _, r1_master = b.add_relay("R1", upstream=b.get_master("M0"))
    _, r2_master = b.add_relay("R2", upstream=r1_master)
    b.add_satellite("S0", upstream=r2_master)
    b.start_all()
    yield b
    b.stop_all()


@pytest.fixture
def diamond():
    """M0 with 2 relays each having 2 leaf sats.

        M0
        ├─ R1 → S0, S1
        └─ R2 → S2, S3
    """
    b = TopologyBuilder()
    b.add_master("M0")
    _, r1m = b.add_relay("R1", upstream=b.get_master("M0"))
    _, r2m = b.add_relay("R2", upstream=b.get_master("M0"))
    b.add_satellite("S0", upstream=r1m)
    b.add_satellite("S1", upstream=r1m)
    b.add_satellite("S2", upstream=r2m)
    b.add_satellite("S3", upstream=r2m)
    b.start_all()
    yield b
    b.stop_all()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPingExactlyOnceMinimal:
    """Minimal topology: M0 sends PING, S0 receives it exactly once."""

    def test_satellite_sees_master_ping_once(self, minimal):
        b = minimal
        sat_rec = defaultdict(list)
        _instrument_satellites(b, sat_rec)

        ping = _do_ping(b.get_master("M0"))
        fid = _flood_id(ping)
        m0_peer = b.get_master("M0").hm_protocol.peer

        # S0 should see M0's PING exactly once
        s0_pings = [(f, p) for f, p in sat_rec["S0"] if f == fid]
        assert len(s0_pings) == 1, f"S0 should see M0 PING once, got {len(s0_pings)}"
        assert s0_pings[0][1] == m0_peer

    def test_master_sees_satellite_response_once(self, minimal):
        b = minimal
        master_rec = defaultdict(list)
        _instrument_masters(b, master_rec)

        ping = _do_ping(b.get_master("M0"))
        fid = _flood_id(ping)
        s0_peer = b.get_satellite("S0").peer

        # M0 should see S0's responsive PING exactly once
        m0_pings = [(f, p) for f, p in master_rec["M0"] if f == fid and p == s0_peer]
        assert len(m0_pings) == 1, f"M0 should see S0 response once, got {len(m0_pings)}"


class TestPingExactlyOnceStar:
    """Star: M0 + 3 satellites.  Each satellite's response seen once at M0."""

    def test_all_satellites_respond_once(self, star3):
        b = star3
        master_rec = defaultdict(list)
        _instrument_masters(b, master_rec)

        ping = _do_ping(b.get_master("M0"))
        fid = _flood_id(ping)

        sat_peers = {b.get_satellite(f"S{i}").peer for i in range(3)}
        m0_pings = [(f, p) for f, p in master_rec["M0"] if f == fid]

        # M0 should see exactly 3 responsive PINGs
        assert len(m0_pings) == 3, \
            f"M0 should see 3 responses, got {len(m0_pings)}: {m0_pings}"

        # Each satellite seen exactly once
        seen_peers = [p for _, p in m0_pings]
        for sp in sat_peers:
            count = seen_peers.count(sp)
            assert count == 1, f"Peer {sp} seen {count} times, expected 1"


class TestPingExactlyOnceChain:
    """Chain: M0 → R1 → S0.  M0 discovers R1_sat and S0, each once."""

    def test_m0_sees_relay_and_leaf_once(self, chain):
        b = chain
        master_rec = defaultdict(list)
        _instrument_masters(b, master_rec)

        ping = _do_ping(b.get_master("M0"))
        fid = _flood_id(ping)

        r1_sat_peer = b.get_satellite("R1_sat").peer
        s0_peer = b.get_satellite("S0").peer

        m0_pings = [(f, p) for f, p in master_rec["M0"] if f == fid]
        seen_peers = [p for _, p in m0_pings]

        assert seen_peers.count(r1_sat_peer) == 1, \
            f"R1_sat seen {seen_peers.count(r1_sat_peer)} times at M0, expected 1"
        assert seen_peers.count(s0_peer) == 1, \
            f"S0 seen {seen_peers.count(s0_peer)} times at M0, expected 1"

    def test_s0_sees_master_ping_once(self, chain):
        b = chain
        sat_rec = defaultdict(list)
        _instrument_satellites(b, sat_rec)

        ping = _do_ping(b.get_master("M0"))
        fid = _flood_id(ping)
        m0_peer = b.get_master("M0").hm_protocol.peer

        s0_pings = [(f, p) for f, p in sat_rec["S0"] if f == fid and p == m0_peer]
        assert len(s0_pings) == 1, f"S0 should see M0 PING once, got {len(s0_pings)}"

    def test_relay_master_sees_leaf_once(self, chain):
        b = chain
        master_rec = defaultdict(list)
        _instrument_masters(b, master_rec)

        ping = _do_ping(b.get_master("M0"))
        fid = _flood_id(ping)
        s0_peer = b.get_satellite("S0").peer

        r1_pings = [(f, p) for f, p in master_rec["R1_master"] if f == fid and p == s0_peer]
        assert len(r1_pings) == 1, \
            f"R1_master should see S0 response once, got {len(r1_pings)}"


class TestPingExactlyOnceDeepChain:
    """Deep chain: M0 → R1 → R2 → S0.  All nodes discovered exactly once."""

    def test_m0_sees_all_three_once(self, deep_chain):
        b = deep_chain
        master_rec = defaultdict(list)
        _instrument_masters(b, master_rec)

        ping = _do_ping(b.get_master("M0"))
        fid = _flood_id(ping)

        expected_peers = {
            b.get_satellite("R1_sat").peer,
            b.get_satellite("R2_sat").peer,
            b.get_satellite("S0").peer,
        }

        m0_pings = [(f, p) for f, p in master_rec["M0"] if f == fid]
        seen_peers = [p for _, p in m0_pings]

        for ep in expected_peers:
            count = seen_peers.count(ep)
            assert count == 1, \
                f"Peer {ep} seen {count} times at M0, expected 1. All: {seen_peers}"

    def test_no_duplicates_anywhere(self, deep_chain):
        """No master receives the same (flood_id, peer) pair more than once."""
        b = deep_chain
        master_rec = defaultdict(list)
        _instrument_masters(b, master_rec)

        ping = _do_ping(b.get_master("M0"))
        fid = _flood_id(ping)

        for node_name, records in master_rec.items():
            flood_records = [(f, p) for f, p in records if f == fid]
            seen = set()
            for pair in flood_records:
                assert pair not in seen, \
                    f"Duplicate {pair} at {node_name}. All: {flood_records}"
                seen.add(pair)


class TestPingExactlyOnceDiamond:
    """Diamond: M0 → R1(S0,S1), R2(S2,S3).  Cross-branch PINGs seen once."""

    def test_m0_sees_all_six_once(self, diamond):
        """M0 discovers 2 relay sats + 4 leaf sats = 6 nodes, each once."""
        b = diamond
        master_rec = defaultdict(list)
        _instrument_masters(b, master_rec)

        ping = _do_ping(b.get_master("M0"))
        fid = _flood_id(ping)

        expected_peers = set()
        for name in ["R1_sat", "R2_sat", "S0", "S1", "S2", "S3"]:
            expected_peers.add(b.get_satellite(name).peer)

        m0_pings = [(f, p) for f, p in master_rec["M0"] if f == fid]
        seen_peers = [p for _, p in m0_pings]

        assert len(m0_pings) == 6, \
            f"M0 should see 6 responsive PINGs, got {len(m0_pings)}"

        for ep in expected_peers:
            count = seen_peers.count(ep)
            assert count == 1, \
                f"Peer {ep} seen {count} times at M0, expected 1"

    def test_no_duplicates_at_any_master(self, diamond):
        """No master in the diamond receives duplicate (flood_id, peer)."""
        b = diamond
        master_rec = defaultdict(list)
        _instrument_masters(b, master_rec)

        ping = _do_ping(b.get_master("M0"))
        fid = _flood_id(ping)

        for node_name, records in master_rec.items():
            flood_records = [(f, p) for f, p in records if f == fid]
            seen = set()
            for pair in flood_records:
                assert pair not in seen, \
                    f"Duplicate {pair} at {node_name}. All: {flood_records}"
                seen.add(pair)

    def test_cross_branch_pings_reach_siblings(self, diamond):
        """S0's responsive PING (via R1→M0→R2) reaches S2 and S3 exactly once."""
        b = diamond
        sat_rec = defaultdict(list)
        _instrument_satellites(b, sat_rec)

        ping = _do_ping(b.get_master("M0"))
        fid = _flood_id(ping)
        s0_peer = b.get_satellite("S0").peer

        # S2 should see S0's responsive PING exactly once (forwarded M0→R2→S2)
        s2_from_s0 = [(f, p) for f, p in sat_rec["S2"] if f == fid and p == s0_peer]
        assert len(s2_from_s0) == 1, \
            f"S2 should see S0's PING once, got {len(s2_from_s0)}"
