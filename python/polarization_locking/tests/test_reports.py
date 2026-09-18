"""Report policy, historical recipe migration, and real PDF generation offline."""
from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from polarization_locking.catalog import TESTS, BY_KEY, default_options
from polarization_locking.config import PolarizationLockConfig
from polarization_locking.reports.run_report import create_run_report
from polarization_locking.runner import execute
from polarization_locking.settings import load_recipe, validate


def test_every_test_requires_a_pdf():
    for case in TESTS:
        options = default_options(case)
        assert options["report"] == "pdf"
        validate(PolarizationLockConfig(), case, options)
        with pytest.raises(ValueError, match="report"):
            validate(PolarizationLockConfig(), case, options | {"report": "none"})


def test_experiments_default_is_independent_of_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    expected = Path(__file__).resolve().parents[3] / "experiments/polarization_locking"
    assert Path(PolarizationLockConfig().results_directory) == expected


def test_old_recipe_migrates_output_defaults_without_rewriting_record(tmp_path):
    original = {"schema_version": 1, "test": "sweep", "options": {"report": "none"}, "config": {"results_directory": str(Path(__file__).resolve().parents[1] / "results")}}
    path = tmp_path / "recipe.json"
    path.write_text(json.dumps(original))
    _, config, options = load_recipe(path)
    assert config.results_directory == PolarizationLockConfig().results_directory
    assert options["report"] == "pdf"
    assert json.loads(path.read_text()) == original


@pytest.mark.parametrize("key", ["live", "field-model-calibration", "phi1-fringe-map", "phi1-pd-gain-scan", "rough"])
def test_previously_unreported_tests_write_real_pdfs(key, tmp_path):
    case = BY_KEY[key]
    paths = SimpleNamespace(directory=tmp_path, csv=tmp_path / "data.csv", pdf=tmp_path / "report.pdf")
    if key != "rough":
        paths.csv.write_text("s1,s2,s3,dop,u,v,pd_mean_v\n0,0,1,.9,1.57,1.57,.1\n0,.1,.99,.92,1.47,1.57,.2\n")
    else:
        (tmp_path / "console.log").write_text("current=(u=0.1, v=1.2)\nremaining phase error=(d_phi1=0.01, d_phi2=0.02)\n")
    state = create_run_report(case, paths, PolarizationLockConfig(), default_options(case), {"status": "completed"})
    assert state["report_status"] == "completed"
    assert paths.pdf.read_bytes().startswith(b"%PDF-")
    assert paths.pdf.stat().st_size > 10000


def test_incomplete_specialized_scan_still_has_a_report(tmp_path):
    case = BY_KEY["phi1-step-map"]
    paths = SimpleNamespace(directory=tmp_path, csv=tmp_path / "data.csv", pdf=tmp_path / "report.pdf")
    paths.csv.write_text("s1,s2,s3,dop\n0,0,1,.9\n")
    state = create_run_report(case, paths, PolarizationLockConfig(), default_options(case), {"status": "interrupted"})
    assert state["report_status"] == "partial"
    assert "report_error" in state
    assert paths.pdf.read_bytes().startswith(b"%PDF-")


def test_reporting_happens_after_cleanup_on_interrupt(tmp_path, monkeypatch):
    from polarization_locking.app import PolarizationLockApp
    config = PolarizationLockConfig(results_directory=str(tmp_path))
    app = PolarizationLockApp(config)
    events = []
    app.connect = Mock()
    app.disconnect = lambda: events.append("disconnect")
    app._run_live_monitor = Mock(side_effect=KeyboardInterrupt())
    def report(*args):
        events.append("report")
        assert args[-1]["status"] == "interrupted"
        return {"report_status": "completed"}
    monkeypatch.setattr("polarization_locking.reports.run_report.create_run_report", report)
    state = execute(BY_KEY["live"], config, default_options(BY_KEY["live"]), app_factory=lambda _: app)
    assert events == ["disconnect", "report"]
    assert state["report_status"] == "completed"
