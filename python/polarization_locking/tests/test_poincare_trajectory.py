"""Recorded coordinates and discontinuities, independent of instrument drivers."""
from datetime import datetime, timedelta, timezone

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pytest

from polarization_locking.reports.poincare_trajectory import trajectory, draw_trajectory


def rows_for(xyz, times=None):
    times = np.arange(len(xyz), dtype=float) if times is None else times
    return [dict(s1=p[0], s2=p[1], s3=p[2], elapsed_s=t, sample=i+1, dop=.01,
                 utc=(datetime(2026, 1, 1, tzinfo=timezone.utc)+timedelta(seconds=float(t))).isoformat())
            for i, (p, t) in enumerate(zip(xyz, times))]


@pytest.mark.parametrize('kind', ['stable', 'circle', 'jump'])
def test_actual_coordinates_and_adjacent_jumps(kind):
    angles = np.linspace(0, 2*np.pi, 80)
    xyz = np.column_stack([np.cos(angles), np.sin(angles), np.zeros(80)])
    if kind == 'stable':
        xyz[:] = [1, 0, 0]
    elif kind == 'jump':
        xyz[40:] *= -1
    rows = rows_for(xyz)
    data = trajectory(rows)
    np.testing.assert_array_equal(data['xyz'], xyz)
    assert data['connect'].all()
    assert 'no DoP filtering' in data['caption']
    before = [row.copy() for row in rows]
    fig = plt.figure(figsize=(3.2, 3.2))
    draw_trajectory(fig, fig.add_gridspec(1, 1)[0, 0], rows)
    fig.canvas.draw()
    plt.close(fig)
    assert rows == before


def test_missing_rows_gap_sequence_and_time_reversal():
    rows = rows_for(np.tile([1., 0., 0.], (9, 1)), [0, 1, 2, 3, 100, 101, 102, 101, 103])
    rows[2]['s2'] = 'nan'
    rows[6]['sample'] = 20
    data = trajectory(rows)
    assert not data['connect'][1:4].any()
    assert not data['connect'][5:7].any()
    assert data['connect'][7]  # Next genuinely consecutive pair remains visible.
    assert data['connect'][0] and data['connect'][4]


def test_wall_clock_disagreement_breaks_only_bad_interval():
    rows = rows_for(np.tile([1., 0., 0.], (5, 1)))
    for row in rows[2:]:
        row['utc'] = (datetime.fromisoformat(row['utc'])+timedelta(seconds=10)).isoformat()
    data = trajectory(rows)
    assert data['connect'].tolist() == [True, False, True, True]
    assert 'disagreement detected' in data['caption']
    np.testing.assert_array_equal(data['times'], np.arange(5))


def test_utc_fallback_and_raw_s0_safeguards():
    rows = rows_for(np.tile([1., 0., 0.], (5, 1)))
    for i, row in enumerate(rows):
        for key in ('s1', 's2', 's3', 'elapsed_s'):
            del row[key]
        row.update(S0=[2, 0, -1, float('nan'), 2][i], S1=1, S2=0, S3=0)
    data = trajectory(rows)
    assert data['valid'].tolist() == [True, False, False, False, True]
    assert not data['connect'].any()
    assert data['xyz'][0, 0] == .5
    np.testing.assert_array_equal(data['times'], np.arange(5))


@pytest.mark.parametrize('rows', [[], [{}], [dict(s1=1, s2=0, s3=0, pax_timestamp=100)]*3])
def test_missing_time_or_stokes_is_unavailable(rows):
    assert not trajectory(rows)['available']


def test_outside_sphere_not_clipped():
    rows = rows_for([[1.4, 0, 0], [-1.5, 0, 0]])
    fig = plt.figure()
    draw_trajectory(fig, fig.add_gridspec(1, 1)[0, 0], rows)
    assert fig.axes[0].get_xlim()[0] < -1.5
    assert fig.axes[0].get_xlim()[1] > 1.4
    plt.close(fig)


def test_visibility_report_preserves_power_page_and_adds_supplement(tmp_path):
    import json
    from types import SimpleNamespace
    from polarization_locking.reports.run_report import add_specialized
    (tmp_path/'visibility.json').write_text(json.dumps({'source': 'pax'}))
    path = tmp_path/'data.csv'
    path.write_text('elapsed_s,pax_ptotal,s1,s2,s3\n0,.001,1,0,0\n1,.001,0,1,0\n')
    figures = []
    class PDF:
        def savefig(self, fig):
            fig.canvas.draw()
            figures.append([ax.get_title() for ax in fig.axes])
    add_specialized(PDF(), SimpleNamespace(report='visibility'),
                    SimpleNamespace(directory=tmp_path, csv=path), {})
    assert len(figures) == 3  # Existing summary and power pages, then supplement.
    assert 'Poincaré Trajectory' not in figures[1]
    assert 'Poincaré Trajectory' in figures[2]


def test_statistics_use_sample_variance_and_disclose_finite_counts():
    from polarization_locking.reports.run_report import descriptive_statistics
    result = descriptive_statistics([{'s1': 1}, {'s1': 2}, {'s1': 3}, {'s1': 'nan'}], ['s1'])[0]
    assert result['n'] == 3 and result['total'] == 4
    assert result['mean'] == 2 and result['std'] == 1 and result['variance'] == 1
    assert result['minimum'] == 1 and result['maximum'] == 3
    single = descriptive_statistics([{'theta_deg': 4}], ['theta_deg'])[0]
    assert np.isnan(single['variance']) and np.isnan(single['std'])


def test_pax_original_four_plots_remain_separate_from_supplement():
    from types import SimpleNamespace
    from polarization_locking.config import PolarizationLockConfig
    from polarization_locking.reports.run_report import add_pax_live
    figures = []
    class PDF:
        def savefig(self, fig):
            fig.canvas.draw()
            figures.append([(ax.name, ax.get_ylabel(), len(ax.tables)) for ax in fig.axes])
    rows = rows_for([[1, 0, 0], [0, 1, 0], [0, 0, 1]])
    for row in rows:
        row.update(theta_deg=10, eta_deg=5, pax_ptotal=.001)
    add_pax_live(PDF(), SimpleNamespace(directory=__import__('pathlib').Path('test')),
                 PolarizationLockConfig(), {'status': 'completed'}, rows)
    assert len(figures) == 3
    assert [a[1] for a in figures[1]] == ['Power (W)', 'Normalized Stokes direction', 'DoP (fraction)', 'Angle (deg)']
    assert all(a[0] != '3d' and a[2] == 0 for a in figures[1])
    assert sum(a[0] == '3d' for a in figures[2]) == 1
    assert sum(a[2] for a in figures[2]) == 2
