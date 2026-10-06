"""Explicit discovery of acquisition experiments; no fitting/calibration dispatch."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Experiment:
    key: str
    description: str


EXPERIMENTS = (
    Experiment("mock_lifecycle", "Synthetic lifecycle events; no optical measurements or hardware."),
    Experiment("single_eom_characterization", "One EOM sweep: mandatory raw PD, PAX reference and host timing."),
    Experiment("dual_eom_characterization", "Ordered two-EOM grid/list: mandatory raw PD, PAX reference and host timing."),
)
BY_KEY = {experiment.key: experiment for experiment in EXPERIMENTS}
PLANNED = (
    "reference_path_characterization", "analyzer_state_design",
    "instrument_matrix_calibration", "calibration_validation",
    "high_speed_reconstruction",
)
