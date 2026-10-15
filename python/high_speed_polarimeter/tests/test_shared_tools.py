"""Shared launch routing without connecting instruments."""
import json

import pytest

from high_speed_polarimeter.__main__ import main
from high_speed_polarimeter.config import DEFAULT_RESULTS
from polarization_locking.catalog import BY_KEY, default_options
from polarization_locking.config import PolarizationLockConfig
from polarization_locking.settings import recipe


def test_dry_run_overrides_old_recipe_root(tmp_path, capsys):
    profile = tmp_path / "old.json"
    case = BY_KEY["pax-live"]
    profile.write_text(json.dumps(recipe(case, PolarizationLockConfig(results_directory="/old/locking"),
                                         default_options(case))))
    assert main(["--dry-run", "--tool", "pax-live", "--profile", str(profile)]) == 0
    saved = json.loads(capsys.readouterr().out)
    assert saved["config"]["results_directory"] == DEFAULT_RESULTS
    assert list(tmp_path.iterdir()) == [profile]


@pytest.mark.parametrize("status,code", [("completed", 0), ("discarded", 0), ("cleanup_failed", 1)])
def test_run_delegates_existing_runner(tmp_path, monkeypatch, status, code):
    import polarization_locking.runner
    calls = []
    def execute(case, config, options):
        calls.append((case, config, options))
        return {"status": status}
    monkeypatch.setattr(polarization_locking.runner, "execute", execute)
    assert main(["--run", "--tool", "pax-live", "--results-directory", str(tmp_path)]) == code
    case, config, options = calls[0]
    assert case.key == "pax-live" and case.pax_only
    assert config.results_directory == str(tmp_path)
    assert not list(tmp_path.iterdir())


def test_eom_recipe_rejected_before_run(monkeypatch):
    with pytest.raises(SystemExit) as error:
        main(["--dry-run", "--tool", "pax-live", "--profile",
              "python/high_speed_polarimeter/profiles/single-eom-example.json"])
    assert error.value.code == 2
