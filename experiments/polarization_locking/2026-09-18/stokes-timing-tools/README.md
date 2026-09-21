# Archived Stokes/timing development tools

These three scripts were moved out of `python/polarization_locking/` during the
suite cleanup. Their bytes are unchanged; `archive.json` records original paths
and SHA-256 digests. They are historical development tools, not supported suite
entry points or an additional SOP. They **drive RP outputs** and use provisional
calibration/timing assumptions.

| Script | Original purpose |
| --- | --- |
| `live_stokes_plot.py` | Live S1/S2/S3 plotting against reconstructed drive voltage |
| `live_stokes_diagnostic.py` | Exploratory phase-plane, phase and timing displays |
| `acquire_stokes_timing_test.py` | Dynamic loopback followed by held steps with independent PAX/IN1 times |

The [session index](../README.md) distinguishes recordings and missing metadata.
CSV fields named estimated/reconstructed voltage are not independent terminal
voltage measurements. The scripts do not meet the current common recipe/status/
report contract. In particular, the timing script can label an interrupted run
completed, so a historical status alone does not prove complete acquisition.

For source inspection, the original fallback imports still resolve with:

```bash
PYTHONPATH=python/polarization_locking python experiments/polarization_locking/2026-09-18/stokes-timing-tools/acquire_stokes_timing_test.py --help
```

That command displays help only. Omitting `--help` performs acquisition and
drives outputs. A present-day execution would use present-day shared adapters;
this archive is not a complete environment snapshot. For new colleague-facing
measurements, use the [suite pipelines](../../../../python/polarization_locking/docs/test-guide.md).
