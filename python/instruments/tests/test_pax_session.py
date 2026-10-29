"""Owned notebook PAX lifecycle with fake clients, processes and USB only."""
import io
import json
from pathlib import Path
import signal
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import tomli

from instruments import PAX
from instruments import _pax_daemon, _pax_session as sessions


class Clock:
    now = 0.

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture
def owned(monkeypatch, tmp_path):
    config = tmp_path / "bench.toml"
    config.write_text('[thorlabs-pax1000]\nserial="TEST"\nport=38400\nvelocity=60\nmeasurement_mode=5\nautorange=true\n')
    state = SimpleNamespace(processes=[], devices=[], connects=0, refused=0,
                            startup_exit=False, foreign=False, timeout=False,
                            stop_ok=True, usb_ok=True, client_close_error=False,
                            launch_error=None, interrupted=False)

    class Process:
        def __init__(self, args, **kwargs):
            if state.launch_error:
                raise state.launch_error
            self.args, self.kwargs = args, kwargs
            self.root = Path(args[-1])
            self.returncode = None
            self.stdin = Mock()
            self.stdin.close.side_effect = self.stop
            self.kill = Mock(side_effect=lambda: setattr(self, "returncode", -9))
            self.wait = Mock(side_effect=self.wait_for_exit)
            kwargs["stdout"].write("fake PAX daemon diagnostic\n")
            kwargs["stdout"].flush()
            state.processes.append(self)

        def poll(self):
            if state.startup_exit:
                self.returncode = 1
            return self.returncode

        def stop(self):
            if state.timeout:
                return
            receipt = dict(usb_opened=True, motor_stop_command_succeeded=state.stop_ok,
                           usb_closed=state.usb_ok, errors=[], parent_pipe_closed=True)
            (self.root / "shutdown.json").write_text(json.dumps(receipt))
            if self.returncode is None:
                self.returncode = 0

        def wait_for_exit(self, timeout):
            if self.returncode is None:
                raise subprocess.TimeoutExpired(self.args, timeout)
            return self.returncode

    class Device(PAX):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.close_calls = 0
            state.devices.append(self)

        def connect(self):
            state.connects += 1
            if state.interrupted:
                raise KeyboardInterrupt()
            if state.connects <= state.refused:
                raise ConnectionRefusedError()
            path = Path("/not/our/config.toml") if state.foreign else state.processes[-1].root / "pax1000.toml"
            self._client = SimpleNamespace(get_config_filepath=lambda: str(path),
                                           id=lambda: {"serial": "TEST"})
            return self

        def close(self):
            self.close_calls += 1
            if state.client_close_error:
                raise RuntimeError("client close failed")
            self._client = None

    monkeypatch.setattr(sessions, "_ensure_port_free", Mock())
    monkeypatch.setattr(sessions.subprocess, "Popen", Process)
    monkeypatch.setattr(sessions, "time", Clock())
    state.config, state.Device = config, Device
    return state


def test_local_session_launches_current_interpreter_verifies_and_stops(owned):
    original = owned.config.read_bytes()
    with owned.Device.local_session(config_path=owned.config) as device:
        process = owned.processes[0]
        assert process.args[0] == sys.executable
        assert process.args[1].endswith("_pax_daemon.py")
        assert process.kwargs["stdin"] == subprocess.PIPE
        assert process.kwargs["start_new_session"] is True
        effective = tomli.loads((process.root / "pax1000.toml").read_text())
        assert effective["thorlabs-pax1000"]["host"] == "127.0.0.1"
        assert effective["thorlabs-pax1000"]["velocity"] == 60
        assert device._client is not None
    assert device._client is None
    assert device.shutdown_evidence["motor_stop_command_succeeded"]
    assert "fake PAX daemon diagnostic" in device.daemon_log
    assert device.session_configuration["source_toml"] == original.decode()
    process.stdin.close.assert_called_once()
    process.kill.assert_not_called()
    assert not process.root.exists()
    assert owned.config.read_bytes() == original


@pytest.mark.parametrize("error", [RuntimeError("cell failed"), KeyboardInterrupt()])
def test_body_failure_or_interrupt_stops_daemon_and_preserves_error(owned, error):
    with pytest.raises(type(error)) as failure:
        with owned.Device.local_session(config_path=owned.config):
            raise error
    assert failure.value is error
    assert owned.processes[0].poll() == 0
    assert not owned.processes[0].root.exists()


def test_startup_polls_through_refusals_and_closes_after_interrupt(owned):
    owned.refused = 3
    with owned.Device.local_session(config_path=owned.config):
        assert owned.connects == 4
    owned.interrupted = True
    with pytest.raises(KeyboardInterrupt):
        with owned.Device.local_session(config_path=owned.config):
            pytest.fail("startup should not yield")
    assert owned.processes[-1].poll() == 0


def test_startup_timeout_stops_owned_child(owned):
    owned.refused = 100
    with pytest.raises(TimeoutError, match="did not become ready"):
        with owned.Device.local_session(config_path=owned.config, startup_timeout_s=.2):
            pytest.fail("startup should not yield")
    assert owned.processes[0].poll() == 0
    assert "fake PAX" in owned.devices[0].daemon_log


def test_startup_crash_includes_daemon_log_and_reaps_child(owned):
    owned.startup_exit = True
    with pytest.raises(RuntimeError, match="fake PAX daemon diagnostic"):
        with owned.Device.local_session(config_path=owned.config):
            pytest.fail("startup should not yield")
    owned.processes[0].wait.assert_called_once()


def test_foreign_endpoint_is_not_yielded_or_sent_shutdown_commands(owned):
    owned.foreign = True
    with pytest.raises(RuntimeError, match="not the daemon started"):
        with owned.Device.local_session(config_path=owned.config):
            pytest.fail("foreign daemon should not yield")
    # Fake client offers no direct_serial_write/shutdown methods; only the
    # owned child is stopped through its private parent pipe.
    assert owned.devices[0]._client is None
    owned.processes[0].stdin.close.assert_called_once()


def test_occupied_port_prevents_process_launch_or_client_connect(owned, monkeypatch):
    monkeypatch.setattr(sessions, "_ensure_port_free", Mock(side_effect=RuntimeError("port occupied")))
    with pytest.raises(RuntimeError, match="port occupied"):
        with owned.Device.local_session(config_path=owned.config):
            pytest.fail("occupied port should not yield")
    assert not owned.processes and owned.connects == 0


def test_launch_failure_closes_temporary_resources(owned, monkeypatch, tmp_path):
    folder = tmp_path / "failed-launch"
    folder.mkdir()
    monkeypatch.setattr(sessions.tempfile, "mkdtemp", lambda **kwargs: str(folder))
    owned.launch_error = OSError("launch failed")
    with pytest.raises(OSError, match="launch failed"):
        with owned.Device.local_session(config_path=owned.config):
            pytest.fail("launch should not yield")
    assert not folder.exists()


@pytest.mark.parametrize("flag", ["stop_ok", "usb_ok"])
def test_unconfirmed_motor_stop_or_usb_close_raises(owned, flag):
    setattr(owned, flag, False)
    with pytest.raises(sessions.PAXCleanupError, match="could not confirm"):
        with owned.Device.local_session(config_path=owned.config):
            pass
    assert owned.processes[0].poll() == 0


def test_close_error_still_stops_and_reaps_owned_process(owned):
    owned.client_close_error = True
    with pytest.raises(sessions.PAXCleanupError, match="client close failed"):
        with owned.Device.local_session(config_path=owned.config):
            pass
    assert owned.processes[0].poll() == 0
    assert not owned.processes[0].root.exists()


def test_failed_graceful_shutdown_kills_only_owned_child_and_reports_failure(owned):
    owned.timeout = True
    with pytest.raises(sessions.PAXCleanupError, match="graceful daemon exit"):
        with owned.Device.local_session(config_path=owned.config):
            pass
    owned.processes[0].kill.assert_called_once()
    assert owned.processes[0].poll() == -9


def test_body_error_remains_primary_if_cleanup_also_fails(owned):
    owned.stop_ok = False
    error = ValueError("bad notebook operation")
    with pytest.raises(ValueError) as failure:
        with owned.Device.local_session(config_path=owned.config):
            raise error
    assert failure.value is error
    assert isinstance(error.__cause__, sessions.PAXCleanupError)


@pytest.mark.parametrize("kwargs", [{"startup_timeout_s": 0}, {"shutdown_timeout_s": float("nan")}])
def test_bad_timeout_fails_before_launch(owned, kwargs):
    with pytest.raises(ValueError):
        with owned.Device.local_session(config_path=owned.config, **kwargs):
            pass
    assert not owned.processes


def test_repeated_sessions_start_and_stop_distinct_processes(owned):
    for _ in range(2):
        with owned.Device.local_session(config_path=owned.config):
            pass
    assert len(owned.processes) == 2
    assert owned.processes[0].root != owned.processes[1].root
    assert all(p.poll() == 0 and not p.root.exists() for p in owned.processes)


@pytest.mark.parametrize("partial_failure,stop_failure,close_failure", [
    (False, False, False), (True, False, False), (False, True, False), (False, False, True)])
def test_supervised_child_closes_partial_initialization_and_records_failures(
        monkeypatch, tmp_path, partial_failure, stop_failure, close_failure):
    instrument = Mock()
    if stop_failure:
        instrument.write.side_effect = RuntimeError("motor stop failed")
    if close_failure:
        instrument.close.side_effect = RuntimeError("USB close failed")
    targets = []

    class BaseDaemon:
        def __init__(self):
            self.inst = instrument
            if partial_failure:
                raise RuntimeError("init failed after USB opened")

        @classmethod
        def main(cls):
            daemon = cls()
            daemon.close()  # YAQD normally invokes close on graceful shutdown.

    fake_thread = lambda target, **kwargs: SimpleNamespace(start=lambda: targets.append(target))
    monkeypatch.setitem(sys.modules, "polarization_locking.hardware.pax1000_daemon",
                        SimpleNamespace(ProjectPAX1000=BaseDaemon))
    monkeypatch.setattr(_pax_daemon.threading, "Thread", fake_thread)
    monkeypatch.setattr(sys, "argv", ["_pax_daemon.py", str(tmp_path)])
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"")))
    signal_call = Mock()
    monkeypatch.setattr(_pax_daemon.os, "kill", signal_call)
    if partial_failure:
        with pytest.raises(RuntimeError, match="init failed"):
            _pax_daemon.run(tmp_path)
    else:
        _pax_daemon.run(tmp_path)
    receipt = json.loads((tmp_path / "shutdown.json").read_text())
    assert receipt["usb_opened"] is True
    assert receipt["motor_stop_command_succeeded"] is not stop_failure
    assert receipt["usb_closed"] is not close_failure
    instrument.write.assert_called_once_with("INPut:ROTation:STATe 0")
    instrument.close.assert_called_once()
    targets[0]()  # Parent pipe EOF requests SIGTERM; no real process is signaled.
    assert signal_call.call_args.args[1] == signal.SIGTERM
