# EPSCoR Project Tools — Engineering Modernization Roadmap

## A. Campaign status and purpose

**First notebook drive/capture (2026-10-27, draft ready for operator review):** the user authorized
editing the EOM characterization notebook, its global settings/smoke tests and
the shared wrapper. The confirmed path is OUT1 → preamp → 3211 → EOM, with
PD/IN1/LV and drive-monitor/IN2/HV; OUT2 remains unused. Scope is one bounded sine
and one raw paired buffer, configuration/preflight, minimal retention/inspection,
and an additive active context manager. Existing suites and standalone raw tool
remain intact; their pure voltage-envelope/sample-coverage/retention checks are
reused in the notebook. Unknown physical bounds stay unset and block execution.
The caller explicitly approves the downstream model and protection; command and
register settings are not measured HV. No automated sweep, fitting or reports.

The user also reported `Address already in use` on passive-monitor port 2223.
Default notebook attachment now tries distinct owned ports on that specific bind
failure; it does not attach to or kill the unknown listener. Explicit ports and
legacy-controller port behavior stay strict. The installed monitor client's
suppressed-close-error path is handled by explicitly closing its socket. The
traceback does not establish the listener's owner. Bench checks must cover passive
reconnection, active startup/cleanup, waveform transitions, frozen paired buffers,
actual voltage/timing and the assumed engineering monitor conversion. No agent
hardware access is authorized or used for this increment.
Software evidence: `scripts/test-offline.sh` passed 602 checks in a clean tracked
export with these draft changes overlaid (75 instrument, 362 locking, 100
high-speed, 57 field-model, 8 boundary). The unchanged standalone EOM tool's two
mock storage/interruption tests also passed. The actual saved notebook code was
compiled/schema-validated and executed with fake backends for environment/global
settings, RP and owned-PAX smoke tests, successful acquisition/inspection, clipping
retention and partial-read retention; incomplete bench settings were confirmed to
stop before connection. These checks do not establish real instrument operation.
Changes remain local and uncommitted for the experimentalist's review; physical
bounds, conversion evidence and bench qualification are outstanding.

Notebook follow-up: the operator confirmed zero OUT1 bias, an absolute RP command
ceiling of 0.9 V, nominal 3211 gain ×40, monitor conversion ×30 and the existing
±5 V amplifier-input / ±200 V electrode / ±6 V monitor windows. These are entered;
remaining nominal offsets, polarity and engineering margins reuse the raw-tool
recipe and stay explicitly subject to physical confirmation. The requested
0.3 V-peak, 0.5 Hz sine previews as 0.4656612873 Hz and four cycles in one buffer.
Preflight checks the requested waveform and zero cleanup against downstream bounds
rather than requiring unused RP ceiling endpoints to fit them. Missing fields and
physical declarations are reported together, impossible timing is rejected before
connection, and acquisition displays frequency readback and capture timing. Pure
checks cover the present command, downstream-bound rejection and impossible cycle
counts; fake-backend notebook acquisition/retention/cleanup checks pass. No new
bench evidence is claimed; operator physical declarations remain unchecked.

**Authorized command-to-monitor bench characterization (2026-10-27):** the operator
subsequently set the notebook declarations and explicitly authorized agent RP
operation. One bounded session retained 17 DC and four sine paired captures in
`experiments/_local/command_monitor_calibration_20261027T201314Z_b468db58`
(`raw.npz` and `metadata.json`, 843,774 bytes total). Command extrema were limited
to ±0.3 V; sine readback was 0.4656612873 Hz. A least-squares fit to all 17 DC
capture means gives IN2 = 9.6442074113 × OUT1-command − 0.0681210879 V, using
the declared IN2 HV scaling. Maximum absolute DC mean residual was 4.155 mV;
matched increasing/decreasing steps differed by at most 1.419 mV. Sine gains were
9.6421–9.6581 V/V for 0.1–0.3 V requested peaks. All captures passed existing
checks; cleanup read both ASGs DC zero with routes off. This is combined bench
command-to-monitor evidence, not independent OUT1 connector, electrode-voltage,
continuous-hysteresis, wideband or optical-stability qualification. The nominal
monitor-to-electrode conversion is unchanged. The notebook now snapshots these
empirical command-to-monitor coefficients, tested range/frequency and source run
with each acquisition. Inspection reports a sine-fit comparison within that scope
and skips prediction outside it; raw traces and engineering limits are preserved.

**Hysteresis check before Vπ measurement (2026-10-27):** the operator requested a
simple forward/reverse DC acquisition, then explicitly requested longer captures.
The notebook now exposes ±0.3 V, 31 points per direction, a requested 0.5 s bias
ramp, 2 s settling and 2.147483648 s paired capture per point (decimation 16384).
An additive `set_dc_offset()` preserves the enabled DC route between points;
the original waveform setter and existing suites retain their behavior. DC
preflight reuses the bench declarations/model without depending on sine settings.
One two-file run bundle retains all raw buffers, commands, settings, calibration,
source and failure/partial-read evidence. By operator clarification, a separate
inspection cell plots PD capture means against saved OUT1 command readbacks, with
within-capture PD standard deviations; IN2 remains a recorded drive monitor. The
±0.3 V window is an interim guard rail, not the Vπ search range. A dedicated Vπ
measurement will be designed after reviewing hysteresis; the operator's approximate
0.8 V expectation is unverified. No automatic
Vπ/hysteresis fit or absolute electrode calibration is claimed. All 79 instrument
software tests passed, including DC route continuity and interruption checks.
Fake-backend notebook checks cover successful scans/plots, clipping, partial reads,
interruptions, post-read failure retention and rejection before connection. This
operator-interrupted first stepped run retained 27 complete increasing-bias
captures and zero/off cleanup readbacks. IN2 followed the previous calibration
within 3.72 mV, while PD means spanned 28.7 mV; the reverse branch was not reached.
Physical settling, optical drift and any branch differences remain bench questions.

The subsequent complete 62-capture run
`vpi_scan_20261027T205108Z_b6bf2a6e` retained unclipped paired data and zero/off
cleanup readbacks. Matching OUT1 settings showed PD branch differences up to
3.420 mV versus monitor differences up to 1.895 mV; sampled PD minima were near
−0.18 V increasing and −0.22 V decreasing. This is branch dependence, not evidence
isolating intrinsic hysteresis from drift/settling. Broader fringe discovery is
now drafted, with the operator explicitly retaining the 3211's 10 Vpp input limit.
The existing preamp gain/margins permit about ±0.506 V RP; the chosen discovery
settings are ±0.5 V, 51 points per direction with unchanged longer timing.
The notebook shares one visible DC acquisition/plot implementation between the
two scans, retains interrupted-run inspection and per-run code/config provenance,
and adds a manual read-off estimate from adjacent interior extrema on one branch.
Unresolved endpoints are rejected; no phase target or Vπ value is assumed.
An excursion of π then back differs from 2π coverage in each direction, which
requires 2Vπ of voltage span. The ±0.3 V command-to-monitor calibration remains
limited to its tested range; discovery does not extend its calibration claim.
Fake-backend checks pass for both scan widths, saved plots, boundary rejection,
fault/interrupt retention and a known synthetic read-off interval. The wider
discovery scan has not been run on hardware; no independent electrode calibration
or intrinsic hysteresis conclusion is claimed.

**Cycling Vπ convergence (2026-10-27, supersedes the notebook's active DC steps):**
the operator requested an adaptive near-1 Hz sine calibration instead of the slow
stepped discovery. Existing saved DC runs and device APIs remain intact. The
notebook now grows a sine from 0.1 V peak inside a combined bias/peak ±0.5 V RP
window, uses actual frequency readback (nominally 1.0477378964 Hz), and captures
8.589934592 s per trial after 2 s settling. Resolved adjacent interior extrema on
both directions trigger recentering and 20% overshoot. Convergence requires three
consecutive usable per-direction estimates spanning at most 0.01 V; directions
remain separate. Limits, poor visibility, distortion or insufficient repeatability
do not become a converged result. A missing/weak IN2 response stops the experiment.

Analysis explicitly fits the IN2 sine phase, constructs an RP-equivalent voltage
coordinate from ASG amplitude (assuming positive linear quasi-static transfer),
averages PD into 64 voltage bins per direction and locally interpolates extrema
with quadratics. A sinusoidal-fringe residual check gates usable spacings. This
is a model-dependent operational estimate, not measured instantaneous OUT1,
independent electrode calibration or absolute phase accuracy. PD is not shifted
to force branch agreement. Trial settings, processing/code, raw signed buffers,
inferred curves and failure/partial-read evidence are retained in one run bundle.
The old DC notebook execution blocks are replaced to avoid both protocols running
on Run All. No device-wrapper or suite behavior changes accompany this increment.

Ten fake-backend scenarios passed: known convergence, distinct stable branches,
flat PD, unresolved range, drift, missing monitor, clipping, partial read,
interruption and rejection before connection. Known synthetic half-wave intervals
were recovered; branch differences were preserved. Schema/compilation checks pass;
the saved real 0.3 V sine was inspected with the estimator without acquiring new
data. No cycling-calibration hardware run or convergence result is claimed yet.

**Owned PAX notebook sessions (2026-10-27):** following notebook connection tests,
the user requested that PAX startup and shutdown happen entirely within the
notebook and not leave a daemon/motor running between operations. The bounded
follow-up adds `PAX.local_session()` as a context manager around an owned,
supervised child using the existing project daemon and config. It retains the
five-second spin-up settling wait, verifies endpoint ownership, preserves raw
acquisition behavior, and requests motor/USB/process cleanup on block exit and
parent-pipe EOF. Existing attach-only and legacy suite behavior remain available.
Tests must cover startup/body/cleanup failures, interruption and repeated sessions;
the offline guard must block the new launcher. Physical motor stop, USB release,
kernel-exit recovery and timing require bench qualification. Notebook cells and
source experiment data are excluded from implementation changes. This extends
the earlier increment's attach-only scope by explicit user request; it is not a
general lifecycle or suite migration.
Software validation: 579 checks passed in a clean exported checkout with this
increment overlaid (52 instrument, 362 locking, 100 high-speed, 57 field-model and
8 boundary checks). The instrument suite adds 21 fake-process/USB lifecycle cases.
No hardware was contacted; physical shutdown and kernel-exit behavior remain bench
checks, not software-test conclusions.

**Notebook-first increment (2026-10-26):** the user authorized an additive shared
`python/instruments` entry point on `feat/notebook-instrument-wrappers`, based on
`06d26f81a24fd2cfd29e932c2611103209b6669c`. This is a bounded exception to the phase
sequence, motivated by interactive device programming; it does not authorize a
suite-wide migration or the rest of phases 5–8. The experimentalist authors and
executes notebook steps; existing suites remain supported. Scope: monitor-only RP
attachment/readback/close and PAX attachment to an existing daemon, raw/fresh reads,
explicit wavelength and client-only close. Shared RP transport and PAX
parsing/validity are extracted behind existing controller imports; active suite
initialization, recipes, acquisition timing defaults and reports remain in place.
Run provenance includes the new shared source. See the
[instrument contracts and bench checks](../../python/instruments/README.md).
Acceptance: driver-free construction/imports; explicit side effects and ownership;
raw anomaly retention; fault/repetition checks; existing offline suites pass.
Physical equivalence, remote monitor-process release and timing remain unverified
until authorized bench checks. No notebook edits, active notebook RP drive API,
driver upgrades, generalized instrument framework or automatic reports are in scope.
Software validation: 558 checks passed using `scripts/test-offline.sh` in a clean
exported baseline plus this increment (31 instrument, 362 locking, 100 high-speed,
57 field-model and 8 boundary checks). The direct workspace run encounters an
existing high-speed menu failure from ignored local settings; the same failure
was reproduced on unmodified base source with those settings. User settings were
left intact. Bench checks remain outstanding.

**PRs 0–3 merged; PR 4 in progress — bounded read-only text reader increment.** This staged campaign makes an
already functional experimental repository reproducible, maintainable, portable
and shareable as a laboratory software platform. Preserving working experiments
is the overriding constraint. The verified integrated starting point for PR 4 is `0a6f70ac9924621698937ee367dc3788e9cd6088`,
following [baseline PR #1](https://github.com/tyndalestutz/epscor-project-tools/pull/1),
[foundation PR #2](https://github.com/tyndalestutz/epscor-project-tools/pull/2),
[offline regression PR #3](https://github.com/tyndalestutz/epscor-project-tools/pull/3),
[hygiene PR #4](https://github.com/tyndalestutz/epscor-project-tools/pull/4),
and [characterization PR #5](https://github.com/tyndalestutz/epscor-project-tools/pull/5).
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

## F.1. Refined execution checkpoints (2026-10-16)

This refinement preserves the phase sequence above. The checkpoint inspected
main `72590f5`; it is a plan, not authorization for later implementation.
Phases 0–3 and Phase 4 increments 1/4B are merged (#7/#8); current audit base
is `bc305a7`. CI/setup and root navigation exist; section E records the original
baseline. PR #8 review CI passed 527 tests on its generated merge ref, not a
separate post-merge run. Physical qualification and archive provisioning remain open.

| Next checkpoint | Bounded work and simplification | Exit evidence |
| --- | --- | --- |
| **4B — Second reader consumer (merged #8)** | Adopt existing CSV ingestion in `reports/plot_continuous_cross_sweep.py`; keep its typed projection, numerical/report behavior and standalone CSV inputs. Remove replaced ingestion/import only; no reader API expansion. | Compare two saved schema variants and synthetic edge cases before/after; distinguish parsing rejection from existing missing-value handling; verify report failure propagation, source immutability and offline CI. |
| **4C — Coverage and completion decision (in progress)** | Reconcile the existing format inventory with actually tested support and remaining consumers. Two locking consumers do not establish cross-application semantic support. Prioritize any necessary metadata/status/partial-run cases before new decoders. | Each family has evidence, support level and an explicit retained/deferred decision. Agree the finite Phase 4 boundary; no silent exclusion or automatic promise to implement every decoder. |
| **5A / 5B — Characterize, then separate configuration** | Map defaults and precedence across CLI, saved recipes, local settings and bench inputs; then migrate one bounded group using existing recipe machinery. Avoid a parallel configuration framework. | Old recipes resolve to the same effective values; units, calibration/reference ownership and overrides explicit. Physical changes need separate bench checks. |
| **6A / 6B — Characterize, then harden ownership** | Trace shared RP/PAX startup, partial connection, passive/active modes, interruption, daemon ownership and cleanup; fix one demonstrated gap at a time. | Mock fault/repetition coverage plus an approved, executed bench protocol for physical claims. Preserve distinct runner statuses until a compatibility contract justifies change. |
| **7 — Decompose characterized families** | Extract one cohesive family or calculation; migrate its callers and remove replaced private code when proven redundant. Preserve supported entry points and scientific algorithms. Review provenance coverage of shared source outside package roots. | Behavioral equivalence, relevant physical checks and demonstrably fewer responsibilities/duplicate paths; small helpers need a real consumer. |
| **8 and later design work** | Validate colleague onboarding and operational workflow; then scope experiment, side-tool, processing and report templates around stabilized contracts. User-facing redesign, report content/LaTeX standards and pipeline reorganization require their own design decisions. | Demonstrated workflow and explicit user agreement; templates reuse established services rather than copy complete implementations. |

Phase 4B merged as [#8](https://github.com/tyndalestutz/epscor-project-tools/pull/8),
`bc305a7`, preserving typed projection and report failure boundaries. Phase 4C
[reconciles every inventoried family](experiment-reader.md) and recommends boundary
A: native metadata/text access, documented structural recognition and the two
qualified consumers are sufficient after explicit review accepts all listed
deferrals. No blocking production follow-up is identified for that boundary.
Phase 4 stays in progress pending the completion decision; Phase 5A is not started.

Simplicity checks apply to every increment: name the existing consumer, identify
what duplicated responsibility is removed, justify every new abstraction and
report compatibility effects. Count reduced responsibilities and clearer failure
paths, not lines alone. Prefer existing tests/docs over parallel scaffolding.
Do not duplicate the format inventory or add another campaign tracking system.

Keep hygiene separate from scientific migration: tracked bytecode and the legacy
daemon log need a narrow ownership/reference review; historical run bundles are
not disposable bloat. Archive removal requires retained copies and verified
restoration. Deferred items stay visible in the existing inventory/ledger.

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

Except for the retention policy below, entries remain **Open / Not yet decided**.
Candidate directions above are not approved designs.

| Decision | Evidence needed | Likely phase |
| --- | --- | --- |
| Archival datasets versus transient generated files | [Inventory](data-inventory.md) and [retention policy](data-retention.md) define future Git/archive handling and optional `experiments/_local/`; existing records/defaults stay intact. Archive ownership/location, replication, recovery and migration authorization remain open. | Policy in PR 2; migration separate |
| Shared run-reader model/version compatibility | Native metadata/text boundary and per-family retained/deferred evidence in [reader coverage](experiment-reader.md); completion boundary A awaits review. Full semantic/binary adapters need an actual consumer and integrity contract. | PR 4C decision pending |
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
| PR 1 | Offline regression infrastructure | Merged | [#3](https://github.com/tyndalestutz/epscor-project-tools/pull/3) | Merge `5827091`; [offline testing guide](offline-testing.md), 445 software checks and failure-propagation evidence |
| PR 2 | Hygiene and onboarding | Merged | [#4](https://github.com/tyndalestutz/epscor-project-tools/pull/4) | Merge `42e8d61`; retention policy and optional local workspace; no historical migration |
| PR 3 | Characterization/fixtures | Merged | [#5](https://github.com/tyndalestutz/epscor-project-tools/pull/5) | Merge `0a6f70a`; [coverage/fixtures](scientific-regression.md) and [bench requirements](bench-regression-checklist.md); synthetic software evidence, not bench validation |
| PR 4 | Read-only run interfaces | In progress / completion decision pending | [#7](https://github.com/tyndalestutz/epscor-project-tools/pull/7), [#8](https://github.com/tyndalestutz/epscor-project-tools/pull/8) | Increment 1 merged `d037a04`; 4B merged `bc305a7`. [Coverage and proposed boundary A](experiment-reader.md): review explicit deferrals before closing Phase 4; no new decoder/consumer required by this proposal |
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
