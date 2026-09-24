"""Small Qt instrument panel; one PAX producer and an independent CSV logger."""
from collections import deque
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from queue import SimpleQueue
import signal
from threading import Event, Lock, Thread
import time

from ..hardware.pax_interface import PAXNotReady


FIELDS = ("sample", "utc", "elapsed_s", "pax_requested_s", "pax_received_s",
          "pax_timestamp", "pax_revisions", "pax_ptotal", "s1", "s2", "s3", "dop",
          "theta", "eta", "theta_deg", "eta_deg", "pax_adc_min", "pax_adc_max",
          "pax_rev_time", "pax_measurement_id", "pax_wavelength_nm", "measurement_status",
          "rp_state", "pax_raw_json")
# Scaling is display-only. CSV theta/eta remain radians and power remains watts.
READOUTS = (("pax_ptotal", "POWER · µW", 1e6), ("dop", "DoP · fraction", 1),
            ("s1", "S1", 1), ("s2", "S2", 1), ("s3", "S3", 1),
            ("theta_deg", "θ · deg", 1), ("eta_deg", "η / ellipticity · deg", 1))


class RecentReadings:
    """One-second endpoint differences, with no smoothing of current values."""
    def __init__(self):
        self.rows = deque()

    def add(self, row):
        self.rows.append(row)
        while len(self.rows) > 2 and self.rows[1]["elapsed_s"] <= row["elapsed_s"] - 1:
            self.rows.popleft()
        first = self.rows[0]
        span = row["elapsed_s"] - first["elapsed_s"]
        delta = {key: row[key] - first[key] for key, _, _ in READOUTS}
        # Ellipse azimuth is periodic at 180 degrees.
        delta["theta_deg"] = (delta["theta_deg"] + 90) % 180 - 90
        return {"row": row, "delta": delta, "span_s": span,
                "rate_hz": (len(self.rows) - 1) / span if span > 0 else 0}


class SampleCache:
    """Bounded recent samples for independent GUI consumers, never hardware reads.

    The lock covers only append/copy operations, not formatting or calculations.
    Samples are immutable after publication. A slow consumer detects an overrun;
    the independent logger queue never drops scientific samples.
    """
    def __init__(self, capacity=2048):
        self.lock = Lock()
        self.samples = deque(maxlen=capacity)

    def publish(self, sample):
        with self.lock:
            self.samples.append(sample)

    def latest(self):
        with self.lock:
            return self.samples[-1] if self.samples else None

    def after(self, cursor):
        with self.lock:
            recent = list(self.samples)
        gap = bool(recent and recent[0]["sample"] > cursor + 1)
        return [sample for sample in recent if sample["sample"] > cursor], gap


def sample_row(sample):
    """CSV/display conversion happens in consumers, never the PAX producer."""
    reading, raw = sample["reading"], sample["raw"]
    return dict(sample=sample["sample"], utc=datetime.fromtimestamp(sample["host_time"], timezone.utc).isoformat(),
                elapsed_s=sample["elapsed_s"], pax_requested_s=sample["requested_s"], pax_received_s=sample["elapsed_s"],
                pax_timestamp=reading.timestamp, pax_revisions=reading.revisions, pax_ptotal=reading.ptotal,
                s1=reading.s1, s2=reading.s2, s3=reading.s3, dop=reading.dop,
                theta=reading.theta, eta=reading.eta, theta_deg=math.degrees(reading.theta), eta_deg=math.degrees(reading.eta),
                pax_adc_min=reading.adc_min, pax_adc_max=reading.adc_max, pax_rev_time=reading.rev_time,
                pax_measurement_id=raw.get("measurement_id", ""), pax_wavelength_nm=sample["wavelength_nm"],
                measurement_status="valid_fresh", rp_state="not_connected_or_measured")


def log_samples(output_file, messages, stop, result):
    """One logger drains raw samples and optional small GUI event records."""
    events = None
    try:
        with Path(output_file).open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            handle.flush()
            while True:
                message = messages.get()
                if message is None:
                    break
                kind, item = message
                if kind == "sample":
                    writer.writerow(sample_row(item) | {"pax_raw_json": json.dumps(item["raw"])})
                    handle.flush()
                else:
                    if events is None:
                        events = Path(output_file).with_name("events.jsonl").open("a")
                    events.write(json.dumps(item) + "\n")
                    events.flush()
    except Exception as exc:
        result["error"] = f"Logger {type(exc).__name__}: {exc}"
        stop.set()
    finally:
        if events is not None:
            events.close()


def collect(app, output_file, stop, cache, result, messages=None):
    """Use the normal adapter/session, entirely on its owning worker thread."""
    started = time.monotonic()
    messages = messages if messages is not None else SimpleQueue()
    logger = Thread(target=log_samples, args=(output_file, messages, stop, result), name="pax-live-log")
    logger.start()
    count = 0
    try:
        app.connect(pax_only=True)
        wavelength = app.pax.client.get_wavelength()
        while not stop.is_set():
            requested = time.monotonic() - started
            try:
                reading = app.pax.read_fresh_polarization()
            except PAXNotReady:
                continue
            received = time.monotonic()
            count += 1
            sample = dict(sample=count, reading=reading, raw=dict(app.pax.last_raw_record or {}),
                          elapsed_s=received - started, requested_s=requested,
                          received_monotonic=received, host_time=time.time(), wavelength_nm=wavelength)
            messages.put(("sample", sample))
            cache.publish(sample)
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            app.disconnect()
        except Exception as exc:
            result["cleanup_error"] = f"{type(exc).__name__}: {exc}"
        messages.put(None)
        logger.join()  # shutdown only; GUI stays responsive while flushing
        result.update(sample_count=count, duration_s=time.monotonic() - started)


def run_panel(app, output_file):
    """GUI lives on the main thread; YAQC connection, reads and cleanup do not."""
    from qtpy import QtCore, QtWidgets

    gui = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    loop = QtCore.QEventLoop()
    cache = SampleCache()
    messages = SimpleQueue()
    stop = Event()
    result = {}
    worker = Thread(target=collect, args=(app, output_file, stop, cache, result, messages), name="pax-live")

    def emit_event(event, **values):
        messages.put(("event", dict(event=event, utc=datetime.now(timezone.utc).isoformat(),
                                    host_monotonic=time.monotonic(), **values)))

    class Panel(QtWidgets.QWidget):
        def __init__(self):
            super().__init__()
            self.allow_close = False
            self.finishing = False
            self.last = None
            self.last_received = None
            self.cursor = 0
            self.history = RecentReadings()
            self.orthogonalizer = None
            self.started = time.monotonic()
            self.setWindowTitle("PAX · manual alignment")
            self.resize(1120, 740)
            self.setMinimumSize(960, 650)
            self.setStyleSheet("QWidget { background: #151a20; color: #f3f5f7; } "
                               "QFrame#readoutCard { background: #1e2731; border: 1px solid #435160; border-radius: 8px; } "
                               "QFrame#readoutCard QLabel { background: transparent; border: none; } "
                               "QPushButton { background: #303c49; padding: 12px 30px; font-size: 18px; }")
            layout = QtWidgets.QVBoxLayout(self)
            layout.setContentsMargins(20, 20, 20, 16)
            layout.setSpacing(12)
            grid = QtWidgets.QGridLayout()
            grid.setSpacing(14)
            layout.addLayout(grid, 1)
            self.values, self.deltas = {}, {}
            positions = ((0, 0, 3), (0, 3, 3), (1, 0, 2), (1, 2, 2), (1, 4, 2), (2, 0, 3), (2, 3, 3))
            for (key, title, _), (row, column, span) in zip(READOUTS, positions):
                card = QtWidgets.QFrame()
                card.setObjectName("readoutCard")
                box = QtWidgets.QVBoxLayout(card)
                box.setContentsMargins(16, 14, 16, 14)
                box.setSpacing(6)
                label = QtWidgets.QLabel(title)
                label.setStyleSheet("font-size: 20px; color: #bac4cf;")
                value = QtWidgets.QLabel("—")
                value.setStyleSheet("font-size: 56px; font-weight: 600;")
                delta = QtWidgets.QLabel("Δ —")
                delta.setStyleSheet("font-size: 18px; color: #bac4cf;")
                box.addStretch()
                for widget in (label, value, delta):
                    widget.setAlignment(QtCore.Qt.AlignCenter)
                    box.addWidget(widget)
                box.addStretch()
                grid.addWidget(card, row, column, 1, span)
                self.values[key], self.deltas[key] = value, delta
            note = QtWidgets.QLabel("S1–S3: normalized direction reported by the suite · Δ over ~1 s · no averaging")
            note.setStyleSheet("font-size: 13px; color: #bac4cf;")
            note.setAlignment(QtCore.Qt.AlignCenter)
            layout.addWidget(note)
            self.status = QtWidgets.QLabel("WAITING FOR PAX…")
            self.status.setStyleSheet("font-size: 16px;")
            self.status.setWordWrap(True)
            layout.addWidget(self.status)
            self.button = QtWidgets.QPushButton("Stop")
            self.button.clicked.connect(self.stop)
            controls = QtWidgets.QHBoxLayout()
            self.orthogonalizer_button = QtWidgets.QPushButton("Orthogonalizer")
            self.orthogonalizer_button.clicked.connect(self.open_orthogonalizer)
            controls.addWidget(self.orthogonalizer_button)
            controls.addStretch()
            controls.addWidget(self.button)
            layout.addLayout(controls)
            self.timer = QtCore.QTimer(self)
            self.timer.timeout.connect(self.tick)
            self.timer.start(50)

        def open_orthogonalizer(self):
            if self.orthogonalizer is None:
                from .orthogonalizer import create_window
                self.orthogonalizer = create_window(self, cache, app.config, emit_event)
                self.orthogonalizer.destroyed.connect(lambda: setattr(self, "orthogonalizer", None))
            self.orthogonalizer.show()
            self.orthogonalizer.raise_()
            self.orthogonalizer.activateWindow()

        def stop(self):
            self.orthogonalizer_button.setEnabled(False)
            if self.orthogonalizer is not None:
                self.orthogonalizer.close()
            stop.set()
            self.button.setEnabled(False)
            self.status.setText("STOPPING… finishing the current PAX request and closing the connection.")

        def closeEvent(self, event):
            if self.allow_close:
                event.accept()
            else:
                event.ignore()
                self.stop()

        def tick(self):
            if self.finishing:
                return
            batch, gap = cache.after(self.cursor)
            if gap:
                self.history = RecentReadings()
            item = None
            for sample in batch:
                item = self.history.add(sample_row(sample)) | {"received_monotonic": sample["received_monotonic"]}
                self.cursor = sample["sample"]
            if item and "row" in item:
                self.last = item
                self.last_received = item["received_monotonic"]
                for key, _, scale in READOUTS:
                    value = item["row"][key] * scale
                    self.values[key].setText(f"{value:.6g}" if key == "pax_ptotal" else f"{value:+.4f}")
                    trend = f"Δ {item['delta'][key] * scale:+.4g} / {item['span_s']:.1f} s" if item["span_s"] > 0 else "Δ —"
                    if key == "eta_deg":
                        trend += f"    |η| {abs(value):.4f}°"
                    self.deltas[key].setText(trend)
            elapsed = time.monotonic() - self.started
            age = time.monotonic() - self.last_received if self.last_received is not None else float("inf")
            waiting = age > 1
            if not stop.is_set():
                if waiting:
                    for key in self.values:
                        self.values[key].setText("—")
                        self.deltas[key].setText("Δ —")
                    self.status.setText(f"WAITING FOR PAX…   elapsed {elapsed:.1f} s · no fresh reading · 0 Hz")
                elif self.last:
                    row = self.last["row"]
                    self.status.setText(f"LIVE · {elapsed:.1f} s · {self.last['rate_hz']:.1f} Hz · "
                                        f"timestamp {row['pax_timestamp']:g} · revision {row['pax_revisions']:g} · "
                                        f"sample {row['sample']} · age {age:.1f} s")
            if not worker.is_alive():
                self.finishing = True
                self.orthogonalizer_button.setEnabled(False)
                if self.orthogonalizer is not None:
                    self.orthogonalizer.close()
                self.timer.stop()
                worker.join()
                message = "Save this alignment run?"
                for field in ("error", "cleanup_error"):
                    if result.get(field):
                        message += f"\n{result[field]}"
                message += f"\n{result.get('sample_count', 0)} samples recorded."
                answer = QtWidgets.QMessageBox.question(self, "Alignment stopped", message,
                         QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Discard, QtWidgets.QMessageBox.Save)
                result["disposition"] = "discard" if answer == QtWidgets.QMessageBox.Discard else "save"
                self.allow_close = True
                self.close()
                loop.quit()

    panel = Panel()
    previous_sigint = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, lambda *_: panel.stop())
    try:
        panel.show()
        worker.start()
        loop.exec_()
    finally:
        stop.set()
        # Normally already stopped. Keep the GUI responsive during abnormal exit,
        # and never let the runner disconnect or delete data under a live writer.
        while worker.is_alive():
            worker.join(.05)
            gui.processEvents()
        panel.timer.stop()
        panel.allow_close = True
        panel.close()
        signal.signal(signal.SIGINT, previous_sigint)
    return result
