"""Compact recorded trajectories; no fitting, filtering or direction renormalization."""
from datetime import datetime

import matplotlib.pyplot as plt
from matplotlib.colors import Normalize
from mpl_toolkits.mplot3d.art3d import Line3DCollection
import numpy as np


def numbers(rows, key):
    def read(row):
        try:
            return float(row.get(key, 'nan'))
        except (ValueError, TypeError):
            return np.nan
    return np.array([read(row) for row in rows])


def trajectory(rows):
    """Keep row order and invalid rows so no segment can bridge a missing record."""
    xyz = np.column_stack([numbers(rows, key) for key in ('s1', 's2', 's3')])
    definition = 'Recorded suite direction (derived from PAX angles; not DoP-scaled).'
    if not any(key in row for row in rows for key in ('s1', 's2', 's3')):
        power = numbers(rows, 'S0')
        raw = np.column_stack([numbers(rows, key) for key in ('S1', 'S2', 'S3')])
        xyz = np.full_like(raw, np.nan)
        valid_power = np.isfinite(power) & (power > 0)
        with np.errstate(over='ignore', invalid='ignore'):
            np.divide(raw, power[:, None], out=xyz, where=valid_power[:, None])
        definition = 'Recorded Stokes divided by S0; nonpositive/invalid S0 unavailable.'
    wall = np.full(len(rows), np.nan)
    for i, row in enumerate(rows):
        try:
            stamp = datetime.fromisoformat(row.get('utc', '').replace('Z', '+00:00'))
            if stamp.tzinfo is not None:
                wall[i] = stamp.timestamp()
        except (ValueError, TypeError, AttributeError, OverflowError):
            pass
    times = numbers(rows, 'elapsed_s')
    notes = [definition, 'Low-DoP measurements have increased polarization-direction uncertainty; no DoP filtering.']
    if np.isfinite(times).sum() >= 2:
        notes.append('Color: recorded host elapsed seconds.')
    elif np.isfinite(wall).sum() >= 2:
        times = wall - wall[np.flatnonzero(np.isfinite(wall))[0]]
        notes.append('Color: UTC elapsed seconds from first timestamp.')
    else:
        return dict(available=False, reason='Physical elapsed time unavailable; device timestamp units are not assumed.')
    valid = np.isfinite(xyz).all(axis=1) & np.isfinite(times)
    if valid.sum() < 2:
        return dict(available=False, reason='Fewer than two finite Stokes/time records.')
    dt = np.diff(times)
    positive = dt[np.isfinite(dt) & (dt > 0)]
    if not len(positive) or times[valid].min() == times[valid].max():
        return dict(available=False, reason='No increasing physical time span for finite Stokes records.')
    gap_limit = 5 * np.median(positive) if len(positive) else np.nan
    connect = valid[:-1] & valid[1:] & (dt > 0) & (dt <= gap_limit)
    notes.append(f'No connections over invalid rows, nonincreasing time, or gaps > {gap_limit:.3g} s (5× median positive cadence).')
    paired = np.isfinite(times) & np.isfinite(wall)
    if paired.sum() >= 2:
        i = np.flatnonzero(paired)[0]
        tolerance = max(.05, .1 * np.median(positive)) if len(positive) else .05
        mismatch = np.abs((times[paired]-times[i]) - (wall[paired]-wall[i])) > tolerance
        wall_dt = np.diff(wall)
        both = paired[:-1] & paired[1:]
        bad_clock = both & ((wall_dt <= 0) | (np.abs(wall_dt-dt) > tolerance))
        connect[bad_clock] = False
        notes.append(f'UTC comparison tolerance: {tolerance:.3g} s (max of 0.05 s and 10% median cadence).')
        notes.append('UTC/elapsed disagreement detected; host elapsed retained and inconsistent adjacent clock intervals unconnected.'
                     if mismatch.any() or bad_clock.any() else 'UTC and elapsed time consistent within timestamp tolerance.')
    for field in ('sample', 'capture'):
        sequence = numbers(rows, field)
        both = np.isfinite(sequence[:-1]) & np.isfinite(sequence[1:])
        connect[both & (np.diff(sequence) != 1)] = False
    notes.append(f'{len(rows)-valid.sum()} invalid Stokes/time rows unplottable; finite low-DoP records retained.')
    return dict(available=True, xyz=xyz, times=times, valid=valid, connect=connect,
                caption='Measured normalized Stokes trajectory, colored by elapsed acquisition time. ' + ' '.join(notes))


def draw_trajectory(fig, slot, rows):
    """Draw in an existing page's compact panel and return its provenance caption."""
    data = trajectory(rows)
    if not data['available']:
        ax = fig.add_subplot(slot)
        ax.axis('off')
        ax.set_title('Poincaré Trajectory', fontsize=10)
        ax.text(.5, .5, 'Not available\n' + data['reason'], ha='center', va='center',
                wrap=True, transform=ax.transAxes, fontsize=8, color='gray')
        return data['reason']
    ax = fig.add_subplot(slot, projection='3d')
    angle = np.linspace(0, 2*np.pi, 120)
    c, s = np.cos(angle), np.sin(angle)
    for points in ((c, s, 0*c), (c, 0*c, s), (0*c, c, s)):
        ax.plot(*points, color='#78909c', alpha=.22, lw=.45)
    for latitude in (-np.pi/3, -np.pi/6, np.pi/6, np.pi/3):
        ax.plot(np.cos(latitude)*c, np.cos(latitude)*s, np.full_like(c, np.sin(latitude)),
                color='#78909c', alpha=.09, lw=.35)
    xyz, times, valid, connect = (data[key] for key in ('xyz', 'times', 'valid', 'connect'))
    norm = Normalize(times[valid].min(), times[valid].max())
    segments = np.stack((xyz[:-1], xyz[1:]), axis=1)[connect]
    lines = Line3DCollection(segments, cmap='viridis', norm=norm, linewidths=.65)
    lines.set_array(((times[:-1]+times[1:])/2)[connect])
    ax.add_collection3d(lines)
    # Retain isolated finite samples, which cannot be represented by a segment.
    linked = np.zeros(len(rows), dtype=bool)
    linked[:-1] |= connect
    linked[1:] |= connect
    isolated = valid & ~linked
    if isolated.any():
        ax.scatter(*xyz[isolated].T, c=times[isolated], cmap='viridis', norm=norm,
                   s=2, depthshade=False, linewidths=0)
    ends = np.flatnonzero(valid)[[0, -1]]
    ax.scatter(*xyz[ends].T, c=times[ends], cmap='viridis', norm=norm,
               s=3, depthshade=False, linewidths=0)
    limit = max(1.05, np.max(np.abs(xyz[valid])) * 1.05)
    for name in ('x', 'y', 'z'):
        getattr(ax, f'set_{name}lim')(-limit, limit)
        getattr(ax, f'set_{name}ticks')([-1, 0, 1])
        getattr(ax, f'set_{name}label')('S'+str('xyz'.index(name)+1), fontsize=8, labelpad=-2)
        getattr(ax, f'{name}axis').pane.fill = False
        getattr(ax, f'{name}axis').pane.set_edgecolor((1, 1, 1, 0))
    ax.set_box_aspect((1, 1, 1))
    ax.view_init(elev=22, azim=35)
    ax.grid(False)
    ax.tick_params(labelsize=6, pad=-2)
    ax.set_title('Poincaré Trajectory', fontsize=10, color='#18354b')
    bar = fig.colorbar(plt.cm.ScalarMappable(norm=norm, cmap='viridis'), ax=ax,
                       fraction=.025, pad=.025, shrink=.6)
    bar.set_label('Elapsed (s)', fontsize=7)
    bar.ax.tick_params(labelsize=6)
    return data['caption']


def add_panel(fig, slot, caption_slot, rows):
    import textwrap
    caption = draw_trajectory(fig, slot, rows)
    ax = fig.add_subplot(caption_slot)
    ax.axis('off')
    ax.text(0, .95, '\n'.join(textwrap.wrap(caption, width=48)), va='top', fontsize=7,
            color='#18354b', transform=ax.transAxes)
