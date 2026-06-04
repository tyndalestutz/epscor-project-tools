# --- IQ ARTIFACT vs PHYSICS TEST ---
from pyrpl import Pyrpl
import numpy as np
import time

HOSTNAME = "192.168.1.98"
p = Pyrpl(hostname=HOSTNAME, config="interface-test-00.yaml")
r = p.rp


# -------------------------
# GLOBAL CLEAN STATE
# -------------------------
r.asg0.output_direct = "off"
r.pid0.output_direct = "off"
r.iq0.output_direct = "off"


# -------------------------
# CONFIGURATION
# -------------------------
drive_freq = 1000.0
drive_amp = 0.2


def run_case(mode):
    """
    mode:
        'A' = full interferometer
        'B' = electronics loopback (artifact test)
        'C' = optical null (no interferometer signal)
    """

    print(f"\n========================")
    print(f"RUNNING CASE {mode}")
    print(f"========================")

    asg = r.asg0
    iq = r.iq0


    # -------------------------
    # DEFAULT CLEAN CONFIG
    # -------------------------
    asg.output_direct = "off"
    r.scope.input1 = "in1"


    # -------------------------
    # MODE CONFIGURATION
    # -------------------------

    if mode == "A":
        # full physics path
        asg.output_direct = "off"

    elif mode == "B":
        # loopback test:
        # route ASG into ADC directly (NO optics)
        r.scope.input1 = "asg0"

    elif mode == "C":
        # optics suppressed (still drives electronics)
        asg.output_direct = "out1"
        # user must physically block beam / misalign interferometer
        print(">> Ensure optical beam is blocked or interferometer is off-fringe")

    else:
        raise ValueError("Unknown mode")


    # -------------------------
    # DRIVE SETUP
    # -------------------------
    asg.setup(
        waveform="sin",
        frequency=drive_freq,
        amplitude=drive_amp,
        offset=0,
        trigger_source="immediately"
    )


    # -------------------------
    # IQ SETUP
    # -------------------------
    iq.setup(
        frequency=drive_freq,
        input="in1",
        output_direct="off",
        output_signal="quadrature",
        bandwidth=[5e3, 10e3],
        gain=1.0
    )

    time.sleep(0.3)


    # -------------------------
    # MEASUREMENT
    # -------------------------
    r.scope.input1 = "iq0"
    r.scope.duration = 0.01
    r.scope.decimation = 64

    trace = r.scope.single()

    I = np.array(trace[0])
    Q = np.array(trace[1])

    A = np.sqrt(I**2 + Q**2)

    result = {
        "I_mean": np.mean(I),
        "Q_mean": np.mean(Q),
        "A_rms": np.mean(A)
    }

    return result



# -------------------------
# RUN ALL TESTS
# -------------------------
results = {}

for mode in ["A", "B", "C"]:
    results[mode] = run_case(mode)
    time.sleep(0.5)


# -------------------------
# PRINT SUMMARY
# -------------------------
print("\n\n========= SUMMARY =========")
for k, v in results.items():
    print(f"\nCASE {k}")
    print(v)