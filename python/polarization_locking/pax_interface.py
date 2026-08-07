from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

try:
    import yaqc
except ImportError:  # pragma: no cover - import may be unavailable in some environments
    yaqc = None


@dataclass
class PAXReading:
    theta: float
    eta: float
    s1: float
    s2: float
    s3: float
    dop: float


class PAXController:
    """Thin wrapper around the PAX1000 YAQC client."""

    def __init__(self, config: Any) -> None:
        self.config = config
        self.client: Optional[Any] = None
        self._test_mode = False

    def connect(self) -> Any:
        if yaqc is None:
            raise RuntimeError("yaqc is not installed in the active Python environment")

        self.client = yaqc.Client(host=self.config.pax_host, port=self.config.pax_port)
        self._test_mode = False
        return self.client

    def disconnect(self) -> None:
        self.client = None
        self._test_mode = False

    def get_channel_names(self) -> list[str]:
        if self.client is None:
            raise RuntimeError("PAX connection is not established")
        return list(self.client.get_channel_names())

    def enable_test_mode(self) -> None:
        self._test_mode = True
        self.client = None

    def read_polarization(self) -> PAXReading:
        """Read the current polarization state from the PAX.

        This follows the notebook pattern of calling measure() and then reading
        the measured values. In environments without the PAX daemon, the test
        mode can be enabled to return a deterministic placeholder reading.
        """
        if self._test_mode:
            time.sleep(0.05)
            return PAXReading(theta=0.0, eta=0.0, s1=1.0, s2=0.0, s3=0.0, dop=1.0)

        if self.client is None:
            raise RuntimeError("PAX connection is not established")

        self.client.measure()
        time.sleep(0.07)
        data = self.client.get_measured()

        theta = float(data["theta"])
        eta = float(data["eta"])
        dop = float(data["dop"])

        # The notebook exposes theta and eta directly; the Stokes parameters are
        # derived from these angles for the lock-loop model.
        s1 = float(__import__("math").cos(2 * eta) * __import__("math").cos(2 * theta))
        s2 = float(__import__("math").cos(2 * eta) * __import__("math").sin(2 * theta))
        s3 = float(__import__("math").sin(2 * eta))

        return PAXReading(theta=theta, eta=eta, s1=s1, s2=s2, s3=s3, dop=dop)
