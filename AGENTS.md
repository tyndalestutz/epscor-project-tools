# Repository Agent Policy

This repository controls, acquires, analyzes, models, and reports laboratory
polarization/interferometry experiments involving Red Pitaya hardware,
piezo-driven interferometers, Thorlabs PAX measurements, Stokes/Poincare
analysis, and related optical diagnostics.

Priorities, in order:

1. Scientific and data integrity
2. Correctness and reproducibility
3. Workflow consistency with the existing repository
4. Clear, user-friendly presentation
5. Token / model efficiency

Never sacrifice the first three merely to reduce usage or make output look cleaner.

---

## Root-agent responsibility

The root agent should retain tasks involving:

- experimental interpretation,
- model/measurement discrepancies,
- Jones/Mueller/Stokes/Poincare reasoning,
- Red Pitaya/PAX synchronization,
- acquisition timing,
- hardware-control semantics,
- calibration,
- actuator behavior,
- numerical validity,
- changes that could alter experimental conclusions or source-data meaning.

Do not delegate these merely because they are large.

For ordinary implementation that is already well specified, delegation is allowed
when it saves meaningful work without forcing the subagent to rediscover large
amounts of repository context.

---

## Delegation policy

Subagents are an optimization tool, not the default execution path.

Use `cheap` for bounded, low-risk mechanical work such as:

- documentation,
- labels/formatting,
- simple plotting changes,
- straightforward config/schema propagation,
- symbol/call-site lookup,
- boilerplate and repetitive edits.

Use `reviewer` for:

- independent implementation review,
- regression/test review,
- data/provenance checks,
- verifying that a refactor preserved intended behavior.

Use the default implementation subagent for clearly specified, separable
implementation work that is nontrivial but does not require scientific judgment.

Do NOT delegate when:

- the root already has the needed context and can make the change directly,
- the subagent would need to reread large parts of the repository,
- the task is tightly sequential,
- several agents would duplicate the same investigation,
- scientific interpretation or experimental validity is central.

When delegating, give the smallest sufficient context and point to specific
files/functions. Optimize total useful work per token, not number of delegated tasks.

---

## Reasoning/model escalation

Use the selected root model efficiently, but do not under-reason scientific work.

If the current root model/effort appears insufficient, explicitly ask the user
to switch models before continuing rather than silently forcing a weak answer.

Request Astra only when materially justified, such as:

- theory, code, hardware, and measured data conflict in a non-obvious way,
- several plausible failure modes survive careful analysis,
- Sol has already failed or remains materially uncertain,
- a major architecture/debugging decision has high experimental consequences,
- a whole-system forensic review is warranted.

When requesting Astra, state the desired level:

- `Astra Medium` for difficult multi-domain analysis or architecture where extra
  reasoning is useful but the problem is still reasonably bounded.
- `Astra High` for unusually difficult, ambiguous, high-consequence debugging
  or synthesis where exhaustive reasoning is warranted.

Do not request Astra for routine coding, formatting, ordinary plots, simple
refactors, or already-specified implementation.

The user controls root-model changes.

---

# Scientific integrity

Treat experimental data as evidence, not as something to make agree with theory.

When measurements disagree with the model:

- preserve the disagreement,
- characterize it,
- distinguish observation from interpretation,
- investigate plausible causes,
- do not tune parameters merely to force agreement.

Never silently:

- smooth,
- clip,
- discard outliers,
- remove negative values,
- interpolate missing regions,
- normalize away meaningful differences,
- merge incompatible runs,
- suppress low-DoP or anomalous points,
- substitute commanded values for measured values,
- replace measurements with predictions.

If a transformation is necessary, make it explicit, reproducible, and traceable.

Always distinguish:

- raw measured data,
- calibrated data,
- filtered/smoothed data,
- normalized data,
- interpolated data,
- averaged data,
- fitted quantities,
- model/simulation output,
- inferred quantities.

Do not present one category as another.

Use appropriately cautious language:
`consistent with`, `associated with`, `suggests`, `systematic deviation`,
`cannot distinguish`, etc.

Do not infer causation from correlation alone.

---

# Provenance and reproducibility

Important displayed quantities must be traceable to source measurements.

Preserve relevant:

- source files/run IDs,
- timestamps,
- hardware/channel assignments,
- sampling rates,
- gains and calibration values,
- actuator settings,
- wavelength,
- configuration,
- processing choices,
- software/version provenance.

Do not mutate source experiment data during analysis.

Prefer derived artifacts that reference immutable source data.

---

# Repository consistency

Before creating or substantially changing a tool, plot, report, or workflow:

1. inspect analogous implementations already in the repository,
2. follow existing naming, layout, configuration, metadata, provenance,
   plotting, reporting, and file-organization conventions,
3. reuse existing infrastructure where practical,
4. deliberately document significant deviations.

New work should feel native to the repository rather than like an isolated script.

---

## Tools and workflows

For new experimental tools:

- reuse existing configuration patterns,
- reuse run-folder/report/provenance infrastructure,
- reuse validation and cleanup behavior,
- preserve safe hardware states on completion/failure,
- verify channel assignments, bounds, gains, timing, and settling assumptions.

Do not assume:

- commanded voltage equals delivered voltage,
- API-call time equals physical measurement time,
- one read equals one fresh measurement,
- actuator response is instantaneous,
- hysteresis/creep are negligible.

Log enough information to test important assumptions later.

---

## Plots

Visual clarity is important, but fidelity is paramount.

Plots should:

- follow repository visual conventions,
- include units,
- clearly distinguish measured/modelled and raw/processed traces,
- identify normalization or processing,
- preserve anomalous values unless explicitly justified,
- use common axes when direct comparison requires them,
- avoid autoscaling or presentation choices that exaggerate or conceal differences.

Any filtering, averaging, fitting, interpolation, normalization, or subtraction
must be evident from the plot, caption, or report.

A beautiful plot that misrepresents the underlying data is unacceptable.

---

## Reports

Follow existing report style and organization.

Reports should clearly separate:

- setup/acquisition conditions,
- measured quantities,
- derived quantities,
- model expectations,
- interpretation,
- limitations,
- provenance.

Important conclusions should be supported by visible quantitative evidence.

Disclose relevant differences between compared runs such as optical power,
alignment, polarization state, gain, sampling rate, bandwidth, actuator state,
or mounting geometry.

Do not state stronger conclusions than the data support.

---

# Analysis standards

Before adding an analysis, verify:

- what quantity is actually measured,
- units,
- timing/sampling assumptions,
- calibration,
- normalization,
- whether compared datasets are commensurate.

Do not assign stronger physical meaning than the measurement supports.

Examples:

- PD voltage variance is not automatically fringe-contrast variance.
- A PSD peak is not automatically a mechanical resonance.
- Low PAX DoP is not automatically true depolarization.
- On-minus-off PSD is not automatically a mechanical transfer function.

The preferred model-validation workflow is:

1. independently measure physical parameters where possible,
2. insert those measurements into the model,
3. compare prediction and experiment,
4. characterize discrepancies,
5. design measurements that discriminate among explanations.

Do not fit the discrepancy away.

---

# Storage and context efficiency

Avoid unnecessary file proliferation and unnecessary context loading.

Prefer a small number of coherent experiment outputs over one file per
bias/cycle/capture unless technically justified.

Do not reread large files already available in context merely to confirm their contents.
Read only the relevant ranges/files needed for the current task.

Do not launch broad repository searches or multiple subagents when a targeted
inspection is sufficient.

Preserve scientific traceability even when optimizing storage/context.

---

# Completion checklist

Before presenting scientific work as complete, verify:

- plotted values trace back to source data,
- raw and processed quantities are distinguished,
- commanded and measured values are distinguished,
- units and timing assumptions are correct,
- transformations/exclusions are explicit,
- compared conditions are actually comparable,
- anomalous values were not silently hidden,
- model predictions are distinct from measurements,
- conclusions do not overstate causation,
- repository conventions were followed,
- hardware safety and provenance were preserved.

If any item is materially uncertain, investigate or state the limitation.

---

# Implementation discipline

For substantial changes:

1. inspect the relevant existing implementation and analogous repo patterns,
2. understand the data/control flow,
3. choose the smallest coherent change,
4. implement,
5. test/check locally where possible,
6. inspect generated artifacts,
7. state what still requires bench validation.

Prefer small, testable, reversible changes over speculative rewrites.