"""
LoopbackNetworkProtocol — Real WebSocket server routing to in-process HiveMindListenerProtocol.

Unlike TestNetworkProtocol which wires satellites directly to the master's protocol
(no sockets), LoopbackNetworkProtocol starts a real WebSocket server on localhost:0
(random port). Real clients (Python async, JS, etc.) connect via WebSocket, and each
connection is routed through HiveMindClientConnection into the same HiveMindListenerProtocol.

This enables E2E testing of clients without requiring a real hub — the harness acts
as a full hub from the client's perspective.
"""

import asyncio
import base64
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

import websockets
import websockets.asyncio.server
from poorman_handshake import PasswordHandShake

from hivemind_plugin_manager.protocols import NetworkProtocol
from hivemind_core.protocol import HiveMindClientConnection, HiveMindNodeType
from ovos_bus_client.session import Session

_LOG = logging.getLogger(__name__)


@dataclass
class LoopbackNetworkProtocol(NetworkProtocol):
    """WebSocket server on localhost:0, routes connections in-process.

    Attributes:
        config: Protocol configuration dict.
        hm_protocol: The HiveMindListenerProtocol to route messages through.
        callbacks: Client callbacks.
        url: Connection URL set after run() starts the server.
             Format: "ws://127.0.0.1:PORT/" where PORT is assigned by OS.
    """
    config: Dict[str, Any] = field(default_factory=dict)
    _url: Optional[str] = field(default=None, init=False, repr=False)
    _server: Optional[Any] = field(default=None, init=False, repr=False)
    _loop: Optional[asyncio.AbstractEventLoop] = field(default=None, init=False, repr=False)
    _thread: Optional[threading.Thread] = field(default=None, init=False, repr=False)
    _clients: List[Any] = field(default_factory=list, init=False, repr=False)

    @property
    def url(self) -> str:
        """Get the WebSocket connection URL after run() starts the server.

        Returns:
            "ws://127.0.0.1:PORT/" where PORT is the OS-assigned random port.

        Raises:
            RuntimeError: If called before run() starts the server.
        """
        if self._url is None:
            raise RuntimeError(
                "LoopbackNetworkProtocol.url not available until after run() "
                "starts the server. Call it after adding the node to the topology."
            )
        return self._url

    def run(self):
        """Start WebSocket server in a daemon thread.

        Creates a real WebSocket server listening on localhost:0 (random port).
        The server runs on its own asyncio event loop in a background daemon thread.
        """
        if self._thread is not None:
            _LOG.warning("LoopbackNetworkProtocol.run() called but server already running")
            return

        # Start background thread
        self._thread = threading.Thread(target=self._run_server, daemon=True)
        self._thread.start()

        # Wait for server to start and URL to be available
        max_wait = 10  # seconds
        for _ in range(int(max_wait * 100)):
            if self._url is not None:
                _LOG.info(f"LoopbackNetworkProtocol listening at {self._url}")
                return
            import time
            time.sleep(0.01)

        raise RuntimeError("LoopbackNetworkProtocol failed to start server after 10s")

    def _run_server(self):
        """Background thread: run asyncio WebSocket server.

        Sets up new event loop, starts WebSocket server, runs event loop until stop().
        """
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        try:
            # Run the server coroutine and keep it alive
            self._server_task = self._loop.run_until_complete(self._start_server())
            # Now run the event loop indefinitely (handles incoming connections)
            self._loop.run_forever()
        except Exception as e:
            _LOG.exception(f"LoopbackNetworkProtocol server error: {e}")
        finally:
            try:
                # Cancel the server task if still running
                if hasattr(self, '_server_task') and self._server_task:
                    self._server_task.cancel()
                    try:
                        self._loop.run_until_complete(self._server_task)
                    except asyncio.CancelledError:
                        pass
                # Close all pending tasks
                pending = asyncio.all_tasks(self._loop)
                for task in pending:
                    task.cancel()
                self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            finally:
                try:
                    self._loop.close()
                except Exception:
                    pass
                self._loop = None

    async def _start_server(self):
        """Start and manage the WebSocket server.

        Returns a server object that can be managed from the thread.
        """
        async def handler(websocket):
            """Handle incoming WebSocket connection.

            Note: websockets v11+ changed signature from (websocket, path) to (websocket)
            """
            await self._handle_client(websocket)

        # Start server on localhost:0 (OS assigns port)
        server = await websockets.asyncio.server.serve(
            handler,
            "127.0.0.1",
            0,  # Random OS-assigned port
        )

        # Extract the actual port and set URL
        sockets = server.sockets
        if sockets:
            port = sockets[0].getsockname()[1]
            self._url = f"ws://127.0.0.1:{port}/"
            _LOG.info(f"LoopbackNetworkProtocol server started on {self._url}")

        return server

    async def _handle_client(self, websocket):
        """Handle a single WebSocket client connection.

        Steps:
        1. Extract authorization from query params (base64(name:key))
        2. Look up client in database
        3. Create HiveMindClientConnection with send_msg routed to WebSocket
        4. Call hm_protocol.handle_new_client() → triggers HELLO+SHAKE handshake
        5. On each message: hm_protocol.handle_message()
        6. On close: hm_protocol.handle_client_disconnected()
        """
        if self.hm_protocol is None:
            await websocket.close(code=1011, reason="No HiveMindListenerProtocol configured")
            return

        # Extract authorization from either:
        # 1. Query parameter: ?authorization=base64(name:key) (MicroPython client)
        # 2. Authorization header: Basic base64(name:key) (JS client)

        name = None
        key = None

        # Try query parameter first (MicroPython style)
        try:
            query_string = websocket.request.path.split("?", 1)[1] if "?" in websocket.request.path else ""
            if query_string:
                import urllib.parse
                params = urllib.parse.parse_qs(query_string)
                auth_b64_list = params.get("authorization", [])
                if auth_b64_list:
                    auth_b64 = auth_b64_list[0]
                    auth_decoded = base64.b64decode(auth_b64).decode("utf-8")
                    name, key = auth_decoded.split(":", 1)
                    _LOG.debug(f"Auth from query param: {name}")
        except Exception as e:
            _LOG.debug(f"Query param auth failed: {e}")

        # If query param didn't work, try Authorization header (JS style)
        if not name or not key:
            auth_header = websocket.request.headers.get("Authorization", "")
            if auth_header.startswith("Basic "):
                try:
                    auth_b64 = auth_header.split(" ", 1)[1]
                    auth_decoded = base64.b64decode(auth_b64).decode("utf-8")
                    name, key = auth_decoded.split(":", 1)
                    _LOG.debug(f"Auth from header: {name}")
                except (ValueError, IndexError, UnicodeDecodeError) as e:
                    _LOG.warning(f"Invalid Authorization header: {e}")

        # If still no auth, reject
        if not name or not key:
            await websocket.close(code=1008, reason="Missing or invalid authorization (query param or Authorization header)")
            return

        # Look up client in database
        self.hm_protocol.db.sync()
        db_client = self.hm_protocol.db.get_client_by_api_key(key)
        if db_client is None:
            _LOG.warning(f"Client key '{key}' not found in database")
            await websocket.close(code=4001, reason="Invalid API key")
            return

        # Create HiveMindClientConnection
        # send_msg will be called from the harness's synchronous code,
        # so we wrap it to push the send onto the server's event loop
        async def ws_send(payload: bytes, is_binary: bool = False):
            """Send data to WebSocket. Called from harness synchronous code via threadsafe call."""
            if is_binary:
                await websocket.send(payload)
            else:
                # Assume it's already binary and send as-is
                await websocket.send(payload)

        def sync_send(payload: bytes):
            """Synchronous wrapper for send_msg (called from harness).
            Routes call back to async ws_send via asyncio.run_coroutine_threadsafe.
            """
            if self._loop is None or self._loop.is_closed():
                return
            try:
                asyncio.run_coroutine_threadsafe(ws_send(payload), self._loop)
            except Exception as e:
                _LOG.exception(f"Error sending to WebSocket: {e}")

        async def ws_close():
            """Close WebSocket. Called from harness via threadsafe call."""
            try:
                await websocket.close()
            except Exception:
                pass

        def sync_disconnect():
            """Synchronous wrapper for disconnect (called from harness).
            Routes call back to async ws_close.
            """
            if self._loop is None or self._loop.is_closed():
                return
            try:
                asyncio.run_coroutine_threadsafe(ws_close(), self._loop)
            except Exception as e:
                _LOG.exception(f"Error closing WebSocket: {e}")

        # Create the connection object with these callbacks
        conn = HiveMindClientConnection(
            key=key,
            name=name,
            send_msg=sync_send,
            disconnect=sync_disconnect,
            sess=Session(session_id="default"),  # Re-assigned after HELLO from satellite
            hm_protocol=self.hm_protocol,
            # Populate from DB entry
            crypto_key=db_client.crypto_key,
            pswd_handshake=(PasswordHandShake(db_client.password)
                            if db_client.password else None),
            is_admin=db_client.is_admin,
            can_escalate=db_client.can_escalate,
            can_propagate=db_client.can_propagate,
            msg_blacklist=list(db_client.message_blacklist or []),
            skill_blacklist=list(db_client.skill_blacklist or []),
            intent_blacklist=list(db_client.intent_blacklist or []),
            allowed_types=list(db_client.allowed_types or []),
            node_type=HiveMindNodeType.NODE,
        )

        # Track this client
        self._clients.append(conn)

        try:
            # Notify harness of new client — synchronous, triggers HELLO+SHAKE
            self.hm_protocol.handle_new_client(conn)

            # Process incoming messages from WebSocket
            async for message in websocket:
                if isinstance(message, bytes):
                    # HiveMind uses binary messages
                    self.hm_protocol.handle_message(message, conn)
                else:
                    _LOG.warning(f"Unexpected text message from {name}: {message}")

        except websockets.exceptions.ConnectionClosed:
            _LOG.info(f"Client {name} disconnected")
        except Exception as e:
            _LOG.exception(f"Error handling client {name}: {e}")
        finally:
            # Notify harness of disconnection
            self.hm_protocol.handle_client_disconnected(conn)
            try:
                self._clients.remove(conn)
            except ValueError:
                pass  # Already removed or not in list

    def stop(self):
        """Stop the WebSocket server and wait for thread to finish."""
        if self._loop is not None and not self._loop.is_closed():
            # Stop the event loop
            self._loop.call_soon_threadsafe(self._loop.stop)

        if self._thread is not None:
            # Wait for thread to finish (give it time to clean up)
            self._thread.join(timeout=5)
            self._thread = None

        self._url = None
        self._server_task = None
        self._clients.clear()
