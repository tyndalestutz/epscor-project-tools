"""Visibility metrology and passive hardware lifecycle regressions."""
import csv
import json
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from polarization_locking.app import PolarizationLockApp
from polarization_locking.catalog import BY_KEY, default_options
from polarization_locking.config import PolarizationLockConfig
from polarization_locking.hardware.rp_interface import _ScopeClient
from polarization_locking.hardware.rp_interface import RPController
from polarization_locking.routines.visibility import analyze_trace, acquire_visibility
from polarization_locking.runner import execute
from polarization_locking.settings import validate


def signal():
    dt = 4.294967296 / 16384
    t = np.arange(16384) * dt
    return t, 0.3 + 0.15 * np.cos(2 * np.pi * .5 * t), dt


@pytest.mark.parametrize("polarity", [1, -1])
def test_known_visibility_with_dark_offset_and_either_polarity(polarity):
    _, v, dt = signal()
    result, _ = analyze_trace(.02 + polarity * v, dt, .5, .02)
    assert result["visibility"] == pytest.approx(.5, abs=.001)
    assert result["peak_to_peak_v"] == pytest.approx(.3, abs=.001)
    assert result["polarity"] == polarity


def test_noise_does_not_become_fifty_percent_visibility():
    _, v, dt = signal()
    trace = -.002 + np.random.default_rng(17).normal(0, .0006, v.size)
    p5, p95 = np.percentile(trace, [5, 95])
    assert (p95 - p5) / abs(p95 + p5) > .45
    result, _ = analyze_trace(trace, dt, .5, 0.0)
    assert result["visibility"] is None
    assert "unresolved" in result["status"]


@pytest.mark.parametrize("kind", ["clipped", "crossing-zero", "nan", "short"])
def test_invalid_trace_does_not_report_visibility(kind):
    _, trace, dt = signal()
    if kind == "clipped":
        trace[0] = 1
    elif kind == "crossing-zero":
        trace -= .3
    elif kind == "nan":
        trace[0] = np.nan
    else:
        trace = trace[:100]
    if kind in {"nan", "short"}:
        with pytest.raises(ValueError):
            analyze_trace(trace, dt, .5)
    else:
        assert analyze_trace(trace, dt, .5, 0.0)[0]["visibility"] is None


@pytest.mark.parametrize("address", [0x40380004, 0x40390004, 0x40200000, 0x40300004])
def test_passive_connection_blocks_output_registers(address):
    client = Mock()
    guarded = _ScopeClient(client)
    with pytest.raises(RuntimeError, match="refused"):
        guarded.writes(address, [1])
    client.writes.assert_not_called()


def test_scope_mux_and_adc_writes_are_allowed():
    client = Mock()
    guarded = _ScopeClient(client)
    for address in (0x40380000, 0x40390000, 0x40100014):
        guarded.writes(address, [1])
    assert client.writes.call_count == 3


@pytest.mark.parametrize("failure", [None, RuntimeError("capture failed"), KeyboardInterrupt()])
def test_passive_runner_never_connects_pax_or_initializes_outputs(tmp_path, monkeypatch, failure):
    config = PolarizationLockConfig(results_directory=str(tmp_path))
    app = PolarizationLockApp(config)
    app.rp = Mock()
    app.pax = Mock()
    app._run_pd_visibility = Mock(side_effect=failure)
    monkeypatch.setattr("polarization_locking.reports.run_report.create_run_report", Mock(return_value={}))
    case = BY_KEY["pd-visibility"]
    state = execute(case, config, default_options(case), app_factory=lambda _: app)
    assert state["status"] == ("completed" if failure is None else "interrupted" if isinstance(failure, KeyboardInterrupt) else "failed")
    app.rp.connect_scope_only.assert_called_once()
    app.rp.disconnect_scope_only.assert_called_once()
    app.rp.connect.assert_not_called()
    app.rp.disconnect.assert_not_called()
    assert app.pax.mock_calls == []


def test_scope_capture_restores_settings_and_keeps_partial_data(tmp_path, monkeypatch):
    t, v, dt = signal()
    original = dict(input1="in1", decimation=64, average=False, trigger_source="ch1_positive_edge",
                    trigger_delay=.1, ch1_active=False, ch2_active=True, rolling_mode=True, trace_average=4)
    scope = SimpleNamespace(**original, _trigger_delay_register=71, times=t, sampling_time=dt, duration=4.294967296)
    scope.setup = lambda **kw: [setattr(scope, key, value) for key, value in kw.items()]
    scope.stop = Mock()
    scope.single = Mock(side_effect=[np.asarray([v, v]), KeyboardInterrupt()])
    config = PolarizationLockConfig(pd_input="in2", visibility_dark_voltage_v=0.0)
    with pytest.raises(KeyboardInterrupt):
        acquire_visibility(scope, config, str(tmp_path / "data.csv"), 100)
    assert {name: getattr(scope, name) for name in original} == original
    assert scope._trigger_delay_register == 71
    assert (tmp_path / "capture-000.npz").exists()
    with (tmp_path / "data.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1 and rows[0]["pd_input"] == "in2"
    assert json.loads((tmp_path / "visibility.json").read_text())["visibility_mean"] == pytest.approx(.5, abs=.001)
    from polarization_locking.reports.run_report import create_run_report
    paths = SimpleNamespace(directory=tmp_path, csv=tmp_path / "data.csv", pdf=tmp_path / "report.pdf")
    case = BY_KEY["pd-visibility"]
    state = create_run_report(case, paths, config, default_options(case), {"status": "interrupted"})
    assert state["report_status"] == "completed"


def test_visibility_needs_no_actuator_calibration():
    config = PolarizationLockConfig(phase_output_map_confirmed=False, phi1_v_lambda=None, phi2_v_lambda=None)
    case = BY_KEY["pd-visibility"]
    validate(config, case, default_options(case))
    for name, value in (("pd_input", "out1"), ("visibility_frequency_hz", 0), ("rp_scope_port", 0)):
        broken = PolarizationLockConfig(**{name: value})
        with pytest.raises(ValueError):
            validate(broken, case, default_options(case))


def test_scope_disconnect_restores_raw_registers_and_closes_transport():
    rp = RPController(PolarizationLockConfig())
    transport = Mock()
    guarded = _ScopeClient(transport)
    previous = {0x40100014: np.array([16384]), 0x40380000: np.array([11])}
    rp.p = SimpleNamespace(rp=SimpleNamespace(client=guarded, previous=previous))
    rp._scope_ssh = Mock()
    ssh = rp._scope_ssh
    rp.disconnect_scope_only()
    assert transport.writes.call_count == 2
    for call, (address, values) in zip(transport.writes.call_args_list, previous.items()):
        assert call.args[0] == address
        np.testing.assert_equal(call.args[1], values)
    transport.close.assert_called_once()
    ssh.close.assert_called_once()
    assert rp.p is None


def test_visibility_cli_declares_input_and_frequency_without_hardware(capsys):
    from polarization_locking.cli import main
    assert main(["--run", "pd-visibility", "--pd-input", "in2", "--frequency", "1", "--dark-voltage", "-.001", "--dry-run"]) == 0
    config = json.loads(capsys.readouterr().out)["config"]
    assert config["pd_input"] == "in2"
    assert config["visibility_frequency_hz"] == 1
    assert config["visibility_dark_voltage_v"] == -.001


def test_missing_dark_preserves_signed_measurements_without_assuming_zero():
    _, trace, dt = signal()
    result, means = analyze_trace(-trace, dt, .5)
    assert result["min_v"] < result["max_v"] < 0
    assert result["mean_v"] < 0 and np.all(means < 0)
    assert result["visibility"] is None
    assert result["dark_voltage_v"] is None and result["polarity"] is None
    assert "dark voltage not measured" in result["status"]


def test_voltage_crossing_zero_is_valid_relative_to_measured_dark():
    _, trace, dt = signal()
    measured = trace - .3
    result, _ = analyze_trace(measured, dt, .5, -.3)
    assert result["min_v"] < 0 < result["max_v"]
    assert result["visibility"] == pytest.approx(.5, abs=.001)


@pytest.mark.parametrize("cancel_after_dark", [False, True])
def test_measured_negative_dark_is_saved_used_and_survives_cancellation(tmp_path, monkeypatch, cancel_after_dark):
    from polarization_locking.routines import visibility
    t, _, dt = signal()
    dark = np.full(t.size, -.0056)
    light = -.0056 + .01 + .001 * np.cos(2 * np.pi * .5 * t)
    original = dict(input1="in1", average=False, trigger_source="ext_positive_edge", trigger_delay=.1,
                    ch1_active=False, ch2_active=True, rolling_mode=True, trace_average=3, duration=4.294967296)
    scope = SimpleNamespace(**original, _trigger_delay_register=77, times=t, sampling_time=dt, decimation=32768)
    scope.setup = lambda **kw: vars(scope).update(kw)
    scope.stop = Mock()
    scope.single = Mock(side_effect=[np.array([dark, dark]), np.array([light, light]), KeyboardInterrupt()])
    prompts = Mock(side_effect=[None, KeyboardInterrupt()] if cancel_after_dark else [None, None])
    monkeypatch.setattr(visibility, "prepare_setup", prompts)
    with pytest.raises(KeyboardInterrupt):
        acquire_visibility(scope, PolarizationLockConfig(pd_input="in2"), str(tmp_path / "data.csv"), 100)
    assert prompts.call_count == 2
    with np.load(tmp_path / "dark.npz") as stored:
        np.testing.assert_array_equal(stored["voltage_v"], dark)
    baseline = json.loads((tmp_path / "dark.json").read_text())
    assert baseline["mean_v"] == pytest.approx(-.0056)
    summary = json.loads((tmp_path / "visibility.json").read_text())
    assert summary["dark_source"] == "measured" and summary["source"] == "pd"
    assert summary["dark_voltage_v"] == pytest.approx(-.0056)
    assert all(getattr(scope, name) == value for name, value in original.items())
    assert scope._trigger_delay_register == 77
    if not cancel_after_dark:
        assert summary["visibility_mean"] == pytest.approx(.1, abs=.001)
        row = next(csv.DictReader((tmp_path / "data.csv").open()))
        assert float(row["dark_voltage_v"]) == pytest.approx(-.0056)
        with np.load(tmp_path / "capture-000.npz") as stored:
            np.testing.assert_array_equal(stored["voltage_v"], light)
        from polarization_locking.reports.run_report import create_run_report
        paths = SimpleNamespace(directory=tmp_path, csv=tmp_path / "data.csv", pdf=tmp_path / "report.pdf")
        result = create_run_report(BY_KEY["pd-visibility"], paths, PolarizationLockConfig(),
                                   default_options(BY_KEY["pd-visibility"]), {"status": "interrupted"})
        assert result["report_status"] == "completed"
    else:
        assert summary["visibility_mean"] is None and summary["captures"] == 0


@pytest.mark.parametrize("failure", [None, KeyboardInterrupt(), RuntimeError("read failed")])
def test_pax_contrast_session_never_connects_or_changes_rp(tmp_path, monkeypatch, failure):
    config = PolarizationLockConfig(results_directory=str(tmp_path), visibility_source="pax")
    app = PolarizationLockApp(config)
    app.rp, app.pax = Mock(), Mock()
    app._run_pd_visibility = Mock(side_effect=failure)
    monkeypatch.setattr("polarization_locking.reports.run_report.create_run_report", Mock(return_value={}))
    case = BY_KEY["pd-visibility"]
    result = execute(case, config, default_options(case), app_factory=lambda _: app)
    assert result["status"] == ("completed" if failure is None else "interrupted" if isinstance(failure, KeyboardInterrupt) else "failed")
    assert app.rp.mock_calls == []
    app.pax.connect.assert_called_once()
    app.pax.disconnect.assert_called_once()


@pytest.mark.parametrize("failure", [None, KeyboardInterrupt(), RuntimeError("PAX stalled")])
def test_pax_contrast_logs_every_power_sample_and_reports(tmp_path, monkeypatch, failure):
    from polarization_locking.routines import visibility
    from polarization_locking.hardware.pax_interface import PAXReading
    from polarization_locking.reports.run_report import create_run_report
    from polarization_locking.tests.test_stokes_phase_sweep import Clock
    clock = Clock()
    monkeypatch.setattr(visibility, "time", clock)
    count = 0

    def read():
        nonlocal count
        count += 1
        clock.sleep(.1)
        if failure and count == 35:
            raise failure
        return PAXReading(timestamp=count, revisions=count * 10, theta=0, eta=0, s1=1, s2=0, s3=0,
                          dop=.2, ptotal=.001 + .0005 * np.cos(2 * np.pi * .5 * clock.now),
                          adc_min=4000, adc_max=45000, rev_time=1/60)

    pax = Mock()
    pax.read_fresh_polarization.side_effect = read
    config = PolarizationLockConfig(visibility_source="pax")
    if failure:
        with pytest.raises(type(failure)):
            visibility.acquire_pax_visibility(pax, config, str(tmp_path / "data.csv"), 4)
    else:
        visibility.acquire_pax_visibility(pax, config, str(tmp_path / "data.csv"), 4)
    rows = list(csv.DictReader((tmp_path / "data.csv").open()))
    summary = json.loads((tmp_path / "visibility.json").read_text())
    assert len(rows) >= 30 and summary["samples"] == len(rows)
    assert summary["source"] == "pax"
    assert summary["visibility"] is None if failure else summary["visibility"] == pytest.approx(.5)
    assert all(float(row["dop"]) == .2 for row in rows)
    assert not list(tmp_path.glob("*.npz"))
    case = BY_KEY["pd-visibility"]
    paths = SimpleNamespace(directory=tmp_path, csv=tmp_path / "data.csv", pdf=tmp_path / "report.pdf")
    result = create_run_report(case, paths, config, default_options(case), {"status": "completed" if failure is None else "interrupted"})
    assert result["report_status"] == "completed"
    assert paths.pdf.read_bytes().startswith(b"%PDF-")


def test_pax_source_cli_and_validation_are_hardware_free(capsys):
    from polarization_locking.cli import main
    assert main(["--run", "pd-visibility", "--source", "pax", "--frequency", ".5", "--dry-run"]) == 0
    config = json.loads(capsys.readouterr().out)["config"]
    assert config["visibility_source"] == "pax"
    case = BY_KEY["pd-visibility"]
    validate(PolarizationLockConfig(visibility_source="pax", visibility_dark_voltage_v=-.005), case, default_options(case))
    for changes in ({"visibility_source": "unknown"},
                    {"visibility_frequency_hz": 0}, {"visibility_frequency_hz": 20},
                    {"visibility_pax_sample_period_s": 0}, {"pax_fresh_read_timeout_s": 0}):
        with pytest.raises(ValueError):
            validate(PolarizationLockConfig(**({"visibility_source": "pax"} | changes)), case, default_options(case))


@pytest.mark.parametrize("source", ["pd", "pax", "both"])
@pytest.mark.parametrize("mode", ["passive", "active"])
@pytest.mark.parametrize("failure", [None, RuntimeError("acquisition failed"), KeyboardInterrupt()])
def test_contrast_detector_mode_lifecycle(tmp_path, monkeypatch, source, mode, failure):
    config = PolarizationLockConfig(results_directory=str(tmp_path), visibility_source=source, visibility_mode=mode)
    app = PolarizationLockApp(config)
    app.rp, app.pax = Mock(), Mock()
    app._run_pd_visibility = Mock(side_effect=failure)
    monkeypatch.setattr("polarization_locking.reports.run_report.create_run_report", Mock(return_value={}))
    case = BY_KEY["pd-visibility"]
    state = execute(case, config, default_options(case), app_factory=lambda _: app)
    assert state["status"] == ("completed" if failure is None else "interrupted" if isinstance(failure, KeyboardInterrupt) else "failed")
    assert app.rp.connect.call_count == app.rp.disconnect.call_count == int(mode == "active")
    assert app.rp.connect_scope_only.call_count == app.rp.disconnect_scope_only.call_count == int(mode == "passive" and source != "pax")
    assert app.pax.connect.call_count == app.pax.disconnect.call_count == int(source != "pd")
    if mode == "passive" and source == "pax":
        assert app.rp.mock_calls == []


@pytest.mark.parametrize("axis", ["phi1", "phi2"])
@pytest.mark.parametrize("waveform,hardware", [("sin", "sin"), ("cos", "cos"), ("triangle", "ramp"), ("sawtooth", "halframp"), ("square", "square")])
def test_active_waveform_drives_selected_output_and_holds_other_zero(axis, waveform, hardware):
    rp = RPController(PolarizationLockConfig())
    rp.p = Mock()
    for name in ("asg1", "asg2"):
        module = SimpleNamespace()
        module.setup = Mock(side_effect=lambda _module=module, **kwargs: vars(_module).update(kwargs))
        setattr(rp, name, module)
    result = rp.set_phase_waveform(axis=axis, waveform=waveform, offset=.4, amplitude=.3, frequency_hz=.5)
    selected, other = (rp.asg1, rp.asg2) if axis == "phi1" else (rp.asg2, rp.asg1)
    assert selected.waveform == hardware and selected.frequency == .5
    assert selected.offset == .4 and selected.amplitude == .3
    assert selected.periodic is True and selected.cycles_per_burst == 0
    assert "periodic" not in selected.setup.call_args.kwargs
    assert other.waveform == "dc" and other.offset == other.amplitude == 0
    assert result["output"] == ("out1" if axis == "phi1" else "out2")
    with pytest.raises(ValueError):
        rp.set_phase_waveform(axis=axis, waveform=waveform, offset=.4, amplitude=.8, frequency_hz=.5)
    assert selected.setup.call_count == 1


@pytest.mark.parametrize("failure", [None, KeyboardInterrupt(), RuntimeError("PAX stalled")])
def test_both_collect_concurrently_with_drive_readback_and_cleanup(tmp_path, monkeypatch, failure):
    from polarization_locking.routines import visibility
    from polarization_locking.hardware.pax_interface import PAXReading
    from polarization_locking.tests.test_stokes_phase_sweep import Clock
    from polarization_locking.reports.run_report import create_run_report
    clock = Clock()
    monkeypatch.setattr(visibility, "time", clock)
    monkeypatch.setattr(visibility, "_scope_sleep", clock.sleep)
    actual_frequency = .46566128730773926
    t, _, dt = signal()
    v = .3 + .15 * np.cos(2 * np.pi * actual_frequency * t)
    initial = dict(input1="in1", duration=4.294967296, average=False, trigger_source="ext_positive_edge",
                   trigger_delay=.1, ch1_active=False, ch2_active=True, rolling_mode=True, trace_average=4)
    scope = SimpleNamespace(**initial, _trigger_delay_register=88, sampling_time=dt, times=t, decimation=32768)
    scope.setup = lambda **kw: vars(scope).update(kw)
    scope.stop = Mock()
    futures = []

    class Capture:
        def __init__(self):
            self.end = clock.now + scope.duration
            self.cancelled = False

        def done(self):
            return clock.now >= self.end or self.cancelled

        def result(self):
            assert self.done() and not self.cancelled
            return np.array([v, v])

        def cancel(self):
            self.cancelled = True

    def single_async():
        future = Capture()
        futures.append(future)
        return future

    scope.single_async = single_async
    rp, pax = Mock(), Mock()
    rp.p.rp.scope = scope
    rp.set_phase_waveform.return_value = {"axis": "phi2", "output": "out2", "waveform": "triangle",
                                         "frequency_hz": actual_frequency, "amplitude_v": .36, "offset_v": .36}
    count = 0

    def read():
        nonlocal count
        count += 1
        clock.sleep(.1)
        if count == 1:
            rp.set_phase_waveform.assert_not_called()  # readiness precedes drive
        if failure and count == 55:
            raise failure
        return PAXReading(timestamp=count, revisions=count*10, theta=0, eta=0, s1=1, s2=0, s3=0, dop=.9,
                          ptotal=.001 + .0005 * np.cos(2 * np.pi * actual_frequency * clock.now),
                          adc_min=4000, adc_max=45000, rev_time=1/60)

    # Check concurrency at the beginning of each read, before the clock advances.
    original_read = read
    def concurrent_read():
        if count >= 1:
            assert futures and not futures[-1].done()
        return original_read()
    pax.read_fresh_polarization.side_effect = concurrent_read
    config = PolarizationLockConfig(visibility_source="both", visibility_mode="active",
                                    visibility_axis="phi2", visibility_waveform="triangle", visibility_dark_voltage_v=0.0)
    if failure:
        with pytest.raises(type(failure)):
            visibility.acquire_contrast(rp, pax, config, str(tmp_path / "data.csv"), 9)
    else:
        visibility.acquire_contrast(rp, pax, config, str(tmp_path / "data.csv"), 9)
    rp.set_output_zero.assert_called_once()
    rp.set_phase_waveform.assert_called_once_with(axis="phi2", waveform="triangle", offset=.36, amplitude=.36, frequency_hz=.5)
    assert all(getattr(scope, key) == value for key, value in initial.items())
    assert scope._trigger_delay_register == 88
    assert futures[-1].cancelled == bool(failure)
    rows = list(csv.DictReader((tmp_path / "data.csv").open()))
    pax_rows = list(csv.DictReader((tmp_path / "pax.csv").open()))
    summary = json.loads((tmp_path / "visibility.json").read_text())
    assert summary["source"] == "both" and summary["mode"] == "active"
    assert summary["visibility_mean"] == pytest.approx(.5, abs=.001)
    assert summary["pax"]["visibility"] == pytest.approx(.5, abs=.01)
    assert len(rows) == (1 if failure else 3)
    for row in rows:
        group = [r for r in pax_rows if r["capture"] == row["capture"]]
        assert len(group) == int(row["pax_sample_count"])
        assert all(float(row["capture_started_s"]) <= float(r["elapsed_s"]) <= float(row["capture_finished_s"]) for r in group)
    drive = json.loads((tmp_path / "drive.json").read_text())
    assert drive["requested_frequency_hz"] == .5 and drive["frequency_hz"] == actual_frequency
    paths = SimpleNamespace(directory=tmp_path, csv=tmp_path / "data.csv", pdf=tmp_path / "report.pdf")
    result = create_run_report(BY_KEY["pd-visibility"], paths, config, default_options(BY_KEY["pd-visibility"]), {"status": "completed" if failure is None else "interrupted"})
    assert result["report_status"] == "completed"


def test_menu_asks_two_choices_then_edits_actuator_in_table():
    from polarization_locking.tests.test_suite import menu_for
    run = Mock(return_value={"status": "completed"})
    menu, output = menu_for(["pd-visibility", "both", "active", "e", "visibility_axis", "phi2",
                             "visibility_waveform", "triangle", "b", "r", "b", "q"], run_test=run)
    menu.run()
    config = run.call_args.args[1]
    assert (config.visibility_source, config.visibility_mode, config.visibility_axis, config.visibility_waveform) == ("both", "active", "phi2", "triangle")
    assert not any("visibility_source =" in line or "visibility_mode =" in line for line in output)
    assert any("Detector: BOTH | Mode: ACTIVE" in line for line in output)


def test_active_contrast_validation_and_cli(capsys):
    from polarization_locking.cli import main
    assert main(["--run", "pd-visibility", "--source", "both", "--mode", "active", "--dry-run"]) == 0
    config = json.loads(capsys.readouterr().out)["config"]
    assert config["visibility_source"] == "both" and config["visibility_mode"] == "active"
    case = BY_KEY["pd-visibility"]
    for changes in ({"visibility_axis": "out3"}, {"visibility_waveform": "unknown"},
                    {"visibility_amplitude_v": -.1}, {"visibility_offset_v": .9},
                    {"visibility_mode": "unknown"}, {"phase_output_map_confirmed": False}):
        with pytest.raises(ValueError):
            validate(PolarizationLockConfig(**({"visibility_mode": "active"} | changes)), case, default_options(case))
