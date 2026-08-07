from __future__ import annotations

import time
from typing import Any

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

        self.p = Pyrpl(hostname=self.config.rp_hostname, config=self.config.rp_config)
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
