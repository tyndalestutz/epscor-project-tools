"""Reproducible structural acquisition checks only; scientific assessment remains UNKNOWN."""
import hashlib
import json
import math
from pathlib import Path

from .experiments.eom_sweep import iter_states
from .io import read_json
from .quality import QualityCheck, QualityStatus, RunQuality


def artifact_evidence(directory):
    evidence = {"evaluator_source": {"sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}}
    for name in ("recipe.json", "acquisition.json", "events.jsonl", "pd_raw.npy"):
        path = directory / name
        if path.exists():
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            evidence[name] = {"sha256": digest.hexdigest()}
        else:
            evidence[name] = {"missing": True}
    # run.json receives postprocessing statuses after this evaluator runs. Hash
    # only stable execution/cleanup evidence so online/offline results agree.
    path = directory / "run.json"
    if path.exists():
        state = read_json(path)
        keys = ("status", "mode", "cleanup_status", "error", "cleanup_error")
        selected = {key: state[key] for key in keys if key in state}
        evidence["run.json"] = {"hash_scope": list(keys), "sha256": hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest()}
    else:
        evidence["run.json"] = {"missing": True}
    for name in ("config.py", "experiments/eom_sweep.py", "quality.py"):
        evidence["evaluator_source:" + name] = {"sha256": hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()}
    return evidence


def evaluate_eom(directory, recipe):
    import numpy as np
    directory = Path(directory)
    config = recipe.acquisition_config()
    planned = list(iter_states(recipe))
    expected = list(range(len(planned)))
    evidence = artifact_evidence(directory)
    checks = []

    def check(name, passed, message, value=None):
        checks.append(QualityCheck(name, QualityStatus.PASS if passed else QualityStatus.FAIL,
                                   message, measured_value=value, evidence=evidence))

    events, parse_errors = [], []
    path = directory / "events.jsonl"
    if path.exists():
        with path.open(encoding="utf-8") as stream:
            for index, line in enumerate(stream, 1):
                try:
                    value = json.loads(line)
                    if not isinstance(value, dict) or "kind" not in value:
                        raise ValueError("not an event object")
                    events.append(value)
                except (ValueError, TypeError) as exc:
                    parse_errors.append(f"line {index}: {exc}")
    else:
        parse_errors.append("events.jsonl missing")
    check("event_log", not parse_errors, "Event parse errors are retained explicitly.", parse_errors)

    def selected(kind):
        return [event for event in events if event["kind"] == kind]

    requests = selected("command_requested")
    check("planned_commands", len(requests) == len(planned) and all(
        all(actual.get(key) == value for key, value in planned_state.items())
        for actual, planned_state in zip(requests, planned)), "Command order, channel mapping, directions and repeats must match the resolved recipe.", len(requests))
    acknowledgements = selected("command_completed")
    check("command_acknowledgements", [event.get("point") for event in acknowledgements] == expected and all(
        event.get("acknowledgement", {}).get("acknowledged") is True for event in acknowledgements),
        "Each planned state requires a completed command acknowledgement; this is not a measured EOM voltage.", len(acknowledgements))
    check("completed_points", [event.get("point") for event in selected("point_completed")] == expected,
          "Every planned point and repeat must complete.", len(selected("point_completed")))

    metadata = {}
    try:
        metadata = read_json(directory / "acquisition.json")
    except (OSError, ValueError):
        pass
    pd_config = metadata.get("hardware", {}).get("pd", {})
    dt = pd_config.get("sampling_time_s")
    block_size = pd_config.get("sample_count")
    valid_pd_config = type(dt) in (int, float) and math.isfinite(dt) and dt > 0 and type(block_size) is int and block_size > 0
    check("pd_initialization", valid_pd_config, "Stored scope settings must specify positive buffer length and sample spacing.")
    captures_per_point = math.ceil(config.pd_duration_s / (dt * block_size)) if valid_pd_config else None
    captures = selected("pd_capture")
    # Check sequential association, not merely aggregate counts. This also makes
    # missing settling events or misplaced PAX/PD records visible offline.
    expected_order = [("acquisition_started", None), ("pax_baseline", None)]
    for point in expected:
        expected_order.extend([(kind, point) for kind in ("command_requested", "command_completed", "settled")])
        expected_order.extend([("pd_capture", point)] * (captures_per_point or 0))
        expected_order.extend([("pax_sample", point)] * config.pax_samples_per_point)
        expected_order.append(("point_completed", point))
    expected_order.append(("acquisition_completed", None))
    check("point_sequence", [(event["kind"], event.get("point")) for event in events] == expected_order,
          "At each state: command acknowledgement, configured settling, all PD buffers, then PAX records; streams remain sequential.")
    settled = selected("settled")
    check("settling_requests", len(settled) == len(planned) and all(event.get("requested_settle_s") == config.settle_s for event in settled),
          "Configured settling delays must be recorded; this does not establish physical settling.")
    expected_buffers = [(point, capture) for point in expected for capture in range(captures_per_point or 0)]
    check("pd_point_coverage", valid_pd_config and [(event.get("point"), event.get("capture")) for event in captures] == expected_buffers,
          "Mandatory full PD buffers at every state; captured nominal duration covers the requested minimum.", len(captures))
    raw_errors, next_offset = [], 0
    raw_path = directory / "pd_raw.npy"
    if not raw_path.exists():
        raw_errors.append("pd_raw.npy missing")
    else:
        with raw_path.open("rb") as stream:
            for index, event in enumerate(captures):
                try:
                    offset = event["byte_offset"]
                    if type(offset) is not int or offset != next_offset:
                        raise ValueError("noncontiguous or invalid buffer offset")
                    stream.seek(offset)
                    trace = np.load(stream, allow_pickle=False)
                    next_offset = stream.tell()
                    if next_offset != event["byte_end"]:
                        raise ValueError("buffer byte boundary mismatch")
                    if trace.ndim != 1 or trace.dtype != np.dtype('float64') or not trace.size or trace.size != event["sample_count"] or trace.size != block_size:
                        raise ValueError("empty, truncated or unexpected PD buffer")
                    if event.get("scope_settings") != pd_config:
                        raise ValueError("effective scope settings changed during acquisition")
                    if event.get("sampling_time_s") != dt:
                        raise ValueError("PD sample spacing metadata mismatch")
                    if not np.all(np.isfinite(trace)):
                        raise ValueError("nonfinite PD values (retained without replacement)")
                except (OSError, ValueError, TypeError, KeyError, EOFError) as exc:
                    raw_errors.append(f"buffer {index}: {exc}")
            if next_offset != raw_path.stat().st_size:
                raw_errors.append("unreferenced or partial trailing raw bytes; retained for recovery")
    check("pd_raw_integrity", bool(captures) and not raw_errors, "Each stored PD array must match its event reference; anomalous values remain stored.", raw_errors)

    pax = selected("pax_sample")
    expected_pax = [(point, sample) for point in expected for sample in range(config.pax_samples_per_point)]
    check("pax_point_coverage", [(event.get("point"), event.get("sample")) for event in pax] == expected_pax and all(event.get("raw_record") and event.get("interface_reading") for event in pax),
          "Each state requires all configured raw/reference PAX records.", len(pax))
    device_records = selected("pax_baseline") + pax
    timestamps = [event.get("raw_record", {}).get("timestamp") for event in device_records]
    finite_stamps = all(type(stamp) in (int, float) and math.isfinite(stamp) for stamp in timestamps)
    check("pax_timestamp_advancement", len(selected("pax_baseline")) == 1 and bool(pax) and finite_stamps and all(b > a for a, b in zip(timestamps, timestamps[1:])),
          "Native PAX timestamps must advance from the stored pre-sweep baseline; this does not prove synchronization or integration wholly after a command.")
    host_valid, previous = bool(events), -math.inf
    for event in events:
        stamp = event.get("elapsed_s")
        if type(stamp) not in (int, float) or not math.isfinite(stamp) or stamp < 0 or stamp < previous:
            host_valid = False
            continue
        previous = stamp
        if "requested_s" in event or "received_s" in event:
            start, end = event.get("requested_s"), event.get("received_s")
            host_valid &= type(start) in (int, float) and type(end) in (int, float) and math.isfinite(start) and math.isfinite(end) and 0 <= start <= end <= stamp
    check("host_timestamps", host_valid, "Host elapsed timestamps must be monotonic with ordered request/receive windows.")
    errors = [event.get("error") for event in selected("acquisition_error")]
    try:
        state = read_json(directory / "run.json")
    except (OSError, ValueError):
        state = {}
    errors.extend(state[key] for key in ("error", "cleanup_error") if key in state)
    check("execution_and_cleanup", not errors and state.get("status") == "COMPLETED" and state.get("cleanup_status") == "COMPLETED" and len(selected("acquisition_completed")) == 1,
          "Acquisition and cleanup failures remain separate from scientific assessment.", errors)
    checks.append(QualityCheck("scientific_quality", QualityStatus.UNKNOWN,
                              "Structural checks cannot establish EOM scientific validity; no DoP/drift/hysteresis/modulation thresholds or calibration applied.", evidence=evidence))
    return RunQuality(tuple(checks), "eom_acquisition_structure", "1")
