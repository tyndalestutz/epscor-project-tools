#!/usr/bin/env python3

import numpy as np
import matplotlib.pyplot as plt
from scipy.signal import find_peaks, peak_widths
from scipy.ndimage import gaussian_filter1d
import argparse
from pathlib import Path


# ---------------------------------------------------
# Utilities
# ---------------------------------------------------

def db(x):
    return 20*np.log10(np.abs(x))


def phase(x):
    return np.unwrap(np.angle(x))*180/np.pi


def find_bandwidth(freq, mag_db):
    """
    Find first -3 dB point relative to low frequency response.
    """
    reference = mag_db[0]
    target = reference - 3

    idx = np.where(mag_db < target)[0]

    if len(idx):
        return freq[idx[0]]
    return None


def find_resonances(freq, mag_db):
    """
    Find significant peaks.
    """
    peaks, props = find_peaks(
        mag_db,
        prominence=1
    )

    results = []

    if len(peaks):
        widths, _, left, right = peak_widths(
            mag_db,
            peaks,
            rel_height=0.5
        )

        for p, l, r in zip(peaks, left, right):

            f0 = freq[p]

            f1 = freq[int(l)]
            f2 = freq[int(r)]

            bw = f2 - f1

            Q = f0/bw if bw > 0 else np.inf

            results.append({
                "freq": f0,
                "height": mag_db[p],
                "Q": Q
            })

    return results


def find_slopes(freq, mag_db):
    """
    dB per decade.
    """

    logf = np.log10(freq)

    slope = np.gradient(
        mag_db,
        logf
    )

    smooth = gaussian_filter1d(
        slope,
        sigma=5
    )

    return smooth


def find_poles(freq, slope):
    """
    Look for slope transitions.
    A new pole roughly introduces -20 dB/dec.
    """

    deriv = np.gradient(slope)

    peaks, _ = find_peaks(
        np.abs(deriv),
        prominence=5
    )

    return freq[peaks]


# ---------------------------------------------------
# Analysis
# ---------------------------------------------------

def analyze(name, tf, freq):

    mag = db(tf)
    ph = phase(tf)
    slopes = find_slopes(freq, mag)

    bandwidth = find_bandwidth(freq, mag)

    resonances = find_resonances(freq, mag)

    poles = find_poles(freq, slopes)

    return {
        "name": name,
        "mag": mag,
        "phase": ph,
        "slope": slopes,
        "bandwidth": bandwidth,
        "resonances": resonances,
        "poles": poles
    }


# ---------------------------------------------------
# Plotting
# ---------------------------------------------------

def bode_plot(freq, results, filename):

    fig, ax = plt.subplots(
        2,
        1,
        figsize=(10, 8),
        sharex=True
    )

    ax[0].semilogx(
        freq,
        results["mag"],
        lw=2
    )

    ax[0].set_ylabel("Magnitude (dB)")
    ax[0].grid(True, which="both")

    if results["bandwidth"]:
        ax[0].axvline(
            results["bandwidth"],
            color="r",
            ls="--",
            label=f"-3 dB: {results['bandwidth']:.1f} Hz"
        )

    for r in results["resonances"]:
        ax[0].plot(
            r["freq"],
            r["height"],
            "*",
            ms=12
        )

    ax[0].legend()


    ax[1].semilogx(
        freq,
        results["phase"],
        lw=2
    )

    ax[1].set_xlabel("Frequency (Hz)")
    ax[1].set_ylabel("Phase (deg)")
    ax[1].grid(True, which="both")

    fig.suptitle(results["name"])

    fig.tight_layout()

    fig.savefig(
        filename,
        dpi=300
    )

    return fig


# ---------------------------------------------------
# Report
# ---------------------------------------------------

def print_report(results):

    print("\n" + "="*60)
    print(results["name"])
    print("="*60)

    if results["bandwidth"]:
        print(
            f"\n-3 dB bandwidth:"
            f" {results['bandwidth']:.1f} Hz"
        )

    else:
        print(
            "\nNo -3 dB point found"
        )

    print("\nCandidate poles:")

    if len(results["poles"]):
        for f in results["poles"]:

            tau = 1/(2*np.pi*f)

            print(
                f"  {f:10.1f} Hz"
                f"   tau = {tau*1e6:.2f} us"
            )

    else:
        print("  none detected")

    print("\nResonances:")

    if len(results["resonances"]):

        for r in results["resonances"]:

            print(
                f"  {r['freq']:10.1f} Hz"
                f"  Q≈{r['Q']:.2f}"
                f"  Peak={r['height']:.2f} dB"
            )

    else:
        print("  none detected")

    print("="*60)


# ---------------------------------------------------
# Main
# ---------------------------------------------------

def main():

    parser = argparse.ArgumentParser(
        description="Analyze VNA sweep CSV"
    )

    parser.add_argument(
        "file",
        help="CSV file"
    )

    args = parser.parse_args()

    data = np.genfromtxt(
        args.file,
        delimiter=",",
        names=True
    )

    freq = data["frequency_Hz"]

    cltf = (
        data["cltf_real"]
        +
        1j*data["cltf_imag"]
    )

    oltf = (
        data["oltf_real"]
        +
        1j*data["oltf_imag"]
    )


    stem = Path(args.file).stem


    for name, tf in [
        ("Closed Loop Transfer Function", cltf),
        ("Open Loop Plant Transfer Function", oltf)
    ]:

        result = analyze(
            name,
            tf,
            freq
        )

        print_report(result)

        outfile = (
            stem
            +
            "_"
            +
            name.lower()
            .replace(" ", "_")
            +
            ".png"
        )

        bode_plot(
            freq,
            result,
            outfile
        )

        print(
            f"Saved {outfile}\n"
        )


if __name__ == "__main__":
    main()