# EPSCoR Project Tools — Engineering Modernization Roadmap

## A. Campaign status and purpose

**PR 0 merged; PR 1 in progress — offline regression infrastructure.** This staged campaign makes an
already functional experimental repository reproducible, maintainable, portable
and shareable as a laboratory software platform. Preserving working experiments
is the overriding constraint. The integrated baseline is `754c9e40c3ae799505a73a92aade0d15fd774799`,
following [baseline PR #1](https://github.com/tyndalestutz/epscor-project-tools/pull/1)
and [foundation PR #2](https://github.com/tyndalestutz/epscor-project-tools/pull/2).
The sequence below is revisable as evidence arrives;
it authorizes no implementation beyond the active PR.

A technically competent colleague should be able to understand the architecture,
install and validate the software, identify and configure required hardware, and
find and safely run the appropriate procedure. They should distinguish measured,
commanded, calculated and inferred quantities; recover interrupted-run artifacts;
reproduce offline analysis/reports; and add an experiment without reverse-engineering
unrelated subsystems.

The inspected snapshot is **2026-10-15**, branch **main**, commit
`3f2b9892f3e805c494f6500f8a253abbf26ef06e` (clean working tree before PR 0).
The earlier source-review reference, `main` at
`22f883a8cbd870e4b7203ea7aecc1909ff41ad76`, predates the PAX-only rough Vπ workflow
and additional shared menu/panel behavior now present. The implementation branch
is `docs/modernization-roadmap-pr0`.

Evidence labels used throughout:

- **Verified from source**: inspected implementation/documentation supports the
  stated structure or contract; this does not prove physical performance.
- **Reported by prior development/testing**: historical developer evidence,
  without independent execution in PR 0.
- **Requires execution or bench verification**: capability or assumption needing
  an actual software run or controlled physical check.
- **Proposed architectural direction**: future work, not an approved design or
  completed capability.

Sections C–D describe **Verified from source** contracts unless explicitly qualified.
Section E separates observed structure from possible consequences. Sections F–J
are **Proposed architectural direction**, including validation requirements.
[AGENTS.md](../../AGENTS.md) is persistent agent policy; this roadmap supplies
strategy and progress; the individual PR prompt supplies authorized scope.

## B. Operating principles and invariants

**Functionality preservation.** Supported commands, entry points, cataloged
experiments, saved recipes, offline analysis and report regeneration remain
usable. Intentional deprecation requires an explicit compatibility process;
recipe/schema migrations require tested paths. Existing operator workflows are
part of the contract.

**Scientific integrity.** Raw measurements are immutable evidence. Never rewrite
historical datasets, discard failures or backfill guessed metadata. Preserve units,
coordinate and phase/sign conventions, calibration assumptions, normalization,
and detector/optical reference planes. Distinguish measured voltage from RP command
voltage, and commands from register/device readback. PAX angle-derived normalized
polarization direction is not absolute Stokes or DoP-scaled polarization. Keep
measured timing, host/device timestamps, assumed delays and empirically associated
offsets distinct. Preserve anomalous and failed measurements; any requested
processing must be explicit and traceable.

**Hardware safety.** Preserve limits, initialization and defined cleanup behavior.
Passive monitoring and modes that initialize or command outputs must remain
visibly distinct. Verify routing and actuator assumptions before changing them.
Retain cleanup-failure evidence. Refactoring must not silently transfer instrument
ownership or introduce conflicting simultaneous access.

**Reproducibility.** Trace configuration, revision, instrument setup, measurement
conditions, processing and results. Separate acquisition, quality assessment,
reporting and interpretation. Successful execution alone cannot establish valid
measurements; provenance hashes identify contents but do not archive dirty source
or externally resolved instrument profiles.

**Controlled change.** Prefer small, reversible PRs and additive migrations.
Characterize high-risk behavior first. A failure or significant uncertainty stops
the affected migration until investigated. Do not combine driver upgrades with
structural changes to simplify scheduling.

## C. Current architecture map

```text
Operator CLI / versioned recipes / live GUI
  |
  +-- polarization_locking: catalog -> runner -> application session
  |                                          -> experimental routines
  |                          |                       |
  |                          +---- RP / PAX controllers <---------+
  |                          |                                   |
  |                          -> CSV / run metadata / reports      |
  |                                                              |
  +-- high_speed_polarimeter: catalog -> runner -> mock sessions   |
                                             -> hardware sessions|
                                                legacy adapter -+
                                             -> acquisition/events/raw buffers
                                             -> offline quality / reports

field_propogation (independently usable)
  raw measured inputs -> parameter construction -> freeze -> prediction
                                                    -> held-out comparison
  Jones network / detector models -> derived artifacts and reports
```

[Polarization locking](../../python/polarization_locking/README.md) is the mature
bench application. Its [catalog](../../python/polarization_locking/catalog.py)
contains 27 procedures: calibration, phase/polarization sweeps, path diagnostics,
visibility, PI/hybrid feedback, PAX monitoring, vibration and live orthogonalization.
The [runner](../../python/polarization_locking/runner.py) owns run allocation,
recipe/provenance/log/status, interruption retention, cleanup and reporting;
[app.py](../../python/polarization_locking/app.py) owns the shared session/helpers.
[Configuration](../../python/polarization_locking/config.py) supplies defaults;
[settings](../../python/polarization_locking/settings.py) validates configuration
and version-1 recipes. Interactive local settings are separate from saved recipes.
The [maintenance guide](../../python/polarization_locking/docs/maintaining.md) and
[operating procedure](../../python/polarization_locking/docs/operating-procedure.md)
define established boundaries and physical preflight responsibilities.

[High-speed polarimeter](../../python/high_speed_polarimeter/README.md) has a
[catalog](../../python/high_speed_polarimeter/catalog.py), versioned recipes,
[runner](../../python/high_speed_polarimeter/runner.py), deterministic mocks and
hardware sessions. Single/dual EOM characterization uses the
[acquisition layer](../../python/high_speed_polarimeter/acquisition/eom.py) to retain
full signed PD buffers, command events, host timing and raw PAX reference telemetry.
Its [legacy bench adapter](../../python/high_speed_polarimeter/hardware/legacy.py)
reuses the existing [RP](../../python/polarization_locking/hardware/rp_interface.py)
and [PAX](../../python/polarization_locking/hardware/pax_interface.py) controllers,
including direct configured-scope access for full traces. Changing these controllers
therefore affects both applications: this is a shared regression surface.

Actual HEAD also implements `eom_vpi_hysteresis`, a **PAX-only** forward/reverse
rough trajectory-phase Vπ estimate and direction comparison, with an undriven
baseline. Its model assumptions and command-voltage limitations are explicit;
it acquires no PD buffers. The shared PAX alignment panel is also accessible from
this suite. Neither addition establishes calibrated terminal-voltage retardance,
intrinsic hysteresis, instrument-matrix calibration or high-speed Stokes inversion.
Those scientific pipelines remain planned.

The applications have different run schemas and status conventions. Legacy runs
are primarily CSV-oriented with lowercase execution states including
`cleanup_failed`; high-speed runs use uppercase execution states, separate cleanup
and quality/report statuses, and experiment-specific events/raw artifacts.
[Independent EOM quality evaluation](../../python/high_speed_polarimeter/quality_eom.py)
checks structural evidence; structural success can leave scientific quality
`UNKNOWN`. This divergence is technical debt, not permission to rewrite both
runners or historical artifacts.

[Field modeling](../../python/field_propogation/README.md) remains independent of
live control. [Propagation](../../python/field_propogation/propagation.py) compiles
Jones-network phase coefficients for vectorized evaluation; detector/spatial models,
parameter provenance and [measured-input acquisition](../../python/field_propogation/docs/measurement_acquisition.md)
support construction, freezing, prediction and held-out comparison. Preserve that
separation and the existing `field_propogation` import spelling.

Other components include [polarization visualization](../../python/polarization_visualization/README.md)
and camera tooling, [PAX1000](../../python/PAX1000) compatibility utilities,
[random_tests](../../python/random_tests) historical exploration, and
[experiments](../../experiments/README.md) scientific records, analysis and historical
development evidence. Committed experiment artifacts are not categorically disposable.

## D. Strengths to preserve

These source-backed features are compatibility requirements:

- Hardware-free CLI discovery/dry runs, centralized legacy dispatch and validated,
  versioned recipes keep preflight separate from instrument access.
- Source/dependency provenance, partial-run retention and explicit execution,
  cleanup and reporting failures support recovery and audit.
- The RP passive scope-only path avoids ordinary output initialization and guards
  writes; passive does not mean existing physical outputs are off.
- PAX freshness checking requires advancing valid records in applicable legacy
  paths. High-speed EOM acquisition intentionally retains every returned record
  instead, exposing stale/anomalous telemetry to offline evaluation.
- The [live PAX panel](../../python/polarization_locking/routines/pax_live.py)
  separates worker sampling/connection/cleanup, independently flushed logging and
  GUI consumption/reference calculations. Additional windows do not own devices.
- High-speed deterministic sessions, event ordering, timing metadata and full
  signed buffers enable reproducible structural checks. ASG settings are explicitly
  not measured delivered voltage. Offline evaluation preserves raw evidence.
- Physics workflows separate raw input, model construction, frozen prediction and
  held-out comparison rather than fitting disagreement away.

**Requires execution or bench verification:** software contracts do not establish
current FPGA state, delivered voltages, synchronization, noise performance or
physical safe states. Mock signals establish workflow behavior only.

## E. Technical-debt and risk register

Priorities reflect experimental consequence: **P1** protects physical/scientific
contracts before affected migrations; **P2** enables reliable maintenance and
handoff. A risk is not a demonstrated current failure.

| Risk / limitation | Evidence / location | Possible consequence | Priority | Intended mitigation | Earliest phase |
| --- | --- | --- | --- | --- | --- |
| Bench-specific defaults | Legacy config: RP address, 830 nm wavelength, actuator gains and preconfirmed mapping; high-speed runner requires explicit mapping/voltage-chain/reference-plane confirmation | Portable use could inherit incorrect physical assumptions | P1 | Portable profiles preserving proven effective defaults; explicit confirmation | PR 3 characterization, PR 5 migration |
| Instrument ownership/lifecycle | RP initialization and PAX local daemon replacement in shared controllers | Concurrent access or incomplete recovery could disrupt another session; no specific concurrency bug established here | P1 | Investigate exclusive ownership, consecutive runs and failure recovery | PR 3, PR 6 |
| Physical regression exposure | RP/PAX calls, FPGA state, routing and host/device clocks; September commit `004389c` mentions RP stalling between runs | Unit tests cannot establish acquisition equivalence | P1 | Investigate historical symptom and define controlled timing/repeated-run protocol; do not assume it remains unresolved | PR 3, PR 6 |
| Shared configuration complexity | Large `PolarizationLockConfig` spans unrelated experiments | Changes may affect unexpected recipes/procedures | P1 | Characterize defaults; layered instrument/bench/experiment contracts with compatibility | PR 3, PR 5 |
| Oversized routine modules | `routines/calibration.py`, `feedback.py`, `diagnostics.py` | Extraction may alter timing, calculations or failure handling | P1 | One characterized experiment family at a time | PR 3, PR 7 |
| Divergent run formats | Two runners and EOM event/buffer schema; PAX-only variants | Consumers may confuse statuses, quantities or missing evidence | P1 | Version-aware read-only adapters/manifests before changing writers | PR 4 |
| Installation reproducibility | READMEs reference known-good `jl-env`; requirements include unpinned hardware drivers | A fresh environment may differ from the working bench | P2 | Capture known-good environment and document software-only setup; validate driver upgrades separately | PR 1 inventory, PR 2 |
| Regression enforcement | Package tests/configs exist; no tracked `.github` CI configuration found | Regressions may escape local validation | P1 | Inventory environments and establish aggregate offline checks/CI | PR 1 |
| Repository hygiene | Tracked PAX bytecode/daemon log, notebooks, supporting data and experiment logs | Bulk cleanup could erase scientific evidence | P2 | Classify archival records versus transient files; no bulk deletion of `experiments/` | PR 2 |
| Onboarding/navigation | Root README is a short index; subsystem docs are richer | A colleague may miss safe setup or analysis paths | P2 | Document installation → discovery → preflight → acquisition → offline review | PR 2, PR 8 |

**Reported by prior development/testing:** October 2026 commits `22f883a` and
`487c01b` each report 348 combined passing tests (the latter in the bench environment
with offscreen Qt). This is historical evidence, not a result executed for PR 0.
The September stall commit is an investigation lead, not proof of a current fault.

## F. Proposed PR sequence

These are planning identifiers, **not existing GitHub PR numbers**. Every phase is
bounded by the acceptance contract below. No completion dates or effort estimates
are assigned. High-risk phases should split further; evidence can revise ordering.

| Phase / scope and primary benefit | Dependencies | Principal regression risks / exclusions | Acceptance and required validation |
| --- | --- | --- | --- |
| **PR 0 — Campaign foundation and architecture baseline.** Roadmap and one agent-policy section provide shared context. | None | Inaccurate claims or implied authorization; documentation only | Accurate links, coherent risks/sequence, preserved policy; diff/link checks; no operational changes |
| **PR 1 — Unified hardware-free regression infrastructure.** Inventory commands/environments; aggregate offline workflow and appropriate CI | PR 0 | Accidental hardware/service/GUI access; false confidence from skipped tests | Documented command works offline and CI failures are visible; separate unit/integration from instrument/service/desktop operations; Tier 1 plus workflow checks |
| **PR 2 — Repository hygiene and onboarding.** Root navigation, setup, dependency/environment records and careful source-control hygiene | PR 1; earlier only for demonstrably documentation-only work | Erasing records or casually changing runtime dependencies | Fresh software-only checkout can be configured/validated from docs; scientific records intact; Tier 1, setup walkthrough and provenance review |
| **PR 3 — Characterization and regression fixtures.** Qualify representative artifacts and deterministic fixtures | PR 1 | Unrepresentative fixtures or tolerances concealing differences | Compare commands/events, schemas, interruption, failures, reports, math/conventions and derived values with justified criteria; Tiers 1–2 and define Tier 3 baselines where needed |
| **PR 4 — Common read-only run interfaces.** Compatible readers/manifests simplify inspection | PR 3 | Schema/status conflation or mutation; exclude writer replacement and historical conversion | Tested version-aware inspection of supported existing formats without source mutation; Tiers 1–2 |
| **PR 5 — Configuration and portable bench profiles.** Separate instrument, bench, calibration and experiment settings where useful | PR 3; informed by PR 4 | Changed effective defaults, units or recipe interpretation | Historical recipe behavior preserved; changed settings explicit; portable profiles require physical confirmation; Tiers 1–2 and Tier 3 for effective hardware changes |
| **PR 6 — Instrument lifecycle/resource ownership.** Investigate startup, teardown, restoration, daemon recovery and repeated runs; harden incrementally | PR 3 and relevant configuration contracts | Output state, device ownership and recovery changes | Fault injection and authorized physical checks demonstrate intended connection/output/cleanup/recovery behavior; all three tiers |
| **PR 7 — Incremental experimental decomposition.** Extract characterized families/calculations in multiple small PRs | PRs 3–6 as relevant | Timing, numerics, dispatch and operator workflow drift | Each extraction independently passes relevant regression and necessary bench comparison; Tiers 1–2, Tier 3 where physically consequential |
| **PR 8 — Usability and operational handoff.** Discovery, preflight, navigation, browsing and diagnostics | Stable interfaces and verified core behavior | Concealed warnings or changed working interfaces | New colleague completes documented software-only workflow and understands authorized bench workflow/physical checks; Tiers 1–2 and supervised Tier 3 as applicable |

## G. Regression and validation strategy

**Tier 1 — Software regression:** hardware-free configuration, CLI/dispatch,
import behavior, numerical functions, interruption, cleanup and report logic.
Existing starting points, to qualify and aggregate in PR 1, are:

```bash
python -m pytest python/polarization_locking/tests
python -m pytest python/high_speed_polarimeter/tests
PYTHONPATH=python python -m unittest discover -s python/field_propogation/tests -v
```

The two application packages have `pytest.ini`, `requirements.txt` and
`requirements-dev.txt`; the field package documents its Python/LaTeX dependencies
in its README. Required environments, optional Qt/report dependencies and test
isolation need execution verification. These commands were **not run in PR 0**.
Do not install/upgrade drivers merely to execute documentation checks.

**Tier 2 — Artifact and experiment regression:** deterministic mocks and selected,
qualified reference artifacts test schemas, raw retention, timestamps/event order,
derived quantities and offline reconstruction/reporting where implemented.
Historical datasets are not universal golden references: specify each fixture's
conditions, limitations, provenance and the behavior it can establish.

**Tier 3 — Physical bench validation:** controlled, authorized checks of actual
initialization, safe outputs, timing, repeated acquisition, routing, recovery and
measurement performance. This tier is unsuitable for ordinary CI and requires
physical setup records and explicit acceptance criteria.

Use **exact equivalence** for schemas, command ordering, hardware-state contracts,
retained raw values and discrete invariants; **numerical equivalence** with documented,
scientifically justified tolerances for derived quantities; and **physical equivalence**
with defined experiments/criteria for timing, noise, feedback and device effects.
Distinguish invariant structure from inherently variable timestamps. A mock cannot
establish physical equivalence, and passing software tests does not establish
measurement validity.

## H. Standard PR acceptance contract

Every campaign PR identifies:

1. Bounded objective and motivating problem.
2. Expected files/interfaces to change.
3. Protected functionality and excluded scope.
4. New or updated regression evidence.
5. Compatibility considerations for commands, recipes, schemas and defaults.
6. Test commands actually executed and their results, including failures/skips.
7. Remaining hardware-verification requirements.
8. Changes to workflows or data interpretation.
9. Rollback considerations for consequential changes.
10. New risks, follow-up issues and roadmap decisions.

Changes affecting acquisition timing, hardware state, physical mapping, calibration
or scientific interpretation require an explicit risk review and an approved
physical-verification strategy before the affected migration proceeds. Define who
reviews the protocol in that PR; roadmap listing alone is not approval. “Existing
tests pass” cannot replace a meaningful assessment of scientific/hardware regression.

## I. Architectural non-goals

Initial constraints exclude wholesale rewriting of `polarization_locking`, immediate
runner unification, forced historical-format conversion, speculative driver
replacement, and default renaming of `field_propogation` or other import paths.
Keep the physics engine independently usable rather than coupled to live acquisition.
Defer GUI rewrites until behavioral contracts stabilize. Never automatically “fix”
discrepant evidence with fitting, smoothing or exclusions. Do not bundle broad
dependency upgrades with refactors or infer scientific performance from deterministic
mock signals. These constraints permit individually justified future proposals;
they do not authorize them now.

## J. Open architectural decisions

All entries initially have status **Open / Not yet decided**. Candidate directions
above are not approved designs.

| Decision | Evidence needed | Likely phase |
| --- | --- | --- |
| Archival datasets versus transient generated files | Record ownership, reproduction sources, retention needs and tracked-artifact inventory | PR 2 |
| Shared run-reader model/version compatibility | Representative legacy/EOM/PAX-only schemas, consumer needs and failure cases | PR 4 |
| Bench-profile format/calibration-record ownership | Historical recipes, effective-default comparisons, units and calibration provenance | PR 5 |
| Cross-application instrument locking | Actual access paths, exclusive-use requirements and fault/recovery behavior | PR 6 |
| PAX daemon lifecycle/recovery policy | Local/remote startup, replacement, teardown and interrupted-session characterization | PR 6 |
| RP/PAX timing protection | Clock-domain inventory, event traces, FPGA state and independent timing measurements | PR 3, PR 6 |
| Minimum bench regression protocol | Representative modes, safe bounds, repeatability and measurable acceptance criteria | PR 3, refined in PR 6 |
| High-speed calibration/reconstruction interface | Independently measured EOM/analyzer response, reference-plane/timing characterization, held-out states and acquisition contracts | Separate future scientific proposal informed by PRs 3–6; not implemented by this campaign plan |

## K. Progress ledger and maintenance

Blank links mean no actual GitHub PR is recorded. Planned entries are unimplemented.

| Planning ID | Objective | Status | GitHub PR | Notes / blockers |
| --- | --- | --- | --- | --- |
| PR 0 | Campaign foundation | Merged | [#2](https://github.com/tyndalestutz/epscor-project-tools/pull/2) | Integrated after baseline PR #1; merge `754c9e4` |
| PR 1 | Offline regression infrastructure | In progress | | [Offline testing guide](offline-testing.md); verify clean-environment execution, Actions and failure propagation before completion |
| PR 2 | Hygiene and onboarding | Planned | | Classify records; qualify environment |
| PR 3 | Characterization/fixtures | Planned | | Select evidence and physical protocols |
| PR 4 | Read-only run interfaces | Planned | | Requires qualified schemas/fixtures |
| PR 5 | Portable configuration | Planned | | Preserve effective recipe behavior |
| PR 6 | Lifecycle/ownership | Planned | | Bench access and approved protocol required |
| PR 7 | Routine decomposition | Planned | | Per-family regression gates |
| PR 8 | Operational handoff | Planned | | Stable verified contracts |

Update this document when a campaign PR merges or a significant architectural
decision changes. Link actual merged PRs; record material deviations, new risks
and dependencies. Keep detailed implementation discussion in individual PRs.
Preserve superseded decisions with a short explanatory note when needed, without
turning the roadmap into an exhaustive diary. Never mark a phase complete merely
because its plan is documented. PR 0 ends with shared engineering context and
controlled scope; PR 1 requires its own authorization.
