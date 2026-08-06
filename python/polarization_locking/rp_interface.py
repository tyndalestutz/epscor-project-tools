from __future__ import annotations

from typing import Any, Dict, Optional

try:
    from pyrpl import Pyrpl
except ImportError:  # pragma: no cover - import may be unavailable in some environments
    Pyrpl = None


class RPController:
    """Thin wrapper around the Pyrpl Red Pitaya interface."""

    def __init__(self, config: Any) -> None:
        self.config = config
        self.p = None
        self.pid = None

    def connect(self) -> Any:
        if Pyrpl is None:
            raise RuntimeError("pyrpl is not installed in the active Python environment")

        self.p = Pyrpl(hostname=self.config.rp_hostname, config=self.config.rp_config)
        self.pid = self.p.rp.pid0
        self.pid.input = "in1"
        self.pid.output_direct = "out1"
        return self.p

    def disconnect(self) -> None:
        self.p = None
        self.pid = None

    def configure_pid(self, p_value: Optional[float] = None, i_value: Optional[float] = None, setpoint: Optional[float] = None) -> None:
        if self.pid is None:
            raise RuntimeError("Red Pitaya connection is not established")

        self.pid.pause_gains = "i"
        self.pid.paused = True
        self.pid.ival = 0.0

        if p_value is not None:
            self.pid.p = p_value
        if i_value is not None:
            self.pid.i = i_value
        if setpoint is not None:
            self.pid.setpoint = setpoint

        self.pid.paused = False

    def enable_lock(self) -> None:
        self.configure_pid(
            p_value=self.config.pid_p,
            i_value=self.config.pid_i,
            setpoint=self.config.pid_setpoint,
        )

    def disable_lock(self) -> None:
        if self.pid is None:
            raise RuntimeError("Red Pitaya connection is not established")

        self.pid.pause_gains = "i"
        self.pid.paused = True
        self.pid.ival = 0.0

    def get_pid_state(self) -> Dict[str, Any]:
        if self.pid is None:
            raise RuntimeError("Red Pitaya connection is not established")

        return {
            "p": self.pid.p,
            "i": self.pid.i,
            "setpoint": self.pid.setpoint,
            "paused": self.pid.paused,
            "ival": self.pid.ival,
        }
