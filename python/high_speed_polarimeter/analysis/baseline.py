"""Time averages of undriven records, with explicit integration/gap conventions."""
import numpy as np


def time_average(times, values):
    times, values = np.asarray(times, float), np.asarray(values, float)
    if values.ndim == 1:
        values = values[:, None]
    if len(times) < 2:
        return {'status': 'UNAVAILABLE', 'reason': 'Fewer than two timestamped baseline samples.'}
    dt = np.diff(times)
    positive = dt[np.isfinite(dt) & (dt > 0)]
    if not positive.size:
        return {'status': 'UNAVAILABLE', 'reason': 'No positive baseline time intervals.'}
    limit = 5*float(np.median(positive))
    valid = np.isfinite(times) & np.isfinite(values).all(axis=1)
    pairs = valid[:-1] & valid[1:] & (dt > 0) & (dt <= limit)
    coverage = float(dt[pairs].sum())
    if coverage <= 0:
        return {'status': 'UNAVAILABLE', 'reason': 'No contiguous valid baseline coverage.'}
    left, right, weights = values[:-1][pairs], values[1:][pairs], dt[pairs,None]
    mean = np.sum((left+right)*weights/2,axis=0)/coverage
    second = np.sum((left**2+right**2)*weights/2,axis=0)/coverage
    variance = np.maximum(0, second-mean**2)  # Only floating-point variance roundoff.
    finite_time = times[np.isfinite(times)]
    return dict(status='AVAILABLE', mean=mean.tolist(), temporal_variance=variance.tolist(),
                temporal_std=np.sqrt(variance).tolist(), covered_s=coverage,
                recorded_span_s=float(finite_time[-1]-finite_time[0]), gap_limit_s=limit,
                excluded_intervals=int((~pairs).sum()), invalid_rows=int((~valid).sum()), samples=len(times),
                method='Trapezoidal estimates of time integrals of values and squared values between adjacent valid records only; no integration over reversed/nonpositive time, invalid records, or gaps >5x median positive cadence. Temporal population variance, not N-1 sample variance. No DoP filtering.')


def summarize(rows):
    selected = [row for row in rows if row['direction']=='undriven']
    times = [float(row['elapsed_s']) for row in selected]
    xyz = np.array([[float(row[k]) for k in ('s1','s2','s3')] for row in selected]).reshape(-1,3)
    result = time_average(times, xyz)
    result['reference_plane'] = 'Undriven downstream PAX reference; not an independently measured pre-EOM input polarization.'
    if result['status']=='AVAILABLE':
        mean = np.array(result['mean'])
        angles = np.degrees(np.arctan2(np.linalg.norm(np.cross(xyz,mean),axis=1), xyz@mean))
        angular = time_average(times, angles**2)
        result['angular_rms_deg'] = float(np.sqrt(angular['mean'][0])) if angular['status']=='AVAILABLE' and np.linalg.norm(mean)>1e-8 else None
        result['dop'] = time_average(times,[float(row['dop']) for row in selected])
        result['power_W'] = time_average(times,[float(row['pax_ptotal']) for row in selected])
        result['distance_to_pax_H_deg'] = float(np.degrees(np.arctan2(np.linalg.norm(mean[1:]),mean[0]))) if np.linalg.norm(mean)>1e-8 else None
    return result
