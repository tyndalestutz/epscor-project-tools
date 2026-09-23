from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import numpy as np


class _ScopeClient:
    """Limit a borrowed monitor connection to scope and input-mux writes."""

    def __init__(self, client):
        self.client = client

    def reads(self, address, length):
        result = self.client.reads(address, length)
        if result is None:
            raise RuntimeError("RP scope read failed")
        return result

    def writes(self, address, values):
        # Scope input muxes share DSP module IDs with ASGs, but their output
        # routing registers are at +4 and must never be written here.
        for offset in range(len(values)):
            register = address + 4 * offset
            if not (0x40100000 <= register <= 0x40100030 or register in (0x40380000, 0x40390000)):
                raise RuntimeError(f"Scope session refused register write {register:#x}")
        if not self.client.writes(address, values):
            raise RuntimeError("RP scope write failed")


class _ScopeSession:
    """Minimal Pyrpl scope parent; no output modules or persisted settings."""

    _autosave_active = False
    frequency_correction = 1.0


@dataclass(frozen=True)
class PhotodiodeReading:
    """Summary of one Red Pitaya scope capture on the final-output PD."""

    mean_voltage: float
    std_voltage: float
    min_voltage: float
    max_voltage: float
    sample_count: int

class RPController:
    """Thin wrapper around the Pyrpl Red Pitaya interface."""

    def __init__(self, config: Any) -> None:
        self.config = config
        self.p = None
        self.asg1 = None
        self.asg2 = None

    def _clear_output_routes(self) -> None:
        """Disable competing Red Pitaya output modules so ASGs can drive the intended outputs cleanly."""
        if self.p is None:
            return

        for name in ("asg0", "asg1", "pid0", "pid1", "pid2", "iq0", "iq1", "iq2"):
            module = getattr(self.p.rp, name, None)
            if module is None:
                continue
            try:
                module.output_direct = "off"
            except Exception:
                pass

    def connect(self) -> Any:
        try:
            from pyrpl import Pyrpl
        except ImportError as exc:  # pragma: no cover - hardware dependency
            raise RuntimeError("pyrpl is not installed in the active Python environment") from exc

        # We only use Pyrpl as a Python hardware client. Passing gui=False
        # overrides any persisted ``redpitaya.gui`` value in scope_config.yml,
        # preventing Pyrpl from constructing/showing its control window while
        # retaining the normal Red Pitaya, ASG, and scope connections.
        self.p = Pyrpl(hostname=self.config.rp_hostname, config=self.config.rp_config, gui=False)
        # Make sure no other module is still driving the outputs before we use the ASGs.
        self._clear_output_routes()

        # Use the arbitrary signal generator modules directly for DC-like voltage commands.
        self.asg1 = self.p.rp.asg0
        self.asg2 = self.p.rp.asg1

        self.asg1.output_direct = "out1"
        self.asg2.output_direct = "out2"

        self.asg1.setup(
            waveform="dc",
            offset=0.0,
            amplitude=0.0,
            trigger_source="immediately",
        )
        self.asg2.setup(
            waveform="dc",
            offset=0.0,
            amplitude=0.0,
            trigger_source="immediately",
        )

        return self.p

    def connect_scope_only(self) -> Any:
        """Start only a monitor transport; never load FPGA/output settings."""
        import os
        import shlex
        from pathlib import Path
        from types import SimpleNamespace
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from pyrpl.hardware_modules.scope import Scope
            from pyrpl.memory import MemoryTree
            from pyrpl.redpitaya_client import MonitorClient
            from pyrpl.directories import user_config_dir
            import paramiko
            import yaml
        except ImportError as exc:  # pragma: no cover - hardware dependency
            raise RuntimeError("pyrpl is not installed in the active Python environment") from exc

        def unavailable():
            raise RuntimeError("Cannot attach to scope monitor; no FPGA reload or output initialization attempted")

        # Use only SSH credentials from the existing profile, never its module
        # settings. A separate port avoids stopping an existing Pyrpl client.
        profile = Path(user_config_dir) / f"{self.config.rp_config}.yml"
        connection = yaml.safe_load(profile.read_text()).get("redpitaya", {})
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self._scope_ssh = ssh
        client = None
        try:
            ssh.connect(self.config.rp_hostname, port=connection.get("sshport", 22),
                        username=connection.get("user", "root"), password=connection.get("password"),
                        timeout=5, banner_timeout=5, auth_timeout=5)
            _, stdout, _ = ssh.exec_command("cat /tmp/loaded_fpga.inf", timeout=5)
            if "pyrpl" not in stdout.read().decode().lower():
                raise RuntimeError("RP is not marked as running the Pyrpl FPGA; refusing to reload it during passive measurement")
            executable = str(Path(connection.get("serverdirname", "/opt/pyrpl")) / connection.get("monitor_server_name", "monitor_server"))
            _, stdout, stderr = ssh.exec_command(f"{shlex.quote(executable)} {self.config.rp_scope_port}", timeout=5)
            time.sleep(0.2)
            if stdout.channel.exit_status_ready():
                raise RuntimeError(f"Scope monitor did not start: {stderr.read().decode().strip()}")
            client = MonitorClient(self.config.rp_hostname, self.config.rp_scope_port, restartserver=unavailable)
            session = _ScopeSession()
            session.client = _ScopeClient(client)
            # Read raw registers before Scope property defaults can normalize
            # them. Restore these exact values when the session closes.
            session.previous = {address: session.client.reads(address, 1).copy()
                                for address in (0x40100004, 0x40100010, 0x40100014, 0x40100028, 0x40380000, 0x40390000)}
            session.c = MemoryTree()  # in-memory only
            session.parent = session
            session.scope = Scope(session, "scope")
            self.p = SimpleNamespace(rp=session)
        except BaseException:
            if client is not None:
                client.close()
            ssh.close()
            self._scope_ssh = None
            raise
        return self.p

    def disconnect_scope_only(self) -> None:
        """Release a scope-only Pyrpl connection without touching RP outputs."""
        try:
            if self.p is not None:
                session = self.p.rp
                try:
                    for address, values in session.previous.items():
                        session.client.writes(address, values)
                finally:
                    session.client.client.close()
        finally:
            self.p = None
            if getattr(self, "_scope_ssh", None) is not None:
                self._scope_ssh.close()
                self._scope_ssh = None

    def disconnect(self) -> None:
        try:
            if self.p is not None:
                if self.asg1 is not None and self.asg2 is not None:
                    self.set_output_zero()
                else:
                    self._clear_output_routes()
        finally:
            self.asg1 = None
            self.asg2 = None
            self.p = None

    def set_output_voltage(self, v1: float, v2: float) -> None:
        if self.p is None:
            raise RuntimeError("Red Pitaya connection is not established")
        if self.asg1 is None or self.asg2 is None:
            raise RuntimeError("ASG outputs are not initialized")

        self._validate_output_voltage(v1, v2)

        # Use DC offsets on the ASGs so the outputs are explicit and match the repository examples.
        self.asg1.setup(
            waveform="dc",
            offset=float(v1),
            amplitude=0.0,
            trigger_source="immediately",
        )
        self.asg2.setup(
            waveform="dc",
            offset=float(v2),
            amplitude=0.0,
            trigger_source="immediately",
        )

    def set_output_sweep(self, v1_values: list[float], v2_values: list[float], delay_s: float = 0.1) -> None:
        if len(v1_values) != len(v2_values):
            raise ValueError("v1_values and v2_values must have the same length")

        for v1, v2 in zip(v1_values, v2_values):
            self.set_output_voltage(v1, v2)
            time.sleep(delay_s)

    def set_phi2_sine(self, *, offset: float, amplitude: float, frequency_hz: float) -> None:
        """Drive OUT2/phi2 with a bounded sine; OUT1/phi1 remains at zero."""
        if self.p is None or self.asg1 is None or self.asg2 is None:
            raise RuntimeError("Red Pitaya connection is not established")
        if frequency_hz <= 0.0 or amplitude < 0.0:
            raise ValueError("Sine frequency must be positive and amplitude non-negative")
        self._validate_output_voltage(0.0, offset - amplitude)
        self._validate_output_voltage(0.0, offset + amplitude)
        self.asg1.setup(waveform="dc", offset=0.0, amplitude=0.0, trigger_source="immediately")
        self.asg2.setup(
            waveform="sin",
            frequency=float(frequency_hz),
            offset=float(offset),
            amplitude=float(amplitude),
            trigger_source="immediately",
        )

    def set_phi1_sine(self, *, offset: float, amplitude: float, frequency_hz: float) -> None:
        """Drive OUT1/phi1 with a bounded sine; OUT2/phi2 remains at zero."""
        if self.p is None or self.asg1 is None or self.asg2 is None:
            raise RuntimeError("Red Pitaya connection is not established")
        if frequency_hz <= 0.0 or amplitude < 0.0:
            raise ValueError("Sine frequency must be positive and amplitude non-negative")
        self._validate_output_voltage(offset - amplitude, 0.0)
        self._validate_output_voltage(offset + amplitude, 0.0)
        self.asg2.setup(waveform="dc", offset=0.0, amplitude=0.0, trigger_source="immediately")
        self.asg1.setup(
            waveform="sin",
            frequency=float(frequency_hz),
            offset=float(offset),
            amplitude=float(amplitude),
            trigger_source="immediately",
        )

    @contextmanager
    def photodiode_monitor(self, *, input_channel: str | None = None):
        """Capture input voltage; an explicit channel selects a fresh immediate trace.

        Values are literal volts, with no photodiode or attenuation correction.
        """
        if self.p is None:
            raise RuntimeError("Red Pitaya connection is not established")
        scope = self.p.rp.scope
        previous = {
            "input1": scope.input1,
            "duration": scope.duration,
            "decimation": scope.decimation,
        }
        if input_channel is not None:
            if input_channel not in {"in1", "in2"}:
                raise ValueError("Input channel must be in1 or in2")
            for name in ("average", "trigger_source", "trigger_delay", "ch1_active", "ch2_active", "rolling_mode", "trace_average"):
                previous[name] = getattr(scope, name)
            delay_register = scope._trigger_delay_register
        try:
            scope.input1 = input_channel or self.config.pd_input
            scope.duration = self.config.pd_scope_duration_s
            scope.decimation = self.config.pd_scope_decimation
            if input_channel is not None:
                scope.setup(average=True, trigger_source="immediately", trigger_delay=0.0,
                            ch1_active=True, ch2_active=False, rolling_mode=False, trace_average=1)
            yield lambda: self._read_photodiode(scope)
        finally:
            try:
                if input_channel is not None:
                    scope.stop()
            finally:
                for name, value in previous.items():
                    setattr(scope, name, value)
                if input_channel is not None:
                    scope._trigger_delay_register = delay_register

    def set_phase_waveform(self, *, axis: str, waveform: str, offset: float,
                           amplitude: float, frequency_hz: float) -> dict:
        """Drive the selected phase output; hold the other output at zero."""
        waveforms = {"sin": "sin", "cos": "cos", "triangle": "ramp",
                     "sawtooth": "halframp", "square": "square"}
        if axis not in {"phi1", "phi2"} or waveform not in waveforms:
            raise ValueError("Unknown phase axis or contrast waveform")
        if self.p is None or self.asg1 is None or self.asg2 is None:
            raise RuntimeError("Red Pitaya connection is not established")
        if not np.all(np.isfinite([offset, amplitude, frequency_hz])) or amplitude <= 0 or frequency_hz <= 0:
            raise ValueError("Waveform settings must be finite; amplitude/frequency must be positive")
        self._validate_output_voltage(offset - amplitude, offset + amplitude)
        selected, other = (self.asg1, self.asg2) if axis == "phi1" else (self.asg2, self.asg1)
        other.setup(waveform="dc", offset=0.0, amplitude=0.0, trigger_source="immediately")
        selected.periodic = True
        selected.setup(waveform=waveforms[waveform], frequency=frequency_hz, offset=offset,
                       amplitude=amplitude, trigger_source="immediately", cycles_per_burst=0)
        return {"axis": axis, "output": "out1" if axis == "phi1" else "out2",
                "waveform": waveform, "asg_waveform": str(selected.waveform),
                "frequency_hz": float(selected.frequency), "offset_v": float(selected.offset),
                "amplitude_v": float(selected.amplitude)}

    def _read_photodiode(self, scope: Any) -> PhotodiodeReading:
        trace = np.asarray(scope.single(timeout=self.config.pd_scope_timeout_s)[0], dtype=float)
        if trace.size == 0:
            raise RuntimeError("Red Pitaya scope returned an empty photodiode trace")
        return PhotodiodeReading(
            mean_voltage=float(np.mean(trace)),
            std_voltage=float(np.std(trace)),
            min_voltage=float(np.min(trace)),
            max_voltage=float(np.max(trace)),
            sample_count=int(trace.size),
        )

    def _validate_output_voltage(self, v1: float, v2: float) -> None:
        lower = self.config.rp_output_min_voltage
        upper = self.config.rp_output_max_voltage
        if not 0.0 <= lower <= upper <= 1.0:
            raise ValueError("Configured RP limits must remain within 0-1 V")
        if not (lower <= v1 <= upper and lower <= v2 <= upper):
            raise ValueError(
                f"RP outputs must remain within [{lower:.3f}, {upper:.3f}] V; "
                f"received ({v1:.3f}, {v2:.3f}) V"
            )

    def set_output_zero(self) -> None:
        self.set_output_voltage(0.0, 0.0)

    @contextmanager
    def photodiode_pid_lock(
        self,
        *,
        setpoint: float,
        initial_output: float,
        proportional_gain: float,
        integral_gain_hz: float,
    ):
        """Route the FPGA PID used in the dynamic-locking notebook: IN1→OUT1.

        The ASG is detached first, the PID is configured while paused with its
        integrator seeded at the present OUT1 command, then unpaused.  Output
        limits enforce the experiment's physical 0–1 V RP range.
        """
        if self.p is None or self.asg1 is None:
            raise RuntimeError("Red Pitaya connection is not established")
        self._validate_output_voltage(initial_output, 0.0)
        pid = self.p.rp.pid1
        self.asg1.output_direct = "off"
        # Freeze both terms while routing/seeding the block.  Freezing only I
        # leaves the proportional path live during a potentially transient
        # re-route of OUT1.
        pid.pause_gains = "pi"
        pid.paused = True
        pid.input = self.config.pd_input
        pid.output_direct = "out1"
        pid.min_voltage = self.config.rp_output_min_voltage
        pid.max_voltage = self.config.rp_output_max_voltage
        pid.ival = float(initial_output)
        pid.p = float(proportional_gain)
        pid.i = float(integral_gain_hz)
        pid.setpoint = float(setpoint)
        pid.paused = False
        try:
            yield pid
        finally:
            pid.pause_gains = "pi"
            pid.paused = True
            pid.ival = 0.0
            pid.output_direct = "off"
            self.asg1.output_direct = "out1"
            self.asg1.setup(waveform="dc", offset=0.0, amplitude=0.0, trigger_source="immediately")
