"""TS-RDV-01..09 — RENDEZVOUS store-and-forward conformance.

A rendezvous node is an ordinary master that holds mail for peers which are
never online at the same time. One satellite deposits a message addressed to
another's public key; the recipient collects it later and acknowledges it.

These tests pin the four properties the type has to get right, because getting
any of them wrong is silent:

* a caller cannot name a mailbox — it only ever gets the one belonging to the
  public key its connection was pinned to
* delivery is at-least-once — collect leaves messages pending, ack removes them
* only INTERCOM may be deposited, since that is the one type the relay cannot read
* a node without a mailbox says so, instead of dropping the frame

Requires the optional ``hivemind-rendezvous`` package; skipped without it.
"""
import tempfile
from unittest.mock import patch

import pytest
from hivemind_bus_client.message import HiveMessage, HiveMessageType
from hivescope.topology import TopologyBuilder

rendezvous = pytest.importorskip("hivemind_rendezvous",
                                 reason="hivemind-rendezvous is optional")
from hivemind_rendezvous.mailbox import RendezvousMailbox  # noqa: E402
from hivemind_rendezvous.storage import RendezvousStore  # noqa: E402

TIMEOUT = 10.0


@pytest.fixture()
def hive(tmp_path):
    """A master holding a mailbox, with two satellites attached."""
    b = TopologyBuilder()
    b.add_master("M0")
    b.add_satellite("S0", upstream=b.get_master("M0"))
    b.add_satellite("S1", upstream=b.get_master("M0"))
    b.start_all()
    # the store is XDG-backed and persists between runs, so give it a
    # directory this test owns
    with patch("hivemind_rendezvous.storage.xdg_data_home",
               return_value=str(tmp_path)):
        store = RendezvousStore(store_name="harness_rendezvous")
    b.get_master("M0").hm_protocol.mailbox = RendezvousMailbox(store=store)
    try:
        yield b
    finally:
        b.stop_all()


@pytest.fixture()
def plain_hive():
    """A master with no mailbox — an ordinary node."""
    b = TopologyBuilder()
    b.add_master("M0")
    b.add_satellite("S0", upstream=b.get_master("M0"))
    b.start_all()
    try:
        yield b
    finally:
        b.stop_all()


def _pubkey_of(builder, master_name, satellite_key):
    """The public key the master pinned for a satellite's access key."""
    return builder.get_master(master_name).hm_protocol.trusted_pubkeys.get(satellite_key)


def _ask(satellite, **payload):
    """Send one RENDEZVOUS request and return the reply payload."""
    satellite.recorder.clear()
    satellite.send(HiveMessage(HiveMessageType.RENDEZVOUS, payload=payload))
    rec = satellite.recorder.wait_for(HiveMessageType.RENDEZVOUS,
                                      direction="in", timeout=TIMEOUT)
    assert rec is not None, "no RENDEZVOUS reply from the master"
    return rec.payload if isinstance(rec.payload, dict) else rec.payload.payload


def _intercom(text="ping"):
    return HiveMessage(HiveMessageType.INTERCOM,
                       payload={"ciphertext": text}).serialize()


def _identity_key(satellite):
    return satellite.identity.access_key


class TestRendezvousDeposit:

    def test_deposit_is_accepted(self, hive):
        """TS-RDV-01 — a satellite deposits INTERCOM mail for a peer."""
        s0, s1 = hive.get_satellite("S0"), hive.get_satellite("S1")
        target = _pubkey_of(hive, "M0", _identity_key(s1))
        reply = _ask(s0, cmd="deposit", target_pubkey=target, payload=_intercom())
        assert reply["status"] == "ok"
        assert reply["deposit_id"]

    def test_only_intercom_may_be_deposited(self, hive):
        """TS-RDV-02 — the relay refuses anything it could read."""
        s0, s1 = hive.get_satellite("S0"), hive.get_satellite("S1")
        target = _pubkey_of(hive, "M0", _identity_key(s1))
        plain = HiveMessage(HiveMessageType.BUS,
                            payload={"type": "speak"}).serialize()
        reply = _ask(s0, cmd="deposit", target_pubkey=target, payload=plain)
        assert reply["reason"] == "payload_must_be_intercom"

    def test_deposit_without_fields_is_refused(self, hive):
        """TS-RDV-03 — a malformed deposit is named, not ignored."""
        reply = _ask(hive.get_satellite("S0"), cmd="deposit")
        assert reply["reason"] == "missing_fields"


class TestRendezvousCollect:

    def test_recipient_collects_what_was_deposited(self, hive):
        """TS-RDV-04 — mail deposited for S1 reaches S1."""
        s0, s1 = hive.get_satellite("S0"), hive.get_satellite("S1")
        target = _pubkey_of(hive, "M0", _identity_key(s1))
        _ask(s0, cmd="deposit", target_pubkey=target, payload=_intercom("hello"))
        reply = _ask(s1, cmd="collect")
        assert len(reply["messages"]) == 1
        inner = HiveMessage.deserialize(reply["messages"][0]["payload"])
        assert inner.payload["ciphertext"] == "hello"

    def test_a_caller_cannot_collect_another_mailbox(self, hive):
        """TS-RDV-05 — naming a pubkey in the request changes nothing.

        The mailbox served is the one belonging to the pinned key of this
        connection, so S0 asking for S1's mail gets its own empty box.
        """
        s0, s1 = hive.get_satellite("S0"), hive.get_satellite("S1")
        target = _pubkey_of(hive, "M0", _identity_key(s1))
        _ask(s0, cmd="deposit", target_pubkey=target, payload=_intercom())
        reply = _ask(s0, cmd="collect", pubkey=target)
        assert reply["messages"] == []

    def test_collect_does_not_delete(self, hive):
        """TS-RDV-06 — delivery is at-least-once, so a lost reply is survivable."""
        s0, s1 = hive.get_satellite("S0"), hive.get_satellite("S1")
        target = _pubkey_of(hive, "M0", _identity_key(s1))
        _ask(s0, cmd="deposit", target_pubkey=target, payload=_intercom())
        assert len(_ask(s1, cmd="collect")["messages"]) == 1
        assert len(_ask(s1, cmd="collect")["messages"]) == 1


class TestRendezvousAck:

    def test_ack_removes_the_message(self, hive):
        """TS-RDV-07 — acked mail is gone on the next collect."""
        s0, s1 = hive.get_satellite("S0"), hive.get_satellite("S1")
        target = _pubkey_of(hive, "M0", _identity_key(s1))
        _ask(s0, cmd="deposit", target_pubkey=target, payload=_intercom())
        pending = _ask(s1, cmd="collect")["messages"]
        reply = _ask(s1, cmd="ack",
                     deposit_ids=[m["deposit_id"] for m in pending])
        assert reply["removed"] == 1
        assert _ask(s1, cmd="collect")["messages"] == []

    def test_one_node_cannot_ack_anothers_mail(self, hive):
        """TS-RDV-08 — acking is scoped to the caller's own mailbox."""
        s0, s1 = hive.get_satellite("S0"), hive.get_satellite("S1")
        target = _pubkey_of(hive, "M0", _identity_key(s1))
        deposit_id = _ask(s0, cmd="deposit", target_pubkey=target,
                          payload=_intercom())["deposit_id"]
        _ask(s0, cmd="ack", deposit_ids=[deposit_id])
        assert len(_ask(s1, cmd="collect")["messages"]) == 1


class TestNodeWithoutMailbox:

    def test_ordinary_node_says_it_is_not_a_rendezvous_node(self, plain_hive):
        """TS-RDV-09 — silence would be indistinguishable from an empty mailbox.

        A peer that cannot tell "no mail" from "wrong node" cannot fail over to
        a node that does hold its mail.
        """
        reply = _ask(plain_hive.get_satellite("S0"), cmd="collect")
        assert reply["status"] == "error"
        assert reply["reason"] == "not_a_rendezvous_node"
