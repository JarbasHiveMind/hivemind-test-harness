"""
HIVEMIND-BRIDGE-1 §4 conformance — the per-connection session NAT.

``test_spec_bridge1.py`` pins the payload-opacity and null-stripping half of
§4. This file pins the other half: the per-connection session_id
namespacing that keeps two peers from colliding onto one OVOS session, the
multiplexing of several declared sessions over one connection, and the
contents-merge that keeps a thin control message from clobbering a
satellite's HELLO-established baseline (the real timezone-bleed regression,
hivemind-core#287).

Pinned here, via hivescope's NAT-aware assertions:

  * BRIDGE-1 §4 — a non-admin's declared ``session_id`` is stamped inbound as
    ``conn_nonce:declared`` (``assert_session_id_natted``), while the
    session's other contents cross unchanged (``assert_session_inbound_preserved``).
  * BRIDGE-1 §4 — distinct declared session ids sent over one connection each
    land on a distinct, isolated Layer-1 session that still shares the
    connection's nonce (``assert_sessions_isolated``).
  * BRIDGE-1 §4 — a thin message that omits a field the peer established at
    HELLO keeps the baseline value, not a freshly-fabricated orchestrator
    default (``assert_session_contents_merged_over_baseline``).
  * BRIDGE-1 §4/§4.1 — an admin connection is exempt from the NAT: its
    declared id is stamped raw and may address the reserved, device-local
    "default" session, while a non-admin's "default" is isolated per
    connection (``assert_session_id_natted(..., admin=True)``).
"""
from ovos_bus_client.message import Message
from ovos_bus_client.session import Session

from hivescope.topology import TopologyBuilder
from hivescope.assertions import (
    assert_session_inbound_preserved,
    assert_session_id_natted,
    assert_sessions_isolated,
    assert_session_contents_merged_over_baseline,
)

from tests.conftest import poll_until

ALLOWED = "recognizer_loop:utterance"


def _bridge(**satellite_kwargs):
    b = TopologyBuilder()
    b.add_master("M0")
    b.add_satellite("S0", upstream=b.get_master("M0"),
                    allowed_types=[ALLOWED, "speak"], **satellite_kwargs)
    b.start_all()
    return b


def _send(b, declared_session):
    """Send one utterance from S0 declaring ``declared_session`` and wait for
    it to reach M0's agent bus."""
    s0, m0 = b.get_satellite("S0"), b.get_master("M0")
    before = len(m0.recorder.snapshot())
    s0.send(Message(ALLOWED, {"utterances": ["hi"]}, {"session": declared_session}))
    poll_until(lambda: len(m0.recorder.snapshot()) > before, timeout=3,
               message=f"{ALLOWED} never reached the agent bus")


# ---------------------------------------------------------------------------
# BRIDGE-1 §4 — per-message NAT of a non-admin's declared session_id
# ---------------------------------------------------------------------------

class TestPerMessageSessionNAT:
    """BRIDGE-1 §4 — the id is namespaced by the connection; the contents are
    not touched."""

    def test_declared_session_id_is_natted_and_contents_preserved(self):
        b = _bridge()
        try:
            s0, m0 = b.get_satellite("S0"), b.get_master("M0")
            declared = "caller-declared-id"
            _send(b, {"session_id": declared, "lang": "pt-pt"})

            assert_session_id_natted(m0, s0, declared)
            assert_session_inbound_preserved(m0, s0, {"lang": "pt-pt"})
        finally:
            b.stop_all()


# ---------------------------------------------------------------------------
# BRIDGE-1 §4 — multiplexing several declared sessions over one connection
# ---------------------------------------------------------------------------

class TestMultiplexIsolation:
    """BRIDGE-1 §4 (hivemind-core#287) — a relay forwarding several peers, or
    a per-call bridge minting a fresh session_id per call, must never merge
    two declared sessions onto one Layer-1 session."""

    def test_distinct_declared_ids_land_on_distinct_isolated_sessions(self):
        b = _bridge()
        try:
            s0, m0 = b.get_satellite("S0"), b.get_master("M0")
            declared_ids = ["call-1", "call-2", "call-3"]
            for declared in declared_ids:
                _send(b, {"session_id": declared})

            assert_sessions_isolated(m0, s0, declared_ids)
        finally:
            b.stop_all()


# ---------------------------------------------------------------------------
# BRIDGE-1 §4 — contents-merge over the HELLO baseline
# ---------------------------------------------------------------------------

class TestContentsMergedOverBaseline:
    """BRIDGE-1 §4 — the connection's HELLO-established baseline (location,
    lang, ...) survives a later thin message that does not repeat it. Losing
    this merge is the timezone-bleed regression: a fresh ``Session`` fabricates
    the master's own defaults for the missing fields instead of keeping the
    satellite's real values."""

    def test_a_thin_message_keeps_the_hello_baseline(self):
        b = _bridge()
        try:
            s0, m0 = b.get_satellite("S0"), b.get_master("M0")
            declared = "the-session"

            # Establish the HELLO baseline for this connection the way a real
            # HELLO does: a full session, once, before any bus traffic.
            conn = m0.hm_protocol.clients[s0.peer]
            conn.sess = Session(session_id=declared, lang="pt-PT",
                                 site_id="the-lab")

            # A later thin message names the session but carries none of the
            # baseline fields.
            _send(b, {"session_id": declared})

            assert_session_contents_merged_over_baseline(
                m0, s0, {"lang": "pt-PT", "site_id": "the-lab"})
        finally:
            b.stop_all()


# ---------------------------------------------------------------------------
# BRIDGE-1 §4/§4.1 — the admin exemption
# ---------------------------------------------------------------------------

class TestAdminExemption:
    """BRIDGE-1 §4/§4.1 — an admin is trusted to address orchestrator
    sessions directly, including the reserved, device-local "default"; a
    non-admin's "default" is namespaced like any other declared id and can
    never reach that reserved session."""

    def test_admin_declared_id_is_stamped_raw(self):
        b = _bridge(is_admin=True)
        try:
            s0, m0 = b.get_satellite("S0"), b.get_master("M0")
            _send(b, {"session_id": "default"})

            assert_session_id_natted(m0, s0, "default", admin=True)
        finally:
            b.stop_all()

    def test_non_admin_default_is_natted_not_raw(self):
        b = _bridge()
        try:
            s0, m0 = b.get_satellite("S0"), b.get_master("M0")
            _send(b, {"session_id": "default"})

            assert_session_id_natted(m0, s0, "default")
        finally:
            b.stop_all()
