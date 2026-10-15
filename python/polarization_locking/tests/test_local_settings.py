"""Parameters survive a fresh menu instance."""
import pytest
from polarization_locking.catalog import BY_KEY
from polarization_locking.cli import DiagnosticsMenu


def test_edits_restore_and_defaults_persist(tmp_path):
    path = tmp_path / 'settings.json'
    answers = iter(['sweep_points', '23', 'b'])
    menu = DiagnosticsMenu(state_path=path, input_fn=lambda _: next(answers), output=lambda _: None)
    case = BY_KEY['sweep']
    menu.edit(case, *menu.settings(case))
    restored = DiagnosticsMenu(state_path=path)
    assert restored.settings(case)[0].sweep_points == 23
    assert restored.settings(BY_KEY['live'])[0].sweep_points == 11
    answers = iter(['d', 'b'])
    restored.input = lambda _: next(answers)
    restored.print = lambda _: None
    restored.detail(case)
    assert DiagnosticsMenu(state_path=path).settings(case)[0].sweep_points == 11


def test_invalid_draft_retained_but_validation_still_applies(tmp_path):
    from polarization_locking.settings import validate
    answers = iter(['sweep_points', '0', 'b'])
    menu = DiagnosticsMenu(state_path=tmp_path/'state.json', input_fn=lambda _: next(answers), output=lambda _: None)
    case = BY_KEY['sweep']
    menu.edit(case, *menu.settings(case))
    restored = DiagnosticsMenu(state_path=tmp_path/'state.json')
    config, options = restored.settings(case)
    assert config.sweep_points == 0
    with pytest.raises(ValueError):
        validate(config, case, options)


def test_broken_state_is_not_silently_overwritten(tmp_path):
    path = tmp_path/'settings.json'
    path.write_text('broken')
    with pytest.raises(ValueError, match='Cannot restore local settings'):
        DiagnosticsMenu(state_path=path)
    assert path.read_text() == 'broken'
