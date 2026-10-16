"""Offline checks for continuous coupling acquisition and analysis."""
from contextlib import contextmanager
import csv
import json

import numpy as np
import pytest

from polarization_locking.catalog import BY_KEY, default_options
from polarization_locking.config import PolarizationLockConfig
from polarization_locking.hardware.pax_interface import PAXReading
from polarization_locking.reports.plot_continuous_cross_sweep import (
    branch_separation,
    cycle_repeatability,
    fit_plane,
)
from polarization_locking.routines import continuous_cross_sweep as continuous
from polarization_locking.settings import load_recipe, validate


def test_cross_sweep_both_validates_and_rejects_unsafe_waveform():
    case = BY_KEY["cross-sweep"]
    options = default_options(case) | {"actuator": "both"}
    validate(PolarizationLockConfig(), case, options)
    config = PolarizationLockConfig(cross_sweep_sine_center_voltage=0.1,
                                    cross_sweep_sine_amplitude_voltage=0.2)
    with pytest.raises(ValueError, match="center"):
        validate(config, case, options)


def test_legacy_cross_sweep_axis_recipe_migrates(tmp_path):
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps({
        "schema_version": 1, "test": "cross-sweep",
        "options": {"axis": "phi2"}, "config": {},
    }))
    _, _, options = load_recipe(path)
    assert options["actuator"] == "phi2"
    assert "axis" not in options


def test_reference_decimation_and_gap_safe_interpolation():
    signal = np.arange(20, dtype=float)
    times, in1, in2, rate = continuous._boxcar_decimate(signal, signal * 2, 0.001, 250)
    assert rate == pytest.approx(250)
    assert in1 == pytest.approx([1.5, 5.5, 9.5, 13.5, 17.5])
    assert in2 == pytest.approx(in1 * 2)
    value, gap, valid = continuous._interpolate_reference(
        np.asarray([0.0, 0.1, 1.0, 1.1]), np.asarray([0.0, 1.0, 10.0, 11.0]),
        np.asarray([0, 0, 1, 1]), 0.5,
    )
    assert not valid and np.isnan(value) and gap == pytest.approx(0.9)
    value, gap, valid = continuous._interpolate_reference(
        np.asarray([0.0, 0.1]), np.asarray([0.0, 1.0]), np.asarray([0, 0]), 0.05,
    )
    assert valid and value == pytest.approx(0.5) and gap == pytest.approx(0.1)


def test_pax_sample_preserves_midpoint_and_applies_explicit_latency(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(continuous.time, "monotonic", clock.monotonic)
    segment = {
        "drive_started_s": 0.0, "frequency_hz": 1.0, "warmup_cycles": 0,
        "recorded_cycles": 1, "minimum_dop": 0.9, "sub_run": 0,
        "target_actuator": "phi1", "segment_id": 0, "bias_index": 0,
        "fixed_actuator": "phi2", "fixed_v": 0.2, "center_v": 0.4,
        "amplitude_v": 0.3,
    }
    sample = continuous._pax_sample(_PAX(clock), 0.0, segment, 0.02)
    assert sample["pax_requested_s"] == pytest.approx(0.0)
    assert sample["pax_received_s"] == pytest.approx(0.1)
    assert sample["pax_transaction_midpoint_s"] == pytest.approx(0.05)
    assert sample["elapsed_s"] == pytest.approx(0.03)
    assert sample["pax_latency_correction_s"] == pytest.approx(0.02)


def test_reference_validation_rejects_attenuated_inverted_target():
    phase = np.linspace(0, 4 * np.pi, 500, endpoint=False)
    trace = {
        name: [np.asarray(value, dtype=continuous.TRACE_DTYPES[name])]
        for name, value in {
            "time_s": phase / (2 * np.pi),
            "in1_v": 0.4 + 0.4 * np.sin(phase),
            "in2_v": 0.007 - 0.016 * np.sin(phase),
            "commanded_target_v": 0.4 + 0.4 * np.sin(phase),
            "commanded_fixed_v": np.zeros(len(phase)),
            "segment_id": np.r_[np.zeros(250), np.ones(250)],
            "actuator_id": np.r_[np.ones(250), np.full(250, 2)],
            "bias_index": np.zeros(len(phase)),
            "cycle_index": np.zeros(len(phase)),
            "capture_id": np.zeros(len(phase)),
            "valid_measurement": np.ones(len(phase), dtype=bool),
            "input_clipped": np.zeros(len(phase), dtype=bool),
            "time_uncertainty_s": np.zeros(len(phase)),
        }.items()
    }
    segments = [
        {"segment_id": 0, "target_actuator": "phi1", "frequency_hz": 1.0,
         "drive_started_s": 0.0, "amplitude_v": 0.4},
        {"segment_id": 1, "target_actuator": "phi2", "frequency_hz": 1.0,
         "drive_started_s": 0.0, "amplitude_v": 0.4},
    ]
    result = continuous._reference_validation(trace, segments)
    assert result[0]["status"] == "trustworthy"
    assert result[1]["status"] == "failed_sanity_check"


def _synthetic_rows(branch_offset=0.0):
    rows = []
    for cycle in (0, 1):
        for phase in np.linspace(0, 2 * np.pi, 80, endpoint=False):
            voltage = 0.5 + 0.4 * np.sin(phase)
            direction = "rising" if np.cos(phase) > 0 else "falling"
            angular_offset = branch_offset if direction == "falling" else 0.0
            angle = 2 * np.pi * voltage + angular_offset
            rows.append({
                "cycle_index": cycle, "sine_phase_rad": phase,
                "measured_target_v": voltage, "voltage_direction": direction,
                "s1": 0.2, "s2": np.sqrt(1 - 0.2 ** 2) * np.cos(angle),
                "s3": np.sqrt(1 - 0.2 ** 2) * np.sin(angle),
                "pax_ptotal": 1.0, "dop": 0.99,
            })
    return rows


def test_plane_cycle_and_branch_metrics_are_descriptive():
    rows = _synthetic_rows(branch_offset=np.deg2rad(3))
    normal, residual = fit_plane(rows)
    assert abs(normal[0]) > 0.99
    assert residual < 1e-12
    cycle_angle, cycle_power, cycle_dop = cycle_repeatability(rows)
    assert cycle_angle < 1e-5 and cycle_power == pytest.approx(0) and cycle_dop == pytest.approx(0)
    branch_angle, branch_power = branch_separation(rows)
    assert 2 < branch_angle < 4
    assert branch_power == pytest.approx(0)


class _Clock:
    def __init__(self):
        self.value = 0.0

    def monotonic(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


def test_fpga_trigger_timestamp_anchors_first_adc_sample(monkeypatch):
    samples = iter((101.1000, 101.1002, 101.1004))
    monkeypatch.setattr(continuous.time, "monotonic", lambda: next(samples))

    class Parent:
        frequency_correction = 1.0

    class Scope:
        parent = Parent()
        current_timestamp = 10_000_000_000
        trigger_timestamp = current_timestamp - int(1.05 / 8e-9)

    start, uncertainty, detail = continuous._capture_start_timing(
        Scope(), origin=100.0, host_start=0.01, host_finish=1.08,
        sample_count=1001, dt=0.001,
    )
    assert detail["source"] == "fpga_trigger_timestamp"
    assert start == pytest.approx(0.0501)
    assert uncertainty == pytest.approx(0.000705)
    assert detail["fallback_capture_start_s"] == pytest.approx(0.045)


def test_capture_timing_retains_explicit_host_fallback(monkeypatch):
    monkeypatch.setattr(continuous.time, "monotonic", lambda: 101.1)

    class ScopeWithoutFpgaTimestamps:
        pass

    start, uncertainty, detail = continuous._capture_start_timing(
        ScopeWithoutFpgaTimestamps(), origin=100.0,
        host_start=0.01, host_finish=1.08, sample_count=1001, dt=0.001,
    )
    assert detail["source"] == "host_request_completion_fallback"
    assert detail["fpga_timing_error"].startswith("AttributeError:")
    assert start == pytest.approx(0.045)
    assert uncertainty == pytest.approx(0.036)


class _Future:
    def __init__(self, scope):
        self.scope = scope
        self.polls = 0

    def done(self):
        self.polls += 1
        return self.polls > 1

    def result(self):
        phase = np.linspace(0, 2 * np.pi, 100, endpoint=False)
        return np.sin(phase) * 0.3 + 0.4, np.full(100, 0.2)

    def cancel(self):
        pass


class _Scope:
    duration = 0.1
    sampling_time = 0.001
    decimation = 65536
    average = True

    def single_async(self):
        return _Future(self)


class _RP:
    def __init__(self):
        self.scope = _Scope()
        self.ramps = []

    @contextmanager
    def dual_reference_monitor(self, **_):
        yield self.scope

    def ramp_output_voltage(self, v1, v2, **_):
        self.ramps.append((v1, v2))
        return {"requested_v": [v1, v2], "host_elapsed_s": 0.0}

    def set_output_voltage(self, v1, v2):
        self.ramps.append((v1, v2))

    def set_continuous_sine(self, *, target_axis, fixed_voltage, center, amplitude, frequency_hz):
        return {"frequency_hz": frequency_hz, "center_v": center,
                "amplitude_v": amplitude, "fixed_v": fixed_voltage}


class _PAX:
    def __init__(self, clock):
        self.clock = clock
        self.count = 0
        self.last_raw_record = {}

    def read_fresh_polarization(self):
        self.clock.sleep(0.1)
        self.count += 1
        phase = self.count * 0.2
        self.last_raw_record = {"measurement_counter": self.count}
        return PAXReading(
            timestamp=self.count, theta=0, eta=0, s1=0.2,
            s2=float(np.sqrt(0.96) * np.cos(phase)),
            s3=float(np.sqrt(0.96) * np.sin(phase)), dop=0.98, ptotal=1e-6,
        )


def test_both_mode_simulation_writes_one_trace_and_pax_rate_csv(tmp_path, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(continuous.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(continuous.time, "sleep", clock.sleep)
    monkeypatch.setattr(continuous, "_scope_sleep", clock.sleep)
    config = PolarizationLockConfig(
        cross_sweep_sine_frequency_hz=2.0,
        cross_sweep_sine_center_voltage=0.4,
        cross_sweep_sine_amplitude_voltage=0.3,
        cross_sweep_warmup_cycles=0,
        cross_sweep_recorded_cycles=1,
        cross_sweep_phi1_bias_voltages=(0.2,),
        cross_sweep_phi2_bias_voltages=(0.3,),
        cross_sweep_settle_s=0,
        cross_sweep_bias_ramp_s=0,
        cross_sweep_reference_sample_rate_hz=1000,
        cross_sweep_scope_block_s=0.1,
    )
    csv_path = tmp_path / "data.csv"
    rp = _RP()
    result = continuous.acquire_continuous_cross_sweep(rp, _PAX(clock), config, csv_path, "both")
    assert result["status"] == "completed"
    assert rp.ramps[-1] == (0.0, 0.0)
    with csv_path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert {row["target_actuator"] for row in rows} == {"phi1", "phi2"}
    assert all("pax_raw_json" in row for row in rows)
    traces = list(tmp_path.glob("*.npz"))
    assert traces == [tmp_path / "drive_trace.npz"]
    assert (tmp_path / "acquisition.json").is_file()
    acquisition = json.loads((tmp_path / "acquisition.json").read_text())
    assert acquisition["segments"][0]["transition_ramp"]["requested_v"] == [0.4, 0.3]
    assert acquisition["cleanup_ramp"]["requested_v"] == [0.0, 0.0]
    with np.load(traces[0]) as trace:
        assert set(("time_s", "in1_v", "in2_v", "segment_id", "actuator_id",
                    "bias_index", "cycle_index", "valid_measurement", "metadata_json")) <= set(trace.files)
        assert trace["in1_v"].dtype == np.float32
        assert set(trace["actuator_id"]) == {1, 2}


# Phase 4B consumer boundary: scientific projection remains in this module.
def _assert_same_native(left, right):
    """Exact recursive equality, treating NaNs alike and retaining zero signs."""
    if isinstance(left, dict):
        assert list(left) == list(right)
        for key in left:
            _assert_same_native(left[key], right[key])
    elif isinstance(left, (list, tuple, np.ndarray)):
        assert len(left) == len(right)
        for a, b in zip(left, right):
            _assert_same_native(a, b)
    elif isinstance(left, (float, np.floating)):
        if np.isnan(left):
            assert np.isnan(right)
        else:
            assert left == right
            if left == 0:
                assert np.signbit(left) == np.signbit(right)
    else:
        assert left == right


@pytest.mark.parametrize('name', ['185740_cross-sweep_quick-test', '215311_cross-sweep_phi1-rerun'])
def test_shared_report_matches_legacy_ingestion_on_saved_records(name, monkeypatch):
    import hashlib
    from pathlib import Path
    from polarization_locking.reports import plot_continuous_cross_sweep as report

    path = Path(__file__).resolve().parents[3]/'experiments/polarization_locking/2026-09-29'/name/'data.csv'
    before = (hashlib.sha256(path.read_bytes()).digest(), path.stat().st_mtime_ns)
    actual = (report.load_rows(path), report.summarize(path), report.summary_text(path))

    def legacy_records(run, artifact):
        with (run.directory/artifact).open(newline='') as stream:
            yield from csv.DictReader(stream)

    with monkeypatch.context() as patch:
        patch.setattr(report, 'iter_records', legacy_records)
        expected = (report.load_rows(path), report.summarize(path), report.summary_text(path))
    _assert_same_native(actual, expected)
    assert before == (hashlib.sha256(path.read_bytes()).digest(), path.stat().st_mtime_ns)


@pytest.mark.parametrize('spelling,expected', [('1', True), ('TRUE', True), ('yes', True),
                                             ('false', False), ('0', False), (' yes ', False)])
def test_shared_report_preserves_projection_and_native_fields(tmp_path, spelling, expected):
    from polarization_locking.reports.plot_continuous_cross_sweep import load_rows
    path = tmp_path/'standalone.csv'
    with path.open('w', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['elapsed_s', 'measured_target_v', 's1', 's2', 'analysis_valid', 'unknown'])
        writer.writerow(['-0.00', '-.2', '', 'not numeric', spelling, 'raw\r\ntelemetry'])
    # Complete final row without a newline remains valid, including interrupted prefixes.
    path.write_bytes(path.read_bytes().rstrip(b'\r\n'))
    row, = load_rows(path)
    assert row['elapsed_s'] == 0 and np.signbit(row['elapsed_s'])
    assert row['measured_target_v'] == -.2
    assert np.isnan(row['s1']) and np.isnan(row['s2']) and np.isnan(row['dop'])
    assert row['analysis_valid'] is expected
    assert row['reference_valid'] is row['reference_input_clipped'] is row['pax_valid'] is False
    assert row['unknown'] == 'raw\r\ntelemetry'
    assert list(row)[:6] == ['elapsed_s', 'measured_target_v', 's1', 's2', 'analysis_valid', 'unknown']


@pytest.mark.parametrize('text', ['', 'elapsed_s,target_actuator\n'])
def test_shared_report_empty_inputs_keep_no_rows(tmp_path, text):
    from polarization_locking.reports import plot_continuous_cross_sweep as report
    path = tmp_path/'data.csv'
    path.write_text(text)
    assert report.load_rows(path) == []
    assert report.summarize(path)['rows'] == 0
    assert isinstance(report.summary_text(path), str)


@pytest.mark.parametrize('content,code', [(b'a,a\n1,2\n', 'ambiguous'),
    (b'a,b\n1\n', 'malformed'), (b'a\n"unterminated', 'malformed'),
    (b'a\n\xff\n', 'malformed'), (b'a\n' + b'x,' * (512*1024) + b'x\n', 'oversized')])
def test_shared_report_propagates_reader_diagnostics(tmp_path, content, code):
    from experiment_reader import ReaderError
    from polarization_locking.reports import plot_continuous_cross_sweep as report
    path = tmp_path/'data.csv'
    path.write_bytes(content)
    for read in (report.load_rows, report.summarize, report.summary_text):
        with pytest.raises(ReaderError) as error:
            read(path)
        assert error.value.diagnostic.code == code
        assert error.value.diagnostic.path == path
        assert error.value.diagnostic.line
    assert path.read_bytes() == content


def test_shared_report_missing_downstream_information_keeps_existing_error(tmp_path):
    from polarization_locking.reports.plot_continuous_cross_sweep import summarize
    path = tmp_path/'data.csv'
    path.write_text('elapsed_s\n1\n')
    with pytest.raises(KeyError, match='target_actuator'):
        summarize(path)


@pytest.mark.parametrize('overview', [True, False])
def test_shared_reader_keeps_overview_and_specialized_failure_boundaries(tmp_path, monkeypatch, overview):
    from types import SimpleNamespace
    from experiment_reader import ReaderError
    from pathlib import Path
    from polarization_locking.reports import run_report
    create_run_report = run_report.create_run_report
    text = 'target_actuator,a,a\nphi1,1,2\n'
    paths = SimpleNamespace(directory=tmp_path, csv=tmp_path/'data.csv', pdf=tmp_path/'report.pdf')
    paths.csv.write_text(text)
    if not overview:
        source = Path(__file__).resolve().parents[3]/'experiments/polarization_locking/2026-09-29/215311_cross-sweep_phi1-rerun/data.csv'
        paths.csv.write_bytes(source.read_bytes())
        original_overview = run_report.add_overview

        def corrupt_after_overview(*args):
            original_overview(*args)
            # Simulate corruption between the separate overview/specialized reads.
            # Only a temporary CSV changes; routing still sees target_actuator.
            paths.csv.write_text(text)

        monkeypatch.setattr(run_report, 'add_overview', corrupt_after_overview)
    case = BY_KEY['cross-sweep']
    args = (case, paths, PolarizationLockConfig(), default_options(case), {'status': 'interrupted'})
    if overview:
        with pytest.raises(ReaderError, match='Duplicate CSV headers'):
            create_run_report(*args)
        assert not paths.pdf.exists()
    else:
        result = create_run_report(*args)
        assert result['report_status'] == 'partial'
        assert 'ReaderError' in result['report_error']
        assert paths.pdf.read_bytes().startswith(b'%PDF-')
    assert paths.csv.read_text() == text


def test_shared_report_pdf_and_optional_reference_stay_local(tmp_path):
    import hashlib
    from pathlib import Path
    from types import SimpleNamespace
    from polarization_locking.reports import plot_continuous_cross_sweep as report
    from polarization_locking.reports.run_report import create_run_report
    source = Path(__file__).resolve().parents[3]/'experiments/polarization_locking/2026-09-29/215311_cross-sweep_phi1-rerun/data.csv'
    before = (hashlib.sha256(source.read_bytes()).digest(), source.stat().st_mtime_ns)
    paths = SimpleNamespace(directory=tmp_path, csv=tmp_path/'data.csv', pdf=tmp_path/'report.pdf')
    # No persistent fixture copy: this is a temporary report input, with no metadata prerequisite.
    paths.csv.write_bytes(source.read_bytes())
    missing = report.summarize(paths.csv)
    assert np.isnan(missing['reference_rate_hz'])
    np.savez(tmp_path/'drive_trace.npz', time_s=np.array([0., .125, .25, 1.]), capture_id=[0, 0, 0, 1])
    present = report.summarize(paths.csv)
    assert present['reference_rate_hz'] == 8
    _assert_same_native({k:v for k,v in missing.items() if k!='reference_rate_hz'},
                        {k:v for k,v in present.items() if k!='reference_rate_hz'})
    case = BY_KEY['cross-sweep']
    state = create_run_report(case, paths, PolarizationLockConfig(), default_options(case), {'status': 'interrupted'})
    assert state['report_status'] == 'completed'
    assert paths.pdf.read_bytes().startswith(b'%PDF-')
    assert before == (hashlib.sha256(source.read_bytes()).digest(), source.stat().st_mtime_ns)
