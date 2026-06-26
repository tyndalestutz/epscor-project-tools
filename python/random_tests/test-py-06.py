from pyrpl import Pyrpl
import numpy as np
import time

# ----------------------------
# CONFIG
# ----------------------------
HOST = "192.168.1.98"
p = Pyrpl(hostname=HOST, config="interface-test-00.yaml")
r = p.rp

# ----------------------------
# CLEAN STATE
# ----------------------------
r.asg0.output_direct = "off"
r.pid0.output_direct = "off"
r.iq0.output_direct = "off"

r.scope.input1 = "in1"
r.scope.decimation = 64
r.scope.duration = 0.05

# ----------------------------
# SWEEP SETTINGS
# ----------------------------
freqs_to_test = np.logspace(np.log10(10), np.log10(5000), 12)
amplitude = 0.2

response = []

# ----------------------------
# MAIN LOOP
# ----------------------------
for f in freqs_to_test:

    print(f"\n--- Driving at {f:.2f} Hz ---")

    # drive EOM
    asg = r.asg0
    asg.output_direct = "out1"
    asg.setup(
        waveform="sin",
        frequency=f,
        amplitude=amplitude,
        offset=0,
        trigger_source="immediately"
    )

    time.sleep(0.3)  # settle time

    # acquire PD signal
    trace = r.scope.single()
    y = np.array(trace[0])

    # remove DC
    y = y - np.mean(y)

    # FFT
    dt = r.scope.duration / len(y)
    freqs = np.fft.rfftfreq(len(y), d=dt)
    spec = np.abs(np.fft.rfft(y))

    # extract nearest bin
    idx = np.argmin(np.abs(freqs - f))

    signal = spec[idx]

    # noise estimate (exclude ±5 bins)
    mask = np.ones_like(spec, dtype=bool)
    mask[max(0, idx-5): idx+6] = False
    noise = np.mean(spec[mask])

    snr = signal / noise if noise > 0 else np.inf

    response.append((f, signal, noise, snr))

    print(f"Signal: {signal:.3e}, Noise: {noise:.3e}, SNR: {snr:.2f}")

# ----------------------------
# OUTPUT
# ----------------------------
response = np.array(response)

np.save("open_loop_response.npy", response)

print("\nDONE. Saved as open_loop_response.npy")