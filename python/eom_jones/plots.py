"""Raw looks for the EOM polarization-transfer notebook. Read from files; no processing.

s1, s2, s3 here are the PAX's own unit direction, cos2η·cos2θ, cos2η·sin2θ, sin2η (as in instruments.pax);
DoP and power are plotted separately and are not folded in.
"""
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

from .acquire import load_stage


def unit_stokes(arrays):
    theta, eta = arrays["theta"], arrays["eta"]
    return np.cos(2 * eta) * np.cos(2 * theta), np.cos(2 * eta) * np.sin(2 * theta), np.sin(2 * eta)


def _time_axis(arrays, record):
    return arrays["received_monotonic_s"] - record["start"]["monotonic_s"]


def raw_look_capture(block, stage):
    """One undriven capture: s1-s3, DoP and power vs host time."""
    block = Path(block)
    a, rec = load_stage(block, stage)
    t = _time_axis(a, rec)
    fig, (top, mid, bot) = plt.subplots(3, 1, figsize=(9, 7), sharex=True)
    for s, name in zip(unit_stokes(a), ("s1", "s2", "s3")):
        top.plot(t, s, ".", ms=3, label=name)
    top.set(ylabel="PAX unit Stokes", ylim=(-1.05, 1.05)); top.legend(fontsize=8, ncol=3)
    mid.plot(t, a["dop"], ".", ms=3, color="k"); mid.set(ylabel="DoP (PAX)")
    bot.plot(t, a["ptotal"] * 1e6, ".", ms=3, color="C3"); bot.set(xlabel="Host time since stage start (s)",
                                                                   ylabel="ptotal (µW, repo convention)")
    fig.suptitle(f"{block.name}/{stage}: raw PAX records ({len(t)} accepted reads, status {rec['status']})")
    plt.tight_layout(); plt.show()


def raw_look_sweep(block):
    """sweep_B: s1-s3 vs host time; DoP, power and ADC max vs time; s vs command for segment 0 only.

    The command axis is INFERRED: V(t) = offset + peak·sin(2πf(t − t_set)), t_set = return time of set_waveform.
    The sine really starts a few RPC calls earlier, and no lag is applied, so a gap between the rising and
    falling halves here is NOT a measurement of lag or hysteresis. Later segments (set_frequency) have an
    unknown phase and are shown against time only.
    """
    block = Path(block)
    a, rec = load_stage(block, "sweep_B")
    t = _time_axis(a, rec)
    s = unit_stokes(a)
    fig, axes = plt.subplots(4, 1, figsize=(10, 13))
    for comp, name in zip(s, ("s1", "s2", "s3")):
        axes[0].plot(t, comp, ".", ms=2, label=name)
    for i, command in enumerate(rec["commands"][1:], start=1):
        axes[0].axvline(command["received_monotonic_s"] - rec["start"]["monotonic_s"], color="0.5", lw=0.8)
    axes[0].set(xlabel="Host time since stage start (s)", ylabel="PAX unit Stokes", ylim=(-1.05, 1.05),
                title="grey line: frequency change (segment boundary)")
    axes[0].legend(fontsize=8, ncol=3)
    axes[1].plot(t, a["dop"], ".", ms=2, color="k")
    twin = axes[1].twinx()
    twin.plot(t, a["ptotal"] * 1e6, ".", ms=2, color="C3")
    axes[1].set(xlabel="Host time since stage start (s)", ylabel="DoP (PAX, black)")
    twin.set_ylabel("ptotal (µW, red)")
    axes[2].plot(t, a["adc_max"], ".", ms=2, color="C4")
    axes[2].axhline(65520, color="C3", lw=0.8, ls=":")
    axes[2].set(xlabel="Host time since stage start (s)", ylabel="PAX adc_max (counts)",
                title="dotted: 65520 ceiling (read_fresh drops reads at or above it)")
    if rec["commands"]:
        command, sel = rec["commands"][0], a["segment"] == 0
        f = command["actual"]["frequency_hz"]
        volts = rec["offset_v"] + rec["peak_v"] * np.sin(
            2 * np.pi * f * (a["received_monotonic_s"][sel] - command["received_monotonic_s"]))
        for comp, name in zip(s, ("s1", "s2", "s3")):
            axes[3].plot(volts, comp[sel], ".", ms=2, label=name)
        axes[3].set(xlabel="Command (V), INFERRED from the sine model: approximate phase, no lag applied",
                    ylabel="PAX unit Stokes", ylim=(-1.05, 1.05),
                    title=f"segment 0: f = {f:.6g} Hz (RP readback), {sel.sum()} reads")
        axes[3].legend(fontsize=8, ncol=3)
    fig.suptitle(f"{block.name}/sweep_B: raw PAX records (status {rec['status']})")
    plt.tight_layout(); plt.show()


def sphere(block, stages):
    """Raw unit directions of the named stages on the Poincaré sphere (on demand)."""
    block = Path(block)
    fig = plt.figure(figsize=(6, 6))
    ax = fig.add_subplot(projection="3d")
    u, v = np.mgrid[0:2 * np.pi:40j, 0:np.pi:20j]
    ax.plot_wireframe(np.cos(u) * np.sin(v), np.sin(u) * np.sin(v), np.cos(v), color="0.85", lw=0.4)
    for i, stage in enumerate(stages):
        a, _ = load_stage(block, stage)
        ax.scatter(*unit_stokes(a), s=3, color=f"C{i}", label=stage)
    ax.set(xlabel="s1", ylabel="s2", zlabel="s3"); ax.set_box_aspect((1, 1, 1))
    ax.legend(fontsize=8); ax.set_title(f"{block.name}: raw PAX directions")
    plt.show()
