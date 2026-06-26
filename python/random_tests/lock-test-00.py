import numpy as np
import matplotlib.pyplot as plt
from pyrpl import Pyrpl, logging
import time

# ----------------------------
# CONFIGURATION
# ----------------------------

HOSTNAME = "192.168.1.98"
CONFIG_NAME = "scope_config"

# PID parameters (scaled)
GAIN_SCALE = 1
PID_P = 6.7 * GAIN_SCALE
PID_I = 7900 * GAIN_SCALE
SETPOINT = 0.6

# Network analyzer settings
F_START = 1e3
F_STOP = 30e3
POINTS = 501
RBW = 100
AVG_PER_POINT = 3
EXCITATION_AMP = 0.01

USE_DB = True
PLOT_RECONSTRUCTED_LOOP = False  # IMPORTANT: set True if you compute loop TF

# ----------------------------
# UTILITY FUNCTIONS (PID CONTROL)
# ----------------------------

def relock(pid, P, I, setpoint):
    pid.pause_gains = 'i'
    pid.paused = True
    pid.ival = 0.0

    pid.p = P
    pid.i = I
    pid.setpoint = setpoint

    pid.paused = False


def unlock(pid):
    pid.pause_gains = 'i'
    pid.paused = True
    pid.ival = 0.0


def reset(pid, P, I, setpoint):
    unlock(pid)
    time.sleep(0.5)
    relock(pid, P, I, setpoint)


# ----------------------------
# CONNECT TO RED PITAYA
# ----------------------------

print("Connecting to Red Pitaya...")

p = Pyrpl(hostname=HOSTNAME, config=CONFIG_NAME)
pid = p.rp.pid0

pid.input = "in1"
pid.output_direct = "out1"

relock(pid, PID_P, PID_I, SETPOINT)

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
    trace_average=1
)

# ----------------------------
# ACQUIRE DATA
# ----------------------------

print("Running network analyzer sweep...")

t0 = time.time()
data = na.single()
t1 = time.time()

print(f"Sweep completed in {t1 - t0:.2f} s")

freq = na.frequencies

# Optional: PID transfer function (for loop reconstruction)
pid_tf = pid.transfer_function(freq)

# ----------------------------
# TRANSFER FUNCTION PROCESSING
# ----------------------------

if PLOT_RECONSTRUCTED_LOOP:
    # Open-loop estimate (ONLY valid if your measurement supports it)
    tf = data * pid_tf
    plot_title = "Open-Loop Transfer Function (Estimated)"
else:
    tf = data
    plot_title = "Measured System Transfer Function (EOM)"

mag = 20 * np.log10(np.abs(tf)) if USE_DB else np.abs(tf)
phase = np.unwrap(np.angle(tf)) * 180 / np.pi

# normalize phase for readability
phase = phase - phase[0]

# ----------------------------
# PLOTTING
# ----------------------------

fig, axs = plt.subplots(2, 1, figsize=(10, 7), sharex=True)

# Magnitude
axs[0].plot(freq, mag, color="red")
axs[0].set_xscale("log")
axs[0].set_ylabel("Magnitude (dB)" if USE_DB else "Magnitude")
axs[0].set_title(plot_title)
axs[0].grid(True, which="both", ls="--", alpha=0.4)

# Phase
axs[1].plot(freq, phase, color="blue")
axs[1].set_xscale("log")
axs[1].set_xlabel("Frequency (Hz)")
axs[1].set_ylabel("Phase (deg)")
axs[1].grid(True, which="both", ls="--", alpha=0.4)

plt.tight_layout()
plt.show()