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
    result, _ = analyze_trace(trace, dt, .5)
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
        assert analyze_trace(trace, dt, .5)[0]["visibility"] is None


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
    config = PolarizationLockConfig(pd_input="in2")
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
