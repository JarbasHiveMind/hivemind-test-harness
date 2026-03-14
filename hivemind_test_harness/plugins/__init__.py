from hivemind_test_harness.plugins.agent import TestAgentProtocol
from hivemind_test_harness.plugins.binary import TestBinaryProtocol
from hivemind_test_harness.plugins.network import TestNetworkProtocol

# Optional — requires ovoscope + ovos-core
try:
    from hivemind_test_harness.plugins.ovoscope_agent import (
        OvoscopeAgentProtocol,
        _HarnessCaptureSession,
    )
except ImportError:
    pass
