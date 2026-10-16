# Read-only experiment reader — phase 4, increment 1

`python/experiment_reader.py` provides native metadata and text records to existing
offline consumers. It does not certify a measurement or replace acquisition,
quality evaluation, scientific analysis or reporting. Phase 4 remains in progress.

```python
from experiment_reader import inspect_run, read_metadata, iter_records

run = inspect_run('/supplied/location')  # may be outside this repository
print(run.family, run.outcomes, run.diagnostics)
metadata = read_metadata(run, 'acquisition.json')
for row in iter_records(run, 'data.csv'):
    pass  # CSV fields stay strings; interpretation belongs to the consumer
```

`RunInfo(directory)` also permits selecting required artifacts without inspecting
unrelated metadata. The timing audit uses this path so partial folders containing
only acquisition metadata and CSV remain usable. Its calculations, required-field
checks, NPZ access, reference fallback and CLI outputs are unchanged. The direct
script entry point adds the repository's `python/` directory to its import path.

## Contract and bounds

- Inspection enumerates at most 256 immediate entries, reads up to 1 MiB each of
  `run.json`, `recipe.json`, `acquisition.json`, `quality.json`, and reads CSV headers
  up to 64 KiB each. It does not recursively discover studies, read CSV bodies,
  decode arrays, hash datasets or parse embedded campaign measurements.
- Artifact names in the listing are discovery evidence only: a directory, missing
  companion or undecodable artifact is not a readable dataset. Explicit reads
  diagnose failures. Directory enumeration order is filesystem-dependent when
  the entry bound is exceeded; the diagnostic makes incomplete discovery visible.
- Selected JSON metadata must be an object, with duplicate keys rejected at every
  nesting depth. Unknown fields, nulls, numbers, strings and tagged telemetry are
  retained. Historical JSON NaN/Infinity remain floats, not silently repaired.
  `read_metadata(..., max_bytes=None)` deliberately reads oversized JSON; this is
  necessary for compact campaigns with embedded measurements. The timing consumer
  explicitly keeps its previous unbounded acquisition-JSON read behavior.
- CSV/JSONL iteration is lazy, with a default 1 MiB record budget (configurable via
  `max_record_bytes`). The Python CSV parser's existing field-size limit also
  applies. Quoted multiline CSV is supported within the record budget; fields are
  never coerced. JSONL requires one object per physical line. A final newline is
  optional. Empty files yield no records; header-only CSV yields no rows.
- Duplicate/empty CSV headers, blank data records, unequal row widths, invalid
  UTF-8, malformed JSON/JSONL and CSV parser failures raise `ReaderError`. Its
  `diagnostic` has code, source path, message and line where available. Iteration
  stops at the first error; no recovery boundary, replacement value or omitted
  record is guessed. Previously yielded records remain available to the caller.
- Inspection collects errors in `run.diagnostics`; selected reads raise them.
  Codes distinguish `missing`, `malformed`, `unsupported`, `ambiguous`, `oversized`,
  `unreadable` and `changed`. Unsupported versions retain their native metadata.
- Family recognition uses native runner structure: string `test` for catalog
  locking; `schema_version: 1` plus string `experiment` for high-speed; version 1
  plus `purpose` and `measurement_definitions` for independent raw calibration.
  Conflicting signatures remain unknown/ambiguous. These are structural labels,
  not validation of every experiment's schema. Each metadata file keeps its own
  schema/format/version fields; one file's version never substitutes for another.
- `outcomes` preserves native execution, cleanup, quality-evaluation execution,
  recorded scientific verdict and reporting independently. Missing values are
  `None` (unknown). A legacy execution value `cleanup_failed` cannot reconstruct
  prior execution or a separate cleanup field. `RUNNING` is recorded evidence,
  not evidence of a live process. Verdicts are read, never recomputed.
- No metadata reference is followed. Selected names must stay within the supplied
  directory, including resolved symlinks; absolute/parent paths are rejected.
  Users can explicitly supply a different root. No hardware, application runners,
  report engines, network operations or services are imported/started.
- Stat identity/size/mtime/ctime checks detect ordinary changes during metadata or
  header reads and before yielded records and at iterator exhaustion. This is
  **not an atomic snapshot or a security sandbox**. Files may change between
  operations, after a yield, or after an iterator is abandoned. A caller needing
  snapshot consistency must use an independently retained immutable bundle.

## Full format inventory and staged coverage

Paths below are repository-relative. `L/` abbreviates
`experiments/polarization_locking/`; source paths begin under `python/` unless
otherwise stated. The inventory was inspected at merge `0a6f70ac9924621698937ee367dc3788e9cd6088`.

| Family / examples | Producers and existing consumers | Increment 1 and remaining work |
| --- | --- | --- |
| Catalog locking: `L/2026-09-23/230004_sweep_sweep/{run,recipe}.json`, `data.csv`; sweep, PID, path/power, feedback variants | `polarization_locking/runner.execute`, `app._new_experiment_paths`, routines; `reports/run_report`, calibration/PID/power plot loaders | Runner metadata recognized; native CSV readable. No common scientific table or per-procedure semantic validation. |
| CSV-only/flat archive: `L/2026-08-07/213425_pid-test_pid-test-9/data.csv`, `L/legacy/flat-archive/{quicksave,phi1-hysteresis,pid-test-0}.csv` | Historical/current calibration, feedback, diagnostic routines; `reports/plot_cross_tests.load_rows` and other plot loaders | CSV readable; missing provenance/status remains unknown. Historical schemas not inferred from names. |
| Visibility: `L/2026-09-21/215158_pd-visibility_pd-visibility/capture-000.npz`; `L/2026-10-05/225117_pd-visibility_phi2-vis-test_path-E1/visibility.json`; standalone `L/2026-09-21/205238_pd-visibility/measurements.csv` | `routines/visibility.acquire_visibility`, `acquire_pax_visibility`, `analyze_trace`; `run_report.add_specialized` | Metadata/CSV explicitly readable. PD-only, PAX-only, concurrent and older standalone variants stay distinct. NPZ decoding deferred. Signed raw volts, measured/provided dark baseline and derived contrast are separate. |
| Live PAX: `L/2026-10-14/155939_pax-live_TEC-active_quick-test/` | `routines/pax_live`, runner; `run_report.add_pax_live` | Native metadata/CSV readable, including raw JSON text and host/device times. No DoP scaling or absolute-Stokes inference. |
| Vibration: `L/2026-09-24/203850_pax-vibration_pax-vibration/{data.csv,vibration-setup.json,csv-conversion.json,csv-compaction.json}` | `pax_vibration.acquire`, `write_samples`; `run_report.vibration_report_rows`, `compact_report_rows` | Native CSV/selected JSON only. Original multi-file, wide converted and compact forms differ. Compact `capture=-1` is dark, numbered captures PD, blank capture PAX. Derived statistics/PSD reconstruction deferred. |
| Continuous sweep: `L/2026-09-29/{185740_cross-sweep_quick-test,215311_cross-sweep_phi1-rerun}/` | `continuous_cross_sweep.acquire_continuous_cross_sweep`, `_write_csv`, `_save_trace`; `plot_continuous_cross_sweep.load_rows`, timing audit | Native metadata/CSV readable and timing consumer adopted. Same format version has evolving fields. No `drive_trace.npz` exists in this checkout; consumer retains its existing optional NPZ path. |
| Independent raw calibration: `L/2026-09-18/195031_raw-phi1_repeatability-1s/{run.json,data.csv,scope.npz}`, interrupted `200242_raw-phi1_fine-brackets-3s` | `raw_calibration.acquire`, `consolidate_scope`; `reports/raw_calibration.create_report`, `analyze` | Runner structure recognized, text readable; consolidated NPZ snapshot/member decoding deferred. Commands, settings readback, internal digital scope and physical ADC evidence stay distinct. |
| Standalone timing/diagnostics: `L/2026-09-18/225751_stokes-timing-test-phi1/{metadata.json,dynamic.csv,static.csv}`, `L/2026-09-29/diagnostic-development/` | Archived `stokes-timing-tools`, run-local analysis, `analysis/verify_rp_loopbacks` | Explicit native text reads only. Archived timing collector can label an interruption completed. Coverage and reconstructed voltage semantics require separate review. |
| High-speed mock lifecycle | `high_speed_polarimeter/experiments/mock_lifecycle.acquire`, runner | Runner metadata recognized; synthetic JSONL readable. No saved bundle here. Events are not measurements. |
| High-speed single/dual EOM waveform | `high_speed_polarimeter/acquisition/eom.acquire_states`; `quality_eom.evaluate_eom` | Runner metadata/events readable; source/synthetic-test backed, no saved bundle here. `pd_raw.npy` is concatenated NPY arrays indexed by event byte ranges, not one ordinary array. All decoding/integrity adapters deferred. |
| High-speed PAX-only Vpi | `experiments/vpi.acquire`; `analysis/vpi.analyze`, `quality_vpi.evaluate`, `reports/vpi.create_report` | Runner metadata/CSV/JSONL readable; no saved bundle here. No PD acquired; Vpi/retardance are model-derived. Undriven PAX is not detector darkness. |
| Field simulation, splitter/spatial studies: `field_propogation/configs/` supplies inputs; generated bundles absent | `run_simulation.run`, `write_scan_csv`, `splitter_study`, `spatial_study` | Artifact discovery and selected native JSON/CSV reads; semantic adapters deferred. Flow/parameters, harmonics, cuts/maps and study results are simulated/derived, sometimes measurement-constrained. |
| Compact/split campaign: `experiments/field_propagation/measurement_campaign/2026-09-10/{campaign.json,validation_schedule.csv}` | `acquisition/session.read_section`, `records`, `initialize`; `prediction.freeze`, `predict`, `compare` | Campaign not eagerly parsed; explicit JSON read available. Split receipt/raw validation, frozen manifests, predictions and held-out comparisons deferred; no frozen manifests here. |
| Historical field fits nested in locking: `L/2026-08-13/192530_field-model-calibration_phi2-power-model-0/{field-propagation-fit,physical-jones-fit}/`, `200934_phi1-fringe-map_phi1-fringe-map-1/physical-jones-validation/` | `field_propogation/archive/effective_fits/` fit/predict/validate tools | Native explicit text only; historical semantic adapters deferred. Fitted coefficients and model comparisons are not direct measurements. |
| Camera visualization: no saved datasets here; results ignored | `polarization_visualization/basler.save_record`, `systems_test.run_axis`; `analyze_systems_test.analyze` | Discovery/selected JSON/CSV only. NPY, raw versus averaged frames and correction-aware adapters deferred. |
| VNA/supporting: `data/0616/MZ-piezo-00_20260616_171042-vna_sweep.csv`, supporting PNGs/logs, `reports/EOM-00-report.html` | `notebooks/Tutorial-MZI_dynamic_locking.ipynb` and `Tutorial-MMI_loop_characterization.ipynb` sweep/CSV cells; `data/0605/loop_gain-plot.py` | Native CSV only; VNA interpretation deferred. Source labels CLTF measured, OLTF inferred using PID response. Images/HTML alone cannot establish numerical provenance. |

The campaign's planned reading lists are empty, planes/basis unconfirmed and
spatial detector model assumed. Separate direct-meter observations explicitly
lack acquisition time, detector identity, darkness, uncertainty and other metadata.
Missing information stays unknown. Failed catalog runs can have completed reports;
empty/header-only/interrupted records exist. Folder names and extensions never
establish measured/synthetic/simulated/fitted provenance by themselves.

## Reuse, exclusions and validation

The reader consolidates JSON/CSV ingestion formerly local to the timing audit.
Duplicate-key handling follows the existing `field_propogation.configuration.read_json`
pattern without importing the optical engine. Strict text parsing deliberately
rejects duplicate keys/headers and malformed widths that the former generic JSON/
DictReader ingestion could overwrite or pad. This changes invalid-input rejection,
not scientific processing; focused tests separate it from valid-input equivalence.

Do not call quality/report APIs to read runs: high-speed `quality.reevaluate`
writes products; `quality_vpi.evaluate` calls analysis that writes `analysis.json`.
Vibration `compact_report_rows` recomputes statistics/dark correction/PSD; continuous
plot loaders coerce missing values to NaN; visualization analysis applies corrections
and writes products. Those remain explicit downstream operations.

Preserve existing Stokes signs/axes/angles and native reference planes. Raw detector
voltage, dark-subtracted voltage and calibrated optical power remain distinct.
Zero volts and mathematical Jones-field darkness do not establish detector darkness.
No implicit conversion, filtering, normalization, calibration, fitting, quality
certification, writer replacement, migration or hardware changes are included.

Tests live in `polarization_locking/tests/test_experiment_reader.py` and the existing
timing tests. They run through `bash scripts/test-offline.sh`, covering strict text
errors, unknown metadata/statuses, bounds, lazy reads, immutability, outside-root
inputs, guarded imports, valid-input ingestion equivalence across nominal/FPGA
fallback cases, and direct CLI products. These are software checks, not bench
validation. Full family semantic adapters and bulk decoding remain future increments.
