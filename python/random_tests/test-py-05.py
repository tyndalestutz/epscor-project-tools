from pyrpl import Pyrpl
import numpy as np

# ----------------------------
# CONFIG
# ----------------------------
HOST = "192.168.1.98"
f_drive = 1000.0  # Hz (must match ASG)

p = Pyrpl(hostname=HOST, config="interface-test-00.yaml")
r = p.rp

# ----------------------------
# CLEAN STATE
# ----------------------------
r.asg0.output_direct = "off"
r.pid0.output_direct = "off"
r.iq0.output_direct = "off"

# ----------------------------
# MEASURE PD SIGNAL
# ----------------------------
r.scope.input1 = "in1"
r.scope.duration = 0.05          # longer record → better FFT resolution
r.scope.decimation = 64

trace = r.scope.single()
y = np.array(trace[0])

# remove DC (CRITICAL for clean spectral estimate)
y = y - np.mean(y)

# time axis
dt = r.scope.duration / len(y)

# ----------------------------
# FFT
# ----------------------------
freqs = np.fft.rfftfreq(len(y), d=dt)
spec = np.abs(np.fft.rfft(y))

# ----------------------------
# FIND BIN CLOSEST TO DRIVE
# ----------------------------
target_idx = np.argmin(np.abs(freqs - f_drive))

# local noise estimate (exclude target bin ±5 bins)
exclude = 5
mask = np.ones_like(spec, dtype=bool)
mask[max(0, target_idx - exclude): target_idx + exclude + 1] = False

noise_floor = np.mean(spec[mask])
signal = spec[target_idx]

snr = signal / noise_floor if noise_floor > 0 else np.inf

# ----------------------------
# REPORT
# ----------------------------
print("\n--- STRICT BIN RESULT ---")
print("Target frequency (Hz):", f_drive)
print("Closest FFT bin (Hz):", freqs[target_idx])
print("Signal amplitude:", signal)
print("Noise floor:", noise_floor)
print("SNR:", snr)