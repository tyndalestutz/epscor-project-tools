"""Bounded active Pyrpl drive and frozen paired capture, based on eom_raw_capture.

No imports of experiment runners. Hardware access starts only on context entry.
Raw counts and scope/readback evidence remain available even after cleanup.
"""
import base64
import hashlib
import inspect
import math
import os
from pathlib import Path
import time

from .red_pitaya import RPCleanupError


_OUTPUTS = {"out1": "asg0", "out2": "asg1"}
_ROUTES = ("asg0", "asg1", "pid0", "pid1", "pid2", "iq0", "iq1", "iq2")


def _finite(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


class RPDrive:
    def __init__(self, host, profile, fingerprint, limits, frequency_limits, max_peak, correction):
        if not host or not profile or not fingerprint:
            raise ValueError("Specify RP host, connection profile and SSH host-key fingerprint")
        if not isinstance(limits, dict) or not limits or set(limits) - set(_OUTPUTS):
            raise ValueError("Provide command limits for out1 and/or out2")
        self.limits = {}
        for output, bounds in limits.items():
            if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
                raise ValueError(f"{output} command limits must be [minimum, maximum]")
            lo, hi = (_finite(v, f"{output} limit") for v in bounds)
            if not -1 <= lo <= 0 <= hi <= 1 or lo == hi:
                raise ValueError("RP command limits must lie within +/-1 V and include zero cleanup")
            self.limits[output] = (lo, hi)
        if not isinstance(frequency_limits, (list, tuple)) or len(frequency_limits) != 2:
            raise ValueError("frequency_limits_hz must be [minimum, maximum]")
        self.frequency_limits = tuple(_finite(v, "frequency limit") for v in frequency_limits)
        self.max_peak = _finite(max_peak, "max_peak_v")
        self.correction = _finite(correction, "frequency_correction")
        if not 0 < self.frequency_limits[0] < self.frequency_limits[1] or self.max_peak <= 0 or self.correction <= 0:
            raise ValueError("Frequency bounds, peak limit and clock correction must be positive")
        self.host, self.profile, self.fingerprint = host, profile, fingerprint
        self.p = None
        self.last_command = self.last_capture = None
        self.connection_evidence = None
        self.cleanup_evidence = {"status": "NOT_CONNECTED"}

    def __enter__(self):
        try:
            self.connect()
        except BaseException as error:
            try:
                self.close()
            except BaseException as cleanup_error:
                raise error from cleanup_error
            raise
        return self

    def __exit__(self, kind, error, traceback):
        try:
            self.close()
        except BaseException as cleanup_error:
            if error is not None:
                raise error from cleanup_error
            raise
        return False

    def connect(self):
        if self.p is not None:
            raise RuntimeError("Drive session is already connected")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from pyrpl import Pyrpl
        from pyrpl.directories import user_config_dir
        import paramiko
        import yaml
        profile = Path(self.profile).expanduser()
        if not profile.is_file():
            profile = Path(user_config_dir) / f"{self.profile}.yml"
        connection = yaml.safe_load(profile.read_text())["redpitaya"]
        transport = paramiko.Transport((self.host, connection.get("sshport", 22)))
        try:
            transport.start_client(timeout=5)
            key = transport.get_remote_server_key()
            actual = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
            if actual != self.fingerprint:
                raise RuntimeError("RP SSH host fingerprint mismatch; active initialization refused")
        finally:
            transport.close()
        allowed = {"port", "sshport", "user", "password", "delay", "serverdirname",
                   "monitor_server_name", "frequency_correction"}
        settings = {k: v for k, v in connection.items() if k in allowed}
        # Reuse the raw EOM tool's in-memory connection-only initialization.
        self.p = Pyrpl.__new__(Pyrpl)
        Pyrpl.__init__(self.p, hostname=self.host, config="", gui=False, **settings)
        self.p._autosave_active = False
        def no_restart():
            raise RuntimeError("Active-session transport failed; automatic server/FPGA restart refused")
        self.p.rp.client._restartserver = no_restart
        for name in _ROUTES:
            module = getattr(self.p.rp, name)
            module._autosave_active = False
            module.output_direct = "off"
        for name in _OUTPUTS.values():
            getattr(self.p.rp, name).setup(waveform="dc", offset=0., amplitude=0., trigger_source="immediately",
                                          start_phase=0., cycles_per_burst=0)
        if not math.isclose(float(self.p.rp.frequency_correction), self.correction, rel_tol=1e-12):
            raise RuntimeError("Pyrpl clock correction differs from the declared bench setting")
        self.scope = self.p.rp.scope
        self.scope._autosave_active = False
        self.scope.stop()
        self.scope.setup(input1="in1", input2="in2", average=False, trace_average=1,
                         rolling_mode=False, ch1_active=True, ch2_active=True,
                         trigger_source="immediately", trigger_delay=0.)
        if int(self.scope.data_length) != 16384:
            raise RuntimeError("This interface expects the established 16384-sample Pyrpl scope")
        initial = self.readback_all()
        if any(s["offset_v"] != 0 or s["amplitude_v"] != 0 or s["output_direct"] != "off" for s in initial.values()):
            raise RuntimeError("Initial zero/off ASG settings were not confirmed by register readback")
        self.connection_evidence = {"host": self.host, "mode": "active", "ssh_host_key_sha256": actual,
                                    "frequency_correction": self.correction,
                                    "initial_asg_readbacks": initial,
                                    "driver_source_text": {
                                        name: Path(inspect.getsourcefile(type(module))).read_text()
                                        for name, module in (("scope.py", self.scope), ("asg.py", self.p.rp.asg0))}}

    def readback(self, output):
        if self.p is None:
            raise RuntimeError("Enter the drive session first")
        asg = getattr(self.p.rp, _OUTPUTS[output])
        return {"waveform": str(asg.waveform), "offset_v": float(asg.offset),
                "amplitude_v": float(asg.amplitude), "frequency_hz": float(asg.frequency),
                "output_direct": str(asg.output_direct), "start_phase_deg": float(asg.start_phase),
                "cycles_per_burst": int(asg.cycles_per_burst), "random_phase": bool(asg.random_phase)}

    def readback_all(self):
        return {output: self.readback(output) for output in _OUTPUTS}

    def _validate(self, output, waveform, offset, peak, frequency):
        if output not in self.limits or waveform not in {"dc", "sin"}:
            raise ValueError("Select an output with approved limits and waveform 'dc' or 'sin'")
        offset, peak, frequency = (_finite(v, name) for v, name in
                                   ((offset, "offset_v"), (peak, "peak_v"), (frequency, "frequency_hz")))
        lo, hi = self.limits[output]
        if peak < 0 or not lo <= offset - peak <= offset + peak <= hi:
            raise ValueError("Waveform extrema exceed approved RP command limits")
        if waveform == "dc" and peak != 0:
            raise ValueError("DC requires peak_v=0")
        if waveform == "sin" and (not 0 < peak <= self.max_peak or
                                  not self.frequency_limits[0] <= frequency <= self.frequency_limits[1]):
            raise ValueError("Sine peak/frequency exceeds approved limits")
        return offset, peak, frequency

    def set_waveform(self, output, *, waveform="sin", offset_v, peak_v, frequency_hz, ramp_s=.2):
        offset, peak, frequency = self._validate(output, waveform, offset_v, peak_v, frequency_hz)
        ramp_s = _finite(ramp_s, "ramp_s")
        if ramp_s < 0:
            raise ValueError("ramp_s must be nonnegative")
        if self.p is None:
            raise RuntimeError("Enter the drive session first")
        requested = dict(output=output, waveform=waveform, offset_v=offset,
                         peak_v=peak, frequency_hz=frequency, ramp_s=ramp_s)
        # Quantized commands must also stay inside the approved interval. Retain
        # the original request separately; do not silently round a rail upward.
        lo, hi = self.limits[output]
        min_code, max_code = max(-8192, math.ceil(lo*8192)), min(8191, math.floor(hi*8192))
        offset_command = min(max(round(offset*8192), min_code), max_code) / 8192
        peak_command = math.floor(min(peak, offset_command-lo, hi-offset_command)*8192) / 8192 if waveform == "sin" else 0.
        self._validate(output, waveform, offset_command, peak_command, frequency)
        asg = getattr(self.p.rp, _OUTPUTS[output])
        started = time.monotonic()
        try:
            previous = self.readback(output)
            self._validate(output, "dc", previous["offset_v"], 0., 0.)
            asg.output_direct = "off"
            asg.setup(waveform="dc", offset=previous["offset_v"], amplitude=0., trigger_source="immediately",
                      start_phase=0., cycles_per_burst=0)
            asg.output_direct = output
            steps = max(1, math.ceil(ramp_s / .02))
            for index in range(1, steps + 1):
                value = previous["offset_v"] + (offset_command - previous["offset_v"]) * index / steps
                asg.offset = min(max(round(value*8192), min_code), max_code) / 8192
                self._validate(output, "dc", float(asg.offset), 0., 0.)
                if ramp_s:
                    time.sleep(ramp_s / steps)
            asg.output_direct = "off"
            # Round AC peak down, as in the raw tool. Do not increase the request.
            asg.setup(waveform=waveform, offset=offset_command, amplitude=peak_command,
                      frequency=frequency if waveform == "sin" else 1.,
                      start_phase=0., cycles_per_burst=0, trigger_source="immediately")
            actual = self.readback(output)
            if actual["cycles_per_burst"] != 0 or actual["random_phase"] or actual["start_phase_deg"] != 0:
                raise RuntimeError("ASG continuous/start-phase settings did not match the request")
            self._validate(output, actual["waveform"], actual["offset_v"], actual["amplitude_v"], actual["frequency_hz"])
            if abs(actual["offset_v"] - offset_command) > 1/8192 or abs(actual["amplitude_v"] - peak_command) > 1/8192:
                raise RuntimeError("ASG amplitude/offset readback disagrees with request")
            if waveform == "sin" and abs(actual["frequency_hz"] - frequency) > 125e6*self.correction/2**30:
                raise RuntimeError("ASG frequency readback disagrees with request")
            asg.output_direct = output
            self.last_command = {"requested": requested, "actual": self.readback(output),
                                 "quantized_setpoints": {"offset_v": offset_command, "peak_v": peak_command},
                                 "requested_monotonic_s": started, "received_monotonic_s": time.monotonic(),
                                 "meaning": "ASG settings/readbacks; not measured delivered voltage"}
            return self.last_command
        except BaseException as error:
            try:
                self.close()
            except BaseException as cleanup_error:
                raise error from cleanup_error
            raise

    def set_dc_offset(self, output, *, offset_v, ramp_s=.2):
        """Ramp an already enabled DC output without interrupting its route."""
        offset, _, _ = self._validate(output, "dc", offset_v, 0., 0.)
        ramp_s = _finite(ramp_s, "ramp_s")
        if ramp_s < 0:
            raise ValueError("ramp_s must be nonnegative")
        previous = self.readback(output)
        if previous["waveform"] != "dc" or previous["amplitude_v"] != 0 or previous["output_direct"] != output:
            raise RuntimeError("Enable a DC waveform on this output before changing its offset")
        self._validate(output, "dc", previous["offset_v"], 0., 0.)
        lo, hi = self.limits[output]
        min_code, max_code = math.ceil(lo*8192), min(8191, math.floor(hi*8192))
        target = min(max(round(offset*8192), min_code), max_code) / 8192
        asg = getattr(self.p.rp, _OUTPUTS[output])
        started = time.monotonic()
        try:
            steps = max(1, math.ceil(ramp_s / .02))
            for index in range(1, steps + 1):
                value = previous["offset_v"] + (target - previous["offset_v"]) * index / steps
                asg.offset = min(max(round(value*8192), min_code), max_code) / 8192
                self._validate(output, "dc", float(asg.offset), 0., 0.)
                if ramp_s:
                    time.sleep(ramp_s / steps)
            actual = self.readback(output)
            if (actual["waveform"] != "dc" or actual["amplitude_v"] != 0 or actual["output_direct"] != output or
                    abs(actual["offset_v"] - target) > 1/8192):
                raise RuntimeError("DC offset/route readback disagrees with request")
            self.last_command = {"requested": dict(output=output, waveform="dc", offset_v=offset,
                                                  peak_v=0., frequency_hz=0., ramp_s=ramp_s),
                                 "actual": actual, "quantized_setpoints": {"offset_v": target, "peak_v": 0.},
                                 "requested_monotonic_s": started, "received_monotonic_s": time.monotonic(),
                                 "meaning": "DC bias update with route held; not measured delivered voltage"}
            return self.last_command
        except BaseException as error:
            try:
                self.close()
            except BaseException as cleanup_error:
                raise error from cleanup_error
            raise

    def _scope_settings(self):
        scope = self.scope
        return {"input1": scope.input1, "input2": scope.input2, "decimation": int(scope.decimation),
                "sample_interval_s": float(scope.sampling_time), "duration_s": float(scope.duration),
                "sample_count": int(scope.data_length), "fpga_average": bool(scope.average),
                "trace_average": int(scope.trace_average), "rolling_mode": bool(scope.rolling_mode),
                "trigger_source": str(scope.trigger_source), "trigger_delay_s": float(scope.trigger_delay),
                "ch1_active": bool(scope.ch1_active), "ch2_active": bool(scope.ch2_active)}

    def capture(self, *, decimation, full_scale_v, timeout_s=15.):
        import numpy as np
        if self.p is None:
            raise RuntimeError("Enter the drive session first")
        if type(decimation) is not int or decimation not in {2**i for i in range(17)}:
            raise ValueError("decimation must be a power of two in 1..65536")
        if (not isinstance(full_scale_v, dict) or set(full_scale_v) != {"in1", "in2"} or
                any(isinstance(v, bool) or v not in (1., 20.) for v in full_scale_v.values())):
            raise ValueError("Declare full_scale_v={'in1': 1 or 20, 'in2': 1 or 20}")
        timeout_s = _finite(timeout_s, "timeout_s")
        self.scope.decimation = decimation
        before = self._scope_settings()
        if not all(math.isfinite(before[k]) and before[k] > 0 for k in ("sample_interval_s", "duration_s")):
            raise RuntimeError("Scope returned invalid timing settings")
        if timeout_s <= before["duration_s"]:
            raise ValueError("Capture timeout must exceed the driver-reported buffer duration")
        self.last_capture = {"metadata": {"scope": before, "full_scale_v": dict(full_scale_v),
                                           "frequency_correction": self.correction,
                                           "asg_before": self.readback_all(),
                                           "requested_monotonic_s": time.monotonic()}}
        result, meta = self.last_capture, self.last_capture["metadata"]
        try:
            self.scope._start_trace_acquisition()
            deadline = time.monotonic() + timeout_s
            while not self.scope.curve_ready():
                if time.monotonic() > deadline:
                    raise TimeoutError("Scope capture timeout")
                time.sleep(.002)
            pointer, trigger = int(self.scope._write_pointer_current), int(self.scope._write_pointer_trigger)
            delay = int(self.scope._trigger_delay_register)
            shift = -(trigger + delay + 1)
            meta.update(write_pointer=pointer, trigger_pointer=trigger,
                        trigger_delay_samples=delay, ring_shift_samples=shift)
            result["raw_in1"] = self.scope._rawdata_ch1.copy()
            result["raw_in2"] = self.scope._rawdata_ch2.copy()
            raw = np.roll(np.stack([result["raw_in1"], result["raw_in2"]]), shift, axis=1)
            result["raw_counts"] = raw
            result["input_volts"] = raw.astype(float) / 8192 * np.array([full_scale_v["in1"], full_scale_v["in2"]])[:, None]
            # Scope reports nominal 8-ns-clock spacing; retain that report and the
            # explicitly clock-corrected time model separately.
            result["time_s"] = np.arange(raw.shape[1]) * before["sample_interval_s"] / self.correction
            frozen = bool(self.scope.curve_ready() and pointer == self.scope._write_pointer_current and
                          trigger == self.scope._write_pointer_trigger and delay == self.scope._trigger_delay_register)
            public_agrees = bool(np.array_equal(raw.astype(float)/8192, self.scope._get_trace()))
            frozen_after_public = bool(self.scope.curve_ready() and pointer == self.scope._write_pointer_current and
                                       trigger == self.scope._write_pointer_trigger and delay == self.scope._trigger_delay_register)
            meta.update(scope_after=self._scope_settings(), frozen=frozen, public_trace_agrees=public_agrees,
                        frozen_after_public_read=frozen_after_public, ring_shift_samples=shift,
                        write_pointer=pointer, trigger_pointer=trigger, trigger_delay_samples=delay,
                        asg_after=self.readback_all(), received_monotonic_s=time.monotonic())
            timestamp = getattr(self.scope, "trigger_timestamp", None)
            meta["trigger_timestamp_cycles"] = None if timestamp is None else int(timestamp)
            issues = []
            if raw.shape != (2, 16384) or raw.dtype.kind != "i" or np.any(raw < -8192) or np.any(raw > 8191):
                issues.append("Malformed signed paired ADC buffer")
            if not frozen or not frozen_after_public or not public_agrees or before != meta["scope_after"]:
                issues.append("Scope changed or paired buffer was not frozen")
            if meta["asg_before"] != meta["asg_after"]:
                issues.append("ASG settings changed during capture")
            if np.any(np.abs(raw.astype(np.int32)) >= 8190):
                issues.append("ADC clipping or near-rail samples")
            if (before["input1"] != "in1" or before["input2"] != "in2" or before["fpga_average"] or
                    before["trace_average"] != 1 or before["rolling_mode"] or before["trigger_source"] != "immediately"):
                issues.append("Scope mapping or averaging differs from raw paired capture")
            if not before["ch1_active"] or not before["ch2_active"] or before["trigger_delay_s"] != 0:
                issues.append("Both inputs and zero-delay immediate triggering are required")
            meta["issues"] = issues
            return result
        except BaseException as error:
            meta.update(error=f"{type(error).__name__}: {error}", received_monotonic_s=time.monotonic())
            raise

    def close(self):
        if self.p is None:
            return
        errors, final = [], {}
        rp = getattr(self.p, "rp", None)

        def attempt(step, action):
            try:
                action()
            except BaseException as error:
                errors.append((step, error))

        if rp is not None:
            # Stop software acquisition/timer callbacks before zeroing hardware.
            try:
                modules = list(getattr(self.p, "modules", ()))
            except BaseException as error:
                errors.append(("module enumeration", error))
                modules = []
            for module in modules:
                attempt("module timer cleanup", module._clear)
            for name in _ROUTES:
                attempt(f"{name} route off", lambda name=name: setattr(getattr(rp, name), "output_direct", "off"))
            for output, name in _OUTPUTS.items():
                def zero(output=output, name=name):
                    getattr(rp, name).setup(waveform="dc", offset=0., amplitude=0., trigger_source="immediately",
                                            start_phase=0., cycles_per_burst=0)
                    final[output] = self.readback(output)
                    if final[output]["output_direct"] != "off" or final[output]["offset_v"] != 0 or final[output]["amplitude_v"] != 0:
                        raise RuntimeError("Zero/off register readback failed")
                attempt(f"{output} zero", zero)
            if hasattr(rp, "scope"):
                attempt("scope stop", rp.scope.stop)
            # Close owned transports directly; Pyrpl.end_all() issues a broad
            # remote killall monitor_server and would also stop passive monitors.
            client = getattr(rp, "client", None)
            if client is not None:
                attempt("monitor close", client.close)
                raw_socket = getattr(client, "socket", None)
                if raw_socket is not None:
                    attempt("monitor socket close", raw_socket.close)
            ssh = getattr(rp, "ssh", None)
            if ssh is not None:
                for name in ("channel", "scp", "ssh"):
                    resource = getattr(ssh, name, None)
                    if resource is not None:
                        attempt(f"SSH {name} close", resource.close)
        self.cleanup_evidence = {"status": "FAILED" if errors else "COMPLETED", "asg_readbacks": final,
                                 "errors": [f"{step}: {type(error).__name__}: {error}" for step, error in errors],
                                 "meaning": "Routes off and DC zero settings; no measured zero-electrode claim"}
        self.p = None
        if errors:
            failure = RPCleanupError(errors)
            for _, error in errors:
                if not isinstance(error, Exception):
                    raise error from failure
            raise failure from errors[0][1]
