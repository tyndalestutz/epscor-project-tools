"""Synthetic checks for the cross-sweep latency audit."""
import numpy as np

from polarization_locking.analysis.analyze_cross_sweep_timing import (
    _branch_curve,
    _measured_branch_curve,
)


def test_branch_scan_recovers_injected_pax_latency():
    frequency = 1.0
    injected_latency_s = 0.12
    physical_time = np.arange(0.0, 4.0, 0.02)
    phase = 2 * np.pi * frequency * physical_time
    voltage = 0.5 + 0.4 * np.sin(phase)
    state_angle = 2 * np.pi * (voltage - 0.1) / 0.8
    rows = []
    for index, time_s in enumerate(physical_time):
        rows.append({
            "pax_transaction_midpoint_s": str(time_s + injected_latency_s),
            "s1": "0.2",
            "s2": str(np.sqrt(0.96) * np.cos(state_angle[index])),
            "s3": str(np.sqrt(0.96) * np.sin(state_angle[index])),
            "dop": "0.98",
            "pax_ptotal": str(1.0 + 0.2 * np.cos(state_angle[index])),
        })
    segment = {
        "frequency_hz": frequency,
        "center_v": 0.5,
        "amplitude_v": 0.4,
        "drive_started_s": 0.0,
    }
    lags = np.arange(0.0, 0.201, 0.001)
    curve = _branch_curve(rows, segment, lags, "stokes")
    recovered = float(lags[np.nanargmin(curve)])
    assert recovered == injected_latency_s


def test_measured_reference_scan_recovers_injected_pax_latency():
    frequency = 1.0
    injected_latency_s = 0.12
    physical_time = np.arange(0.25, 4.0, 0.02)
    phase = 2 * np.pi * frequency * physical_time
    voltage = 0.5 + 0.4 * np.sin(phase)
    state_angle = 2 * np.pi * (voltage - 0.1) / 0.8
    rows = []
    for index, time_s in enumerate(physical_time):
        rows.append({
            "pax_transaction_midpoint_s": str(time_s + injected_latency_s),
            "s1": "0.2",
            "s2": str(np.sqrt(0.96) * np.cos(state_angle[index])),
            "s3": str(np.sqrt(0.96) * np.sin(state_angle[index])),
            "dop": "0.98",
            "pax_ptotal": str(1.0 + 0.2 * np.cos(state_angle[index])),
        })
    reference_time = np.arange(0.0, 4.25, 0.001)
    reference_voltage = 0.5 + 0.4 * np.sin(
        2 * np.pi * frequency * reference_time
    )
    references = {
        0: (
            reference_time,
            reference_voltage,
            np.gradient(reference_voltage, reference_time),
        ),
    }
    lags = np.arange(0.0, 0.201, 0.001)
    curve, counts = _measured_branch_curve(
        rows, references, lags, "stokes", maximum_lag_s=0.2,
    )
    recovered = float(lags[np.nanargmin(curve)])
    assert recovered == injected_latency_s
    assert np.all(counts == 1)


def test_empty_capture_metadata_does_not_establish_fpga_timing(tmp_path, monkeypatch):
    import csv
    import json
    from polarization_locking.analysis import analyze_cross_sweep_timing as audit

    segment = {
        "segment_id": 0, "target_actuator": "phi1", "frequency_hz": 1.0,
        "center_v": .5, "amplitude_v": .4, "drive_started_s": 0.0,
        "reference_captures": [],
    }
    acquisition = {"segments": [segment], "reference_timing_primary": "fpga_trigger_timestamp", "pax_latency_correction_s": .12}
    (tmp_path / 'acquisition.json').write_text(json.dumps(acquisition))
    row = {"pax_transaction_midpoint_s": .2, "segment_id": 0, "phase": "analysis",
           "s1": 1., "s2": 0., "s3": 0., "dop": .9, "pax_ptotal": .001}
    with (tmp_path / 'data.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=row)
        writer.writeheader()
        writer.writerow(row)
    np.savez(tmp_path / 'drive_trace.npz', unused=np.array([1]))
    monkeypatch.setattr(audit, '_branch_curve', lambda rows, segment, lags, observable: np.ones(len(lags)))
    monkeypatch.setattr(audit, '_fpga_reference_for_segment', lambda *args: (_ for _ in ()).throw(AssertionError('Missing metadata must not be accepted')))
    result = audit.analyze(tmp_path, maximum_lag_s=.25, step_s=.001)
    assert result['primary_reference'] == 'nominal_command_clock'
    assert 'fpga_measured_reference' not in result['analyses']
