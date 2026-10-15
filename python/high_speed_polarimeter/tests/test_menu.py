"""Interactive bench workflow with injected inputs and no instruments."""
import json
from pathlib import Path
import subprocess
import sys

from high_speed_polarimeter.catalog import BY_KEY
from high_speed_polarimeter.cli import HighSpeedMenu
from high_speed_polarimeter.config import DEFAULT_RESULTS


def menu_for(answers, run_test=None):
    answers = iter(answers)
    output = []
    return HighSpeedMenu(input_fn=lambda _: next(answers), output=output.append,
                         run_test=run_test), output


def test_direct_launcher_browses_without_drivers():
    path = Path(__file__).resolve().parents[1] / "test-suite.py"
    result = subprocess.run([sys.executable, str(path)], input="2\nb\n4\nb\nq\n",
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "single_eom_characterization" in result.stdout
    assert "pax-live" in result.stdout
    assert "r run | e edit" in result.stdout
    assert "Cannot continue" not in result.stdout


def test_edit_save_load_and_run_prompts(tmp_path):
    path = tmp_path / "recipe.json"
    calls = []
    def execute(recipe, mode):
        calls.append((recipe, mode))
        return dict(directory=str(tmp_path), status="COMPLETED", cleanup_status="COMPLETED", report_status="COMPLETED")
    menu, output = menu_for(["2", "e", "points", "7", "b", "s", str(path),
                            "d", "l", str(path), "r", "my-run", "my comment", "b", "q"], execute)
    menu.run()
    assert json.loads(path.read_text())["config"]["points"] == 7
    recipe, mode = calls[0]
    assert mode == "hardware" and recipe.config["points"] == 7
    assert recipe.label == "my-run" and recipe.comment == "my comment"
    assert output.count("\nAvailable bench tests:") == 2


def test_invalid_edit_is_rejected_before_dispatch():
    calls = []
    menu, output = menu_for(["2", "e", "points", "0", "b", "r", "b", "q"],
                            lambda *args, **kwargs: calls.append(args))
    menu.run()
    assert not calls
    assert any("Cannot continue" in line for line in output)


def test_loaded_pax_recipe_is_routed_to_high_speed(tmp_path):
    from polarization_locking.catalog import BY_KEY as LOCKING, default_options
    from polarization_locking.config import PolarizationLockConfig
    from polarization_locking.settings import recipe
    case = LOCKING["pax-live"]
    path = tmp_path / "pax.json"
    path.write_text(json.dumps(recipe(case, PolarizationLockConfig(results_directory="old-root"), default_options(case))))
    menu, _ = menu_for([str(path)])
    assert menu.load() == "pax-live"
    assert menu.pax.settings(case)[0].results_directory == DEFAULT_RESULTS


def test_mock_entry_uses_mock_mode():
    calls = []
    def execute(recipe, mode):
        calls.append(mode)
        return dict(directory="test", status="COMPLETED", cleanup_status="COMPLETED", report_status="COMPLETED")
    menu, _ = menu_for(["1", "r", "", "", "b", "q"], execute)
    menu.run()
    assert calls == ["mock"]


def test_cleanup_failure_closes_menu():
    menu, output = menu_for(["2", "r", "", ""], lambda *a, **k: dict(
        directory="test", status="COMPLETED", cleanup_status="FAILED", report_status="COMPLETED"))
    menu.run()
    assert "Menu closed." in output


def test_settings_and_pax_survive_restart(tmp_path):
    from polarization_locking.catalog import BY_KEY as LOCKING
    path = tmp_path / 'settings.json'
    answers = iter(['points', '19', 'b'])
    menu = HighSpeedMenu(state_path=path, input_fn=lambda _: next(answers), output=lambda _: None)
    menu.edit(BY_KEY['single_eom_characterization'])
    answers = iter(['pax_wavelength_nm', '780', 'b'])
    menu.pax.input = lambda _: next(answers)
    case = LOCKING['pax-live']
    menu.pax.edit(case, *menu.pax.settings(case))
    restored = HighSpeedMenu(state_path=path)
    assert restored.settings(BY_KEY['single_eom_characterization']).config['points'] == 19
    assert restored.pax.settings(case)[0].pax_wavelength_nm == 780
    assert restored.pax.settings(case)[0].results_directory == DEFAULT_RESULTS
    answers = iter(['d', 'b'])
    restored.input = lambda _: next(answers)
    restored.print = lambda _: None
    restored.detail(BY_KEY['single_eom_characterization'])
    assert HighSpeedMenu(state_path=path).settings(BY_KEY['single_eom_characterization']).config['points'] == 11
