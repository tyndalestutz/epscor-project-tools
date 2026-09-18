"""One hardware session per run, with replay recipe, log, and completion status."""
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import platform
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


class Tee:
    def __init__(self, terminal, logfile):
        self.terminal, self.logfile = terminal, logfile

    def write(self, text):
        self.terminal.write(text)
        self.logfile.write(text)
        self.logfile.flush()
        return len(text)

    def flush(self):
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
    status_path = paths.directory / "run.json"
    write_json(status_path, state)
    print(f"Run folder: {paths.directory}")
    with (paths.directory / "console.log").open("w") as logfile, redirect_stdout(Tee(sys.stdout, logfile)), redirect_stderr(Tee(sys.stderr, logfile)):
        try:
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
            getattr(app, case.method)(**kwargs)
            state["status"] = "completed"
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
            # Reporting runs after cleanup, including stopped monitors and partial scans.
            try:
                from .reports.run_report import create_run_report
                state.update(create_run_report(case, paths, app.config, options, state))
            except Exception as exc:
                state.update(report_status="failed", report_error=f"{type(exc).__name__}: {exc}")
                print(f"Data saved; report failed: {state['report_error']}")
            write_json(status_path, state)
    return state
