"""
TopologyBuilder — assembles and wires MasterNode / SatelliteNode instances.

Node roles
──────────
In HiveMind every deployed node has one or more of the following roles:

  Master    — runs ``HiveMindListenerProtocol`` (hivemind-core).  Accepts
              inbound satellite connections.  Any node running hivemind-core
              is a master, regardless of whether it is also connected upstream.

  Satellite — connected to a master via ``HiveMindSlaveProtocol``
              (hivemind-bus-client).  Emits messages upstream, receives
              responses and broadcast messages downstream.

These two roles are NOT mutually exclusive.  A node can simultaneously:

  * act as a satellite (connected to a parent master), AND
  * act as a master (accepting connections from child satellites),
    running the same AI agent on both sides.

The test harness calls such a dual-role node a **relay** (because it relays
messages between layers of the hive).  Internally it is modelled as a
:class:`RelayNode` — a single object that owns one :class:`SatelliteNode`
(the upstream connection) and one :class:`MasterNode` (the downstream
listener), both sharing the same :class:`~hivemind_test_harness.plugins.agent.TestAgentProtocol`
and ``FakeBus``.
"""
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from ovos_utils.fakebus import FakeBus

from hivemind_bus_client.message import HiveMessage, HiveMessageType
from hivemind_test_harness.node import MasterNode, SatelliteNode
from hivemind_test_harness.plugins.agent import TestAgentProtocol


# ---------------------------------------------------------------------------
# RelayNode — dual-role (satellite + master) in-process node
# ---------------------------------------------------------------------------

@dataclass
class RelayNode:
    """A node that is simultaneously a satellite and a master.

    In a real HiveMind deployment this is any node that:
      * runs ``hivemind-core`` (``HiveMindListenerProtocol``) to accept
        downstream satellite connections, AND
      * connects to a parent master as a satellite via
        ``HiveMindSlaveProtocol`` (hivemind-bus-client).

    Both sides share the **same** agent protocol and bus, meaning a single AI
    brain handles messages from both upstream and downstream.

    Attributes:
        name:     Logical name of this relay node (e.g. ``"R1"``).
        upstream: The :class:`SatelliteNode` representing the connection to
                  this relay's parent master.
        listener: The :class:`MasterNode` representing this relay's listener
                  that accepts its own downstream satellites.
        bus:      The shared :class:`~ovos_utils.fakebus.FakeBus` instance.
    """
    name: str
    upstream: SatelliteNode
    listener: MasterNode
    bus: FakeBus

    # Convenience pass-throughs ----------------------------------------

    @property
    def hm_protocol(self):
        """``HiveMindListenerProtocol`` (downstream/listener side)."""
        return self.listener.hm_protocol

    @property
    def slave_protocol(self):
        """``HiveMindSlaveProtocol`` (upstream/satellite side)."""
        return self.upstream.slave_protocol

    @property
    def peer(self):
        """Peer string of the upstream satellite connection."""
        return self.upstream.peer

    @property
    def identity(self):
        return self.listener.identity


class TopologyBuilder:
    def __init__(self):
        self._masters: Dict[str, MasterNode] = {}
        self._satellites: Dict[str, SatelliteNode] = {}
        self._relays: Dict[str, RelayNode] = {}
        # (satellite_name, master_name, connect_kwargs)
        self._connections: List[Tuple[str, str, dict]] = []

    # --- builder API ---

    def add_master(self, name: str, **kwargs) -> MasterNode:
        node = MasterNode.create(name, **kwargs)
        self._masters[name] = node
        return node

    def add_satellite(self, name: str, upstream: MasterNode,
                      shared_bus: bool = False,
                      **connect_kwargs) -> SatelliteNode:
        """
        Add a satellite that will connect to `upstream` on start_all().
        Extra kwargs (is_admin, can_escalate, …) are forwarded to connect().
        shared_bus=True enables passive SHARED_BUS monitoring (slave forwards every
        internal bus message to master, which fires shared_bus_callback).
        """
        node = SatelliteNode.create(name, shared_bus=shared_bus)
        self._satellites[name] = node
        self._connections.append((name, upstream.name, connect_kwargs))
        return node

    def add_relay(self, name: str, upstream: MasterNode,
                  **connect_kwargs) -> Tuple[SatelliteNode, MasterNode]:
        """Add a dual-role node: a satellite connected to *upstream* that also
        acts as a master for its own downstream satellites.

        Both sides share a single :class:`~hivemind_test_harness.plugins.agent.TestAgentProtocol`
        and ``FakeBus``, matching real HiveMind deployments where one AI brain
        handles both the upstream satellite connection and downstream listener.

        Args:
            name:         Logical name for this relay (e.g. ``"R1"``).
                          Internal nodes are registered as ``{name}_sat``
                          (satellite connection) and ``{name}_master``
                          (listener).  Use :meth:`get_relay` to access the
                          combined :class:`RelayNode`.
            upstream:     The parent :class:`MasterNode` this relay connects to.
            **connect_kwargs: Forwarded to :meth:`SatelliteNode.connect`
                          (``is_admin``, ``can_escalate``, etc.).

        Returns:
            ``(satellite_side, master_side)`` — the upstream
            :class:`SatelliteNode` and the listener :class:`MasterNode`.
            Access the combined view via :meth:`get_relay`.
        """
        # Shared agent protocol — one brain, two protocol connections.
        shared_agent = TestAgentProtocol()
        shared_bus = shared_agent.bus

        sat = SatelliteNode.create(f"{name}_sat", bus=shared_bus)
        master = MasterNode.create(f"{name}_master", agent_protocol=shared_agent)

        self._satellites[f"{name}_sat"] = sat
        self._masters[f"{name}_master"] = master
        self._connections.append((f"{name}_sat", upstream.name, connect_kwargs))

        # Wire relay: when the listener receives ESCALATE or PROPAGATE from a
        # downstream satellite, forward it upstream via the satellite connection.
        _orig_handle = master.hm_protocol.handle_message

        def _relaying_handle(hm_message, client, _orig=_orig_handle, _sat=sat):
            _orig(hm_message, client)
            if (hm_message.msg_type in (HiveMessageType.ESCALATE, HiveMessageType.PROPAGATE)
                    and _sat._master is not None):
                _sat.send(HiveMessage(hm_message.msg_type, hm_message.payload))

        master.hm_protocol.handle_message = _relaying_handle

        relay = RelayNode(name=name, upstream=sat, listener=master, bus=shared_bus)
        self._relays[name] = relay
        return sat, master

    # --- lifecycle ---

    def start_all(self):
        """Connect every satellite to its master in registration order."""
        for sat_name, master_name, kwargs in self._connections:
            sat = self._satellites[sat_name]
            master = self._masters[master_name]
            sat.connect(master, **kwargs)

    def stop_all(self):
        """Gracefully disconnect all satellites."""
        for sat in self._satellites.values():
            if sat._connection is not None:
                try:
                    sat.disconnect()
                except Exception:
                    pass

    # --- accessors ---

    def get_master(self, name: str) -> MasterNode:
        if name not in self._masters:
            raise KeyError(f"No master named '{name}'. "
                           f"Available: {list(self._masters)}")
        return self._masters[name]

    def get_satellite(self, name: str) -> SatelliteNode:
        if name not in self._satellites:
            raise KeyError(f"No satellite named '{name}'. "
                           f"Available: {list(self._satellites)}")
        return self._satellites[name]

    def get_relay(self, name: str) -> RelayNode:
        """Return the combined :class:`RelayNode` for a dual-role node.

        ``name`` is the logical relay name passed to :meth:`add_relay`
        (without the ``_sat`` / ``_master`` suffix).
        """
        if name not in self._relays:
            raise KeyError(f"No relay named '{name}'. "
                           f"Available: {list(self._relays)}")
        return self._relays[name]

    @property
    def masters(self) -> List[MasterNode]:
        """All master (listener) nodes, including relay listener sides."""
        return list(self._masters.values())

    @property
    def satellites(self) -> List[SatelliteNode]:
        """All satellite (upstream-connection) nodes, including relay satellite sides."""
        return list(self._satellites.values())

    @property
    def relays(self) -> List[RelayNode]:
        """All dual-role relay nodes."""
        return list(self._relays.values())
