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

Every accepted sample is flushed immediately to the printed run folder:
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
