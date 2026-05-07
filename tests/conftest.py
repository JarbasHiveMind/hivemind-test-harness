"""
Last Edit: Claude Sonnet 4.6 - 2026-03-09 - Motive: Updated terminology — relay = dual-role node (master + satellite sharing one agent bus).

Shared pytest fixtures — one per topology definition.

Node roles
──────────
  Master    — any node running hivemind-core (``HiveMindListenerProtocol``).
              Accepts inbound satellite connections.
  Satellite — any node connected to a master via hivemind-bus-client
              (``HiveMindSlaveProtocol``).
  Dual-role — a node that is simultaneously a satellite (connected upstream)
              AND a master (accepting downstream satellites), sharing one
              agent bus/AI brain.  The harness calls this a **relay** and
              models it as a :class:`~hivescope.topology.RelayNode`.

Topology catalogue
──────────────────
  T1  minimal_topology        1 master, 1 satellite
  T2  star_topology           1 master, N satellites (default 3)
  T2a admin_star_topology     T2 with S0 granted is_admin
  T3  chain_topology          M0 ─── R1 (dual-role) ─── S0
                               R1 is connected to M0 as a satellite AND
                               accepts S0 as its own satellite.
  T3a deep_chain_topology     M0 ─── R1 ─── R2 ─── S0 (depth 3)
  T4  huge_hive_topology      10 dual-role relays each with a seeded-random
                               number of satellites (slow)
  T5  chaotic_hive_topology   multi-level irregular tree (slow):
                               M0
                               ├─ R1 (dual-role) ── S0, S1, S2
                               ├─ R2 (dual-role) ── S3, R3 (dual-role) ── S4, S5
                               └─ S6 (direct satellite of M0)
  T6  asymmetric_hive_topology one branch 10× deeper than all others (slow):
                               M0
                               ├─ long arm: RA0→RA1→…→RA9→S_deep  (depth 10)
                               └─ short arms: S_short0/1/2         (depth 1)
"""
import random
import pytest
from hivescope.topology import TopologyBuilder


# ---------------------------------------------------------------------------
# T1 — minimal
# ---------------------------------------------------------------------------

@pytest.fixture
def minimal_topology():
    """T1: 1 master, 1 satellite."""
    b = TopologyBuilder()
    b.add_master("M0")
    b.add_satellite("S0", upstream=b.get_master("M0"))
    b.start_all()
    yield b
    b.stop_all()


# ---------------------------------------------------------------------------
# T2 — star
# ---------------------------------------------------------------------------

@pytest.fixture
def star_topology(request):
    """T2: 1 master, N satellites (default 3). Override via @pytest.mark.parametrize."""
    n = getattr(request, "param", 3)
    b = TopologyBuilder()
    b.add_master("M0")
    for i in range(n):
        b.add_satellite(f"S{i}", upstream=b.get_master("M0"))
    b.start_all()
    yield b
    b.stop_all()


@pytest.fixture
def admin_star_topology():
    """T2 variant: 1 master, 3 satellites where S0 is admin."""
    b = TopologyBuilder()
    b.add_master("M0")
    b.add_satellite("S0", upstream=b.get_master("M0"), is_admin=True)
    b.add_satellite("S1", upstream=b.get_master("M0"))
    b.add_satellite("S2", upstream=b.get_master("M0"))
    b.start_all()
    yield b
    b.stop_all()


# ---------------------------------------------------------------------------
# T3 — chain
# ---------------------------------------------------------------------------

@pytest.fixture
def chain_topology():
    """T3: M0 → relay R1 (satellite+master) → S0."""
    b = TopologyBuilder()
    b.add_master("M0")
    _, relay_master = b.add_relay("R1", upstream=b.get_master("M0"))
    b.add_satellite("S0", upstream=relay_master)
    b.start_all()
    yield b
    b.stop_all()


@pytest.fixture
def deep_chain_topology():
    """T3 variant: M0 → R1 → R2 → S0 (depth 3)."""
    b = TopologyBuilder()
    b.add_master("M0")
    _, r1_master = b.add_relay("R1", upstream=b.get_master("M0"))
    _, r2_master = b.add_relay("R2", upstream=r1_master)
    b.add_satellite("S0", upstream=r2_master)
    b.start_all()
    yield b
    b.stop_all()


# ---------------------------------------------------------------------------
# T4 — huge hive  (marked slow — excluded from fast runs with -m "not slow")
# ---------------------------------------------------------------------------

# Fixed seed so the topology is deterministic across runs.
_HUGE_HIVE_SEED = 2026

# Satellite counts per relay master (seeded random, range 2-6).
_rng = random.Random(_HUGE_HIVE_SEED)
_HUGE_HIVE_COUNTS = [_rng.randint(2, 6) for _ in range(10)]
# e.g. [5, 3, 4, 2, 6, 3, 5, 4, 2, 3]  → 37 total leaf satellites


@pytest.fixture
def huge_hive_topology():
    """T4: 10 relay masters, each with a seeded-random number of satellites (2–6).

    Structure::

        M0  (root master)
        ├─ RM0_sat → M0   relay 0, serves COUNT[0] satellites
        ├─ RM1_sat → M0   relay 1, serves COUNT[1] satellites
        ...
        └─ RM9_sat → M0   relay 9, serves COUNT[9] satellites

    Total leaf satellites: sum(_HUGE_HIVE_COUNTS) = 37 (seed=2026).
    Use ``-m "not slow"`` to skip in quick CI runs.
    """
    b = TopologyBuilder()
    b.add_master("M0")

    sat_idx = 0
    for relay_idx, n_sats in enumerate(_HUGE_HIVE_COUNTS):
        _, rm = b.add_relay(f"RM{relay_idx}", upstream=b.get_master("M0"))
        for _ in range(n_sats):
            b.add_satellite(f"HS{sat_idx}", upstream=rm)
            sat_idx += 1

    b.start_all()
    yield b
    b.stop_all()


def huge_hive_total_satellites() -> int:
    """Return the expected number of leaf satellites in huge_hive_topology."""
    return sum(_HUGE_HIVE_COUNTS)


# ---------------------------------------------------------------------------
# T5 — chaotic hive  (marked slow)
# ---------------------------------------------------------------------------

@pytest.fixture
def chaotic_hive_topology():
    """T5: multi-level irregular tree — simulates a real-world mesh.

    Structure (→ = satellite-of)::

        M0  (root master)
        ├─ R1_sat → M0          (relay R1's satellite side)
        │  R1_master serves:
        │   ├─ S0
        │   ├─ S1
        │   └─ S2
        ├─ R2_sat → M0          (relay R2's satellite side)
        │  R2_master serves:
        │   ├─ S3
        │   └─ R3_sat → R2_master   (nested relay R3)
        │      R3_master serves:
        │       ├─ S4
        │       └─ S5
        └─ S6  (direct satellite of M0)

    Total: 1 root master + 3 relay masters + 7 leaf satellites = 11 nodes.
    """
    b = TopologyBuilder()
    b.add_master("M0")

    # R1: relay off M0, serves S0, S1, S2
    _, r1_master = b.add_relay("R1", upstream=b.get_master("M0"))
    b.add_satellite("S0", upstream=r1_master)
    b.add_satellite("S1", upstream=r1_master)
    b.add_satellite("S2", upstream=r1_master)

    # R2: relay off M0, serves S3 and a nested relay R3
    _, r2_master = b.add_relay("R2", upstream=b.get_master("M0"))
    b.add_satellite("S3", upstream=r2_master)

    # R3: nested relay off R2, serves S4, S5
    _, r3_master = b.add_relay("R3", upstream=r2_master)
    b.add_satellite("S4", upstream=r3_master)
    b.add_satellite("S5", upstream=r3_master)

    # S6: direct satellite of root M0
    b.add_satellite("S6", upstream=b.get_master("M0"))

    b.start_all()
    yield b
    b.stop_all()


# ---------------------------------------------------------------------------
# T6 — asymmetric hive  (marked slow)
# ---------------------------------------------------------------------------

@pytest.fixture
def asymmetric_hive_topology():
    """T6: one branch 10× deeper than all others.

    Structure::

        M0  (root master)
        ├─ long arm: RA0 → RA1 → RA2 → … → RA9 → S_deep   (depth 10)
        └─ short arms: S_short0, S_short1, S_short2          (depth 1)

    The *long arm* is a chain of 10 relay nodes.  Each relay master feeds
    only one child, except the last one which feeds the single leaf
    ``S_deep``.

    The *short arms* are three direct satellites of M0.

    This topology is useful for verifying that:
      - deeply-nested leaves can still reach M0 via relay chain (BUS /
        ESCALATE / PROPAGATE tests)
      - M0's PING flood only reaches depth-1 direct children, not S_deep
      - each relay's PING reaches only its own immediate child
    """
    _DEPTH = 10
    b = TopologyBuilder()
    b.add_master("M0")

    # Long arm: chain of DEPTH relays
    current_master = b.get_master("M0")
    for i in range(_DEPTH):
        _, current_master = b.add_relay(f"RA{i}", upstream=current_master)
    b.add_satellite("S_deep", upstream=current_master)

    # Short arms: 3 direct satellites of M0
    for i in range(3):
        b.add_satellite(f"S_short{i}", upstream=b.get_master("M0"))

    b.start_all()
    yield b
    b.stop_all()


# ---------------------------------------------------------------------------
# Shared E2E helpers — skill IDs and utilities
# ---------------------------------------------------------------------------

SKILL_HELLO = "ovos-skill-hello-world.openvoiceos"
SKILL_DATETIME = "ovos-skill-date-time.openvoiceos"
SKILL_PERSONAL = "ovos-skill-personal.openvoiceos"
SKILL_NAPTIME = "ovos-skill-naptime.openvoiceos"
SKILL_FALLBACK = "ovos-skill-fallback-unknown.openvoiceos"
SKILL_VOLUME = "ovos-skill-volume.openvoiceos"
SKILL_EASTER_EGGS = "ovos-skill-easter-eggs.openvoiceos"
SKILL_SPELLING = "ovos-skill-spelling.openvoiceos"
SKILL_PARROT = "ovos-skill-parrot.openvoiceos"
SKILL_IP = "ovos-skill-ip.openvoiceos"
SKILL_COUNT = "ovos-skill-count.openvoiceos"
SKILL_RANDOMNESS = "ovos-skill-randomness.openvoiceos"
SKILL_DICTATION = "ovos-skill-dictation.openvoiceos"
SKILL_TUNEIN = "ovos-skill-tunein.openvoiceos"


def skill_missing(*skill_ids: str) -> bool:
    """Return True if ANY of the given skills are not installed."""
    try:
        from ovos_plugin_manager.skills import find_skill_plugins
        plugins = find_skill_plugins()
        return any(sid not in plugins for sid in skill_ids)
    except Exception:
        return True


def make_utterance(text: str, pipeline: list, session_id: str,
                   lang: str = "en-US") -> "Message":
    """Build a recognizer_loop:utterance Message with a specific pipeline."""
    from ovos_bus_client.message import Message
    from ovos_bus_client.session import Session
    sess = Session(session_id)
    sess.lang = lang
    sess.pipeline = pipeline
    return Message(
        "recognizer_loop:utterance",
        {"utterances": [text], "lang": lang},
        {"session": sess.serialize(), "source": "sat", "destination": "master"},
    )


def assert_types_in_order(messages: list, *expected_types: str) -> None:
    """Assert every expected_type appears in messages in order."""
    types = [m.msg_type for m in messages]
    pos = 0
    for t in expected_types:
        found = next((i for i in range(pos, len(types)) if types[i] == t), None)
        assert found is not None, (
            f"Expected message type '{t}' not found after position {pos}.\n"
            f"Captured sequence: {types}"
        )
        pos = found + 1


def wait_for_satellite_message(satellite, msg_type: str, timeout: float = 10.0):
    """Block until msg_type arrives on satellite.internal_bus. Returns the message or None."""
    import threading
    result = []
    event = threading.Event()

    def _on_msg(msg):
        result.append(msg)
        event.set()

    satellite.internal_bus.once(msg_type, _on_msg)
    event.wait(timeout=timeout)
    return result[0] if result else None
