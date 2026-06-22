"""
End-to-end tests for JavaScript client against loopback harness master.

These tests launch a Node.js subprocess (js_e2e_driver.mjs) that connects to
a loopback harness master via real WebSocket, performs handshake, and exchanges
messages. The Python side monitors the hub to verify message receipt.

Test IDs:
- JS-E2E-01: JavaScript client connects and sends utterance
- JS-E2E-02: Hub receives utterance from JS client
"""

import subprocess
import sys
from pathlib import Path

import pytest

from hivescope.topology import TopologyBuilder
from hivemind_bus_client.message import HiveMessageType


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
        m = b.add_master("M0", use_loopback=True)
        m.register_satellite("js-sat", password="js-password")
        b.start_all()

        try:
            # Get driver path
            driver_path = self._get_js_driver_path()
            if not driver_path.exists():
                pytest.skip(f"JS driver not found at {driver_path}")

            # Extract host and port from URL (format: ws://127.0.0.1:PORT/)
            url = m.network_protocol.url

            # Run Node.js driver
            result = subprocess.run(
                [
                    sys.executable, "-m", "node",  # Try 'node' command
                    str(driver_path),
                    url,
                    "js-sat",
                    "js-sat",  # name and key are same in test
                    "js-password",
                    "hello from javascript",
                ],
                capture_output=True,
                text=True,
                timeout=20,
            )

            # Check exit code
            if result.returncode != 0:
                # Try with 'node' command directly if python wrapper fails
                result = subprocess.run(
                    [
                        "node",
                        str(driver_path),
                        url,
                        "js-sat",
                        "js-sat",
                        "js-password",
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
        m = b.add_master("M0", use_loopback=True)
        m.register_satellite("js-sat2", password="js-password2")
        b.start_all()

        try:
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
                    "js-password2",
                    "test utterance from js",
                ],
                capture_output=True,
                text=True,
                timeout=20,
            )

            # Check exit code (may fail if 'node' not available, that's ok)
            if result.returncode != 0:
                if "node: command not found" in result.stderr or "No such file" in result.stderr:
                    pytest.skip("Node.js not available in test environment")
                else:
                    # Node failed for other reason
                    print("STDOUT:", result.stdout)
                    print("STDERR:", result.stderr)
                    pytest.fail(f"JS driver failed: {result.stderr}")

            # Hub should have recorded messages via the agent protocol
            # The agent protocol's injected list captures bus messages
            injected = m.agent_protocol.injected
            utterance_messages = [
                msg for msg in injected
                if msg.msg_type == "recognizer_loop:utterance"
            ]

            assert len(utterance_messages) > 0, (
                "No utterance messages received on hub. "
                f"Injected: {injected}, "
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
        m = b.add_master("M0", use_loopback=True)
        m.register_satellite("js-sat3", password="js-password3")
        b.start_all()

        try:
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
                    "js-password3",
                    "session test utterance",
                ],
                capture_output=True,
                text=True,
                timeout=20,
            )

            if result.returncode != 0:
                if "node: command not found" in result.stderr or "No such file" in result.stderr:
                    pytest.skip("Node.js not available in test environment")
                else:
                    print("STDOUT:", result.stdout)
                    print("STDERR:", result.stderr)
                    pytest.fail(f"JS driver failed: {result.stderr}")

            # Check that at least one injected utterance has session context
            injected = m.agent_protocol.injected
            utterance_messages = [
                msg for msg in injected
                if msg.msg_type == "recognizer_loop:utterance"
            ]

            assert len(utterance_messages) > 0, (
                "No utterance messages received on hub. "
                f"Injected: {injected}"
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
