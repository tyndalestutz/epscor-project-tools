# Polarization-locking test suite

Bench measurements and locking diagnostics for the Red Pitaya (RP), PAX
polarimeter, and photodiode (PD). Use the menu to select a test, review its
setup, save a recipe, and acquire one labelled run. Automated software tests
are in `tests/`; they do not connect instruments.

| Start here | Purpose |
| --- | --- |
| [Operating procedure](docs/operating-procedure.md) | Prepare, run, check quality, repeat, and hand over |
| [Test and pipeline guide](docs/test-guide.md) | Choose a test and understand what its result establishes |
| [Passive PD visibility](docs/visibility.md) | Measure contrast under external drive without changing RP outputs |
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

**Output behavior matters:** ordinary menu tests, including `live` (PAX monitor),
initialize RP outputs to zero and attempt to return them to zero on exit.
`pd-visibility` is the passive exception: it connects only the RP scope, blocks
output-register writes, restores scope settings, and leaves existing output
activity unchanged. It never connects PAX.

## First measurement and replay

Choose the actual external drive frequency before using this IN2 example:

```bash
python python/polarization_locking/lock.py --run pd-visibility --pd-input in2 --frequency 0.5
```

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
succeeds. Measurement tests also write `data.csv`; visibility adds scope captures
and `visibility.json`. The one-shot `rough` move records results in the log.
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
