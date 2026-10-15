"""PAX-only optical reference; existing RP controller and explicit zero cleanup."""
from dataclasses import asdict
from datetime import datetime, timezone
import time

from .legacy import BenchSession
from ..acquisition.eom import json_evidence


class VpiSession(BenchSession):
    def connect(self):
        self._origin = {"utc": datetime.now(timezone.utc).isoformat(),
                        "monotonic_origin_s": time.monotonic(), "clock": "time.monotonic"}
        self.pax.connect()
        first = self.pax.read_fresh_polarization()  # Validate PAX before touching RP.
        self.startup_pax = {"interface_reading": asdict(first), "raw_record": dict(self.pax.last_raw_record or {})}
        self.rp.connect()  # Existing route initialization and zeroing; no PD/scope setup.

    def describe(self):
        return {"mode": self.mode, "rp_hostname": self.config.rp_hostname,
                "pax": {"wavelength_nm": self.config.pax_wavelength_nm,
                        "reference_plane": self.config.pax_reference_plane,
                        "daemon_config": self.config.pax_daemon_config_path},
                "startup_pax": json_evidence(self.startup_pax), "voltage_chain": self.config.voltage_chain,
                "pd": None, "synchronization": "Sequential RP ramps and fresh PAX reads; host timing only"}

    def command(self, values):
        self.rp.ramp_output_voltage(*values, duration_s=self.config.ramp_duration_s, updates_per_s=20)
        return {"acknowledged": True, "asg_offset_settings_v": [float(self.rp.asg1.offset), float(self.rp.asg2.offset)],
                "delivered_eom_voltage_measured": False}

    def read_pax(self):
        reading = self.pax.read_fresh_polarization()
        return {"raw_record": dict(self.pax.last_raw_record or {}),
                "interface_reading": asdict(reading), "synthetic": False}

    def disconnect(self):
        errors = []
        self.cleanup_evidence = {"delivered_voltage_measured": False}
        if self.rp.p is not None and self.rp.asg1 is not None and self.rp.asg2 is not None:
            try:
                self.rp.set_output_zero()
                values = [float(self.rp.asg1.offset), float(self.rp.asg2.offset)]
                self.cleanup_evidence['final_asg_offset_settings_v'] = values
                if any(abs(value) > 1e-6 for value in values):
                    raise RuntimeError("RP zero-setting readback failed")
            except BaseException as exc:
                errors.append(str(exc))
        try:
            super().disconnect()  # Also handles partially failed connect; retries zeroing.
        except BaseException as exc:
            errors.append(str(exc))
        if errors:
            raise RuntimeError('; '.join(errors))
