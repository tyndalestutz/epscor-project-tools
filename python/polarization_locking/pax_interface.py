from __future__ import annotations

import time
import subprocess
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


class PAXController:
    """Thin wrapper around the PAX1000 YAQC client."""

    def __init__(self, config: Any) -> None:
        self.config = config
        self.client: Optional[Any] = None
        self._test_mode = False
        self._daemon_process: Optional[subprocess.Popen[Any]] = None

    def connect(self) -> Any:
        if yaqc is None:
            raise RuntimeError("yaqc is not installed in the active Python environment")

        try:
            self.client = self._new_client()
        except ConnectionRefusedError as exc:
            if not self.config.pax_autostart_daemon:
                raise RuntimeError(
                    f"PAX daemon refused the connection at {self.config.pax_host}:{self.config.pax_port}."
                ) from exc
            self._start_daemon()
            self.client = self._wait_for_daemon(exc)
        self._test_mode = False
        return self.client

    def _new_client(self) -> Any:
        if yaqc is None:
            raise RuntimeError("yaqc is not installed in the active Python environment")
        return yaqc.Client(host=self.config.pax_host, port=self.config.pax_port)

    def disconnect(self) -> None:
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
        try:
            self._daemon_process = subprocess.Popen(
                ["yaqd-thorlabs-pax1000", "-c", config_path.name],
                cwd=config_path.parent,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("yaqd-thorlabs-pax1000 is not available in the active environment") from exc

    def _wait_for_daemon(self, original_error: ConnectionRefusedError) -> Any:
        deadline = time.monotonic() + self.config.pax_daemon_start_timeout_s
        while time.monotonic() < deadline:
            if self._daemon_process is not None and self._daemon_process.poll() is not None:
                raise RuntimeError(
                    "The PAX daemon exited while starting; run it manually with '-v' to see its error output."
                )
            try:
                return self._new_client()
            except ConnectionRefusedError:
                time.sleep(0.1)
        raise RuntimeError(
            f"PAX daemon did not begin listening at {self.config.pax_host}:{self.config.pax_port} within "
            f"{self.config.pax_daemon_start_timeout_s:.1f} s."
        ) from original_error

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
