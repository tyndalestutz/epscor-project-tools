# Polarization-locking test suite

Bench measurements and locking diagnostics for the Red Pitaya (RP), PAX
polarimeter, and photodiode (PD). Use the menu to select a test, review its
setup, save a recipe, and acquire one labelled run. Automated software tests
are in `tests/`; they do not connect instruments.

| Start here | Purpose |
| --- | --- |
| [Operating procedure](docs/operating-procedure.md) | Prepare, run, check quality, repeat, and hand over |
| [Test and pipeline guide](docs/test-guide.md) | Choose a test and understand what its result establishes |
| [PD/PAX contrast](docs/visibility.md) | PD, PAX or both; passive acquisition or active actuator drive |
| [Recipe examples](profiles/README.md) | Reusable settings and an IN2 visibility example |
| [Experiment index](../../experiments/polarization_locking/README.md) | Recordings, validity notes, and historical development data |
| [Maintaining the suite](docs/maintaining.md) | Code boundaries, adding tests, and release review |

## Quick start

Run commands from the repository root. Activate the established instrument
environment (`conda activate jl-env` on the current workstation) for bench runs.
Browsing, editing, saving recipes, `--list`, `--show`, and `--dry-run` require only
Python 3.10+. Hardware/report dependencies are in [requirements.txt](requirements.txt);
the optional `pid-live` display also needs `pyvista` and `pyvistaqt`. A working
RP FPGA/profile and PAX configuration are bench prerequisites; installing Python
packages alone does not configure them.

```bash
python python/polarization_locking/lock.py --list
python python/polarization_locking/lock.py
```

Select a test by name or number. **e** edits, **s** saves a recipe, **l** loads,
**r** runs, **b** goes back, and **q** quits. **d** restores that test's defaults;
**all** exposes every configuration field. Enter keeps an existing value.
Edits persist for that test during the session; save a recipe to keep them.
Only Run connects instruments. Ctrl+C stops a run and retains partial data.

### PAX vibration diagnostic

```bash
python python/polarization_locking/lock.py --run pax-vibration --pd-input in2
```

Use IN1 or IN2. The default is **30 seconds per condition**: first PD + fresh
PAX with its motor running, then the same fast PD acquisition with the PAX
rotation motor commanded off. The PAX stays mounted and powered; this is a
motor-on/off comparison. There is a five-second motor settling pause before
the off window. No waveforms run: both outputs hold fixed biases (default zero).
Edit `pax_vibration_phi1_bias_voltage` / `pax_vibration_phi2_bias_voltage` in the
normal table if a different static operating point is needed.

The test first prompts for a blocked-light PD dark capture, then for restored
light. To reuse a known signed dark baseline, supply `--dark-voltage VALUE` or
`pax_vibration_dark_voltage_v`. Keep light, detector gain, optics and PAX mounting
unchanged across both windows. Normal suite cleanup zeros RP outputs; the PAX
motor is stopped at the end. Startup uses the shared fresh-record validation.

The PD records full scope traces at about **15.26 kSa/s**, with FPGA averaging
and approximately 1.074-second captures at default decimation 8192. PAX polling
runs while the FPGA captures; it does not downsample the saved PD trace to PAX
rate. Records have host/network gaps, and a final complete capture may extend
a window slightly beyond 30 seconds. Actual sampling rate, captured time and
wall-clock coverage are recorded, with identical settings for both conditions.

Results use **sample variance**, not extrema:

- Relative PD power is `(V - V_dark) / mean(V - V_dark)`; raw signed voltage is
  preserved. Per-condition normalized variance is `var(V, ddof=1) / (mean(V)-V_dark)^2`.
- A second metric integrates the mean-removed Hann power spectrum over
  **5–1000 Hz** by default. Edit `pax_vibration_band_low_hz` / `high_hz` to change
  the comparison band. The same band applies in both conditions.
- The report shows on/off variance ratios and **signed on-minus-off excess**.
  Negative excess is preserved. Clipping or a zero corrected baseline makes
  normalized metrics unavailable. Compare light levels as well as noise: drift,
  shot noise or electronics can change the result, so excess is not automatically
  proof of mechanical vibration.
- PAX-on telemetry includes relative power variance and static sphere-angle
  variance using the established **S1-polar** coordinates:
  `u = atan2(S3,S2)`, `v = acos(S1/||S||)`. `u` is unwrapped before variance;
  its variance is unavailable at an S1 pole. Both variances are in rad²; the
  plots use degrees. Near a pole, azimuth noise is geometrically amplified.
  All acquisition-valid DoP values are retained. PAX's slower bandwidth cannot
  be directly compared with the PD's fast variance.

Each run keeps one compact `data.csv` with eight columns: `capture`, `voltage_v`,
`elapsed_s`, `pax_ptotal`, `s1`, `s2`, `s3`, `dop`. PD rows contain only a capture
ID and signed voltage; capture -1 is the dark trace. PAX rows have a blank capture
and retain elapsed time, power, normalized Stokes direction and DoP. Sample order
within each PD capture supplies the sample index. `vibration-setup.json` stores
sampling interval, host timing, motor state, dark offset and sample count once
per capture, plus wavelength once per run. Keep it with the CSV for reanalysis.
Reports derive normalized power, spectra and u/v from these essentials. No repeated
sample timestamps, derived arrays or full PAX diagnostic payloads are saved.
The usual summary JSON, recipe, provenance, log and PDF remain alongside the CSV.
No PAX measurements are fabricated during motor-off.
Partial data survive interruption. See [example recipe](profiles/pax-vibration-example.json).

### Live manual alignment

```bash
python python/polarization_locking/lock.py --run pax-live
```

`pax-live` opens a simple Qt panel with large power (µW), DoP (fraction),
S1/S2/S3 and theta/eta (degrees) readouts. Current values are unsmoothed;
small deltas compare endpoints roughly one second apart (theta wraps at 180°).
Stokes values are the existing adapter's normalized direction, without DoP scaling.
The panel uses `qtpy` and a Qt binding (`PyQt5` in the bench environment).
No Red Pitaya connection or output changes are made. Its state is recorded as
unmeasured; existing output drives may still be active.

The worker reuses PAX startup settling and `read_fresh_polarization()` validation.
It adds no polling sleep. Recent phase-sweep runs delivered about 5.6–5.8 fresh
samples/s including scope overhead; expect roughly 5–10 Hz on this setup,
subject to instrument/network timing. The panel displays the actual rate.
Startup/stale data show `WAITING FOR PAX…`; invalid placeholders are never
displayed as measurements. Stop/close/Ctrl+C finishes the current request and
disconnects PAX, then offers **Save** or **Discard**.

Every accepted sample is queued to one independent logger, which formats and
flushes each row to the printed run folder:
`experiments/polarization_locking/YYYY-MM-DD/HHMMSS_pax-live_LABEL/data.csv`
(or the configured `results_directory`). While open, `run.json` marks this
as `data_disposition: temporary`. CSV includes host/device timing, raw power
in W, angles in radians and degrees, Stokes, DoP, ADC diagnostics, revision
time, measurement counter, wavelength readback and the full exposed raw record
in `pax_raw_json`. Future diagnostic fields remain recoverable there. Standard
configuration/provenance live in `recipe.json` and `run.json`.

**Save** keeps the folder, marks it saved and writes a simple summary/trace PDF
after cleanup. **Discard** removes only this run's folder. Crashes or an
unconfirmed choice retain temporary data for recovery; cleanup failures are
always retained. An interrupted CSV can be read directly, and the normal
offline report command can regenerate its report. The legacy terminal-only
`live` test keeps its existing behavior.

Click **Orthogonalizer** for a separate manual two-arm alignment window. It can
be closed/reopened without affecting the main panel, PAX connection or raw CSV.
Open one arm and block the other, then **Acquire Reference** (default **5 s**,
editable in the window or `pax_live_reference_duration_s`, 0.1–60 s). Once ready,
block the reference arm, open the second, select **Align to Orthogonal**, and
adjust optics by hand. **Clear Reference** cancels capture/removes the target;
reacquisition replaces it. Closing the secondary window clears its state.

Reference capture uses only unique, fresh, validated samples received during
that interval, with `minimum_dop <= DoP <= 1` (default 0.9–1, the existing
trusted-target convention). Other acquisition-valid DoP values remain in the
main panel/raw log. Each Stokes vector is normalized first; those unit vectors
are averaged with equal weights, and the mean is normalized again:

```text
u_i = s_i / ||s_i||
m = (1/N) sum(u_i)
s_ref = m / ||m||
s_target = -s_ref
```

At least three usable samples and a nonzero mean direction are required. The
reference reports sample/exclusion counts, mean DoP, mean power, and the RMS
of `acos(clip(u_i · s_ref, -1, 1))` in degrees. A spread above
`pax_live_reference_max_spread_deg` (default 5°), or excluded samples, shows
**REFERENCE POLARIZATION UNSTABLE**; a defined target is still available with
that warning. Too few samples, an undefined mean, or cache overrun rejects the
reference. The reference and its exact negative remain frozen until cleared
or reacquired. Informational reference theta/eta come from the frozen vector,
never from averaging angles.

With normalized live direction `u`, the window uses:

```text
d = clip(s_ref · u, -1, 1)
error_deg = degrees(acos(-d))
overlap = sqrt((1 + d) / 2)
V_pol = overlap
balance = 2 sqrt(P_ref P_live) / (P_ref + P_live)
V_pred = balance * overlap
```

Error 0°, dot −1, and overlap 0 mean antipodal directions. The neutral meter
spans the full 0–180° range; full means 0°. `V_pol` is a **pure-state
polarization model**, not measured fringe visibility. The smaller power-balanced
prediction assumes positive powers measured through the same optical/detection
chain; it does not model partial polarization, coherence, or unequal attenuation.
Stale samples or live DoP outside the trusted range hide overlap/error metrics.

There remains exactly **one PAX acquisition loop**. It publishes raw snapshots
to a bounded, thread-safe cache and an independent logger queue; no CSV/JSON
formatting, GUI calculation, reference averaging or file flushing runs in the
PAX worker. Both GUI timers run at 20 Hz and consume cached samples, without
changing PAX timing settings or triggering duplicate reads. Reference capture
consumes every new cached sample, not each repaint. Cache overflow is detected;
it cannot silently bias a reference. The logger queue preserves every acquired
sample, drains before Save/Discard, and reports write failures. A process crash
can lose queued-but-unflushed tail samples; the flushed CSV remains recoverable.
Reference start/set/clear/cancel/alignment metadata goes through that same logger
to small `events.jsonl` records in the run folder. Raw `data.csv` stays authoritative.


**Output behavior matters:** ordinary menu tests, including `live` (PAX monitor),
initialize RP outputs to zero and attempt to return them to zero on exit.
`pd-visibility` offers PD, PAX or both. Its default passive mode preserves existing
outputs; active mode drives the selected phase output and returns both to zero
on exit. Only the required detectors connect. The interactive flow asks for
detector and mode first; actuator/waveform settings stay in the parameter table.

## First measurement and replay

Choose the actual external drive frequency before using this IN2 example:

```bash
python python/polarization_locking/lock.py --run pd-visibility --pd-input in2 --frequency 0.5
python python/polarization_locking/lock.py --run pd-visibility --source pax --frequency 0.5
python python/polarization_locking/lock.py --run pd-visibility --source both --mode active --frequency 0.5
```

PD mode measures a signed blocked-light baseline unless `--dark-voltage` is
provided; follow the block/unblock prompts. PAX mode uses instrument power in watts.

For routine use, load a template, edit its setup notes and settings, save it, and
run it from the menu. Each run stores a full recipe for replay:

```bash
python python/polarization_locking/lock.py --profile python/polarization_locking/profiles/visibility-in2-example.json
python python/polarization_locking/lock.py --profile path/to/run/recipe.json --dry-run
python python/polarization_locking/lock.py --profile path/to/run/recipe.json --run pd-visibility
```

The package entry point is `PYTHONPATH=python python -m polarization_locking`.
Recipes repeat software settings; the operator must reproduce the physical setup.
Historical gains and Vπ/Vλ values are candidates, not a current calibration.

## Results and offline review

Each menu run creates `experiments/polarization_locking/YYYY-MM-DD/HHMMSS_test_label/`
with `recipe.json`, `run.json`, `console.log`, and `report.pdf` if reporting
succeeds. Measurement tests also write `data.csv`; contrast adds `visibility.json`,
and PD mode retains raw scope/dark captures. The one-shot `rough` move records results in the log.
Review acquisition status, report status, and physical validity separately.

```bash
PYTHONPATH=python python -m polarization_locking.reports.run_report path/to/run
```

That command only rebuilds a report. [Offline analysis](analysis/README.md)
documents specialized analyzers. [Raw phi1 calibration](docs/raw-calibration.md)
is a separate specialist collector with its own data schema and report command.
Historical notes and archived timing scripts are not the operating procedure.

## Verify the software

```bash
python -m pip install -r python/polarization_locking/requirements-dev.txt
python -m pytest python/polarization_locking/tests
```

Checks cover measurement math, menu/recipes, every catalog dispatch, reports,
failures, and cleanup using synthetic data and mocks. Passing software tests
does not establish an optical calibration or verify bench wiring.

### Run names and comments

After selecting **Run**, two short prompts accept an optional run name and comment.
Press Enter at both to use the default name and no comment. These entries apply
only to that run; they do not silently carry into the next run. Saved recipe
values remain available as defaults and can be edited through `all`.

Folders keep the date and time: `YYYY-MM-DD/HHMMSS_pax-vibration` by default,
or `YYYY-MM-DD/HHMMSS_pax-vibration_heavy-base` for the name `heavy base`.
Comments are saved in `recipe.json`, `run.json`, the console log and the PDF.
Command-line runs stay noninteractive: use `--name "heavy base" --comment "PAX displaced onto heavier base"`.
