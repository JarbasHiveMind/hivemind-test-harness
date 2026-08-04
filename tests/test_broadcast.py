"""
TS-BC-01..04 — BROADCAST scenarios.
"""
import pytest
from ovos_bus_client.message import Message
from hivemind_bus_client.message import HiveMessage, HiveMessageType


def _broadcast_msg():
    inner = HiveMessage(HiveMessageType.BUS,
                        payload=Message("test.event", {"ping": "pong"}))
    return HiveMessage(HiveMessageType.BROADCAST, payload=inner)


class TestBroadcastFromMaster:
    """TS-BC-01 — master sends BROADCAST; all connected satellites receive it."""

    def test_all_satellites_receive_broadcast(self, star_topology):
        b = star_topology
        m0 = b.get_master("M0")

        # Collect inbound BROADCAST messages on each satellite
        received = {f"S{i}": [] for i in range(3)}
        for i in range(3):
            sat = b.get_satellite(f"S{i}")
            sat.shim.emitter.on(HiveMessageType.BROADCAST,
                                lambda msg, name=f"S{i}": received[name].append(msg))

        m0.send_to_all(_broadcast_msg())

        for i in range(3):
            assert len(received[f"S{i}"]) == 1, \
                f"S{i} did not receive BROADCAST"

    def test_broadcast_payload_preserved(self, star_topology):
        b = star_topology
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")

        payloads = []
        s0.shim.emitter.on(HiveMessageType.BROADCAST, payloads.append)

        m0.send_to_all(_broadcast_msg())

        assert len(payloads) == 1
        received = payloads[0]
        assert received.payload.msg_type == HiveMessageType.BUS


class TestBroadcastFromAdminSatellite:
    """TS-BC-01 (satellite side) — admin satellite can BROADCAST."""

    def test_admin_satellite_broadcast_forwarded(self, admin_star_topology):
        b = admin_star_topology
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")  # admin

        # S1 and S2 should receive the broadcast from S0 via M0. core keeps the
        # envelope when it forwards (_rewrap, HIVEMIND-NODE-1 §3.3), so siblings
        # see the BROADCAST wrapper with the BUS content still inside it.
        s1_received = []
        s2_received = []
        b.get_satellite("S1").shim.emitter.on(HiveMessageType.BROADCAST,
                                               s1_received.append)
        b.get_satellite("S2").shim.emitter.on(HiveMessageType.BROADCAST,
                                               s2_received.append)

        s0.send(_broadcast_msg())

        assert len(s1_received) == 1, "S1 did not receive admin broadcast"
        assert len(s2_received) == 1, "S2 did not receive admin broadcast"
        for got in (s1_received[0], s2_received[0]):
            assert got.payload.msg_type == HiveMessageType.BUS, \
                f"inner payload must stay a BUS, got {got.payload.msg_type}"


class TestBroadcastFromNonAdmin:
    """TS-BC-02 — non-admin satellite BROADCAST is rejected."""

    def test_non_admin_broadcast_triggers_illegal(self, star_topology):
        b = star_topology
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")   # non-admin
        s1 = b.get_satellite("S1")

        illegal_calls = []
        m0.hm_protocol.illegal_callback = illegal_calls.append

        s1_received = []
        s1.shim.emitter.on(HiveMessageType.BROADCAST, s1_received.append)

        s0.send(_broadcast_msg())

        assert len(illegal_calls) == 1, "illegal_callback should fire for non-admin broadcast"
        assert len(s1_received) == 0, "Non-admin broadcast must not be forwarded to other satellites"


class TestBroadcastTargetSiteId:
    """TS-BC-03 — BROADCAST with target_site_id routes BUS injection to the matching site.

    Protocol: when handle_broadcast_message sees target_site_id == master's own site_id,
    it injects the inner BUS payload on the master's agent bus. When site_id does NOT
    match, no bus injection occurs (message is forwarded to satellites only).
    The broadcast is always forwarded to all sibling satellites regardless of site_id.
    """

    def test_site_targeted_broadcast_injects_on_matching_master(self, admin_star_topology):
        """Admin satellite broadcasts with target_site_id = master's site → BUS injected."""
        b = admin_star_topology
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")  # admin

        # Use a type in the default allowed_types list so it passes the ACL check
        inner = HiveMessage(HiveMessageType.BUS,
                            payload=Message("recognizer_loop:utterance",
                                           {"utterances": ["site targeted test"]},
                                           context={"session": {"session_id": s0.shim.session_id}}),
                            target_site_id=m0.identity.site_id)
        broadcast = HiveMessage(HiveMessageType.BROADCAST, payload=inner,
                                target_site_id=m0.identity.site_id)

        # Admin satellite sends BROADCAST targeting master's own site_id
        s0.send(broadcast)

        # Master sees its site_id matches → injects inner BUS on agent bus
        m0.agent_protocol.assert_injected("recognizer_loop:utterance")

    def test_site_targeted_broadcast_does_not_inject_on_non_matching_site(self, admin_star_topology):
        """BROADCAST targeting a different site_id must NOT inject on the master's bus."""
        b = admin_star_topology
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")  # admin
        s1 = b.get_satellite("S1")

        # Target s1's site_id — this does NOT match master's site_id
        inner = HiveMessage(HiveMessageType.BUS,
                            payload=Message("recognizer_loop:utterance",
                                           {"utterances": ["other site test"]}),
                            target_site_id=s1.identity.site_id)
        broadcast = HiveMessage(HiveMessageType.BROADCAST, payload=inner,
                                target_site_id=s1.identity.site_id)

        s0.send(broadcast)

        # Master's site_id != s1's site_id → no bus injection on master agent bus
        # (Note: the message IS forwarded to satellites unconditionally, but
        #  the BUS payload is only injected on the agent bus if site_id matches)
        injected = m0.agent_protocol.injected
        # Only hive.client.connect events should be present (from satellite connections),
        # not the recognizer_loop:utterance we just sent targeting a different site
        site_targeted = [m for m in injected if m.msg_type == "recognizer_loop:utterance"
                         and "other site test" in m.data.get("utterances", [])]
        assert len(site_targeted) == 0, \
            "Master must not inject BUS payload when target_site_id doesn't match its own site_id"
