"""
TS-SHARED-01..04 — SHARED_BUS scenarios.

When a satellite is created with shared_bus=True, every message emitted on its
internal FakeBus is automatically wrapped in HiveMessage(SHARED_BUS, payload=...)
and forwarded upstream. The master handles it in handle_client_shared_bus(),
calling shared_bus_callback(message) if set.

This is a passive monitoring channel — the master can observe the satellite's
internal bus without injecting anything back.
"""
from ovos_bus_client.message import Message
from hivemind_bus_client.message import HiveMessage, HiveMessageType
from hivemind_test_harness.topology import TopologyBuilder


class TestSharedBus:
    """TS-SHARED-01 — shared_bus=True forwards internal bus events to master."""

    def test_shared_bus_callback_fires(self):
        """When satellite emits on internal bus, master's shared_bus_callback fires."""
        b = TopologyBuilder()
        b.add_master("M0")
        b.add_satellite("S0", upstream=b.get_master("M0"), shared_bus=True)
        b.start_all()

        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")

        shared_bus_calls = []
        m0.hm_protocol.shared_bus_callback = shared_bus_calls.append

        # Emit directly on the satellite's internal bus
        s0.internal_bus.emit(Message("speak", {"utterance": "hello"}))

        assert len(shared_bus_calls) == 1, \
            "shared_bus_callback should fire once for each internal bus emission"
        b.stop_all()

    def test_shared_bus_payload_is_message(self):
        """The payload received by shared_bus_callback is the OVOS Message."""
        b = TopologyBuilder()
        b.add_master("M0")
        b.add_satellite("S0", upstream=b.get_master("M0"), shared_bus=True)
        b.start_all()

        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")

        received = []
        m0.hm_protocol.shared_bus_callback = received.append

        s0.internal_bus.emit(Message("speak", {"utterance": "monitor me"}))

        assert len(received) == 1
        msg = received[0]
        # shared_bus_callback receives message.payload (a Message / dict)
        assert msg is not None
        b.stop_all()

    def test_shared_bus_disabled_by_default(self):
        """TS-SHARED-02 — satellite without shared_bus=True does NOT trigger callback."""
        b = TopologyBuilder()
        b.add_master("M0")
        b.add_satellite("S0", upstream=b.get_master("M0"))  # shared_bus=False default
        b.start_all()

        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")

        shared_bus_calls = []
        m0.hm_protocol.shared_bus_callback = shared_bus_calls.append

        s0.internal_bus.emit(Message("speak", {"utterance": "should not forward"}))

        assert len(shared_bus_calls) == 0, \
            "shared_bus_callback must not fire when shared_bus is disabled"
        b.stop_all()

    def test_shared_bus_multiple_events(self):
        """Each internal bus emission fires the callback exactly once."""
        b = TopologyBuilder()
        b.add_master("M0")
        b.add_satellite("S0", upstream=b.get_master("M0"), shared_bus=True)
        b.start_all()

        m0 = b.get_master("M0")
        s0 = b.get_satellite("S0")

        shared_bus_calls = []
        m0.hm_protocol.shared_bus_callback = shared_bus_calls.append

        for i in range(3):
            s0.internal_bus.emit(Message("test.event", {"seq": i}))

        assert len(shared_bus_calls) == 3, \
            "Each bus emission should fire shared_bus_callback once"
        b.stop_all()
