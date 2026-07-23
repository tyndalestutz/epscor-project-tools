import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

# ------------------------------------------------------------------
# Load CSV
# ------------------------------------------------------------------
csv_file = "EOM-00_20260605_202127-vna_sweep.csv"

df = pd.read_csv(csv_file)

freq = df["frequency_Hz"].to_numpy()

# Reconstruct open-loop transfer function from CSV
oltf = (
    df["oltf_real"].to_numpy()
    + 1j * df["oltf_imag"].to_numpy()
)

# ------------------------------------------------------------------
# Define your PID object here
# (Replace this with however you create it in your notebook.)
# ------------------------------------------------------------------
# Example:
# pid = PID(...)
#
# The only requirement is that this returns the PID transfer function
# evaluated at the frequencies in `freq`.
pid_tf = pid.transfer_function(freq)

# ------------------------------------------------------------------
# Loop Gain
# ------------------------------------------------------------------
loop_gain = oltf * pid_tf

# ------------------------------------------------------------------
# Plotting function (identical to notebook)
# ------------------------------------------------------------------
def plot_transfer_function(freq, tf, title, label, color):

    mag_db = 20 * np.log10(np.abs(tf))

    phase_deg = np.unwrap(np.angle(tf)) * 180 / np.pi
    phase_deg -= phase_deg[0]

    fig, axs = plt.subplots(
        2, 1,
        figsize=(10, 7),
        sharex=True
    )

    axs[0].plot(freq, mag_db, color=color, label=label)
    axs[0].set_xscale("log")
    axs[0].set_ylabel("Magnitude (dB)")
    axs[0].set_title(title)
    axs[0].grid(True, which="both", ls="--", alpha=0.4)
    axs[0].legend()

    axs[1].plot(freq, phase_deg, color=color, label=label)
    axs[1].set_xscale("log")
    axs[1].set_xlabel("Frequency (Hz)")
    axs[1].set_ylabel("Phase (deg)")
    axs[1].grid(True, which="both", ls="--", alpha=0.4)
    axs[1].legend()

    fig.tight_layout()

    return fig

# ------------------------------------------------------------------
# Plot Loop Gain
# ------------------------------------------------------------------
fig_L = plot_transfer_function(
    freq,
    loop_gain,
    title="Loop Gain $G(s)H(s)$",
    label="L(s) = G(s)H(s)",
    color="purple"
)

plt.show()