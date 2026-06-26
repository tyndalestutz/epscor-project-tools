from pyrpl import Pyrpl
import numpy as np
import time

HOST = "192.168.1.98"
CONFIG = "interface-test-00.yaml"

p = Pyrpl(hostname=HOST, config=CONFIG)
r = p.rp

# -------------------------
# CLEAN STATE
# -------------------------
r.asg0.output_direct = "off"
r.pid0.output_direct = "off"
r.iq0.output_direct = "off"

# -------------------------
# SCOPE CONFIG
# -------------------------
r.scope.input1 = "in1"
r.scope.duration = 0.01
r.scope.decimation = 64

# -------------------------
# LOCK-IN FUNCTION (software)
# -------------------------
def lockin(frequency, signal, t):
    ref_cos = np.cos(2 * np.pi * frequency * t)
    ref_sin = np.sin(2 * np.pi * frequency * t)

    I = np.mean(signal * ref_cos)
    Q = np.mean(signal * ref_sin)

    return I, Q

# -------------------------
# MEASURE BASELINE (NO DRIVE)
# -------------------------
print("\n--- Baseline (no drive) ---")
r.asg0.output_direct = "off"
time.sleep(0.2)

trace = r.scope.single()
baseline_signal = np.array(trace[0])
t = np.linspace(0, r.scope.duration, len(baseline_signal))

# just noise reference (optional but useful)
baseline_I, baseline_Q = lockin(1000, baseline_signal, t)
baseline = baseline_I + 1j * baseline_Q

# -------------------------
# SWEEP
# -------------------------
freqs = np.logspace(np.log10(10), np.log10(5000), 10)
gain = 0.2

G = []

for f in freqs:

    print(f"\n--- Driving at {f:.2f} Hz ---")

    # drive
    r.asg0.output_direct = "out1"
    r.asg0.setup(
        waveform="sin",
        frequency=f,
        amplitude=gain,
        offset=0,
        trigger_source="immediately"
    )

    time.sleep(0.3)

    # measure
    trace = r.scope.single()
    x = np.array(trace[0])
    t = np.linspace(0, r.scope.duration, len(x))

    # lock-in
    I, Q = lockin(f, x, t)
    Gf = (I + 1j * Q) / gain

    # subtract baseline projection
    Gf = Gf - baseline / gain

    G.append(Gf)

    print("  I:", I)
    print("  Q:", Q)
    print("  |G|:", np.abs(Gf))
    print("  phase:", np.angle(Gf))

# -------------------------
# SAVE
# -------------------------
np.savez(
    "tf_raw_lockin.npz",
    freqs=freqs,
    G=np.array(G)
)

print("\nDONE: Saved tf_raw_lockin.npz")