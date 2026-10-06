"""One retained run per session; execution, cleanup, quality and report are separate."""
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
import sys
from uuid import uuid4

from .experiments import ACQUISITIONS
from .hardware import MockSession, MockAcquisitionSession
from .io import write_json
from .provenance import runtime_provenance
from .quality import evaluate_run
from .reports import create_report


def now():
    return datetime.now(timezone.utc).isoformat()


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


def execute(recipe, *, session_factory=None, acquire=None, mode="mock",
            additional_source_roots=(), dependencies=()):
    recipe = recipe.resolved()  # reject invalid input before allocating or initializing
    if mode not in {"mock", "hardware"}:
        raise ValueError("mode must be mock or hardware")
    if mode == "hardware":
        if recipe.experiment == "mock_lifecycle":
            raise ValueError("mock_lifecycle cannot initialize bench hardware")
        config = recipe.acquisition_config()
        if not config.output_map_confirmed or not config.voltage_chain.strip() or not config.pax_reference_plane.strip():
            raise ValueError("Hardware run requires output_map_confirmed and recorded voltage_chain/pax_reference_plane")
    if session_factory is None:
        if recipe.experiment == "mock_lifecycle":
            session_factory = MockSession
        elif mode == "mock":
            session_factory = lambda: MockAcquisitionSession(recipe.acquisition_config())
        else:
            def session_factory():
                from .hardware.legacy import BenchSession
                return BenchSession(recipe.acquisition_config())
    timestamp = datetime.now(timezone.utc)
    directory = Path(recipe.config["results_directory"]).expanduser() / timestamp.strftime("%Y-%m-%d") / f"{timestamp:%H%M%S}_{recipe.experiment}_{recipe.label}_{uuid4().hex[:8]}"
    directory.mkdir(parents=True, exist_ok=False)
    write_json(directory / "recipe.json", recipe.to_dict())
    state = {"schema_version": 1, "experiment": recipe.experiment,
             "status": "RUNNING", "mode": mode, "started_at": now(),
             "directory": str(directory.resolve()), "hardware": [],
             "cleanup_status": "NOT_STARTED", "quality_evaluation_status": "PENDING", "report_status": "PENDING"}
    write_json(directory / "run.json", state)
    session = None
    with (directory / "console.log").open("w", encoding="utf-8") as logfile, redirect_stdout(Tee(sys.stdout, logfile)), redirect_stderr(Tee(sys.stderr, logfile)):
        try:
            roots, packages = list(additional_source_roots), list(dependencies)
            if recipe.experiment != "mock_lifecycle":
                packages.append("numpy")
            if mode == "hardware":
                from .config import LEGACY_ROOT
                roots.append(LEGACY_ROOT)
                daemon_config = Path(recipe.config["pax_daemon_config_path"]).expanduser().resolve()
                for path in (daemon_config, daemon_config.with_name("pax1000_daemon.py"), Path(recipe.config["rp_config"]).expanduser()):
                    if path.is_file():
                        roots.append(path)
                packages.extend(("pyrpl", "yaqc", "yaqd-core", "yaqd-thorlabs", "pyvisa", "pyvisa-py", "pyusb"))
            state["provenance"] = runtime_provenance(additional_source_roots=tuple(dict.fromkeys(roots)), dependencies=tuple(dict.fromkeys(packages)))
            write_json(directory / "run.json", state)
            session = session_factory()
            if getattr(session, "mode", mode) != mode:
                raise ValueError("Session mode must match runner mode; hardware sessions require hardware provenance")
            session.connect()
            if hasattr(session, "describe"):
                state["hardware"] = session.describe()
                write_json(directory / "run.json", state)
            result = (acquire or ACQUISITIONS[recipe.experiment])(session, recipe, directory)
            if result is not None:
                state["acquisition"] = result
            state["status"] = "COMPLETED"
        except (KeyboardInterrupt, EOFError) as exc:
            state.update(status="INTERRUPTED", error=f"{type(exc).__name__}: {exc}")
        except Exception as exc:
            state.update(status="FAILED", error=f"{type(exc).__name__}: {exc}")
        finally:
            if session is not None:
                try:
                    session.disconnect()  # also on partially failed connect
                    state["cleanup_status"] = "COMPLETED"
                except BaseException as exc:
                    state.update(cleanup_status="FAILED", cleanup_error=f"{type(exc).__name__}: {exc}")
            state["finished_at"] = now()
            print(f"Execution: {state['status']}; cleanup: {state['cleanup_status']}. Partial artifacts retained.")
            write_json(directory / "run.json", state)
        try:
            write_json(directory / "quality.json", evaluate_run(directory).to_dict())
            state["quality_evaluation_status"] = "COMPLETED"  # evaluation execution, not scientific verdict
        except Exception as exc:
            state.update(quality_evaluation_status="FAILED", quality_error=f"{type(exc).__name__}: {exc}")
        write_json(directory / "run.json", state)
        try:
            create_report(directory)
            state["report_status"] = "COMPLETED"
        except Exception as exc:
            state.update(report_status="FAILED", report_error=f"{type(exc).__name__}: {exc}")
        write_json(directory / "run.json", state)
    return state
