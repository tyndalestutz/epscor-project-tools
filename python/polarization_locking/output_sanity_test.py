#!/usr/bin/env python3
from __future__ import annotations

import time
from typing import List

try:
    from .config import DEFAULT_CONFIG
    from .control import pax_to_sphere_angles
    from .pax_interface import PAXController, PAXReading
    from .rp_interface import RPController
except ImportError:  # pragma: no cover - support direct execution
    from config import DEFAULT_CONFIG
    from control import pax_to_sphere_angles
    from pax_interface import PAXController, PAXReading
    from rp_interface import RPController


def _summarize_readings(readings: list[PAXReading]) -> PAXReading:
    if not readings:
        raise ValueError("At least one reading is required")

    count = len(readings)
    return PAXReading(
        theta=sum(item.theta for item in readings) / count,
        eta=sum(item.eta for item in readings) / count,
        s1=sum(item.s1 for item in readings) / count,
        s2=sum(item.s2 for item in readings) / count,
        s3=sum(item.s3 for item in readings) / count,
        dop=sum(item.dop for item in readings) / count,
    )


def run_output_sanity_test(
    settle_s: float = 0.75,
    samples_per_step: int = 3,
    interval_s: float = 0.15,
    sweep_v1: List[float] | None = None,
    sweep_v2: List[float] | None = None,
) -> None:
    cfg = DEFAULT_CONFIG
    rp = RPController(cfg)
    pax = PAXController(cfg)

    if sweep_v1 is None:
        sweep_v1 = [i / 19.0 for i in range(20)]
    if sweep_v2 is None:
        sweep_v2 = [i / 19.0 for i in range(20)]

    print("Connecting to Red Pitaya and PAX...")
    rp.connect()
    pax.connect()

    try:
        rp.set_output_zero()
        time.sleep(0.5)

        print("Starting independent phi1/phi2 sweep")
        print("Format: stage | RP voltage pair | theta | eta | u | v | DOP")

        print("[phi1 sweep]")
        for v1 in sweep_v1:
            rp.set_output_voltage(v1, 0.0)
            time.sleep(settle_s)
            readings = [pax.read_polarization() for _ in range(samples_per_step)]
            summary = _summarize_readings(readings)
            sphere = pax_to_sphere_angles((summary.theta, summary.eta))
            print(f"[v1={v1:+.3f}, v2=+0.000]")
            print(
                f"  mean: theta={summary.theta:.5f} eta={summary.eta:.5f} "
                f"u={sphere.u:.5f} v={sphere.v:.5f} dop={summary.dop:.5f}"
            )
            for idx, reading in enumerate(readings):
                print(
                    f"  sample {idx+1:02d}: theta={reading.theta:.5f} eta={reading.eta:.5f} "
                    f"s1={reading.s1:.5f} s2={reading.s2:.5f} s3={reading.s3:.5f} dop={reading.dop:.5f}"
                )
                time.sleep(interval_s)

        print("[phi2 sweep]")
        for v2 in sweep_v2:
            rp.set_output_voltage(0.0, v2)
            time.sleep(settle_s)
            readings = [pax.read_polarization() for _ in range(samples_per_step)]
            summary = _summarize_readings(readings)
            sphere = pax_to_sphere_angles((summary.theta, summary.eta))
            print(f"[v1=+0.000, v2={v2:+.3f}]")
            print(
                f"  mean: theta={summary.theta:.5f} eta={summary.eta:.5f} "
                f"u={sphere.u:.5f} v={sphere.v:.5f} dop={summary.dop:.5f}"
            )
            for idx, reading in enumerate(readings):
                print(
                    f"  sample {idx+1:02d}: theta={reading.theta:.5f} eta={reading.eta:.5f} "
                    f"s1={reading.s1:.5f} s2={reading.s2:.5f} s3={reading.s3:.5f} dop={reading.dop:.5f}"
                )
                time.sleep(interval_s)

        rp.set_output_zero()
        print("Done.")
    finally:
        rp.set_output_zero()
        pax.disconnect()
        rp.disconnect()


if __name__ == "__main__":
    run_output_sanity_test()
