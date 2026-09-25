"""Static PAX motor-on/off comparison using identical fast PD acquisitions."""
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import time

import numpy as np

from ..control import sphere_angles_from_stokes
from ..settings import write_json
from .prompts import prepare_setup
from .visibility import _pax_snapshot, _scope_sleep


# Blank capture identifies PAX rows; -1 identifies the dark trace. PD sample
# order within each capture supplies its index. Capture metadata lives in setup.
CSV_FIELDS = ('capture', 'voltage_v', 'elapsed_s', 'pax_ptotal', 's1', 's2', 's3', 'dop')


def write_samples(writer, trace, capture):
    writer.writerows(dict(capture=capture, voltage_v=float(value)) for value in trace)


def compact_report_rows(path):
    """Rebuild derived report data with at most one raw capture in memory."""
    setup = json.loads(path.with_name('vibration-setup.json').read_text())
    metadata = {str(row['capture']): row for row in setup['captures']}
    rows, spectra, trace = [], {}, []
    current = None

    def finish():
        if current is None or current == '-1':
            return
        meta = metadata[current]
        if len(trace) != meta['sample_count']:
            raise ValueError(f'Incomplete PD capture {current}')
        stats, _, frequency, psd = trace_statistics(trace, meta['sampling_time_s'],
                                                  meta['dark_voltage_v'], setup['band_hz'])
        rows.append(dict(record_type='pd_capture', **meta, **{k:v for k,v in stats.items() if k not in meta}))
        condition = meta['condition']
        if condition not in spectra:
            spectra[condition] = [frequency, psd, 1]
        else:
            if not np.array_equal(frequency, spectra[condition][0]):
                raise ValueError('PD sampling grid changed between captures')
            spectra[condition][1] += psd
            spectra[condition][2] += 1

    with path.open(newline='') as handle:
        for row in csv.DictReader(handle):
            if row['capture'] == '':
                row = {k:float(v) for k,v in row.items() if v != ''}
                angles = sphere_angles_from_stokes(row['s1'], row['s2'], row['s3'])
                rows.append(dict(record_type='pax', condition='pax_on', u_rad=angles.u, v_rad=angles.v, **row))
            else:
                if row['capture'] != current:
                    finish()
                    current, trace = row['capture'], []
                trace.append(float(row['voltage_v']))
        finish()
    for condition, (frequency, psd, count) in spectra.items():
        summary = summarize_pd([r for r in rows if r['record_type']=='pd_capture' and r['condition']==condition],
                               setup['dark_voltage_v'])
        normalized = psd/count/summary['dark_corrected_mean_v']**2 if summary['status']=='ok' else np.full(len(psd), np.nan)
        rows.extend(dict(record_type='spectrum', condition=condition, frequency_hz=f,
                         normalized_psd_per_hz=n) for f,n in zip(frequency, normalized))
    return rows


def trace_statistics(trace, dt, dark, band):
    """Sample variance plus mean-removed Hann periodogram power in a fixed band."""
    trace = np.asarray(trace, dtype=float)
    if trace.ndim != 1 or len(trace) < 4 or not np.all(np.isfinite(trace)) or dt <= 0:
        raise ValueError('Invalid PD scope trace')
    if not 0 < band[0] < band[1] < .5 / dt:
        raise ValueError('Requested noise band is outside actual PD bandwidth')
    mean = float(trace.mean())
    variance = float(trace.var(ddof=1))
    centered = trace - mean
    window = np.hanning(len(trace))
    psd = np.abs(np.fft.rfft(centered * window)) ** 2 * dt / np.sum(window ** 2)
    psd[1:-1 if len(trace) % 2 == 0 else None] *= 2
    frequency = np.fft.rfftfreq(len(trace), dt)
    selected = (frequency >= band[0]) & (frequency <= band[1])
    if not np.any(selected):
        raise ValueError('Capture is too short to resolve the requested noise band')
    band_variance = float(np.sum(psd[selected]) / (len(trace) * dt))
    clipped = int(np.sum(np.abs(trace) >= 8190 / 8192))
    denominator = mean - dark
    usable = abs(denominator) > 1e-12 and not clipped
    stats = dict(sample_count=len(trace), sampling_time_s=dt, mean_v=mean,
                 variance_v2=variance, normalized_variance=variance / denominator**2 if usable else None,
                 band_variance_v2=band_variance,
                 normalized_band_variance=band_variance / denominator**2 if usable else None,
                 dark_voltage_v=dark, clipped_samples=clipped)
    normalized = (trace - dark) / denominator if usable else np.full(len(trace), np.nan)
    return stats, normalized, frequency, psd


def summarize_pd(rows, dark):
    if not rows:
        return {'sample_count': 0}
    count = sum(row['sample_count'] for row in rows)
    mean = sum(row['sample_count'] * row['mean_v'] for row in rows) / count
    variance = sum((row['sample_count'] - 1) * row['variance_v2'] +
                   row['sample_count'] * (row['mean_v'] - mean)**2 for row in rows) / (count - 1)
    band_variance = float(np.mean([row['band_variance_v2'] for row in rows]))
    clipped = sum(row['clipped_samples'] for row in rows)
    dc = mean - dark
    usable = abs(dc) > 1e-12 and not clipped
    return dict(sample_count=count, capture_count=len(rows), mean_v=mean, dark_corrected_mean_v=dc,
                variance_v2=variance, band_variance_v2=band_variance,
                normalized_variance=variance / dc**2 if usable else None,
                normalized_band_variance=band_variance / dc**2 if usable else None,
                clipped_samples=clipped, status='ok' if usable else 'clipped or zero light baseline; normalized metrics unavailable',
                acquired_s=sum(row['sample_count'] * row['sampling_time_s'] for row in rows),
                wall_span_s=rows[-1]['finished_s'] - rows[0]['started_s'],
                sampling_rate_hz=1 / rows[0]['sampling_time_s'])


def summarize_pax(rows, pole_tolerance):
    if len(rows) < 2:
        return {'sample_count': len(rows)}
    power = np.array([row['pax_ptotal'] for row in rows])
    u = np.unwrap([row['u_rad'] for row in rows])
    v = np.array([row['v_rad'] for row in rows])
    at_pole = bool(np.any(np.abs(np.sin(v)) <= pole_tolerance))
    return dict(sample_count=len(rows), mean_power_w=float(power.mean()),
                normalized_power_variance=float(power.var(ddof=1) / power.mean()**2),
                u_variance_rad2=None if at_pole else float(u.var(ddof=1)),
                v_variance_rad2=float(v.var(ddof=1)), mean_dop=float(np.mean([row['dop'] for row in rows])),
                minimum_sin_v=float(np.min(np.abs(np.sin(v)))),
                angle_note='u undefined at an S1 pole' if at_pole else 'u unwrapped in acquisition order; coordinate noise grows near S1 poles',
                update_rate_hz=(len(rows)-1)/(rows[-1]['elapsed_s']-rows[0]['elapsed_s']))


def compare(on, off):
    result = {}
    for key in ('normalized_variance', 'normalized_band_variance'):
        a, b = on.get(key), off.get(key)
        result[key + '_on_minus_off'] = a-b if a is not None and b is not None else None
        result[key + '_on_over_off'] = a/b if a is not None and b is not None and b > 0 else None
    result['interpretation'] = ('Signed on-minus-off excess, not proof of mechanical causation. '
        'Compare the same PD bandwidth, optical alignment, light level and gain. Sequential drift, '
        'shot noise and electronics can also differ. PAX power/angles have a much slower bandwidth.')
    return result


def capture(scope, pax, config, origin, index, rows, writer, handle):
    """FPGA captures the fast trace while same-thread PAX polling proceeds."""
    future = scope.single_async()
    deadline = time.monotonic() + scope.duration + config.pax_fresh_read_timeout_s + 3
    try:
        _scope_sleep(.001)
        while not future.done():
            if time.monotonic() >= deadline:
                raise TimeoutError('PD capture did not complete')
            if pax is not None:
                row = _pax_snapshot(pax, origin, index)
                angles = sphere_angles_from_stokes(row['s1'], row['s2'], row['s3'])
                row.update(condition='pax_on', u_rad=angles.u, v_rad=angles.v)
                writer.writerow({key: row[key] for key in CSV_FIELDS if key not in ('capture', 'voltage_v')})
                handle.flush()
                rows.append(row)
            _scope_sleep(.001 if pax is not None else .01)
        return np.asarray(future.result()[0], dtype=float).copy()
    finally:
        if not future.done():
            future.cancel()


def acquire(rp, pax, config, output_file, duration_s):
    path = Path(output_file)
    scope = rp.p.rp.scope
    names = ('input1', 'duration', 'average', 'trigger_source', 'trigger_delay',
             'ch1_active', 'ch2_active', 'rolling_mode', 'trace_average')
    previous = {name: getattr(scope, name) for name in names}
    trigger_delay = scope._trigger_delay_register
    dark = config.pax_vibration_dark_voltage_v
    band = (config.pax_vibration_band_low_hz, config.pax_vibration_band_high_hz)
    pd_rows, pax_rows = [], []
    origin = time.monotonic()
    motor_stopped = False
    setup = dict(data_format='compact-vibration-v1', captures=[],
                 csv_layout='capture blank: PAX; -1: dark; otherwise PD capture ID. Sample index is row order within capture; time=index*sampling_time_s.',
                 pd_input=config.pd_input, duration_per_condition_s=duration_s,
                 band_hz=band, fpga_average=True, scope_decimation=config.pax_vibration_scope_decimation,
                 phi1_bias_v=config.pax_vibration_phi1_bias_voltage,
                 phi2_bias_v=config.pax_vibration_phi2_bias_voltage,
                 off_definition='rotation motor commanded off; PAX stays physically mounted and powered',
                 motor_settle_s=config.pax_vibration_motor_settle_s,
                 normalization='(V - dark) / mean(V - dark); no clipping to zero; per-capture traces, per-condition summary',
                 spectrum='one-sided mean-removed Hann periodogram, averaged across fixed-length captures',
                 timing='FPGA records have host bounds and gaps; PAX host timestamps are not hardware-trigger synchronized')
    write_json(path.parent / 'vibration-setup.json', setup)
    with path.open('w', newline='') as pd_handle:
        pd_writer = csv.DictWriter(pd_handle, fieldnames=CSV_FIELDS)
        pd_writer.writeheader(); pd_handle.flush()
        try:
            rp.set_output_voltage(config.pax_vibration_phi1_bias_voltage, config.pax_vibration_phi2_bias_voltage)
            scope.setup(input1=config.pd_input, duration=16384 * 8e-9 * config.pax_vibration_scope_decimation,
                        average=True, trigger_source='immediately', trigger_delay=0.,
                        ch1_active=True, ch2_active=False, rolling_mode=False, trace_average=1)
            if dark is None:
                prepare_setup(f'Block light to the PD on {config.pd_input} to measure its signed dark offset. ')
                trace = np.asarray(scope.single(timeout=max(3., scope.duration+3))[0], dtype=float).copy()
                setup['captures'].append(dict(capture=-1, condition='dark', sampling_time_s=float(scope.sampling_time), sample_count=len(trace)))
                write_json(path.parent / 'vibration-setup.json', setup)
                write_samples(pd_writer, trace, -1)
                pd_handle.flush()
                if not len(trace) or not np.all(np.isfinite(trace)) or np.any(np.abs(trace) >= 8190/8192):
                    raise ValueError('Invalid/clipped dark trace; raw data retained')
                dark = float(trace.mean())
                write_json(path.parent / 'dark.json', dict(mean_v=dark, variance_v2=float(trace.var(ddof=1)), sample_count=len(trace)))
                prepare_setup('Restore PD light. Keep both detectors, optics and electronics fixed for both windows. ')
            else:
                prepare_setup('Keep PD light, PAX mounting, optics and electronics fixed for both windows. ')
            setup['dark_voltage_v'] = dark
            setup['pax_wavelength_nm'] = float(pax.client.get_wavelength())
            write_json(path.parent / 'vibration-setup.json', setup)
            pax.read_fresh_polarization()  # existing startup/validity/advancement gate
            for condition in ('pax_on', 'pax_off'):
                if condition == 'pax_off':
                    pax.stop_rotation()
                    motor_stopped = True
                    setup['motor_off_command_utc'] = datetime.now(timezone.utc).isoformat()
                    write_json(path.parent / 'vibration-setup.json', setup)
                    print(f'PAX motor commanded off; settling {config.pax_vibration_motor_settle_s:g} s.')
                    _scope_sleep(config.pax_vibration_motor_settle_s)
                print(f'{condition}: {duration_s:g} s on {config.pd_input}; static outputs, identical PD settings.')
                started = time.monotonic()
                while time.monotonic() - started < duration_s:
                    index = len(pd_rows)
                    stamp = datetime.now(timezone.utc).isoformat()
                    before = time.monotonic() - origin
                    trace = capture(scope, pax if condition == 'pax_on' else None, config, origin, index,
                                    pax_rows, pd_writer, pd_handle)
                    after = time.monotonic() - origin
                    dt = float(scope.sampling_time)
                    metadata = dict(capture=index, condition=condition, utc=stamp, started_s=before,
                                    finished_s=after, sampling_time_s=dt, sample_count=len(trace), dark_voltage_v=dark)
                    setup['captures'].append(metadata)
                    write_json(path.parent / 'vibration-setup.json', setup)
                    write_samples(pd_writer, trace, index)
                    pd_handle.flush()
                    stats, _, _, _ = trace_statistics(trace, dt, dark, band)
                    pd_rows.append(dict(condition=condition, capture=index, utc=stamp,
                                        started_s=before, finished_s=after, **stats))

        finally:
            # Preserve diagnostics even after an interrupted window or failed cleanup.
            try:
                if not motor_stopped:
                    pax.stop_rotation()
            finally:
                try:
                    try:
                        scope.stop()
                    finally:
                        for name, value in previous.items():
                            setattr(scope, name, value)
                        scope._trigger_delay_register = trigger_delay
                finally:
                    summaries = {condition: summarize_pd([row for row in pd_rows if row['condition'] == condition], dark)
                                 for condition in ('pax_on', 'pax_off')}
                    summary = dict(pd=summaries, pax=summarize_pax(pax_rows, config.sphere_pole_tolerance),
                                   comparison=compare(summaries['pax_on'], summaries['pax_off']), band_hz=band)
                    write_json(path.parent / 'vibration.json', summary)
                    print('Variance comparison:', summary['comparison'])
