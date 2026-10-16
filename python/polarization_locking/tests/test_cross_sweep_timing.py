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


def _saved_timing_input(directory, reference):
    """Deterministic complete analysis input, with independent measured trace."""
    import csv
    import json
    time = np.arange(.25, 4., .02)
    voltage = .5 + .4 * np.sin(2 * np.pi * time)
    angle = 2 * np.pi * (voltage - .1) / .8
    rows = [dict(pax_transaction_midpoint_s=str(t + .12), segment_id='0', phase='analysis',
                 s1='.2', s2=str(np.sqrt(.96) * np.cos(a)), s3=str(np.sqrt(.96) * np.sin(a)),
                 dop=str(.98 + .001 * t), pax_ptotal=str(1 + .2 * np.cos(a)), unknown='retained')
            for t, a in zip(time, angle)]
    segment = dict(segment_id=0, target_actuator='phi1', frequency_hz=1., center_v=.5,
                   amplitude_v=.4, drive_started_s=0., reference_captures=[])
    if reference in ('fpga', 'host'):
        segment['reference_captures'] = [dict(source='fpga_trigger_timestamp' if reference == 'fpga' else 'host_request_completion_bracket')]
    acquisition = dict(segments=[segment], reference_timing_primary='fpga_trigger_timestamp', pax_latency_correction_s=.12)
    (directory/'acquisition.json').write_text(json.dumps(acquisition))
    with (directory/'data.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    if reference != 'absent':
        t = np.arange(0., 4.25, .002)
        np.savez(directory/'drive_trace.npz', time_s=t, segment_id=np.zeros(len(t), dtype=int),
                 actuator_id=np.ones(len(t), dtype=int), capture_id=np.zeros(len(t), dtype=int),
                 valid_measurement=np.ones(len(t), dtype=bool), input_clipped=np.zeros(len(t), dtype=bool),
                 in1_v=.5 + .4 * np.sin(2 * np.pi * t))


def test_full_analysis_matches_previous_ingestion_for_all_reference_cases(tmp_path, monkeypatch):
    import csv
    import json
    from polarization_locking.analysis import analyze_cross_sweep_timing as audit

    for reference in ('absent', 'empty', 'host', 'fpga'):
        directory = tmp_path/reference
        directory.mkdir()
        _saved_timing_input(directory, reference)
        actual = audit.analyze(directory, .2, .02)
        # Reproduce the pre-increment ingestion, feeding the unchanged calculations.
        def old_metadata(run, name, **kwargs):
            return json.loads((run.directory/name).read_text())

        def old_records(run, name):
            with (run.directory/name).open(newline='') as handle:
                yield from csv.DictReader(handle)

        with monkeypatch.context() as patch:
            patch.setattr(audit, 'read_metadata', old_metadata)
            patch.setattr(audit, 'iter_records', old_records)
            expected = audit.analyze(directory, .2, .02)
        assert json.dumps(actual, sort_keys=True) == json.dumps(expected, sort_keys=True)
        assert actual['primary_reference'] == ('fpga_measured_reference' if reference == 'fpga' else 'nominal_command_clock')


def test_consumer_rejects_malformed_required_record_with_location(tmp_path):
    import pytest
    from experiment_reader import ReaderError
    from polarization_locking.analysis.analyze_cross_sweep_timing import analyze

    _saved_timing_input(tmp_path, 'absent')
    with (tmp_path/'data.csv').open('a') as handle:
        handle.write('truncated,row\n')
    with pytest.raises(ReaderError, match='CSV row width') as failure:
        analyze(tmp_path, .2, .02)
    assert failure.value.diagnostic.path == tmp_path/'data.csv'
    assert failure.value.diagnostic.line > 1


def test_consumer_keeps_empty_input_required_field_rejection(tmp_path):
    import pytest
    from polarization_locking.analysis.analyze_cross_sweep_timing import analyze

    _saved_timing_input(tmp_path, 'absent')
    (tmp_path/'data.csv').write_text('pax_transaction_midpoint_s,segment_id,phase,s1,s2,s3,dop,pax_ptotal\n')
    with pytest.raises(ValueError, match='Run lacks latency-audit fields'):
        analyze(tmp_path, .2, .02)


def test_direct_cli_keeps_output_products_without_source_pythonpath(tmp_path):
    import json
    import os
    from pathlib import Path
    import subprocess
    import sys
    from polarization_locking.analysis import analyze_cross_sweep_timing as audit

    _saved_timing_input(tmp_path, 'absent')
    output = tmp_path/'selected-output.json'
    environment = dict(os.environ)
    root = Path(__file__).resolve().parents[3]
    environment['PYTHONPATH'] = str(root/'scripts/offline')
    result = subprocess.run([sys.executable, audit.__file__, str(tmp_path), '--maximum-lag-s', '.2',
                             '--step-s', '.02', '--output', str(output)], cwd=tmp_path,
                            env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    expected = audit.analyze(tmp_path, .2, .02)
    assert output.read_text().strip() == json.dumps(expected, indent=2)
    assert json.loads(result.stdout)['primary_reference'] == 'nominal_command_clock'
    assert not output.with_suffix('.tmp').exists()
