"""Thin bench session: reuse legacy controllers without locking-model configuration."""
from dataclasses import asdict
from datetime import datetime, timezone
import time
from types import SimpleNamespace


class BenchSession:
    mode = "hardware"

    def __init__(self, config):
        # Driver construction is side-effect free; only connect initializes hardware.
        from polarization_locking.hardware.rp_interface import RPController
        from polarization_locking.hardware.pax_interface import PAXController
        self.config = config
        self.rp = RPController(SimpleNamespace(
            rp_hostname=config.rp_hostname, rp_config=config.rp_config,
            rp_output_min_voltage=config.rp_output_min_voltage,
            rp_output_max_voltage=config.rp_output_max_voltage,
            pd_input=config.pd_input, pd_scope_duration_s=config.pd_duration_s,
            pd_scope_decimation=config.pd_scope_decimation,
            pd_scope_timeout_s=config.pd_scope_timeout_s))
        self.pax = PAXController(config)
        self._monitor = None
        self._monitor_entered = False
        self._origin = None

    def connect(self):
        self.rp.connect()  # legacy initialization disables other routes and zeros both outputs
        self.pax.connect()  # legacy local-daemon ownership/startup behavior is retained
        self._monitor = self.rp.photodiode_monitor(input_channel=self.config.pd_input)
        self._monitor.__enter__()
        self._monitor_entered = True
        self.scope = self.rp.p.rp.scope
        # Same fixed-buffer, immediate-trigger configuration as legacy raw capture.
        self.scope.setup(average=self.config.pd_fpga_average, trace_average=1,
                         trigger_source="immediately", trigger_delay=0.0,
                         ch1_active=True, ch2_active=False, rolling_mode=False)
        if self.config.pd_scope_timeout_s <= float(self.scope.duration):
            raise ValueError("PD timeout must exceed the effective scope capture duration")
        self._origin = {"utc": datetime.now(timezone.utc).isoformat(),
                        "monotonic_origin_s": time.monotonic(), "clock": "time.monotonic"}

    def origin(self):
        return dict(self._origin)

    def clock(self):
        return time.monotonic() - self._origin["monotonic_origin_s"]

    def sleep(self, seconds):
        time.sleep(seconds)

    def describe(self):
        return {"mode": self.mode, "rp_hostname": self.config.rp_hostname,
                "rp_config": self.config.rp_config,
                "pd": {"input": self.scope.input1, "sample_count": int(getattr(self.scope, "data_length", 16384)),
                       "sampling_time_s": float(self.scope.sampling_time),
                       "duration_s": float(self.scope.duration),
                       "decimation": int(self.scope.decimation),
                       "fpga_average": bool(self.scope.average),
                       "trace_average": int(self.scope.trace_average),
                       "trigger_source": str(self.scope.trigger_source),
                       "trigger_delay_s": float(self.scope.trigger_delay)},
                "pax": {"host": self.config.pax_host, "port": self.config.pax_port,
                        "wavelength_nm": self.config.pax_wavelength_nm,
                        "reference_plane": self.config.pax_reference_plane,
                        "daemon_config": self.config.pax_daemon_config_path,
                        "daemon_log": str(self.pax._daemon_log_path) if self.pax._daemon_log_path else None},
                "voltage_chain": self.config.voltage_chain,
                "synchronization": "Sequential ASG writes, PD captures, then PAX requests; no shared trigger/clock"}

    def command(self, values):
        self.rp.set_output_voltage(*values)
        return {"acknowledged": True,
                "asg_offset_settings_v": [float(self.rp.asg1.offset), float(self.rp.asg2.offset)],
                "delivered_eom_voltage_measured": False}

    def capture_pd(self):
        import numpy as np
        # The existing callback computes summaries; access its configured scope
        # directly to retain all returned samples, with no normalization/filtering.
        return np.asarray(self.scope.single(timeout=self.config.pd_scope_timeout_s)[0], dtype=np.float64).copy()

    def read_pax(self):
        # Deliberately no read_fresh_polarization validity gate: store every returned
        # record, including low DoP/stale/anomalous values, for offline evaluation.
        reading = self.pax.read_polarization()
        return {"raw_record": dict(self.pax.last_raw_record or {}),
                "interface_reading": asdict(reading), "synthetic": False}

    def pax_error_evidence(self):
        return dict(self.pax.last_raw_record or {})

    def disconnect(self):
        errors = []
        if self._monitor_entered:
            try:
                self._monitor.__exit__(None, None, None)
            except BaseException as exc:
                errors.append(f"scope restore: {type(exc).__name__}: {exc}")
            self._monitor_entered = False
        for name, controller in (("RP zero/cleanup", self.rp), ("PAX cleanup", self.pax)):
            try:
                controller.disconnect()
            except BaseException as exc:
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
        if errors:
            raise RuntimeError("; ".join(errors))
