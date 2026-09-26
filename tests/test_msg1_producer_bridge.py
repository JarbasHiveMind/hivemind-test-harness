"""OVOS-MSG-1 §3.3 producer cell: the HiveMind bridge family.

MSG-1 §3.3 lets ``context.destination`` be a string or an array of strings.
A run of OVOS producers used to put a one-element list where a string
belongs (ovos-bus-client#368, ovos-dinkum-listener#258, ovos-docker#189,
ovos-gui-api-client#7, HiveMind-baresip-bridge#37, T-2650).
ovos-test-harness ``test/conformance/test_msg1_producers_conformance.py``
holds one cell per family of that run, except this one: the
``HiveMind-baresip-bridge`` ``CallCallbacks`` producer speaks to
hivemind-core, not to an OVOS bus, so a cell for it needs a real
hivemind-core between the producer and the OVOS side. That stack is pinned
here, so the fourth cell lives here.

The wire cell drives the real ``CallCallbacks.text_callback`` with a real
``HiveMessageBusClient`` against a real loopback hivemind-core master, and
reads ``destination`` off the message the master injected on its OVOS side.
The source cell reads the producer's own context object, which is where the
stamp is made. The two together separate "the producer stamps a string" from
"the string survives the HiveMind transport".

Fail-before. The floor in the ``dev`` extra is
``hivemind-baresip-bridge>=0.1.2a1``, the first release that carries #37.
Both cells assert outright, so 0.1.1a5 — the release before #37 — fails them
instead of skipping.

``source`` is deliberately not asserted: hivemind-core replaces it with the
peer id as part of its own answer routing (T-2650), which is not what §3.3
is about.
"""

import pytest
from ovos_bus_client.message import Message

from hivescope.topology import TopologyBuilder
from hivescope.utils import make_identity

from tests.conftest import poll_until

UTTERANCE = "recognizer_loop:utterance"
UNKNOWN = "recognizer_loop:speech.recognition.unknown"

#: The satellite credentials the loopback master registers for this cell.
KEY = "t3449-baresip-key"
PASSWORD = "Corr3ct-Horse!Batt3ry_v3xx"

CALLER = "sip:harness@example.invalid"
SESSION_ID = "t3449-session"


def _call_callbacks(bus):
    """The real producer under test, built on the given bus."""
    from hivemind_baresip_bridge.bridge import CallCallbacks
    return CallCallbacks(bus=bus, caller=CALLER, session_id=SESSION_ID,
                         lang="en-us")


def _assert_string_destination(context: dict, expected: str, where: str):
    """§3.3: ``destination`` is the string ``expected``, not a one-element
    list holding it."""
    dest = context.get("destination")
    assert isinstance(dest, str), (
        f"MSG-1 §3.3: destination {where} is {type(dest).__name__} {dest!r}, "
        f"a string {expected!r} was expected; context={context!r}")
    assert dest == expected


class TestBaresipBridgeContextObject:
    """The stamp at its source: the producer's own context dict."""

    def test_context_stamps_a_string_destination(self):
        cb = _call_callbacks(bus=None)
        _assert_string_destination(cb._context(), "audio",
                                   "in CallCallbacks._context()")


class TestBaresipBridgeThroughHivemindCore:
    """The stamp on the far side of a real hivemind-core."""

    @pytest.fixture
    def bridge_client(self):
        """A real ``HiveMessageBusClient`` connected to a real loopback
        master, with the two types the bridge emits granted."""
        from hivemind_bus_client.client import HiveMessageBusClient

        b = TopologyBuilder()
        client = None
        try:
            m = b.add_master("M0", use_loopback=True)
            # hivemind-core is whitelist-only: an ungranted type is refused at
            # admission and never reaches the OVOS side, so the cell would read
            # an empty injection list instead of a destination.
            m.register_satellite(KEY, password=PASSWORD,
                                 allowed_types=[UTTERANCE, UNKNOWN])
            b.start_all()

            host, port = m.network_protocol.url.replace("ws://", "") \
                .rstrip("/").split(":")
            # in-memory identity: a bare NodeIdentity() is the real on-disk one
            # (~/.config/hivemind) and the run would rewrite the user's keys.
            identity = make_identity("t3449-baresip")
            identity.access_key = KEY
            identity.password = PASSWORD
            identity.default_master = f"ws://{host}"
            identity.default_port = int(port)
            identity.site_id = "t3449-site"

            client = HiveMessageBusClient(
                key=KEY, password=PASSWORD, host=f"ws://{host}",
                port=int(port), useragent="t3449-baresip",
                self_signed=False, identity=identity)
            client.connect(site_id="t3449-site")
            client.wait_for_handshake(timeout=10)
            assert client.handshake_event.is_set(), \
                "precondition: the bridge client must complete the handshake"
            yield m, client
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
            b.stop_all()

    def _injected(self, master, msg_type: str) -> Message:
        poll_until(
            lambda: [m for m in master.agent_protocol.injected
                     if m.msg_type == msg_type],
            timeout=10,
            message=f"'{msg_type}' never reached the OVOS side of the master")
        return [m for m in master.agent_protocol.injected
                if m.msg_type == msg_type][-1]

    def test_text_callback_destination_is_a_string_on_the_ovos_side(
            self, bridge_client):
        """``CallCallbacks.text_callback`` is the production path: a call's
        recognized speech goes to hivemind-core, which injects it on its OVOS
        side. ``destination`` must arrive there as a string."""
        master, client = bridge_client
        _call_callbacks(client).text_callback("what is the weather", "en-us")

        msg = self._injected(master, UTTERANCE)
        assert msg.data["utterances"] == ["what is the weather"]
        _assert_string_destination(
            msg.context, "audio",
            "on the OVOS side of hivemind-core, after the HiveMind transport")

    def test_error_callback_destination_is_a_string_on_the_ovos_side(
            self, bridge_client):
        """The same context object rides the recognition-failure message, so
        a regression in either path is a §3.3 break."""
        master, client = bridge_client
        _call_callbacks(client).error_callback(b"")

        msg = self._injected(master, UNKNOWN)
        _assert_string_destination(
            msg.context, "audio",
            "on the OVOS side of hivemind-core, after the HiveMind transport")
