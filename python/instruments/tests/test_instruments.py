"""Transport, freshness and fault checks with fake drivers only."""
import json
import subprocess
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from instruments import PAX, PAXNotReady, RedPitaya
from instruments import pax, red_pitaya as rp_module


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def record(timestamp=1, **changes):
    return dict(timestamp=timestamp, revisions=timestamp * 10, theta=0., eta=0.,
                dop=.4, ptotal=.001, adc_min=4000., adc_max=45000., rev_time=1/60) | changes


@pytest.fixture
def pax_driver(monkeypatch):
    clients = []

    class Client:
        def __init__(self, **settings):
            self.settings = settings
            self._socket = SimpleNamespace(_socket=Mock())
            self.measure = Mock(return_value=17)
            self.get_measured = Mock(return_value=record())
            self.id = Mock(return_value={"kind": "thorlabs-pax1000"})
            self.get_wavelength = Mock(return_value=830.)
            self.get_channel_units = Mock(return_value={"ptotal": None})
            self.set_wavelength = Mock()
            clients.append(self)

    monkeypatch.setitem(sys.modules, "yaqc", SimpleNamespace(Client=Client))
    clock = Clock()
    monkeypatch.setattr(pax, "time", clock)
    return clients, clock


@pytest.fixture
def rp_driver(monkeypatch, tmp_path):
    client = Mock()
    client.reads.side_effect = lambda address, length: np.array([address % 8192], dtype=np.uint32)
    client.writes.return_value = True
    ssh = Mock()

    def command(text, **kwargs):
        stdout = Mock()
        stdout.read.return_value = b"pyrpl\n" if text.startswith("cat ") else b""
        stdout.channel.exit_status_ready.return_value = False
        return None, stdout, Mock()

    ssh.exec_command.side_effect = command
    scope = Mock()

    def make_scope(session, name):
        session.client.writes(0x40100014, [17])
        return scope

    constructor = Mock(side_effect=make_scope)
    monitor_factory = Mock(return_value=client)
    for name in ("pyrpl", "pyrpl.hardware_modules"):
        monkeypatch.setitem(sys.modules, name, ModuleType(name))
    modules = {
        "pyrpl.hardware_modules.scope": SimpleNamespace(Scope=constructor),
        "pyrpl.memory": SimpleNamespace(MemoryTree=Mock(return_value=object())),
        "pyrpl.redpitaya_client": SimpleNamespace(MonitorClient=monitor_factory),
        "pyrpl.directories": SimpleNamespace(user_config_dir=str(tmp_path)),
        "paramiko": SimpleNamespace(SSHClient=Mock(return_value=ssh), AutoAddPolicy=Mock()),
        "yaml": SimpleNamespace(safe_load=Mock(return_value={"redpitaya": {"user": "test"}})),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    (tmp_path / "scope_config.yml").write_text("redpitaya: {}")
    monkeypatch.setattr(rp_module, "time", Clock())
    return SimpleNamespace(client=client, ssh=ssh, scope=scope,
                           constructor=constructor, monitor_factory=monitor_factory)


def test_imports_and_construction_need_no_drivers_or_experiment_packages():
    result = subprocess.run([sys.executable, "-c", """
import sys
from instruments import PAX, RedPitaya
rp = RedPitaya('example.invalid')
pax = PAX()
assert not any(name in sys.modules for name in
               ('numpy', 'pyrpl', 'paramiko', 'yaqc', 'polarization_locking'))
rp.close()
pax.close()
"""], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_rp_attach_readback_close_and_repeat(rp_driver):
    rp = RedPitaya("example.invalid")
    assert rp.attach_monitor() is rp
    assert rp.scope is rp_driver.scope
    assert rp_driver.ssh.exec_command.call_args_list[0].args[0] == "cat /tmp/loaded_fpga.inf"
    assert rp_driver.ssh.exec_command.call_args_list[1].args[0] == "/opt/pyrpl/monitor_server 2223"
    with pytest.raises(RuntimeError, match="already attached"):
        rp.attach_monitor()
    rp_driver.client.reads.reset_mock()
    status = rp.read_status()
    assert rp_driver.client.reads.call_count == 6
    assert status["scope_registers"]["0x40100014"] == 20
    assert status["host"] == "example.invalid"
    json.dumps(status, allow_nan=False)
    rp_driver.client.writes.reset_mock()
    rp.close()
    assert rp_driver.client.writes.call_count == 6
    rp_driver.client.close.assert_called_once()
    rp_driver.ssh.close.assert_called_once()
    rp.close()
    rp_driver.client.close.assert_called_once()
    rp.attach_monitor()
    rp.close()
    assert rp_driver.monitor_factory.call_count == 2


def test_rp_transport_never_restarts_or_initializes_outputs(rp_driver):
    rp = RedPitaya("example.invalid").attach_monitor()
    restart = rp_driver.monitor_factory.call_args.kwargs["restartserver"]
    with pytest.raises(RuntimeError, match="no FPGA reload"):
        restart()
    guarded = rp._p.rp.client
    with pytest.raises(RuntimeError, match="refused"):
        guarded.writes(0x40380004, [1])
    with pytest.raises(RuntimeError, match="refused"):
        guarded.writes(0x40100030, [1, 2])
    rp.close()


def test_rp_wrong_fpga_marker_refuses_to_start_monitor(rp_driver):
    stdout = Mock()
    stdout.read.return_value = b"stock FPGA\n"
    rp_driver.ssh.exec_command.side_effect = None
    rp_driver.ssh.exec_command.return_value = (None, stdout, Mock())
    with pytest.raises(RuntimeError, match="refusing to reload"):
        RedPitaya("example.invalid").attach_monitor()
    rp_driver.monitor_factory.assert_not_called()
    rp_driver.constructor.assert_not_called()
    rp_driver.ssh.close.assert_called_once()
    assert rp_driver.ssh.exec_command.call_count == 1


def test_rp_ssh_failure_closes_transport_before_propagating(rp_driver):
    rp_driver.ssh.connect.side_effect = RuntimeError("SSH failed")
    with pytest.raises(RuntimeError, match="SSH failed"):
        RedPitaya("example.invalid").attach_monitor()
    rp_driver.monitor_factory.assert_not_called()
    rp_driver.ssh.close.assert_called_once()


def test_rp_monitor_launch_failure_does_not_take_over_existing_port(rp_driver):
    marker, failure = Mock(), Mock()
    marker.read.return_value = b"pyrpl\n"
    failure.channel.exit_status_ready.return_value = True
    stderr = Mock()
    stderr.read.return_value = b"port in use"
    rp_driver.ssh.exec_command.side_effect = [(None, marker, Mock()), (None, failure, stderr)]
    with pytest.raises(RuntimeError, match="port in use"):
        RedPitaya("example.invalid").attach_monitor()
    rp_driver.monitor_factory.assert_not_called()
    rp_driver.ssh.close.assert_called_once()


def test_rp_interrupted_scope_construction_still_restores_and_closes(rp_driver):
    rp_driver.constructor.side_effect = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        RedPitaya("example.invalid").attach_monitor()
    assert rp_driver.client.writes.call_count == 6
    rp_driver.client.close.assert_called_once()
    rp_driver.ssh.close.assert_called_once()


def test_rp_scope_construction_failure_restores_registers_and_closes(rp_driver):
    rp_driver.constructor.side_effect = RuntimeError("scope construction failed")
    rp = RedPitaya("example.invalid")
    with pytest.raises(RuntimeError, match="scope construction failed"):
        rp.attach_monitor()
    assert rp_driver.client.writes.call_count == 6
    rp_driver.client.close.assert_called_once()
    rp_driver.ssh.close.assert_called_once()
    with pytest.raises(RuntimeError, match="Attach"):
        rp.read_status()


def test_rp_partial_snapshot_failure_closes_and_restores_captured_values(rp_driver):
    rp_driver.client.reads.side_effect = [np.array([2]), RuntimeError("read failed")]
    with pytest.raises(RuntimeError, match="read failed"):
        RedPitaya("example.invalid").attach_monitor()
    rp_driver.client.writes.assert_called_once()
    rp_driver.client.close.assert_called_once()
    rp_driver.ssh.close.assert_called_once()
    rp_driver.constructor.assert_not_called()


def test_rp_cleanup_attempts_every_restore_and_both_transports(rp_driver):
    rp = RedPitaya("example.invalid").attach_monitor()
    rp_driver.client.writes.reset_mock()
    rp_driver.client.writes.side_effect = RuntimeError("write failed")
    rp_driver.client.close.side_effect = RuntimeError("close failed")
    with pytest.raises(rp_module.RPCleanupError) as failure:
        rp.close()
    assert len(failure.value.errors) == 7
    assert rp_driver.client.writes.call_count == 6
    rp_driver.ssh.close.assert_called_once()
    with pytest.raises(RuntimeError, match="already attached"):
        rp.attach_monitor()


def test_rp_interrupt_during_restoration_still_closes_both_transports(rp_driver):
    rp = RedPitaya("example.invalid").attach_monitor()
    rp_driver.client.writes.reset_mock()
    rp_driver.client.writes.side_effect = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt) as failure:
        rp.close()
    assert isinstance(failure.value.__cause__, rp_module.RPCleanupError)
    assert rp_driver.client.writes.call_count == 6
    rp_driver.client.close.assert_called_once()
    rp_driver.ssh.close.assert_called_once()


def test_rp_bad_readback_is_not_reported_as_responsive(rp_driver):
    rp = RedPitaya("example.invalid").attach_monitor()
    rp_driver.client.reads.return_value = None
    rp_driver.client.reads.side_effect = None
    with pytest.raises(RuntimeError, match="read failed"):
        rp.read_status()
    rp.close()


def test_pax_connection_status_and_close_do_not_configure_or_stop_daemon(pax_driver):
    clients, clock = pax_driver
    device = PAX(rpc_timeout_s=1.5)
    assert device.connect() is device
    client = clients[0]
    assert client.settings == dict(host="localhost", port=38400, timeout=1.5)
    status = device.read_status()
    assert status["wavelength_nm"] == 830.
    assert status["channel_units"] == {"ptotal": None}
    client.set_wavelength.assert_not_called()
    client.measure.assert_not_called()
    with pytest.raises(RuntimeError, match="already connected"):
        device.connect()
    device.close()
    device.close()
    client._socket._socket.close.assert_called_once()
    device.connect()
    assert len(clients) == 2
    device.close()


def test_pax_handshake_failure_closes_partial_client(monkeypatch):
    socket = Mock()

    class Client:
        def __init__(self, **kwargs):
            self._socket = SimpleNamespace(_socket=socket)
            raise RuntimeError("handshake failed")

    monkeypatch.setitem(sys.modules, "yaqc", SimpleNamespace(Client=Client))
    device = PAX()
    with pytest.raises(RuntimeError, match="handshake failed"):
        device.connect()
    socket.close.assert_called_once()
    device.close()


def test_pax_failed_handshake_cleanup_keeps_client_for_another_close(monkeypatch):
    socket = Mock()
    socket.close.side_effect = RuntimeError("close failed")

    class Client:
        def __init__(self, **kwargs):
            self._socket = SimpleNamespace(_socket=socket)
            raise RuntimeError("handshake failed")

    monkeypatch.setitem(sys.modules, "yaqc", SimpleNamespace(Client=Client))
    device = PAX()
    with pytest.raises(RuntimeError, match="handshake failed") as failure:
        device.connect()
    assert str(failure.value.__cause__) == "close failed"
    with pytest.raises(RuntimeError, match="already connected"):
        device.connect()
    socket.close.side_effect = None
    device.close()


def test_pax_close_failure_is_visible_and_blocks_reconnection(pax_driver):
    clients, _ = pax_driver
    device = PAX().connect()
    clients[0]._socket._socket.close.side_effect = RuntimeError("socket close failed")
    with pytest.raises(RuntimeError, match="socket close failed"):
        device.close()
    with pytest.raises(RuntimeError, match="already connected"):
        device.connect()
    clients[0]._socket._socket.close.side_effect = None
    device.close()


def test_pax_raw_preserves_anomalies_extra_fields_and_host_timing(pax_driver):
    clients, clock = pax_driver
    device = PAX().connect()
    raw = record(ptotal=-.001, dop=.01) | {"extra": "unchanged"}
    clients[0].get_measured.return_value = raw
    result = device.read_raw()
    assert result == raw and result is not raw
    assert device.last_read_timing["measurement_id"] == 17
    assert device.last_read_timing["received_monotonic_s"] == pytest.approx(.06)
    assert device.last_read_timing["requested_monotonic_s"] == 0
    assert result["timestamp"] == 1  # Not replaced with host time.
    device.close()


def test_pax_fresh_retains_all_attempts_and_requires_both_counters(pax_driver):
    clients, _ = pax_driver
    device = PAX().connect()
    sequence = [{}, record(0), record(1, adc_min=65520), record(1), record(1),
                record(2, revisions=10), record(2, dop=.01), record(3, dop=1.004)]
    clients[0].get_measured.side_effect = sequence
    result = device.read_fresh()
    assert result == sequence[6]
    assert [attempt["raw_record"] for attempt in device.last_attempts] == sequence[:7]
    assert [a["valid"] for a in device.last_attempts[:4]] == [False, False, False, True]
    assert device.read_fresh()["dop"] == 1.004
    assert len(device.last_attempts) == 1
    device.close()


@pytest.mark.parametrize("sample", [record(), record(0), record(ptotal=-1),
                                    record(ptotal=float("nan")), record(theta=float("inf"))])
def test_pax_fresh_timeout_retains_stale_or_invalid_records(pax_driver, sample):
    clients, _ = pax_driver
    device = PAX().connect()
    clients[0].get_measured.return_value = sample
    with pytest.raises(PAXNotReady):
        device.read_fresh(timeout_s=.2)
    assert device.last_attempts
    assert all(attempt["raw_record"] == sample for attempt in device.last_attempts)
    device.close()


def test_pax_rpc_failure_propagates_without_retriggering(pax_driver):
    clients, _ = pax_driver
    device = PAX().connect()
    clients[0].get_measured.side_effect = TimeoutError("RPC failed")
    with pytest.raises(TimeoutError, match="RPC failed"):
        device.read_fresh()
    clients[0].measure.assert_called_once()
    assert device.last_attempts[0]["raw_record"] is None
    assert "TimeoutError" in device.last_attempts[0]["error"]
    device.close()


def test_pax_wavelength_change_is_explicit_and_resets_freshness(pax_driver):
    clients, _ = pax_driver
    device = PAX().connect()
    device._last_fresh_record = (5, 50)
    device.set_wavelength(850.)
    clients[0].set_wavelength.assert_called_once_with(850.)
    assert device._last_fresh_record is None
    device.close()


@pytest.mark.parametrize("factory", [lambda: RedPitaya("host", monitor_port=0),
                                    lambda: PAX(port=True),
                                    lambda: PAX(rpc_timeout_s=float("nan")),
                                    lambda: PAX(measurement_wait_s=-1)])
def test_invalid_connection_parameters_fail_without_drivers(factory):
    with pytest.raises(ValueError):
        factory()


def test_legacy_reading_and_exception_imports_remain_available():
    from polarization_locking.hardware.pax_interface import PAXReading, PAXNotReady as LegacyNotReady
    assert LegacyNotReady is PAXNotReady
    assert PAXReading is pax.PAXReading
    assert pax._parse_reading(record()).s1 == 1.


def test_locking_fingerprint_includes_shared_instrument_source(monkeypatch, tmp_path):
    from polarization_locking import runner
    suite, instruments = tmp_path / "suite", tmp_path / "instruments"
    suite.mkdir()
    instruments.mkdir()
    (suite / "run.py").write_text("suite")
    shared = instruments / "pax.py"
    shared.write_text("before")
    monkeypatch.setattr(runner, "SOURCE_ROOTS", (suite, instruments))
    before = runner.source_fingerprint()
    shared.write_text("after")
    assert runner.source_fingerprint() != before
