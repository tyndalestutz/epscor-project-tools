"""Small, versioned JSON recipes and shared input validation (no hardware imports)."""
from dataclasses import asdict
import json
import math
from pathlib import Path
from typing import get_args, get_origin, get_type_hints, Union

from .catalog import BY_KEY, default_options
from .config import PolarizationLockConfig

SCHEMA_VERSION = 1
TYPES = get_type_hints(PolarizationLockConfig)


def coerce(value, kind):
    """Strict JSON types, including nullable calibration values and numeric tuples."""
    origin, args = get_origin(kind), get_args(kind)
    if origin is Union:
        if value is None and type(None) in args:
            return None
        return coerce(value, next(t for t in args if t is not type(None)))
    if origin is tuple:
        if not isinstance(value, (list, tuple)) or not value:
            raise ValueError("Enter a nonempty JSON array, e.g. [0.2, 0.1]")
        if args[-1] is not Ellipsis and len(value) != len(args):
            raise ValueError(f"Expected {len(args)} entries")
        return tuple(coerce(v, args[0]) for v in value)
    if kind is float and type(value) in (int, float):
        if not math.isfinite(value):
            raise ValueError("Value must be finite")
        return float(value)
    if type(value) is not kind:
        raise ValueError(f"Expected {kind.__name__}")
    return value


def parse_value(text: str, kind):
    if kind is str:
        return text
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("Use a number, true/false, null, or a JSON array") from exc
    return coerce(value, kind)


def validate(config, case, options):
    values = asdict(config)
    for name, value in values.items():
        coerce(value, TYPES[name])
        if value is None or isinstance(value, (str, bool, tuple)):
            continue
        if name.endswith(("_s", "_hz", "_nm")) and value < 0:
            raise ValueError(f"{name} cannot be negative")
        if name.endswith(("_points", "_count", "_repeats", "_intervals", "_retries", "_per_step")) and value < 1:
            raise ValueError(f"{name} must be at least 1")
    if not config.rp_output_min_voltage == 0.0 < config.rp_output_max_voltage <= 1.0:
        raise ValueError("RP range must start at 0 and end at or below 1 V (cleanup requires zero)")
    for axis in ("phi1", "phi2"):
        for suffix in ("v_lambda", "actuator_volts_per_rp_volt"):
            value = values[f"{axis}_{suffix}"]
            if value is not None and value <= 0:
                raise ValueError(f"{axis}_{suffix} must be positive or null")
    if not 0 <= config.minimum_dop <= 1:
        raise ValueError("minimum_dop must be within 0..1")
    if not 1 <= config.pax_port <= 65535:
        raise ValueError("pax_port must be within 1..65535")
    for name, value in values.items():
        if name.endswith(("_bias_voltage", "_rp_voltage")) and not 0 <= value <= config.rp_output_max_voltage:
            raise ValueError(f"{name} exceeds the RP range")
    expected = default_options(case)
    if options.keys() != expected.keys():
        raise ValueError(f"Options must be: {', '.join(expected)}")
    for name, value in options.items():
        coerce(value, type(expected[name]))
    if "axis" in options and options["axis"] not in {"phi1", "phi2"}:
        raise ValueError("axis must be phi1 or phi2")
    if "duration_s" in options and options["duration_s"] <= 0:
        raise ValueError("duration_s must be positive")
    if "report" in options:
        allowed = {"pdf", "both"} if (case.report.startswith("analyze_") or case.report == "power") else {"pdf"}
        if options["report"] not in allowed:
            raise ValueError(f"report must be one of {', '.join(sorted(allowed))}")
    if case.target:
        if options["target_mode"] not in {"current", "explicit"}:
            raise ValueError("target_mode must be current or explicit")
        if options["target_mode"] == "explicit":
            if config.target_u is None or config.target_v is None:
                raise ValueError("Explicit target requires target_u and target_v (radians)")
            if not 0 <= config.target_v <= math.pi:
                raise ValueError("target_v must be within 0..pi")
    if case.key == "sweep":
        if not 0 <= config.sweep_start_rp_v < config.sweep_stop_rp_v <= config.rp_output_max_voltage:
            raise ValueError("Sweep must satisfy 0 <= start < stop <= RP maximum")
        if config.sweep_points < 2:
            raise ValueError("sweep_points must be at least 2")
    for name in case.config_names():
        value = values[name]
        if isinstance(value, (int, float)) and ("sample_period_s" in name or "step_voltage" in name or name.endswith("step_rp_v")) and value <= 0:
            raise ValueError(f"{name} must be positive")
    # Full-period scans must fit before any instrument is connected. An explicit
    # voltage sweep does not depend on the unmeasured terminal transfer gain.
    if case.key not in {"live", "pax-path-hold", "first-npbs-d-isolation"} and not config.phase_output_map_confirmed:
        raise ValueError("Set phase_output_map_confirmed after checking the output assignment")
    if case.key not in {"live", "sweep", "pax-path-hold", "first-npbs-d-isolation"}:
        axes = ("phi1", "phi2")
        if case.key in {"bidirectional-sweep", "single-axis-pid"}:
            axes = (options["axis"],)
        elif case.setup.startswith("PAX at first-NPBS D"):
            axes = ("phi1",)
        elif case.key in {"phi2-path-test", "power-balance"}:
            axes = ("phi2",)
        for axis in axes:
            voltage = getattr(config, f"{axis}_v_lambda")
            gain = getattr(config, f"{axis}_actuator_volts_per_rp_volt")
            if voltage is None or gain is None:
                raise ValueError(f"{axis} needs a configured terminal V_lambda and measured transfer gain")
            if voltage / gain > config.rp_output_max_voltage:
                raise ValueError(f"One {axis} V_lambda needs {voltage / gain:.4f} RP V, above the configured limit")


def recipe(case, config, options):
    return {"schema_version": SCHEMA_VERSION, "test": case.key, "options": dict(options), "config": asdict(config)}


def write_json(path, data):
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def load_recipe(path):
    data = json.loads(Path(path).expanduser().read_text())
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported recipe schema_version")
    if data.get("test") not in BY_KEY:
        raise ValueError("Unknown test in recipe")
    case = BY_KEY[data["test"]]
    raw = data.get("config", {})
    if not isinstance(raw, dict) or set(raw) - TYPES.keys():
        raise ValueError("Recipe has unknown configuration fields")
    config = PolarizationLockConfig(**{k: coerce(v, TYPES[k]) for k, v in raw.items()})
    options = default_options(case)
    if not isinstance(data.get("options", {}), dict):
        raise ValueError("Recipe options must be an object")
    options.update(data.get("options", {}))
    # Migrate the refactor's temporary defaults without changing recorded files.
    old_results = Path(__file__).resolve().parent / "results"
    if Path(config.results_directory).expanduser().resolve() == old_results:
        config.results_directory = PolarizationLockConfig().results_directory
    if options.get("report") in {"none", "png"}:
        options["report"] = "both" if options["report"] == "png" else "pdf"
    validate(config, case, options)
    return case, config, options
