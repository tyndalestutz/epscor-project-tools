from __future__ import annotations

import time
import subprocess
import sys
import os
import signal
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

try:
    import yaqc
except ImportError:  # pragma: no cover - import may be unavailable in some environments
    yaqc = None


@dataclass
class PAXReading:
    timestamp: float
    theta: float
    eta: float
    s1: float
    s2: float
    s3: float
    dop: float
    # Total optical power reported by the PAX at its own input port. Older
    # daemon versions may omit it, in which case it is NaN.
    ptotal: float = float("nan")
    # Diagnostic telemetry passed through directly from YAQD's primary record.
    # These are useful for identifying a bad acquisition/ADC condition without
    # assigning any physical meaning in the lock controller.
    revisions: float = float("nan")
    adc_min: float = float("nan")
    adc_max: float = float("nan")
    rev_time: float = float("nan")


class PAXNotReady(RuntimeError):
    """No valid advancing record within the shared freshness timeout."""


class PAXController:
    """Thin wrapper around the PAX1000 YAQC client."""

    def __init__(self, config: Any) -> None:
        self.config = config
        self.client: Optional[Any] = None
        self._test_mode = False
        self._daemon_process: Optional[subprocess.Popen[Any]] = None
        self._daemon_log_path: Optional[Path] = None
        self.last_raw_record: dict[str, Any] | None = None
        self._last_fresh_record: tuple[float, float] | None = None

    def connect(self) -> Any:
        self._last_fresh_record = None
        if yaqc is None:
            raise RuntimeError("yaqc is not installed in the active Python environment")

        print(f"Connecting to PAX at {self.config.pax_host}:{self.config.pax_port}...", flush=True)
        if self.config.pax_host in {"localhost", "127.0.0.1", "::1"}:
            self._stop_existing_pax_daemons()
        try:
            self.client = self._new_client()
        except ConnectionRefusedError as exc:
            if not self.config.pax_autostart_daemon:
                raise RuntimeError(
                    f"PAX daemon refused the connection at {self.config.pax_host}:{self.config.pax_port}."
                ) from exc
            print("Starting PAX daemon; motor spin-up includes a 5-second settling wait. Please wait...", flush=True)
            self._start_daemon()
            self.client = self._wait_for_daemon(exc)
        print("PAX daemon connected; applying wavelength...", flush=True)
        self._apply_measurement_configuration()
        print("PAX ready for setup. Fresh measurement validation runs before data logging.", flush=True)
        self._test_mode = False
        return self.client

    @staticmethod
    def _stop_existing_pax_daemons() -> None:
        """Reclaim the local PAX from lingering daemons before connecting."""
        if not sys.platform.startswith("linux"):
            return
        names = {"yaqd-thorlabs-pax1000", "pax1000_daemon.py"}
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit() or int(entry.name) == os.getpid():
                continue
            try:
                argv = (entry / "cmdline").read_bytes().decode(errors="replace").strip("\0").split("\0")
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                continue
            executable = Path(argv[0]).name if argv else ""
            # Match actual launch arguments, not a shell/editor mentioning PAX.
            matches = executable in names or (
                executable.startswith("python") and len(argv) > 1 and Path(argv[1]).name in names
            )
            if not matches:
                continue
            pid = int(entry.name)
            print(f"Stopping existing PAX daemon (PID {pid}) before connection.")
            try:
                os.kill(pid, signal.SIGTERM)
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    if not entry.exists():
                        break
                    time.sleep(0.05)
                else:
                    os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError as exc:
                raise RuntimeError(f"Cannot stop existing PAX daemon PID {pid}; it still owns the device") from exc

    def _apply_measurement_configuration(self) -> None:
        """Apply wavelength after the project daemon configures motor/mode."""
        if self.client is None:
            raise RuntimeError("PAX connection is not established")
        wavelength = float(self.config.pax_wavelength_nm)
        if wavelength <= 0.0:
            raise ValueError("pax_wavelength_nm must be positive")
        try:
            self.client.set_wavelength(wavelength)
        except Exception as exc:
            message = str(exc)
            if "No such device" in message:
                raise RuntimeError(
                    "The PAX daemon accepted the network connection, but its USB device handle is stale. "
                    "Stop the existing YAQD PAX daemon and retry; the local project daemon will restart "
                    "automatically. Confirm the PAX serial in hardware/pax1000.toml."
                ) from exc
            raise

    def _new_client(self) -> Any:
        if yaqc is None:
            raise RuntimeError("yaqc is not installed in the active Python environment")
        return yaqc.Client(host=self.config.pax_host, port=self.config.pax_port)

    def disconnect(self) -> None:
        self._last_fresh_record = None
        self.client = None
        self._test_mode = False
        if self._daemon_process is not None:
            self._daemon_process.terminate()
            try:
                self._daemon_process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self._daemon_process.kill()
                self._daemon_process.wait(timeout=2.0)
            self._daemon_process = None

    def _start_daemon(self) -> None:
        config_path = Path(self.config.pax_daemon_config_path)
        if not config_path.is_file():
            raise RuntimeError(f"PAX daemon configuration was not found: {config_path}")
        daemon_path = config_path.with_name("pax1000_daemon.py")
        if not daemon_path.is_file():
            raise RuntimeError(f"Project PAX daemon was not found: {daemon_path}")
        # Do not discard daemon stderr: when VISA cannot open the USB device,
        # that error is the only useful diagnosis available to lock.py.
        self._daemon_log_path = config_path.with_name("pax1000-daemon.log")
        try:
            with self._daemon_log_path.open("a", encoding="utf-8") as daemon_log:
                daemon_log.write(
                    f"\n--- PAX daemon launch {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n"
                )
                daemon_log.flush()
                self._daemon_process = subprocess.Popen(
                    [sys.executable, str(daemon_path), "-c", config_path.name, "-v"],
                    cwd=config_path.parent,
                    stdout=daemon_log,
                    stderr=subprocess.STDOUT,
                )
        except FileNotFoundError as exc:
            raise RuntimeError("Python or the project PAX daemon is unavailable in the active environment") from exc

    def _wait_for_daemon(self, original_error: ConnectionRefusedError) -> Any:
        # Include the daemon's five-second spin-up wait, including old recipes
        # whose configured startup timeout was only five seconds.
        startup_timeout_s = max(10.0, self.config.pax_daemon_start_timeout_s)
        deadline = time.monotonic() + startup_timeout_s
        while time.monotonic() < deadline:
            if self._daemon_process is not None and self._daemon_process.poll() is not None:
                log_hint = (
                    f" See {self._daemon_log_path} for its traceback."
                    if self._daemon_log_path is not None
                    else ""
                )
                raise RuntimeError(
                    "The PAX daemon exited while starting." + log_hint
                )
            try:
                return self._new_client()
            except ConnectionRefusedError:
                time.sleep(0.1)
        raise RuntimeError(
            f"PAX daemon did not begin listening at {self.config.pax_host}:{self.config.pax_port} within "
            f"{startup_timeout_s:.1f} s."
        ) from original_error

    def stop_rotation(self) -> None:
        """Stop the waveplate motor through the existing daemon serial command.

        Leave the daemon/device connected; do not request polarization while
        stopped. Advancement must be re-established before any later fresh read.
        """
        if self.client is None:
            raise RuntimeError("PAX connection is not established")
        self.client.direct_serial_write(b"INPut:ROTation:STATe 0")
        self._last_fresh_record = None

    def get_channel_names(self) -> list[str]:
        if self.client is None:
            raise RuntimeError("PAX connection is not established")
        return list(self.client.get_channel_names())

    def enable_test_mode(self) -> None:
        self._test_mode = True
        self.client = None

    def read_polarization(self) -> PAXReading:
        """Read the current polarization state from the PAX.

        This matches the PAX live-plot acquisition sequence: call measure(),
        wait 60 ms, then retrieve get_measured(). In environments without the
        PAX daemon, test mode returns a deterministic placeholder reading.
        """
        if self._test_mode:
            time.sleep(0.05)
            return PAXReading(timestamp=0.0, theta=0.0, eta=0.0, s1=1.0, s2=0.0, s3=0.0, dop=1.0)

        if self.client is None:
            raise RuntimeError("PAX connection is not established")

        data = None
        for attempt in range(self.config.pax_read_retries):
            self.client.measure()
            time.sleep(self.config.pax_measurement_wait_s)
            try:
                data = self.client.get_measured()
                break
            except TimeoutError:
                if attempt + 1 == self.config.pax_read_retries:
                    raise
                # The daemon may have completed the measurement after the
                # client socket timed out. Start the next trigger on a fresh
                # YAQC connection rather than reusing a possibly desynced one.
                self.client = self._new_client()
                time.sleep(self.config.pax_retry_wait_s)

        assert data is not None  # protected by the retry loop above
        self.last_raw_record = dict(data)

        timestamp = float(data["timestamp"])
        theta = float(data["theta"])
        eta = float(data["eta"])
        dop = float(data["dop"])
        ptotal = float(data.get("ptotal", float("nan")))
        revisions = float(data.get("revisions", float("nan")))
        adc_min = float(data.get("adc_min", float("nan")))
        adc_max = float(data.get("adc_max", float("nan")))
        rev_time = float(data.get("rev_time", float("nan")))

        # The notebook exposes theta and eta directly; the Stokes parameters are
        # derived from these angles for the lock-loop model.
        s1 = float(__import__("math").cos(2 * eta) * __import__("math").cos(2 * theta))
        s2 = float(__import__("math").cos(2 * eta) * __import__("math").sin(2 * theta))
        s3 = float(__import__("math").sin(2 * eta))

        return PAXReading(
            timestamp=timestamp, theta=theta, eta=eta, s1=s1, s2=s2, s3=s3, dop=dop, ptotal=ptotal,
            revisions=revisions, adc_min=adc_min, adc_max=adc_max, rev_time=rev_time,
        )

    def read_fresh_polarization(self) -> PAXReading:
        """Return an initialized, advancing record or fail within a bounded wait.

        The first call establishes advancement using two valid device records.
        Low DOP and small calibration excursions above one remain raw data;
        this checks acquisition validity, not polarization purity.
        """
        deadline = time.monotonic() + self.config.pax_fresh_read_timeout_s
        previous = self._last_fresh_record
        while time.monotonic() < deadline:
            try:
                reading = self.read_polarization()
            except (KeyError, ValueError):
                # YAQD can expose its empty initial result before the first
                # completed measurement; non-finite angles also cannot form Stokes.
                time.sleep(0.01)
                continue
            values = (reading.timestamp, reading.revisions, reading.theta, reading.eta,
                      reading.dop, reading.ptotal, reading.adc_min, reading.adc_max, reading.rev_time)
            valid = (all(math.isfinite(value) and abs(value) < 1e30 for value in values)
                     and reading.timestamp > 0 and reading.revisions > 0 and reading.rev_time > 0
                     and 0 <= reading.adc_min < reading.adc_max < 65520
                     and abs(reading.theta) <= math.pi / 2 and abs(reading.eta) <= math.pi / 4
                     and reading.dop >= 0 and reading.ptotal > 0)
            if valid:
                current = (reading.timestamp, reading.revisions)
                if previous is not None and all(now > before for now, before in zip(current, previous)):
                    self._last_fresh_record = current
                    return reading
                if previous is None:
                    previous = current
            time.sleep(0.01)
        raise PAXNotReady("PAX did not return a valid advancing measurement before the freshness timeout")
