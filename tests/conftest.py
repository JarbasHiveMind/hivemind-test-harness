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
import socket
import time
import pytest
from hivescope.topology import TopologyBuilder


# ---------------------------------------------------------------------------
# Timing helpers — poll-until-condition instead of fixed sleeps
# ---------------------------------------------------------------------------

class PollTimeout(AssertionError):
    """Raised by :func:`poll_until` when the predicate never becomes true.

    A subclass of ``AssertionError`` so a timed-out wait fails the test loudly
    with a real assertion rather than silently passing on a slept-through
    fixed delay.
    """


def poll_until(predicate, timeout: float = 10.0, interval: float = 0.02,
               message: str = "condition not met within timeout"):
    """Poll ``predicate`` until it returns a truthy value or ``timeout`` elapses.

    Returns the truthy value the predicate produced (so callers can poll for a
    message and use it). Raises :class:`PollTimeout` on expiry.

    This replaces ``time.sleep(<fixed>)`` waits: a fast machine no longer burns
    the whole delay, and a slow one no longer flakes because the fixed delay was
    a hair too short. The predicate is re-checked immediately once before the
    first sleep so an already-satisfied condition returns with no latency.
    """
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() >= deadline:
            raise PollTimeout(message)
        time.sleep(interval)


def free_port(host: str = "127.0.0.1") -> int:
    """Return a currently-free TCP port by binding to port 0 and reading it back.

    Each fixture that needs a listening port should allocate one through this so
    two topologies started in parallel (``pytest -n auto``) never collide on a
    hardcoded port. There is an unavoidable bind/close race, but the window is
    microseconds and far smaller than the collision rate of a fixed constant.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, 0))
        return s.getsockname()[1]
    finally:
        s.close()


@pytest.fixture
def poll():
    """Expose :func:`poll_until` as a fixture for tests that prefer injection."""
    return poll_until

# The in-process simulator (nodes, fixtures, assertions, scenarios) lives in
# hivescope. Register its pytest fixtures (topology, master_node, satellite_node,
# admin_satellite, restricted_satellite) so they are available repo-wide.
pytest_plugins = ["hivescope.pytest_fixtures"]

# Standard satellite→master voice/audio message types a normal satellite is
# granted. hivemind-core is deny-by-default / whitelist-only: a satellite that
# is not granted ``recognizer_loop:utterance`` has its utterances rejected at
# admission ("policy denied ... acl_disallowed_type") and the agent never sees
# them, so every skill-execution assertion in the module fails with an empty
# capture. Modules that build their own topology import this list. Tests that
# exercise ACL restriction build their own topologies with explicit
# allowed_types instead of these fixtures; non-standard types (e.g.
# speak:synth) are still denied by default and granted per-test where needed.
VOICE_TYPES = [
    "recognizer_loop:utterance",
    "recognizer_loop:record_begin",
    "recognizer_loop:record_end",
    "recognizer_loop:b64_transcribe",
    "speak:b64_audio",
]


# ---------------------------------------------------------------------------
# T1 — minimal
# ---------------------------------------------------------------------------

@pytest.fixture
def minimal_topology():
    """T1: 1 master, 1 satellite."""
    b = TopologyBuilder()
    try:
        b.add_master("M0")
        b.add_satellite("S0", upstream=b.get_master("M0"), allowed_types=VOICE_TYPES)
        b.start_all()
        yield b
    finally:
        b.stop_all()


# ---------------------------------------------------------------------------
# T2 — star
# ---------------------------------------------------------------------------

@pytest.fixture
def star_topology(request):
    """T2: 1 master, N satellites (default 3). Override via @pytest.mark.parametrize."""
    n = getattr(request, "param", 3)
    b = TopologyBuilder()
    try:
        b.add_master("M0")
        for i in range(n):
            b.add_satellite(f"S{i}", upstream=b.get_master("M0"), allowed_types=VOICE_TYPES)
        b.start_all()
        yield b
    finally:
        b.stop_all()


@pytest.fixture
def admin_star_topology():
    """T2 variant: 1 master, 3 satellites where S0 is admin."""
    b = TopologyBuilder()
    try:
        b.add_master("M0")
        b.add_satellite("S0", upstream=b.get_master("M0"), is_admin=True, allowed_types=VOICE_TYPES)
        b.add_satellite("S1", upstream=b.get_master("M0"), allowed_types=VOICE_TYPES)
        b.add_satellite("S2", upstream=b.get_master("M0"), allowed_types=VOICE_TYPES)
        b.start_all()
        yield b
    finally:
        b.stop_all()


# ---------------------------------------------------------------------------
# T3 — chain
# ---------------------------------------------------------------------------

@pytest.fixture
def chain_topology():
    """T3: M0 → relay R1 (satellite+master) → S0."""
    b = TopologyBuilder()
    try:
        b.add_master("M0")
        relay_master = b.add_relay("R1", upstream=b.get_master("M0")).listener
        b.add_satellite("S0", upstream=relay_master, allowed_types=VOICE_TYPES)
        b.start_all()
        yield b
    finally:
        b.stop_all()


@pytest.fixture
def deep_chain_topology():
    """T3 variant: M0 → R1 → R2 → S0 (depth 3)."""
    b = TopologyBuilder()
    try:
        b.add_master("M0")
        r1_master = b.add_relay("R1", upstream=b.get_master("M0")).listener
        r2_master = b.add_relay("R2", upstream=r1_master).listener
        b.add_satellite("S0", upstream=r2_master, allowed_types=VOICE_TYPES)
        b.start_all()
        yield b
    finally:
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
    try:
        b.add_master("M0")

        sat_idx = 0
        for relay_idx, n_sats in enumerate(_HUGE_HIVE_COUNTS):
            rm = b.add_relay(f"RM{relay_idx}", upstream=b.get_master("M0")).listener
            for _ in range(n_sats):
                b.add_satellite(f"HS{sat_idx}", upstream=rm, allowed_types=VOICE_TYPES)
                sat_idx += 1

        b.start_all()
        yield b
    finally:
        b.stop_all()


def huge_hive_total_satellites() -> int:
    """Return the expected number of leaf satellites in huge_hive_topology."""
    return sum(_HUGE_HIVE_COUNTS)


def huge_hive_expected_satellites() -> int:
    """Return every satellite connection M0 discovers in huge_hive_topology.

    That is one satellite side per relay, plus every leaf satellite behind the
    relays. Tests import this instead of re-deriving the counts from a copied
    seed, so the topology and its expectations can never drift apart.
    """
    return len(_HUGE_HIVE_COUNTS) + sum(_HUGE_HIVE_COUNTS)


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
    try:
        b.add_master("M0")

        # R1: relay off M0, serves S0, S1, S2
        r1_master = b.add_relay("R1", upstream=b.get_master("M0")).listener
        b.add_satellite("S0", upstream=r1_master, allowed_types=VOICE_TYPES)
        b.add_satellite("S1", upstream=r1_master, allowed_types=VOICE_TYPES)
        b.add_satellite("S2", upstream=r1_master, allowed_types=VOICE_TYPES)

        # R2: relay off M0, serves S3 and a nested relay R3
        r2_master = b.add_relay("R2", upstream=b.get_master("M0")).listener
        b.add_satellite("S3", upstream=r2_master, allowed_types=VOICE_TYPES)

        # R3: nested relay off R2, serves S4, S5
        r3_master = b.add_relay("R3", upstream=r2_master).listener
        b.add_satellite("S4", upstream=r3_master, allowed_types=VOICE_TYPES)
        b.add_satellite("S5", upstream=r3_master, allowed_types=VOICE_TYPES)

        # S6: direct satellite of root M0
        b.add_satellite("S6", upstream=b.get_master("M0"), allowed_types=VOICE_TYPES)

        b.start_all()
        yield b
    finally:
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
    try:
        b.add_master("M0")

        # Long arm: chain of DEPTH relays
        current_master = b.get_master("M0")
        for i in range(_DEPTH):
            current_master = b.add_relay(f"RA{i}", upstream=current_master).listener
        b.add_satellite("S_deep", upstream=current_master, allowed_types=VOICE_TYPES)

        # Short arms: 3 direct satellites of M0
        for i in range(3):
            b.add_satellite(f"S_short{i}", upstream=b.get_master("M0"), allowed_types=VOICE_TYPES)

        b.start_all()
        yield b
    finally:
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


# MiniCroft READY budget. ovoscope's get_minicroft() defaults to max_wait=60s,
# but a MiniCroft boots the *entire* default plugin set (common_query, stop, OCP
# x2, persona, IntentService) serially before the requested test skills even
# start loading — and several test skills do slow first-run work the first time
# they boot on a clean runner (e.g. ovos-skill-date-time registers homescreen
# example utterances + writes first-run settings, ~45s alone). On a loaded CI
# runner the full boot legitimately exceeds 60s, so the default deadline trips
# during fixture setup ("MiniCroft did not reach READY in 60s") even though the
# stack is healthy. OvoscopeAgentProtocol does not forward max_wait to
# get_minicroft, so we pre-build MiniCroft here with a roomier budget and hand
# it in via ``minicroft=`` (the supported bring-your-own-MiniCroft path).
MINICROFT_READY_TIMEOUT = 180


def make_ovoscope_agent(skill_ids=None, *, extra_skills=None,
                        max_wait: float = MINICROFT_READY_TIMEOUT,
                        **minicroft_kwargs):
    """Build an :class:`OvoscopeAgentProtocol` with a generous READY budget.

    Pre-starts the MiniCroft via ``ovoscope.get_minicroft`` with ``max_wait`` so
    a slow-but-healthy boot (full default plugin set + cold-cache skill first
    runs on a loaded CI runner) is not mistaken for a startup failure.

    Extra keyword arguments go straight to ``MiniCroft``. Use them to switch on
    a service the default MiniCroft leaves off, e.g.
    ``enable_event_scheduler=True`` for skills that call ``schedule_event()``.
    """
    from hivescope.plugins.ovoscope_agent import OvoscopeAgentProtocol
    from ovoscope import get_minicroft
    skill_ids = list(skill_ids or [])
    extra_skills = extra_skills or {}
    minicroft = get_minicroft(skill_ids, extra_skills=extra_skills,
                              max_wait=max_wait, **minicroft_kwargs)
    try:
        return OvoscopeAgentProtocol(minicroft=minicroft)
    except BaseException:
        # The MiniCroft is already booted (threads, bus, skill services). If the
        # protocol constructor raises, nothing else holds a reference to it, so
        # shut it down here instead of leaking it into the rest of the session.
        shutdown = getattr(minicroft, "stop", None) or getattr(minicroft, "shutdown", None)
        if shutdown is not None:
            shutdown()
        raise


def skill_missing(*skill_ids: str) -> bool:
    """Return True if ANY of the given skills are not installed.

    Only an ImportError (the OVOS plugin manager is not installed at all) means
    "cannot tell — assume missing". Every other failure is a real fault and
    propagates, so a broken plugin scan is not silently reported as a skip.
    """
    try:
        from ovos_plugin_manager.skills import find_skill_plugins
    except ImportError:
        return True
    plugins = find_skill_plugins()
    return any(sid not in plugins for sid in skill_ids)


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


def wait_for_satellite_message(satellite, msg_type: str, timeout: float = 30.0):
    """Block until msg_type has been received by the satellite. Returns a
    Message (reconstructed from the satellite's recorder) or None on timeout.

    Uses the satellite's MessageRecorder rather than a freshly-registered
    ``internal_bus.once`` listener. The recorder buffers every inbound message,
    so a message that already arrived *before* this call (e.g. the skill's
    speak landed during the preceding ``cap.wait(...)``) is still matched —
    a late ``once`` listener would silently miss it and the test would flake.

    The recorder keys each record on the HiveMessage *envelope* type ("bus",
    "broadcast", …), never on the OVOS topic inside it, so
    ``recorder.wait_for("ovos.utterance.speak")`` never matches anything and
    every caller times out. Match on the payload's own ``type`` field instead.
    """
    import time
    from ovos_bus_client.message import Message

    def _as_message(payload):
        if isinstance(payload, Message):
            return payload
        return Message(payload.get("type", msg_type),
                       payload.get("data", {}),
                       payload.get("context", {}))

    def _payload_type(payload):
        if isinstance(payload, Message):
            return payload.msg_type
        if isinstance(payload, dict):
            return payload.get("type")
        return None

    deadline = time.monotonic() + timeout
    while True:
        for rec in reversed(satellite.recorder.snapshot()):
            if rec.direction != "in":
                continue
            if _payload_type(rec.payload) == msg_type:
                return _as_message(rec.payload)
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.05)


def open_capture(agent, **kwargs):
    """Open an ARMED capture session on the agent's MiniCroft bus.

    ovoscope's ``CaptureSession`` only counts EOF messages while it is armed,
    and the only thing that arms it is ``CaptureSession.capture()`` — which
    also emits the source message. The HiveMind harness cannot use that path:
    the satellite emits the utterance, not the capture. hivescope's
    ``agent.new_capture()`` therefore hands back a session that is never
    armed, so ``wait()`` ignores every EOF message, always burns its full
    timeout, and ``assert_complete()`` can never pass.

    Arm the session here, with the same reset ``capture()`` does, so a capture
    ends when the utterance is handled instead of when the clock runs out.
    """
    cap = agent.new_capture(**kwargs)
    session = cap._cap
    with session._eof_lock:
        session.done.clear()
        session._eof_seen = 0
        session._generation += 1
        session._armed = True
    return cap
