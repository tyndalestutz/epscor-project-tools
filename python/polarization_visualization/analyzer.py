from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from .config import AnalyzerConfig


class AnalyzerError(RuntimeError):
    pass


@dataclass(frozen=True)
class PhotodiodeReading:
    mean_voltage: float
    std_voltage: float
    min_voltage: float
    max_voltage: float
    sample_count: int


class AnalyzerController:
    """Fail-closed PyRPL control for EOM on OUT1, LCVR on OUT2, and PD on IN2."""

    def __init__(
        self,
        config: AnalyzerConfig,
        *,
        pyrpl_factory: Any = None,
        pd_input: str = "in2",
        pd_scope_duration_s: float = 0.01,
        pd_scope_decimation: int = 64,
        pd_scope_timeout_s: float = 2.0,
    ) -> None:
        config.require_verified_transfer_chain()
        self.config = config
        self._pyrpl_factory = pyrpl_factory
        self.pd_input = pd_input
        self.pd_scope_duration_s = pd_scope_duration_s
        self.pd_scope_decimation = pd_scope_decimation
        self.pd_scope_timeout_s = pd_scope_timeout_s
        self.p: Any = None
        self.asg_eom: Any = None
        self.asg_lcvr: Any = None
        self.eom_device_v = 0.0
        self.lcvr_cell_vpp = 0.0

    @property
    def connected(self) -> bool:
        return self.p is not None

    def _factory(self):
        if self._pyrpl_factory is not None:
            return self._pyrpl_factory
        try:
            from pyrpl import Pyrpl
        except ImportError as exc:
            raise AnalyzerError("pyrpl is not installed in the active environment") from exc
        return Pyrpl

    def connect(self) -> "AnalyzerController":
        if self.connected:
            return self
        p = self._factory()(hostname=self.config.rp_hostname, config=self.config.rp_pyrpl_config, gui=False)
        self.p = p
        try:
            self._clear_output_routes()
            self.asg_eom = p.rp.asg0
            self.asg_lcvr = p.rp.asg1
            self._configure_safe_off()
        except Exception:
            self.close()
            raise
        return self

    def _clear_output_routes(self) -> None:
        for name in ("asg0", "asg1", "pid0", "pid1", "pid2", "iq0", "iq1", "iq2"):
            module = getattr(self.p.rp, name, None)
            if module is not None:
                try:
                    module.output_direct = "off"
                except Exception:
                    pass

    def _configure_safe_off(self) -> None:
        self.asg_eom.output_direct = "off"
        self.asg_lcvr.output_direct = "off"
        self.asg_eom.setup(waveform="dc", offset=0.0, amplitude=0.0, trigger_source="immediately")
        self.asg_lcvr.setup(
            waveform="square",
            frequency=self.config.lcvr_drive_frequency_hz,
            offset=0.0,
            amplitude=0.0,
            trigger_source="immediately",
        )
        self.eom_device_v = 0.0
        self.lcvr_cell_vpp = 0.0

    def safe_off(self) -> None:
        if self.asg_eom is None or self.asg_lcvr is None:
            return
        try:
            self._configure_safe_off()
        finally:
            self.asg_eom.output_direct = "off"
            self.asg_lcvr.output_direct = "off"

    def close(self) -> None:
        try:
            self.safe_off()
        finally:
            self.asg_eom = None
            self.asg_lcvr = None
            self.p = None

    def __enter__(self) -> "AnalyzerController":
        return self.connect()

    def __exit__(self, *_: object) -> None:
        self.close()

    def _require_connected(self) -> None:
        if not self.connected or self.asg_eom is None or self.asg_lcvr is None:
            raise AnalyzerError("Analyzer is not connected")

    def eom_rp_command_for_device_voltage(self, device_v: float) -> float:
        if device_v < 0:
            raise ValueError("EOM voltage must be non-negative for this test")
        gain = float(self.config.eom_device_volts_per_rp_volt)
        command = device_v / gain
        if device_v > float(self.config.eom_max_device_v) or command > float(self.config.eom_max_rp_command_v):
            raise ValueError(
                f"EOM request {device_v:g} V requires {command:g} RP V and exceeds configured limits"
            )
        return command

    def lcvr_rp_amplitude_for_cell_vpp(self, cell_vpp: float) -> float:
        if cell_vpp < 0 or cell_vpp > float(self.config.lcvr_max_cell_vpp):
            raise ValueError(
                f"LCVR request must be between 0 and {self.config.lcvr_max_cell_vpp:g} Vpp"
            )
        if cell_vpp > 0 and not self.config.lcvr_bipolar_output_verified:
            raise ValueError(
                "Nonzero LCVR drive is locked: the MDT690 is specified as a 0..150 V "
                "unipolar output and cannot be assumed to preserve a zero-mean waveform"
            )
        if (
            not self.config.lcvr_terminal_gain_verified
            and cell_vpp > self.config.lcvr_unverified_max_cell_vpp
        ):
            raise ValueError(
                "LCVR terminal gain is not measured at 2 kHz; requests above "
                f"{self.config.lcvr_unverified_max_cell_vpp:g} Vpp are locked"
            )
        command = cell_vpp / float(self.config.lcvr_cell_vpp_per_rp_volt)
        if command > float(self.config.lcvr_max_rp_command_v) + 1e-12:
            raise ValueError(
                f"LCVR request {cell_vpp:g} Vpp exceeds conservative test ceiling of "
                f"{float(self.config.lcvr_max_rp_command_v) * float(self.config.lcvr_cell_vpp_per_rp_volt):g} Vpp"
            )
        return command

    def set_eom_voltage(self, device_v: float) -> float:
        self._require_connected()
        command = self.eom_rp_command_for_device_voltage(device_v)
        self.asg_eom.setup(
            waveform="dc", offset=float(command), amplitude=0.0, trigger_source="immediately"
        )
        self.asg_eom.output_direct = self.config.eom_output
        self.eom_device_v = float(device_v)
        return command

    def set_lcvr_vpp(self, cell_vpp: float) -> float:
        self._require_connected()
        amplitude = self.lcvr_rp_amplitude_for_cell_vpp(cell_vpp)
        # Configure while disconnected from OUT2, then route only the already
        # bounded, zero-mean waveform. This prevents a transient DC level.
        self.asg_lcvr.output_direct = "off"
        self.asg_lcvr.setup(
            waveform="square",
            frequency=float(self.config.lcvr_drive_frequency_hz),
            offset=0.0,
            amplitude=float(amplitude),
            trigger_source="immediately",
        )
        if cell_vpp > 0:
            self.asg_lcvr.output_direct = self.config.lcvr_output
        self.lcvr_cell_vpp = float(cell_vpp)
        return amplitude

    def read_photodiode(self) -> PhotodiodeReading:
        self._require_connected()
        scope = self.p.rp.scope
        previous = {
            "input1": scope.input1,
            "duration": scope.duration,
            "decimation": scope.decimation,
        }
        try:
            scope.input1 = self.pd_input
            scope.duration = self.pd_scope_duration_s
            scope.decimation = self.pd_scope_decimation
            trace = np.asarray(scope.single(timeout=self.pd_scope_timeout_s)[0], dtype=float)
        finally:
            for name, value in previous.items():
                setattr(scope, name, value)
        if trace.size == 0:
            raise AnalyzerError("Red Pitaya IN2 returned an empty photodiode trace")
        return PhotodiodeReading(
            mean_voltage=float(np.mean(trace)),
            std_voltage=float(np.std(trace)),
            min_voltage=float(np.min(trace)),
            max_voltage=float(np.max(trace)),
            sample_count=int(trace.size),
        )

    def settle(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("settling time must be non-negative")
        time.sleep(seconds)
