"""Explicit attachment/acquisition through an existing PAX YAQD daemon.

No process discovery, daemon startup/shutdown, motor command or implicit
wavelength change. Driver imports and network activity begin only at connect().
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import math
import time


@dataclass
class PAXReading:
    """Legacy-compatible reading; s1/s2/s3 are derived unit-direction coordinates."""

    timestamp: float
    theta: float
    eta: float
    s1: float
    s2: float
    s3: float
    dop: float
    ptotal: float = float("nan")
    revisions: float = float("nan")
    adc_min: float = float("nan")
    adc_max: float = float("nan")
    rev_time: float = float("nan")


class PAXNotReady(RuntimeError):
    """No valid advancing record within the freshness polling budget."""


def _parse_reading(data):
    theta, eta = float(data["theta"]), float(data["eta"])
    return PAXReading(
        timestamp=float(data["timestamp"]), theta=theta, eta=eta,
        s1=math.cos(2 * eta) * math.cos(2 * theta),
        s2=math.cos(2 * eta) * math.sin(2 * theta), s3=math.sin(2 * eta),
        dop=float(data["dop"]),
        **{key: float(data.get(key, float("nan"))) for key in
           ("ptotal", "revisions", "adc_min", "adc_max", "rev_time")})


def _valid_reading(reading):
    """Existing acquisition-validity policy; no low-DoP threshold or clipping."""
    values = (reading.timestamp, reading.revisions, reading.theta, reading.eta,
              reading.dop, reading.ptotal, reading.adc_min, reading.adc_max, reading.rev_time)
    return (all(math.isfinite(value) and abs(value) < 1e30 for value in values)
            and reading.timestamp > 0 and reading.revisions > 0 and reading.rev_time > 0
            and 0 <= reading.adc_min < reading.adc_max < 65520
            and abs(reading.theta) <= math.pi / 2 and abs(reading.eta) <= math.pi / 4
            and reading.dop >= 0 and reading.ptotal > 0)


def _close_yaqc_client(client):
    # YAQC 2023.6.0 has no public client close. Calling shutdown() would stop
    # the shared daemon; close only this client's underlying Python socket.
    transport = getattr(client, "_socket", None)
    if transport is not None:
        transport._socket.close()


class PAX:
    """Attach to an already-running daemon; acquire raw primary records.

    The daemon owns USB and motor configuration. Use read_status() to inspect
    its wavelength/channel metadata. read_raw() triggers acquisition but alone
    does not establish freshness. Calls are sequential, not thread-safe.
    """

    def __init__(self, host="localhost", port=38400, *, rpc_timeout_s=2.0,
                 measurement_wait_s=0.06):
        if not host or type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("Specify a PAX host and integer port in 1..65535")
        if not math.isfinite(rpc_timeout_s) or rpc_timeout_s <= 0:
            raise ValueError("rpc_timeout_s must be positive and finite")
        if not math.isfinite(measurement_wait_s) or measurement_wait_s < 0:
            raise ValueError("measurement_wait_s must be nonnegative and finite")
        self.host, self.port = host, port
        self.rpc_timeout_s, self.measurement_wait_s = rpc_timeout_s, measurement_wait_s
        self._client = None
        self._last_fresh_record = None
        self.last_raw_record = None
        self.last_read_timing = None
        self.last_attempts = []

    def connect(self):
        """Open a client and perform YAQC's handshake/identity exchange only."""
        if self._client is not None:
            raise RuntimeError("PAX already connected; close it before connecting again")
        try:
            from yaqc import Client
        except ImportError as error:
            raise RuntimeError("yaqc is required to connect to the PAX daemon") from error
        # Retain the instance for socket cleanup if the handshake fails.
        client = Client.__new__(Client)
        self._client = client
        try:
            Client.__init__(client, host=self.host, port=self.port, timeout=self.rpc_timeout_s)
        except BaseException as error:
            try:
                self.close()
            except Exception as cleanup_error:
                raise error from cleanup_error
            raise
        self._last_fresh_record = None
        return self

    def _require_client(self):
        if self._client is None:
            raise RuntimeError("Connect to the PAX daemon first")
        return self._client

    def read_status(self):
        """Read daemon identity, wavelength (nm) and unmodified channel metadata."""
        client = self._require_client()
        return {"host": self.host, "port": self.port, "identity": client.id(),
                "wavelength_nm": client.get_wavelength(),
                "channel_units": client.get_channel_units()}

    def set_wavelength(self, wavelength_nm):
        """Explicitly change the daemon/device wavelength correction, in nm."""
        if not math.isfinite(wavelength_nm) or wavelength_nm <= 0:
            raise ValueError("wavelength_nm must be positive and finite")
        client = self._require_client()
        self._last_fresh_record = None
        client.set_wavelength(wavelength_nm)

    def read_raw(self):
        """Trigger, wait, fetch. Return all fields unchanged, including anomalies.

        ptotal is instrument power in watts by the established repository
        convention; daemon metadata may omit its unit. Device timestamps retain
        native unspecified units. Host timing is stored separately.
        """
        client = self._require_client()
        self.last_raw_record = None
        self.last_read_timing = {"requested_monotonic_s": time.monotonic(),
                                 "requested_utc": datetime.now(timezone.utc).isoformat(),
                                 "measurement_wait_s": self.measurement_wait_s,
                                 "initial_rpc_timeout_s": self.rpc_timeout_s}
        try:
            self.last_read_timing["measurement_id"] = client.measure()
            time.sleep(self.measurement_wait_s)
            self.last_raw_record = dict(client.get_measured())
        finally:
            self.last_read_timing["received_monotonic_s"] = time.monotonic()
            self.last_read_timing["received_utc"] = datetime.now(timezone.utc).isoformat()
        return dict(self.last_raw_record)

    def read_fresh(self, *, timeout_s=5.0):
        """Return a valid advancing raw record using the established policy.

        First call requires two valid records with advancing timestamp AND
        revisions. All attempts remain in last_attempts, including invalid and
        duplicate records. timeout_s is a polling budget: an in-progress RPC or
        configured measurement wait can finish after it. RPC errors propagate.
        """
        self._require_client()
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("timeout_s must be positive and finite")
        self.last_attempts = []
        previous = self._last_fresh_record
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                raw = self.read_raw()
            except Exception as error:
                self.last_attempts.append({"raw_record": self.last_raw_record,
                                           "host_timing": dict(self.last_read_timing or {}),
                                           "error": f"{type(error).__name__}: {error}"})
                raise
            try:
                reading = _parse_reading(raw)
                valid = _valid_reading(reading)
            except (KeyError, ValueError, TypeError, OverflowError):
                valid = False
            self.last_attempts.append({"raw_record": dict(raw),
                                       "host_timing": dict(self.last_read_timing),
                                       "valid": valid})
            if valid:
                current = (reading.timestamp, reading.revisions)
                if previous is not None and all(now > before for now, before in zip(current, previous)):
                    self._last_fresh_record = current
                    return raw
                if previous is None:
                    previous = current
            time.sleep(0.01)
        raise PAXNotReady("PAX did not return a valid advancing measurement before the freshness timeout")

    def close(self):
        """Close only this client socket. Leave the daemon and motor untouched."""
        if self._client is not None:
            _close_yaqc_client(self._client)
            self._client = None
        self._last_fresh_record = None
