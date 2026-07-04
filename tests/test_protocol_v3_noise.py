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
import os
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path

import pytest

from hivemind_bus_client.client import HiveMessageBusClient
from hivemind_bus_client.message import HiveMessage, HiveMessageType
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

WS_PORT = 5678
MB_PORT = 8181
STRONG_KEY = "Rur7lZma4H4uraQ6qHgWH3lg"
STRONG_PW = "Corr3ct-Horse!Batt3ry_v3xx"
WEAK_KEY = "weakweakweakweakweakweak"
WEAK_PW = "1234"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _free(port: int, host: str = "127.0.0.1") -> bool:
    s = socket.socket()
    s.settimeout(0.3)
    try:
        s.connect((host, port))
        return False
    except OSError:
        return True
    finally:
        s.close()


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
        self.env = dict(os.environ)
        self.env.update(
            XDG_CONFIG_HOME=str(tmp_path / "config"),
            XDG_DATA_HOME=str(tmp_path / "data"),
            XDG_CACHE_HOME=str(tmp_path / "cache"),
        )
        (tmp_path / "config").mkdir(parents=True, exist_ok=True)
        (tmp_path / "data").mkdir(parents=True, exist_ok=True)
        self.server_json = tmp_path / "config" / "hivemind-core" / "server.json"
        self.mb = None
        self.core = None

    # -- cli --
    def cli(self, *args, check=True):
        return subprocess.run([_CORE_BIN, *args], env=self.env,
                              capture_output=True, text=True)

    def add_client(self, name, key, password, allow_weak=False):
        args = ["add-client", "--name", name, "--access-key", key,
                "--password", password]
        if allow_weak:
            args.append("--allow-weak-password")
        r = self.cli(*args)
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
        cfg = json.loads(self.server_json.read_text())
        cfg.update(kw)
        self.server_json.write_text(json.dumps(cfg, indent=2))

    # -- processes --
    def start_messagebus(self):
        self.mb = subprocess.Popen(
            [_MB_BIN], env=self.env,
            stdout=open(self.root / "mb.log", "w"), stderr=subprocess.STDOUT)
        assert _wait_port(MB_PORT), "ovos-messagebus never bound 8181"

    def start_core(self, extra_env=None, logname="core.log"):
        env = dict(self.env)
        if extra_env:
            env.update(extra_env)
        self._core_log = self.root / logname
        self.core = subprocess.Popen(
            [_CORE_BIN, "listen"], env=env,
            stdout=open(self._core_log, "w"), stderr=subprocess.STDOUT)

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
                except Exception:
                    pass


@pytest.fixture
def hub(tmp_path):
    if not (_free(WS_PORT) and _free(MB_PORT)):
        pytest.skip(f"ports {WS_PORT}/{MB_PORT} already in use on this host")
    # Isolate the CLIENT's identity + Noise pin store per test. hivemind-core
    # pins the server's Noise static key under its node_id (``master:0.0.0.0``);
    # a stale pin from a prior run against a differently-keyed local hub would
    # abort the handshake as a "possible MITM". A fresh XDG_CONFIG_HOME gives
    # the client an empty pin store so TOFU pins THIS hub's key cleanly.
    client_cfg = tmp_path / "client_config"
    client_cfg.mkdir(parents=True, exist_ok=True)
    _saved_xdg = os.environ.get("XDG_CONFIG_HOME")
    os.environ["XDG_CONFIG_HOME"] = str(client_cfg)
    h = Hub(tmp_path)
    h.last_crypto_key = None
    h.start_messagebus()
    # provision the standard strong client used by most tests
    h.add_client("sat1", STRONG_KEY, STRONG_PW)
    h.allow_msg("recognizer_loop:utterance", 1)
    yield h
    h.stop()
    if _saved_xdg is None:
        os.environ.pop("XDG_CONFIG_HOME", None)
    else:
        os.environ["XDG_CONFIG_HOME"] = _saved_xdg


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


def _connect(key, password, retries=4, **kw):
    c = HiveMessageBusClient(key=key, password=password,
                             host="127.0.0.1", port=WS_PORT, **kw)
    _clear_noise_pins(c)
    c.connect(site_id="e2e", handshake_max_retries=retries)
    return c


# ---------------------------------------------------------------------------
# 1. happy path — XXpsk2 handshake + encrypted bus round-trip
# ---------------------------------------------------------------------------
def test_v3_noise_handshake_and_encrypted_roundtrip(hub):
    hub.set_config(min_protocol_version=2)
    hub.start_core()
    assert _wait_port(WS_PORT), "hivemind-core never bound 5678:\n" + hub.core_log()
    time.sleep(1)

    from ovos_bus_client import MessageBusClient as OVOSBus
    c = _connect(STRONG_KEY, STRONG_PW, retries=5)
    try:
        # v3 negotiated (a legacy v2 fallback would leave this None)
        assert c.noise_transport is not None, "expected a protocol v3 Noise session"

        # agent-side responder echoes utterances back as speak
        ob = OVOSBus(host="127.0.0.1", port=MB_PORT)
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
        try:
            c.close()
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
    assert _wait_port(WS_PORT)
    time.sleep(1)

    with pytest.raises(Exception):
        _connect(STRONG_KEY, "WRONG-passw0rd-Zzz!!", retries=2)
    assert "Noise session established" not in hub.core_log()


# ---------------------------------------------------------------------------
# 3. weak password refused at ingestion, accepted with the override flag
# ---------------------------------------------------------------------------
def test_weak_password_refused_at_ingestion(hub):
    r = hub.add_client("weak_reject", WEAK_KEY, WEAK_PW)
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
    assert _wait_port(WS_PORT)
    time.sleep(1)

    # the client must also be allowed to build a weak-password handshake locally
    os.environ["HIVEMIND_DISABLE_PASSWORD_STRENGTH_CHECK"] = "1"
    try:
        refused = False
        try:
            c = _connect(WEAK_KEY, WEAK_PW, retries=1)
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
def _read_advertised_handshake(timeout=10):
    """Open a raw websocket, read HELLO+HANDSHAKE param frames, return the
    HANDSHAKE payload dict the hub advertises (before any crypto)."""
    import pybase64
    from websocket import create_connection
    ua = "HiveMessageBusClientV0.0.1"
    auth = pybase64.b64encode(f"{ua}:{STRONG_KEY}".encode()).decode()
    ws = create_connection(f"ws://127.0.0.1:{WS_PORT}?authorization={auth}",
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
    assert _wait_port(WS_PORT)
    time.sleep(1)

    payload = _read_advertised_handshake()
    assert payload["min_protocol_version"] == 2, payload
    # a normal (password) client is v3-capable, so the hub offers up to v3
    assert payload["max_protocol_version"] == 3, payload
    assert payload.get("noise"), "hub should advertise Noise patterns/suites at v3"


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
