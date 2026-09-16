# Independent measurement campaign

Edit **campaign.json** for session metadata, plan, phase choices, detector settings and all readings.

Copy a shared `reading_templates` entry into the appropriate `measurements.STEP_ID` list for each repeat. Fill values, uncertainties, UTC, detector IDs and notes. No separate reading files or import step are required.

Use `acquire.py status`, then `build` or `freeze`. Complete actuator commands in `validation_schedule.csv` from independent phase calibration before bench scans. Freeze and predict before validation acquisition.

See [the acquisition protocol](../../../../python/field_propogation/docs/measurement_acquisition.md).
