from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional


@dataclass(frozen=True)
class CameraConfig:
    serial_number: str = "24934175"
    exposure_us: float = 100.0
    gain: float = 0.0
    pixel_format: str = "Mono12"
    timeout_ms: int = 5_000
    saturation_margin_codes: int = 0
    width: Optional[int] = None
    height: Optional[int] = None
    offset_x: int = 0
    offset_y: int = 0
    open_retries: int = 2
    grab_retries: int = 2
    retry_delay_s: float = 0.25

    def __post_init__(self) -> None:
        if not self.serial_number:
            raise ValueError("serial_number must not be empty")
        if self.exposure_us <= 0 or self.gain < 0:
            raise ValueError("exposure_us must be positive and gain must be non-negative")
        if self.timeout_ms <= 0 or self.saturation_margin_codes < 0:
            raise ValueError("timeout_ms must be positive and saturation margin non-negative")
        if self.width is not None and self.width <= 0:
            raise ValueError("width must be positive when set")
        if self.height is not None and self.height <= 0:
            raise ValueError("height must be positive when set")
        if self.offset_x < 0 or self.offset_y < 0:
            raise ValueError("ROI offsets must be non-negative")
        if self.open_retries < 0 or self.grab_retries < 0 or self.retry_delay_s < 0:
            raise ValueError("retry counts and delay must be non-negative")


@dataclass(frozen=True)
class AnalyzerConfig:
    rp_hostname: str = "192.168.1.98"
    rp_pyrpl_config: str = "scope_config"
    eom_output: str = "out1"
    lcvr_output: str = "out2"

    # Deliberately unset. No analyzer output may be enabled until these are
    # measured at the device and entered here.
    eom_device_volts_per_rp_volt: Optional[float] = None
    eom_max_rp_command_v: Optional[float] = None
    eom_max_device_v: Optional[float] = None
    lcvr_cell_vpp_per_rp_volt: Optional[float] = None
    lcvr_max_rp_command_v: Optional[float] = None
    lcvr_max_cell_vpp: Optional[float] = None
    lcvr_drive_frequency_hz: float = 2_000.0
    lcvr_bipolar_output_verified: bool = False
    lcvr_terminal_gain_verified: bool = False
    lcvr_unverified_max_cell_vpp: float = 0.5
    transfer_chain_verified: bool = False

    def require_verified_transfer_chain(self) -> None:
        required = (
            self.eom_device_volts_per_rp_volt,
            self.eom_max_rp_command_v,
            self.eom_max_device_v,
            self.lcvr_cell_vpp_per_rp_volt,
            self.lcvr_max_rp_command_v,
            self.lcvr_max_cell_vpp,
        )
        if not self.transfer_chain_verified or any(value is None for value in required):
            raise RuntimeError(
                "Analyzer outputs are locked: measure both transfer chains, set all safety limits, "
                "and mark transfer_chain_verified=True."
            )


# Explicit opt-in configuration for the present analyzer systems test. These
# gains come from the measured polarization_locking chain and the current
# wiring stated by the operator. DEFAULT_CONFIG remains locked so importing
# this package can never enable analyzer outputs implicitly.
SYSTEM_TEST_ANALYZER_CONFIG = AnalyzerConfig(
    eom_device_volts_per_rp_volt=150.0,        # 1.125 RP scaling * x8.89 preamp * x15 driver
    eom_max_rp_command_v=1.0,
    eom_max_device_v=150.0,
    lcvr_cell_vpp_per_rp_volt=33.75,           # 2 * 1.125 * x15 driver; no x8.89 preamp on OUT2
    lcvr_max_rp_command_v=4.0 / 33.75,         # test ceiling: 4 Vpp at cell
    lcvr_max_cell_vpp=5.0,                     # absolute hardware ceiling
    # MDT690 is specified for 0..10 V input and 0..150 V output, not bipolar
    # operation. All nonzero LCVR commands remain locked on this path.
    lcvr_bipolar_output_verified=False,
    lcvr_terminal_gain_verified=False,
    lcvr_unverified_max_cell_vpp=0.5,
    transfer_chain_verified=True,
)


@dataclass(frozen=True)
class HardwareConfig:
    camera: CameraConfig = CameraConfig()
    analyzer: AnalyzerConfig = AnalyzerConfig()

    def to_dict(self) -> dict:
        return asdict(self)


DEFAULT_CONFIG = HardwareConfig()
