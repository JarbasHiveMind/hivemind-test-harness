"""Conformance tests for the four normative requirements that had no
implementation at the 2026-08-04 baseline audit.

Each class names the spec document and section it pins:

- HIVEMIND-NODE-1 §5.5 / HIVEMIND-AGENT-1 §5 — the QUERY originator's own
  liveness bound.
- HIVEMIND-TRANSPORT-1 §4 — the HTTP binding's retention bound on
  undelivered frames.
- HIVEMIND-WIRE-1 §4.1 — a sender must not build a binary frame whose
  metadata block exceeds the 8-bit length field.
- HIVEMIND-AUDIO-1 §2 — a receiver that cannot process the stated audio
  format must reject the payload.
"""
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from ovos_bus_client.message import Message
from ovos_utils.fakebus import FakeBus

from hivemind_bus_client.message import HiveMessage, HiveMessageType


def _bus_message(msg_type):
    return HiveMessage(HiveMessageType.BUS, payload=Message(msg_type))


def _query_response(inner_type, query_id="q1"):
    return HiveMessage(HiveMessageType.QUERY,
                       payload=_bus_message(inner_type),
                       metadata={"query_id": query_id, "is_response": True})


class TestQueryOriginatorTimeout(unittest.TestCase):
    """HIVEMIND-NODE-1 §5.5 and HIVEMIND-AGENT-1 §5.

    An intermediate node that declines a QUERY escalates it silently, so
    nothing comes back down while the query travels. The originator must
    carry its own bound: with neither a chunk, a ``hive.query.complete``,
    nor a ``hive.query.timeout`` inside a configured interval it must treat
    the query as failed.
    """

    def _protocol(self, timeout=0.2):
        from hivemind_bus_client.protocol import HiveMindSlaveProtocol
        proto = HiveMindSlaveProtocol.__new__(HiveMindSlaveProtocol)
        proto.query_timeout = timeout
        proto.query_liveness = None
        proto.internal_protocol = SimpleNamespace(bus=FakeBus())
        proto.handle_bus = MagicMock()
        return proto

    @staticmethod
    def _watch_for_failure(proto):
        seen = []
        proto.internal_protocol.bus.on("hive.query.timeout", seen.append)
        return seen

    def test_originator_reports_failure_when_no_answer_arrives(self):
        proto = self._protocol()
        seen = self._watch_for_failure(proto)

        proto.arm_query_timeout()
        time.sleep(0.5)

        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].data["error"], "no_answer")

    def test_answer_chunk_restarts_the_interval(self):
        proto = self._protocol()
        seen = self._watch_for_failure(proto)

        proto.arm_query_timeout()
        for _ in range(4):
            time.sleep(0.1)
            proto.handle_query(_query_response("speak"))

        self.assertEqual(seen, [], "a streaming answer must not time out")

    def test_stream_completion_cancels_the_interval(self):
        proto = self._protocol()
        seen = self._watch_for_failure(proto)

        proto.arm_query_timeout()
        proto.handle_query(_query_response("hive.query.complete"))
        time.sleep(0.5)

        self.assertEqual(seen, [])

    def test_explicit_timeout_response_cancels_the_interval(self):
        """A top-of-chain ``hive.query.timeout`` already reports the failure;
        the originator must not report it a second time."""
        proto = self._protocol()
        seen = self._watch_for_failure(proto)

        proto.arm_query_timeout()
        proto.handle_query(_query_response("hive.query.timeout"))
        time.sleep(0.5)

        self.assertEqual(seen, [])

    def test_the_interval_is_configurable_and_can_be_disabled(self):
        proto = self._protocol(timeout=0)
        seen = self._watch_for_failure(proto)

        proto.arm_query_timeout()
        time.sleep(0.3)

        self.assertIsNone(proto.query_liveness)
        self.assertEqual(seen, [])


class TestHttpRetentionBound(unittest.TestCase):
    """HIVEMIND-TRANSPORT-1 §4.

    The HTTP binding has no server push, so the node holds a peer's frames
    until it polls. That obligation must end: the node retains a frame until
    it is polled, until the session closes, or until a documented retention
    bound elapses.
    """

    def _queue(self, retention_seconds=300.0, max_frames=512):
        # The shipped bound is RetentionQueue(maxsize, ttl) — note the
        # argument order is the reverse of the (retention, max) this helper
        # takes, so keep the keywords.
        from hivemind_http_protocol import RetentionQueue
        return RetentionQueue(maxsize=max_frames, ttl=retention_seconds)

    def test_frames_within_the_bound_are_delivered_on_poll(self):
        queue = self._queue()
        queue.put("first")
        queue.put("second")

        self.assertEqual(queue.drain(), ["first", "second"])
        self.assertEqual(queue.drain(), [])

    def test_unpolled_frames_are_dropped_once_the_retention_bound_elapses(self):
        """The sweep lives in the store, not the queue, and that is the path
        the listener uses: every ``registry.messages(key)`` sweeps expired
        clients before handing back a queue. Testing a bare queue would miss
        the half of the bound that caps the *number* of queues."""
        from hivemind_http_protocol import RetentionStore
        store = RetentionStore(maxsize=512, ttl=0.1)
        store.queue_for("idle").put("stale")
        self.assertEqual(len(store["idle"]), 1)

        time.sleep(0.2)

        # the expired client is swept out entirely, not just emptied
        self.assertEqual(store.queue_for("idle").drain(), [])
        self.assertEqual(len(store["idle"]), 0)

    def test_a_peer_that_never_polls_cannot_grow_the_queue_without_limit(self):
        queue = self._queue(max_frames=8)
        for i in range(100):
            queue.put(f"frame-{i}")

        self.assertEqual(len(queue), 8)
        # the bound drops the oldest, so the newest are the ones still owed
        self.assertEqual(queue.drain()[-1], "frame-99")

    def test_the_bound_is_configurable_from_the_plugin_config(self):
        """The operator can move the bound; it is not a hard-coded constant.

        The listener reads ``max_undelivered`` and ``undelivered_ttl`` from
        its plugin config and builds the registry from them, so asserting the
        registry carries the configured values is what actually pins
        configurability — a ``hasattr`` on the handler would pass against a
        constant that no config can reach.
        """
        from hivemind_http_protocol import (ClientRegistry,
                                            DEFAULT_MAX_UNDELIVERED,
                                            DEFAULT_UNDELIVERED_TTL)
        registry = ClientRegistry(maxsize=7, ttl=1.5)
        queue = registry.messages("peer")
        self.assertEqual(queue.maxsize, 7)
        self.assertEqual(queue.ttl, 1.5)

        default = ClientRegistry().messages("peer")
        self.assertEqual(default.maxsize, DEFAULT_MAX_UNDELIVERED)
        self.assertEqual(default.ttl, DEFAULT_UNDELIVERED_TTL)


class TestBinaryFrameMetadataLimit(unittest.TestCase):
    """HIVEMIND-WIRE-1 §4.1.

    The metadata-length field is 8 bits. A sender MUST NOT build a frame
    whose metadata block exceeds 255 bytes, and MUST either shorten it, rely
    on compression, or send the message as text. Building it anyway makes
    every field after the metadata unreadable.
    """

    def test_compression_rescues_an_over_long_metadata_block(self):
        from hivemind_bus_client.serialization import decode_bitstring, get_bitstring
        metadata = {"pad": "a" * 400}

        frame = get_bitstring(hive_type=HiveMessageType.BUS,
                              payload=Message("speak"),
                              hivemeta=metadata)

        self.assertEqual(decode_bitstring(frame.bytes).metadata, metadata)

    def test_an_incompressible_over_long_metadata_block_is_refused(self):
        from hivemind_bus_client.exceptions import MetadataTooLarge
        from hivemind_bus_client.serialization import get_bitstring
        # high-entropy text: zlib cannot bring this under 255 bytes
        metadata = {"pad": "".join(f"{i:04x}" for i in range(2000, 2200))}

        with self.assertRaises(MetadataTooLarge):
            get_bitstring(hive_type=HiveMessageType.BUS,
                          payload=Message("speak"),
                          hivemeta=metadata)

    def test_an_explicitly_uncompressed_over_long_block_is_refused(self):
        from hivemind_bus_client.exceptions import MetadataTooLarge
        from hivemind_bus_client.serialization import get_bitstring

        with self.assertRaises(MetadataTooLarge):
            get_bitstring(hive_type=HiveMessageType.BUS,
                          payload=Message("speak"),
                          compressed=False,
                          hivemeta={"pad": "a" * 400})


class TestRawAudioFormatRejection(unittest.TestCase):
    """HIVEMIND-AUDIO-1 §2.

    A receiver that cannot process the stated raw-audio format MUST reject
    the payload rather than misinterpret the bytes. There is no in-band
    format negotiation in a raw stream, so a peer that is never told keeps
    streaming into a void.
    """

    def _protocol(self):
        from hivemind_audio_binary_protocol.protocol import AudioBinaryProtocol
        proto = AudioBinaryProtocol.__new__(AudioBinaryProtocol)
        proto.listeners = {}
        proto.refused_streams = set()
        return proto

    @staticmethod
    def _client():
        client = MagicMock()
        client.peer = "satellite::1"
        return client

    @staticmethod
    def _refusals(client):
        return [call.args[0].payload for call in client.send.call_args_list]

    def test_an_unsupported_stream_is_refused_and_not_transcribed(self):
        proto = self._protocol()
        client = self._client()

        proto.handle_microphone_input(b"\x00" * 128, 8000, 2, client)

        refusals = self._refusals(client)
        self.assertEqual(len(refusals), 1)
        self.assertEqual(refusals[0].msg_type,
                         "recognizer_loop:speech.recognition.unknown")
        self.assertEqual(refusals[0].data["error"], "unsupported_audio_format")
        self.assertEqual(proto.listeners, {},
                         "no listener may be started for a format we refuse")

    def test_a_continuous_stream_is_refused_once_not_once_per_chunk(self):
        proto = self._protocol()
        client = self._client()

        for _ in range(20):
            proto.handle_microphone_input(b"\x00" * 128, 44100, 2, client)

        self.assertEqual(len(self._refusals(client)), 1)


if __name__ == "__main__":
    unittest.main()
