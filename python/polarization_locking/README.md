# Polarization diagnostics

A bench diagnostics suite with 23 numbered tests, editable defaults,
JSON recipes, and one folder per run. Start from the repository root:

```bash
python python/polarization_locking/lock.py
```

Use your instrument Python environment for hardware runs (currently `jl-env`).
Browsing, editing, saving recipes, `--list`, and `--show` need only Python 3.10+.
Hardware/report dependencies are in `requirements.txt`; the optional live sphere
also needs `pyvista` and `pyvistaqt`. The bundled PAX daemon configuration is
[hardware/pax1000.toml](hardware/pax1000.toml); edit its serial number for another PAX.

1. Enter a test number or its name to see its purpose, required optical setup,
   run options, and acquisition parameters.
2. Choose **r** to run with those values, or **e** to edit selected parameters by
   number/name. Enter keeps a value; **b** cancels or goes back. **all** exposes
   every configuration field. Numeric arrays use JSON, e.g. `[0.2, 0.1]`;
   booleans use `true`/`false`; nullable values accept `null`.
3. **s** saves a recipe; **l** loads one. Edits survive Back and repeat runs in
   the current session and are independent per test. Save to retain them after
quitting. **d** restores that test's defaults.
4. **b** returns to the list; **q** quits. Ctrl+C cancels an edit or stops a run.
   During manual beam-block/setup prompts, **b** cancels the run.

Only Run connects instruments. A run initializes RP outputs to zero and attempts
to return them to zero on completion, cancellation, or failure. Connection and
acquisition failures return to the menu with an error; cleanup failure closes the
menu and reports that the outputs need checking. The PAX monitor is continuous
until Ctrl+C. Duration on path comparisons means **per path condition**.

Repeat a recipe without re-entering parameters:

```bash
python python/polarization_locking/lock.py --list
python python/polarization_locking/lock.py --show sweep
python python/polarization_locking/lock.py --profile path/to/recipe.json --dry-run
python python/polarization_locking/lock.py --profile path/to/recipe.json
# Explicit hardware execution:
python python/polarization_locking/lock.py --profile path/to/recipe.json --run sweep
```

The equivalent package entry point is `PYTHONPATH=python python -m polarization_locking`.
An editable minimal recipe is provided in [profiles/sweep-example.json](profiles/sweep-example.json).
`--dry-run` prints and validates a recipe without hardware access. It does not
simulate PAX measurements. Each run creates `experiments/polarization_locking/YYYY-MM-DD/time_test_label/`
(or an explicitly edited `results_directory`) with:

- `recipe.json`: complete input configuration and test options, loadable for replay.
- `run.json`: status, timestamps, code fingerprint, Python version, required setup,
  effective configuration including a captured target, and any failure.
- `data.csv`: readings, including partial data if interrupted; the one-shot rough
  move instead records its measurements in `console.log`.
- `console.log` and **`report.pdf` for every test**, including stopped monitors and
  interrupted scans. Reports contain a run summary, the existing specialized plots
  where available, raw telemetry, and active parameter tables. Incomplete scans
  retain a PDF with raw data even when a specialized fit cannot be calculated.
  `report_status` and any plotting failure are recorded separately in `run.json`.

Recipes reproduce commands and settings, not the bench's physical state. With
`target_mode=current`, each run captures a new target through the DOP gate. Use
`target_mode=explicit` and set `target_u`/`target_v` in radians to repeat a fixed
target; a captured target is recorded in `run.json`. Existing PI diagnostic loops
retain their historical behavior of logging DOP without gating feedback.

The current bench note records **PAX in front of NPBS 1 at C**, the chain
**RP → Thorlabs MDT690 → piezo**, and an **expected Vπ ≈ 13 V at the terminals**.
This is an expectation, not a measured calibration. RP commands stay within
**0–1 V**. `phi*_v_lambda` is terminal voltage for **2π**, so a 13 V Vπ would imply
26 V Vλ. The historical gains 16.875 and 150 terminal V / RP command V and old
Vλ candidates remain editable; they have not been established for this hookup.
The suite does not infer MDT690 gain from its model name or silently replace an
existing calibration. Check the actual channel and transfer gain before a new
scan. D-port tests explicitly require a different PAX location.

The explicit-range **Single-axis voltage sweep** now exposes start, stop, point
count, repeats, and settling time. It is a data collector, not a Vπ fit. The
existing **D-port phi1 step map** retains its D-port-specific interpretation.
For direct calibration without fits, use the dedicated raw acquisition command:

```bash
PYTHONPATH=python python -m polarization_locking.raw_calibration --stop 0.8 --step 0.02 --repeats 3 --settle 1 --samples 5 --label repeatability
PYTHONPATH=python python -m polarization_locking.raw_calibration --hold-voltage 0.4 --hold-points 30 --samples 10 --label fixed-voltage
```

Use `--pax-location` and `--input-connections` to record the actual wiring.
`--levels 0,0.2,0.3,0.35,0.4,0.6,0.8` supplies an explicit ascending grid;
the command also visits it in reverse. All voltages are RP commands within 0–1 V.
Each run saves `data.csv`, exact PAX records in `pax_raw.jsonl`, command/register
readbacks in `commands.jsonl`, full scope snapshots in one `scope.npz`, active settings
in `run.json`, and a PDF in the dated experiments folder. Fixed-voltage mode
writes the command once and observes successive blocks without rewriting it.
During acquisition, snapshots are written incrementally; on exit they are packed
into the single archive and verified byte for byte before the temporary snapshots
are removed. Archive keys preserve the original point and array names, for example
`0000/samples`, `0000/analog_in1_samples` and `cleanup-zero/samples`.

The report shows every measurement and discrete π-change brackets from labelled
step medians, with wider brackets including the observed endpoint sample spread.
There are no fits, interpolated crossings, drift corrections, or assumed driver
gains. `atan2(S3, -S2)` is a projected equatorial phase; if S1 departs from zero,
its π-change is not an independent calibration of actuator retardance.
Internal OUT1 scope traces establish the digital pre-DAC signal, not an electrical
measurement of the RP BNC or piezo terminals. A verified electrical monitor is
needed to complete that voltage-chain mapping.

Regenerate these raw reports with their dedicated, fit-free report command:

```bash
PYTHONPATH=python python -m polarization_locking.reports.raw_calibration experiments/polarization_locking/YYYY-MM-DD/raw-run-folder
```

On the installed FPGA, the PyRPL `scope.voltage_out1/2` properties address trigger
timestamp registers, so this collector does not use them. It captures the scope
with an explicitly selected `out1` source instead. Similarly, scope register
inputs follow the selected source; physical IN1/IN2 are recorded only after
explicitly routing those sources. Earlier connection probes carry validity notes
in their `run.json`; do not treat their invalid register fields as voltages.

Code organization is intentionally small:

| Location | Responsibility |
|---|---|
| `cli.py`, `catalog.py` | Menu, test descriptions, relevant settings, dispatch metadata |
| `settings.py`, `config.py` | JSON recipes, type/range validation, defaults |
| `runner.py`, `app.py`, `control.py` | Run lifecycle, instrument session, coordinate math |
| `routines/` | Sweep collection, diagnostics, feedback experiments |
| `hardware/` | RP/PAX adapters and local PAX daemon/configuration |
| `analysis/`, `reports/` | Offline analysis and plots |
| `profiles/` | Saved recipes; generated runs live under `experiments/polarization_locking/` |
| `tests/` | Automated software tests; never connect to hardware |
| `docs/` | Historical bench notes and checkpoint report source |

To add a bench test, implement its method in `routines/`, add parameters to
`PolarizationLockConfig`, and add one `TestCase` to `catalog.py`. `groups` controls
which configuration prefixes appear on its detail page; shared connection and
voltage-chain settings always appear. The runner handles saving and cleanup.

Run the offline regression suite:

```bash
python -m pip install -r python/polarization_locking/requirements-dev.txt
python -m pytest python/polarization_locking/tests
```

Tests cover navigation, editing/defaults, JSON round trips, dispatch of every bench
test, cancellation, invalid voltages, connection failures, reports, and cleanup.
Both historical and new data live in dated folders under `experiments/polarization_locking/`.
Recipes using the temporary suite-local `results/` default are migrated on load.
PDF output is mandatory; legacy `report=none` becomes `pdf`, and `png` becomes
`both`. Tests with PNG support can request both formats. Historical command syntax is replaced by the menu;
`lock.py` remains a launch shim. Imports move from `pax_interface`/`rp_interface`
to `hardware.*`, `calibration` to `routines.calibration`, and plots to `reports.*`.
See [historical bench notes](docs/bench_reference.md) for coordinate conventions
and prior experiments, and [offline analysis](analysis/README.md) for report tools.

Regenerate a report from an existing run, without connecting instruments:

```bash
PYTHONPATH=python python -m polarization_locking.reports.run_report experiments/polarization_locking/YYYY-MM-DD/run-folder
```
