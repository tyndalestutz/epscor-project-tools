# Read-only experiment reader — coverage and Phase 4 decision

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

## Phase 4B — continuous cross-sweep reports

`polarization_locking/reports/plot_continuous_cross_sweep.load_rows` now reuses
`RunInfo`/`iter_records` for native CSV ingestion. Its existing float/NaN and boolean
projection stays in the consumer, including optional-column defaults and unknown
text fields. Standalone CSVs require no runner metadata. Summary calculations,
thresholds, optional `drive_trace.npz` access and report presentation are unchanged.

Malformed CSV now raises shared `ReaderError` with source diagnostics instead of
DictReader padding/overwriting fields. This is separate from accepted empty files,
header-only files, missing optional columns and structurally valid interrupted
prefixes; a syntactically valid shortened value cannot always be detected.
Report orchestration is unchanged: errors while constructing the continuous
summary in `add_overview` propagate; errors in specialized pages are caught by the
existing handler and yield a partial report. Corruption is not empty success.
Tests compare both retained September schema variants, projection edge cases,
caller failures and temporary PDF output. Synthetic optional reference traces
exercise software behavior only. Phase 4 remains in progress.

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
  Version values alone do not establish support. Only the recognized high-speed
  and raw-calibration runner signatures interpret run.json schema_version (integer
  1, excluding booleans). Other version fields are retained without a compatibility
  verdict; reading their native JSON does not validate their semantic schema.
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
otherwise stated. Short locking module names resolve under
`python/polarization_locking/`; high-speed/field modules resolve under their named
application. The initial inventory was inspected at merge `0a6f70ac9924621698937ee367dc3788e9cd6088`;
Phase 4C reconciles it against `bc305a711c3c24765adfb921aac2213e7875c8a4`.
The rows below are the existing inventory, not a new catalog.

Support levels are cumulative only where evidence says so: **D** = immediate
artifact discovery; **T** = explicit native metadata/CSV/JSONL access; **R** =
structural runner recognition; **C** = demonstrated adopted-consumer semantics.
T does not establish a family schema; R does not validate an acquisition. A test
of an existing producer/analyzer is not a test of its migration to this reader.

Evidence abbreviations (function names below are pytest selectors):

- **Reader**: [test_experiment_reader.py](../../python/polarization_locking/tests/test_experiment_reader.py).
  `test_exact_csv_and_jsonl_unknowns_and_incomplete_events` establishes generic T;
  `test_inspection_never_reads_bulk_or_external_references` establishes bounded D;
  `test_family_native_metadata_and_independent_outcomes` establishes synthetic R.
  These generic contracts alone do not qualify each saved family.
- **Timing**: [test_cross_sweep_timing.py](../../python/polarization_locking/tests/test_cross_sweep_timing.py);
  **Continuous**: [test_continuous_cross_sweep.py](../../python/polarization_locking/tests/test_continuous_cross_sweep.py).
- **HS**: tests under [high_speed_polarimeter/tests](../../python/high_speed_polarimeter/tests);
  **Field**: tests under [field_propogation/tests](../../python/field_propogation/tests);
  other locking test filenames are under [polarization_locking/tests](../../python/polarization_locking/tests).

Each decision retains the existing native access/consumer where stated; deferred
work has a concrete trigger, not an automatic promise of implementation.

| Family / examples | Producers and existing consumers | Support / unqualified behavior | Evidence; retained/deferred decision and trigger |
| --- | --- | --- | --- |
| Catalog locking: `L/2026-09-23/230004_sweep_sweep/{run,recipe}.json`, `data.csv`; sweep, PID, path/power, feedback variants | `polarization_locking/runner.execute`, `app._new_experiment_paths`, routines; `reports/run_report`, calibration/PID/power plot loaders | **R/T**, synthetic runner mapping plus generic text; no per-procedure scientific schema. Saved catalog variants are not all reader-qualified. | Reader `test_family_native_metadata_and_independent_outcomes`; producer coverage: test_suite.py `test_all_catalog_entries_dispatch_and_persist`. **Retain** R/T. Defer per-test schemas until a concrete read-only consumer needs one; not every catalog writer is qualified. |
| CSV-only/flat archive: `L/2026-08-07/213425_pid-test_pid-test-9/data.csv`, `L/legacy/flat-archive/{quicksave,phi1-hysteresis,pid-test-0}.csv` | Historical/current calibration, feedback, diagnostic routines; `reports/plot_cross_tests.load_rows` and other plot loaders | **T**, generic standalone CSV only; no inferred runner/status or historical-schema adapter. | Reader `test_missing_empty_header_only_and_unsupported`, `test_exact_csv_and_jsonl_unknowns_and_incomplete_events`; saved paths at left are source-inventory examples, not family regression fixtures. **Retain** native CSV; defer legacy interpretation until a specified consumer/compatibility comparison exists. |
| Visibility: `L/2026-09-21/215158_pd-visibility_pd-visibility/capture-000.npz`; `L/2026-10-05/225117_pd-visibility_phi2-vis-test_path-E1/visibility.json`; standalone `L/2026-09-21/205238_pd-visibility/measurements.csv` | `routines/visibility.acquire_visibility`, `acquire_pax_visibility`, `analyze_trace`; `run_report.add_specialized` | **T** for selected JSON/CSV, **D** for NPZ; no visibility/dark-correction or capture decoding semantics. Signed raw volts and measured/provided dark baselines stay distinct across PD-only, PAX-only, concurrent and older standalone forms. | Producer/analysis coverage: test_visibility.py `test_contrast_detector_mode_lifecycle`, `test_both_collect_concurrently_with_drive_readback_and_cleanup`; generic Reader T/D tests only. **Retain** native text; defer NPZ/dark semantics until a consumer needs safe capture access with measured/provided baselines qualified. |
| Live PAX: `L/2026-10-14/155939_pax-live_TEC-active_quick-test/` | `routines/pax_live`, runner; `run_report.add_pax_live` | **R/T**, generic catalog metadata and text; saved live telemetry is not independently reader-qualified. No DoP scaling or absolute-Stokes inference. | Producer coverage: test_pax_live.py `test_worker_logs_every_row_reuses_freshness_and_preserves_diagnostics`, `test_worker_failure_keeps_partial_csv_and_cleans_up`. Reader generic R/T only. **Retain** native text; defer richer timing/DoP adapters until a named offline consumer and native-record fixtures exist. |
| Vibration: `L/2026-09-24/203850_pax-vibration_pax-vibration/{data.csv,vibration-setup.json,csv-conversion.json,csv-compaction.json}` | `pax_vibration.acquire`, `write_samples`; `run_report.vibration_report_rows`, `compact_report_rows` | **T** for native CSV/selected JSON, **D** for other artifacts; no reconstruction of original/wide/compact representations or PSD/dark summaries. Compact capture=-1 denotes dark, numbered captures PD and blank capture PAX; native reading does not recompute those products. | Existing analysis: test_pax_vibration.py `test_pooled_variance_retains_between_capture_drift_and_signed_excess`; Reader generic T only, not a compaction comparison. **Retain** text; defer reconstruction until original/wide/compact provenance and a concrete consumer are qualified. Existing analyzer is not a migration requirement. |
| Continuous sweep: `L/2026-09-29/{185740_cross-sweep_quick-test,215311_cross-sweep_phi1-rerun}/` | `continuous_cross_sweep.acquire_continuous_cross_sweep`, `_write_csv`, `_save_trace`; `plot_continuous_cross_sweep.load_rows`, timing audit | **C** for timing-audit ingestion and continuous-report typed projection on retained 42/44-column CSV variants; **T** metadata; optional NPZ stays in existing consumers, not reader decoding. | Timing `test_full_analysis_matches_previous_ingestion_for_all_reference_cases`; Continuous `test_shared_report_matches_legacy_ingestion_on_saved_records`, `test_shared_reader_keeps_overview_and_specialized_failure_boundaries`, `test_shared_report_pdf_and_optional_reference_stay_local`. **Retain** both adopted consumers (#7/#8). Defer optional reference decoding to a separate demonstrated reuse/integrity need; no saved drive_trace.npz here. |
| Independent raw calibration: `L/2026-09-18/195031_raw-phi1_repeatability-1s/{run.json,data.csv,scope.npz}`, interrupted `200242_raw-phi1_fine-brackets-3s` | `polarization_locking/raw_calibration.py:acquire`, `consolidate_scope`; `reports/raw_calibration.create_report`, `analyze` | **R/T** on retained completed/interrupted records; commands, ASG settings, internal digital scope and physical ADC evidence stay distinct. **D** only for scope.npz. | Reader `test_retained_raw_calibration_preserves_native_status_and_unknowns` (both saved examples); existing test_raw_calibration.py `test_scope_consolidation_preserves_all_arrays_and_removes_snapshots` tests producer consolidation, not reader decoding. **Retain** R/T; defer snapshot/member access until a consumer and immutable index/units contract justify it. |
| Standalone timing/diagnostics: `L/2026-09-18/225751_stokes-timing-test-phi1/{metadata.json,dynamic.csv,static.csv}`, `L/2026-09-29/diagnostic-development/` | Archived `stokes-timing-tools`, run-local analysis, `analysis/verify_rp_loopbacks` | **T** generic explicit text; no metadata.json status interpretation or voltage reconstruction. Archived collector can mark interruption completed; reader must not repair that history. | Reader generic T tests; collector source `stokes-timing-tools/acquire_stokes_timing_test.py:run` catches interruption then writes completed. No dedicated shared-reader semantics fixture. **Retain** original text/status; defer interpretation pending a historical status/reference audit and actual consumer need. |
| High-speed mock lifecycle | `high_speed_polarimeter/experiments/mock_lifecycle.acquire`, runner | **R/T** on actual driver-free mock-runner output; JSONL is synthetic lifecycle evidence, not a detector dataset. No saved physical bundle. | Reader `test_reader_keeps_real_mock_runner_outcomes_independent`; HS test_framework.py `test_mock_lifecycle_and_offline_replay` independently covers producer/replay. **Retain** R/T and synthetic labeling. No physical semantic adapter implied; qualify new event versions if introduced. |
| High-speed single/dual EOM waveform | `high_speed_polarimeter/acquisition/eom.acquire_states`; `quality_eom.evaluate_eom` | **R/T** on actual single/dual mock-producer metadata/events; **D** only for pd_raw.npy. No saved physical bundle. Concatenated arrays require event byte-range integrity, not one ordinary np.load. | Reader `test_native_acquisition_reads_do_not_run_postprocessing_or_decode_pd` (single/dual); HS test_eom_acquisition.py `test_single_eom_selection_and_channel_mapping`, `test_interruption_retains_pd_and_command_evidence` cover existing acquisition/quality, not reader buffer access. **Retain** R/T; defer binary access until a named consumer needs reuse plus complete byte-range/corruption/partial-buffer contracts and representative data. |
| High-speed PAX-only Vpi | `high_speed_polarimeter/experiments/vpi.py:acquire`; `analysis/vpi.analyze`, `quality_vpi.evaluate`, `reports/vpi.create_report` | **R/T** on actual PAX-only mock-producer output; CSV/events work without a PD file. No saved physical bundle; rough Vpi/retardance and synthetic direction are not physical calibration. | Reader `test_native_acquisition_reads_do_not_run_postprocessing_or_decode_pd` (Vpi); HS test_vpi.py `test_offline_vpi_rejects_invalid_evidence_before_model_fit` covers existing evidence checks, not native-reader scientific validation. **Retain** R/T with no PD prerequisite; defer model/evidence adapters to a distinct consumer request with independent physical qualification for physical claims. |
| Field simulation, splitter/spatial studies: `field_propogation/configs/` supplies inputs; generated bundles absent | `run_simulation.run`, `write_scan_csv`, `splitter_study`, `spatial_study` | **D**, generic explicit T available but not family-qualified; source configs exist, generated bundles absent. Simulated/derived studies are not measurement records. | Field test_simulation.py `test_ideal_fields_stokes_and_power_from_existing_reference`, test_spatial.py `test_gaussian_overlap_matches_independent_transverse_integration` qualify model math, not reader adapters. **Retain** D/generic T; defer harmonics/cuts/maps schemas until generated fixtures and a consumer with a provenance distinction are supplied. |
| Compact/split campaign: `experiments/field_propagation/measurement_campaign/2026-09-10/{campaign.json,validation_schedule.csv}` | `acquisition/session.read_section`, `records`, `initialize`; `prediction.freeze`, `predict`, `compare` | **D**, explicit campaign JSON/CSV T available but not a campaign adapter; retained planned reading lists are empty. Split receipts, frozen bundles and held-out semantics are not reader-qualified. | Field test_acquisition.py `test_compact_campaign_supports_direct_editing_without_import_files`, `test_synthetic_end_to_end_and_tamper_guards` cover existing compact/split/freeze consumers with synthetic records, not shared reading. **Retain** D/explicit T; defer adapters until a named consumer needs receipt/hash/version relationships preserved. No measured complete campaign/frozen bundle here. |
| Historical field fits nested in locking: `L/2026-08-13/192530_field-model-calibration_phi2-power-model-0/{field-propagation-fit,physical-jones-fit}/`, `200934_phi1-fringe-map_phi1-fringe-map-1/physical-jones-validation/` | `field_propogation/archive/effective_fits/` fit/predict/validate tools | **T** generic explicit text only; retained fitted/derived comparisons remain in place. No field-fit semantic adapter or reader-driven model validation. | Field test_simulation.py `test_frozen_fit_reproduces_all_saved_detector_fringe_predictions`, `test_preserves_held_out_disagreement_in_existing_report` cover existing historical consumers; no shared-reader semantic comparison. **Retain** generic T and disagreements; defer migration until it reduces concrete duplicate reading without changing fitted/held-out meanings. |
| Camera visualization: no saved datasets here; results ignored | `polarization_visualization/basler.save_record`, `systems_test.run_axis`; `analyze_systems_test.analyze` | **D**, generic selected text access only; no saved camera bundle or family-specific reader tests. NPY and raw/averaged/corrected images remain distinct. | Source inspection: basler.save_record, systems_test.run_axis, analyze_systems_test.analyze; generic Reader D/T only, no camera qualification. **Retain** discovery; defer frame decoding until a representative bundle, raw/averaged/correction provenance and consumer requirement exist. |
| VNA/supporting: `data/0616/MZ-piezo-00_20260616_171042-vna_sweep.csv`, supporting PNGs/logs, `reports/EOM-00-report.html` | `notebooks/Tutorial-MZI_dynamic_locking.ipynb` and `Tutorial-MMI_loop_characterization.ipynb` sweep/CSV cells; `data/0605/loop_gain-plot.py` | **T** generic native CSV; PNG/HTML **D** only. No reader-based VNA/CLTF/OLTF interpretation or notebook migration. Source labels CLTF measured and OLTF inferred using PID response; retain that distinction. | Source inspection: the two named notebooks and data/0605/loop_gain-plot.py; generic Reader T only, no VNA-specific fixture. **Retain** native text; defer transfer-function interpretation until calibrated units/PID-reference conventions and a named consumer are characterized. |

The campaign's planned reading lists are empty, planes/basis unconfirmed and
spatial detector model assumed. Separate direct-meter observations explicitly
lack acquisition time, detector identity, darkness, uncertainty and other metadata.
Missing information stays unknown. Failed catalog runs can have completed reports;
empty/header-only/interrupted records exist. Folder names and extensions never
establish measured/synthetic/simulated/fitted provenance by themselves.

## Phase 4C completion recommendation — decision pending

**Recommend A:** close Phase 4 only after review explicitly accepts the finite
native metadata/text boundary and every retained/deferred decision above. This
PR is an evidence audit with focused tests, not a declaration of completion.
No blocking production follow-up is identified for that proposed boundary.

The proposed acceptance contract is: bounded one-location discovery; native
JSON-object/CSV/JSONL access with explicit diagnostics and no source mutation;
structural recognition only for the three documented runner signatures and their
interpreted versions; independent native outcomes and unknown provenance; and
qualified adoption by the timing audit and continuous report. High-speed mock
producer and retained raw-calibration checks extend R/T evidence across producers,
but two locking consumers do not establish cross-application semantic support.
No full scientific-table model, common manifest writer or external record lookup
is promised. Existing producer manifests remain native data, not a new schema.

Focused evidence added in 4C checks actual high-speed mock runner outcomes
(completed, failed, interrupted, cleanup failure with completed report), actual
single/dual EOM and PAX-only mock bundles without binary decoding or postprocessing,
and both retained raw-calibration statuses. Existing tests already cover RUNNING
as native evidence, legacy cleanup_failed without reconstructed outcomes, missing
metadata, separate artifact versions, conflicting/unknown signatures, malformed
versus empty/header-only records, guarded imports and non-atomic change detection.
Do not duplicate those checks or infer a live process from RUNNING.

Deferred semantics/binary support is optional future scope unless review identifies
one concrete contract this boundary fails to fulfill. A decoder must have an
actual consumer, an integrity/provenance contract and qualified representative
fixtures; tests of an existing analyzer alone are insufficient. In particular,
quality_vpi.evaluate calls analysis that writes products; it is not a read adapter.
No additional real acquisition is required to decide this native-access boundary;
physical performance or new measurement-specific adapters need separately qualified
records/protocols. Source inspection and synthetic evidence never establish bench
validity. Stat checks are not an atomic snapshot or security sandbox.

Phase 4 remains **in progress / completion decision pending** until review accepts
this boundary. Phase 5A (configuration characterization) is the next possible
checkpoint after acceptance, not work started or authorized by this PR.

To reproduce the focused native-reader checks, use the software-only environment
and guards in the [offline guide](offline-testing.md), with:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=scripts/offline:python QT_QPA_PLATFORM=offscreen MPLBACKEND=Agg MPLCONFIGDIR=/tmp/epscor-reader-audit-mpl XDG_CACHE_HOME=/tmp/epscor-reader-audit-cache PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -p no:cacheprovider python/polarization_locking/tests/test_experiment_reader.py --basetemp=/tmp/epscor-reader-audit-pytest
bash scripts/test-offline.sh
```

Use fresh temporary locations if these example paths already hold needed work.
The full command also executes the specifically named producer/consumer tests
above. The table distinguishes those tests from direct shared-reader evidence;
passing them does not upgrade every family to C or prove every historical format.

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
