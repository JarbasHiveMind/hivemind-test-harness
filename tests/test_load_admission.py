"""TS-LOAD-01 — sustained admission load against the real listener.

Every other suite here routes through hivescope's in-process loopback, which
never loads a transport plugin. Admission cost is paid on the listener's single
IOLoop, so it only shows up when real sockets arrive together — that is what
these exercise.

Deliberately no assertion on absolute latency: the numbers depend on the host,
and a threshold would either be useless or flaky. These assert the invariants
(everyone gets in, nobody errors, nothing leaks) and *report* the distribution,
so the same test doubles as the benchmark you re-run against a change.

    pytest tests/test_load_admission.py -m slow -s
    HIVEMIND_LOAD_CLIENTS=400 pytest tests/test_load_admission.py -m slow -s
"""
import os
import time

import pytest

from _load import burst

# The suite-wide limit is 30s, which a real burst of several hundred sockets
# will exceed on a loaded host. Raised per-module, as the OVOS-backed e2e
# modules do, rather than relaxing it for everyone.
pytestmark = pytest.mark.timeout(600)

CLIENTS = int(os.environ.get("HIVEMIND_LOAD_CLIENTS", "100"))
# A full PAKE handshake per satellite is far heavier than an admission, so the
# registry tests use a smaller fleet by default.
HANDSHAKE_CLIENTS = int(os.environ.get("HIVEMIND_LOAD_HANDSHAKE_CLIENTS", "15"))


def _connected_client(hub, name):
    """A fully handshaked satellite.

    ``listener.clients`` is only populated by the HELLO handler, not at
    admission, so anything asserting on the client registry has to complete a
    real handshake rather than open a socket.
    """
    from hivemind_bus_client.client import HiveMessageBusClient
    from hivescope.utils import make_identity

    key = hub.register(name)
    identity = make_identity(name)
    identity.access_key = key
    identity.password = hub.password
    identity.default_master = f"ws://{hub.host}"
    identity.default_port = hub.port
    identity.name = name
    identity.site_id = f"{name}-site"

    client = HiveMessageBusClient(
        key=key, password=hub.password, host=f"ws://{hub.host}",
        port=hub.port, useragent=name, self_signed=False, identity=identity,
    )
    client.connect(handshake_max_retries=3)
    return client


def _open_and_admit(hub, sockets, lock, useragent):
    """Connect, then wait for the first server frame — admission is complete
    when the listener answers, which is what ``open()`` gates."""
    import websocket as ws_client

    sock = ws_client.create_connection(hub.socket_url(useragent), timeout=30)
    sock.settimeout(30)
    sock.recv_frame()
    with lock:
        sockets.append(sock)


@pytest.mark.slow
def test_concurrent_admissions_all_succeed(tornado_hub, capsys):
    """N satellites arriving at once are all admitted, none refused."""
    import threading
    sockets, lock = [], threading.Lock()

    result = burst(
        lambda i: _open_and_admit(tornado_hub, sockets, lock, f"sat-{i}"),
        n=CLIENTS,
    )
    for sock in sockets:
        try:
            sock.close()
        except Exception:  # noqa: BLE001
            pass

    with capsys.disabled():
        print(f"\n  admission burst: {result.summary()}")

    assert not result.errors, f"admissions failed: {result.errors[:3]}"
    assert result.ok == CLIENTS


@pytest.mark.slow
def test_listener_releases_every_client_after_a_burst(tornado_hub, poll):
    """A burst must not leak connections: what goes up comes down."""
    import threading
    clients, lock = [], threading.Lock()

    def join(i):
        client = _connected_client(tornado_hub, f"sat-{i}")
        with lock:
            clients.append(client)

    result = burst(join, n=HANDSHAKE_CLIENTS)
    assert not result.errors, f"handshakes failed: {result.errors[:3]}"

    poll(lambda: len(tornado_hub.listener.clients) == len(clients),
         timeout=60, message="listener never registered every client")

    for client in clients:
        client.close()

    poll(lambda: not tornado_hub.listener.clients, timeout=60,
         message="listener still holds clients after every satellite left")


@pytest.mark.slow
def test_reconnect_storm_is_survivable(tornado_hub, capsys):
    """Close everything, then reconnect at once — the shape of a node restart
    seen from the hive."""
    import threading

    def wave():
        sockets, lock = [], threading.Lock()
        result = burst(
            lambda i: _open_and_admit(tornado_hub, sockets, lock, f"sat-{i}"),
            n=CLIENTS)
        for sock in sockets:
            try:
                sock.close()
            except Exception:  # noqa: BLE001
                pass
        return result

    first = wave()
    assert not first.errors, f"first wave failed: {first.errors[:3]}"
    # Settle before reconnecting, as the r34 profile does. Without it the
    # second wave races the first wave's sockets through TIME_WAIT on a
    # single loopback host, which fails the client rather than the node and
    # makes this flaky for reasons that say nothing about the listener.
    time.sleep(0.5)
    second = wave()

    with capsys.disabled():
        print(f"\n  wave 1: {first.summary()}\n  wave 2: {second.summary()}")

    assert not second.errors, (
        f"reconnect storm failed: {len(second.errors)} of {CLIENTS} — "
        f"{second.errors[:5]}")
    assert second.ok == first.ok == CLIENTS
