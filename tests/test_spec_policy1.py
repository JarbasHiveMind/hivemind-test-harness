"""
HIVEMIND-POLICY-1 conformance — the admission-chain MUSTs that had no test.

The chain is the only thing standing between a connected peer and the agent
bus, and almost all of its contract was implemented but unpinned. What is
pinned here:

  * POLICY-1 §2 — policies run in the configured order and the first deny
                  short-circuits the rest of the chain
  * POLICY-1 §2 — a mutation is applied BEFORE the next policy reviews the
                  message, including the replace-the-message form
  * POLICY-1 §5 — a raising *mutation* (not just a raising policy) fails the
                  chain closed with ``policy_error`` naming the offender
  * POLICY-1 §2 — ``observe`` is a post-admission notification: it cannot
                  change the verdict, its failures are ignored, and it does
                  not run for a denied message
  * POLICY-1 §2 — ``client.is_admin`` is informational to the runner; it is
                  not a runner-level bypass
  * POLICY-1 §3/§6 — a denial reaches the client as ``hive.policy.denied``
                  carrying the full documented field set, with the literal
                  ``"binary"`` denied_type for a binary denial
  * POLICY-1 §4 / BRIDGE-1 §5 — the allowed-types whitelist is never written
                  into the OVOS session

Already pinned elsewhere and deliberately not repeated here: the fail-closed
DenyAllPolicy fallback and the non-removable builtin gates
(``test_spec_musts.py``), live whitelist mutation, and the empty-whitelist
binary deny (``test_binary.py``).
"""
from copy import deepcopy

import pytest
from ovos_bus_client.message import Message
from hivemind_bus_client.message import HiveMessage, HiveMessageType
from hivemind_bus_client.serialization import HiveMindBinaryPayloadType

from hivemind_plugin_manager import DenyCodes, Mutation, PolicyPlugin, Verdict
from hivemind_core.policy import PolicyChain

from hivescope.topology import TopologyBuilder

from tests.conftest import poll_until


# ---------------------------------------------------------------------------
# doubles — the collaborators the chain runner is specified against
# ---------------------------------------------------------------------------

class RecordingPolicy(PolicyPlugin):
    """Allows everything and appends its name to a shared log when reviewed."""

    def __init__(self, name, log, verdict=None):
        super().__init__()
        self.name = name
        self.log = log
        self._verdict = verdict
        self.seen = []
        self.observed = []

    def review(self, message, client):
        self.log.append(self.name)
        # A *snapshot* of what this policy was shown, not a live reference:
        # holding the reference would let a mutation applied later in the
        # chain appear to have been visible here, and the test would pass
        # against an implementation that defers every mutation to the end.
        self.seen.append((message, message.msg_type, deepcopy(message.data)))
        return self._verdict if self._verdict is not None else Verdict.allow()

    def observe(self, message, client):
        self.observed.append(message)


class SetDataMutation(Mutation):
    """Mutates in place — the common case."""

    def __init__(self, key, value):
        self.key = key
        self.value = value

    def apply(self, message, client):
        message.data[self.key] = self.value
        return None


class ReplaceMessageMutation(Mutation):
    """Returns a replacement message — the swap-wholesale case."""

    def __init__(self, replacement):
        self.replacement = replacement

    def apply(self, message, client):
        return self.replacement


class ExplodingMutation(Mutation):
    def apply(self, message, client):
        raise RuntimeError("mutation blew up")


def _msg(msg_type="recognizer_loop:utterance"):
    return Message(msg_type, {"utterances": ["hello"]}, {})


class _FakeClient:
    """Minimal stand-in for HiveMindClientConnection.

    The chain runner reads nothing off the connection itself — it only passes
    it through to the policies — except ``is_admin``, which is exactly the
    thing the admin-flag test is about.
    """

    def __init__(self, is_admin=False):
        self.is_admin = is_admin
        self.peer = "fake::peer"


# ---------------------------------------------------------------------------
# POLICY-1 §2 — evaluation order and short-circuit
# ---------------------------------------------------------------------------

class TestChainOrderAndShortCircuit:
    """POLICY-1 §2 — 'policies are called in the order the operator declared
    them, and the first deny short-circuits the chain.'

    Order is part of the contract, not an implementation incidental: an
    operator puts a cheap deny (rate limit, ACL) ahead of an expensive one
    deliberately, and a policy that runs after a deny would observe traffic
    the node already refused.
    """

    def test_policies_are_reviewed_in_the_declared_order(self):
        log = []
        chain = PolicyChain(policies=[RecordingPolicy(n, log) for n in "ABC"])
        verdict = chain.review(_msg(), _FakeClient())
        assert not verdict.denied
        assert log == ["A", "B", "C"], (
            f"the chain must review policies in declared order, got {log}")

    def test_the_first_deny_short_circuits_the_remaining_policies(self):
        log = []
        allow = RecordingPolicy("A", log)
        deny = RecordingPolicy("B", log,
                               verdict=Verdict.deny("quota_exceeded", "no"))
        never = RecordingPolicy("C", log)
        chain = PolicyChain(policies=[allow, deny, never])

        verdict = chain.review(_msg(), _FakeClient())

        assert verdict.denied and verdict.code == "quota_exceeded"
        assert log == ["A", "B"], (
            "a policy after the first deny MUST NOT be reviewed — the chain "
            f"short-circuits; got {log}")
        assert never.seen == []

    def test_the_first_deny_short_circuits_a_binary_review_too(self):
        log = []
        chain = PolicyChain(policies=[
            RecordingPolicyBinary("A", log, deny=True),
            RecordingPolicyBinary("B", log),
        ])
        verdict = chain.review_binary(b"\x00" * 16, _FakeClient())
        assert verdict.denied
        assert log == ["A"], f"binary review must short-circuit too; got {log}"


class RecordingPolicyBinary(PolicyPlugin):
    def __init__(self, name, log, deny=False):
        super().__init__()
        self.name = name
        self.log = log
        self.deny = deny

    def review_binary(self, payload, client):
        self.log.append(self.name)
        return Verdict.deny("nope", "denied") if self.deny else Verdict.allow()


# ---------------------------------------------------------------------------
# POLICY-1 §2 — mutations land before the next policy runs
# ---------------------------------------------------------------------------

class TestMutationsAreAppliedBeforeTheNextPolicy:
    """POLICY-1 §2 — 'mutations MUST be applied before the next policy sees
    the message.'

    A policy that adds a session field or rewrites an utterance is making a
    decision the policies behind it are entitled to judge. Deferring the
    application to the end of the chain would let a later ACL policy vet a
    message that is not the one which reaches the bus.
    """

    def test_a_later_policy_sees_the_mutated_message(self):
        log = []
        mutator = RecordingPolicy("A", log,
                                  verdict=Verdict.allow(SetDataMutation("tag", "set-by-A")))
        watcher = RecordingPolicy("B", log)
        chain = PolicyChain(policies=[mutator, watcher])

        chain.review(_msg(), _FakeClient())

        assert watcher.seen[0][2].get("tag") == "set-by-A", (
            "policy B reviewed a message that policy A's mutation had not yet "
            "been applied to")

    def test_a_replacement_message_is_what_the_later_policy_reviews(self):
        log = []
        replacement = Message("replaced", {"utterances": ["swapped"]}, {})
        mutator = RecordingPolicy(
            "A", log, verdict=Verdict.allow(ReplaceMessageMutation(replacement)))
        watcher = RecordingPolicy("B", log)
        chain = PolicyChain(policies=[mutator, watcher])

        chain.review(_msg(), _FakeClient())

        assert watcher.seen[0][0] is replacement, (
            "a mutation that returns a replacement message must swap the "
            "message the rest of the chain reviews")

    def test_a_raising_mutation_denies_and_names_the_offender(self):
        """POLICY-1 §5 — 'any policy *or mutation* exception MUST become a
        deny naming the offender.' The raising-policy half is pinned in
        test_spec_musts.py; this is the mutation half, which is a separate
        except branch and is what a buggy third-party mutation actually
        trips."""
        log = []
        chain = PolicyChain(policies=[
            RecordingPolicy("A", log, verdict=Verdict.allow(ExplodingMutation())),
            RecordingPolicy("B", log),
        ])

        verdict = chain.review(_msg(), _FakeClient())

        assert verdict.denied, (
            "a mutation that raises MUST fail the chain closed, not be skipped")
        assert verdict.code == DenyCodes.POLICY_ERROR.value
        assert verdict.data.get("policy") == "RecordingPolicy"
        assert verdict.data.get("mutation") == "ExplodingMutation", (
            "the deny MUST name the offending mutation so an operator can "
            f"find it; data={verdict.data}")
        assert log == ["A"], "the chain must not continue past a failed mutation"


# ---------------------------------------------------------------------------
# POLICY-1 §2 — observe() is post-admission and inert
# ---------------------------------------------------------------------------

class ExplodingObserver(PolicyPlugin):
    """Allows everything, but its post-admission notification always raises."""

    def __init__(self):
        super().__init__()
        self.calls = 0

    def observe(self, message, client):
        self.calls += 1
        raise RuntimeError("observer blew up")


class TestObserveIsPostAdmissionOnly:
    """POLICY-1 §2 — 'post-admission notifications MUST NOT change the verdict;
    their failures MUST be ignored.'

    An audit-log or telemetry policy that goes down must not take the bus with
    it. This is tested through the real node rather than against ``PolicyChain``
    alone, because the requirement is about what reaches the agent bus.
    """

    def test_a_raising_observer_does_not_stop_delivery(self):
        ALLOWED = "recognizer_loop:utterance"
        observer = ExplodingObserver()
        b = TopologyBuilder()
        try:
            b.add_master("M0")
            b.add_satellite("S0", upstream=b.get_master("M0"),
                            allowed_types=[ALLOWED])
            b.start_all()
            m0, s0 = b.get_master("M0"), b.get_satellite("S0")
            m0.hm_protocol.policy_chain.policies.append(observer)
            m0.hm_protocol.policy_chain._optional.append(False)

            s0.send(Message(ALLOWED, {"utterances": ["hi"]}))

            poll_until(lambda: m0.agent_protocol.last_injected(ALLOWED) is not None,
                       timeout=3,
                       message="a failing post-admission observer swallowed the "
                               "message — observe() MUST NOT affect delivery")
            assert observer.calls == 1, (
                "observe() must still be invoked for an admitted message")
        finally:
            b.stop_all()

    def test_observe_does_not_run_for_a_denied_message(self):
        DENIED = "speak:synth"   # not on the satellite's whitelist
        observer = ExplodingObserver()
        b = TopologyBuilder()
        try:
            b.add_master("M0")
            b.add_satellite("S0", upstream=b.get_master("M0"),
                            allowed_types=["recognizer_loop:utterance"])
            b.start_all()
            m0, s0 = b.get_master("M0"), b.get_satellite("S0")
            m0.hm_protocol.policy_chain.policies.append(observer)
            m0.hm_protocol.policy_chain._optional.append(False)

            s0.send(Message(DENIED, {"utterance": "hi"}))

            m0.agent_protocol.assert_not_injected(DENIED)
            assert observer.calls == 0, (
                "observe() is a POST-admission notification — it MUST NOT run "
                "for a message the chain denied")
        finally:
            b.stop_all()


# ---------------------------------------------------------------------------
# POLICY-1 §2 — the admin flag is not a runner-level bypass
# ---------------------------------------------------------------------------

class TestAdminFlagIsInformationalToTheRunner:
    """POLICY-1 §2 — 'the admin flag is informational to the runner; a policy
    wanting an exemption MUST check it itself.'

    If the runner ever short-circuits on ``is_admin``, every operator-installed
    policy (quota, audit, content gates) silently stops applying to exactly the
    accounts with the most reach. The flag reaching the policy is the contract;
    the runner acting on it is the violation.
    """

    def test_an_admin_client_still_runs_every_policy(self):
        log = []
        denier = RecordingPolicy("A", log,
                                 verdict=Verdict.deny("quota_exceeded", "no"))
        chain = PolicyChain(policies=[denier])

        verdict = chain.review(_msg(), _FakeClient(is_admin=True))

        assert log == ["A"], (
            "the runner MUST NOT skip a policy because the client is admin")
        assert verdict.denied and verdict.code == "quota_exceeded", (
            "the runner MUST NOT override a policy's deny for an admin client")

    def test_the_policy_receives_the_admin_flag_to_decide_for_itself(self):
        seen = []

        class AdminAware(PolicyPlugin):
            def review(self, message, client):
                seen.append(client.is_admin)
                return Verdict.allow()

        PolicyChain(policies=[AdminAware()]).review(_msg(), _FakeClient(is_admin=True))
        assert seen == [True], (
            "a policy must be able to read is_admin off the connection to "
            "grant its own exemption")


# ---------------------------------------------------------------------------
# POLICY-1 §3 / §6 — the shape of a denial on the wire
# ---------------------------------------------------------------------------

def _denials(satellite):
    """Every ``hive.policy.denied`` payload ``data`` block this satellite got.

    Reads the recorder the same way ``hivescope.assertions`` does — inbound
    BUS records whose inner payload type is ``hive.policy.denied`` — rather
    than re-deriving the record shape.
    """
    out = []
    for r in satellite.recorder.snapshot():
        if r.direction != "in" or r.msg_type != HiveMessageType.BUS.value:
            continue
        payload = r.payload if isinstance(r.payload, dict) else {}
        if payload.get("type") == "hive.policy.denied":
            out.append(payload.get("data") or {})
    return out


class TestDenialPayloadShape:
    """POLICY-1 §3 / §6 — a deny MUST carry a stable machine-readable code and
    a reason, and it reaches the peer as a Layer-1 ``hive.policy.denied`` BUS
    payload with the documented fields.

    A client that cannot tell 'you are not allowed this type' from 'the backend
    is down' cannot retry correctly, which is why §6 fixes the field names
    rather than leaving the shape to the implementation.
    """

    def test_a_message_denial_carries_denied_type_code_reason_and_data(self):
        DENIED = "speak:synth"
        b = TopologyBuilder()
        try:
            b.add_master("M0")
            b.add_satellite("S0", upstream=b.get_master("M0"),
                            allowed_types=["recognizer_loop:utterance"])
            b.start_all()
            s0 = b.get_satellite("S0")

            s0.send(Message(DENIED, {"utterance": "hi"}))

            payload = poll_until(
                lambda: next((d for d in _denials(s0)
                              if d.get("denied_type") == DENIED), None),
                timeout=3,
                message=f"no hive.policy.denied for {DENIED} reached the peer")
            for key in ("denied_type", "code", "reason", "data"):
                assert key in payload, (
                    f"POLICY-1 §6 requires the field {key!r}; got {payload}")
            assert payload["code"] == DenyCodes.ACL_DISALLOWED_TYPE.value, (
                "a whitelist rejection MUST use the registered code "
                f"acl_disallowed_type; got {payload['code']!r}")
            assert payload["reason"], "a deny MUST carry a human-readable reason"
        finally:
            b.stop_all()

    def test_a_binary_denial_uses_the_literal_binary_denied_type(self):
        """POLICY-1 §6 — a binary denial reports ``denied_type == "binary"``
        and additionally names the ``bin_type``. There is no per-tag grant, so
        the tag is diagnostic only — but a client cannot tell *which* upload
        was refused without it."""
        b = TopologyBuilder()
        try:
            b.add_master("M0")
            b.add_satellite("S0", upstream=b.get_master("M0"), allowed_types=[])
            b.start_all()
            s0 = b.get_satellite("S0")

            s0.send(HiveMessage(
                HiveMessageType.BINARY, payload=b"\x00\x01" * 64,
                bin_type=HiveMindBinaryPayloadType.RAW_AUDIO,
                metadata={"sample_rate": 16000, "sample_width": 2}))

            payload = poll_until(
                lambda: next((d for d in _denials(s0)
                              if d.get("denied_type") == "binary"), None),
                timeout=3,
                message="a denied binary payload MUST produce a "
                        "hive.policy.denied with denied_type='binary'")
            assert "bin_type" in payload, (
                f"POLICY-1 §6 requires bin_type on a binary denial; {payload}")
            assert payload["bin_type"] == str(HiveMindBinaryPayloadType.RAW_AUDIO), (
                "bin_type must identify the refused payload tag (WIRE-1 §5 "
                f"tag value); got {payload['bin_type']!r}")
            assert payload.get("code"), "a binary deny still needs a stable code"
        finally:
            b.stop_all()


# ---------------------------------------------------------------------------
# POLICY-1 §4 / BRIDGE-1 §5 — the whitelist is not a session field
# ---------------------------------------------------------------------------

class TestWhitelistIsNotWrittenIntoTheSession:
    """POLICY-1 §4 — 'the whitelist MUST NOT be treated as a Layer-1 session
    field' (BRIDGE-1 §5 says the same from the bridge side).

    An earlier design leaked ``allowed_types`` into ``context["session"]``,
    which meant a skill could read a peer's ACL, and — worse — a peer could
    return a session that looked like a grant. The whitelist lives in the
    client database and is consulted by the gate; it has no business on the
    bus.
    """

    def test_no_session_field_carries_the_allowed_types_grant(self):
        ALLOWED = "recognizer_loop:utterance"
        b = TopologyBuilder()
        try:
            b.add_master("M0")
            b.add_satellite("S0", upstream=b.get_master("M0"),
                            allowed_types=[ALLOWED, "speak"])
            b.start_all()
            m0, s0 = b.get_master("M0"), b.get_satellite("S0")

            s0.send(Message(ALLOWED, {"utterances": ["hi"]}))

            injected = poll_until(
                lambda: m0.agent_protocol.last_injected(ALLOWED), timeout=3,
                message="the allowed message never reached the agent bus")
            session = injected.context.get("session") or {}
            leaked = [k for k, v in session.items()
                      if "allowed" in k.lower() or v == [ALLOWED, "speak"]]
            assert not leaked, (
                "the allowed_types whitelist leaked into the OVOS session "
                f"under {leaked}; POLICY-1 §4 forbids it")
        finally:
            b.stop_all()
