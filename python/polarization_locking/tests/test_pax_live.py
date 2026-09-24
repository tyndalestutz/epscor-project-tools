"""Offline acquisition, recovery and real offscreen Qt interaction checks."""
import csv
import json
from pathlib import Path
from queue import SimpleQueue
from threading import Event, Thread, get_ident, main_thread
import time
from unittest.mock import Mock

import pytest

from polarization_locking.app import PolarizationLockApp
from polarization_locking.catalog import BY_KEY, default_options
from polarization_locking.config import PolarizationLockConfig
from polarization_locking.hardware.pax_interface import PAXNotReady
from polarization_locking.routines import pax_live
from polarization_locking.runner import execute
from polarization_locking.tests.test_stokes_phase_sweep import Clock, reading


def test_worker_logs_every_row_reuses_freshness_and_preserves_diagnostics(tmp_path, monkeypatch):
    clock = Clock()
    clock.time = lambda: 1700000000 + clock.now
    monkeypatch.setattr(pax_live, "time", clock)
    app = PolarizationLockApp()
    app.rp = Mock()
    app.pax = Mock(last_raw_record={"measurement_id": 11, "extra_flag": 7})
    app.pax.client.get_wavelength.return_value = 780.0
    stop, updates, result = Event(), pax_live.SampleCache(), {}
    path = tmp_path / "data.csv"
    count = 0

    def fresh():
        nonlocal count
        count += 1
        clock.sleep(.1)
        if count == 1:
            raise PAXNotReady("spinup")
        if count == 5:
            stop.set()
        return reading(count, theta=.1, eta=-.02)

    app.pax.read_fresh_polarization.side_effect = fresh
    pax_live.collect(app, path, stop, updates, result)
    rows = list(csv.DictReader(path.open()))
    assert len(rows) == result["sample_count"] == 4
    assert result.get("error") is None
    assert float(rows[0]["theta"]) == .1
    assert float(rows[0]["theta_deg"]) == pytest.approx(5.72957795)
    assert rows[0]["pax_wavelength_nm"] == "780.0"
    assert json.loads(rows[-1]["pax_raw_json"])["extra_flag"] == 7
    assert rows[-1]["pax_measurement_id"] == "11"
    assert rows[-1]["rp_state"] == "not_connected_or_measured"
    assert updates.latest()["sample"] == 4
    app.pax.connect.assert_called_once()
    app.pax.disconnect.assert_called_once()
    app.pax.read_polarization.assert_not_called()
    assert app.rp.mock_calls == []


def test_trend_uses_recent_endpoints_and_azimuth_wrap():
    history = pax_live.RecentReadings()
    for i in range(31):
        row = {key: i / 10 for key, _, _ in pax_live.READOUTS}
        row.update(elapsed_s=i / 10, theta_deg=(89 + i / 10 + 90) % 180 - 90)
        update = history.add(row)
    assert update["span_s"] == 1
    assert update["delta"]["eta_deg"] == 1
    assert update["delta"]["theta_deg"] == 1
    assert update["rate_hz"] == 10
    assert len(history.rows) == 11


@pytest.mark.parametrize("failure_at", ["connect", "read", "cleanup"])
def test_worker_failure_keeps_partial_csv_and_cleans_up(tmp_path, failure_at):
    app = PolarizationLockApp()
    app.rp = Mock()
    app.pax = Mock(last_raw_record={})
    stop = Event()
    app.pax.client.get_wavelength.return_value = 780
    if failure_at == "connect":
        app.pax.connect.side_effect = RuntimeError("connect failed")
    elif failure_at == "read":
        app.pax.read_fresh_polarization.side_effect = [reading(), RuntimeError("USB lost")]
    else:
        def fresh():
            stop.set()
            return reading()
        app.pax.read_fresh_polarization.side_effect = fresh
        app.pax.disconnect.side_effect = RuntimeError("cleanup failed")
    result = {}
    path = tmp_path / "data.csv"
    pax_live.collect(app, path, stop, pax_live.SampleCache(), result)
    assert ("cleanup_error" if failure_at == "cleanup" else "error") in result
    assert len(list(csv.DictReader(path.open()))) == (0 if failure_at == "connect" else 1)
    app.pax.disconnect.assert_called_once()
    assert app.rp.mock_calls == []


@pytest.mark.parametrize("choice", ["save", "discard", None, "cleanup_failure"])
def test_runner_save_discard_and_crash_recovery(tmp_path, monkeypatch, choice):
    case = BY_KEY["pax-live"]
    app = PolarizationLockApp(PolarizationLockConfig(results_directory=str(tmp_path)))
    app.rp = Mock()
    app.pax = Mock()
    report = Mock(return_value={"report_status": "completed"})
    monkeypatch.setattr("polarization_locking.reports.run_report.create_run_report", report)
    unrelated = tmp_path / "existing.csv"
    unrelated.write_text("keep")

    def run(output_file):
        Path(output_file).write_text("pax_ptotal\n.001\n")
        if choice is None:
            raise RuntimeError("unexpected panel failure")
        return {"disposition": "discard" if choice == "cleanup_failure" else choice,
                **({"cleanup_error": "failed"} if choice == "cleanup_failure" else {})}

    app._run_pax_live = run
    state = execute(case, app.config, default_options(case), app_factory=lambda _: app)
    app.pax.connect.assert_not_called()
    assert app.rp.mock_calls == []
    assert unrelated.read_text() == "keep"
    if choice == "discard":
        assert state["status"] == "discarded"
        assert not list(tmp_path.rglob("data.csv"))
    else:
        status_path = next(tmp_path.rglob("run.json"))
        assert json.loads(status_path.read_text())["data_disposition"] == ("saved" if choice == "save" else "temporary")
        assert status_path.with_name("data.csv").exists()
    assert report.call_count == int(choice == "save")


@pytest.mark.parametrize("action", ["stop", "close"])
def test_real_qt_panel_stays_responsive_and_keeps_client_in_worker(tmp_path, monkeypatch, action):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from qtpy import QtCore, QtWidgets
    gui = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app = PolarizationLockApp()
    app.rp = Mock()
    app.pax = Mock(last_raw_record={})
    app.pax.client.get_wavelength.return_value = 780
    owners, ticks = [], []

    def connect():
        owners.append(get_ident())
        time.sleep(.12)  # UI must paint and process timers during startup.

    def fresh():
        owners.append(get_ident())
        time.sleep(.06)
        return reading(len(owners), eta=.02)

    app.pax.connect.side_effect = connect
    app.pax.read_fresh_polarization.side_effect = fresh
    app.pax.disconnect.side_effect = lambda: owners.append(get_ident())
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *args: QtWidgets.QMessageBox.Save)
    timer = QtCore.QTimer()
    evidence = {}

    def exercise():
        ticks.append(time.monotonic())
        panels = [w for w in gui.topLevelWidgets() if w.windowTitle() == "PAX · manual alignment" and w.isVisible()]
        if not panels:
            return
        panel = panels[0]
        if len(ticks) == 2:
            evidence["waiting"] = panel.status.text()
        if panel.last and len(ticks) >= 12:
            evidence["eta"] = panel.values["eta_deg"].text()
            evidence["power"] = panel.values["pax_ptotal"].text()
            if action == "close":
                panel.close()
            else:
                panel.button.click()
            timer.stop()

    timer.timeout.connect(exercise)
    timer.start(20)
    # Fail safe prevents a broken interaction test from hanging indefinitely.
    watchdog = QtCore.QTimer()
    watchdog.setSingleShot(True)
    watchdog.timeout.connect(lambda: [w.close() for w in gui.topLevelWidgets()])
    watchdog.start(3000)
    try:
        result = pax_live.run_panel(app, tmp_path / "data.csv")
    finally:
        timer.stop()
        watchdog.stop()
    assert result["disposition"] == "save" and result["sample_count"] > 0
    assert "WAITING" in evidence["waiting"]
    assert float(evidence["eta"]) == pytest.approx(1.1459)
    assert evidence["power"] == "1000"
    assert len(ticks) >= 12
    assert len(set(owners)) == 1 and owners[0] != main_thread().ident
    assert app.rp.mock_calls == []


def test_simple_saved_report(tmp_path):
    from types import SimpleNamespace
    from polarization_locking.reports.run_report import create_run_report, main
    from polarization_locking.settings import recipe, write_json
    paths = SimpleNamespace(directory=tmp_path, csv=tmp_path / "data.csv", pdf=tmp_path / "report.pdf")
    paths.csv.write_text("elapsed_s,pax_ptotal,s1,s2,s3,dop,theta_deg,eta_deg\n0,.001,1,0,0,.99,0,0\n1,.002,.9,.1,.1,.95,1,1\n")
    case = BY_KEY["pax-live"]
    result = create_run_report(case, paths, PolarizationLockConfig(), default_options(case), {"status": "completed"})
    assert result["report_status"] == "completed"
    assert paths.pdf.read_bytes().startswith(b"%PDF-")
    write_json(tmp_path / "recipe.json", recipe(case, PolarizationLockConfig(), default_options(case)))
    write_json(tmp_path / "run.json", {"status": "completed", "data_disposition": "saved"})
    main([str(tmp_path)])
    assert json.loads((tmp_path / "run.json").read_text())["report_status"] == "completed"


def test_slow_csv_formatting_cannot_block_pax_acquisition(tmp_path, monkeypatch):
    app = PolarizationLockApp()
    app.rp = Mock()
    app.pax = Mock(last_raw_record={})
    app.pax.client.get_wavelength.return_value = 780
    stop, entered, release, acquired = Event(), Event(), Event(), Event()
    cache, result = pax_live.SampleCache(capacity=5), {}
    row = pax_live.sample_row
    owners = []

    def slow_row(sample):
        owners.append(get_ident())
        entered.set()
        assert release.wait(3)
        return row(sample)

    monkeypatch.setattr(pax_live, "sample_row", slow_row)
    count = 0
    def fresh():
        nonlocal count
        count += 1
        if count == 20:
            stop.set()
            acquired.set()
        return reading(count)

    app.pax.read_fresh_polarization.side_effect = fresh
    path = tmp_path / "data.csv"
    worker = Thread(target=pax_live.collect, args=(app, path, stop, cache, result))
    worker.start()
    try:
        assert entered.wait(2) and acquired.wait(2)
        assert cache.latest()["sample"] == 20
        samples, gap = cache.after(0)
        assert gap and len(samples) == 5
        assert worker.is_alive()  # waiting only for final logger drain
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive() and "error" not in result
    assert len(list(csv.DictReader(path.open()))) == 20
    assert all(owner != worker.ident for owner in owners)


def test_logger_failure_stops_acquisition_and_is_reported(tmp_path, monkeypatch):
    app = PolarizationLockApp()
    app.rp = Mock()
    app.pax = Mock(last_raw_record={})
    app.pax.client.get_wavelength.return_value = 780
    stop, result = Event(), {}
    app.pax.read_fresh_polarization.side_effect = lambda: (time.sleep(.002), reading())[1]
    monkeypatch.setattr(pax_live, "sample_row", Mock(side_effect=OSError("disk full")))
    pax_live.collect(app, tmp_path / "data.csv", stop, pax_live.SampleCache(), result)
    assert stop.is_set() and "disk full" in result["error"]
    app.pax.disconnect.assert_called_once()
