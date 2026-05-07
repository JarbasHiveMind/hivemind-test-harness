"""
Last Edit: Claude Sonnet 4.6 - 2026-03-09 - Motive: New file — generate topology and HiveMap PNG plots for documentation.

TS-PLOT-01..07 — Topology visualization tests.

These tests generate PNG plots that are checked in to
``hivemind-core/docs/img/`` so the Markdown documentation can reference them.

Run this file explicitly to regenerate the images::

    pytest hivemind-test-harness/tests/test_topology_plots.py -v

Each test:
  1. Builds the topology / runs a PING flood.
  2. Calls the plotting utilities.
  3. Asserts the file was written and is non-empty.

The plots are deterministic — re-running overwrites the same files.
"""

import os
import time
import uuid
from pathlib import Path

import pytest

from hivemind_bus_client.message import HiveMessage, HiveMessageType
from hivescope.topology import TopologyBuilder
from hivescope.topology_plot import (
    plot_topology_builder,
    plot_hive_mapper,
    plot_topology_and_discovery,
)

# Absolute path to hivemind-core/docs/img/ relative to this file.
_HERE = Path(__file__).parent
_WORKSPACE = _HERE.parent.parent          # HiveMind Workspace/
_IMG_DIR = _WORKSPACE / "hivemind-core" / "docs" / "img"


def _img(name: str) -> str:
    return str(_IMG_DIR / name)


def _make_ping(peer: str) -> HiveMessage:
    return HiveMessage(HiveMessageType.PROPAGATE, payload=HiveMessage(
        HiveMessageType.PING,
        payload={"ping_id": str(uuid.uuid4()), "timestamp": time.time(),
                 "peer": peer, "site_id": "hub"},
    ))


def _do_ping_and_plot(master_node, topology_builder, prefix: str,
                      layout_static: str = "spring",
                      layout_dynamic: str = "kamada_kawai"):
    """Run a PING flood, then plot both static wiring and live discovery."""
    ping_msg = _make_ping(master_node.hm_protocol.peer)
    ping_id = ping_msg.payload.payload["ping_id"]
    master_node.hm_protocol.hive_mapper.start_ping(ping_id)
    master_node.send_to_all(ping_msg)

    p1, p2 = plot_topology_and_discovery(
        topology_builder,
        master_node.hm_protocol.hive_mapper,
        dir_path=str(_IMG_DIR),
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

    def test_static_wiring_written(self):
        b = TopologyBuilder()
        b.add_master("M0")
        b.add_satellite("S0", upstream=b.get_master("M0"))
        b.start_all()
        try:
            path = plot_topology_builder(
                b, _img("minimal_static.png"),
                title="Minimal topology — M0 → S0",
                layout="spring",
            )
            _assert_png(path)
        finally:
            b.stop_all()

    def test_discovery_plot_written(self):
        b = TopologyBuilder()
        b.add_master("M0")
        b.add_satellite("S0", upstream=b.get_master("M0"))
        b.start_all()
        try:
            p1, p2 = _do_ping_and_plot(
                b.get_master("M0"), b,
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

    def test_static_and_discovery_written(self):
        b = TopologyBuilder()
        b.add_master("M0")
        for i in range(3):
            b.add_satellite(f"S{i}", upstream=b.get_master("M0"))
        b.start_all()
        try:
            p1, p2 = _do_ping_and_plot(
                b.get_master("M0"), b,
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

    def test_static_written(self):
        b = TopologyBuilder()
        b.add_master("M0")
        _, r1_master = b.add_relay("R1", upstream=b.get_master("M0"))
        b.add_satellite("S0", upstream=r1_master)
        b.start_all()
        try:
            path = plot_topology_builder(
                b, _img("chain_static.png"),
                title="Chain topology — M0 → R1 → S0",
                layout="spring",
            )
            _assert_png(path)
        finally:
            b.stop_all()

    def test_m0_discovery_written(self):
        b = TopologyBuilder()
        b.add_master("M0")
        _, r1_master = b.add_relay("R1", upstream=b.get_master("M0"))
        b.add_satellite("S0", upstream=r1_master)
        b.start_all()
        try:
            p1, p2 = _do_ping_and_plot(
                b.get_master("M0"), b,
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

    def test_static_written(self):
        b = TopologyBuilder()
        b.add_master("M0")
        _, r1_master = b.add_relay("R1", upstream=b.get_master("M0"))
        _, r2_master = b.add_relay("R2", upstream=r1_master)
        b.add_satellite("S0", upstream=r2_master)
        b.start_all()
        try:
            path = plot_topology_builder(
                b, _img("deep_chain_static.png"),
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
    def test_static_written(self, huge_hive_topology):
        path = plot_topology_builder(
            huge_hive_topology,
            _img("huge_hive_static.png"),
            title="Huge hive — M0 + 15 satellites",
            layout="spring",
        )
        _assert_png(path)

    @pytest.mark.slow
    def test_discovery_written(self, huge_hive_topology):
        b = huge_hive_topology
        m0 = b.get_master("M0")
        p1, p2 = _do_ping_and_plot(m0, b, prefix="huge_hive",
                                   layout_dynamic="spring")
        _assert_png(p1)
        _assert_png(p2)


# ---------------------------------------------------------------------------
# TS-PLOT-06 — chaotic hive topology
# ---------------------------------------------------------------------------

class TestPlotChaoticHive:
    """TS-PLOT-06 — multi-level irregular tree."""

    @pytest.mark.slow
    def test_static_written(self, chaotic_hive_topology):
        path = plot_topology_builder(
            chaotic_hive_topology,
            _img("chaotic_hive_static.png"),
            title="Chaotic hive — M0 + nested relays",
            layout="spring",
        )
        _assert_png(path)

    @pytest.mark.slow
    def test_m0_discovery_written(self, chaotic_hive_topology):
        b = chaotic_hive_topology
        m0 = b.get_master("M0")
        p1, p2 = _do_ping_and_plot(m0, b, prefix="chaotic_hive_m0")
        _assert_png(p1)
        _assert_png(p2)

    @pytest.mark.slow
    def test_each_master_discovery_written(self, chaotic_hive_topology):
        """Generate one discovery plot per master node."""
        b = chaotic_hive_topology
        for master in b.masters:
            safe_name = master.name.replace("_", "")
            p1, p2 = _do_ping_and_plot(
                master, b,
                prefix=f"chaotic_{safe_name}",
            )
            _assert_png(p2)


# ---------------------------------------------------------------------------
# TS-PLOT-07 — empty mapper produces placeholder image
# ---------------------------------------------------------------------------

class TestPlotEmptyMapper:
    """TS-PLOT-07 — plot_hive_mapper with empty mapper writes a placeholder PNG."""

    def test_empty_mapper_placeholder(self):
        from hivemind_core.hive_map import HiveMapper
        mapper = HiveMapper()
        path = _img("empty_mapper.png")
        result = plot_hive_mapper(mapper, path, title="Empty mapper test")
        _assert_png(result)
