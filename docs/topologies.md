# Network Topologies — hivemind-test-harness

The harness must exercise the protocol across a range of realistic deployment shapes.
Each topology is defined by a fixture in `conftest.py` that assembles the nodes and
wires their connections before tests run.

---

## T1 · Minimal (1 Master, 1 Satellite)

```
M0
└── S0
```

**Purpose**: Baseline. Validates every single-hop message type in isolation.

**Implementation**: `TopologyBuilder — hivemind_test_harness/topology.py:87`

---

## T3 · Chain (Linear Hierarchy)

```
M0
└── M1  (M1 is both satellite of M0 and master to S0)
    └── S0
```

**Purpose**: Validate multi-hop ESCALATE and PROPAGATE.

**Implementation**: `RelayNode — hivemind_test_harness/topology.py:46`

---

## T9 · Relay Node (Satellite-Master)

```
M0
└── R0  (relay: satellite of M0, master to S0, S1)
    ├── S0
    └── S1
```

**Purpose**: Test a node acting as both slave and master simultaneously.

**Implementation**: `TopologyBuilder.add_relay — hivemind_test_harness/topology.py:116`
