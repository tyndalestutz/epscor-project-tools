"""EOM acquisition data flow and hardware-adapter contract; no physical fitting."""
from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace
from contextlib import contextmanager

import numpy as np
import pytest

from high_speed_polarimeter.config import AcquisitionConfig, Recipe, load_recipe
from high_speed_polarimeter.experiments.eom_sweep import iter_states
from high_speed_polarimeter.hardware import MockAcquisitionSession
from high_speed_polarimeter.io import read_json, write_json
from high_speed_polarimeter.quality import reevaluate
from high_speed_polarimeter.runner import execute


def recipe_for(tmp_path, experiment="single_eom_characterization", **config):
    return Recipe(experiment=experiment, config={"results_directory": str(tmp_path),
                  "points": 3, "settle_s": 0.0, **config}).resolved()


def events_at(directory):
    return [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]


def checks_at(directory):
    return {check["name"]: check for check in read_json(directory / "quality.json")["checks"]}


def test_profiles_and_resolved_defaults(tmp_path):
    root = Path(__file__).resolve().parents[1] / "profiles"
    for path in root.glob('*eom*json'):
        recipe = load_recipe(path)
        saved = tmp_path / path.name
        write_json(saved, recipe.to_dict())
        assert load_recipe(saved).resolved() == recipe.resolved()
        assert "pd_scope_decimation" in recipe.to_dict()["config"]
    assert AcquisitionConfig().pd_input == "in1"


@pytest.mark.parametrize("selected,mapping,expected", [
    ("eom1", ("out1", "out2"), [[0., 0.], [.05, 0.], [.1, 0.]]),
    ("eom2", ("out1", "out2"), [[0., 0.], [0., .05], [0., .1]]),
    ("eom1", ("out2", "out1"), [[0., 0.], [0., .05], [0., .1]]),
])
def test_single_eom_selection_and_channel_mapping(tmp_path, selected, mapping, expected):
    recipe = recipe_for(tmp_path, selected_eom=selected, eom1_output=mapping[0], eom2_output=mapping[1])
    assert [state["rp_commands_v"] for state in iter_states(recipe)] == expected
    state = execute(recipe)
    directory = Path(state["directory"])
    assert state["status"] == "COMPLETED"
    events = events_at(directory)
    assert [event["rp_commands_v"] for event in events if event["kind"] == "command_requested"] == expected
    pd_events = [event for event in events if event["kind"] == "pd_capture"]
    assert {event["point"] for event in pd_events} == {0, 1, 2}
    with (directory / "pd_raw.npy").open('rb') as stream:
        for event in pd_events:
            stream.seek(event["byte_offset"])
            trace = np.load(stream, allow_pickle=False)
            assert stream.tell() == event["byte_end"]
            assert trace.shape == (16384,) and trace.dtype == np.float64
            np.testing.assert_array_equal(trace, (np.arange(16384) % 8 - 4) * .001)
            assert event["sample_count"] == len(trace)
            assert event["requested_s"] < event["received_s"] <= event["elapsed_s"]
    pax = [event for event in events if event["kind"] == "pax_sample"]
    assert len(pax) == 3
    assert all("ptotal" in event["raw_record"] and "dop" in event["raw_record"] for event in pax)
    assert all("timestamp" in event["interface_reading"] for event in pax)
    assert all(event["received_s"] > event["requested_s"] for event in pax)
    quality = checks_at(directory)
    assert all(check["status"] == "PASS" for name, check in quality.items() if name != "scientific_quality")
    assert quality["scientific_quality"]["status"] == "UNKNOWN"
    initial = (directory / "quality.json").read_bytes()
    original = {name: (directory / name).read_bytes() for name in ("run.json", "recipe.json", "events.jsonl", "pd_raw.npy")}
    reevaluate(directory)
    assert (directory / "quality.json").read_bytes() == initial
    assert original == {name: (directory / name).read_bytes() for name in original}
    assert state["provenance"]["dependencies"]["numpy"]


def test_forward_reverse_repeats_pax_count_and_minimum_duration(tmp_path):
    recipe = recipe_for(tmp_path, bidirectional=True, repeats=2, pax_samples_per_point=2, pd_duration_s=.02)
    plan = list(iter_states(recipe))
    assert [item["eom_commands_v"][0] for item in plan] == [0, .05, .1, .1, .05, 0] * 2
    assert [item["repeat"] for item in plan] == [0] * 6 + [1] * 6
    state = execute(recipe)
    directory = Path(state["directory"])
    assert state["status"] == "COMPLETED"
    events = events_at(directory)
    for point in range(12):
        captures = [event for event in events if event["kind"] == "pd_capture" and event["point"] == point]
        assert len(captures) == 3
        assert sum(event["sample_count"] * event["sampling_time_s"] for event in captures) >= .02
        assert len([event for event in events if event["kind"] == "pax_sample" and event["point"] == point]) == 2
    assert read_json(directory / "quality.json")["status"] == "UNKNOWN"


@pytest.mark.parametrize("config,expected", [
    ({"eom1_commands_v": [0., .1], "eom2_commands_v": [.02, .03]}, [[0., .02], [0., .03], [.1, .02], [.1, .03]]),
    ({"states_v": [[.1, .02], [0., 0.], [.1, .02]]}, [[.1, .02], [0., 0.], [.1, .02]]),
])
def test_dual_grid_and_ordered_pairs(tmp_path, config, expected):
    recipe = recipe_for(tmp_path, "dual_eom_characterization", repeats=2, **config)
    state = execute(recipe)
    directory = Path(state["directory"])
    events = events_at(directory)
    assert [event["eom_commands_v"] for event in events if event["kind"] == "command_requested"] == expected * 2
    assert [event["rp_commands_v"] for event in events if event["kind"] == "command_requested"] == expected * 2
    assert {event["point"] for event in events if event["kind"] == "pd_capture"} == set(range(len(expected) * 2))
    assert read_json(directory / "quality.json")["status"] == "UNKNOWN"


@pytest.mark.parametrize("change", [
    {"pd_duration_s": 0}, {"pax_samples_per_point": 0}, {"points": True},
    {"points": 1}, {"repeats": 0}, {"selected_eom": "eom3"},
    {"eom2_output": "out1"}, {"stop_command_v": 1.01},
    {"stop_command_v": float('nan')}, {"pd_scope_decimation": 3},
    {"rp_output_min_voltage": .01}, {"pd_input": "in3"},
    {"states_v": [[0., True]]}, {"states_v": []},
    {"pd_scope_timeout_s": .001}, {"unknown": 1},
])
def test_invalid_acquisition_recipe_before_hardware(tmp_path, change):
    called = []
    with pytest.raises(ValueError):
        execute(Recipe(experiment="single_eom_characterization", config={"results_directory": str(tmp_path), **change}), session_factory=lambda: called.append(True))
    assert not called and not list(tmp_path.iterdir())


@pytest.mark.parametrize("failure", ["empty_pd", "short_pd", "pd_error", "pd_init", "pax_error", "command_error"])
def test_mandatory_failures_and_cleanup(tmp_path, failure):
    config = recipe_for(tmp_path).acquisition_config()

    class Session(MockAcquisitionSession):
        closed = False

        def connect(self):
            if failure == "pd_init":
                raise RuntimeError("PD not initialized")
            super().connect()

        def command(self, values):
            if failure == "command_error":
                raise RuntimeError("command failed")
            return super().command(values)

        def capture_pd(self):
            if failure == "empty_pd":
                return np.array([])
            if failure == "short_pd":
                return np.array([-1.0])
            if failure == "pd_error":
                raise RuntimeError("PD capture failed")
            return super().capture_pd()

        def read_pax(self):
            if failure == "pax_error" and self.pax_index > 0:
                raise RuntimeError("PAX read failed")
            return super().read_pax()

        def disconnect(self):
            self.closed = True
            super().disconnect()

    session = Session(config)
    state = execute(recipe_for(tmp_path), session_factory=lambda: session)
    directory = Path(state["directory"])
    assert state["status"] == "FAILED" and session.closed
    assert read_json(directory / "quality.json")["status"] == "FAIL"
    assert state["quality_evaluation_status"] == "COMPLETED"
    assert reevaluate(directory).status == "FAIL"
    if failure in {"empty_pd", "short_pd"}:
        assert (directory / "pd_raw.npy").stat().st_size > 0
        assert checks_at(directory)["pd_raw_integrity"]["status"] == "FAIL"


def test_interruption_retains_pd_and_command_evidence(tmp_path):
    recipe = recipe_for(tmp_path)

    class Session(MockAcquisitionSession):
        count = 0
        def capture_pd(self):
            self.count += 1
            if self.count == 3:
                raise KeyboardInterrupt()
            return super().capture_pd()

    session = Session(recipe.acquisition_config())
    state = execute(recipe, session_factory=lambda: session)
    directory = Path(state["directory"])
    assert state["status"] == "INTERRUPTED" and session.commands == [0., 0.]
    assert (directory / "pd_raw.npy").stat().st_size > 0
    assert checks_at(directory)["completed_points"]["measured_value"] == 1
    assert read_json(directory / "quality.json")["status"] == "FAIL"
    assert reevaluate(directory).status == "FAIL"


@pytest.mark.parametrize("anomaly", ["stale_pax", "nonfinite_pd", "nonfinite_pax"])
def test_anomalies_preserved_and_structurally_flagged(tmp_path, anomaly):
    recipe = recipe_for(tmp_path)

    class Session(MockAcquisitionSession):
        def capture_pd(self):
            trace = super().capture_pd()
            if anomaly == "nonfinite_pd":
                trace[0] = np.nan
            return trace

        def read_pax(self):
            value = super().read_pax()
            if anomaly == "stale_pax":
                value["raw_record"]["timestamp"] = 1.0
            if anomaly == "nonfinite_pax":
                value["raw_record"]["timestamp"] = float('nan')
            return value

    state = execute(recipe, session_factory=lambda: Session(recipe.acquisition_config()))
    directory = Path(state["directory"])
    assert state["status"] == "COMPLETED"
    assert read_json(directory / "quality.json")["status"] == "FAIL"
    if anomaly == "nonfinite_pax":
        assert any(event["raw_record"]["timestamp"] == {"nonfinite": "nan"} for event in events_at(directory) if event["kind"] == "pax_sample")
    if anomaly == "nonfinite_pd":
        with (directory / "pd_raw.npy").open('rb') as stream:
            assert np.isnan(np.load(stream, allow_pickle=False)[0])


def test_mock_deterministic_and_offline_detects_corruption(tmp_path):
    recipe = recipe_for(tmp_path)
    first, second = execute(recipe), execute(recipe)
    a, b = Path(first["directory"]), Path(second["directory"])
    assert (a / "events.jsonl").read_bytes() == (b / "events.jsonl").read_bytes()
    assert (a / "pd_raw.npy").read_bytes() == (b / "pd_raw.npy").read_bytes()
    with (a / "events.jsonl").open('a') as stream:
        stream.write('{"partial":')
    assert reevaluate(a).status == "FAIL"
    assert checks_at(a)["event_log"]["status"] == "FAIL"
    raw_path = b / "pd_raw.npy"
    raw_path.write_bytes(raw_path.read_bytes()[:-4])
    assert reevaluate(b).status == "FAIL"
    assert checks_at(b)["pd_raw_integrity"]["status"] == "FAIL"


def test_hardware_confirmation_before_allocation(tmp_path):
    with pytest.raises(ValueError, match="output_map_confirmed"):
        execute(recipe_for(tmp_path), mode="hardware")
    assert not list(tmp_path.iterdir())


def install_fake_legacy(monkeypatch, calls, cleanup_failure=False):
    # Real controller modules are imported, but their classes are replaced before
    # BenchSession construction; no drivers or instrument connections are used.
    import polarization_locking.hardware.rp_interface as rp_module
    import polarization_locking.hardware.pax_interface as pax_module

    class Scope:
        input1 = "in1"
        duration = 16384 * 8e-9 * 64
        sampling_time = 8e-9 * 64
        decimation = 64
        average = True
        trace_average = 1
        def setup(self, **kwargs):
            calls.append(("scope_setup", kwargs))
            for key, value in kwargs.items():
                setattr(self, key, value)
        def single(self, **kwargs):
            calls.append(("scope_single", kwargs))
            return [np.arange(16384, dtype=np.float64) * -.001]

    class RP:
        def __init__(self, config):
            calls.append(("rp_config", vars(config)))
            self.p = SimpleNamespace(rp=SimpleNamespace(scope=Scope()))
            self.asg1, self.asg2 = SimpleNamespace(offset=0.), SimpleNamespace(offset=0.)
        def connect(self):
            calls.append(("rp_connect",))
        @contextmanager
        def photodiode_monitor(self, **kwargs):
            self.p.rp.scope.input1 = kwargs['input_channel']
            calls.append(("monitor_enter", kwargs))
            try:
                def summary():
                    pytest.fail("Must retain scope trace instead of calling summary helper")
                yield summary
            finally:
                calls.append(("monitor_exit",))
        def set_output_voltage(self, v1, v2):
            calls.append(("command", v1, v2))
            self.asg1.offset, self.asg2.offset = v1, v2
        def disconnect(self):
            calls.append(("rp_disconnect",))
            if cleanup_failure:
                raise RuntimeError("RP cleanup failed")

    class PAX:
        index = 0
        _daemon_log_path = None
        last_raw_record = None
        def __init__(self, config):
            calls.append(("pax_config", asdict(config)))
        def connect(self):
            calls.append(("pax_connect",))
        def read_polarization(self):
            self.index += 1
            self.last_raw_record = {"timestamp": float(self.index), "ptotal": -.001, "dop": .01}
            return pax_module.PAXReading(timestamp=float(self.index), theta=0., eta=0.,
                                         s1=1., s2=0., s3=0., dop=.01, ptotal=-.001)
        def disconnect(self):
            calls.append(("pax_disconnect",))

    monkeypatch.setattr(rp_module, 'RPController', RP)
    monkeypatch.setattr(pax_module, 'PAXController', PAX)


def test_bench_adapter_reuses_controllers_raw_capture_and_provenance(tmp_path, monkeypatch):
    calls = []
    install_fake_legacy(monkeypatch, calls)
    recipe = recipe_for(tmp_path, output_map_confirmed=True,
                        voltage_chain="Test fixture only", pax_reference_plane="Test plane",
                        pd_input="in2", pd_fpga_average=False)
    state = execute(recipe, mode="hardware")
    directory = Path(state["directory"])
    assert state["status"] == "COMPLETED" and state["cleanup_status"] == "COMPLETED"
    assert state["mode"] == "hardware" and state["hardware"]["pd"]["input"] == "in2"
    assert state["hardware"]["pd"]["fpga_average"] is False
    assert len([call for call in calls if call[0] == 'scope_single']) == 6
    assert [call for call in calls if call[0] == 'command'] == [('command', 0., 0.), ('command', .05, 0.), ('command', .1, 0.)]
    assert ('monitor_exit',) in calls and ('rp_disconnect',) in calls and ('pax_disconnect',) in calls
    assert any(source['root'].endswith('polarization_locking') for source in state["provenance"]["sources"])
    assert any(source['root'].endswith('pax1000.toml') for source in state["provenance"]["sources"])
    assert 'pyrpl' in state["provenance"]["dependencies"]
    pax = next(event for event in events_at(directory) if event['kind'] == 'pax_sample')
    assert pax['raw_record']['ptotal'] == -.001 and pax['raw_record']['dop'] == .01
    assert pax['interface_reading']['adc_min'] == {"nonfinite": "nan"}
    assert 's0' not in pax['interface_reading']
    assert read_json(directory / 'quality.json')['status'] == 'UNKNOWN'
    assert 'mode: hardware' in (directory / 'report.md').read_text()


def test_bench_cleanup_attempts_both_controllers_on_failure(tmp_path, monkeypatch):
    calls = []
    install_fake_legacy(monkeypatch, calls, cleanup_failure=True)
    recipe = recipe_for(tmp_path, output_map_confirmed=True,
                        voltage_chain="Test fixture", pax_reference_plane="Test plane")
    state = execute(recipe, mode="hardware")
    assert state["status"] == "COMPLETED" and state["cleanup_status"] == "FAILED"
    assert ('pax_disconnect',) in calls
    assert read_json(Path(state["directory"]) / 'quality.json')['status'] == 'FAIL'


def test_injected_hardware_session_cannot_use_mock_provenance(tmp_path):
    class Session(MockAcquisitionSession):
        mode = "hardware"
        connected = False
        def connect(self):
            self.connected = True
    recipe = recipe_for(tmp_path)
    session = Session(recipe.acquisition_config())
    state = execute(recipe, session_factory=lambda: session)
    assert state["status"] == "FAILED" and not session.connected
    assert "Session mode" in state["error"]


def test_cli_preserves_config_when_overriding_output_and_blocks_unconfirmed_bench(tmp_path):
    import subprocess
    import sys
    profile = Path(__file__).resolve().parents[1] / "profiles" / "single-eom-repeated-example.json"
    script = Path(__file__).resolve().parents[2]
    dry = subprocess.run([sys.executable, '-m', 'high_speed_polarimeter', '--dry-run',
                          '--profile', str(profile), '--results-directory', str(tmp_path)],
                         cwd=script, capture_output=True, text=True)
    assert dry.returncode == 0, dry.stderr
    config = json.loads(dry.stdout)['config']
    assert config['results_directory'] == str(tmp_path)
    assert config['selected_eom'] == 'eom2' and config['bidirectional'] is True and config['repeats'] == 2
    bench = subprocess.run([sys.executable, '-m', 'high_speed_polarimeter', '--run',
                            '--profile', str(profile), '--results-directory', str(tmp_path)],
                           cwd=script, capture_output=True, text=True)
    assert bench.returncode != 0 and 'output_map_confirmed' in bench.stderr
    assert not list(tmp_path.iterdir())
