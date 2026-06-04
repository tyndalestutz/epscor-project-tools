#!/usr/bin/env python3

import numpy as np
import matplotlib.pyplot as plt
from pyrpl import Pyrpl
import time

# RP configuration
HOSTNAME = "192.168.1.98"
CONFIG_NAME = "scope_config"
p = Pyrpl(hostname=HOSTNAME, config=CONFIG_NAME)
pid = p.rp.pid0
pid.input = 'in1'
pid.output_direct = 'out1'

# PID parameters --- EO TUNING
GAIN_SCALE = 1
PID_P = 6.9 * GAIN_SCALE
PID_I = 7900 * GAIN_SCALE
SETPOINT = 0.5

# Network analyzer parameters
F_START = 8e2
F_STOP = 60e3
POINTS = 501
RBW = 100
AVG_PER_POINT = 5
EXCITATION_AMP = 0.01

# Plot behavior
USE_DB = True
PLOT_RECONSTRUCTED_LOOP = True   # OLTF ≈ data * pid_tf
PLOT_BOTH = True            # Optional: show CLTF + OLTF separately


# ============================
# PID CONTROL FUNCTIONS
# ============================

def relock(pid):
    """Engage PID loop with defined parameters."""
    print("[LOCK] relocking...")
    pid.pause_gains = 'i'
    pid.paused = True
    pid.ival = 0.0

    pid.p = PID_P
    pid.i = PID_I
    pid.setpoint = SETPOINT

    pid.paused = False
    print("[LOCK] engaged")


def unlock(pid):
    """Disable PID loop (open loop)."""
    print("[LOCK] unlocking...")
    pid.pause_gains = 'i'
    pid.paused = True
    pid.ival = 0.0
    print("[LOCK] open loop")


def reset(pid):
    """Reset integrator and relock."""
    print("[LOCK] reset...")
    unlock(pid)
    time.sleep(0.5)
    relock(pid)


def status(pid):
    """Print current PID state."""
    print("\n--- PID STATUS ---")
    print(f"P: {pid.p}")
    print(f"I: {pid.i}")
    print(f"Setpoint: {pid.setpoint}")
    print(f"Paused: {pid.paused}")
    print(f"I term: {pid.ival}")
    print("------------------\n")


# ============================
# NETWORK ANALYZER SWEEP
# ============================

def run_sweep(p, pid):
    """
    Runs a frequency sweep and plots:
    - Measured transfer function (CLTF-like)
    - Optionally reconstructed OLTF = data * pid_tf
    """

    if pid.paused:
        print("[ERROR] Loop is unlocked. Refusing to run sweep.")
        return

    confirm = input("Type 'yes' to run sweep: ").strip().lower()
    if confirm != "yes":
        print("[ABORTED]")
        return

    print("[NA] Running sweep...")

    na = p.networkanalyzer

    # Configure network analyzer
    na.setup(
        start_freq=F_START,
        stop_freq=F_STOP,
        logscale=True,
        points=POINTS,
        rbw=RBW,
        avg_per_point=AVG_PER_POINT,
        amplitude=EXCITATION_AMP,
        acbandwidth=0,
        sleepcycles=0.1,
        input="in1",
        output_direct="out1",
        trace_average=1
    )

    # Run sweep
    t0 = time.time()
    data = na.single()   # complex response
    t1 = time.time()

    print(f"[NA] Sweep completed in {t1 - t0:.2f} s")

    # Frequency axis
    freq = na.frequencies

    # PID transfer function C(jw)
    pid_tf = pid.transfer_function(freq)

    # ---------------------------------
    # Transfer functions
    # ---------------------------------

    cltf = data
    oltf = (data) / (1 - data * pid_tf) 

    # Decide what to plot
    if PLOT_BOTH:
        tfs = [
            (cltf, "Measured Transfer Function (CLTF)"),
            (oltf, "Inferred Open-Loop TF (OLTF)")
        ]
    else:
        if PLOT_RECONSTRUCTED_LOOP:
            tfs = [(oltf, "Open-Loop Transfer Function (Inferred)")]
        else:
            tfs = [(cltf, "Measured System Transfer Function")]

    # ---------------------------------
    # Plotting
    # ---------------------------------

    # Single figure: plot all requested transfer functions on the same axes
    fig, axs = plt.subplots(2, 1, figsize=(10, 7), sharex=True)

    for tf, label in tfs:
        # Magnitude
        mag = 20 * np.log10(np.abs(tf)) if USE_DB else np.abs(tf)

        # Phase (unwrap + reference shift per TF)
        phase = np.unwrap(np.angle(tf)) * 180 / np.pi
        phase -= phase[0]

        axs[0].plot(freq, mag, label=label)
        axs[1].plot(freq, phase, label=label)

    # Formatting
    axs[0].set_xscale("log")
    axs[0].set_ylabel("Magnitude (dB)" if USE_DB else "Magnitude")
    axs[0].set_title("Piezo + DET100A/M: CLTF and OLTF")
    axs[0].grid(True, which="both", ls="--", alpha=0.4)
    axs[0].legend()

    axs[1].set_xscale("log")
    axs[1].set_xlabel("Frequency (Hz)")
    axs[1].set_ylabel("Phase (deg)")
    axs[1].grid(True, which="both", ls="--", alpha=0.4)
    axs[1].legend()

    fig.tight_layout()
    plt.show()


# ============================
# MAIN PROGRAM
# ============================

print("[INIT] Connecting to Red Pitaya...")

# Start locked
relock(pid)

print("""
=== LOCK + NA CLI ===
Commands:
  relock | unlock | reset | status
  run    | quit
=====================
""")

# CLI loop
while True:
    try:
        cmd = input(">>> ").strip().lower()

        if cmd == "relock":
            relock(pid)

        elif cmd == "unlock":
            unlock(pid)

        elif cmd == "reset":
            reset(pid)

        elif cmd == "status":
            status(pid)

        elif cmd == "run":
            run_sweep(p, pid)

        elif cmd in ["quit", "exit"]:
            print("[EXIT]")
            break

        else:
            print("[ERROR] Unknown command")

    except KeyboardInterrupt:
        print("\n[EXIT]")
        break