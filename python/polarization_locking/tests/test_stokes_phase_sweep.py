"""Acquisition, validity and failure handling without instrument access."""
from contextlib import contextmanager
import csv
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from polarization_locking.catalog import BY_KEY, default_options
from polarization_locking.config import PolarizationLockConfig
from polarization_locking.hardware.pax_interface import PAXController, PAXReading
from polarization_locking.hardware.rp_interface import PhotodiodeReading, RPController
from polarization_locking.routines import stokes_phase_sweep as sweep
from polarization_locking.runner import execute
from polarization_locking.settings import load_recipe, validate


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, duration):
        self.now += duration


def reading(timestamp=1, **kwargs):
    return replace(PAXReading(timestamp=timestamp, revisions=timestamp * 10,
                             theta=0, eta=0, s1=1, s2=0, s3=0, dop=.4,
                             ptotal=.001, adc_min=4000, adc_max=45000, rev_time=1/60), **kwargs)


def test_fresh_pax_rejects_startup_clipping_and_duplicate_device_records(monkeypatch):
    from polarization_locking.hardware import pax_interface
    monkeypatch.setattr(pax_interface, "time", Clock())
    pax = PAXController(PolarizationLockConfig())
    pax.read_polarization = Mock(side_effect=[
        KeyError("timestamp"), reading(0), reading(1, eta=9.91e37),
        reading(1, dop=float("nan")), reading(1, adc_min=65520, adc_max=65520),
        reading(1), reading(1), reading(2, revisions=10), reading(2),
        reading(2), reading(3, dop=1.004),
    ])
    assert pax.read_fresh_polarization().timestamp == 2
    assert pax.read_fresh_polarization().dop == 1.004
    assert pax.read_polarization.call_count == 11
    pax.disconnect()
    assert pax._last_fresh_record is None


@pytest.mark.parametrize("sample", [reading(0), reading(1), reading(1, ptotal=9.91e37)])
def test_fresh_pax_times_out_without_advancing_valid_data(monkeypatch, sample):
    from polarization_locking.hardware import pax_interface
    clock = Clock()
    monkeypatch.setattr(pax_interface, "time", clock)
    pax = PAXController(PolarizationLockConfig(pax_fresh_read_timeout_s=.1))
    pax.read_polarization = Mock(return_value=sample)
    with pytest.raises(RuntimeError, match="valid advancing"):
        pax.read_fresh_polarization()
    assert .1 <= clock.now < .12


@pytest.mark.parametrize("changes", [
    {"stokes_phase_sweep_frequency_hz": 0},
    {"stokes_phase_sweep_frequency_hz": 10},
    {"stokes_phase_sweep_amplitude_v": 0},
    {"stokes_phase_sweep_offset_v": .1},
    {"stokes_phase_sweep_amplitude_v": .7},
    {"stokes_phase_sweep_sample_period_s": 0},
    {"pd_scope_decimation": 3},
    {"pax_fresh_read_timeout_s": 0},
    {"phase_output_map_confirmed": False},
])
def test_invalid_sweep_is_rejected_before_connecting(changes):
    factory = Mock()
    case = BY_KEY["stokes-phase-sweep"]
    with pytest.raises(ValueError):
        execute(case, PolarizationLockConfig(**changes), default_options(case), app_factory=factory)
    factory.assert_not_called()


def test_explicit_voltage_sweep_needs_no_phase_calibration():
    case = BY_KEY["stokes-phase-sweep"]
    config = PolarizationLockConfig(phi1_v_lambda=None, phi2_v_lambda=None,
                                    phi1_actuator_volts_per_rp_volt=None, phi2_actuator_volts_per_rp_volt=None)
    validate(config, case, default_options(case))
    with pytest.raises(ValueError, match="two drive cycles"):
        validate(config, case, default_options(case) | {"duration_s": 1.0})
    path = Path(__file__).resolve().parents[1] / "profiles/stokes-phase-sweep-example.json"
    assert load_recipe(path)[0] == case


def instruments(clock, failure=None):
    events = []
    scope = SimpleNamespace(sampling_time=512e-9, duration=.008388608, decimation=64, average=True)
    rp = Mock()
    rp.p.rp.scope = scope
    rp.asg1 = SimpleNamespace(frequency=.5, amplitude=.36, offset=.36)
    rp.set_output_zero.side_effect = lambda: events.append("zero")
    rp.set_phi1_sine.side_effect = lambda **kw: events.append("drive")

    def reference():
        clock.sleep(.01)
        return PhotodiodeReading(.123, .001, .12, .126, 16384)

    @contextmanager
    def monitor(**kwargs):
        assert kwargs == {"input_channel": "in1"}
        try:
            yield reference
        finally:
            events.append("scope restored")

    rp.photodiode_monitor = monitor
    pax = Mock()
    count = 0

    def acquire():
        nonlocal count
        count += 1
        clock.sleep(.06)
        if failure and count == 3:
            raise failure
        events.append("pax")
        return reading(count)

    pax.read_fresh_polarization.side_effect = acquire
    return rp, pax, events


@pytest.mark.parametrize("failure", [None, KeyboardInterrupt(), RuntimeError("PAX stalled")])
def test_csv_is_measured_reference_with_timing_and_partial_data(tmp_path, monkeypatch, failure):
    clock = Clock()
    monkeypatch.setattr(sweep, "time", clock)
    rp, pax, events = instruments(clock, failure)
    output = tmp_path / "data.csv"
    args = (rp, pax, PolarizationLockConfig(pd_nd_optical_density=9), str(output), .2)
    if failure:
        with pytest.raises(type(failure)):
            sweep.acquire_stokes_phase_sweep(*args)
    else:
        sweep.acquire_stokes_phase_sweep(*args)
    rows = list(csv.DictReader(output.open()))
    assert len(rows) == (1 if failure else 3)
    assert events[:3] == ["zero", "pax", "drive"]
    assert events[-2:] == ["scope restored", "zero"]
    assert all(float(row["in1_reference_v"]) == .123 for row in rows)
    assert float(rows[0]["out1_command_estimated_v"]) != .123
    assert float(rows[0]["s1"]) == 1
    assert float(rows[0]["s1_over_s0"]) == .4
    assert [float(row["pax_timestamp"]) for row in rows] == list(range(2, len(rows) + 2))
    for row in rows:
        assert float(row["pax_requested_s"]) < float(row["pax_received_s"]) <= float(row["in1_started_s"])
        assert float(row["in1_started_s"]) < float(row["elapsed_s"]) < float(row["in1_finished_s"])
        assert float(row["rp_out2_v"]) == 0
        assert int(row["in1_sample_count"]) == 16384


def test_voltage_monitor_forces_in1_immediate_capture_and_restores_scope():
    initial = dict(input1="in2", duration=.1, decimation=1024, average=False,
                   trigger_source="ext_positive_edge", trigger_delay=.3,
                   ch1_active=False, ch2_active=True, rolling_mode=True,
                   trace_average=5, _trigger_delay_register=123)
    scope = SimpleNamespace(**initial)
    scope.setup = lambda **kwargs: vars(scope).update(kwargs)
    scope.single = Mock(return_value=[[.1, .2, .3]])
    scope.stop = Mock()
    rp = RPController(PolarizationLockConfig(pd_input="in2"))
    rp.p = SimpleNamespace(rp=SimpleNamespace(scope=scope))
    with pytest.raises(KeyboardInterrupt):
        with rp.photodiode_monitor(input_channel="in1") as read:
            assert scope.input1 == "in1" and scope.trigger_source == "immediately"
            assert scope.trace_average == 1 and scope.ch1_active and not scope.ch2_active
            assert read().mean_voltage == pytest.approx(.2)
            raise KeyboardInterrupt
    assert all(getattr(scope, name) == value for name, value in initial.items())
    scope.stop.assert_called_once()


def test_runner_acquires_and_regenerates_standard_report(tmp_path, monkeypatch):
    from polarization_locking.app import PolarizationLockApp
    from polarization_locking.reports.run_report import main as regenerate
    clock = Clock()
    monkeypatch.setattr(sweep, "time", clock)
    config = PolarizationLockConfig(results_directory=str(tmp_path))
    app = PolarizationLockApp(config)
    app.rp, app.pax, events = instruments(clock)
    app.connect = Mock()
    app.disconnect = lambda: events.append("disconnect")
    case = BY_KEY["stokes-phase-sweep"]
    state = execute(case, config, default_options(case) | {"duration_s": 4.0}, app_factory=lambda _: app)
    assert state["status"] == state["report_status"] == "completed", state
    directory = next(tmp_path.rglob("data.csv")).parent
    assert {"data.csv", "recipe.json", "run.json", "console.log", "report.pdf"} <= {p.name for p in directory.iterdir()}
    assert events[-1] == "disconnect"
    assert (directory / "report.pdf").read_bytes().startswith(b"%PDF-")
    regenerate([str(directory)])
    assert (directory / "report.pdf").stat().st_size > 10000
