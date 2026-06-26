#!/usr/bin/env python3

import numpy as np
import matplotlib.pyplot as plt
from pyrpl import Pyrpl
import time

# ----------------------------
# CONFIGURATION
# ----------------------------

HOSTNAME = "192.168.1.98"
CONFIG_NAME = "scope_config-2"

GAIN_SCALE = 1
PID_P = 6.7 * GAIN_SCALE
PID_I = 7900 * GAIN_SCALE
SETPOINT = 0.6

F_START = 8e2
F_STOP = 50e3
POINTS = 501
RBW = 100
AVG_PER_POINT = 5
EXCITATION_AMP = 0.01

USE_DB = True
PLOT_RECONSTRUCTED_LOOP = True

# ----------------------------
# LOCK DETECTION CONFIGURATION
# ----------------------------

# Use the first input channel of the scope to monitor the photodiode
PD_LOCK_INPUT = "in1"

# Duration of each scope acquisition used to judge lock stability.
PD_SCOPE_DURATION = 0.05

# Decimation for the scope trace. 64 gives ~8 ms window for the Red Pitaya scope.
PD_SCOPE_DECIMATION = 64

# Threshold on the PD trace RMS after DC removal. Lower values mean more stable lock.
PD_LOCK_STABILITY_THRESHOLD = 0.02

# The PD signal must remain below the threshold for this many seconds before we consider the lock stable.
PD_LOCK_STABLE_TIME = 5.0

# Interval between lock stability checks.
PD_LOCK_CHECK_INTERVAL = 1.0

# Maximum total time to wait for the lock to become stable.
PD_LOCK_CHECK_TIMEOUT = 300.0


# ----------------------------
# SCOPE / LOCK-STATE HELPERS
# ----------------------------

def _get_scope(p):
    """Return the scope module from the Pyrpl instance."""
    return p.rp.scope


def _configure_scope_for_pd(p):
    """Temporarily configure the scope to acquire PD data.

    This saves the previous scope settings and restores them later.
    """
    scope = _get_scope(p)
    previous = {
        "input1": scope.input1,
        "duration": scope.duration,
        "decimation": scope.decimation,
    }
    scope.input1 = PD_LOCK_INPUT
    scope.duration = PD_SCOPE_DURATION
    scope.decimation = PD_SCOPE_DECIMATION
    return scope, previous


def _restore_scope(scope, previous):
    """Restore previously saved scope configuration."""
    for name, value in previous.items():
        setattr(scope, name, value)


def _get_scope_trace(scope, timeout=2.0):
    """Acquire a single scope trace and return the first channel as a NumPy array."""
    trace = scope.single(timeout=timeout)
    return np.asarray(trace[0], dtype=float)


def _pd_lock_metric(trace):
    """Compute a stability metric for the photodiode trace.

    The trace is centered by removing its mean, then the standard deviation
    is used as a simple measure of whether the PD signal is stable.
    """
    trace = np.asarray(trace, dtype=float)
    trace -= np.mean(trace)
    return np.std(trace)


def get_pd_lock_quality(p, timeout=2.0):
    """Return the photodiode lock stability score and the raw trace."""
    scope, previous = _configure_scope_for_pd(p)
    try:
        trace = _get_scope_trace(scope, timeout=timeout)
    finally:
        _restore_scope(scope, previous)
    score = _pd_lock_metric(trace)
    return score, trace


def wait_for_lock(
    p,
    stable_time=PD_LOCK_STABLE_TIME,
    threshold=PD_LOCK_STABILITY_THRESHOLD,
    interval=PD_LOCK_CHECK_INTERVAL,
    timeout=PD_LOCK_CHECK_TIMEOUT,
):
    """Wait until the photodiode signal remains stable for a defined interval.

    This function repeatedly acquires short scope traces and evaluates the
    PD stability score. Once the score stays below the threshold for
    stable_time seconds, the lock is considered stable.
    """
    stable_start = None
    start_time = time.time()

    while True:
        score, trace = get_pd_lock_quality(p, timeout=interval + 1.0)
        locked = score <= threshold
        current_time = time.time()

        if locked:
            if stable_start is None:
                stable_start = current_time
                print(
                    f"[LOCK] PD looks stable (score={score:.4g}). "
                    f"Verifying for {stable_time:.1f} s..."
                )
            else:
                elapsed = current_time - stable_start
                print(
                    f"[LOCK] stable for {elapsed:.1f}/{stable_time:.1f} s "
                    f"(score={score:.4g})"
                )
                if elapsed >= stable_time:
                    print(f"[LOCK] stable lock confirmed after {elapsed:.1f} s.")
                    return True, {"score": score, "trace": trace}
        else:
            if stable_start is not None:
                print(
                    f"[LOCK] lost stability (score={score:.4g} > {threshold}). "
                    "Resetting timer."
                )
            else:
                print(f"[LOCK] not locked yet (score={score:.4g} > {threshold}).")
            stable_start = None

        if timeout is not None and current_time - start_time >= timeout:
            print("[LOCK] timeout waiting for stable lock.")
            return False, {"score": score, "trace": trace}

        time.sleep(interval)


# ----------------------------
# TRANSFER FUNCTION CALCULATIONS
# ----------------------------

def compute_closed_loop_transfer_function(measured_cltf):
    """Return the measured closed-loop transfer function.

    In this script, the raw network analyzer data is interpreted as the
    closed-loop transfer function of the system, since the loop is active
    during the measurement.
    """
    return measured_cltf


def compute_open_loop_transfer_function(cltf):
    """Estimate the open-loop transfer function from the measured CLTF.

    For a unity-feedback loop,
        CLTF = L / (1 + L)
    where L is the open-loop transfer function.
    Solving for L gives:
        L = CLTF / (1 - CLTF)
    """
    return cltf / (1.0 - cltf)


def compute_plant_transfer_function(open_loop_tf, pid_tf):
    """Return the plant transfer function by dividing the open-loop TF by the PID TF."""
    return open_loop_tf / pid_tf


def bode_data(tf, use_db=True):
    """Convert a complex transfer function array into magnitude and phase for plotting."""
    magnitude = 20.0 * np.log10(np.abs(tf)) if use_db else np.abs(tf)
    phase = np.unwrap(np.angle(tf)) * 180.0 / np.pi
    return magnitude, phase


def plot_bode(freq, tf, title, color, use_db=True):
    """Plot a Bode diagram for a complex transfer function."""
    mag, phase = bode_data(tf, use_db)

    fig, axs = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
    axs[0].plot(freq, mag, color=color)
    axs[0].set_xscale("log")
    axs[0].set_ylabel("Magnitude (dB)" if use_db else "Magnitude")
    axs[0].set_title(title)
    axs[0].grid(True, which="both", ls="--", alpha=0.4)

    axs[1].plot(freq, phase, color=color)
    axs[1].set_xscale("log")
    axs[1].set_xlabel("Frequency (Hz)")
    axs[1].set_ylabel("Phase (deg)")
    axs[1].grid(True, which="both", ls="--", alpha=0.4)

    plt.tight_layout()
    plt.show()


def plot_transfer_functions(freq, cltf, oltf, plant_tf, pid_tf):
    """Plot closed-loop, open-loop, and plant transfer functions in separate figures."""
    plot_bode(freq, cltf, "Measured Closed-Loop Transfer Function (CLTF)", "C0", use_db=USE_DB)
    plot_bode(freq, oltf, "Estimated Open-Loop Transfer Function (OLTF)", "C1", use_db=USE_DB)
    if PLOT_RECONSTRUCTED_LOOP:
        plot_bode(freq, plant_tf, "Estimated Plant Transfer Function (OLTF / PID)", "C2", use_db=USE_DB)


# ----------------------------
# NETWORK ANALYZER SWEEP
# ----------------------------

def run_sweep(p, pid):
    """Run the sweep only after the photodiode lock state is confirmed stable."""
    if pid.paused:
        print("[ERROR] Loop is unlocked. Refusing to run sweep.")
        return

    confirm = input("Type 'yes' to run sweep: ").strip().lower()
    if confirm != "yes":
        print("[ABORTED]")
        return

    print("[LOCK] Checking photodiode lock state before sweeping...")
    locked, lock_info = wait_for_lock(p)
    if not locked:
        print("[ERROR] Sweep aborted because stable lock could not be confirmed.")
        return lock_info

    print("[NA] Running sweep...")
    na = p.networkanalyzer
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
        trace_average=1,
    )

    t0 = time.time()
    data = na.single()
    t1 = time.time()
    print(f"[NA] Sweep completed in {t1 - t0:.2f} s")

    freq = na.frequencies
    pid_tf = pid.transfer_function(freq)

    # Interpret the measured NA data as the closed-loop transfer function.
    cltf = compute_closed_loop_transfer_function(data)
    oltf = compute_open_loop_transfer_function(cltf)
    plant_tf = compute_plant_transfer_function(oltf, pid_tf)

    plot_transfer_functions(freq, cltf, oltf, plant_tf, pid_tf)

    return {
        "frequency": freq,
        "cltf": cltf,
        "oltf": oltf,
        "plant_tf": plant_tf,
        "pid_transfer_function": pid_tf,
        "lock_info": lock_info,
    }


# ----------------------------
# PID CONTROL FUNCTIONS
# ----------------------------

def relock(pid):
    """Engage the PID loop with configured P/I gains and setpoint."""
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
    """Disable the PID loop and leave the controller in open-loop."""
    print("[LOCK] unlocking...")
    pid.pause_gains = 'i'
    pid.paused = True
    pid.ival = 0.0
    return pid


def reset(pid):
    """Reset the PID loop by unlocking and then re-engaging it."""
    print("[LOCK] reset...")
    unlock(pid)
    time.sleep(0.5)
    relock(pid)


def status(pid):
    """Print the current PID parameters and lock state."""
    print("\n--- PID STATUS ---")
    print(f"P: {pid.p}")
    print(f"I: {pid.i}")
    print(f"Setpoint: {pid.setpoint}")
    print(f"Paused: {pid.paused}")
    print(f"I term: {pid.ival}")
    print("------------------\n")


# ----------------------------
# MAIN
# ----------------------------

def main():
    print("[INIT] Connecting to Red Pitaya...")
    p = Pyrpl(hostname=HOSTNAME, config=CONFIG_NAME)
    pid = p.rp.pid0

    pid.input = "in1"
    pid.output_direct = "out1"

    relock(pid)

    print(
        """
=== LOCK + NA CLI ===
Commands:
  relock | unlock | reset | status
  run    | quit
=====================
"""
    )

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


if __name__ == "__main__":
    main()
