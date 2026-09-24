"""Manual antipodal alignment geometry and a cache-only secondary Qt window."""
import math
import time

import numpy as np


def unit_stokes(reading):
    vector = np.array([reading.s1, reading.s2, reading.s3], dtype=float)
    norm = np.linalg.norm(vector)
    if not np.all(np.isfinite(vector)) or norm < 1e-12:
        raise ValueError("Invalid Stokes direction")
    return vector / norm


def usable_dop(reading, minimum_dop):
    # Same purity gate as the suite's trusted target capture. The raw monitor
    # still logs all acquisition-valid DoP, including small excursions above 1.
    return math.isfinite(reading.dop) and minimum_dop <= reading.dop <= 1


def metrics(reference, live):
    """Pure-state geometry; callers separately qualify DoP and sample freshness."""
    s_live = unit_stokes(live)
    dot = float(np.clip(np.dot(reference["stokes"], s_live), -1, 1))
    overlap = math.sqrt((1 + dot) / 2)
    result = dict(dot=dot, error_deg=math.degrees(math.acos(-dot)), overlap=overlap,
                  v_pol=overlap, live=s_live)
    p_ref, p_live = reference["power"], live.ptotal
    if all(math.isfinite(p) and p > 0 for p in (p_ref, p_live)):
        # Ratio form avoids unnecessary large intermediate power products.
        ratio = min(p_ref, p_live) / max(p_ref, p_live)
        balance = 2 * math.sqrt(ratio) / (1 + ratio)
        result.update(balance=balance, v_pred=balance * overlap)
    return result


class ReferenceCapture:
    """Accumulate unique fresh directions; calculate spread only once at finish."""
    def __init__(self, started, duration, minimum_dop, max_spread_deg):
        self.started, self.deadline = started, started + duration
        self.minimum_dop, self.max_spread_deg = minimum_dop, max_spread_deg
        self.vectors, self.dops, self.powers = [], [], []
        self.sum_stokes = np.zeros(3)
        self.seen = set()
        self.rejected = 0
        self.gap = False

    def add(self, sample):
        if not self.started <= sample["received_monotonic"] <= self.deadline or sample["sample"] in self.seen:
            return
        self.seen.add(sample["sample"])
        reading = sample["reading"]
        try:
            vector = unit_stokes(reading)
            if not usable_dop(reading, self.minimum_dop):
                raise ValueError("DoP below trusted capture threshold or outside 0–1")
        except ValueError:
            self.rejected += 1
            return
        self.sum_stokes += vector
        self.vectors.append(vector)
        self.dops.append(reading.dop)
        self.powers.append(reading.ptotal)

    def finish(self):
        if self.gap:
            raise ValueError("Reference samples missed by the display cache; reacquire")
        count = len(self.vectors)
        if count < 3:
            raise ValueError(f"REFERENCE POLARIZATION UNSTABLE: only {count} usable samples; need at least 3")
        mean = self.sum_stokes / count
        norm = float(np.linalg.norm(mean))
        if norm < 1e-6:
            raise ValueError("REFERENCE POLARIZATION UNSTABLE: mean direction is undefined")
        reference = mean / norm
        angles = np.degrees(np.arccos(np.clip(np.asarray(self.vectors) @ reference, -1, 1)))
        spread = float(np.sqrt(np.mean(angles ** 2)))
        return dict(stokes=reference.tolist(), target=(-reference).tolist(), sample_count=count,
                    rejected_count=self.rejected, mean_dop=float(np.mean(self.dops)),
                    power=float(np.mean(self.powers)), rms_spread_deg=spread,
                    resultant_length=norm, unstable=spread > self.max_spread_deg or self.rejected > 0,
                    theta_deg=math.degrees(.5 * math.atan2(reference[1], reference[0])),
                    eta_deg=math.degrees(.5 * math.asin(float(np.clip(reference[2], -1, 1)))),
                    first_sample=min(self.seen), last_sample=max(self.seen),
                    started_monotonic=self.started, ended_monotonic=self.deadline,
                    minimum_dop=self.minimum_dop, max_spread_deg=self.max_spread_deg)


def create_window(parent, cache, config, emit_event):
    """No instrument or logger ownership; closing destroys only this window."""
    from qtpy import QtCore, QtWidgets

    class Orthogonalizer(QtWidgets.QWidget):
        def __init__(self):
            super().__init__(parent, QtCore.Qt.Window)
            self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
            self.setWindowTitle("PAX · Orthogonalizer")
            self.setStyleSheet(parent.styleSheet())
            self.resize(1000, 790)
            self.reference = None
            self.capture = None
            self.cursor = 0
            self.mode = "REFERENCE"
            layout = QtWidgets.QVBoxLayout(self)
            layout.setContentsMargins(20, 20, 20, 16)
            layout.setSpacing(14)
            controls = QtWidgets.QHBoxLayout()
            self.acquire_button = QtWidgets.QPushButton("Acquire Reference")
            self.clear_button = QtWidgets.QPushButton("Clear Reference")
            self.align_button = QtWidgets.QPushButton("Align to Orthogonal")
            for button, action in ((self.acquire_button, self.acquire), (self.clear_button, self.clear), (self.align_button, self.align)):
                button.clicked.connect(action)
                controls.addWidget(button)
            layout.addLayout(controls)
            self.duration = QtWidgets.QDoubleSpinBox()
            self.duration.setRange(.1, 60)
            self.duration.setDecimals(1)
            self.duration.setValue(config.pax_live_reference_duration_s)
            self.duration.setSuffix(" s")
            self.duration.setStyleSheet("font-size: 17px; padding: 4px;")
            timing = QtWidgets.QHBoxLayout()
            timing.addWidget(QtWidgets.QLabel("Reference duration"))
            timing.addWidget(self.duration)
            timing.addStretch()
            timing.addWidget(QtWidgets.QLabel(f"Reference DoP ≥ {config.minimum_dop:g}; spread warning > {config.pax_live_reference_max_spread_deg:g}°"))
            layout.addLayout(timing)
            self.status = QtWidgets.QLabel("REFERENCE · Open one arm, block the other, then acquire.")
            self.status.setWordWrap(True)
            self.status.setStyleSheet("font-size: 18px;")
            layout.addWidget(self.status)
            cards = QtWidgets.QHBoxLayout()
            self.values = {}
            for key, title in (("error_deg", "ORTHOGONALITY ERROR · deg"), ("overlap", "JONES OVERLAP"), ("v_pol", "Vpol · pure-state %")):
                card = QtWidgets.QFrame()
                card.setObjectName("readoutCard")
                box = QtWidgets.QVBoxLayout(card)
                box.setContentsMargins(12, 20, 12, 20)
                label = QtWidgets.QLabel(title)
                label.setStyleSheet("font-size: 15px; color: #bac4cf;")
                value = QtWidgets.QLabel("—")
                value.setStyleSheet("font-size: 48px; font-weight: 600;")
                for widget in (label, value):
                    widget.setAlignment(QtCore.Qt.AlignCenter)
                    box.addWidget(widget)
                self.values[key] = value
                cards.addWidget(card, 1)
            layout.addLayout(cards)
            self.meter = QtWidgets.QProgressBar()
            self.meter.setRange(0, 1800)
            self.meter.setValue(0)
            self.meter.setTextVisible(False)
            self.meter.setFixedHeight(22)
            self.meter.setStyleSheet("QProgressBar { border: 1px solid #435160; background: #1e2731; } QProgressBar::chunk { background: #9fb2c4; }")
            layout.addWidget(self.meter)
            layout.addWidget(QtWidgets.QLabel("180° away                                                                              0° target (full bar)"))
            comparison = QtWidgets.QFrame()
            comparison.setObjectName("readoutCard")
            grid = QtWidgets.QGridLayout(comparison)
            self.comparison = {}
            for column, title in enumerate(("", "TARGET = −REFERENCE", "LIVE")):
                label = QtWidgets.QLabel(title)
                label.setAlignment(QtCore.Qt.AlignCenter)
                label.setStyleSheet("font-size: 17px; color: #bac4cf;")
                grid.addWidget(label, 0, column)
            for row, name in enumerate(("S1", "S2", "S3"), 1):
                grid.addWidget(QtWidgets.QLabel(name), row, 0)
                for column, kind in enumerate(("target", "live"), 1):
                    label = QtWidgets.QLabel("—")
                    label.setAlignment(QtCore.Qt.AlignCenter)
                    label.setStyleSheet("font-size: 27px;")
                    grid.addWidget(label, row, column)
                    self.comparison[kind, row - 1] = label
            layout.addWidget(comparison)
            self.details = QtWidgets.QLabel("Reference: —\nLive: —")
            self.details.setStyleSheet("font-size: 17px;")
            self.details.setWordWrap(True)
            layout.addWidget(self.details)
            self.model = QtWidgets.QLabel("Pure-state overlap model; not measured fringe visibility.")
            self.model.setWordWrap(True)
            self.model.setStyleSheet("font-size: 15px; color: #bac4cf;")
            layout.addWidget(self.model)
            layout.addStretch()
            self.timer = QtCore.QTimer(self)
            self.timer.timeout.connect(self.tick)
            self.timer.start(50)
            self.align_button.setEnabled(False)
            self.tick()

        def acquire(self):
            latest = cache.latest()
            self.cursor = latest["sample"] if latest else 0
            self.reference = None
            self.mode = "REFERENCE"
            self.capture = ReferenceCapture(time.monotonic(), self.duration.value(), config.minimum_dop,
                                            config.pax_live_reference_max_spread_deg)
            self.acquire_button.setEnabled(False)
            self.align_button.setEnabled(False)
            self.duration.setEnabled(False)
            emit_event("orthogonalizer_reference_start", duration_s=self.duration.value(), after_sample=self.cursor,
                       minimum_dop=config.minimum_dop, max_spread_deg=config.pax_live_reference_max_spread_deg)
            self.tick()

        def clear(self):
            emit_event("orthogonalizer_reference_clear", cancelled_capture=self.capture is not None)
            self.capture = self.reference = None
            self.mode = "REFERENCE"
            self.acquire_button.setEnabled(True)
            self.align_button.setEnabled(False)
            self.duration.setEnabled(True)
            self.status.setText("REFERENCE · Open one arm, block the other, then acquire.")
            self.tick()

        def align(self):
            if self.reference is not None:
                self.mode = "ALIGN"
                emit_event("orthogonalizer_align", reference=self.reference)
                self.tick()

        def tick(self):
            now = time.monotonic()
            if self.capture is not None:
                samples, gap = cache.after(self.cursor)
                self.capture.gap |= gap
                for sample in samples:
                    self.capture.add(sample)
                    self.cursor = sample["sample"]
                remaining = max(0, self.capture.deadline - now)
                self.status.setText(f"REFERENCE · {remaining:.1f} s remaining · {len(self.capture.vectors)} usable · {self.capture.rejected} excluded")
                if now >= self.capture.deadline:
                    try:
                        self.reference = self.capture.finish()
                        quality = "REFERENCE POLARIZATION UNSTABLE" if self.reference["unstable"] else "Reference ready"
                        self.status.setText(quality + " · Switch arms manually, then click Align to Orthogonal.")
                        emit_event("orthogonalizer_reference_set", **self.reference)
                        self.align_button.setEnabled(True)
                    except ValueError as exc:
                        self.status.setText(str(exc))
                        emit_event("orthogonalizer_reference_rejected", reason=str(exc))
                    self.capture = None
                    self.acquire_button.setEnabled(True)
                    self.duration.setEnabled(True)
            sample = cache.latest()
            fresh = sample is not None and now - sample["received_monotonic"] <= 1
            reading = sample["reading"] if fresh else None
            live = unit_stokes(reading) if reading else None
            for index in range(3):
                self.comparison["target", index].setText(f"{self.reference['target'][index]:+.4f}" if self.reference else "—")
                self.comparison["live", index].setText(f"{live[index]:+.4f}" if live is not None else "—")
            reference_text = "Reference: —"
            if self.reference:
                ref = self.reference
                reference_text = (f"Reference: θ {ref['theta_deg']:+.3f}° · η {ref['eta_deg']:+.3f}° · DoP {ref['mean_dop']:.4f} · {ref['power'] * 1e6:.3f} µW\n"
                                  f"{ref['sample_count']} samples · {ref['rejected_count']} excluded · RMS spread {ref['rms_spread_deg']:.3f}°")
            live_text = (f"Live: θ {math.degrees(reading.theta):+.3f}° · η {math.degrees(reading.eta):+.3f}° · DoP {reading.dop:.4f} · {reading.ptotal * 1e6:.3f} µW"
                         if reading else "Live: WAITING FOR PAX…")
            self.details.setText(reference_text + "\n" + live_text)
            for value in self.values.values():
                value.setText("—")
            self.meter.setValue(0)
            self.model.setText("Pure-state overlap model; not measured fringe visibility.")
            if self.mode == "ALIGN" and self.reference:
                if reading is None or not usable_dop(reading, config.minimum_dop):
                    self.status.setText("ALIGN · WAITING FOR PAX…" if reading is None else "ALIGN · Live DoP outside trusted range; overlap unavailable.")
                    return
                value = metrics(self.reference, reading)
                self.values["error_deg"].setText(f"{value['error_deg']:.2f}")
                self.values["overlap"].setText(f"{value['overlap']:.4f}")
                self.values["v_pol"].setText(f"{100 * value['v_pol']:.2f}")
                self.meter.setValue(round((180 - value["error_deg"]) * 10))
                quality = " · REFERENCE POLARIZATION UNSTABLE" if self.reference["unstable"] else ""
                self.status.setText("ALIGN · Adjust the open arm toward the fixed target." + quality)
                prediction = f" · power-balanced Vpred {100 * value['v_pred']:.2f}%" if "v_pred" in value else ""
                self.model.setText(f"Stokes dot {value['dot']:+.6f}{prediction}\nPure-state model only; power balance assumes the same measurement chain for both arms.")

        def closeEvent(self, event):
            self.timer.stop()
            if self.capture is not None:
                emit_event("orthogonalizer_reference_cancelled", reason="window closed")
            self.capture = None
            event.accept()

    return Orthogonalizer()
