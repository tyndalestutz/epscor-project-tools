"""Append raw scope arrays and compact timing/state events; no scientific analysis."""
import json
import math
from pathlib import Path

from ..io import write_json


def json_evidence(value):
    """Preserve nonfinite telemetry explicitly in strict JSON, rather than dropping it."""
    if isinstance(value, dict):
        return {str(key): json_evidence(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_evidence(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return {"nonfinite": str(value)}
    if hasattr(value, "item"):
        return json_evidence(value.item())
    return value


def acquire_states(session, recipe, directory, states, expected_points):
    import numpy as np
    directory = Path(directory)
    config = recipe.acquisition_config()
    hardware = session.describe()
    metadata = {"schema_version": 1, "experiment": recipe.experiment,
                "mode": hardware["mode"], "hardware": hardware,
                "timing_origin": session.origin(),
                "timing": "Host elapsed seconds relative to monotonic origin; PD/PAX are sequential. Sample spacing is scope-reported, not host time. Device PAX timestamps remain in their native clock/units.",
                "pd_format": "pd_raw.npy is a sequence of np.save arrays (float64 signed input volts); each pd_capture event supplies byte_offset/byte_end. No pickle, normalization, trimming or host smoothing.",
                "pax_format": "raw_record is unmodified daemon telemetry (nonfinite tagged); interface_reading s1/s2/s3 are angle-derived unit direction, not absolute Stokes. No S0 inferred.",
                "command_units": {"eom_commands_v": "RP command volts, EOM1/EOM2 identity order", "rp_commands_v": "RP command volts, OUT1/OUT2 order", "asg_offset_settings_v": "ASG register/settings readback; not measured delivered voltage"},
                "expected_points": expected_points}
    write_json(directory / "acquisition.json", metadata)
    with (directory / "events.jsonl").open("w", encoding="utf-8") as events, (directory / "pd_raw.npy").open("wb") as raw:
        def emit(kind, **values):
            record = {"kind": kind, "elapsed_s": session.clock(), **values}
            events.write(json.dumps(json_evidence(record), allow_nan=False, sort_keys=True) + "\n")
            events.flush()

        point = None
        try:
            pd = hardware["pd"]
            dt = pd["sampling_time_s"]
            if not math.isfinite(dt) or dt <= 0 or pd["sample_count"] < 1:
                raise RuntimeError("Invalid PD scope initialization metadata")
            # Request complete fixed-length buffers; preserve overshoot and gaps.
            captures = math.ceil(config.pd_duration_s / (dt * pd["sample_count"]))
            emit("acquisition_started", captures_per_point=captures)
            requested = session.clock()
            try:
                baseline = session.read_pax()
            except BaseException:
                if hasattr(session, "pax_error_evidence"):
                    emit("pax_error_evidence", point=None, requested_s=requested,
                         received_s=session.clock(), raw_record=session.pax_error_evidence())
                raise
            emit("pax_baseline", requested_s=requested, received_s=session.clock(), **baseline)
            for state in states:
                point = state["point"]
                emit("command_requested", **state)
                command_started = session.clock()
                acknowledgement = session.command(state["rp_commands_v"])
                emit("command_completed", point=point, requested_s=command_started,
                     received_s=session.clock(), acknowledgement=acknowledgement)
                if acknowledgement.get("acknowledged") is not True:
                    raise RuntimeError("EOM command not acknowledged")
                settle_started = session.clock()
                session.sleep(config.settle_s)
                emit("settled", point=point, requested_s=settle_started,
                     received_s=session.clock(), requested_settle_s=config.settle_s)
                for capture in range(captures):
                    requested = session.clock()
                    trace = np.asarray(session.capture_pd(), dtype=np.float64)
                    received = session.clock()
                    effective_pd = session.describe()["pd"]
                    offset = raw.tell()
                    np.save(raw, trace, allow_pickle=False)
                    raw.flush()  # preserve a completed buffer before writing its reference
                    emit("pd_capture", point=point, capture=capture,
                         requested_s=requested, received_s=received,
                         byte_offset=offset, byte_end=raw.tell(), sample_count=int(trace.size),
                         sampling_time_s=effective_pd["sampling_time_s"], units="V", source="RP input voltage",
                         scope_settings=effective_pd)
                    if trace.ndim != 1 or trace.size != pd["sample_count"] or not trace.size:
                        raise RuntimeError("Missing/incomplete PD buffer; raw returned data retained")
                for sample in range(config.pax_samples_per_point):
                    requested = session.clock()
                    try:
                        reading = session.read_pax()
                    except BaseException:
                        if hasattr(session, "pax_error_evidence"):
                            emit("pax_error_evidence", point=point, requested_s=requested, received_s=session.clock(), raw_record=session.pax_error_evidence())
                        raise
                    emit("pax_sample", point=point, sample=sample, requested_s=requested,
                         received_s=session.clock(), **reading)
                    if not reading.get("raw_record"):
                        raise RuntimeError("PAX returned no raw measurement record")
                emit("point_completed", point=point)
            emit("acquisition_completed")
        except BaseException as exc:
            emit("acquisition_error", point=point, error=f"{type(exc).__name__}: {exc}")
            raise
    return metadata
