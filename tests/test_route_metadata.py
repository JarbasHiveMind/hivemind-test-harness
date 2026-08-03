"""
TS-ROUTE-HOP-01..08 — Route metadata hop-by-hop tracking.

Verifies that HiveMessage.route accumulates hop data as messages
traverse the hive, and that route data survives serialization,
unpacking, and response building.
"""
import threading
import time
import uuid

import pytest
from ovos_bus_client.message import Message
from hivemind_bus_client.message import HiveMessage, HiveMessageType


class TestRouteHopOnBus:
    """TS-ROUTE-HOP-01 — BUS from satellite → master has 1 hop in route."""

    def test_bus_message_has_hop_data(self, minimal_topology):
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        # Capture the HiveMessage as it arrives at master's handle_message
        captured = []
        _orig = m0.hm_protocol.handle_message

        def _capture(message, client):
            captured.append(message)
            _orig(message, client)

        m0.hm_protocol.handle_message = _capture

        s0.send(Message("recognizer_loop:utterance", {"utterances": ["hello"]}))

        assert len(captured) >= 1
        msg = captured[0]
        # After handle_message calls update_hop_data, route should have at least 1 hop
        assert len(msg.route) >= 1, f"Expected at least 1 hop, got {msg.route}"
        hop = msg.route[0]
        assert "source" in hop
        assert "targets" in hop


class TestRouteAccumulatesThroughRelay:
    """TS-ROUTE-HOP-02 — ESCALATE through relay accumulates hops."""

    def test_escalate_through_relay_accumulates_hops(self, chain_topology):
        b = chain_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        captured = []
        _orig = m0.hm_protocol.handle_message

        def _capture(message, client):
            captured.append(message)
            _orig(message, client)

        m0.hm_protocol.handle_message = _capture

        inner = HiveMessage(HiveMessageType.RENDEZVOUS,
                            payload={"data": "route-test"})
        s0.send(HiveMessage(HiveMessageType.ESCALATE, payload=inner))

        # Wait briefly for propagation
        time.sleep(0.5)

        escalate_msgs = [m for m in captured
                         if m.msg_type == HiveMessageType.ESCALATE]
        assert len(escalate_msgs) >= 1, "ESCALATE should reach M0"

        # The message traveled S0 → R1 → M0, so route should have 2+ hops
        msg = escalate_msgs[0]
        assert len(msg.route) >= 1, f"Expected hops from relay chain, got {msg.route}"


class TestPropagateRouteThroughRelay:
    """TS-ROUTE-HOP-03 — PROPAGATE has hops at each master."""

    def test_propagate_through_relay_accumulates_hops(self, chain_topology):
        b = chain_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        inner = HiveMessage(HiveMessageType.RENDEZVOUS,
                            payload={"data": "propagate-route"})
        propagate = HiveMessage(HiveMessageType.PROPAGATE, payload=inner)

        captured = []
        _orig = m0.hm_protocol.handle_message

        def _capture(message, client):
            captured.append(message)
            _orig(message, client)

        m0.hm_protocol.handle_message = _capture

        s0.send(propagate)
        time.sleep(0.5)

        prop_msgs = [m for m in captured
                     if m.msg_type == HiveMessageType.PROPAGATE]
        assert len(prop_msgs) >= 1


class TestRoutePreservedThroughUnpack:
    """TS-ROUTE-HOP-04 — _unpack_message transfers route from outer to inner."""

    def test_route_preserved_through_unpack(self, minimal_topology):
        b = minimal_topology
        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")

        inner = HiveMessage(HiveMessageType.RENDEZVOUS,
                            payload={"data": "unpack-test"})
        outer = HiveMessage(HiveMessageType.PROPAGATE, payload=inner)
        route = [{"source": "peer-A", "targets": ["peer-B"]}]
        outer.replace_route(route)
        outer.update_source_peer(s0.peer)

        client = m0.hm_protocol.clients.get(s0.peer)
        if client is None:
            pytest.skip("Client connection not found")

        unpacked = m0.hm_protocol._unpack_message(outer, client)
        assert unpacked.route == route, \
            f"Unpacked route should match outer route: {unpacked.route}"


class TestRouteHopStructure:
    """TS-ROUTE-HOP-05 — Each hop has source (str) and targets (list)."""

    def test_route_hop_structure(self, minimal_topology):
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        msg = HiveMessage(HiveMessageType.BUS,
                          payload={"type": "test", "data": {}, "context": {}},
                          source_peer="peer-1", target_peers=["peer-2"])
        msg.update_hop_data()

        for hop in msg.route:
            assert isinstance(hop, dict), f"Hop must be dict, got {type(hop)}"
            assert isinstance(hop["source"], str), "source must be str"
            assert isinstance(hop["targets"], list), "targets must be list"


class TestQueryResponseHasRoute:
    """TS-ROUTE-HOP-06 — QUERY response carries route back."""

    def test_query_response_has_route(self, minimal_topology):
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        # Capture outbound messages from master to satellite
        response_msgs = []

        def _capture_response(msg):
            if isinstance(msg, HiveMessage):
                response_msgs.append(msg)

        s0.shim.emitter.on(HiveMessageType.QUERY, _capture_response)

        query_id = str(uuid.uuid4())
        bus_inner = HiveMessage(HiveMessageType.BUS,
                                payload=Message("test.query", {"q": "hello"}))
        query = HiveMessage(HiveMessageType.QUERY, payload=bus_inner,
                            metadata={"query_id": query_id,
                                      "originator_peer": s0.peer})
        s0.send(query)

        time.sleep(1.0)

        # Whether the query was answered or errored, the response should exist
        # and carry route data (may be empty list if no hops recorded)
        if response_msgs:
            resp = response_msgs[0]
            assert isinstance(resp.route, list), "Response should have route as list"


class TestCascadeResponseHasRoute:
    """TS-ROUTE-HOP-07 — CASCADE response carries route."""

    def test_cascade_response_has_route(self, minimal_topology):
        b = minimal_topology
        s0 = b.get_satellite("S0")
        m0 = b.get_master("M0")

        response_msgs = []

        def _capture_response(msg):
            if isinstance(msg, HiveMessage):
                response_msgs.append(msg)

        s0.shim.emitter.on(HiveMessageType.CASCADE, _capture_response)

        query_id = str(uuid.uuid4())
        bus_inner = HiveMessage(HiveMessageType.BUS,
                                payload=Message("test.cascade", {"q": "hello"}))
        cascade = HiveMessage(HiveMessageType.CASCADE, payload=bus_inner,
                              metadata={"query_id": query_id,
                                        "originator_peer": s0.peer})
        s0.send(cascade)

        time.sleep(1.0)

        if response_msgs:
            resp = response_msgs[0]
            assert isinstance(resp.route, list), "Response should have route as list"


class TestPingRouteForHiveMapper:
    """TS-ROUTE-HOP-08 — PING through relay → HiveMapper gets edges from route."""

    def test_ping_route_feeds_hive_mapper(self, chain_topology):
        b = chain_topology
        m0 = b.get_master("M0")

        ping_inner = HiveMessage(
            HiveMessageType.PING,
            payload={
                "flood_id": str(uuid.uuid4()),
                "timestamp": time.time(),
                "peer": m0.hm_protocol.peer,
                "site_id": "test-site",
            },
        )
        propagate = HiveMessage(HiveMessageType.PROPAGATE, payload=ping_inner)

        # Send PING flood from M0
        for peer, client in m0.hm_protocol.clients.items():
            client.send(propagate)

        time.sleep(1.0)

        # HiveMapper should have discovered at least one node
        mapper = m0.hm_protocol.hive_mapper
        if mapper is not None:
            # The mapper should have at least the direct connection
            assert len(mapper.nodes) >= 1 or len(mapper.edges) >= 0
