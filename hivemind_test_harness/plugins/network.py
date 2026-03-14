"""
TestNetworkProtocol — NetworkProtocol with no sockets.

run() is a no-op.  connect_satellite() wires a SatelliteNode directly to the
HiveMindListenerProtocol, creating a HiveMindClientConnection whose send_msg
callable routes to satellite._receive_raw() instead of a WebSocket.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, TYPE_CHECKING

from poorman_handshake import PasswordHandShake
from hivemind_plugin_manager.protocols import NetworkProtocol
from hivemind_core.protocol import HiveMindClientConnection, HiveMindNodeType
from ovos_bus_client.session import Session

if TYPE_CHECKING:
    from hivemind_test_harness.node import SatelliteNode


@dataclass
class TestNetworkProtocol(NetworkProtocol):
    config: Dict[str, Any] = field(default_factory=dict)

    def run(self):
        # No server to start — topology wiring happens through connect_satellite()
        pass

    def connect_satellite(self, satellite: "SatelliteNode") -> HiveMindClientConnection:
        """
        Wire satellite to the master's HiveMindListenerProtocol in-process.

        Steps:
        1. Fetch DB entry for the satellite's access key.
        2. Create HiveMindClientConnection with all DB-populated fields, and
           send_msg pointing to satellite._receive_raw().
        3. Store the connection on satellite._connection so satellite.send() works
           during the synchronous handshake call chain that follows.
        4. Call hm_protocol.handle_new_client(conn) — this triggers HELLO +
           HANDSHAKE to be sent to the satellite (via _receive_raw) and the
           satellite's handlers respond synchronously in the same call stack.
        """
        hm_proto = self.hm_protocol
        key = satellite.identity.access_key

        hm_proto.db.sync()
        db_client = hm_proto.db.get_client_by_api_key(key)
        if db_client is None:
            raise ValueError(
                f"Satellite key '{key}' not found in master database. "
                "Call master.register_satellite() before connecting."
            )

        conn = HiveMindClientConnection(
            key=key,
            name=satellite.identity.name,
            send_msg=satellite._receive_raw,
            disconnect=satellite._on_disconnect,
            sess=Session(session_id="default"),  # re-assigned after HELLO from satellite
            hm_protocol=hm_proto,
            # populate from DB entry (mirrors what HiveMindTornadoWebSocket.open() does)
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

        # Must be stored BEFORE handle_new_client so satellite.send() (used
        # during the synchronous handshake exchange) can reach the connection.
        satellite._connection = conn

        hm_proto.handle_new_client(conn)
        return conn
