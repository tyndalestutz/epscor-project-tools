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
