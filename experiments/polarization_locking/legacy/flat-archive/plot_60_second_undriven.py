#!/usr/bin/env python3
"""Plot the nominally undriven interferometer's measured phase coordinates."""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


HERE = Path(__file__).resolve().parent
DATA = HERE / "60-second-undriven-0.csv"
OUTPUT = HERE / "60-second-undriven-static-instability.png"

data = np.genfromtxt(DATA, delimiter=",", names=True)
time_s = (data["pax_timestamp"] - data["pax_timestamp"][0]) / 1000

plt.rcParams.update(
    {
        "font.family": "sans-serif",
        "font.size": 10,
        "axes.linewidth": 0.8,
        "xtick.direction": "out",
        "ytick.direction": "out",
    }
)

fig, ax = plt.subplots(figsize=(7.0, 3.8), constrained_layout=True)
delta_u = data["u"] - data["u"][0]
delta_v = data["v"] - data["v"][0]
ax.plot(time_s, delta_u, color="#0072B2", linewidth=1.8, label=r"$\Delta u$")
ax.plot(time_s, delta_v, color="#D55E00", linewidth=1.8, label=r"$\Delta v$")

ax.set_title("Undriven interferometer instability", pad=10)
ax.set_xlabel("Elapsed time (s)")
ax.set_ylabel("Change in phase coordinate (rad)")
ax.set_xlim(0, 60)
ax.grid(axis="y", color="0.88", linewidth=0.7)
ax.spines[["top", "right"]].set_visible(False)
ax.legend(frameon=False, ncols=2, loc="best")

fig.savefig(OUTPUT, dpi=300, facecolor="white")
print(OUTPUT)
