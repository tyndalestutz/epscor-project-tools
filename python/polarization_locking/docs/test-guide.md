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
`pd-visibility` defaults to passive mode, which leaves output state unchanged.
Select PD, PAX or both, then active/passive outside the parameter table; active
mode drives the selected phase output and zeros outputs on exit.
Path-condition durations are per condition. Calibration/settling add time.

Every menu run attempts a PDF and keeps recipe/status/log artifacts. A complete
report may still describe invalid physics; specialized analysis can be partial.
Exception: `pax-live` connects only PAX and logs temporarily; Stop/close offers
Save (retain and report) or Discard (remove this run). Crashes retain recovery data.
CSV fields depend on the measurement. `rough` reports through its log instead
of a measurement CSV. Report regeneration is offline.

## Monitoring and contrast

| Test key | Purpose / action | Required setup |
| --- | --- | --- |
| `live` | Log raw PAX angles, Stokes, DOP and u/v until Ctrl+C. No actuator scan; RP outputs are initialized to zero. | PAX at the location recorded in bench_pax_location. |
| `pax-live` | Large numeric alignment panel, small recent deltas, every fresh PAX record logged. Optional Orthogonalizer freezes one arm and targets its antipodal Stokes direction. Save/Discard on stop. No RP connection or output changes. | PAX at recorded location/wavelength; Qt desktop. See [launch and logging](../README.md#live-manual-alignment). |
| `pd-visibility` | Contrast from PD, PAX or both. Choose passive or active; active actuator/waveform settings remain in the table. | Drive spans fringes. PD: DC coupling and signed dark baseline. PAX: recorded port/wavelength. Both: simultaneous optical delivery. See [contrast procedure](visibility.md). |

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

## Repeated power/Stokes acquisition

`stokes-phase-sweep` measures optical power and polarization while OUT1 drives
phi1 with a continuous sine. Put PAX in path A / E4 observing the combined
field, and physically T OUT1 into IN1. IN1 is a literal voltage reference, not
a photodiode; no ND correction or theoretical phase conversion is applied.
OUT2 remains at zero and outputs are returned to zero on exit.

```bash
python python/polarization_locking/lock.py --show stokes-phase-sweep
python python/polarization_locking/lock.py --profile python/polarization_locking/profiles/stokes-phase-sweep-example.json --dry-run
python python/polarization_locking/lock.py --profile python/polarization_locking/profiles/stokes-phase-sweep-example.json --run stokes-phase-sweep
```

Defaults: 60 s (30 electrical cycles), 0.5 Hz, 0.3875 V amplitude and 0.3875 V
offset (0–0.775 V). These are explicit electrical commands, not a claim of a
calibrated 2π optical excursion. Configure `stokes_phase_sweep_*` in the recipe
or menu and `duration_s` in run options. Nominal polling is 0.05 s, matching the
existing driven PAX tests; actual cadence is limited by fresh PAX records and
scope acquisition. No cycle averaging or fitting is performed.

The shared PAX daemon retains its five-second startup wait. This test also uses
the shared `read_fresh_polarization()` adapter: initialization, finite values,
ADC validity and advancement of both timestamp and revision are required.
Readiness is established before starting the sine and acquisition clock.
An unavailable/stalled PAX fails the run after the configured freshness wait;
already flushed rows are retained. Low DOP and small raw DOP excursions above
one are retained rather than filtered for polarization purity.

`data.csv` has one row per fresh PAX reading paired with the immediately
following short IN1 scope capture. `in1_reference_v` is the capture mean in
volts; standard deviation, extrema, count, scope timing/decimation and FPGA
averaging state are also retained. `pd_scope_*` settings control this existing
scope helper; the input is always IN1 regardless of `pd_input`. Each short
capture remains a separate row; no averaging across PAX measurements occurs.
`out1_command_estimated_v` is the software sine estimate, not measured voltage
or hardware phase readback. Actual ASG frequency/amplitude/offset are logged.

`elapsed_s` is the midpoint of the host IN1 capture call; `utc` is row completion.
`in1_started_s`, `in1_finished_s`, `pax_requested_s`, and `pax_received_s`
record separate acquisition windows. These are sequential paired snapshots,
not hardware-trigger-synchronized measurements; the PAX integration window and
device clock offset are not calibrated. Preserve these times and PAX counters
for later alignment, particularly when comparing voltage-dependent trajectories.

As elsewhere in this suite, `s1,s2,s3` are unit-norm directions derived from
PAX theta/eta. Additional `s1_over_s0,s2_over_s0,s3_over_s0` equal DOP times
that direction, providing total-power-normalized Stokes for coherency analysis.
Raw theta, eta, DOP, power and the usual PAX diagnostic fields remain in the CSV.

The standard dated run folder contains `data.csv`, `recipe.json`, `run.json`,
`console.log`, and `report.pdf`. The report shows voltage, power, Stokes, DOP,
and angles versus time plus standard metadata. Visibility/model analysis stays
offline: the existing visibility estimator assumes dense uniform scope traces,
not irregular PAX snapshots. Regenerate the PDF without hardware:

```bash
PYTHONPATH=python python -m polarization_locking.reports.run_report path/to/run-directory
```

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
