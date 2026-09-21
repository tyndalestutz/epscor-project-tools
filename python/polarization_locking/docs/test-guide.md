# Test and pipeline guide

Use the [operating procedure](operating-procedure.md) for every bench run.
The catalog in `catalog.py` supplies menu descriptions, setup requirements,
parameters, and dispatch. Inspect current values with `--show TEST`; use named
recipes for shared procedures. All listed tests use the standard run lifecycle.

## Choose a pipeline

| Question | Pipeline | Review before the next step |
| --- | --- | --- |
| What is PD contrast under an external drive? | Record coupling/dark level/active outputs → `pd-visibility` → inspect traces and validity | Full fringe coverage, baseline, noise and bandwidth; apparent contrast is not calibrated visibility |
| What does an uncalibrated actuator do? | Record voltage chain → bounded `sweep` → optionally specialist raw calibration → review repeated forward/reverse response | Measured electrical gain, placement, settling, drift and phase-model validity before adopting Vλ |
| Do paths or axes interact? | Verified gains/limits → `pax-path-hold` or D-port isolation → `power-balance` / `cross-sweep` / field maps | Each manual path condition recorded; distinguish PD signal from PAX power |
| Can a local lock hold the chosen state? | Appropriate placement and calibrated response → `single-axis-pid` or `phi1-lock-test` → FPGA PD lock → hybrid/gain comparison | Slope sign, useful local branch, actuator headroom, residual error and DOP before increasing complexity |
| Can both axes track the target? | Verified voltage-to-phase mapping → `rough` if appropriate → `pid-test`; `pid-live` only adds the display | Target convention, branch/pole handling, residuals and output saturation |

These are suggested staged workflows, not one automatic pipeline. Each step
creates its own run and needs a recorded interpretation. Do not proceed just
because the previous command returned `completed`.

## Shared output behavior

Every ordinary test below connects both RP and PAX, initializes RP outputs to
zero, and attempts zero on exit, even when its name includes “monitor” or “hold”.
`pd-visibility` alone leaves output state unchanged and never connects PAX.
Path-condition durations are per condition. Calibration/settling add time.

Every menu run attempts a PDF and keeps recipe/status/log artifacts. A complete
report may still describe invalid physics; specialized analysis can be partial.
CSV fields depend on the measurement. `rough` reports through its log instead
of a measurement CSV. Report regeneration is offline.

## Monitoring and passive visibility

| Test key | Purpose / action | Required setup |
| --- | --- | --- |
| `live` | Log raw PAX angles, Stokes, DOP and u/v until Ctrl+C. No actuator scan; RP outputs are initialized to zero. | PAX at the location recorded in bench_pax_location. |
| `pd-visibility` | Measure fringe contrast on the selected PD input under external drive. No RP output initialization or PAX connection. | DC-coupled PD on the selected RP input; external drive must span complete fringes. Set its frequency and, when known, the blocked-light voltage offset. RP must already have the Pyrpl FPGA loaded. |

## Actuator response and coupling

| Test key | Purpose / action | Required setup |
| --- | --- | --- |
| `sweep` | Step one output through an explicit RP voltage range; hold the other at zero. Log PAX response. This collects data; it does not fit V_pi. | PAX must observe the selected actuator's optical response; record its location. |
| `bidirectional-sweep` | Sweep one configured V_lambda in both directions with the other actuator at a fixed bias; compare hysteresis. | PAX at final output; both paths open unless prompted. |
| `cross-sweep` | Sweep one V_lambda at each fixed-axis bias across the other V_lambda. | PAX at final output; both paths open unless prompted. |
| `diagnostic-suite` | Record baseline and held fractions of each V_lambda to distinguish drift from motion effects. | PAX at final output; both paths open unless prompted. |
| `intensity-diagnostic` | Sweep each axis independently; record PAX and photodiode mean/noise with ND correction. | PAX at final output; both paths open unless prompted. Photodiode connected to configured RP input. |
| `phi1-step-map` | Acquire settled forward/reverse voltage steps for phase-slope and hysteresis analysis. | PAX at first-NPBS D port (requires moving it from the current C position). Photodiode at final F on the configured RP input. |
| `phi1-fringe-map` | Forward/reverse phi1 biases with a phi2 sweep at each bias; log both PAX and PD. | PAX at final output; both paths open unless prompted. Photodiode connected to configured RP input. |
| `field-model-calibration` | Sweep phi2 at phi1 biases for A, B and both paths, repeating in reverse order to bracket drift. | PAX at final output; both paths open unless prompted. Photodiode connected to configured RP input. |

## Path balance and optical isolation

| Test key | Purpose / action | Required setup |
| --- | --- | --- |
| `phi2-path-test` | Sweep phi2 for A only, B only and both paths; pause for manual beam-block changes. | PAX at final output; both paths open unless prompted. Photodiode connected to configured RP input. |
| `pax-path-hold` | Hold both outputs at zero and record PAX/PD for each manual path condition. Duration is per condition. | PAX at final output; both paths open unless prompted. Photodiode connected to configured RP input. |
| `power-balance` | Drive a bounded phi2 sine over one V_lambda for each manual path condition. Duration is per condition. | PAX at final output; both paths open unless prompted. Photodiode connected to configured RP input. |
| `first-npbs-d-test` | Compare static polarization with a phi1 sine at first NPBS D. | PAX at first-NPBS D port (requires moving it from the current C position). Photodiode at final F on the configured RP input. |
| `first-npbs-d-isolation` | Measure static polarization for manual blocked-path conditions. | PAX at first-NPBS D port (requires moving it from the current C position). Photodiode at final F on the configured RP input. |
| `d-polarizer-phi1-test` | Compare raw D-port polarization and final-F photodiode response with a linear polarizer in C. | PAX at first-NPBS D port (requires moving it from the current C position). No polarizer before PAX. Linear polarizer in C before final NPBS; PD at final F; both paths open. |

## Feedback diagnostics

| Test key | Purpose / action | Required setup |
| --- | --- | --- |
| `single-axis-pid` | Calibrate one axis and hold its midpoint coordinate with PI; the other output stays zero. DOP is logged without gating feedback. | PAX at final output; both paths open unless prompted. |
| `phi1-lock-test` | Use settled steps to estimate slope, then hold a local PAX target. DOP is logged without gating feedback. | PAX at first-NPBS D port (requires moving it from the current C position). |
| `phi1-pd-lock-test` | Calibrate the local PD slope and hold a fringe branch with the RP FPGA PID. | PAX at final output; both paths open unless prompted. Photodiode connected to configured RP input. |
| `phi1-pd-hybrid-test` | Run the FPGA photodiode loop with slow PAX correction of the PD setpoint. | PAX at final output; both paths open unless prompted. Photodiode connected to configured RP input. |
| `phi1-pd-gain-scan` | Hold each configured gain on the same fringe branch and log PAX validation. | PAX at final output; both paths open unless prompted. Photodiode connected to configured RP input. |
| `pid-test` | Apply a rough correction followed by bounded PI feedback. DOP is logged without gating feedback; current-target capture uses the DOP gate. | PAX at final output; both paths open unless prompted. |
| `pid-live` | Run the two-axis PI experiment with an interactive Poincare sphere. Requires PyVista; DOP behavior matches the two-axis PI hold. | PAX at final output; both paths open unless prompted. |
| `rough` | Measure, make one bounded target correction, settle, and report the residual. Uses the DOP gate; summary goes to the run log. | PAX at final output; both paths open unless prompted. |

## Specialist and historical tools

[Raw phi1 calibration](raw-calibration.md) is a separate collector with independent
PAX records, command readbacks, and scope archives. It does not use menu recipes;
its `run.json` parameters define the command to reconstruct. Use its dedicated
report command. It deliberately does not infer a fitted Vπ or electrical gain.

September 18 live-Stokes and timing tools are archived with the
[session data](../../../experiments/polarization_locking/2026-09-18/README.md).
They use different schemas, inferred timing/voltage quantities, and provisional
calibrations. They are not additional supported menu tests. The archive preserves
provenance without making those shortcuts the standard colleague workflow.

Coordinate conventions and previous controller candidates remain in
[historical bench notes](bench_reference.md). Independent Jones-field modeling
and measurement validation live in
[the field-propagation project](../../field_propogation/README.md).
