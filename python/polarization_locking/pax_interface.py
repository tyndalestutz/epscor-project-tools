from __future__ import annotations

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


class PAXController:
    """Thin wrapper around the PAX1000 YAQC client."""

    def __init__(self, config: Any) -> None:
        self.config = config
        self.client: Optional[Any] = None

    def connect(self) -> Any:
        if yaqc is None:
            raise RuntimeError("yaqc is not installed in the active Python environment")

        self.client = yaqc.Client(host=self.config.pax_host, port=self.config.pax_port)
        return self.client

    def disconnect(self) -> None:
        self.client = None

    def get_channel_names(self) -> list[str]:
        if self.client is None:
            raise RuntimeError("PAX connection is not established")
        return list(self.client.get_channel_names())

    def read_polarization(self) -> PAXReading:
        """Placeholder implementation.

        Replace this with the actual channel mapping for your PAX setup.
        """
        if self.client is None:
            raise RuntimeError("PAX connection is not established")

        raise NotImplementedError(
            "Map the correct PAX channels for theta, eta, and the Stokes parameters before using this loop."
        )
