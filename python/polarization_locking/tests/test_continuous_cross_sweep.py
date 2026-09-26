"""Offline checks for continuous coupling acquisition and analysis."""
from contextlib import contextmanager
import csv
import json

import numpy as np
import pytest

from polarization_locking.catalog import BY_KEY, default_options
from polarization_locking.config import PolarizationLockConfig
from polarization_locking.hardware.pax_interface import PAXReading
from polarization_locking.reports.plot_continuous_cross_sweep import (
    branch_separation,
    cycle_repeatability,
    fit_plane,
)
from polarization_locking.routines import continuous_cross_sweep as continuous
from polarization_locking.settings import load_recipe, validate


def test_cross_sweep_both_validates_and_rejects_unsafe_waveform():
    case = BY_KEY["cross-sweep"]
    options = default_options(case) | {"actuator": "both"}
    validate(PolarizationLockConfig(), case, options)
    config = PolarizationLockConfig(cross_sweep_sine_center_voltage=0.1,
                                    cross_sweep_sine_amplitude_voltage=0.2)
    with pytest.raises(ValueError, match="center"):
        validate(config, case, options)


def test_legacy_cross_sweep_axis_recipe_migrates(tmp_path):
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps({
        "schema_version": 1, "test": "cross-sweep",
        "options": {"axis": "phi2"}, "config": {},
    }))
    _, _, options = load_recipe(path)
    assert options["actuator"] == "phi2"
    assert "axis" not in options


def test_reference_decimation_and_gap_safe_interpolation():
    signal = np.arange(20, dtype=float)
    times, in1, in2, rate = continuous._boxcar_decimate(signal, signal * 2, 0.001, 250)
    assert rate == pytest.approx(250)
    assert in1 == pytest.approx([1.5, 5.5, 9.5, 13.5, 17.5])
    assert in2 == pytest.approx(in1 * 2)
    value, gap, valid = continuous._interpolate_reference(
        np.asarray([0.0, 0.1, 1.0, 1.1]), np.asarray([0.0, 1.0, 10.0, 11.0]),
        np.asarray([0, 0, 1, 1]), 0.5,
    )
    assert not valid and np.isnan(value) and gap == pytest.approx(0.9)
    value, gap, valid = continuous._interpolate_reference(
        np.asarray([0.0, 0.1]), np.asarray([0.0, 1.0]), np.asarray([0, 0]), 0.05,
    )
    assert valid and value == pytest.approx(0.5) and gap == pytest.approx(0.1)


def _synthetic_rows(branch_offset=0.0):
    rows = []
    for cycle in (0, 1):
        for phase in np.linspace(0, 2 * np.pi, 80, endpoint=False):
            voltage = 0.5 + 0.4 * np.sin(phase)
            direction = "rising" if np.cos(phase) > 0 else "falling"
            angular_offset = branch_offset if direction == "falling" else 0.0
            angle = 2 * np.pi * voltage + angular_offset
            rows.append({
                "cycle_index": cycle, "sine_phase_rad": phase,
                "measured_target_v": voltage, "voltage_direction": direction,
                "s1": 0.2, "s2": np.sqrt(1 - 0.2 ** 2) * np.cos(angle),
                "s3": np.sqrt(1 - 0.2 ** 2) * np.sin(angle),
                "pax_ptotal": 1.0, "dop": 0.99,
            })
    return rows


def test_plane_cycle_and_branch_metrics_are_descriptive():
    rows = _synthetic_rows(branch_offset=np.deg2rad(3))
    normal, residual = fit_plane(rows)
    assert abs(normal[0]) > 0.99
    assert residual < 1e-12
    cycle_angle, cycle_power, cycle_dop = cycle_repeatability(rows)
    assert cycle_angle < 1e-5 and cycle_power == pytest.approx(0) and cycle_dop == pytest.approx(0)
    branch_angle, branch_power = branch_separation(rows)
    assert 2 < branch_angle < 4
    assert branch_power == pytest.approx(0)


class _Clock:
    def __init__(self):
        self.value = 0.0

    def monotonic(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class _Future:
    def __init__(self, scope):
        self.scope = scope
        self.polls = 0

    def done(self):
        self.polls += 1
        return self.polls > 1

    def result(self):
        phase = np.linspace(0, 2 * np.pi, 100, endpoint=False)
        return np.sin(phase) * 0.3 + 0.4, np.full(100, 0.2)

    def cancel(self):
        pass


class _Scope:
    duration = 0.1
    sampling_time = 0.001
    decimation = 65536
    average = True

    def single_async(self):
        return _Future(self)


class _RP:
    def __init__(self):
        self.scope = _Scope()
        self.ramps = []

    @contextmanager
    def dual_reference_monitor(self, **_):
        yield self.scope

    def ramp_output_voltage(self, v1, v2, **_):
        self.ramps.append((v1, v2))

    def set_output_voltage(self, v1, v2):
        self.ramps.append((v1, v2))

    def set_continuous_sine(self, *, target_axis, fixed_voltage, center, amplitude, frequency_hz):
        return {"frequency_hz": frequency_hz, "center_v": center,
                "amplitude_v": amplitude, "fixed_v": fixed_voltage}


class _PAX:
    def __init__(self, clock):
        self.clock = clock
        self.count = 0
        self.last_raw_record = {}

    def read_fresh_polarization(self):
        self.clock.sleep(0.1)
        self.count += 1
        phase = self.count * 0.2
        self.last_raw_record = {"measurement_counter": self.count}
        return PAXReading(
            timestamp=self.count, theta=0, eta=0, s1=0.2,
            s2=float(np.sqrt(0.96) * np.cos(phase)),
            s3=float(np.sqrt(0.96) * np.sin(phase)), dop=0.98, ptotal=1e-6,
        )


def test_both_mode_simulation_writes_one_trace_and_pax_rate_csv(tmp_path, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(continuous.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(continuous.time, "sleep", clock.sleep)
    monkeypatch.setattr(continuous, "_scope_sleep", clock.sleep)
    config = PolarizationLockConfig(
        cross_sweep_sine_frequency_hz=2.0,
        cross_sweep_sine_center_voltage=0.4,
        cross_sweep_sine_amplitude_voltage=0.3,
        cross_sweep_warmup_cycles=0,
        cross_sweep_recorded_cycles=1,
        cross_sweep_phi1_bias_voltages=(0.2,),
        cross_sweep_phi2_bias_voltages=(0.3,),
        cross_sweep_settle_s=0,
        cross_sweep_bias_ramp_s=0,
        cross_sweep_reference_sample_rate_hz=1000,
        cross_sweep_scope_block_s=0.1,
    )
    csv_path = tmp_path / "data.csv"
    rp = _RP()
    result = continuous.acquire_continuous_cross_sweep(rp, _PAX(clock), config, csv_path, "both")
    assert result["status"] == "completed"
    assert rp.ramps[-1] == (0.0, 0.0)
    with csv_path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert {row["target_actuator"] for row in rows} == {"phi1", "phi2"}
    assert all("pax_raw_json" in row for row in rows)
    traces = list(tmp_path.glob("*.npz"))
    assert traces == [tmp_path / "drive_trace.npz"]
    assert (tmp_path / "acquisition.json").is_file()
    with np.load(traces[0]) as trace:
        assert set(("time_s", "in1_v", "in2_v", "segment_id", "actuator_id",
                    "bias_index", "cycle_index", "valid_measurement", "metadata_json")) <= set(trace.files)
        assert trace["in1_v"].dtype == np.float32
        assert set(trace["actuator_id"]) == {1, 2}
