from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class PhotodiodeReading:
    """Summary of one Red Pitaya scope capture on the final-output PD."""

    mean_voltage: float
    std_voltage: float
    min_voltage: float
    max_voltage: float
    sample_count: int

class RPController:
    """Thin wrapper around the Pyrpl Red Pitaya interface."""

    def __init__(self, config: Any) -> None:
        self.config = config
        self.p = None
        self.asg1 = None
        self.asg2 = None

    def _clear_output_routes(self) -> None:
        """Disable competing Red Pitaya output modules so ASGs can drive the intended outputs cleanly."""
        if self.p is None:
            return

        for name in ("asg0", "asg1", "pid0", "pid1", "pid2", "iq0", "iq1", "iq2"):
            module = getattr(self.p.rp, name, None)
            if module is None:
                continue
            try:
                module.output_direct = "off"
            except Exception:
                pass

    def connect(self) -> Any:
        try:
            from pyrpl import Pyrpl
        except ImportError as exc:  # pragma: no cover - hardware dependency
            raise RuntimeError("pyrpl is not installed in the active Python environment") from exc

        # We only use Pyrpl as a Python hardware client. Passing gui=False
        # overrides any persisted ``redpitaya.gui`` value in scope_config.yml,
        # preventing Pyrpl from constructing/showing its control window while
        # retaining the normal Red Pitaya, ASG, and scope connections.
        self.p = Pyrpl(hostname=self.config.rp_hostname, config=self.config.rp_config, gui=False)
        # Make sure no other module is still driving the outputs before we use the ASGs.
        self._clear_output_routes()

        # Use the arbitrary signal generator modules directly for DC-like voltage commands.
        self.asg1 = self.p.rp.asg0
        self.asg2 = self.p.rp.asg1

        self.asg1.output_direct = "out1"
        self.asg2.output_direct = "out2"

        self.asg1.setup(
            waveform="dc",
            offset=0.0,
            amplitude=0.0,
            trigger_source="immediately",
        )
        self.asg2.setup(
            waveform="dc",
            offset=0.0,
            amplitude=0.0,
            trigger_source="immediately",
        )

        return self.p

    def disconnect(self) -> None:
        self.set_output_zero()
        self.asg1 = None
        self.asg2 = None
        self.p = None

    def set_output_voltage(self, v1: float, v2: float) -> None:
        if self.p is None:
            raise RuntimeError("Red Pitaya connection is not established")
        if self.asg1 is None or self.asg2 is None:
            raise RuntimeError("ASG outputs are not initialized")

        self._validate_output_voltage(v1, v2)

        # Use DC offsets on the ASGs so the outputs are explicit and match the repository examples.
        self.asg1.setup(
            waveform="dc",
            offset=float(v1),
            amplitude=0.0,
            trigger_source="immediately",
        )
        self.asg2.setup(
            waveform="dc",
            offset=float(v2),
            amplitude=0.0,
            trigger_source="immediately",
        )

    def set_output_sweep(self, v1_values: list[float], v2_values: list[float], delay_s: float = 0.1) -> None:
        if len(v1_values) != len(v2_values):
            raise ValueError("v1_values and v2_values must have the same length")

        for v1, v2 in zip(v1_values, v2_values):
            self.set_output_voltage(v1, v2)
            time.sleep(delay_s)

    def set_phi2_sine(self, *, offset: float, amplitude: float, frequency_hz: float) -> None:
        """Drive OUT2/phi2 with a bounded sine; OUT1/phi1 remains at zero."""
        if self.p is None or self.asg1 is None or self.asg2 is None:
            raise RuntimeError("Red Pitaya connection is not established")
        if frequency_hz <= 0.0 or amplitude < 0.0:
            raise ValueError("Sine frequency must be positive and amplitude non-negative")
        self._validate_output_voltage(0.0, offset - amplitude)
        self._validate_output_voltage(0.0, offset + amplitude)
        self.asg1.setup(waveform="dc", offset=0.0, amplitude=0.0, trigger_source="immediately")
        self.asg2.setup(
            waveform="sin",
            frequency=float(frequency_hz),
            offset=float(offset),
            amplitude=float(amplitude),
            trigger_source="immediately",
        )

    def set_phi1_sine(self, *, offset: float, amplitude: float, frequency_hz: float) -> None:
        """Drive OUT1/phi1 with a bounded sine; OUT2/phi2 remains at zero."""
        if self.p is None or self.asg1 is None or self.asg2 is None:
            raise RuntimeError("Red Pitaya connection is not established")
        if frequency_hz <= 0.0 or amplitude < 0.0:
            raise ValueError("Sine frequency must be positive and amplitude non-negative")
        self._validate_output_voltage(offset - amplitude, 0.0)
        self._validate_output_voltage(offset + amplitude, 0.0)
        self.asg2.setup(waveform="dc", offset=0.0, amplitude=0.0, trigger_source="immediately")
        self.asg1.setup(
            waveform="sin",
            frequency=float(frequency_hz),
            offset=float(offset),
            amplitude=float(amplitude),
            trigger_source="immediately",
        )

    @contextmanager
    def photodiode_monitor(self):
        """Temporarily route the Pyrpl scope's first channel to the PD on IN1."""
        if self.p is None:
            raise RuntimeError("Red Pitaya connection is not established")
        scope = self.p.rp.scope
        previous = {
            "input1": scope.input1,
            "duration": scope.duration,
            "decimation": scope.decimation,
        }
        scope.input1 = self.config.pd_input
        scope.duration = self.config.pd_scope_duration_s
        scope.decimation = self.config.pd_scope_decimation
        try:
            yield lambda: self._read_photodiode(scope)
        finally:
            for name, value in previous.items():
                setattr(scope, name, value)

    def _read_photodiode(self, scope: Any) -> PhotodiodeReading:
        trace = np.asarray(scope.single(timeout=self.config.pd_scope_timeout_s)[0], dtype=float)
        if trace.size == 0:
            raise RuntimeError("Red Pitaya scope returned an empty photodiode trace")
        return PhotodiodeReading(
            mean_voltage=float(np.mean(trace)),
            std_voltage=float(np.std(trace)),
            min_voltage=float(np.min(trace)),
            max_voltage=float(np.max(trace)),
            sample_count=int(trace.size),
        )

    def _validate_output_voltage(self, v1: float, v2: float) -> None:
        lower = self.config.rp_output_min_voltage
        upper = self.config.rp_output_max_voltage
        if not (lower <= v1 <= upper and lower <= v2 <= upper):
            raise ValueError(
                f"RP outputs must remain within [{lower:.3f}, {upper:.3f}] V; "
                f"received ({v1:.3f}, {v2:.3f}) V"
            )

    def set_output_zero(self) -> None:
        self.set_output_voltage(0.0, 0.0)
