"""Infrastructure checks; no optical thresholds or drivers."""
from dataclasses import replace
import itertools
import json
from pathlib import Path
import subprocess
import sys

import pytest

import high_speed_polarimeter
from high_speed_polarimeter.catalog import BY_KEY, PLANNED
from high_speed_polarimeter.config import Recipe, load_recipe
from high_speed_polarimeter.io import read_json, write_json
from high_speed_polarimeter.provenance import runtime_provenance
from high_speed_polarimeter.quality import QualityCheck, QualityStatus, RunQuality, reevaluate
from high_speed_polarimeter.runner import execute


def recipe_for(tmp_path):
    return Recipe(config={"results_directory": str(tmp_path)})


def test_import_catalog_recipe(tmp_path):
    assert high_speed_polarimeter.__version__
    assert set(BY_KEY) == {"mock_lifecycle", "single_eom_characterization", "dual_eom_characterization"}
    assert not set(BY_KEY).intersection(PLANNED)
    profile = Path(__file__).resolve().parents[1] / "profiles/mock-example.json"
    assert load_recipe(profile).experiment == "mock_lifecycle"
    path = tmp_path / "recipe.json"
    recipe = recipe_for(tmp_path)
    write_json(path, recipe.to_dict())
    assert load_recipe(path) == recipe


@pytest.mark.parametrize("change", [
    {"schema_version": True}, {"schema_version": 2}, {"label": "../escape"},
    {"experiment": "instrument_matrix_calibration"}, {"comment": 3},
    {"config": {"results_directory": ""}}, {"config": {"unknown": 1}},
])
def test_invalid_recipe_no_session_or_artifacts(tmp_path, change):
    called = []
    with pytest.raises(ValueError):
        execute(replace(recipe_for(tmp_path), **change), session_factory=lambda: called.append(True))
    assert not called
    assert not list(tmp_path.iterdir())


def test_unknown_fields_rejected(tmp_path):
    path = tmp_path / "recipe.json"
    write_json(path, {"schema_version": 1, "typo": 1})
    with pytest.raises(ValueError):
        load_recipe(path)


def test_quality_all_statuses_and_order_independence():
    assert RunQuality().status == QualityStatus.UNKNOWN
    priority = [QualityStatus.FAIL, QualityStatus.WARN, QualityStatus.UNKNOWN, QualityStatus.PASS]
    for count in range(1, 5):
        for statuses in itertools.product(QualityStatus, repeat=count):
            checks = tuple(QualityCheck(str(i), status, "Test-only evidence", measured_value=i,
                                      units="test units", thresholds={"test_only": True},
                                      evidence={"artifact": "test.json"}) for i, status in enumerate(statuses))
            quality = RunQuality(checks, "test_evaluator", "1")
            assert quality.status == next(status for status in priority if status in statuses)
            assert RunQuality(tuple(reversed(checks))).status == quality.status
            assert json.loads(json.dumps(quality.to_dict(), allow_nan=False))["status"] == quality.status.value
    with pytest.raises(ValueError):
        QualityCheck("invalid", "INVALID", "Test")


def test_mock_lifecycle_and_offline_replay(tmp_path):
    state = execute(recipe_for(tmp_path))
    directory = Path(state["directory"])
    assert state["status"] == "COMPLETED"
    assert state["cleanup_status"] == "COMPLETED"
    assert state["quality_evaluation_status"] == "COMPLETED"
    assert state["report_status"] == "COMPLETED"
    assert {p.name for p in directory.iterdir()} == {
        "recipe.json", "run.json", "quality.json", "mock_events.jsonl", "console.log", "report.md"}
    provenance = state["provenance"]
    assert provenance["sources"][0]["files"]["runner.py"]
    assert provenance["repo_dirty"] in (True, False, None)
    assert "git_commit" in provenance and provenance["python"]
    assert "no physical measurements" in (directory / "console.log").read_text()
    original = {name: (directory / name).read_bytes() for name in ("recipe.json", "run.json", "mock_events.jsonl")}
    first = (directory / "quality.json").read_bytes()
    assert reevaluate(directory).status == QualityStatus.UNKNOWN
    assert (directory / "quality.json").read_bytes() == first
    assert original == {name: (directory / name).read_bytes() for name in original}
    assert "Execution: COMPLETED" in (directory / "report.md").read_text()
    assert "quality: UNKNOWN" in (directory / "report.md").read_text()
    second = execute(recipe_for(tmp_path))
    assert second["directory"] != state["directory"]


@pytest.mark.parametrize("failure,expected", [(RuntimeError("failure"), "FAILED"),
                                               (KeyboardInterrupt(), "INTERRUPTED"),
                                               (EOFError(), "INTERRUPTED")])
def test_partial_data_retained_and_cleanup(tmp_path, failure, expected):
    events = []

    class Session:
        def connect(self):
            events.append("connect")

        def disconnect(self):
            events.append("disconnect")

    def acquire(session, recipe, directory):
        (directory / "partial.txt").write_text("retained evidence")
        raise failure

    state = execute(recipe_for(tmp_path), session_factory=Session, acquire=acquire)
    assert state["status"] == expected
    assert events == ["connect", "disconnect"]
    directory = Path(state["directory"])
    assert (directory / "partial.txt").read_text() == "retained evidence"
    assert read_json(directory / "quality.json")["status"] == "UNKNOWN"
    assert read_json(directory / "run.json")["status"] == expected
    assert read_json(directory / "quality.json")["checks"][0]["evidence"]["mock_events.jsonl"]["missing"]


@pytest.mark.parametrize("stage", ["factory", "connect", "disconnect"])
def test_initialization_cleanup_failures(tmp_path, stage):
    events = []

    class Session:
        def __init__(self):
            if stage == "factory":
                raise RuntimeError("factory failed")

        def connect(self):
            if stage == "connect":
                raise RuntimeError("connect failed")

        def disconnect(self):
            events.append("cleanup attempted")
            if stage == "disconnect":
                raise RuntimeError("cleanup failed")

    state = execute(recipe_for(tmp_path), session_factory=Session)
    assert state["status"] == ("COMPLETED" if stage == "disconnect" else "FAILED")
    assert bool(events) == (stage != "factory")
    assert state["cleanup_status"] == {"factory": "NOT_STARTED", "connect": "COMPLETED", "disconnect": "FAILED"}[stage]
    assert (Path(state["directory"]) / "run.json").exists()


def test_completed_execution_can_have_failed_quality(tmp_path, monkeypatch):
    quality = RunQuality((QualityCheck("test_only", "FAIL", "Test-only failure"),), "test", "1")
    monkeypatch.setattr("high_speed_polarimeter.runner.evaluate_run", lambda directory: quality)
    state = execute(recipe_for(tmp_path))
    assert state["status"] == "COMPLETED"
    assert read_json(Path(state["directory"]) / "quality.json")["status"] == "FAIL"
    assert "quality: FAIL" in (Path(state["directory"]) / "report.md").read_text()


@pytest.mark.parametrize("stage", ["quality", "report"])
def test_postprocessing_failure_keeps_execution_and_events(tmp_path, monkeypatch, stage):
    def fail(directory):
        raise RuntimeError("postprocessing failed")
    monkeypatch.setattr("high_speed_polarimeter.runner." + ("evaluate_run" if stage == "quality" else "create_report"), fail)
    state = execute(recipe_for(tmp_path))
    assert state["status"] == "COMPLETED"
    assert state["quality_evaluation_status" if stage == "quality" else "report_status"] == "FAILED"
    assert (Path(state["directory"]) / "mock_events.jsonl").exists()


def test_explicit_legacy_source_and_dependency_provenance(tmp_path):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    source = legacy / "driver.py"
    source.write_text("version = 1")
    first = runtime_provenance(additional_source_roots=(legacy,), dependencies=("nonexistent-polarimeter-test-dependency",))
    source.write_text("version = 2")
    second = runtime_provenance(additional_source_roots=(legacy,))
    assert first["sources"][1]["sha256"] != second["sources"][1]["sha256"]
    assert first["dependencies"]["nonexistent-polarimeter-test-dependency"] is None


def test_dry_run_hardware_free_and_no_artifacts(tmp_path):
    script = '''import sys
from high_speed_polarimeter.__main__ import main
assert main(['--dry-run', '--results-directory', sys.argv[1]]) == 0
assert not any(name in sys.modules for name in ('numpy', 'yaqc', 'pyrpl', 'polarization_locking'))
'''
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)],
                            cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["experiment"] == "mock_lifecycle"
    assert not list(tmp_path.iterdir())


def test_provenance_failure_retains_run_and_report(tmp_path, monkeypatch):
    def fail(**kwargs):
        raise OSError("source unavailable")
    monkeypatch.setattr("high_speed_polarimeter.runner.runtime_provenance", fail)
    state = execute(recipe_for(tmp_path))
    assert state["status"] == "FAILED"
    assert state["cleanup_status"] == "NOT_STARTED"
    assert state["report_status"] == "COMPLETED"
    assert read_json(Path(state["directory"]) / "run.json")["error"].startswith("OSError")


def test_cli_mock_and_offline_no_driver_imports(tmp_path):
    script = """import sys
from high_speed_polarimeter.__main__ import main
from pathlib import Path
assert main(['--mock-run', '--results-directory', sys.argv[1]]) == 0
runs = list(Path(sys.argv[1]).glob('*/*'))
assert len(runs) == 1
assert main(['--reevaluate', str(runs[0])]) == 0
assert not any(name in sys.modules for name in ('numpy', 'yaqc', 'pyrpl', 'polarization_locking'))
"""
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)],
                            cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
