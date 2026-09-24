"""Known-state geometry, sample selection, and window lifecycle without a PAX."""
import csv
import json
import math
from unittest.mock import Mock
import time

import numpy as np
import pytest

from polarization_locking.app import PolarizationLockApp
from polarization_locking.catalog import BY_KEY, default_options
from polarization_locking.config import PolarizationLockConfig
from polarization_locking.routines import pax_live
from polarization_locking.routines.orthogonalizer import ReferenceCapture, metrics
from polarization_locking.settings import validate
from polarization_locking.tests.test_stokes_phase_sweep import reading


def sample(index, vector, dop=.99, timestamp=None):
    return dict(sample=index, received_monotonic=index * .1 if timestamp is None else timestamp,
                reading=reading(index, s1=vector[0], s2=vector[1], s3=vector[2], dop=dop))


@pytest.mark.parametrize('vector,error,overlap', [((1, 0, 0), 180, 1), ((-1, 0, 0), 0, 0), ((0, 4, 0), 90, 1/math.sqrt(2))])
def test_known_state_geometry_and_power_balance(vector, error, overlap):
    result = metrics({'stokes': [1, 0, 0], 'power': .004}, sample(1, vector)['reading'])
    assert result['error_deg'] == pytest.approx(error)
    assert result['overlap'] == result['v_pol'] == pytest.approx(overlap)
    assert result['balance'] == pytest.approx(.8)
    assert result['v_pred'] == pytest.approx(.8 * overlap)


def test_reference_normalizes_each_direction_before_averaging_and_freezes():
    capture = ReferenceCapture(0, 5, .9, 5)
    for index, vector in enumerate(((10, 0, 0), (0, 4, 0), (1, 0, 0)), 1):
        capture.add(sample(index, vector))
        capture.add(sample(index, vector))  # repainting the same sample cannot reweight it
    capture.add(sample(4, (0, 0, 1), dop=.1))
    capture.add(sample(5, (0, 0, 1), dop=1.01))
    capture.add(sample(6, (0, 0, 1), timestamp=6))
    result = capture.finish()
    assert result['sample_count'] == 3 and result['rejected_count'] == 2
    np.testing.assert_allclose(result['stokes'], [2/np.sqrt(5), 1/np.sqrt(5), 0])
    np.testing.assert_allclose(result['target'], -np.array(result['stokes']))
    assert result['unstable'] and result['mean_dop'] == pytest.approx(.99)
    original = result['stokes'][:]
    capture.add(sample(7, (0, 0, 1), timestamp=1))
    assert result['stokes'] == original


def test_azimuth_wrap_and_rms_spread_are_computed_in_stokes_space():
    capture = ReferenceCapture(0, 5, .9, 5)
    for i, theta in enumerate((89, -89, 89, -89), 1):
        angle = math.radians(2*theta)
        capture.add(sample(i, (math.cos(angle), math.sin(angle), 0)))
    result = capture.finish()
    np.testing.assert_allclose(result['stokes'], [-1, 0, 0], atol=1e-12)
    assert result['rms_spread_deg'] == pytest.approx(2)
    assert not result['unstable']


@pytest.mark.parametrize('problem', ['low_dop', 'zero', 'cancelled_mean', 'cache_gap'])
def test_invalid_reference_cannot_silently_create_target(problem):
    capture = ReferenceCapture(0, 5, .9, 5)
    for i, vector in enumerate(((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0)), 1):
        capture.add(sample(i, (0, 0, 0) if problem == 'zero' else vector, dop=.1 if problem == 'low_dop' else .99))
    capture.gap = problem == 'cache_gap'
    with pytest.raises(ValueError):
        capture.finish()


@pytest.mark.parametrize('name,value', [('pax_live_reference_duration_s', 0), ('pax_live_reference_duration_s', 61), ('pax_live_reference_max_spread_deg', 0)])
def test_reference_config_is_validated_before_hardware(name, value):
    config = PolarizationLockConfig(**{name: value})
    with pytest.raises(ValueError):
        validate(config, BY_KEY['pax-live'], default_options(BY_KEY['pax-live']))


def test_real_window_reference_align_close_reopen_and_single_acquisition(tmp_path, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    from qtpy import QtCore, QtWidgets
    gui = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app = PolarizationLockApp()
    app.rp = Mock()
    app.pax = Mock(last_raw_record={})
    app.pax.client.get_wavelength.return_value = 830
    arm, count, phase, evidence = 'reference', 0, 0, {}

    def fresh():
        nonlocal count
        time.sleep(.02)
        count += 1
        return reading(count, dop=.99, s1=1 if arm == 'reference' else -1,
                       theta=0 if arm == 'reference' else math.pi/2)

    app.pax.read_fresh_polarization.side_effect = fresh
    monkeypatch.setattr(QtWidgets.QMessageBox, 'question', lambda *args: QtWidgets.QMessageBox.Save)
    timer = QtCore.QTimer()

    def exercise():
        nonlocal phase, arm
        panels = [w for w in gui.topLevelWidgets() if w.windowTitle() == 'PAX · manual alignment' and w.isVisible()]
        if not panels or not panels[0].last:
            return
        panel = panels[0]
        if phase == 0:
            panel.orthogonalizer_button.click()
            window = panel.orthogonalizer
            panel.orthogonalizer_button.click()
            evidence['same_window'] = panel.orthogonalizer is window
            window.duration.setValue(.2)
            window.acquire_button.click()
            phase = 1
        elif phase == 1 and panel.orthogonalizer.reference:
            evidence['reference'] = panel.orthogonalizer.reference
            arm = 'live'
            panel.orthogonalizer.align_button.click()
            phase = 2
        elif phase == 2 and panel.orthogonalizer.values['error_deg'].text() == '0.00':
            evidence['overlap'] = panel.orthogonalizer.values['overlap'].text()
            evidence['closed_at'] = count
            panel.orthogonalizer.close()
            phase = 3
        elif phase == 3 and panel.orthogonalizer is None and count > evidence['closed_at'] + 3:
            evidence['continued'] = panel.last['row']['sample'] > evidence['closed_at']
            panel.orthogonalizer_button.click()
            evidence['cleared'] = panel.orthogonalizer.reference is None
            panel.orthogonalizer.acquire_button.click()
            panel.orthogonalizer.close()
            phase = 4
        elif phase == 4 and panel.orthogonalizer is None:
            panel.button.click()
            timer.stop()

    timer.timeout.connect(exercise)
    timer.start(20)
    watchdog = QtCore.QTimer()
    watchdog.setSingleShot(True)
    watchdog.timeout.connect(lambda: [w.close() for w in gui.topLevelWidgets()])
    watchdog.start(5000)
    path = tmp_path / 'data.csv'
    try:
        result = pax_live.run_panel(app, path)
    finally:
        timer.stop()
        watchdog.stop()
    assert phase == 4 and evidence['same_window'] and evidence['continued'] and evidence['cleared']
    assert evidence['overlap'] == '0.0000'
    assert evidence['reference']['sample_count'] >= 3
    assert result['disposition'] == 'save'
    app.pax.connect.assert_called_once()
    app.pax.disconnect.assert_called_once()
    assert app.rp.mock_calls == []
    assert len(list(csv.DictReader(path.open()))) == result['sample_count'] == count
    events = [json.loads(line)['event'] for line in path.with_name('events.jsonl').read_text().splitlines()]
    assert 'orthogonalizer_reference_set' in events
    assert 'orthogonalizer_align' in events
    assert 'orthogonalizer_reference_cancelled' in events
