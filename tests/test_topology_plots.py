"""
Last Edit: Claude Sonnet 4.6 - 2026-03-09 - Motive: New file — generate topology and HiveMap PNG plots for documentation.

TS-PLOT-01..07 — Topology visualization tests.

These tests prove the plotting utilities render every topology in the
catalogue. Every PNG goes to pytest's ``tmp_path``: a test must never write
into a sibling checkout (the old target was ``../hivemind-core/docs/img/``,
which does not exist on CI and is not this repo's to modify).

To keep a rendered image, copy it out of the reported ``tmp_path`` after a run
with ``--basetemp``.

Each test:
  1. Builds the topology / runs a PING flood.
  2. Calls the plotting utilities.
  3. Asserts the file was written and is non-empty.
"""

import os
import time
import uuid

import pytest

from hivemind_bus_client.message import HiveMessage, HiveMessageType
from hivescope.topology import TopologyBuilder
from hivescope.topology_plot import (
    plot_topology_builder,
    plot_hive_mapper,
    plot_topology_and_discovery,
)

# hivescope#46 fixed plot_hive_mapper to read NodeInfo.latency_ms (rtt_ms
# never existed); the strict xfail that used to sit here XPASS-failed the
# moment the fix released — these are plain regression tests now.

@pytest.fixture
def img_dir(tmp_path):
    """Per-test output directory for the rendered PNGs."""
    out = tmp_path / "img"
    out.mkdir()
    return out


def _make_ping(peer: str) -> HiveMessage:
    # The discovery protocol keys a flood on ``flood_id``. The old ``ping_id``
    # key meant HiveMapper never correlated the responses, so every discovery
    # plot drew an empty hive.
    return HiveMessage(HiveMessageType.PROPAGATE, payload=HiveMessage(
        HiveMessageType.PING,
        payload={"flood_id": str(uuid.uuid4()), "timestamp": time.time(),
                 "peer": peer, "site_id": "hub"},
    ))


def _do_ping_and_plot(master_node, topology_builder, img_dir, prefix: str,
                      layout_static: str = "spring",
                      layout_dynamic: str = "kamada_kawai"):
    """Run a PING flood, then plot both static wiring and live discovery."""
    ping_msg = _make_ping(master_node.hm_protocol.peer)
    flood_id = ping_msg.payload.payload["flood_id"]
    mapper = master_node.hm_protocol.hive_mapper
    mapper.start_ping(flood_id)
    # The master already announced by sending; don't let it re-announce when
    # the responsive PINGs come back with the same flood_id.
    master_node.hm_protocol._seen_flood_ids.add(flood_id)
    master_node.send_to_all(ping_msg)

    assert mapper.nodes, (
        f"PING flood from {master_node.name} discovered no nodes — the "
        "discovery plot would be empty and prove nothing"
    )

    p1, p2 = plot_topology_and_discovery(
        topology_builder,
        mapper,
        dir_path=str(img_dir),
        prefix=prefix,
        root_peer=master_node.hm_protocol.peer,
        layout_static=layout_static,
        layout_dynamic=layout_dynamic,
    )
    return p1, p2


def _assert_png(path: str):
    assert os.path.isfile(path), f"Expected PNG at {path!r}"
    assert os.path.getsize(path) > 1000, \
        f"PNG at {path!r} is suspiciously small ({os.path.getsize(path)} bytes)"


# ---------------------------------------------------------------------------
# TS-PLOT-01 — minimal topology
# ---------------------------------------------------------------------------

class TestPlotMinimalTopology:
    """TS-PLOT-01 — 1 master, 1 satellite."""

    def test_static_wiring_written(self, img_dir):
        b = TopologyBuilder()
        b.add_master("M0")
        b.add_satellite("S0", upstream=b.get_master("M0"))
        b.start_all()
        try:
            path = plot_topology_builder(
                b, str(img_dir / "minimal_static.png"),
                title="Minimal topology — M0 → S0",
                layout="spring",
            )
            _assert_png(path)
        finally:
            b.stop_all()

    def test_discovery_plot_written(self, img_dir):
        b = TopologyBuilder()
        b.add_master("M0")
        b.add_satellite("S0", upstream=b.get_master("M0"))
        b.start_all()
        try:
            p1, p2 = _do_ping_and_plot(
                b.get_master("M0"), b, img_dir,
                prefix="minimal",
            )
            _assert_png(p1)
            _assert_png(p2)
        finally:
            b.stop_all()


# ---------------------------------------------------------------------------
# TS-PLOT-02 — star topology
# ---------------------------------------------------------------------------

class TestPlotStarTopology:
    """TS-PLOT-02 — 1 master, 3 satellites."""

    def test_static_and_discovery_written(self, img_dir):
        b = TopologyBuilder()
        b.add_master("M0")
        for i in range(3):
            b.add_satellite(f"S{i}", upstream=b.get_master("M0"))
        b.start_all()
        try:
            p1, p2 = _do_ping_and_plot(
                b.get_master("M0"), b, img_dir,
                prefix="star",
                layout_static="spring",
            )
            _assert_png(p1)
            _assert_png(p2)
        finally:
            b.stop_all()


# ---------------------------------------------------------------------------
# TS-PLOT-03 — chain topology
# ---------------------------------------------------------------------------

class TestPlotChainTopology:
    """TS-PLOT-03 — M0 → relay R1 → S0."""

    def test_static_written(self, img_dir):
        b = TopologyBuilder()
        b.add_master("M0")
        r1_master = b.add_relay("R1", upstream=b.get_master("M0")).listener
        b.add_satellite("S0", upstream=r1_master)
        b.start_all()
        try:
            path = plot_topology_builder(
                b, str(img_dir / "chain_static.png"),
                title="Chain topology — M0 → R1 → S0",
                layout="spring",
            )
            _assert_png(path)
        finally:
            b.stop_all()

    def test_m0_discovery_written(self, img_dir):
        b = TopologyBuilder()
        b.add_master("M0")
        r1_master = b.add_relay("R1", upstream=b.get_master("M0")).listener
        b.add_satellite("S0", upstream=r1_master)
        b.start_all()
        try:
            p1, p2 = _do_ping_and_plot(
                b.get_master("M0"), b, img_dir,
                prefix="chain_m0",
            )
            _assert_png(p1)
            _assert_png(p2)
        finally:
            b.stop_all()


# ---------------------------------------------------------------------------
# TS-PLOT-04 — deep chain topology
# ---------------------------------------------------------------------------

class TestPlotDeepChainTopology:
    """TS-PLOT-04 — M0 → R1 → R2 → S0 (depth 3)."""

    def test_static_written(self, img_dir):
        b = TopologyBuilder()
        b.add_master("M0")
        r1_master = b.add_relay("R1", upstream=b.get_master("M0")).listener
        r2_master = b.add_relay("R2", upstream=r1_master).listener
        b.add_satellite("S0", upstream=r2_master)
        b.start_all()
        try:
            path = plot_topology_builder(
                b, str(img_dir / "deep_chain_static.png"),
                title="Deep chain — M0 → R1 → R2 → S0",
                layout="spring",
            )
            _assert_png(path)
        finally:
            b.stop_all()


# ---------------------------------------------------------------------------
# TS-PLOT-05 — huge hive topology
# ---------------------------------------------------------------------------

class TestPlotHugeHive:
    """TS-PLOT-05 — 1 master, 15 satellites."""

    @pytest.mark.slow
    def test_static_written(self, huge_hive_topology, img_dir):
        path = plot_topology_builder(
            huge_hive_topology,
            str(img_dir / "huge_hive_static.png"),
            title="Huge hive — M0 + 15 satellites",
            layout="spring",
        )
        _assert_png(path)

    @pytest.mark.slow
    def test_discovery_written(self, huge_hive_topology, img_dir):
        b = huge_hive_topology
        m0 = b.get_master("M0")
        p1, p2 = _do_ping_and_plot(m0, b, img_dir, prefix="huge_hive",
                                   layout_dynamic="spring")
        _assert_png(p1)
        _assert_png(p2)


# ---------------------------------------------------------------------------
# TS-PLOT-06 — chaotic hive topology
# ---------------------------------------------------------------------------

class TestPlotChaoticHive:
    """TS-PLOT-06 — multi-level irregular tree."""

    @pytest.mark.slow
    def test_static_written(self, chaotic_hive_topology, img_dir):
        path = plot_topology_builder(
            chaotic_hive_topology,
            str(img_dir / "chaotic_hive_static.png"),
            title="Chaotic hive — M0 + nested relays",
            layout="spring",
        )
        _assert_png(path)

    @pytest.mark.slow
    def test_m0_discovery_written(self, chaotic_hive_topology, img_dir):
        b = chaotic_hive_topology
        m0 = b.get_master("M0")
        p1, p2 = _do_ping_and_plot(m0, b, img_dir, prefix="chaotic_hive_m0")
        _assert_png(p1)
        _assert_png(p2)

    @pytest.mark.slow
    def test_each_master_discovery_written(self, chaotic_hive_topology, img_dir):
        """Generate one discovery plot per master node."""
        b = chaotic_hive_topology
        for master in b.masters:
            safe_name = master.name.replace("_", "")
            p1, p2 = _do_ping_and_plot(
                master, b, img_dir,
                prefix=f"chaotic_{safe_name}",
            )
            _assert_png(p2)


# ---------------------------------------------------------------------------
# TS-PLOT-07 — empty mapper produces placeholder image
# ---------------------------------------------------------------------------

class TestPlotEmptyMapper:
    """TS-PLOT-07 — plot_hive_mapper with empty mapper writes a placeholder PNG."""

    def test_empty_mapper_placeholder(self, img_dir):
        from hivemind_bus_client.hive_map import HiveMapper
        mapper = HiveMapper()
        path = str(img_dir / "empty_mapper.png")
        result = plot_hive_mapper(mapper, path, title="Empty mapper test")
        _assert_png(result)
