# Maintaining the suite

## Boundaries

| Location | Responsibility |
| --- | --- |
| `lock.py`, `__main__.py`, `cli.py` | Launch/menu/argument handling; browsing stays hardware-free |
| `catalog.py` | Supported test names, purposes, setup, parameters, method/report dispatch |
| `config.py`, `settings.py`, `profiles/` | Defaults, validation, versioned recipes and examples |
| `runner.py` | One run's recipe, provenance, log, status, cleanup and reporting |
| `app.py`, `control.py` | Session ownership, shared helpers, coordinate/control math |
| `routines/` | Acquisition/feedback methods and measurement calculations |
| `hardware/` | RP/PAX adapters, PAX daemon and its bench configuration |
| `analysis/`, `reports/` | Offline calculations, figures and PDF generation |
| `raw_calibration.py` | Separate specialist collector with its own artifact schema |
| `tests/` | Offline software regressions using mocks and synthetic data |
| `docs/` | Operating procedure, purpose/interpretation, implementation policy and historical references |
| `experiments/polarization_locking/` (repository root) | Measured data, run-local analysis and dated development archives |

Keep hardware modules and plotting dependencies out of CLI browsing. Keep
experiment-specific scripts beside their session data until they meet the
supported test contract; do not add another top-level acquisition entry point
for routine work. Do not import experiment archives into the production package.
Avoid large structural rewrites of the working calibration/feedback collectors
without a behavior-level reason and regression coverage.

## Supported test contract

1. Implement the acquisition in `routines/` and expose a small app/mixin method.
   Add parameters to `PolarizationLockConfig`; validate before connecting.
2. Add one `TestCase` in `catalog.py`, including honest purpose, physical setup,
   relevant config groups, duration semantics, and report type. `scope_only=True`
   selects the contrast-specific session lifecycle (detectors and active/passive
   mode from configuration); ordinary tests own both RP and PAX.
3. Use the runner's output path and instrument session. Do not duplicate recipes,
   session startup, or report dispatch in an ad-hoc command. Retain partial data
   on failures and interruption, and label derived estimates and units accurately.
4. Add meaningful offline checks for physics/math, dispatch, failure cleanup,
   and any changed hardware behavior. Use representative synthetic traces;
   never connect real instruments from pytest.
5. Update the [test guide](test-guide.md), procedure if needed, and a minimal
   example recipe. Demonstrate hardware-free `--show`, `--dry-run`, and report
   regeneration before a controlled bench validation.

Do not load a full Pyrpl profile for a passive measurement: startup can apply
saved output drives. Preserve the scope-only register write guard. The PAX
adapter deliberately replaces existing local PAX daemons on Linux to reclaim
the single-owner device; keep process matching specific to PAX launch commands.
Passive PD visibility never connects PAX and therefore never runs that cleanup. Hardware
failure and cleanup failure must remain visible in the run status.

## Provenance and repeatability

Menu runs store the complete input `recipe.json`, effective configuration in
`run.json`, UTC timestamps, Python version, source SHA-256, Git revision, suite
dirty state, and relevant installed package versions. Provenance collection
does not import instrument drivers. Missing package metadata is recorded as null;
exported source without Git has null Git fields. A locally modified driver can
share a package version with another checkout: record its revision in setup
notes when applicable.

The source hash covers package `.py` and `.toml` files; it is evidence of identity,
not a copy of the code. Dirty-source runs need the corresponding source changes
preserved in version control to be reproducible. Dependency versions are recorded,
not automatically installed or pinned. Use the established bench environment and
validate driver upgrades separately. Historical runs and the separate raw
collector lack some of this metadata; never backfill guessed provenance.

Record operator/setup/calibration details in recipe notes. A replay cannot
reproduce physical placement, dark offset, drift or hardware state on its own.
Keep raw run data immutable. New interpretation belongs in a separate analysis
file or directory; regenerated PDFs and derived summaries are not raw evidence.

## Review before committing or pushing

```bash
git status --short
git diff --check
git diff --stat
git diff -- python/polarization_locking
git status --short -- experiments/polarization_locking
python -m pytest python/polarization_locking/tests
python python/polarization_locking/lock.py --list
python python/polarization_locking/lock.py --profile python/polarization_locking/profiles/visibility-in2-example.json --dry-run
```

`git diff` excludes untracked files. Review new code, recipes, documentation,
and experiment folders explicitly. Keep cache/bytecode out of commits. Keep
`console.log`, raw CSV/NPZ, metadata, reports, and validity notes together when
sharing a run. Check archive/file size before adding data; do not delete failed
runs merely to make the status shorter. Record their status in the session index.

Review source/SOP changes and experiment additions separately, then stage only
the intended paths. Commit before a reference acquisition when practical; the
reference then records a clean code revision. Pushing is a separate release action.
