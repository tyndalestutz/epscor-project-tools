"""Explicit acquisition dispatch."""
from .mock_lifecycle import acquire as acquire_mock
from .eom_sweep import acquire as acquire_eom

ACQUISITIONS = {"mock_lifecycle": acquire_mock,
                "single_eom_characterization": acquire_eom,
                "dual_eom_characterization": acquire_eom}
