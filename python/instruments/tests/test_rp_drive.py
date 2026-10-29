"""Active RP command/capture/cleanup contracts with fake hardware only."""
import base64
import hashlib
from types import SimpleNamespace
import sys
from unittest.mock import Mock

import numpy as np
import pytest

from instruments import RedPitaya
from instruments import _rp_drive as active
from instruments.red_pitaya import RPCleanupError


@pytest.fixture
def board(monkeypatch, tmp_path):
    state = SimpleNamespace(init_error=None, bad_readback=False, second_read_error=False,
                            moving_buffer=False, clipped=False, fail_route=False)

    class Module:
        def __init__(self):
            self.route = "out2"
            self.route_writes = []
            self._clear = Mock()

        @property
        def output_direct(self):
            return self.route

        @output_direct.setter
        def output_direct(self, value):
            if state.fail_route and self is state.rp.pid0:
                raise RuntimeError("route write failed")
            self.route = value
            self.route_writes.append(value)

    class ASG(Module):
        def __init__(self):
            super().__init__()
            self.waveform, self.offset, self.amplitude, self.frequency = "dc", .1, .01, 1.
            self.calls = []

        def setup(self, **values):
            # Installed Pyrpl only accepts ASG setup attributes. In particular,
            # random_phase is reset by its waveform setter, not a setup keyword.
            allowed = {"waveform", "offset", "amplitude", "frequency", "trigger_source",
                       "output_direct", "start_phase", "cycles_per_burst"}
            assert not set(values) - allowed
            if "waveform" in values:
                self.random_phase = False
            self.calls.append(dict(values))
            for key, value in values.items():
                setattr(self, key, value)
            if state.bad_readback and values["waveform"] == "sin":
                self.frequency *= 2

    class Scope:
        data_length = 16384
        _write_pointer_current = 23
        _write_pointer_trigger = 11
        _trigger_delay_register = 8192

        def __init__(self):
            self.decimation = 1
            self.stop = Mock()
            self._clear = Mock()
            self._start_trace_acquisition = Mock()

        def setup(self, **values):
            for key, value in values.items():
                setattr(self, key, value)

        @property
        def sampling_time(self):
            return self.decimation * 8e-9

        @property
        def duration(self):
            return self.data_length * self.sampling_time

        def curve_ready(self):
            return True

        @property
        def _rawdata_ch1(self):
            raw = (np.arange(16384) % 64 - 32).astype(np.int16)
            if state.clipped:
                raw[0] = 8191
            return raw

        @property
        def _rawdata_ch2(self):
            if state.second_read_error:
                raise RuntimeError("IN2 read failed")
            if state.moving_buffer:
                self._write_pointer_current += 1
            return (np.arange(16384) % 32 - 16).astype(np.int16)

        def _get_trace(self):
            raw = np.stack([self._rawdata_ch1, self._rawdata_ch2])
            return np.roll(raw, -(self._write_pointer_trigger+self._trigger_delay_register+1), axis=1) / 8192

    class Pyrpl:
        def __init__(self, **settings):
            state.settings = settings
            self.rp = state.rp
            self.modules = list(state.modules)
            if state.init_error:
                raise state.init_error

    scope = Scope()
    modules = {name: ASG() if name.startswith("asg") else Module() for name in active._ROUTES}
    state.rp = SimpleNamespace(**modules, scope=scope, frequency_correction=1., client=Mock(),
                               ssh=SimpleNamespace(channel=Mock(), scp=Mock(), ssh=Mock()))
    state.modules = [*modules.values(), scope]
    state.key = b"fake RP key"
    state.fingerprint = base64.b64encode(hashlib.sha256(state.key).digest()).decode().rstrip("=")
    transport = Mock()
    transport.get_remote_server_key.return_value.asbytes.return_value = state.key
    state.transport = transport
    monkeypatch.setitem(sys.modules, "pyrpl", SimpleNamespace(Pyrpl=Pyrpl))
    monkeypatch.setitem(sys.modules, "pyrpl.directories", SimpleNamespace(user_config_dir=str(tmp_path)))
    monkeypatch.setitem(sys.modules, "paramiko", SimpleNamespace(Transport=Mock(return_value=transport)))
    profile = {"redpitaya": {"user": "test", "password": "test", "frequency_correction": 1.},
               "asg0": {"offset": .99}}
    monkeypatch.setitem(sys.modules, "yaml", SimpleNamespace(safe_load=Mock(return_value=profile)))
    (tmp_path / "scope_config.yml").write_text("fake profile")
    now = [0.]
    monkeypatch.setattr(active, "time", SimpleNamespace(monotonic=lambda: now[0],
                                                       sleep=lambda seconds: now.__setitem__(0, now[0]+seconds)))
    return state


def session(board, **changes):
    settings = dict(profile="scope_config", ssh_host_key_sha256=board.fingerprint,
                    command_limits_v={"out1": [0., .5]}, frequency_limits_hz=[1., 10000.], max_peak_v=.05)
    settings.update(changes)
    return RedPitaya.drive_session("example.invalid", **settings)


def assert_zero_closed(board):
    for name in active._ROUTES:
        assert getattr(board.rp, name).output_direct == "off"
    for name in ("asg0", "asg1"):
        assert getattr(board.rp, name).offset == 0
        assert getattr(board.rp, name).amplitude == 0
    board.rp.client.close.assert_called_once()
    board.rp.client.socket.close.assert_called_once()
    for name in ("channel", "scp", "ssh"):
        getattr(board.rp.ssh, name).close.assert_called_once()


def test_connection_uses_credentials_only_and_initializes_zero_off(board):
    with session(board) as drive:
        assert board.settings["config"] == "" and board.settings["gui"] is False
        assert "asg0" not in board.settings
        assert all(s["output_direct"] == "off" and s["offset_v"] == 0 for s in drive.readback_all().values())
        with pytest.raises(RuntimeError, match="automatic server/FPGA restart refused"):
            board.rp.client._restartserver()
    assert_zero_closed(board)
    assert all(module._clear.call_count == 1 for module in board.modules)
    assert drive.cleanup_evidence["status"] == "COMPLETED"


def test_fingerprint_mismatch_stops_before_active_initialization(board):
    with pytest.raises(RuntimeError, match="fingerprint mismatch"):
        with session(board, ssh_host_key_sha256="wrong"):
            pass
    assert not hasattr(board, "settings")
    board.transport.close.assert_called_once()
    board.rp.client.close.assert_not_called()


@pytest.mark.parametrize("error", [RuntimeError("partial init failed"), KeyboardInterrupt()])
def test_partial_initialization_or_interrupt_attempts_zero_and_close(board, error):
    board.init_error = error
    with pytest.raises(type(error)):
        with session(board):
            pass
    assert_zero_closed(board)


def test_waveform_request_quantization_and_actual_readback_are_distinct(board):
    with session(board) as drive:
        command = drive.set_waveform("out1", offset_v=.1, peak_v=.005, frequency_hz=100., ramp_s=.1)
        assert command["requested"]["peak_v"] == .005
        assert 0 < command["actual"]["amplitude_v"] <= .005
        assert command["actual"]["output_direct"] == "out1"
        assert drive.readback("out2")["output_direct"] == "off"
    assert_zero_closed(board)


def test_quantized_bias_and_ramp_never_exceed_an_unrepresentable_rail(board):
    with session(board, command_limits_v={"out1": [0., .10001]}) as drive:
        result = drive.set_waveform("out1", waveform="dc", offset_v=.10001, peak_v=0., frequency_hz=0.)
        assert result["actual"]["offset_v"] <= .10001
        assert result["requested"]["offset_v"] == .10001
    assert_zero_closed(board)


def test_dc_bias_steps_preserve_route_and_do_not_reconfigure_asg(board):
    with session(board, command_limits_v={"out1": [-.30001, .30001]}) as drive:
        drive.set_waveform("out1", waveform="dc", offset_v=0., peak_v=0., frequency_hz=0.)
        asg = board.rp.asg0
        asg.route_writes.clear()
        setup_count = len(asg.calls)
        for requested in (-.30001, 0., .30001, 0.):
            command = drive.set_dc_offset("out1", offset_v=requested, ramp_s=.1)
            assert command["requested"]["offset_v"] == requested
            assert -.30001 <= command["actual"]["offset_v"] <= .30001
            assert command["actual"]["output_direct"] == "out1"
        assert asg.route_writes == []
        assert len(asg.calls) == setup_count
        assert drive.readback("out2")["output_direct"] == "off"
    assert_zero_closed(board)


def test_dc_bias_requires_enabled_dc_and_rejects_invalid_settings_before_writes(board):
    with session(board) as drive:
        with pytest.raises(RuntimeError, match="Enable a DC waveform"):
            drive.set_dc_offset("out1", offset_v=.1)
        drive.set_waveform("out1", offset_v=.1, peak_v=.005, frequency_hz=100.)
        with pytest.raises(RuntimeError, match="Enable a DC waveform"):
            drive.set_dc_offset("out1", offset_v=.1)
        for settings in (dict(output="out2", offset_v=0.), dict(output="out1", offset_v=.6),
                         dict(output="out1", offset_v=.1, ramp_s=-1.)):
            with pytest.raises(ValueError):
                drive.set_dc_offset(**settings)
        assert drive.readback("out1")["waveform"] == "sin"
    assert_zero_closed(board)


@pytest.mark.parametrize("failure", [RuntimeError("write failed"), KeyboardInterrupt()])
def test_dc_bias_write_failure_or_interrupt_zeros_and_closes(board, monkeypatch, failure):
    with pytest.raises(type(failure)):
        with session(board) as drive:
            drive.set_waveform("out1", waveform="dc", offset_v=0., peak_v=0., frequency_hz=0.)
            asg = board.rp.asg0
            armed = [True]
            def set_offset(module, value):
                if module is asg and armed[0]:
                    armed[0] = False
                    raise failure
                module.__dict__["offset"] = value
            monkeypatch.setattr(type(asg), "offset", property(lambda m: m.__dict__["offset"], set_offset), raising=False)
            drive.set_dc_offset("out1", offset_v=.1)
    assert_zero_closed(board)


@pytest.mark.parametrize("changes", [dict(output="out2"), dict(offset_v=.49, peak_v=.05),
                                      dict(peak_v=.1), dict(frequency_hz=20000.), dict(peak_v=1e-6)])
def test_invalid_or_sub_lsb_waveform_never_enables_output(board, changes):
    settings = dict(output="out1", offset_v=.1, peak_v=.005, frequency_hz=100.)
    settings.update(changes)
    with session(board) as drive:
        with pytest.raises(ValueError):
            drive.set_waveform(**settings)
        assert all(s["output_direct"] == "off" for s in drive.readback_all().values())


def test_bad_asg_readback_closes_session_and_never_leaves_drive_enabled(board):
    with pytest.raises(RuntimeError, match="frequency readback"):
        with session(board) as drive:
            board.bad_readback = True
            drive.set_waveform("out1", offset_v=.1, peak_v=.005, frequency_hz=100.)
    assert_zero_closed(board)


def test_paired_capture_retains_signed_codes_and_declared_hv_scaling(board):
    with session(board) as drive:
        drive.set_waveform("out1", offset_v=.1, peak_v=.005, frequency_hz=100.)
        capture = drive.capture(decimation=64, full_scale_v={"in2": 20., "in1": 1.})
        assert capture["raw_counts"].shape == (2, 16384)
        assert capture["raw_counts"].dtype == np.int16
        assert capture["raw_counts"].min() < 0
        np.testing.assert_array_equal(capture["input_volts"][0], capture["raw_counts"][0]/8192)
        np.testing.assert_array_equal(capture["input_volts"][1], capture["raw_counts"][1]/8192*20)
        assert capture["metadata"]["issues"] == []
        assert capture["time_s"][1] == pytest.approx(64*8e-9)
    assert_zero_closed(board)


@pytest.mark.parametrize("flag,issue", [("clipped", "ADC clipping"), ("moving_buffer", "not frozen")])
def test_clipping_and_unfrozen_data_are_retained_and_flagged(board, flag, issue):
    setattr(board, flag, True)
    with session(board) as drive:
        capture = drive.capture(decimation=1, full_scale_v={"in1": 1., "in2": 20.})
        assert capture["raw_counts"].shape == (2, 16384)
        assert any(issue in message for message in capture["metadata"]["issues"])
        if flag == "clipped":
            assert capture["raw_counts"].max() == 8191


def test_partial_input_read_failure_keeps_available_raw_channel(board):
    with pytest.raises(RuntimeError, match="IN2 read failed"):
        with session(board) as drive:
            board.second_read_error = True
            drive.capture(decimation=1, full_scale_v={"in1": 1., "in2": 20.})
    assert drive.last_capture["raw_in1"].shape == (16384,)
    assert drive.last_capture["metadata"]["ring_shift_samples"] == -(11+8192+1)
    assert "raw_in2" not in drive.last_capture
    assert "IN2 read failed" in drive.last_capture["metadata"]["error"]
    assert_zero_closed(board)


def test_cleanup_failure_still_attempts_other_outputs_and_all_transports(board):
    with pytest.raises(RPCleanupError):
        with session(board) as drive:
            board.fail_route = True
    assert drive.cleanup_evidence["status"] == "FAILED"
    assert board.rp.asg0.offset == board.rp.asg1.offset == 0
    board.rp.client.close.assert_called_once()
    board.rp.ssh.ssh.close.assert_called_once()


@pytest.mark.parametrize("limits", [None, {"out1": None}, {"out1": [.1, .5]}, {"out3": [0., .5]}])
def test_unapproved_limits_fail_without_driver_construction(board, limits):
    with pytest.raises(ValueError):
        session(board, command_limits_v=limits)
    assert not hasattr(board, "settings")


def test_square_wave_uses_the_sine_bounds_and_readbacks(board):
    with session(board) as drive:
        command = drive.set_waveform("out1", waveform="square", offset_v=.1, peak_v=.005, frequency_hz=100.)
        assert command["actual"]["waveform"] == "square" and command["actual"]["output_direct"] == "out1"
        with pytest.raises(ValueError):
            drive.set_waveform("out1", waveform="square", offset_v=.1, peak_v=0., frequency_hz=100.)
        with pytest.raises(ValueError):
            drive.set_waveform("out1", waveform="square", offset_v=.1, peak_v=.005, frequency_hz=1e9)
    assert_zero_closed(board)


def test_frequency_change_holds_route_and_bias_and_respects_limits(board):
    with session(board) as drive:
        with pytest.raises(RuntimeError, match="Enable an AC waveform"):
            drive.set_frequency("out1", frequency_hz=100.)
        drive.set_waveform("out1", offset_v=.1, peak_v=.005, frequency_hz=100.)
        asg, before = board.rp.asg0, drive.readback("out1")
        calls = len(asg.calls)
        command = drive.set_frequency("out1", frequency_hz=200.)
        assert len(asg.calls) == calls  # no ASG reconfiguration, route never switched off
        assert command["actual"]["output_direct"] == "out1" and command["actual"]["offset_v"] == before["offset_v"]
        assert abs(command["actual"]["frequency_hz"] - 200.) < 1
        with pytest.raises(ValueError):
            drive.set_frequency("out1", frequency_hz=1e9)
    assert_zero_closed(board)


def test_network_sweep_needs_dc_bias_respects_limits_and_turns_iq_off(board):
    class IQ:
        _LPFBITS, output_direct, amplitude, frequency, acbandwidth = 24, "off", 0., 0., 5.
        bandwidth = [1e4, 1e4]
        def setup(self, **values):
            for key, value in values.items():
                setattr(self, key, value)
        @property
        def _nadata(self):
            return self.amplitude * 2**24 / 4 * (0.5 + 0.5j)
    board.rp.iq0 = IQ()
    with session(board) as drive:
        with pytest.raises(RuntimeError, match="Enable a DC bias"):
            drive.network_sweep("out1", input="in1", frequencies_hz=[100.], amplitude_v=.01)
        drive.set_waveform("out1", waveform="dc", offset_v=.1, peak_v=0., frequency_hz=0.)
        for bad in (dict(frequencies_hz=[1e9], amplitude_v=.01), dict(frequencies_hz=[100.], amplitude_v=.1)):
            with pytest.raises(ValueError):
                drive.network_sweep("out1", input="in1", **bad)
        result = drive.network_sweep("out1", input="in1", frequencies_hz=[10., 1000.], amplitude_v=.01)
        assert result["response"] == [0.5 + 0.5j] * 2 and result["rbw_hz"] == [1.186, 100.]  # 1.186 Hz floor
        assert board.rp.iq0.amplitude == 0 and board.rp.iq0.output_direct == "off"
        assert drive.readback("out1")["output_direct"] == "out1"  # bias untouched
