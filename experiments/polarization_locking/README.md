# Polarization-locking experiment index

Run new measurements through the [documented suite](../../python/polarization_locking/README.md).
Reusable code belongs there; this directory holds results and session-specific
analysis/development evidence. Historical folders retain their original names
and schemas. None of the archived datasets is automatically a current calibration.

| Session | Contents and interpretation |
| --- | --- |
| [2026-09-18](2026-09-18/README.md) | Raw phi1 measurements and experimental Stokes/timing work; includes provisional and partial captures |
| [2026-09-21](2026-09-21/README.md) | IN2 contrast audit, rejected percentile estimate, passive replacement and remaining bench limitations |
| `2026-08-*` | Earlier path, sweep and locking runs; interpret using each run's original metadata and analysis |
| `legacy/flat-archive/` | Older recordings with original names and no guarantee of current recipe compatibility |

## Current menu-run schema

```text
YYYY-MM-DD/HHMMSS_<test>_<label>/
  recipe.json       full replay settings
  run.json          acquisition/cleanup/report status, effective config, provenance
  console.log       operator-visible messages and errors
  data.csv          measurements where applicable
  report.pdf        generated after cleanup, including partial data where possible
  notes.md          optional operator interpretation, added separately
```

Visibility adds `visibility.json` and `capture-*.npz`. `rough` records results in
the log rather than CSV. A missing PDF may mean reporting failed; read `run.json`.
Date/folder names use local time; new run metadata uses UTC timestamps.

The separate [raw calibration collector](../../python/polarization_locking/docs/raw-calibration.md)
saves `run.json` parameters, `data.csv`, `pax_raw.jsonl`, `commands.jsonl`,
`scope.npz`, and its own report. It does not write a menu recipe. Earlier timing
tools instead use `metadata.json`, single CSVs, or dynamic/static CSV pairs.
Never manufacture missing recipes or completion status for historical runs.

## Sharing and reanalysis

Keep raw data, recipes, metadata, log, report, and validity notes together.
Run-local analysis scripts and plots may stay beside their inputs, clearly
labelled as derived work. Do not import them from the suite. Cache/bytecode are
ignored; console logs and experimental evidence are intentionally retained.

Regenerate a current menu report without hardware access:

```bash
PYTHONPATH=python python -m polarization_locking.reports.run_report path/to/run
```

Use the dedicated raw-calibration report command for its schema. Reprocessing
with newer code changes derived reports; preserve a copy or use a new analysis
directory when the original report matters. A recipe repeats settings, not the
bench's physical state; follow the operating procedure before replaying it.
