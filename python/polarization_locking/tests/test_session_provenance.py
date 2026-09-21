"""Session ownership and reproducibility without instrument access."""
import json
from subprocess import CalledProcessError
from unittest.mock import Mock

import pytest

from polarization_locking.catalog import BY_KEY, default_options
from polarization_locking.config import PolarizationLockConfig
from polarization_locking.hardware.pax_interface import PAXController
from polarization_locking.runner import runtime_provenance
from polarization_locking.settings import load_recipe


def test_pax_cleans_up_before_connecting(monkeypatch):
    controller = PAXController(PolarizationLockConfig())
    client = Mock()
    monkeypatch.setattr("polarization_locking.hardware.pax_interface.yaqc", Mock())
    events = []
    monkeypatch.setattr(controller, "_stop_existing_pax_daemons", lambda: events.append("cleanup"))
    monkeypatch.setattr(controller, "_new_client", lambda: events.append("connect") or client)
    start = Mock()
    monkeypatch.setattr(controller, "_start_daemon", start)
    assert controller.connect() is client
    assert events == ["cleanup", "connect"]
    client.set_wavelength.assert_called_once_with(830.0)
    controller.disconnect()
    start.assert_not_called()
    assert controller._daemon_process is None


def test_pax_starts_only_when_connection_refused_and_closes_its_own_daemon(monkeypatch):
    controller = PAXController(PolarizationLockConfig())
    client, process = Mock(), Mock()
    monkeypatch.setattr(controller, "_stop_existing_pax_daemons", Mock())
    monkeypatch.setattr("polarization_locking.hardware.pax_interface.yaqc", Mock())
    monkeypatch.setattr(controller, "_new_client", Mock(side_effect=ConnectionRefusedError()))
    monkeypatch.setattr(controller, "_start_daemon", lambda: setattr(controller, "_daemon_process", process))
    monkeypatch.setattr(controller, "_wait_for_daemon", Mock(return_value=client))
    assert controller.connect() is client
    controller.disconnect()
    process.terminate.assert_called_once()
    process.wait.assert_called_once_with(timeout=2.0)
    process.kill.assert_not_called()


def test_pax_stale_device_after_cleanup_is_reported(monkeypatch):
    controller = PAXController(PolarizationLockConfig())
    client = Mock()
    monkeypatch.setattr(controller, "_stop_existing_pax_daemons", Mock())
    client.set_wavelength.side_effect = RuntimeError("No such device")
    monkeypatch.setattr("polarization_locking.hardware.pax_interface.yaqc", Mock())
    monkeypatch.setattr(controller, "_new_client", Mock(return_value=client))
    start = Mock()
    monkeypatch.setattr(controller, "_start_daemon", start)
    with pytest.raises(RuntimeError, match="stale"):
        controller.connect()
    controller.disconnect()
    start.assert_not_called()


@pytest.mark.parametrize("command,stubborn", [
    (["/env/bin/yaqd-thorlabs-pax1000", "-c", "pax.toml"], False),
    (["/env/bin/python", "/repo/hardware/pax1000_daemon.py", "-c", "pax.toml"], True),
])
def test_local_pax_cleanup_targets_daemons_and_escalates(tmp_path, monkeypatch, command, stubborn):
    import signal
    from pathlib import Path
    from polarization_locking.hardware import pax_interface
    proc = tmp_path / "proc"
    proc.mkdir()
    commands = {11: command, 12: ["bash", "-c", "echo yaqd-thorlabs-pax1000"],
                13: ["python", "unrelated.py"], 14: command}
    for pid, args in commands.items():
        entry = proc / str(pid)
        entry.mkdir()
        (entry / "cmdline").write_bytes("\0".join(args).encode() + b"\0")
    monkeypatch.setattr(pax_interface, "Path", lambda path: proc if path == "/proc" else Path(path))
    monkeypatch.setattr(pax_interface.sys, "platform", "linux")
    monkeypatch.setattr(pax_interface.os, "getpid", lambda: 14)
    monkeypatch.setattr(pax_interface.time, "monotonic", Mock(side_effect=[0, 3] if stubborn else [0, 0]))
    calls = []

    def kill(pid, sig):
        calls.append((pid, sig))
        if not stubborn or sig == signal.SIGKILL:
            (proc / str(pid) / "cmdline").unlink()
            (proc / str(pid)).rmdir()

    monkeypatch.setattr(pax_interface.os, "kill", kill)
    PAXController._stop_existing_pax_daemons()
    expected = [(11, signal.SIGTERM)]
    if stubborn:
        expected.append((11, signal.SIGKILL))
    assert calls == expected
    assert all((proc / str(pid)).exists() for pid in (12, 13, 14))


def test_remote_pax_connection_does_not_stop_local_daemons(monkeypatch):
    controller = PAXController(PolarizationLockConfig(pax_host="remote-bench"))
    cleanup = Mock()
    monkeypatch.setattr("polarization_locking.hardware.pax_interface.yaqc", Mock())
    monkeypatch.setattr(controller, "_stop_existing_pax_daemons", cleanup)
    monkeypatch.setattr(controller, "_new_client", Mock(return_value=Mock()))
    controller.connect()
    controller.disconnect()
    cleanup.assert_not_called()


def test_provenance_records_revision_dirty_state_and_versions(monkeypatch):
    git = Mock(side_effect=["abc123\n", " M runner.py\n"])
    monkeypatch.setattr("polarization_locking.runner.subprocess.check_output", git)
    monkeypatch.setattr("polarization_locking.runner.version", lambda _: "1.2.3")
    result = runtime_provenance()
    assert result["git_commit"] == "abc123"
    assert result["suite_dirty"] is True
    assert result["packages"]["pyrpl"] == "1.2.3"
    assert git.call_args_list[1].args[0][-2:] == ["--", "."]
    json.dumps(result, allow_nan=False)


def test_provenance_handles_exported_source_and_uninstalled_drivers(monkeypatch):
    from importlib.metadata import PackageNotFoundError
    monkeypatch.setattr("polarization_locking.runner.subprocess.check_output", Mock(side_effect=CalledProcessError(128, "git")))
    monkeypatch.setattr("polarization_locking.runner.version", Mock(side_effect=PackageNotFoundError()))
    result = runtime_provenance()
    assert result["git_commit"] is None and result["suite_dirty"] is None
    assert result["packages"]["pyrpl"] is None


@pytest.mark.parametrize("name,key", [("sweep-example.json", "sweep"), ("visibility-in2-example.json", "pd-visibility")])
def test_colleague_example_recipes_load_and_validate(name, key):
    from pathlib import Path
    case, config, options = load_recipe(Path(__file__).resolve().parents[1] / "profiles" / name)
    assert case == BY_KEY[key]
    assert options.keys() == default_options(case).keys()
    if case.scope_only:
        assert config.pd_input == "in2"
        assert config.visibility_dark_voltage_v is None
        assert "bench_notes" in case.config_names()
