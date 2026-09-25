"""Offline interaction and session lifecycle checks. No instrument connections."""
from dataclasses import asdict
import inspect
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest

from polarization_locking.app import PolarizationLockApp
from polarization_locking.catalog import TESTS, BY_KEY, default_options
from polarization_locking.cli import DiagnosticsMenu
from polarization_locking.config import PolarizationLockConfig
from polarization_locking.hardware.rp_interface import RPController
from polarization_locking.runner import execute
from polarization_locking.settings import load_recipe, parse_value, recipe, validate, write_json


def menu_for(answers, **kwargs):
    answers = iter(answers)
    output = []

    def answer(prompt):
        value = next(answers)
        if isinstance(value, BaseException):
            raise value
        return value

    return DiagnosticsMenu(input_fn=answer, output=output.append, **kwargs), output


def test_browse_every_test_without_running_hardware():
    answers = []
    for index in range(1, len(TESTS) + 1):
        answers += [str(index), "b"]
    answers += ["garbage", "0", "999", "q"]
    run = Mock()
    menu, output = menu_for(answers, run_test=run)
    menu.run()
    run.assert_not_called()
    assert sum("Required setup:" in line for line in output) == len(TESTS)


def test_edit_keep_cancel_invalid_input_and_revisit():
    menu, output = menu_for(["2", "e", "sweep_points", "bad", "sweep_points", "21", "sweep_stop_rp_v", "", "sweep_start_rp_v", "b", "b", "b", "2", "b", "q"])
    menu.run()
    config, _ = menu.settings(BY_KEY["sweep"])
    assert config.sweep_points == 21
    assert config.sweep_stop_rp_v == 0.1
    assert any("Invalid value" in line for line in output)
    assert menu.settings(BY_KEY["live"])[0].sweep_points == 11


def test_defaults_and_run_without_parameter_questions():
    run = Mock(return_value={"status": "completed"})
    menu, _ = menu_for(["2", "e", "sweep_points", "21", "b", "d", "r", "", "", "b", "q"], run_test=run)
    menu.run()
    assert run.call_args.args[1].sweep_points == 11


@pytest.mark.parametrize("answers", [[EOFError()], [KeyboardInterrupt()], ["2", KeyboardInterrupt(), "q"], ["2", "e", KeyboardInterrupt(), "b", "q"]])
def test_menu_interrupts_are_navigation(answers):
    menu, _ = menu_for(answers)
    menu.run()


def test_invalid_recipe_returns_to_menu(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text('{"schema_version": 1, "test": "sweep", "config": []}')
    menu, output = menu_for(["l", str(path), "q"])
    menu.run()
    assert any("Cannot load recipe" in line for line in output)


@pytest.mark.parametrize("case", TESTS, ids=lambda case: case.key)
def test_recipe_round_trip_for_every_case(case, tmp_path):
    config = PolarizationLockConfig()
    options = default_options(case)
    validate(config, case, options)
    path = tmp_path / "recipe.json"
    write_json(path, recipe(case, config, options))
    loaded_case, loaded_config, loaded_options = load_recipe(path)
    assert (loaded_case, asdict(loaded_config), loaded_options) == (case, asdict(config), options)


@pytest.mark.parametrize("field,value", [("rp_output_max_voltage", 13.0), ("rp_output_min_voltage", -1.0), ("sweep_stop_rp_v", 1.1), ("sweep_points", 1), ("phi1_v_lambda", 0.0), ("sweep_settle_s", -1), ("pax_port", 70000), ("sweep_stop_rp_v", float("nan"))])
def test_invalid_settings_rejected_before_app_creation(field, value):
    case = BY_KEY["sweep"]
    config = PolarizationLockConfig()
    setattr(config, field, value)
    factory = Mock()
    with pytest.raises(ValueError):
        execute(case, config, default_options(case), app_factory=factory)
    factory.assert_not_called()


@pytest.mark.parametrize("text,kind", [("true", int), ("1.5", int), ('"false"', bool), ("NaN", float), ("[1]", tuple[float, float])])
def test_strict_input_types(text, kind):
    with pytest.raises(ValueError):
        parse_value(text, kind)


def fake_app(config, monkeypatch, failure=None):
    monkeypatch.setattr("polarization_locking.reports.run_report.create_run_report", Mock(return_value={"report_file": "report.pdf", "report_status": "completed"}))
    app = PolarizationLockApp(config)
    app.connect = Mock()
    app.disconnect = Mock()
    app.capture_target = Mock(side_effect=lambda: app.set_target(0.3, 1.2))
    for case in TESTS:
        original = getattr(app, case.method)
        signature = inspect.signature(original)

        def run(*args, _signature=signature, **kwargs):
            _signature.bind(*args, **kwargs)
            if failure:
                raise failure
            if "output_file" in kwargs:
                Path(kwargs["output_file"]).write_text("fake data\n")
        monkeypatch.setattr(app, case.method, run)
    return app


@pytest.mark.parametrize("case", TESTS, ids=lambda case: case.key)
def test_all_catalog_entries_dispatch_and_persist(case, tmp_path, monkeypatch):
    config = PolarizationLockConfig(results_directory=str(tmp_path))
    app = fake_app(config, monkeypatch)
    options = default_options(case) | {"comment": "Displaced PAX; heavier base"}
    state = execute(case, config, options, app_factory=lambda _: app)
    assert state["status"] == "completed", state
    if case.pax_only:
        app.connect.assert_not_called()  # real panel connects inside its worker
    else:
        app.connect.assert_called_once()
    app.disconnect.assert_called_once()
    path = next(tmp_path.rglob("recipe.json"))
    assert load_recipe(path)[0] == case
    recorded = json.loads(path.with_name("run.json").read_text())
    assert recorded["status"] == "completed"
    assert "provenance" in recorded and "packages" in recorded["provenance"]
    assert recorded["run_comment"] == options["comment"]
    assert load_recipe(path)[2]["comment"] == options["comment"]
    assert options["comment"] in path.with_name("console.log").read_text()
    from polarization_locking.reports.run_report import create_run_report
    if case.key == "pax-live":
        create_run_report.assert_not_called()  # mock live session has not selected Save
    else:
        assert create_run_report.call_args.args[3]["comment"] == options["comment"]
        assert create_run_report.call_args.args[4]["run_comment"] == options["comment"]


@pytest.mark.parametrize("failure,status", [(KeyboardInterrupt(), "interrupted"), (EOFError(), "interrupted"), (RuntimeError("read failed"), "failed")])
def test_failed_and_interrupted_runs_cleanup_and_keep_recipe(tmp_path, monkeypatch, failure, status):
    config = PolarizationLockConfig(results_directory=str(tmp_path))
    app = fake_app(config, monkeypatch, failure)
    state = execute(BY_KEY["sweep"], config, default_options(BY_KEY["sweep"]), app_factory=lambda _: app)
    assert state["status"] == status
    app.disconnect.assert_called_once()
    assert list(tmp_path.rglob("recipe.json"))


def test_partial_connection_failure_cleans_up(tmp_path, monkeypatch):
    config = PolarizationLockConfig(results_directory=str(tmp_path))
    app = fake_app(config, monkeypatch)
    app.connect.side_effect = RuntimeError("PAX unavailable after RP connected")
    state = execute(BY_KEY["sweep"], config, default_options(BY_KEY["sweep"]), app_factory=lambda _: app)
    assert state["status"] == "failed"
    app.disconnect.assert_called_once()


def test_report_failure_preserves_completed_data(tmp_path, monkeypatch):
    config = PolarizationLockConfig(results_directory=str(tmp_path))
    app = fake_app(config, monkeypatch)
    monkeypatch.setattr("polarization_locking.reports.run_report.create_run_report", Mock(side_effect=RuntimeError("plot unavailable")))
    options = default_options(BY_KEY["sweep"]) | {"report": "pdf"}
    state = execute(BY_KEY["sweep"], config, options, app_factory=lambda _: app)
    assert state["status"] == "completed"
    assert "plot unavailable" in state["report_error"]
    assert list(tmp_path.rglob("data.csv"))


def test_rp_disconnect_without_connection_and_hard_output_limit():
    rp = RPController(PolarizationLockConfig())
    rp.disconnect()
    rp.config.rp_output_max_voltage = 13.0
    with pytest.raises(ValueError, match="0-1 V"):
        rp._validate_output_voltage(2, 0)


def test_actual_sweep_uses_recipe_points_and_returns_zero(tmp_path, monkeypatch):
    config = PolarizationLockConfig(results_directory=str(tmp_path), sweep_start_rp_v=0.2, sweep_stop_rp_v=0.4, sweep_points=3, sweep_repeats=2, sweep_settle_s=0.7)
    app = PolarizationLockApp(config)
    app.rp = Mock()
    app.pax = Mock()
    collector = Mock()
    monkeypatch.setattr("polarization_locking.routines.calibration.CalibrationSweep", lambda *a, **kw: collector)
    app._run_calibration_sweep("phi2", str(tmp_path / "data.csv"))
    kw = collector.run_axis_sweep.call_args.kwargs
    assert kw["rp_values"] == pytest.approx([0.2, 0.3, 0.4])
    assert kw["settle_s"] == 0.7 and kw["repeats"] == 2 and kw["axis"] == "phi2"
    collector.disconnect.assert_called_once()


def test_browse_cli_loads_no_hardware_modules():
    package_parent = Path(__file__).resolve().parents[2]
    script = "from polarization_locking.cli import main; main(['--list']); import sys; assert 'numpy' not in sys.modules; assert 'pyrpl' not in sys.modules; assert 'yaqc' not in sys.modules"
    result = subprocess.run([sys.executable, "-c", script], cwd=package_parent, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_interrupted_real_collector_saves_partial_csv(tmp_path, monkeypatch):
    import csv
    from polarization_locking.routines.calibration import CalibrationSweep
    from polarization_locking.hardware.pax_interface import PAXReading
    reading = PAXReading(timestamp=1, theta=0, eta=0, s1=1, s2=0, s3=0, dop=1)
    rp, pax = Mock(), Mock()
    pax.read_polarization.side_effect = [reading, KeyboardInterrupt()]
    collector = CalibrationSweep(PolarizationLockConfig(), rp=rp, pax=pax)
    monkeypatch.setattr("polarization_locking.routines.calibration.time.sleep", lambda _: None)
    output = tmp_path / "partial.csv"
    with pytest.raises(KeyboardInterrupt):
        collector.run_axis_sweep("phi1", [0, 0.1], repeats=1, output_file=str(output))
    with output.open() as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert float(rows[0]["rp_out1_v"]) == 0.0


def test_cleanup_failure_closes_menu():
    run = Mock(return_value={"status": "cleanup_failed"})
    menu, output = menu_for(["2", "r", "", ""], run_test=run)
    menu.run()
    assert any("Menu closed" in line for line in output)


def test_manual_setup_back_aborts(monkeypatch):
    from polarization_locking.routines.prompts import prepare_setup
    monkeypatch.setattr("builtins.input", lambda _: "b")
    with pytest.raises(KeyboardInterrupt):
        prepare_setup("Ready? ")


def test_direct_launch_is_hardware_free():
    path = Path(__file__).resolve().parents[1] / "lock.py"
    script = "import runpy, sys; sys.argv = ['lock.py', '--list'];\ntry: runpy.run_path(%r, run_name='__main__')\nexcept SystemExit as e: assert e.code == 0\nassert 'numpy' not in sys.modules\nassert 'yaqc' not in sys.modules" % str(path)
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_saved_menu_recipe_can_be_loaded_and_run(tmp_path):
    path = str(tmp_path / "custom.json")
    run = Mock(return_value={"status": "completed"})
    menu, _ = menu_for(["2", "e", "sweep_points", "17", "b", "s", path, "d", "l", path, "r", "", "", "b", "q"], run_test=run)
    menu.run()
    assert run.call_args.args[1].sweep_points == 17


def test_dry_run_does_not_load_hardware(tmp_path):
    path = tmp_path / "recipe.json"
    case = BY_KEY["sweep"]
    write_json(path, recipe(case, PolarizationLockConfig(), default_options(case)))
    script = "import sys; from polarization_locking.cli import main; main(['--profile', %r, '--run', 'sweep', '--dry-run']); assert 'pyrpl' not in sys.modules; assert 'numpy' not in sys.modules" % str(path)
    result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["test"] == "sweep"


def test_full_period_scan_rejected_if_terminal_gain_requires_more_than_one_rp_volt():
    config = PolarizationLockConfig(phi1_v_lambda=26.0)
    case = BY_KEY["phi1-step-map"]
    with pytest.raises(ValueError, match="above the configured limit"):
        validate(config, case, default_options(case))
    # A bounded raw voltage sweep is still usable to measure the unknown response.
    validate(config, BY_KEY["sweep"], default_options(BY_KEY["sweep"]))


def test_partial_connection_disconnect_still_closes_pax_when_rp_cleanup_fails():
    app = PolarizationLockApp()
    app.rp.disconnect = Mock(side_effect=RuntimeError("RP failed"))
    app.pax.disconnect = Mock()
    with pytest.raises(RuntimeError, match="RP failed"):
        app.disconnect()
    app.pax.disconnect.assert_called_once()


def test_inline_report_failure_preserves_acquisition(tmp_path, monkeypatch):
    config = PolarizationLockConfig(results_directory=str(tmp_path))
    app = fake_app(config, monkeypatch)
    monkeypatch.setattr("polarization_locking.reports.run_report.create_run_report", Mock(side_effect=RuntimeError("analyzer unavailable")))
    case = BY_KEY["phi1-step-map"]
    state = execute(case, config, default_options(case) | {"report": "both"}, app_factory=lambda _: app)
    assert state["status"] == "completed"
    assert "analyzer unavailable" in state["report_error"]


@pytest.mark.parametrize("name,comment", [("", ""), ("heavy base", "PAX displaced; same IN2")])
def test_quick_run_details_are_optional_and_per_run(name, comment):
    run = Mock(return_value={"status": "completed"})
    menu, _ = menu_for(["2", "r", name, comment, "r", "", "", "b", "q"], run_test=run)
    menu.run()
    first, second = [call.args[2] for call in run.call_args_list]
    assert first["label"] == (name or "sweep")
    assert first["comment"] == comment
    assert second["label"] == "sweep" and second["comment"] == ""


def test_cancel_run_details_never_connects():
    run = Mock()
    menu, _ = menu_for(["2", "r", KeyboardInterrupt(), "q"], run_test=run)
    menu.run()
    run.assert_not_called()


@pytest.mark.parametrize("name,expected", [("", "sweep"), ("sweep", "sweep"), ("heavy base", "sweep_heavy-base")])
def test_folder_name_keeps_date_without_duplicate_default(tmp_path, monkeypatch, name, expected):
    from datetime import datetime
    from polarization_locking import app as module
    clock = Mock()
    clock.now.return_value = datetime(2026, 9, 25, 12, 34, 56)
    monkeypatch.setattr(module, "datetime", clock)
    app = PolarizationLockApp(PolarizationLockConfig(results_directory=str(tmp_path)))
    first = app._new_experiment_paths("sweep", name)
    second = app._new_experiment_paths("sweep", name)
    assert first.directory == tmp_path / "2026-09-25" / f"123456_{expected}"
    assert second.directory.name == f"123456_{expected}_2"


def test_comment_recipe_and_cli_roundtrip(tmp_path, capsys):
    from polarization_locking.cli import main
    assert main(["--run", "sweep", "--dry-run", "--name", "heavy base", "--comment", "PAX moved"])==0
    data=json.loads(capsys.readouterr().out)
    path=tmp_path/"recipe.json";path.write_text(json.dumps(data))
    _,_,options=load_recipe(path)
    assert options["label"]=="heavy base" and options["comment"]=="PAX moved"
