"""Sessions: driver-free lifecycle mock plus deterministic acquisition mock."""
from datetime import datetime, timezone
from typing import Protocol


class Session(Protocol):
    def connect(self) -> None: ...
    def disconnect(self) -> None: ...


class MockSession:
    """No driver imports, instrument access, output commands, or optical model."""
    def connect(self):
        print("Mock session initialized (no hardware).")

    def disconnect(self):
        print("Mock session closed (no hardware).")


class MockAcquisitionSession(MockSession):
    """Deterministic counters, signed PD values and PAX telemetry; no fake physics."""
    mode = "mock"

    def __init__(self, config):
        self.config = config
        self.elapsed = 0.0
        self.pax_index = 0
        self.commands = [0.0, 0.0]

    def clock(self):
        self.elapsed += 0.0001
        return self.elapsed

    def sleep(self, seconds):
        self.elapsed += seconds

    def origin(self):
        return {"utc": datetime(2000, 1, 1, tzinfo=timezone.utc).isoformat(),
                "monotonic_origin_s": 0.0, "clock": "deterministic synthetic clock"}

    def describe(self):
        dt = 8e-9 * self.config.pd_scope_decimation
        return {"mode": self.mode, "pd": {"input": self.config.pd_input,
                "sample_count": 16384, "sampling_time_s": dt,
                "duration_s": 16384 * dt, "decimation": self.config.pd_scope_decimation,
                "fpga_average": self.config.pd_fpga_average, "trace_average": 1,
                "trigger_source": "immediately", "trigger_delay_s": 0.0},
                "pax": {"source": "synthetic counters"}, "synchronization": "sequential; not sample-synchronous"}

    def command(self, values):
        self.commands = list(values)
        return {"acknowledged": True, "asg_offset_settings_v": list(values),
                "delivered_eom_voltage_measured": False, "synthetic": True}

    def capture_pd(self):
        import numpy as np
        self.sleep(self.describe()["pd"]["duration_s"])
        return (np.arange(16384, dtype=np.float64) % 8 - 4) * 0.001

    def read_pax(self):
        self.sleep(self.config.pax_measurement_wait_s)
        self.pax_index += 1
        raw = {"timestamp": float(self.pax_index), "revisions": float(self.pax_index),
               "theta": 0.0, "eta": 0.0, "dop": 1.0, "ptotal": 0.001,
               "adc_min": 10.0, "adc_max": 100.0, "rev_time": 1.0}
        return {"raw_record": raw, "interface_reading": dict(raw, s1=1.0, s2=0.0, s3=0.0), "synthetic": True}

    def disconnect(self):
        self.commands = [0.0, 0.0]
        super().disconnect()
