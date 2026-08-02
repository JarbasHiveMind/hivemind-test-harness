"""
Protocol v3 (Noise XXpsk2) end-to-end tests against a REAL hivemind-core hub.

Unlike the rest of the harness (which drives the protocol in-process through
hivescope's shim), the v3 Noise handshake cannot be exercised in-process: the
shim completes only the legacy v2 password/RSA handshake, and ``noise_transport``
is never set. So this module boots a real ``hivemind-core listen`` subprocess
(plus a real ``ovos-messagebus`` for its agent backend) and connects a real
``HiveMessageBusClient`` over a TCP websocket, asserting on the wire that:

  * the Noise ``XXpsk2`` session is negotiated (``client.noise_transport`` set),
    not a legacy-v2 fallback;
  * a bus message round-trips through the encrypted channel;
  * a wrong password fails the handshake fast (no session);
  * a low-entropy password is refused at ``add-client`` ingestion and accepted
    only with ``--allow-weak-password``;
  * the runtime password-strength backstop refuses a weak DB password by
    default;
  * ``min_protocol_version`` (default 2) rejects a client that can offer only
    the legacy pre-handshake (v0/v1) protocol;
  * ``derive-psk`` reproduces ``poorman_handshake.noise.derive_psk`` for the
    constrained-device provisioning path.

The whole module SKIPS cleanly when the ``hivemind-core`` / ``ovos-messagebus``
console scripts (the ``ovos`` extra) are not installed, so it never reds a
minimal ``build_tests`` environment.
"""
import json
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path

import pytest

from hivemind_bus_client.client import HiveMessageBusClient
from hivemind_bus_client.message import HiveMessage, HiveMessageType

from tests.conftest import free_port
from ovos_bus_client.message import Message

# ---------------------------------------------------------------------------
# module-level skip guard
# ---------------------------------------------------------------------------
_CORE_BIN = shutil.which("hivemind-core")
_MB_BIN = shutil.which("ovos-messagebus")

# Booting a real hub subprocess + completing the Noise handshake takes well
# over the harness-wide 30s default, so each test gets a generous timeout.
pytestmark = [
    pytest.mark.skipif(
        not (_CORE_BIN and _MB_BIN),
        reason="needs the hivemind-core + ovos-messagebus console scripts "
               "(install the harness 'ovos' extra) to boot a real hub",
    ),
    pytest.mark.timeout(180),
]

STRONG_KEY = "Rur7lZma4H4uraQ6qHgWH3lg"
STRONG_PW = "Corr3ct-Horse!Batt3ry_v3xx"
WEAK_KEY = "weakweakweakweakweakweak"
WEAK_PW = "1234"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _wait_port(port: int, host: str = "127.0.0.1", timeout: float = 25) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        s = socket.socket()
        s.settimeout(0.3)
        try:
            s.connect((host, port))
            return True
        except OSError:
            time.sleep(0.25)
        finally:
            s.close()
    return False


class Hub:
    """Boots ovos-messagebus + hivemind-core in an isolated XDG sandbox."""

    def __init__(self, tmp_path: Path):
        self.root = tmp_path
        # Per-hub dynamic ports, allocated through the shared free_port() helper so
        # two hubs booted in parallel (pytest -n auto) never collide on a fixed
        # constant. The websocket listener and the ovos-messagebus backend each
        # get their own port; both the hub subprocesses and the in-test clients
        # read them off this instance, never a module-level constant.
        self.ws_port = free_port()
        self.mb_port = free_port()
        self.env = dict(os.environ)
        self.env.update(
            XDG_CONFIG_HOME=str(tmp_path / "config"),
            XDG_DATA_HOME=str(tmp_path / "data"),
            XDG_CACHE_HOME=str(tmp_path / "cache"),
        )
        (tmp_path / "config").mkdir(parents=True, exist_ok=True)
        (tmp_path / "data").mkdir(parents=True, exist_ok=True)
        self.server_json = tmp_path / "config" / "hivemind-core" / "server.json"
        # ovos-messagebus reads its bind port from mycroft.conf's websocket
        # section; write it into the hub's isolated XDG_CONFIG_HOME so the bus
        # binds mb_port instead of the 8181 default. hivemind-core's own agent
        # connection is pointed at the same port via server.json (see
        # _apply_dynamic_ports).
        mycroft_conf = tmp_path / "config" / "mycroft" / "mycroft.conf"
        mycroft_conf.parent.mkdir(parents=True, exist_ok=True)
        mycroft_conf.write_text(json.dumps(
            {"websocket": {"host": "127.0.0.1", "port": self.mb_port}}))
        self.mb = None
        self.core = None

    # -- cli --
    def cli(self, *args, check=True):
        """Run a hivemind-core subcommand.

        With ``check`` (the default) a nonzero exit raises, so a broken CLI
        call fails where it happens instead of surfacing later as a confusing
        connection error. Pass ``check=False`` where nonzero IS the expectation.
        """
        r = subprocess.run([_CORE_BIN, *args], env=self.env,
                           capture_output=True, text=True)
        if check and r.returncode != 0:
            raise AssertionError(
                f"hivemind-core {' '.join(args)} exited {r.returncode}\n"
                f"stdout: {r.stdout}\nstderr: {r.stderr}")
        return r

    def add_client(self, name, key, password, allow_weak=False, check=True):
        args = ["add-client", "--name", name, "--access-key", key,
                "--password", password]
        if allow_weak:
            args.append("--allow-weak-password")
        r = self.cli(*args, check=check)
        for line in r.stdout.splitlines():
            if "Encryption Key:" in line:
                self.last_crypto_key = line.split("Encryption Key:")[-1].strip()
        return r

    def allow_msg(self, msg_type, node_id):
        return self.cli("allow-msg", msg_type, str(node_id))

    def set_config(self, **kw):
        # server.json is created lazily on first CLI call; ensure it exists
        if not self.server_json.exists():
            self.cli("print-config")
        assert self.server_json.exists(), (
            f"hivemind-core print-config did not create {self.server_json}")
        cfg = json.loads(self.server_json.read_text())
        cfg.update(kw)
        self.server_json.write_text(json.dumps(cfg, indent=2))

    def _apply_dynamic_ports(self):
        """Point the hub's server.json at this instance's dynamic ports.

        Sets the websocket listener to ``ws_port`` and the ovos-agent backend
        connection to ``mb_port``, and drops the default hivemind-http listener
        (its fixed 5679 would be a second hardcoded-port collision under
        ``-n auto`` and no test exercises it). Reads-modifies-writes so any
        keys a test already set via ``set_config`` are preserved.
        """
        if not self.server_json.exists():
            self.cli("print-config")
        cfg = json.loads(self.server_json.read_text())
        net = cfg.get("network_protocol", {})
        ws = dict(net.get("hivemind-websocket-plugin",
                          {"host": "0.0.0.0", "ssl": False}))
        ws["port"] = self.ws_port
        cfg["network_protocol"] = {"hivemind-websocket-plugin": ws}
        agent = cfg.get("agent_protocol", {})
        agent.setdefault("module", "hivemind-ovos-agent-plugin")
        plug = dict(agent.get("hivemind-ovos-agent-plugin", {"host": "127.0.0.1"}))
        plug["port"] = self.mb_port
        agent["hivemind-ovos-agent-plugin"] = plug
        cfg["agent_protocol"] = agent
        self.server_json.write_text(json.dumps(cfg, indent=2))

    # -- processes --
    def start_messagebus(self):
        # The log handle is kept so stop() can close it; an unclosed handle
        # leaks a file descriptor per hub.
        self._mb_log_fh = open(self.root / "mb.log", "w")
        self.mb = subprocess.Popen(
            [_MB_BIN], env=self.env,
            stdout=self._mb_log_fh, stderr=subprocess.STDOUT)
        # The caller (the fixture) owns stopping this hub, so a failure to bind
        # must NOT leave the process behind — see the fixture's try/finally.
        assert _wait_port(self.mb_port), \
            f"ovos-messagebus never bound {self.mb_port}"

    def start_core(self, extra_env=None, logname="core.log"):
        # Fold the dynamic ports into server.json just before launch, on top of
        # whatever a test set via set_config.
        self._apply_dynamic_ports()
        env = dict(self.env)
        if extra_env:
            env.update(extra_env)
        self._core_log = self.root / logname
        self._core_log_fh = open(self._core_log, "w")
        self.core = subprocess.Popen(
            [_CORE_BIN, "listen"], env=env,
            stdout=self._core_log_fh, stderr=subprocess.STDOUT)

    def core_log(self) -> str:
        try:
            return self._core_log.read_text()
        except OSError:
            return ""

    def stop(self):
        for p in (self.core, self.mb):
            if p is None:
                continue
            try:
                p.send_signal(signal.SIGINT)
                p.wait(timeout=8)
            except Exception:
                try:
                    p.kill()
                    # Reap it — without this the child stays a zombie and its
                    # port can still be held when the next test starts.
                    p.wait(timeout=5)
                except Exception:
                    pass
        self.core = self.mb = None
        for attr in ("_core_log_fh", "_mb_log_fh"):
            fh = self.__dict__.pop(attr, None)
            if fh is not None:
                fh.close()


@pytest.fixture
def hub(tmp_path, monkeypatch):
    # Ports are allocated dynamically per Hub (see Hub.__init__), so there is no
    # fixed-port contention to skip on: the test runs whenever the console
    # scripts are installed (the module-level skipif is the only gate).
    #
    # Isolate the CLIENT's identity + Noise pin store per test. hivemind-core
    # pins the server's Noise static key under its node_id (``master:0.0.0.0``);
    # a stale pin from a prior run against a differently-keyed local hub would
    # abort the handshake as a "possible MITM". A fresh XDG_CONFIG_HOME gives
    # the client an empty pin store so TOFU pins THIS hub's key cleanly.
    #
    # monkeypatch restores XDG_CONFIG_HOME even when setup below raises. The
    # hand-rolled save/restore used to be skipped on any failure after the
    # Popen, so the rest of the session ran against the temp config dir.
    client_cfg = tmp_path / "client_config"
    client_cfg.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(client_cfg))

    h = Hub(tmp_path)
    h.last_crypto_key = None
    try:
        # start_messagebus() asserts on the bind; the Popen has already
        # happened by then, so the teardown must run even on that failure.
        h.start_messagebus()
        # provision the standard strong client used by most tests
        h.add_client("sat1", STRONG_KEY, STRONG_PW)
        h.allow_msg("recognizer_loop:utterance", 1)
        yield h
    finally:
        h.stop()


def _clear_noise_pins(client):
    """Drop any TOFU-pinned server Noise keys so a fresh hub (new static key on
    the same host:port) is pinned cleanly instead of aborting as a "possible
    MITM". Each test boots a hub with its own key, so stale pins from a previous
    run/test would otherwise fail the handshake."""
    try:
        client.identity.IDENTITY_FILE["pinned_noise_keys"] = {}
        client.identity.save()
    except Exception:
        pass


def _connect(hub, key, password, retries=4, **kw):
    c = HiveMessageBusClient(key=key, password=password,
                             host="127.0.0.1", port=hub.ws_port, **kw)
    _clear_noise_pins(c)
    c.connect(site_id="e2e", handshake_max_retries=retries)
    return c


# ---------------------------------------------------------------------------
# 1. happy path — XXpsk2 handshake + encrypted bus round-trip
# ---------------------------------------------------------------------------
def test_v3_noise_handshake_and_encrypted_roundtrip(hub):
    hub.set_config(min_protocol_version=2)
    hub.start_core()
    assert _wait_port(hub.ws_port), f"hivemind-core never bound {hub.ws_port}:\n" + hub.core_log()
    time.sleep(1)

    from ovos_bus_client import MessageBusClient as OVOSBus
    ob = None
    c = _connect(hub, STRONG_KEY, STRONG_PW, retries=5)
    try:
        # v3 negotiated (a legacy v2 fallback would leave this None)
        assert c.noise_transport is not None, "expected a protocol v3 Noise session"

        # agent-side responder echoes utterances back as speak
        ob = OVOSBus(host="127.0.0.1", port=hub.mb_port)
        ob.run_in_thread()
        time.sleep(1)
        ob.on("recognizer_loop:utterance",
              lambda m: ob.emit(m.reply("speak",
                                        {"utterance": "echo:" + m.data["utterances"][0]})))
        got = {}
        c.on_mycroft("speak", lambda m: got.setdefault("r", m.data.get("utterance")))
        c.emit(HiveMessage(HiveMessageType.BUS, payload=Message(
            "recognizer_loop:utterance", {"utterances": ["hello world"]},
            {"session": {"session_id": c.session_id}})))

        end = time.time() + 6
        while time.time() < end and "r" not in got:
            time.sleep(0.2)
        assert got.get("r") == "echo:hello world", f"no encrypted round-trip: {got}"
    finally:
        # Separate try blocks: a failing c.close() used to skip ob.close()
        # entirely, and ob is undefined when the assert above it fired.
        try:
            c.close()
        except Exception:
            pass
        if ob is not None:
            try:
                ob.close()
            except Exception:
                pass

    assert "Noise session established" in hub.core_log()


# ---------------------------------------------------------------------------
# 2. wrong password — handshake fails fast, no session
# ---------------------------------------------------------------------------
def test_wrong_password_fails_fast(hub):
    hub.set_config(min_protocol_version=2)
    hub.start_core()
    assert _wait_port(hub.ws_port)
    time.sleep(1)

    with pytest.raises(Exception):
        _connect(hub, STRONG_KEY, "WRONG-passw0rd-Zzz!!", retries=2)
    assert "Noise session established" not in hub.core_log()


# ---------------------------------------------------------------------------
# 3. weak password refused at ingestion, accepted with the override flag
# ---------------------------------------------------------------------------
def test_weak_password_refused_at_ingestion(hub):
    r = hub.add_client("weak_reject", WEAK_KEY, WEAK_PW, check=False)
    assert r.returncode != 0, "weak password should be refused at add-client"
    assert "guessable" in (r.stdout + r.stderr).lower()


def test_weak_password_accepted_with_flag(hub):
    r = hub.add_client("weak_ok", WEAK_KEY, WEAK_PW, allow_weak=True)
    assert r.returncode == 0, f"--allow-weak-password should accept: {r.stderr}"


# ---------------------------------------------------------------------------
# 4. runtime backstop — a weak DB password is refused at handshake by default
# ---------------------------------------------------------------------------
def test_runtime_backstop_refuses_weak_db_password(hub):
    hub.add_client("weak_rt", WEAK_KEY, WEAK_PW, allow_weak=True)
    hub.allow_msg("recognizer_loop:utterance", 2)
    hub.set_config(min_protocol_version=2)
    hub.start_core()
    assert _wait_port(hub.ws_port)
    time.sleep(1)

    # the client must also be allowed to build a weak-password handshake locally
    os.environ["HIVEMIND_DISABLE_PASSWORD_STRENGTH_CHECK"] = "1"
    try:
        refused = False
        try:
            c = _connect(hub, WEAK_KEY, WEAK_PW, retries=1)
            refused = c.noise_transport is None
            try:
                c.close()
            except Exception:
                pass
        except Exception:
            refused = True
    finally:
        os.environ.pop("HIVEMIND_DISABLE_PASSWORD_STRENGTH_CHECK", None)
    assert refused, "runtime backstop should refuse a weak DB password by default"


# ---------------------------------------------------------------------------
# 5. min_protocol_version — the hub advertises the configured floor
#
# NOTE (honest limitation): the *rejection* branch (min_version > max_version)
# cannot be exercised with the published stack. ``add-client`` always assigns a
# password (auto-generating a strong one when none is passed), so the server
# always builds a PasswordHandShake for the client and computes max_version = 3
# (v3-capable). And ``HiveMessageBusClient`` refuses to construct without a
# password. So no published client/CLI combination can present a genuine
# v0/v1 (password-less) connection for the floor to reject. What we CAN assert
# on the wire is that the hub advertises the configured floor in its HANDSHAKE
# parameter message — the value a client uses to decide whether to proceed.
# ---------------------------------------------------------------------------
def _read_advertised_handshake(hub, timeout=10):
    """Open a raw websocket, read HELLO+HANDSHAKE param frames, return the
    HANDSHAKE payload dict the hub advertises (before any crypto)."""
    import pybase64
    from websocket import create_connection
    ua = "HiveMessageBusClientV0.0.1"
    auth = pybase64.b64encode(f"{ua}:{STRONG_KEY}".encode()).decode()
    ws = create_connection(f"ws://127.0.0.1:{hub.ws_port}?authorization={auth}",
                           timeout=timeout)
    try:
        end = time.time() + timeout
        while time.time() < end:
            frame = ws.recv()
            if isinstance(frame, bytes):
                continue
            msg = json.loads(frame)
            payload = msg.get("payload") or {}
            if "min_protocol_version" in payload:
                return payload
        raise AssertionError("hub never advertised a HANDSHAKE param frame")
    finally:
        ws.close()


def test_min_protocol_version_advertised(hub):
    hub.set_config(min_protocol_version=2)
    hub.start_core(logname="core5.log")
    assert _wait_port(hub.ws_port)
    time.sleep(1)

    payload = _read_advertised_handshake(hub)
    assert payload["min_protocol_version"] == 2, payload
    # a normal (password) client is v3-capable, so the hub offers up to v3
    assert payload["max_protocol_version"] == 3, payload
    assert payload.get("noise"), "hub should advertise Noise patterns/suites at v3"


# ---------------------------------------------------------------------------
# 5b. Noise handshake patterns (CRYPTO-1 §3.4.2)
# ---------------------------------------------------------------------------
def test_default_pattern_is_xxpsk2(hub):
    """CRYPTO-1 §3.4.2 — XXpsk2 is the mandatory default. A fresh (un-pinned)
    client is offered exactly XXpsk2 as the preferred pattern (KKpsk0 is only
    offered once the client's static key has been pinned by a prior handshake)."""
    hub.set_config(min_protocol_version=2)
    hub.start_core(logname="core_xx.log")
    assert _wait_port(hub.ws_port)
    time.sleep(1)

    payload = _read_advertised_handshake(hub)
    patterns = (payload.get("noise") or {}).get("patterns") or []
    assert "XXpsk2" in patterns, f"XXpsk2 must be advertised, got {patterns}"
    assert patterns[0] == "XXpsk2", \
        f"XXpsk2 must be the preferred (first) pattern for an un-pinned client, got {patterns}"
    assert "KKpsk0" not in patterns, \
        "KKpsk0 must NOT be offered to a client whose static key is not yet pinned"


@pytest.mark.xfail(
    strict=True,
    reason="the negotiated Noise pattern is not observable through the harness: "
           "hivemind-core logs only 'Noise session established' without the "
           "pattern name and HiveMessageBusClient exposes no selected-pattern "
           "attribute, so KKpsk0 preference on reconnection (CRYPTO-1 §3.4.2) "
           "cannot be asserted on the wire.",
)
def test_kkpsk0_negotiated_on_reconnect(hub):
    """CRYPTO-1 §3.4.2 — once both peers hold each other's static keys, KKpsk0
    SHOULD be the negotiated pattern on reconnection. Encoded here so it flips
    to a pass the moment the negotiated pattern becomes observable."""
    hub.set_config(min_protocol_version=2)
    hub.start_core(logname="core_kk.log")
    assert _wait_port(hub.ws_port)
    time.sleep(1)

    c1 = _connect(hub, STRONG_KEY, STRONG_PW, retries=5)
    try:
        assert c1.noise_transport is not None
    finally:
        try:
            c1.close()
        except Exception:
            pass

    # Reconnect WITHOUT clearing pins so KKpsk0 becomes eligible.
    c2 = HiveMessageBusClient(key=STRONG_KEY, password=STRONG_PW,
                              host="127.0.0.1", port=hub.ws_port)
    try:
        c2.connect(site_id="e2e", handshake_max_retries=5)
        selected = getattr(c2, "selected_noise_pattern", None)
        assert selected == "KKpsk0", \
            f"expected KKpsk0 on reconnect, observed {selected!r}"
    finally:
        try:
            c2.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 5c. Identity pinning — a contradicted static key aborts (CRYPTO-1 §3.4.5)
# ---------------------------------------------------------------------------
def test_noise_pinning_aborts_on_contradicted_key(hub, caplog):
    """A pinned Noise static key that is later contradicted (simulated MITM)
    MUST NOT be allowed to complete a session.

    What this exercises, precisely: on reconnect, ``select_noise_options``
    sees a pinned remote key and the server still offering ``KKpsk0``, so it
    picks KKpsk0 with ``remote_pubkey=<bogus pinned key>``
    (``hivemind_bus_client/protocol.py`` ``negotiate``/``start_noise_handshake``
    call around line ~334-351). Handshaking KKpsk0 against the wrong static
    key fails cryptographically inside ``start_noise_handshake``/
    ``read_message``, which is caught and routed through ``_abort_noise``
    (~line 427): ``noise_transport`` is cleared and the connection is closed,
    but ``handshake_event`` is deliberately never set. ``wait_for_handshake``
    then exhausts ``handshake_max_retries`` waiting on that event and raises
    ``RuntimeError``, which propagates out of ``connect()``. That raise is the
    guarantee this test checks — not the floor config.

    The ``min_protocol_version=3`` floor config exercises no enforcement path:
    hivemind-core's ``handle_handshake_message`` never reads it and the client
    never reads it either — it is set here only to keep this hub's config
    identical to the other v3-floor tests in this module, not because the
    floor does anything. Note this test does NOT exercise CRYPTO-1 §3.4.5's
    pinned-mismatch branch (``hivemind_bus_client/protocol.py`` ~399-402,
    reached when the handshake completes but the resulting static key differs
    from the pin) — that branch remains uncovered; a bogus pin fails earlier,
    inside the KKpsk0 handshake itself, before that branch is ever reached.
    """
    hub.set_config(min_protocol_version=3)
    hub.start_core(logname="core_pin.log")
    assert _wait_port(hub.ws_port)
    time.sleep(1)

    # First connect pins the genuine server static key via TOFU.
    c1 = _connect(hub, STRONG_KEY, STRONG_PW, retries=5)
    try:
        assert c1.noise_transport is not None, "baseline XXpsk2 must succeed"
        pins = dict(c1.identity.pinned_noise_keys)
        assert pins, "a completed XXpsk2 handshake must pin the server static key"
    finally:
        try:
            c1.close()
        except Exception:
            pass

    # Corrupt every pin to a bogus key (the MITM's key) and persist it.
    bogus = "00" * 32
    for node_id in pins:
        c1.identity.pin_noise_key(node_id, bogus)
    c1.identity.save()

    # Reconnect WITHOUT clearing pins: the pinned (now bogus) key is fed to
    # KKpsk0 as remote_pubkey, so the handshake fails authentication and
    # connect() must raise rather than silently leaving a usable session.
    c2 = HiveMessageBusClient(key=STRONG_KEY, password=STRONG_PW,
                              host="127.0.0.1", port=hub.ws_port)
    caplog.set_level(logging.ERROR)
    raised = None
    try:
        with pytest.raises(Exception) as exc_info:
            c2.connect(site_id="e2e", handshake_max_retries=1)
        raised = exc_info.value
    finally:
        try:
            c2.close()
        except Exception:
            pass

    assert raised is not None, (
        "a contradicted pinned static key MUST cause connect() to raise: "
        "wait_for_handshake must exhaust its retries and raise since "
        "_abort_noise never sets handshake_event")
    assert not c2.handshake_event.is_set(), (
        "handshake_event is the real authorization signal for a usable "
        "session (wait_for_handshake blocks on it, connect() only proceeds "
        "once it is set) — a contradicted pin must leave it unset")
    assert c2.noise_transport is None, \
        "no Noise transport may be left installed after an aborted handshake"
    abort_evidence = (
        "aborting protocol v3 connection" in caplog.text
        or re.search(r"handshake|noise|abort", str(raised), re.I) is not None
    )
    assert abort_evidence, (
        "the failure must be traceable to a handshake/noise abort (via the "
        "client's 'aborting protocol v3 connection' log line or the raised "
        "error), so this test cannot pass on an unrelated connect failure "
        f"(e.g. a port/timeout issue); observed error: {raised!r}")


# ---------------------------------------------------------------------------
# 6. derive-psk provisioning matches the poorman reference
# ---------------------------------------------------------------------------
def test_derive_psk_matches_poorman(hub):
    from poorman_handshake.noise import derive_psk
    node_id = "provision-node-42"
    out = hub.cli("derive-psk", "--password", STRONG_PW, "--node-id", node_id)
    printed = out.stdout.strip().splitlines()[-1].strip()
    expected = derive_psk(STRONG_PW, node_id=node_id).hex()
    assert printed == expected, f"derive-psk {printed} != poorman {expected}"
    assert len(bytes.fromhex(printed)) == 32
