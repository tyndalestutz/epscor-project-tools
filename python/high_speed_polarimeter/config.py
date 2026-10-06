"""Versioned recipes with explicit RP-command units and bench-editable settings."""
from dataclasses import asdict, dataclass, field, fields
import math
from pathlib import Path
import re

from .catalog import BY_KEY
from .io import read_json

DEFAULT_RESULTS = str(Path(__file__).resolve().parents[2] / "experiments" / "high_speed_polarimeter")
LEGACY_ROOT = Path(__file__).resolve().parents[1] / "polarization_locking"


@dataclass(frozen=True)
class AcquisitionConfig:
    # Numerical defaults mirror existing locking sweep/scope/PAX settings.
    # They are RP commands, not delivered EOM voltages or a Vpi assumption.
    results_directory: str = DEFAULT_RESULTS
    selected_eom: str = "eom1"
    eom1_output: str = "out1"
    eom2_output: str = "out2"
    output_map_confirmed: bool = False
    voltage_chain: str = ""
    pax_reference_plane: str = ""
    start_command_v: float = 0.0
    stop_command_v: float = 0.1
    points: int = 11
    bidirectional: bool = False
    repeats: int = 1
    settle_s: float = 1.0
    other_eom_command_v: float = 0.0
    # Dual experiment: an explicit ordered list overrides the Cartesian grid.
    states_v: list | None = None
    eom1_commands_v: list = field(default_factory=lambda: [0.0, 0.1])
    eom2_commands_v: list = field(default_factory=lambda: [0.0, 0.1])
    rp_hostname: str = "192.168.1.98"
    rp_config: str = "scope_config"
    rp_output_min_voltage: float = 0.0
    rp_output_max_voltage: float = 1.0
    pd_input: str = "in1"
    pd_duration_s: float = 0.01
    pd_scope_decimation: int = 64
    pd_scope_timeout_s: float = 2.0
    pd_fpga_average: bool = True
    pax_samples_per_point: int = 1
    pax_host: str = "localhost"
    pax_port: int = 38400
    pax_autostart_daemon: bool = True
    pax_daemon_config_path: str = str(LEGACY_ROOT / "hardware" / "pax1000.toml")
    pax_daemon_start_timeout_s: float = 5.0
    pax_measurement_wait_s: float = 0.06
    pax_read_retries: int = 3
    pax_retry_wait_s: float = 0.25
    pax_wavelength_nm: float = 830.0

    def validate(self, experiment):
        defaults = AcquisitionConfig()
        for f in fields(self):
            value, default = getattr(self, f.name), getattr(defaults, f.name)
            if f.name == "states_v":
                if value is not None and (not isinstance(value, list) or not value):
                    raise ValueError("states_v must be null or a nonempty list of [EOM1, EOM2] pairs")
            elif isinstance(default, float):
                if type(value) not in (int, float) or not math.isfinite(value):
                    raise ValueError(f"{f.name} must be a finite number")
            elif type(value) is not type(default):
                raise ValueError(f"Invalid type for {f.name}")
        if self.selected_eom not in {"eom1", "eom2"}:
            raise ValueError("selected_eom must be eom1 or eom2")
        if {self.eom1_output, self.eom2_output} != {"out1", "out2"}:
            raise ValueError("EOMs must map to distinct out1/out2 channels")
        if not self.results_directory.strip() or not self.rp_hostname.strip() or not self.rp_config.strip() or not self.pax_host.strip():
            raise ValueError("Output directory and hardware addresses/profile must be nonempty")
        if not self.rp_output_min_voltage == 0.0 < self.rp_output_max_voltage <= 1.0:
            raise ValueError("RP command limits must start at zero and end at or below 1 V")
        if self.points < 2 or self.repeats < 1 or self.pax_samples_per_point < 1 or self.pax_read_retries < 1:
            raise ValueError("Need at least two sweep points and positive repeats/PAX samples/retries")
        if self.pd_input not in {"in1", "in2"} or self.pd_scope_decimation not in {2**n for n in range(17)}:
            raise ValueError("Invalid PD input or scope decimation (power of two, 1..65536)")
        if self.pd_scope_timeout_s <= 16384 * 8e-9 * self.pd_scope_decimation:
            raise ValueError("pd_scope_timeout_s must exceed nominal duration of one fixed scope buffer")
        if not 1 <= self.pax_port <= 65535:
            raise ValueError("Invalid PAX port")
        for name in ("pd_duration_s", "pd_scope_timeout_s", "pax_daemon_start_timeout_s", "pax_wavelength_nm"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        for name in ("settle_s", "pax_measurement_wait_s", "pax_retry_wait_s"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be nonnegative")
        commands = [self.start_command_v, self.stop_command_v, self.other_eom_command_v]
        for values in (self.eom1_commands_v, self.eom2_commands_v):
            if not values:
                raise ValueError("Grid voltage arrays must be nonempty")
            commands.extend(values)
        if self.states_v is not None:
            for pair in self.states_v:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError("Each states_v entry must be [EOM1 command V, EOM2 command V]")
                commands.extend(pair)
        for value in commands:
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= self.rp_output_max_voltage:
                raise ValueError("Every command must be finite and within the configured RP command limits")
        if experiment == "single_eom_characterization" and self.start_command_v == self.stop_command_v:
            raise ValueError("Single-EOM sweep endpoints must differ")
        if experiment == "dual_eom_characterization" and self.bidirectional:
            raise ValueError("Dual experiment uses explicit grid/list order; bidirectional is single-EOM only")
        return self


@dataclass(frozen=True)
class Recipe:
    schema_version: int = 1
    experiment: str = "mock_lifecycle"
    label: str = "smoke"
    comment: str = ""
    config: dict = field(default_factory=lambda: {"results_directory": DEFAULT_RESULTS})

    def validate(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("Unsupported recipe schema_version")
        if not isinstance(self.experiment, str) or self.experiment not in BY_KEY:
            raise ValueError("Unknown or unimplemented experiment")
        if not isinstance(self.label, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", self.label):
            raise ValueError("label must contain letters, digits, underscores or hyphens")
        if not isinstance(self.comment, str) or not isinstance(self.config, dict):
            raise ValueError("comment must be a string and config an object")
        if self.experiment == "mock_lifecycle":
            if self.config.keys() != {"results_directory"} or not isinstance(self.config["results_directory"], str) or not self.config["results_directory"].strip():
                raise ValueError("mock config must contain only a nonempty results_directory")
        else:
            self.acquisition_config()
        return self

    def acquisition_config(self):
        unknown = self.config.keys() - {f.name for f in fields(AcquisitionConfig)}
        if unknown:
            raise ValueError(f"Unknown acquisition config fields: {sorted(unknown)}")
        return AcquisitionConfig(**self.config).validate(self.experiment)

    def resolved(self):
        self.validate()
        return Recipe(self.schema_version, self.experiment, self.label, self.comment,
                      asdict(self.acquisition_config()) if self.experiment != "mock_lifecycle" else dict(self.config))

    def to_dict(self):
        return asdict(self.resolved())


def load_recipe(path):
    value = read_json(path)
    if not isinstance(value, dict) or set(value) - {"schema_version", "experiment", "label", "comment", "config"}:
        raise ValueError("Invalid recipe fields")
    if "schema_version" not in value:
        raise ValueError("Recipe requires schema_version")
    return Recipe(**value).validate()
