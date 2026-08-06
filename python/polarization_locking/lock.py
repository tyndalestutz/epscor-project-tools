#!/usr/bin/env python3
from __future__ import annotations

import time
from typing import Optional

try:
    from .config import DEFAULT_CONFIG, PolarizationLockConfig
    from .control import PolarizationState, error_to_voltage, polarization_error
    from .pax_interface import PAXController
    from .rp_interface import RPController
except ImportError:  # pragma: no cover - support direct execution
    from config import DEFAULT_CONFIG, PolarizationLockConfig
    from control import PolarizationState, error_to_voltage, polarization_error
    from pax_interface import PAXController
    from rp_interface import RPController


class PolarizationLockApp:
    def __init__(self, config: Optional[PolarizationLockConfig] = None) -> None:
        self.config = config or DEFAULT_CONFIG
        self.rp = RPController(self.config)
        self.pax = PAXController(self.config)
        self.running = False

    def connect(self) -> None:
        self.rp.connect()
        self.pax.connect()
        self.running = True

    def disconnect(self) -> None:
        self.running = False
        self.pax.disconnect()
        self.rp.disconnect()

    def run_once(self) -> None:
        if not self.running:
            raise RuntimeError("App is not connected")

        reading = self.pax.read_polarization()
        current_state = PolarizationState(theta=reading.theta, eta=reading.eta)
        target_state = PolarizationState(theta=self.config.target_theta, eta=self.config.target_eta)

        error = polarization_error(current_state, target_state)
        voltages = error_to_voltage(error, self.config.voltage_gain)

        print(f"current=(theta={current_state.theta:.4f}, eta={current_state.eta:.4f})")
        print(f"target=(theta={target_state.theta:.4f}, eta={target_state.eta:.4f})")
        print(f"error=(d_theta={error[0]:.4f}, d_eta={error[1]:.4f})")
        print(f"voltages=(phi1={voltages[0]:.4f}, phi2={voltages[1]:.4f})")

        # Placeholder: this is where you would write the voltages to the RP actuators.
        # The exact mapping should be calibrated experimentally.
        # Example:
        # self.rp.pid.setpoint = ...
        # self.rp.pid.ival = ...

        time.sleep(1.0 / self.config.update_hz)

    def run(self) -> None:
        self.connect()
        try:
            while self.running:
                self.run_once()
        finally:
            self.disconnect()


if __name__ == "__main__":
    app = PolarizationLockApp()
    try:
        app.run()
    except KeyboardInterrupt:
        print("Stopping polarization lock loop")
