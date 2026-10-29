# EPSCoR Project Tools — Modernization Roadmap

Goal: make this working lab repository reproducible, maintainable and usable by a
colleague, **without breaking working experiments**. Small, reversible PRs; one
bounded objective each. This roadmap is strategy and status. Agent rules are in
[AGENTS.md](../../AGENTS.md); the active PR defines authorized scope.

Earlier versions of this file kept increment-by-increment notes. Read them in
history: [roadmap before distillation](https://github.com/tyndalestutz/epscor-project-tools/blob/8e2c24d/docs/engineering/refactor-roadmap.md).

## Status (2026-10-29)

| Phase | Objective | Status | PRs |
| --- | --- | --- | --- |
| 0 | Roadmap and agent policy | Merged | [#1](https://github.com/tyndalestutz/epscor-project-tools/pull/1) baseline, [#2](https://github.com/tyndalestutz/epscor-project-tools/pull/2) |
| 1 | Hardware-free regression tests + CI | Merged | [#3](https://github.com/tyndalestutz/epscor-project-tools/pull/3) — [offline testing](offline-testing.md) |
| 2 | Hygiene, retention policy, onboarding | Merged | [#4](https://github.com/tyndalestutz/epscor-project-tools/pull/4) — [inventory](data-inventory.md), [retention](data-retention.md) |
| 3 | Scientific regression fixtures | Merged | [#5](https://github.com/tyndalestutz/epscor-project-tools/pull/5) — [fixtures](scientific-regression.md), [bench checklist](bench-regression-checklist.md) |
| 4 | Read-only run reader | **Dropped** 2026-10-29: reader removed, its two consumers restored | [#7](https://github.com/tyndalestutz/epscor-project-tools/pull/7), [#8](https://github.com/tyndalestutz/epscor-project-tools/pull/8), [#9](https://github.com/tyndalestutz/epscor-project-tools/pull/9) (reverted) |
| — | Notebook instrument wrappers (added early, below) | Merged | [#12](https://github.com/tyndalestutz/epscor-project-tools/pull/12) |
| 5 | Portable configuration / bench profiles | **Next: 5A** | |
| 6 | Instrument lifecycle and ownership | Not started | |
| 7 | Decompose large routine modules | Not started | |
| 8 | Colleague onboarding and handoff | Not started | |

- Offline suite: 549 passed (8 boundary, 82 instruments, 302 locking, 100
  high-speed, 57 field), 2026-10-29. Software evidence only.
- Phase 4 dropped by the scientist: reading run folders is simple enough to do
  directly (or with an agent) and can be templated later. The cross-sweep timing
  audit and continuous report read their files as they did before #7/#8.
- Closed without merging: [#10](https://github.com/tyndalestutz/epscor-project-tools/pull/10)
  (automated EOM qualification/adaptive Vπ), superseded by the notebook approach.

### Notebook instruments (added early)

The plan put instrument changes in phase 6. By the scientist's request,
`python/instruments` was added now so notebooks can drive and read the Red Pitaya
and PAX directly. It is a new path beside the existing suites, which are
unchanged and not migrated to it. See the [instrument README](../../python/instruments/README.md).

- Adds RP attach/readback/waveforms (sine, square, DC, frequency-only update,
  FPGA network sweep), owned PAX sessions (`PAX.local_session()`) and a standalone
  paired raw EOM capture. Existing suites and entry points are unchanged.
- Bench results live in the day records, not here. Current one:
  [2026-10-28 EOM summary](../../experiments/high_speed_polarimeter/2026-10-28/SUMMARY.md).
- Still unverified on the bench: PAX motor/USB release on kernel exit, passive
  RP reconnection and remote monitor release, and delivered electrode voltage
  (only one DMM point, ≈ 406 V/V).

## Rules that apply to every phase

Full policy is in [AGENTS.md](../../AGENTS.md). For refactoring specifically:

- **Preserve behavior.** Commands, catalog entries, recipes, schemas, defaults,
  offline analysis and report regeneration keep working. Deprecation needs an
  explicit plan.
- **Raw data is immutable.** No rewriting historical runs, discarding failures or
  backfilling guessed metadata. Commanded ≠ delivered voltage; register readback ≠
  measurement.
- **Hardware safety.** Keep limits, initialization and cleanup. Passive and
  output-commanding modes stay visibly distinct.
- **Characterize before changing** anything touching timing, mapping, actuator
  commands, calibration, raw data or cleanup.
- **No bundling.** Driver upgrades, refactors and scientific changes go in
  separate PRs.

## Architecture

```text
polarization_locking    catalog -> runner -> app session -> routines
  (mature bench app)          |                    |
                              +--- RP / PAX controllers <---+
                              -> CSV, metadata, reports      |
high_speed_polarimeter  catalog -> runner -> mock/hardware   |
                              legacy adapter ----------------+
                              -> events, raw buffers, quality, reports
instruments             notebook RP/PAX wrappers (shares RP transport, PAX parsing)
field_propogation       Jones/Stokes models: raw inputs -> freeze -> predict -> held-out compare
```

- **polarization_locking:** 27 cataloged procedures; runner owns run folders,
  recipes, provenance, interruption retention and cleanup. See its
  [README](../../python/polarization_locking/README.md) and
  [maintenance guide](../../python/polarization_locking/docs/maintaining.md).
- **high_speed_polarimeter:** EOM characterization with full signed PD buffers,
  command events and host timing; reuses the locking app's RP/PAX controllers,
  so those controllers are a **shared regression surface**.
  [README](../../python/high_speed_polarimeter/README.md).
- **field_propogation:** independent of live control. Keep it that way, and keep
  the import spelling. [README](../../python/field_propogation/README.md).
- The two runners use different run schemas and status conventions (lowercase vs
  uppercase states). That is debt, not permission to rewrite either.

Worth keeping: hardware-free dry runs, provenance and partial-run retention,
the passive RP scope path, deterministic mocks, and the raw → freeze → predict
separation in the physics models.

## Risks

| Risk | Where | Mitigation | Phase |
| --- | --- | --- | --- |
| Bench-specific defaults (RP address, 830 nm, gains, mapping) | `PolarizationLockConfig`, high-speed runner | Portable profiles that reproduce effective defaults; physical confirmation | 5 |
| Shared config spans unrelated experiments | `PolarizationLockConfig` | Layered instrument/bench/experiment settings, old recipes resolve identically | 5 |
| Instrument ownership / concurrent access | RP init, PAX daemon replacement | Characterize startup, interruption, cleanup; fix one gap at a time | 6 |
| Timing and repeated-run behavior | RP/PAX calls, FPGA state, clocks; Sept. stall (`004389c`) | Controlled timing/repeat protocol on the bench | 6 |
| Oversized routine modules | `routines/calibration.py`, `feedback.py`, `diagnostics.py` | Extract one characterized family at a time | 7 |
| Unpinned bench drivers | `polarization_locking/requirements.txt` | Record `jl-env`; upgrade drivers separately | 5–6 |
| Large tracked data (≈ 250 MB in `experiments/`) | 22–32 MB CSV/NPZ files | Backed-up archive decision; size alone never justifies deletion | open |
| Bench daemon log grows unbounded | `polarization_locking/hardware/pax1000-daemon.log` (ignored; was 236 MB) | Rotate or cap in Phase 6 | 6 |

## Next phases

- **5A — Map configuration (next).** Read-only: list where each setting comes
  from (code defaults, CLI, saved recipes, local settings, notebook settings
  cells) and which bench facts (RP address, wavelength, gains, channel map) are
  hard-coded. No code changes. Exit: one short table the scientist agrees with.
- **5B — Configuration.** Migrate one group using existing recipe machinery.
  Exit: old recipes resolve to the same effective values.
- **6 — Lifecycle.** Trace RP/PAX startup, partial connection, interruption, daemon
  ownership and cleanup. Exit: mock fault tests plus an approved, executed bench
  protocol.
- **7 — Decomposition.** One cohesive family per PR; remove replaced code. Exit:
  behavioral equivalence plus the relevant bench checks.
- **8 — Handoff.** A colleague completes the documented software workflow and
  understands the authorized bench workflow.

Each increment must name its real consumer, say what duplication it removes and
justify any new abstraction. Fewer responsibilities, not fewer lines.

## Validation

- **Tier 1, software:** `bash scripts/test-offline.sh` (runs in CI on PRs).
- **Tier 2, artifacts:** deterministic mocks and qualified saved runs; each
  fixture states what it can and cannot establish.
- **Tier 3, bench:** authorized physical checks with setup records and acceptance
  criteria. Never in CI.

Exact equivalence for schemas, command order and raw values; justified tolerances
for derived numbers; defined experiments for physical behavior. Mocks never
establish physical equivalence.

## PR checklist

Every campaign PR states: objective; files/interfaces touched; protected and
excluded scope; tests actually run and their results; compatibility effects;
remaining bench checks; rollback notes for consequential changes. Changes to
timing, hardware state, mapping, calibration or interpretation need a risk review
and an agreed bench-verification plan first.

## Non-goals

- Rewriting `polarization_locking` or unifying the runners now.
- Converting historical run formats.
- Renaming `field_propogation` or other import paths.
- GUI rewrites before contracts stabilize.
- Speculative driver replacement or broad dependency upgrades.

## Open decisions

| Decision | Needs | Phase |
| --- | --- | --- |
| Archive location/ownership for large data | Backups, restore test, migration approval | open |
| Bench-profile format and calibration-record ownership | Recipe/default comparison, units, provenance | 5 |
| Cross-application instrument locking | Real access paths, fault/recovery behavior | 6 |
| PAX daemon lifecycle and recovery | Startup/teardown/interruption characterization | 6 |
| RP/PAX timing protection | Clock inventory, event traces, independent timing | 6 |
| Minimum bench regression protocol | Representative modes, safe bounds, criteria | 6 |
| High-speed calibration/reconstruction | Independent EOM/analyzer response measurements | separate scientific proposal |

Update this file when a phase PR merges or a decision changes. Link PRs; keep
details in the PRs and day records.
