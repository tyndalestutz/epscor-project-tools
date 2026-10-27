"""Owned local PAX process lifetime; no process or device activity on import."""
from contextlib import contextmanager
import json
import math
from pathlib import Path
import socket
import shutil
import subprocess
import sys
import tempfile
import time


DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "polarization_locking/hardware/pax1000.toml"


class PAXCleanupError(RuntimeError):
    """Cleanup was attempted; stopped hardware must not be inferred from failure."""

    def __init__(self, errors):
        self.errors = tuple(errors)
        super().__init__("PAX session cleanup failed: " + "; ".join(
            f"{step}: {type(error).__name__}: {error}" for step, error in errors))


def _ensure_port_free(port):
    # Refuse a listening daemon before opening USB. Reuse permits our previous
    # session's TIME_WAIT sockets, but never steals an active listening socket.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as error:
            raise RuntimeError(f"PAX port {port} is occupied; close its owning session first") from error


class _OwnedDaemon:
    def __init__(self, config_path, startup_timeout_s, shutdown_timeout_s):
        if sys.platform.startswith("win"):
            raise RuntimeError("Owned PAX sessions currently require POSIX signal handling")
        for value in (startup_timeout_s, shutdown_timeout_s):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("Session timeouts must be positive and finite")
        # These are existing YAQD dependencies, imported only for a local session.
        import tomli
        import tomli_w
        self.config_path = Path(config_path or DEFAULT_CONFIG).expanduser().resolve()
        self.config_text = self.config_path.read_text()
        config = tomli.loads(self.config_text)
        if set(config) != {"thorlabs-pax1000"}:
            raise ValueError("A local session requires exactly one [thorlabs-pax1000] section")
        settings = config["thorlabs-pax1000"]
        self.serial = settings.get("serial")
        self.port = settings.get("port", 38400)
        if not isinstance(self.serial, str) or not self.serial:
            raise ValueError("PAX daemon config requires its device serial")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("PAX daemon config port must be an integer in 1..65535")
        settings["host"] = "127.0.0.1"
        self.owned_config_text = tomli_w.dumps(config)
        self.startup_timeout_s = startup_timeout_s
        self.shutdown_timeout_s = shutdown_timeout_s
        self.process = self.directory = self.log = None
        self.verified_owner = False

    def start(self):
        _ensure_port_free(self.port)
        self.directory = Path(tempfile.mkdtemp(prefix="epscor-pax-session-"))
        root = self.directory
        self.owned_config_path = root / "pax1000.toml"
        self.owned_config_path.write_text(self.owned_config_text)
        self.log_path = root / "daemon.log"
        self.receipt_path = root / "shutdown.json"
        self.log = self.log_path.open("w")
        print("Starting local PAX daemon (includes 5 s motor settling)...", flush=True)
        self.process = subprocess.Popen(
            [sys.executable, str(Path(__file__).with_name("_pax_daemon.py")), str(root)],
            stdin=subprocess.PIPE, stdout=self.log, stderr=subprocess.STDOUT,
            start_new_session=True)

    def connect(self, device):
        deadline = time.monotonic() + self.startup_timeout_s
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("Owned PAX daemon exited during startup:\n" +
                                   self.log_path.read_text(errors="replace")[-4000:])
            try:
                device.connect()
            except ConnectionRefusedError:
                time.sleep(.1)
                continue
            # A unique config pathname proves endpoint ownership even if another
            # process won a race for this port. These are read-only RPCs.
            client = device._require_client()
            actual = Path(client.get_config_filepath()).resolve()
            if actual != self.owned_config_path or client.id().get("serial") != self.serial:
                raise RuntimeError("PAX endpoint is not the daemon started by this session")
            if self.process.poll() is not None:
                raise RuntimeError("Owned PAX daemon exited while connecting")
            self.verified_owner = True
            print(f"PAX session ready at 127.0.0.1:{self.port} (serial {self.serial}).", flush=True)
            return
        raise TimeoutError(f"PAX daemon did not become ready within {self.startup_timeout_s:g} s\n" +
                           self.log_path.read_text(errors="replace")[-4000:])

    def close(self, device):
        errors = []

        def attempt(step, operation):
            try:
                operation()
            except BaseException as error:
                errors.append((step, error))

        attempt("client close", device.close)
        if self.process is not None:
            # EOF asks the child to shut down, including when the kernel exits.
            attempt("parent pipe close", self.process.stdin.close)
            try:
                self.process.wait(timeout=self.shutdown_timeout_s)
            except BaseException as error:
                errors.append(("graceful daemon exit", error))
                attempt("owned daemon kill", self.process.kill)
                attempt("owned daemon reap", lambda: self.process.wait(timeout=self.shutdown_timeout_s))
            if self.process.poll() is not None:
                try:
                    receipt = json.loads(self.receipt_path.read_text())
                    device.shutdown_evidence = receipt
                    if self.verified_owner and not receipt.get("usb_opened"):
                        raise RuntimeError("Connected session has no USB shutdown evidence")
                    if receipt.get("usb_opened") and (not receipt.get("motor_stop_command_succeeded") or
                                                      not receipt.get("usb_closed")):
                        raise RuntimeError(f"Daemon could not confirm stop/close: {receipt}")
                    if self.process.returncode != 0:
                        raise RuntimeError(f"Daemon exited with code {self.process.returncode}")
                except Exception as error:
                    errors.append(("daemon shutdown evidence", error))
        if self.log is not None:
            attempt("log close", self.log.close)
            attempt("log read", lambda: setattr(device, "daemon_log", self.log_path.read_text(errors="replace")))
        if self.directory is not None:
            # Keep files if the child survived kill/reap, so diagnosis stays possible.
            if self.process is None or self.process.poll() is not None:
                attempt("temporary session cleanup", lambda: shutil.rmtree(self.directory))
        if errors:
            failure = PAXCleanupError(errors)
            for _, error in errors:
                if not isinstance(error, Exception):
                    raise error from failure
            raise failure from errors[0][1]


@contextmanager
def local_session(device_class, *, config_path, startup_timeout_s, shutdown_timeout_s,
                  rpc_timeout_s, measurement_wait_s):
    daemon = _OwnedDaemon(config_path, startup_timeout_s, shutdown_timeout_s)
    device = device_class(host="127.0.0.1", port=daemon.port, rpc_timeout_s=rpc_timeout_s,
                          measurement_wait_s=measurement_wait_s)
    device.session_configuration = {"source_path": str(daemon.config_path),
                                    "source_toml": daemon.config_text,
                                    "effective_toml": daemon.owned_config_text}
    device.daemon_log = ""
    device.shutdown_evidence = None
    try:
        daemon.start()
        daemon.connect(device)
        yield device
    except BaseException as error:
        try:
            daemon.close(device)
        except BaseException as cleanup_error:
            error.daemon_log = device.daemon_log
            raise error from cleanup_error
        if device.daemon_log:
            # Preserve the original exception type while making startup diagnosis
            # available on the exception, including when __enter__ never yielded.
            error.daemon_log = device.daemon_log
        raise
    else:
        daemon.close(device)
        print("PAX daemon exited; motor-stop command and USB close succeeded.", flush=True)
