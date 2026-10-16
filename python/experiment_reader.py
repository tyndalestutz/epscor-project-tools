"""Read native experiment evidence without scientific processing or application imports.

See docs/engineering/experiment-reader.md for bounds, coverage and consistency.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
import json
from pathlib import Path

METADATA_BYTES = 1024 * 1024
HEADER_BYTES = 64 * 1024
RECORD_BYTES = 1024 * 1024
MAX_ENTRIES = 256


@dataclass(frozen=True)
class Diagnostic:
    code: str
    path: Path
    message: str
    line: int | None = None

    def __str__(self):
        location = f"{self.path}:{self.line}" if self.line else str(self.path)
        return f"{location}: {self.code}: {self.message}"


class ReaderError(ValueError):
    def __init__(self, diagnostic):
        self.diagnostic = diagnostic
        super().__init__(str(diagnostic))


@dataclass
class RunInfo:
    directory: Path
    family: str = "unknown"
    artifacts: tuple[str, ...] = ()
    metadata: dict = field(default_factory=dict)
    headers: dict = field(default_factory=dict)
    versions: dict = field(default_factory=dict)
    outcomes: dict = field(default_factory=lambda: dict.fromkeys((
        "execution", "cleanup", "quality_evaluation", "scientific_verdict", "reporting",
    )))
    diagnostics: list[Diagnostic] = field(default_factory=list)

    def __post_init__(self):
        self.directory = Path(self.directory).expanduser().resolve()


def _error(code, path, message, line=None):
    return ReaderError(Diagnostic(code, path, message, line))


def _artifact(run, name):
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts:
        raise _error("unsupported", run.directory, "Select a relative artifact inside the supplied location")
    path = run.directory / relative
    if not path.resolve().is_relative_to(run.directory):
        raise _error("unsupported", path, "External symlink/reference is not followed")
    return path


def _signature(path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _unchanged(path, before):
    if _signature(path) != before:
        raise _error("changed", path, "File changed during reading; no atomic snapshot is available")


def _object(text, path, line=None):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise _error("malformed", path, f"Duplicate JSON key {key!r}", line)
            value[key] = item
        return value
    try:
        value = json.loads(text, object_pairs_hook=unique)
    except json.JSONDecodeError as exc:
        raise _error("malformed", path, exc.msg, line or exc.lineno) from exc
    if not isinstance(value, dict):
        raise _error("unsupported", path, "Expected a JSON object", line)
    return value


def read_metadata(run, artifact_name, *, max_bytes=METADATA_BYTES):
    """Read a selected JSON object. max_bytes=None deliberately permits large JSON.

    Native values/unknown keys survive; no referenced file is opened. JSON's
    historical NaN/Infinity values remain floats rather than being repaired.
    """
    path = _artifact(run, artifact_name)
    try:
        before = _signature(path)
        with path.open("rb") as stream:
            raw = stream.read() if max_bytes is None else stream.read(max_bytes + 1)
        _unchanged(path, before)
        if max_bytes is not None and len(raw) > max_bytes:
            raise _error("oversized", path, f"Metadata exceeds {max_bytes} bytes; read explicitly with max_bytes=None")
        return _object(raw.decode("utf-8"), path)
    except FileNotFoundError as exc:
        raise _error("missing", path, "Artifact does not exist") from exc
    except UnicodeError as exc:
        raise _error("malformed", path, "Expected UTF-8 text") from exc
    except OSError as exc:
        raise _error("unreadable", path, str(exc)) from exc


class _Lines:
    """Bound physical reads and total bytes per CSV record, including multiline."""
    def __init__(self, stream, path, limit):
        self.stream, self.path, self.limit = stream, path, limit
        self.line = self.used = 0

    def __iter__(self):
        return self

    def __next__(self):
        raw = self.stream.readline(self.limit - self.used + 1)
        if not raw:
            raise StopIteration
        self.line += 1
        self.used += len(raw)
        if self.used > self.limit:
            raise _error("oversized", self.path, f"Record exceeds {self.limit} bytes", self.line)
        return raw.decode("utf-8")

    def reset(self):
        self.used = 0


def _header(reader, lines, path):
    header = next(reader, None)
    if header is None:
        return None
    if not header or any(not key for key in header):
        raise _error("malformed", path, "Empty CSV header field", lines.line)
    if len(set(header)) != len(header):
        raise _error("ambiguous", path, "Duplicate CSV headers", lines.line)
    return header


def iter_records(run, artifact_name, *, max_record_bytes=RECORD_BYTES):
    """Lazily yield unconverted dicts. Fail at the first malformed record.

    CSV rows must match the header width; blank records are errors, not skipped
    evidence. JSONL requires one object per physical line. No recovery is guessed.
    """
    path = _artifact(run, artifact_name)
    if path.suffix not in {".csv", ".jsonl"}:
        raise _error("unsupported", path, "Only CSV and JSONL records are readable")
    try:
        before = _signature(path)
        with path.open("rb") as stream:
            lines = _Lines(stream, path, max_record_bytes)
            if path.suffix == ".jsonl":
                for text in lines:
                    value = _object(text, path, lines.line)
                    _unchanged(path, before)
                    yield value
                    lines.reset()
            else:
                reader = csv.reader(lines, strict=True)
                header = _header(reader, lines, path)
                lines.reset()
                for values in reader:
                    if header is None or len(values) != len(header):
                        raise _error("malformed", path, "CSV row width differs from header", lines.line)
                    _unchanged(path, before)
                    yield dict(zip(header, values))
                    lines.reset()
            _unchanged(path, before)
    except FileNotFoundError as exc:
        raise _error("missing", path, "Artifact does not exist") from exc
    except (UnicodeError, csv.Error) as exc:
        raise _error("malformed", path, str(exc), lines.line) from exc
    except OSError as exc:
        raise _error("unreadable", path, str(exc)) from exc


def inspect_run(location):
    """Inspect one directory (or one CSV/JSONL), never recursively or via metadata references.

    At most MAX_ENTRIES entries, four bounded metadata files and CSV headers are
    inspected. Recognition describes a structural family, not scientific validity.
    """
    location = Path(location).expanduser().resolve()
    run = RunInfo(location.parent if location.is_file() else location)
    try:
        if location.is_file():
            names = [location.name]
        else:
            names = []
            for entry in location.iterdir():
                if len(names) == MAX_ENTRIES:
                    run.diagnostics.append(Diagnostic("oversized", location, "Directory inspection limited to 256 entries"))
                    break
                names.append(entry.name)
        run.artifacts = tuple(sorted(names))
    except OSError as exc:
        code = "missing" if isinstance(exc, FileNotFoundError) else "unreadable"
        run.diagnostics.append(Diagnostic(code, location, str(exc)))
        return run
    for name in ("run.json", "recipe.json", "acquisition.json", "quality.json"):
        if name not in names:
            continue
        try:
            value = read_metadata(run, name)
            run.metadata[name] = value
            run.versions[name] = {key: value[key] for key in ("schema_version", "format_version", "version") if key in value}
            for key, version in run.versions[name].items():
                if type(version) is not int or version != 1:
                    run.diagnostics.append(Diagnostic("unsupported", run.directory / name, f"Unrecognized {key}={version!r}; native metadata retained"))
        except ReaderError as exc:
            run.diagnostics.append(exc.diagnostic)
    for name in names:
        if Path(name).suffix != ".csv":
            continue
        path = run.directory / name
        try:
            path = _artifact(run, name)
            before = _signature(path)
            with path.open("rb") as stream:
                lines = _Lines(stream, path, HEADER_BYTES)
                run.headers[name] = _header(csv.reader(lines, strict=True), lines, path)
            _unchanged(path, before)
        except ReaderError as exc:
            run.diagnostics.append(exc.diagnostic)
        except (OSError, UnicodeError, csv.Error) as exc:
            run.diagnostics.append(Diagnostic("malformed" if not isinstance(exc, OSError) else "unreadable", path, str(exc)))
    state = run.metadata.get("run.json", {})
    candidates = []
    if isinstance(state.get("test"), str):
        candidates.append("polarization_locking")
    if isinstance(state.get("experiment"), str) and state.get("schema_version") == 1:
        candidates.append("high_speed_polarimeter")
    if state.get("schema_version") == 1 and "measurement_definitions" in state and "purpose" in state:
        candidates.append("raw_calibration")
    if len(candidates) > 1:
        run.diagnostics.append(Diagnostic("ambiguous", run.directory / "run.json", f"Conflicting family evidence: {candidates}"))
    elif candidates:
        run.family = candidates[0]
    else:
        run.diagnostics.append(Diagnostic("unsupported", location, "No supported runner signature; artifacts remain explicitly readable"))
    quality = run.metadata.get("quality.json", {})
    run.outcomes = {
        "execution": state.get("status"),
        "cleanup": state.get("cleanup_status"),
        "quality_evaluation": state.get("quality_evaluation_status"),
        "scientific_verdict": quality.get("status"),
        "reporting": state.get("report_status"),
    }
    # None means unknown; legacy cleanup_failed never reconstructs prior execution.
    return run
