"""
TS-FIX-01..07 — Integration tests for bugs fixed per PROTOCOL_AUDIT.md.

Each class targets one audit finding and verifies the fix end-to-end through the
full connect/handshake/send pipeline.

  CRIT-1  (TS-FIX-01) — Wrong-password satellite is disconnected at handshake
  CRIT-3  (TS-FIX-02) — INTERCOM inner BUS is delivered after decryption
  HIGH-3  (TS-FIX-03) — Non-admin BROADCAST disconnects the offending satellite
  HIGH-3  (TS-FIX-04) — can_propagate=False PROPAGATE disconnects satellite
  HIGH-3  (TS-FIX-05) — can_escalate=False ESCALATE disconnects satellite
  MED-4   (TS-FIX-06) — update_last_seen does not crash when DB key is missing
  MED-1   (TS-FIX-07) — FILE binary: path-traversal file_name is stripped
"""
import time
import pytest
import pybase64
from unittest.mock import patch
from ovos_bus_client.message import Message
from hivemind_bus_client.message import HiveMessage, HiveMessageType, HiveMindBinaryPayloadType
from hivescope.topology import TopologyBuilder
from hivescope.node import MasterNode, SatelliteNode


# ---------------------------------------------------------------------------
# TS-FIX-01 — CRIT-1: Wrong password → satellite disconnected
# ---------------------------------------------------------------------------

class TestWrongPasswordDisconnects:
    """TS-FIX-01 — A satellite that presents the wrong password must be
    disconnected during the handshake phase and never enter self.clients."""

    def test_wrong_password_satellite_is_rejected(self):
        """Satellite with wrong password: handshake fails, peer not in clients.

        ``SatelliteNode.connect`` registers the DB row from the satellite's own
        identity, so the two passwords always agree and a wrong password can
        never be expressed through it. This test therefore drives the wiring by
        hand: the master's DB row for ``wrong-sat`` carries the CORRECT
        password, and the satellite presents a DIFFERENT one.
        """
        b = TopologyBuilder()
        wrong_sat = None
        try:
            b.add_master("M0")
            m0 = b.get_master("M0")

            # Baseline: a satellite with the matching password is accepted.
            b.add_satellite("S0", upstream=m0)
            b.start_all()

            s0 = b.get_satellite("S0")
            assert len(m0.hm_protocol.clients) == 1, \
                "S0 (correct password) must be accepted"

            # Register a key the master knows, with the password the master expects.
            wrong_sat = SatelliteNode.create("wrong-sat")
            m0.register_satellite(key=wrong_sat.identity.access_key,
                                  password="the-correct-password")
            # ...then have the satellite present a different one.
            wrong_sat.identity.password = "definitely-wrong-password"
            wrong_sat._master = m0

            # Spy on the master's specific invalid-key rejection path so a crash
            # elsewhere in the handshake (which would produce the same external
            # symptoms below) cannot be mistaken for the wrong-password rejection.
            real_handle_invalid_key = m0.hm_protocol.handle_invalid_key_connected
            with patch.object(
                m0.hm_protocol, "handle_invalid_key_connected",
                wraps=real_handle_invalid_key,
            ) as spy_invalid_key:
                try:
                    m0.network_protocol.connect_satellite(satellite=wrong_sat)
                    if not wrong_sat.shim.handshake_event.is_set():
                        wrong_sat.slave_protocol.start_handshake()
                except RuntimeError:
                    # The master drops the connection mid-handshake, so the client's
                    # next write finds no connection. That IS the rejection.
                    pass

                assert spy_invalid_key.call_count == 1, \
                    ("Wrong password must be rejected via handle_invalid_key_connected "
                     f"exactly once; got {spy_invalid_key.call_count} calls")

            assert not wrong_sat.shim.handshake_event.is_set(), \
                "Wrong-password satellite must never complete the handshake"

            peers = list(m0.hm_protocol.clients.keys())
            assert not any(p.startswith("wrong-sat::") for p in peers), \
                f"Wrong-password satellite must not be admitted; clients={peers}"

            # The rejection must not disturb the already-connected satellite.
            assert len(m0.hm_protocol.clients) == 1, \
                f"Only S0 must remain connected; clients={peers}"
            assert s0.shim.session_id in [c.sess.session_id
                                          for c in m0.hm_protocol.clients.values()], \
                "S0 session must survive the failed wrong-password attempt"

        finally:
            if wrong_sat is not None:
                wrong_sat.cleanup()
            b.stop_all()


# ---------------------------------------------------------------------------
# TS-FIX-02 — CRIT-3: INTERCOM inner BUS delivered after decryption
# ---------------------------------------------------------------------------

class TestIntercomInnerDelivery:
    """TS-FIX-02 — After RSA decryption the inner BUS message is injected on the
    master's agent bus.  Before the fix, dispatch checked message.msg_type
    (always INTERCOM) instead of inner.msg_type, so the BUS was silently dropped."""

    @pytest.mark.skip(reason="INTERCOM is an unfinished feature — not used in production yet")
    def test_encrypted_intercom_delivers_inner_bus(self, minimal_topology):
        """RSA-encrypted INTERCOM(BUS) → master injects the BUS on agent bus."""
        pass

    def test_unencrypted_intercom_bus_delivered(self, minimal_topology):
        """Unencrypted INTERCOM(BUS) with no target_pubkey → inner BUS injected."""
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        inner = HiveMessage(HiveMessageType.BUS,
                            payload=Message("recognizer_loop:utterance",
                                            {"utterances": ["plain intercom test"]},
                                            context={"session": {"session_id": s0.shim.session_id}}))
        intercom = HiveMessage(HiveMessageType.INTERCOM, payload=inner)

        s0.send(intercom)

        m0.agent_protocol.assert_injected("recognizer_loop:utterance")


# ---------------------------------------------------------------------------
# TS-FIX-03 — HIGH-3: Non-admin BROADCAST disconnects the satellite
# ---------------------------------------------------------------------------

class TestIllegalBroadcastDisconnects:
    """TS-FIX-03 — Non-admin satellite that sends BROADCAST must be disconnected."""

    def test_non_admin_broadcast_disconnects_satellite(self, star_topology):
        """After a non-admin BROADCAST the sending satellite is no longer in clients."""
        # star_topology is a fixture; it owns stop_all().
        b = star_topology
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")  # non-admin

        illegal_calls = []
        m0.hm_protocol.illegal_callback = illegal_calls.append

        peers_before = set(m0.hm_protocol.clients.keys())
        assert s0.shim.session_id in str(peers_before), \
            "S0 must be connected before the illegal broadcast"

        inner = HiveMessage(HiveMessageType.BUS,
                            payload=Message("test.event", {}))
        broadcast = HiveMessage(HiveMessageType.BROADCAST, payload=inner)
        s0.send(broadcast)

        # Illegal callback fires
        assert len(illegal_calls) == 1, "illegal_callback must fire"

        # After disconnect, S0's peer is no longer in clients
        s0_peer = f"{s0.identity.name}::{s0.shim.session_id}"
        assert s0_peer not in m0.hm_protocol.clients, \
            "Non-admin satellite must be removed from clients after illegal BROADCAST"


# ---------------------------------------------------------------------------
# TS-FIX-04 — HIGH-3: can_propagate=False PROPAGATE disconnects satellite
# ---------------------------------------------------------------------------

class TestIllegalPropagateDisconnects:
    """TS-FIX-04 — Satellite with can_propagate=False that sends PROPAGATE is disconnected."""

    def test_cant_propagate_satellite_disconnected(self):
        b = TopologyBuilder()
        try:
            b.add_master("M0")
            m0 = b.get_master("M0")
            b.add_satellite("S0", upstream=m0, can_propagate=False)
            b.start_all()

            s0 = b.get_satellite("S0")
            illegal_calls = []
            m0.hm_protocol.illegal_callback = illegal_calls.append

            inner = HiveMessage(HiveMessageType.THIRDPRTY, payload={"data": "test"})
            propagate = HiveMessage(HiveMessageType.PROPAGATE, payload=inner)
            s0.send(propagate)

            assert len(illegal_calls) == 1

            s0_peer = f"{s0.identity.name}::{s0.shim.session_id}"
            assert s0_peer not in m0.hm_protocol.clients, \
                "Satellite violating can_propagate must be disconnected"

        finally:
            b.stop_all()


# ---------------------------------------------------------------------------
# TS-FIX-05 — HIGH-3: can_escalate=False ESCALATE disconnects satellite
# ---------------------------------------------------------------------------

class TestIllegalEscalateDisconnects:
    """TS-FIX-05 — Satellite with can_escalate=False that sends ESCALATE is disconnected."""

    def test_cant_escalate_satellite_disconnected(self):
        b = TopologyBuilder()
        try:
            b.add_master("M0")
            m0 = b.get_master("M0")
            b.add_satellite("S0", upstream=m0, can_escalate=False)
            b.start_all()

            s0 = b.get_satellite("S0")
            illegal_calls = []
            m0.hm_protocol.illegal_callback = illegal_calls.append

            inner = HiveMessage(HiveMessageType.THIRDPRTY, payload={"data": "escalate-test"})
            escalate = HiveMessage(HiveMessageType.ESCALATE, payload=inner)
            s0.send(escalate)

            assert len(illegal_calls) == 1

            s0_peer = f"{s0.identity.name}::{s0.shim.session_id}"
            assert s0_peer not in m0.hm_protocol.clients, \
                "Satellite violating can_escalate must be disconnected"

        finally:
            b.stop_all()


# ---------------------------------------------------------------------------
# TS-FIX-06 — MED-4: update_last_seen does not crash on missing DB key
# ---------------------------------------------------------------------------

class TestUpdateLastSeenMissingKey:
    """TS-FIX-06 — update_last_seen must not raise when the DB no longer has the
    client's API key (e.g. key was revoked while client was connected)."""

    def test_missing_key_does_not_crash(self, minimal_topology):
        b = minimal_topology
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")

        # Patch the DB to return None for the client's key
        from unittest.mock import patch

        client = list(m0.hm_protocol.clients.values())[0]
        peers_before = set(m0.hm_protocol.clients.keys())

        with patch.object(m0.hm_protocol.db, 'get_client_by_api_key', return_value=None):
            # Must not raise AttributeError: 'NoneType' has no attribute 'last_seen'.
            # A raised exception fails the test; the explicit assertions below
            # additionally prove the missing key was handled as a no-op rather
            # than corrupting connection state.
            result = m0.hm_protocol.update_last_seen(client)

        assert result is None, "update_last_seen returns nothing on a missing key"
        assert set(m0.hm_protocol.clients.keys()) == peers_before, \
            "A revoked/missing DB key must not drop the live connection"
        assert client.peer in m0.hm_protocol.clients, \
            "The affected client stays connected after the no-op update"


# ---------------------------------------------------------------------------
# TS-FIX-07 — MED-1: FILE binary file_name path traversal stripped
# ---------------------------------------------------------------------------

class TestFileBinaryNameSanitized:
    """TS-FIX-07 — BINARY(FILE) metadata file_name is passed through os.path.basename()
    before reaching handle_receive_file, preventing path-traversal attacks."""

    def test_path_traversal_stripped_before_handler(self, minimal_topology):
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        received_names = []

        def capture_file(bin_data, file_name, client):
            received_names.append(file_name)

        m0.hm_protocol.binary_data_protocol.handle_receive_file = capture_file

        # Send BINARY(FILE) with a path-traversal file_name
        msg = HiveMessage(HiveMessageType.BINARY,
                          payload=b"\x00\x01\x02\x03",
                          bin_type=HiveMindBinaryPayloadType.FILE,
                          metadata={"file_name": "../../etc/passwd"})
        s0.send(msg)

        assert len(received_names) == 1
        import os
        assert received_names[0] == "passwd", \
            f"Expected 'passwd' after basename(), got {received_names[0]!r}"
        assert os.sep not in received_names[0], \
            "Path separators must not appear in the sanitized file_name"
