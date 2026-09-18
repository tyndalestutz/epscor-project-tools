# Independent measurement campaign

Edit **campaign.json** for session metadata, plan, phase choices, detector settings and all readings.

Copy a shared `reading_templates` entry into the appropriate `measurements.STEP_ID` list for each repeat. Fill values, uncertainties, UTC, detector IDs and notes. No separate reading files or import step are required.

Use `acquire.py status`, then `build` or `freeze`. Complete actuator commands in `validation_schedule.csv` from independent phase calibration before bench scans. Freeze and predict before validation acquisition.

See [the acquisition protocol](../../../../python/field_propogation/docs/measurement_acquisition.md).

The `path_power_observations` section contains the direct-meter route powers
provided on 2026-09-16, with missing metadata left explicitly unknown. These are
preliminary constraints, not independently reconstructed Jones coefficients.
Inspect their split fractions and downstream power sums with:

```bash
python python/field_propogation/acquire.py inspect-powers experiments/field_propagation/measurement_campaign/2026-09-10
```

The campaign directory date is its creation date, not the acquisition date of
these readings (which was not supplied).
