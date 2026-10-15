# Scientific record inventory

Read-only snapshot: 2026-10-15, integrated commit
`582709158e8ed19f76810cc4d3c4597a6a50fa20`. This is a retention inventory,
not a determination of physical validity or permission to remove records.

| Tree | Git files | Git bytes | Local files | Local bytes |
| --- | ---: | ---: | ---: | ---: |
| experiments/ | 629 | 247025005 | 921 | 501660928 |
| data/ | 55 | 13111113 | 55 | 13111113 |
| reports/ | 1 | 875560 | 1 | 875560 |
| papers/ | 1 | 1936902 | 1 | 1936902 |
| notebooks/ | 8 | 4908209 | 8 | 4908209 |

Git counts/sizes come from `git ls-tree -rl HEAD` (logical blob bytes,
not compressed history). Local counts/sizes come from recursive regular-file
stat calls, including ignored/untracked files, without following symlinks.
Local totals are workstation-specific, not an archive completeness claim.
No files were moved, deleted, untracked or rewritten.

## Classification and retention implications

- **Production source:** reusable implementations live in `python/`; tests and
  software-only tooling also live in `tests/` and `scripts/`. Keep these in Git.
- **Recipes/configuration:** package profiles and model configurations are reusable
  source inputs. Run `recipe.json` files instead record resolved acquisition
  conditions; preserve them with their measurements, not as interchangeable defaults.
- **Raw evidence and provenance:** experiments include 171 CSVs, 22 NPZs,
  176 JSONs and six JSONL files. Extensions alone cannot establish raw versus
  derived status: buffers, event logs, metadata and derived tables must be read
  according to their producer/schema. Preserve failed and partial runs.
- **Derived and historical records:** experiments include 118 PDFs, 50 PNGs,
  four TeX sources and nine run-local/historical Python scripts. Analysis and
  historical acquisition code remain evidence of how records were produced;
  new reusable code belongs under `python/`. Flat archives remain discoverable
  through the [experiment index](../../experiments/README.md).
- **Supporting data:** `data/` contains 13 CSVs, 40 PNGs, one Python script and
  one text file, including historical VNA/transfer-function work. It is not the
  current acquisition destination. `reports/` contains an HTML report;
  notebooks contain eight exploratory notebooks with potentially embedded outputs.
- **External literature:** `papers/` contains one proposal PDF. Retain existing
  records in this PR; future publication needs ownership/distribution review.
  Prefer citations/authorized links for new third-party literature.
- **Caches/local state:** two tracked bytecode files and a 12585492-byte daemon
  log exist under `python/PAX1000/`. Experiments also contain TeX auxiliary/log
  products. These are candidates for later ownership review, not automatic
  deletion: acquisition console logs can be essential provenance. Package-local
  settings and caches already have subsystem ignore rules; settings are not
  portable bench profiles and must not contain credentials in public examples.

## Size and fixture decisions

Largest tracked file: the 32397193-byte `scope.npz` in
`experiments/polarization_locking/2026-09-18/195031_raw-phi1_repeatability-1s/`.
The 2026-10-08 long PAX run has a 24140852-byte CSV; four September vibration
CSVs are each about 22 MB. These are priority candidates for a later backed-up
archive decision, with their entire provenance bundles retained. Size does not
justify removing them now. No size threshold authorizes deletion.

`python/field_propogation/tests/test_simulation.py` currently reads the two
2026-08-13 run directories named `192530_field-model-calibration_phi2-power-model-0`
and `200934_phi1-fringe-map_phi1-fringe-map-1`, including fit/prediction and
validation JSON/CSV records. Keep these available in Git for the existing offline
suite. They are specific regression evidence, not universal scientific goldens.
Selecting smaller fixtures and changing consumers requires a separate PR.

## Current output conventions

Locking allocates dated run folders with recipe, `run.json`, console log,
measurements and optional report. High-speed acquisition retains resolved recipes,
run/source provenance, events, raw buffers/PAX references and derived quality
products; its run allocator adds a unique suffix. Field workflows retain measured
inputs, frozen parameters, predictions and held-out comparisons separately.
Existing subsystem ignore rules differ: high-speed output is already broadly
ignored within its own results tree; locking retains most run products while
ignoring selected bulk drive traces; field ignores selected regenerated outputs.
These rules and all production output destinations remain unchanged.

The [retention policy](data-retention.md) establishes future handling. Actual
storage owner/location, replication and recovery verification remain open; no
external archive has been provisioned by this work.
