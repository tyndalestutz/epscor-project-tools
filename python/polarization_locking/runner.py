"""One hardware session per run, with replay recipe, log, and completion status."""
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import platform
import shutil
import subprocess
import sys

from .settings import recipe, validate, write_json


def source_fingerprint():
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.suffix in {".py", ".toml"}:
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def runtime_provenance():
    """Record local code and dependency versions without importing drivers."""
    root = Path(__file__).resolve().parent
    packages = {}
    for name in ("numpy", "scipy", "matplotlib", "pyrpl", "yaqc", "yaqd-core", "yaqd-thorlabs", "pyvisa", "pyvisa-py", "pyusb", "pyvista", "pyvistaqt"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    provenance = {"packages": packages, "platform": platform.platform(), "git_commit": None, "suite_dirty": None}
    try:
        provenance["git_commit"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True, stderr=subprocess.DEVNULL, timeout=5).strip()
        provenance["suite_dirty"] = bool(subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=all", "--", "."],
            cwd=root, text=True, stderr=subprocess.DEVNULL, timeout=5).strip())
    except (OSError, subprocess.SubprocessError):
        pass  # exported source trees are also usable; the source hash remains
    return provenance


class Tee:
    def __init__(self, terminal, logfile, stream_name="stdout"):
        self.terminal, self.logfile = terminal, logfile
        self.stream_name = stream_name

    def _current_stream(self):
        # Library StreamHandlers can retain this object after its run ends.
        # Forward stale references into the next run's tee (or the terminal).
        current = getattr(sys, self.stream_name)
        return self.terminal if current is self else current

    def write(self, text):
        if self.logfile.closed:
            return self._current_stream().write(text)
        self.terminal.write(text)
        self.logfile.write(text)
        self.logfile.flush()
        return len(text)

    def flush(self):
        if self.logfile.closed:
            self._current_stream().flush()
            return
        self.terminal.flush()
        self.logfile.flush()


def execute(case, config, options, *, app_factory=None):
    validate(config, case, options)
    if app_factory is None:
        from .app import PolarizationLockApp
        app_factory = PolarizationLockApp
    # Target capture is per run; never let stale targets leak into a new session.
    runtime_config = replace(config)
    if not case.target or options.get("target_mode") == "current":
        runtime_config.target_u = runtime_config.target_v = None
    app = app_factory(runtime_config)
    paths = app._new_experiment_paths(case.key, options["label"])
    write_json(paths.directory / "recipe.json", recipe(case, config, options))
    state = {"test": case.key, "status": "running", "started_at": datetime.now(timezone.utc).isoformat(), "python": platform.python_version(), "source_sha256": source_fingerprint(), "required_setup": case.setup}
    state["run_comment"] = options.get("comment", "")
    if case.key == "pax-live":
        state["data_disposition"] = "temporary"
    state["provenance"] = runtime_provenance()
    status_path = paths.directory / "run.json"
    write_json(status_path, state)
    print(f"Run folder: {paths.directory}")
    with (paths.directory / "console.log").open("w") as logfile, redirect_stdout(Tee(sys.stdout, logfile)), redirect_stderr(Tee(sys.stderr, logfile, "stderr")):
        if state["run_comment"]:
            print(f"Run comment: {state['run_comment']}")
        try:
            if case.pax_only:
                # The live panel opens immediately; its worker owns the entire
                # PAX session so YAQC never moves between threads.
                app._pax_only = True
            elif case.scope_only:
                app.connect(scope_only=True)
            else:
                app.connect()
            if case.target and options["target_mode"] == "current":
                app.capture_target()
            state["effective_config"] = asdict(app.config)
            write_json(status_path, state)
            kwargs = {"output_file": str(paths.csv)}
            if case.axis:
                kwargs["axis"] = options["axis"]
            if case.timed or "gain_scan" in case.flags:
                kwargs["duration_s"] = options.get("duration_s", 0.0)
            kwargs.update({flag: True for flag in case.flags})
            if case.key == "rough":
                kwargs = {}
            result = getattr(app, case.method)(**kwargs)
            state["status"] = "completed"
            if case.key == "pax-live":
                result = result or {}
                state["data_disposition"] = {"save": "saved", "discard": "discard"}.get(result.get("disposition"), "temporary")
                state.update({key: result[key] for key in ("sample_count", "duration_s", "error", "cleanup_error") if key in result})
                if state.get("error"):
                    state["status"] = "failed"
                if state.get("cleanup_error"):
                    state["status"] = "cleanup_failed"
        except (KeyboardInterrupt, EOFError):
            state["status"] = "interrupted"
            print("Run stopped. Partial data is retained.")
        except Exception as exc:
            state.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            print(f"Run failed: {state['error']}")
        finally:
            try:
                app.disconnect()
            except Exception as exc:
                state.update(status="cleanup_failed", cleanup_error=str(exc))
                print(f"Could not complete instrument cleanup: {exc}. Check RP outputs before continuing.")
            state["effective_config"] = asdict(app.config)
            state["finished_at"] = datetime.now(timezone.utc).isoformat()
            write_json(status_path, state)
            # Unconfirmed live runs remain recoverable; only Save generates a PDF.
            if case.key != "pax-live" or state.get("data_disposition") == "saved":
                try:
                    from .reports.run_report import create_run_report
                    state.update(create_run_report(case, paths, app.config, options, state))
                except Exception as exc:
                    state.update(report_status="failed", report_error=f"{type(exc).__name__}: {exc}")
                    print(f"Data saved; report failed: {state['report_error']}")
            write_json(status_path, state)
    # The worker and log handles are closed. Delete only this newly allocated run,
    # and retain cleanup failures as recovery evidence even after Discard.
    if state.get("data_disposition") == "discard":
        if state["status"] == "cleanup_failed":
            state["data_disposition"] = "temporary"
            write_json(status_path, state)
            print(f"Cleanup failed; temporary data retained at {paths.directory}")
        else:
            try:
                shutil.rmtree(paths.directory)
                state.update(status="discarded", data_disposition="discarded")
                print("Alignment run discarded.")
            except OSError as exc:
                state.update(status="discard_failed", data_disposition="temporary", error=str(exc))
                print(f"Could not fully discard {paths.directory}: {exc}")
    return state
