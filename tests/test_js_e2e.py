"""
End-to-end tests for JavaScript client against loopback harness master.

These tests launch a Node.js subprocess (js_e2e_driver.mjs) that connects to
a loopback harness master via real WebSocket, performs handshake, and exchanges
messages. The Python side monitors the hub to verify message receipt.

Test IDs:
- JS-E2E-01: JavaScript client connects and sends utterance
- JS-E2E-02: Hub receives utterance from JS client
"""

import shutil
import subprocess
import time
from pathlib import Path

import pytest

from hivescope.topology import TopologyBuilder
from hivemind_bus_client.message import HiveMessageType

# Node.js is an external runtime, not something the driver can install. Decide
# once, at collection time — a missing `node` used to surface as a driver
# failure whose stderr the tests then string-matched.
pytestmark = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="Node.js (`node`) is not on PATH; the JS client driver cannot run",
)

# The hub records the utterance from its own thread after the node process
# exits, so poll instead of reading the agent state straight away.
_HUB_POLL_DEADLINE = 10.0


def _wait_for_utterances(master, deadline: float = _HUB_POLL_DEADLINE):
    """Poll the hub until it has injected at least one utterance."""
    end = time.monotonic() + deadline
    while True:
        found = [msg for msg in master.agent_protocol.injected
                 if msg.msg_type == "recognizer_loop:utterance"]
        if found or time.monotonic() > end:
            return found
        time.sleep(0.1)


class TestJSE2E:
    """JavaScript client E2E tests using loopback harness."""

    def _get_js_driver_path(self) -> Path:
        """Get absolute path to js_e2e_driver.mjs."""
        return Path(__file__).resolve().parent.parent / "test_helpers" / "js_e2e_driver.mjs"

    def test_js_client_connects_and_sends_utterance(self):
        """JS-E2E-01: JavaScript client connects and sends utterance.

        Launches Node.js driver that connects to loopback hub via WebSocket,
        performs handshake, and sends an utterance. Verifies subprocess exits
        successfully.
        """
        # Setup: Create topology with loopback master
        b = TopologyBuilder()
        try:
            m = b.add_master("M0", use_loopback=True)
            m.register_satellite("js-sat", password="glide-tavern-plum-yonder-58",
                                 allowed_types=["recognizer_loop:utterance"])
            b.start_all()

            # Get driver path
            driver_path = self._get_js_driver_path()
            if not driver_path.exists():
                pytest.skip(f"JS driver not found at {driver_path}")

            # Extract host and port from URL (format: ws://127.0.0.1:PORT/)
            url = m.network_protocol.url

            # Run the Node.js driver.
            result = subprocess.run(
                [
                    "node",
                    str(driver_path),
                    url,
                    "js-sat",
                    "js-sat",  # name and key are same in test
                    "glide-tavern-plum-yonder-58",
                    "hello from javascript",
                ],
                capture_output=True,
                text=True,
                timeout=20,
            )

            print("STDOUT:", result.stdout)
            print("STDERR:", result.stderr)
            assert result.returncode == 0, f"JS driver failed with code {result.returncode}"

        finally:
            b.stop_all()

    def test_hub_receives_utterance_from_js_client(self):
        """JS-E2E-02: Hub receives and records utterance from JS client.

        Verifies that the loopback hub correctly received and recorded
        the utterance message from the JavaScript client.
        """
        # Setup
        b = TopologyBuilder()
        try:
            m = b.add_master("M0", use_loopback=True)
            m.register_satellite("js-sat2", password="moss-quiver-lantern-drift-71",
                                 allowed_types=["recognizer_loop:utterance"])
            b.start_all()

            driver_path = self._get_js_driver_path()
            if not driver_path.exists():
                pytest.skip(f"JS driver not found at {driver_path}")

            url = m.network_protocol.url

            # Run driver
            result = subprocess.run(
                [
                    "node",
                    str(driver_path),
                    url,
                    "js-sat2",
                    "js-sat2",
                    "moss-quiver-lantern-drift-71",
                    "test utterance from js",
                ],
                capture_output=True,
                text=True,
                timeout=20,
            )

            # Check exit code (may fail if 'node' not available, that's ok)
            if result.returncode != 0:
                print("STDOUT:", result.stdout)
                print("STDERR:", result.stderr)
                pytest.fail(
                    f"JS driver exited {result.returncode}: {result.stderr}")

            utterance_messages = _wait_for_utterances(m)
            assert len(utterance_messages) > 0, (
                f"No utterance messages received on hub within {_HUB_POLL_DEADLINE}s. "
                f"Injected: {m.agent_protocol.injected}, "
                f"Records: {m.recorder.records}"
            )

        finally:
            b.stop_all()

    def test_session_id_propagated_in_utterance(self):
        """JS-E2E-03: Session ID is propagated in utterance context.

        Verifies that the JS client includes session.session_id in the
        bus message context, so the hub can associate the message with
        the correct session instead of falling back to 'default'.
        """
        b = TopologyBuilder()
        try:
            m = b.add_master("M0", use_loopback=True)
            m.register_satellite("js-sat3", password="copper-nimbus-fjord-waltz-93",
                                 allowed_types=["recognizer_loop:utterance"])
            b.start_all()

            driver_path = self._get_js_driver_path()
            if not driver_path.exists():
                pytest.skip(f"JS driver not found at {driver_path}")

            url = m.network_protocol.url

            result = subprocess.run(
                [
                    "node",
                    str(driver_path),
                    url,
                    "js-sat3",
                    "js-sat3",
                    "copper-nimbus-fjord-waltz-93",
                    "session test utterance",
                ],
                capture_output=True,
                text=True,
                timeout=20,
            )

            if result.returncode != 0:
                print("STDOUT:", result.stdout)
                print("STDERR:", result.stderr)
                pytest.fail(
                    f"JS driver exited {result.returncode}: {result.stderr}")

            # Check that at least one injected utterance has session context
            utterance_messages = _wait_for_utterances(m)
            assert len(utterance_messages) > 0, (
                f"No utterance messages received on hub within {_HUB_POLL_DEADLINE}s. "
                f"Injected: {m.agent_protocol.injected}"
            )

            # Verify session_id is present and not 'default'
            for msg in utterance_messages:
                ctx = msg.context or {}
                session = ctx.get("session", {})
                session_id = session.get("session_id", "")
                assert session_id, (
                    f"session_id missing from utterance context: {ctx}"
                )
                assert session_id != "default", (
                    f"session_id should not be 'default': {ctx}"
                )

        finally:
            b.stop_all()
