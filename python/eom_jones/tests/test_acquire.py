"""eom_jones.acquire with fake PAX, RP drive, viewer process and clock. No hardware."""
import json

import numpy as np
import pytest

from eom_jones import acquire as aq


class Clock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def sleep(self, s):
        self.now += s


class FakePAX:
    """Context manager standing in for PAX.local_session(); each read advances the fake clock by dt."""

    def __init__(self, clock, dt=0.1, fail_after=None):
        self.clock, self.dt, self.fail_after, self.n = clock, dt, fail_after, 0
        self.wavelength = None
        self.last_attempts, self.last_read_timing = [], None
        self.daemon_log = "daemon log text"
        self.shutdown_evidence = {"stop_command": True}
        self.session_configuration = {"measurement_mode": 5}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.exited = True
        return False

    def set_wavelength(self, nm):
        self.wavelength = nm

    def read_status(self):
        return {"wavelength_nm": self.wavelength}

    def _record(self):
        self.n += 1
        if self.fail_after is not None and self.n > self.fail_after:
            raise RuntimeError("fake PAX failure")
        self.last_read_timing = {"requested_monotonic_s": self.clock.now, "measurement_id": self.n}
        self.clock.sleep(self.dt)
        self.last_read_timing["received_monotonic_s"] = self.clock.now
        raw = {"timestamp": float(self.n), "revisions": float(self.n), "theta": 0.1, "eta": 0.05, "dop": 0.99,
               "ptotal": 6.6e-4, "adc_min": 100.0, "adc_max": 40000.0, "rev_time": 0.0167}
        self.last_attempts = [{"raw_record": raw, "valid": True}, {"raw_record": raw, "valid": False}]
        return raw

    def read_fresh(self, timeout_s):
        return self._record()

    def read_raw(self):
        return self._record()


class FakeDrive:
    def __init__(self, clock, log):
        self.clock, self.log = clock, log
        self.last_command = None
        self.connection_evidence = {"initial_asg_readbacks": "zero/off"}
        self.cleanup_evidence = {"status": "NOT_CONNECTED"}

    def __enter__(self):
        self.log.append("drive_connect")
        return self

    def __exit__(self, *exc):
        self.log.append("drive_close")
        self.cleanup_evidence = {"status": "zeroed"}
        return False

    def _command(self, f):
        self.last_command = {"actual": {"frequency_hz": f}, "received_monotonic_s": self.clock.now}

    def set_waveform(self, output, *, waveform, offset_v, peak_v, frequency_hz):
        self.log.append(("set_waveform", output, waveform, offset_v, peak_v, frequency_hz))
        self._command(frequency_hz)

    def set_frequency(self, output, *, frequency_hz):
        self.log.append(("set_frequency", output, frequency_hz))
        self._command(frequency_hz)

    def capture(self, *, decimation, full_scale_v):
        self.log.append(("capture", decimation))
        return {"raw_counts": np.zeros((2, 16), dtype=np.int16), "metadata": {"requested_monotonic_s": self.clock.now}}


@pytest.fixture(autouse=True)
def no_real_daemons(monkeypatch):
    """Never scan or signal real processes from tests."""
    monkeypatch.setattr(aq, "stop_pax_daemons", lambda: [])
    monkeypatch.setattr(aq, "port_in_use", lambda port: False)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def session(tmp_path, clock):
    return aq.new_session(tmp_path, "EO-PM-NR-C1", {"eom": "test"}, clock=clock)


def eom_block(session, clock, label="H"):
    return aq.new_block(session, label, {"psg_dials": "P 0, Q 0"}, clock=clock)


def test_capture_keeps_every_record_and_attempt(session, clock):
    block = eom_block(session, clock)
    aq.capture(block, "input_A", pax_factory=lambda: FakePAX(clock), duration_s=3.0, clock=clock)
    arrays, rec = aq.load_stage(block, "input_A")
    assert rec["status"] == "completed" and rec["n_accepted"] == 30 == len(arrays["theta"])
    assert len(rec["attempts"]) == 60  # invalid attempts kept too
    assert rec["pax"]["status"]["wavelength_nm"] == 830.0
    assert np.all(np.diff(arrays["received_monotonic_s"]) > 0)
    assert not list(block.glob("*.tmp"))


def test_stage_order_and_write_once(session, clock):
    block = eom_block(session, clock)
    pax = lambda: FakePAX(clock)
    with pytest.raises(RuntimeError, match="input_A"):
        aq.capture(block, "baseline_B", pax_factory=pax, duration_s=1, clock=clock)
    aq.capture(block, "input_A", pax_factory=pax, duration_s=1, clock=clock)
    with pytest.raises(FileExistsError):
        aq.capture(block, "input_A", pax_factory=pax, duration_s=1, clock=clock)
    with pytest.raises(ValueError):
        aq._begin_stage(block, "nope")


def test_blank_block_has_no_sweep(session, clock):
    block = aq.new_block(session, "D", {}, kind="blank", clock=clock)
    with pytest.raises(ValueError):
        aq._begin_stage(block, "sweep_B")


def test_failed_capture_is_saved_and_reraised(session, clock):
    block = eom_block(session, clock)
    with pytest.raises(RuntimeError, match="fake PAX failure"):
        aq.capture(block, "input_A", pax_factory=lambda: FakePAX(clock, fail_after=5), duration_s=3, clock=clock)
    arrays, rec = aq.load_stage(block, "input_A")
    assert rec["status"] == "failed" and len(arrays["theta"]) == 5
    aq.capture(block, "baseline_B", pax_factory=lambda: FakePAX(clock), duration_s=1, clock=clock)  # may continue


def run_to_sweep(session, clock):
    block = eom_block(session, clock)
    for stage in ("input_A", "baseline_B"):
        aq.capture(block, stage, pax_factory=lambda: FakePAX(clock), duration_s=1, clock=clock)
    return block


def test_sweep_order_segments_and_cleanup(session, clock):
    block, log = run_to_sweep(session, clock), []
    confirm = lambda: log.append("hv_on") or True
    aq.sweep(block, pax_factory=lambda: FakePAX(clock), drive_factory=lambda: FakeDrive(clock, log),
             confirm_hv_on=confirm, output="out2", offset_v=0.5, peak_v=0.46,
             segments=[{"period_s": 10.0, "cycles": 2}, {"period_s": 5.0, "cycles": 2}],
             tee={"decimation": 65536, "full_scale_v": {"in1": 1.0, "in2": 1.0}}, clock=clock)
    assert log[0] == "drive_connect" and log[1] == "hv_on"           # HV only after RP connect
    assert log[2] == ("set_waveform", "out2", "sin", 0.5, 0.46, 0.1)
    assert ("set_frequency", "out2", 0.2) in log and log[-1] == "drive_close"
    arrays, rec = aq.load_stage(block, "sweep_B")
    assert rec["status"] == "completed" and rec["drive_cleanup"] == {"status": "zeroed"}
    assert set(arrays["segment"]) == {0.0, 1.0}
    assert abs((arrays["segment"] == 0).sum() * 0.1 - 20) < 0.25 and abs((arrays["segment"] == 1).sum() * 0.1 - 10) < 0.25
    assert {"tee_seg0_pre", "tee_seg1_post"} <= set(arrays)


def test_sweep_refused_without_hv_confirmation(session, clock):
    block, log = run_to_sweep(session, clock), []
    with pytest.raises(RuntimeError, match="nothing was driven"):
        aq.sweep(block, pax_factory=lambda: FakePAX(clock), drive_factory=lambda: FakeDrive(clock, log),
                 confirm_hv_on=lambda: False, output="out2", offset_v=0.5, peak_v=0.4,
                 segments=[{"period_s": 10.0, "cycles": 1}], clock=clock)
    assert not any(isinstance(e, tuple) and e[0] == "set_waveform" for e in log) and log[-1] == "drive_close"
    assert not (block / "sweep_B.json").exists()            # nothing measured -> no file, rerun allowed
    assert "HV driver ON not confirmed" in (session / "failed_starts.log").read_text()


def test_viewer_stops_other_daemons_before_and_leftovers_after(session, clock):
    calls, stops = [], iter([[{"pid": 1, "command": "old", "how": "SIGTERM"}], [{"pid": 2, "command": "left", "how": "SIGTERM"}]])

    class Proc:
        def __init__(self, args, **kw):
            calls.append(args)
            assert "QT_QPA_PLATFORM" not in kw["env"]

        def wait(self):
            return 1     # e.g. the viewer crashed with an X error

    import os
    os.environ["QT_QPA_PLATFORM"] = "offscreen"      # as left behind by an RP drive session
    try:
        rec = aq.launch_viewer(session, "viewer.py", "python", popen=Proc, stop=lambda: next(stops), clock=clock)
    finally:
        del os.environ["QT_QPA_PLATFORM"]
    assert calls == [["python", "viewer.py"]] and rec["returncode"] == 1
    assert rec["daemons_left_running"] == [{"pid": 2, "command": "left", "how": "SIGTERM"}]
    log = (session / "pax_cleanup.log").read_text().splitlines()
    assert len(log) == 2 and "before viewer" in log[0] and "after viewer" in log[1]


def test_capture_clears_other_daemons_first(session, clock):
    block, order = eom_block(session, clock), []

    def factory():
        order.append("pax_start")
        return FakePAX(clock)
    aq.capture(block, "input_A", pax_factory=factory, duration_s=1, clock=clock,
               stop=lambda: order.append("clear") or [])
    assert order == ["clear", "pax_start"]


def test_verdict_once_and_ledger(session, clock):
    block = run_to_sweep(session, clock)
    aq.verdict(block, "redo", "PAX seat not clicked in", clock=clock)
    with pytest.raises(FileExistsError):
        aq.verdict(block, "accept", "", clock=clock)
    second = eom_block(session, clock, "V")
    assert second.name == "02_V"
    rows = aq.ledger(session)
    assert rows[0]["verdict"] == "redo" and rows[0]["stages"] == {"input_A": "completed", "baseline_B": "completed"}
    assert rows[1]["stages"] == {} and rows[1]["verdict"] is None


def test_ambient_uses_read_raw(session, clock):
    aq.ambient(session, pax_factory=lambda: FakePAX(clock), duration_s=1, clock=clock)
    rec = json.loads((session / "ambient.json").read_text())
    assert rec["status"] == "completed" and rec["n_records"] == 10
    with pytest.raises(FileExistsError):
        aq.ambient(session, pax_factory=lambda: FakePAX(clock), duration_s=1, clock=clock)


class SessionCM:
    """Like PAX.local_session(): the context manager is not the device; evidence lands on the device."""

    def __init__(self, device):
        self.device = device

    def __enter__(self):
        return self.device

    def __exit__(self, *exc):
        self.device.shutdown_evidence = {"usb_closed": True, "motor_stop_command_succeeded": True}
        return False


def test_shutdown_evidence_comes_from_the_device(session, clock):
    block = eom_block(session, clock)
    device = FakePAX(clock)
    aq.capture(block, "input_A", pax_factory=lambda: SessionCM(device), duration_s=1, clock=clock)
    _, rec = aq.load_stage(block, "input_A")
    assert rec["pax_shutdown"] == {"usb_closed": True, "motor_stop_command_succeeded": True}
    assert rec["pax_daemon_log"] == "daemon log text"


def test_wavelength_mismatch_stops_capture(session, clock):
    block = eom_block(session, clock)
    device = FakePAX(clock)
    device.set_wavelength = lambda nm: setattr(device, "wavelength", 780.0)
    with pytest.raises(RuntimeError, match="wavelength"):
        aq.capture(block, "input_A", pax_factory=lambda: device, duration_s=1, clock=clock)
    assert not (block / "input_A.json").exists() and "wavelength" in (session / "failed_starts.log").read_text()


def test_attempts_of_a_failed_read_are_kept(session, clock):
    block = eom_block(session, clock)
    device = FakePAX(clock, fail_after=3)
    original = device.read_fresh

    def failing(timeout_s):
        try:
            return original(timeout_s)
        except RuntimeError:
            device.last_attempts = [{"raw_record": None, "valid": False, "why": "stale"}]
            raise
    device.read_fresh = failing
    with pytest.raises(RuntimeError):
        aq.capture(block, "input_A", pax_factory=lambda: device, duration_s=5, clock=clock)
    _, rec = aq.load_stage(block, "input_A")
    assert rec["attempts"][-1]["why"] == "stale" and rec["n_accepted"] == 3


def test_failure_before_any_record_leaves_no_files(session, clock):
    block = eom_block(session, clock)
    with pytest.raises(RuntimeError, match="fake PAX failure"):
        aq.capture(block, "input_A", pax_factory=lambda: FakePAX(clock, fail_after=0), duration_s=1, clock=clock)
    assert sorted(f.name for f in block.iterdir()) == ["block.json"]
    aq.capture(block, "input_A", pax_factory=lambda: FakePAX(clock), duration_s=1, clock=clock)   # rerun works
    with pytest.raises(RuntimeError):
        aq.ambient(session, pax_factory=lambda: FakePAX(clock, fail_after=0), duration_s=1, clock=clock)
    assert not (session / "ambient.json").exists()
    assert len((session / "failed_starts.log").read_text().splitlines()) == 2


def test_empty_latest_block_is_replaced(session, clock):
    first = eom_block(session, clock, "H")
    second = eom_block(session, clock, "H")          # first never started a stage
    assert second.name == "01_H" and len(list((session / "blocks").iterdir())) == 1
    aq.capture(second, "input_A", pax_factory=lambda: FakePAX(clock), duration_s=1, clock=clock)
    assert eom_block(session, clock, "V").name == "02_V"
