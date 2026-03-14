"""
Last Edit: Claude Sonnet 4.6 - 2026-03-09 - Motive: New module — topology visualization utilities for the test harness.

Topology visualization for the HiveMind test harness.

Two public functions:

  ``plot_topology_builder(builder, path, ...)``
      Renders the *static* wiring of a :class:`TopologyBuilder` — shows who
      is a master, who is a relay, who is a leaf satellite, and which node
      connects to which.

  ``plot_hive_mapper(hive_mapper, path, ...)``
      Renders the *live* discovery graph built from PING/PONG responses stored
      in a :class:`~hivemind_core.hive_map.HiveMapper` instance.

Both functions save a PNG file and return the absolute path to it.

Usage::

    from hivemind_test_harness.topology import TopologyBuilder
    from hivemind_test_harness.topology_plot import plot_topology_builder

    b = TopologyBuilder()
    b.add_master("M0")
    b.add_satellite("S0", upstream=b.get_master("M0"))
    b.start_all()
    plot_topology_builder(b, "docs/img/minimal.png", title="Minimal topology")

Requirements: ``matplotlib``, ``networkx``  (``uv pip install matplotlib networkx``).
"""

import os
from typing import Dict, List, Optional, Tuple

import matplotlib
matplotlib.use("Agg")  # non-interactive backend — safe for CI and test runners

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import networkx as nx

# ---------------------------------------------------------------------------
# Palette and shared drawing helpers
# ---------------------------------------------------------------------------

_PALETTE: Dict[str, str] = {
    "master":    "#4A90D9",   # blue
    "relay_sat": "#E67E22",   # orange  (satellite side of relay)
    "relay_mst": "#D35400",   # darker orange (master side of relay)
    "satellite": "#27AE60",   # green
    "root":      "#C0392B",   # red (PING originator / self in hive-map)
    "edge":      "#444444",
    "bg":        "#F8F8F8",
}

_NODE_SIZE = 1800
_FONT_SIZE = 7.5


def _fig_ax(title: str) -> Tuple[plt.Figure, plt.Axes]:
    fig, ax = plt.subplots(figsize=(14, 9))
    fig.patch.set_facecolor(_PALETTE["bg"])
    ax.set_facecolor(_PALETTE["bg"])
    ax.set_title(title, fontsize=13, fontweight="bold", pad=16)
    ax.axis("off")
    return fig, ax


def _choose_layout(G: nx.DiGraph, layout: str) -> Dict:
    if layout == "spring":
        return nx.spring_layout(G, seed=42, k=2.8)
    if layout == "kamada_kawai":
        try:
            return nx.kamada_kawai_layout(G)
        except Exception:
            return nx.spring_layout(G, seed=42, k=2.8)
    if layout == "shell":
        return nx.shell_layout(G)
    if layout == "circular":
        return nx.circular_layout(G)
    if layout == "spectral":
        try:
            return nx.spectral_layout(G)
        except Exception:
            return nx.spring_layout(G, seed=42)
    return nx.spring_layout(G, seed=42, k=2.8)


def _draw_graph(
    G: nx.DiGraph,
    node_colours: List[str],
    node_labels: Dict[str, str],
    legend_patches: List[mpatches.Patch],
    title: str,
    path: str,
    layout: str,
) -> str:
    """Draw *G* and save to *path*. Returns the absolute path written."""
    fig, ax = _fig_ax(title)
    pos = _choose_layout(G, layout)

    nx.draw_networkx_nodes(
        G, pos, ax=ax,
        node_color=node_colours,
        node_size=_NODE_SIZE,
        alpha=0.93,
        linewidths=1.2,
        edgecolors="#222222",
    )
    nx.draw_networkx_labels(
        G, pos, labels=node_labels, ax=ax,
        font_size=_FONT_SIZE,
        font_family="monospace",
        font_weight="bold",
    )
    nx.draw_networkx_edges(
        G, pos, ax=ax,
        edge_color=_PALETTE["edge"],
        arrows=True,
        arrowsize=20,
        arrowstyle="-|>",
        connectionstyle="arc3,rad=0.07",
        width=1.8,
        alpha=0.85,
    )

    if legend_patches:
        ax.legend(handles=legend_patches, loc="upper left",
                  fontsize=9, framealpha=0.88)

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return os.path.abspath(path)


# ---------------------------------------------------------------------------
# plot_topology_builder
# ---------------------------------------------------------------------------

def plot_topology_builder(
    builder,
    path: str,
    title: str = "HiveMind Topology",
    layout: str = "spring",
) -> str:
    """Render the static wiring of a :class:`TopologyBuilder` to a PNG.

    Node colours:
      * **blue** — plain master
      * **orange** — relay node (satellite side shown as light orange, master
        side as darker orange)
      * **green** — leaf satellite

    Edges point **master → satellite** (downstream direction).

    Args:
        builder: A :class:`~hivemind_test_harness.topology.TopologyBuilder`
                 instance.  The topology does *not* need to be started.
        path:    Destination file path (e.g. ``"docs/img/star.png"``).
                 Parent directories are created automatically.
        title:   Plot title shown at the top.
        layout:  NetworkX layout name: ``"spring"`` (default), ``"kamada_kawai"``,
                 ``"shell"``, ``"circular"``, ``"spectral"``.

    Returns:
        Absolute path to the written PNG file.
    """
    master_names: set = {m.name for m in builder.masters}
    sat_names: set    = {s.name for s in builder.satellites}

    # Identify relay base names: any "X_sat" / "X_master" pair.
    relay_bases: set = set()
    for n in master_names | sat_names:
        if n.endswith("_sat"):
            base = n[:-4]
            if f"{base}_master" in master_names:
                relay_bases.add(base)

    def _role(name: str) -> str:
        base = name.replace("_sat", "").replace("_master", "")
        if base in relay_bases:
            return "relay_sat" if name.endswith("_sat") else "relay_mst"
        return "master" if name in master_names else "satellite"

    def _short_label(name: str) -> str:
        if name.endswith("_sat"):
            return name[:-4] + "\n↑(sat)"
        if name.endswith("_master"):
            return name[:-7] + "\n↓(mst)"
        return name

    G = nx.DiGraph()
    for n in master_names | sat_names:
        G.add_node(n, role=_role(n))

    # Edges: master_name → sat_name (from builder._connections)
    for sat_name, master_name, _ in builder._connections:
        G.add_edge(master_name, sat_name)

    node_colours = [_PALETTE.get(_role(n), _PALETTE["satellite"])
                    for n in G.nodes()]
    node_labels  = {n: _short_label(n) for n in G.nodes()}

    legend_patches = [
        mpatches.Patch(color=_PALETTE["master"],    label="Master"),
        mpatches.Patch(color=_PALETTE["relay_sat"], label="Relay (sat side)"),
        mpatches.Patch(color=_PALETTE["relay_mst"], label="Relay (master side)"),
        mpatches.Patch(color=_PALETTE["satellite"], label="Satellite"),
    ]

    return _draw_graph(G, node_colours, node_labels, legend_patches,
                       title, path, layout)


# ---------------------------------------------------------------------------
# plot_hive_mapper
# ---------------------------------------------------------------------------

def plot_hive_mapper(
    hive_mapper,
    path: str,
    title: str = "HiveMap Discovery",
    root_peer: Optional[str] = None,
    layout: str = "kamada_kawai",
) -> str:
    """Render the live discovery graph from a :class:`~hivemind_core.hive_map.HiveMapper`.

    Each node discovered via PONG becomes a graph vertex; edges come from
    the route hops embedded in each PONG.  The *root_peer* (PING originator)
    is highlighted in red.  RTT in milliseconds is shown as a node annotation
    when available.

    Args:
        hive_mapper: A :class:`~hivemind_core.hive_map.HiveMapper` instance.
        path:        Destination file path.
        title:       Plot title.
        root_peer:   Peer ID of the local (PING-originating) node; highlighted
                     in red and labelled ``[self]``.
        layout:      NetworkX layout: ``"kamada_kawai"`` (default),
                     ``"spring"``, ``"shell"``, ``"circular"``.

    Returns:
        Absolute path to the written PNG file.
    """
    # Build graph from mapper state.
    G = nx.DiGraph()

    for peer, info in hive_mapper.nodes.items():
        rtt_str = f"  {info.rtt_ms:.0f}ms" if info.rtt_ms is not None else ""
        site_str = f"\n({info.site_id})" if info.site_id else ""
        label = f"{peer}{site_str}{rtt_str}"
        if peer == root_peer:
            label = f"[self]\n{label}"
        G.add_node(peer, label=label)

    for src, targets in hive_mapper.edges.items():
        for tgt in targets:
            if tgt not in G:
                G.add_node(tgt, label=tgt)
            G.add_edge(src, tgt)

    if not G.nodes():
        # Empty mapper — write a blank placeholder.
        fig, ax = plt.subplots(figsize=(6, 3))
        fig.patch.set_facecolor(_PALETTE["bg"])
        ax.text(0.5, 0.5, "No nodes discovered",
                ha="center", va="center", fontsize=14, color="#888888")
        ax.axis("off")
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        fig.savefig(path, dpi=130, bbox_inches="tight")
        plt.close(fig)
        return os.path.abspath(path)

    node_colours = [
        _PALETTE["root"] if n == root_peer else _PALETTE["satellite"]
        for n in G.nodes()
    ]
    node_labels = {n: G.nodes[n].get("label", n) for n in G.nodes()}

    legend_patches = [
        mpatches.Patch(color=_PALETTE["root"],      label="Self (PING origin)"),
        mpatches.Patch(color=_PALETTE["satellite"], label="Discovered node"),
    ]

    return _draw_graph(G, node_colours, node_labels, legend_patches,
                       title, path, layout)


# ---------------------------------------------------------------------------
# plot_topology_and_discovery  (convenience wrapper)
# ---------------------------------------------------------------------------

def plot_topology_and_discovery(
    builder,
    hive_mapper,
    dir_path: str,
    prefix: str = "topology",
    root_peer: Optional[str] = None,
    layout_static: str = "spring",
    layout_dynamic: str = "kamada_kawai",
) -> Tuple[str, str]:
    """Generate both a static wiring plot and a live discovery plot.

    Args:
        builder:        :class:`~hivemind_test_harness.topology.TopologyBuilder`.
        hive_mapper:    :class:`~hivemind_core.hive_map.HiveMapper` after PING flood.
        dir_path:       Output directory (created if absent).
        prefix:         Filename prefix; produces ``<prefix>_static.png`` and
                        ``<prefix>_discovery.png``.
        root_peer:      Highlighted in discovery plot as ``[self]``.
        layout_static:  Layout for static plot.
        layout_dynamic: Layout for discovery plot.

    Returns:
        Tuple of ``(static_path, discovery_path)`` as absolute paths.
    """
    static_path    = os.path.join(dir_path, f"{prefix}_static.png")
    discovery_path = os.path.join(dir_path, f"{prefix}_discovery.png")

    p1 = plot_topology_builder(
        builder, static_path,
        title=f"{prefix} — static wiring",
        layout=layout_static,
    )
    p2 = plot_hive_mapper(
        hive_mapper, discovery_path,
        title=f"{prefix} — PING/PONG discovery",
        root_peer=root_peer,
        layout=layout_dynamic,
    )
    return p1, p2
