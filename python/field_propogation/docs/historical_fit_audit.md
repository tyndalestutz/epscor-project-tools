# Historical hybrid-MZI fit: evidence and corrected interpretation

The propagation is reproducible. The historical parameters are **effective fit
coordinates, not independently measured optic properties**. Their ability to
reproduce a fringe does not establish its physical cause. The active workflow
is now [independent measurement → frozen prediction](measurement_acquisition.md).
Historical raw measurements and reports remain unchanged.

This audit responds to the supplied [diagnostic/handoff](source/hybrid_mzi_physical_model_diagnostic.txt).
Its hypotheses were checked against repository code and data, rather than
accepted as established experimental conclusions.

## Evidence used

- [Original fitting algorithm, now archived](../archive/effective_fits/fit_physical_jones_network.py).
- [984-row training data](../../../experiments/polarization_locking/2026-08-13/192530_field-model-calibration_phi2-power-model-0/data.csv)
  and [saved fit](../../../experiments/polarization_locking/2026-08-13/192530_field-model-calibration_phi2-power-model-0/physical-jones-fit/physical-jones-fit.json).
- [714-row held-out validation summary](../../../experiments/polarization_locking/2026-08-13/200934_phi1-fringe-map_phi1-fringe-map-1/physical-jones-validation/validation-summary.json).
- [Reproducible local identifiability audit](../archive/effective_fits/audit_identifiability.py)
  and [numerical results](../../../experiments/field_propagation/historical_audit/identifiability.json).
- [Controlled parameter resets](../../../experiments/field_propagation/historical_contributors/README.md).

## Explicit answers to the fourteen audit questions

1. **Where did ax, ay and the losses come from?** `ax=1` and `loss_d=1` were
   fixed reference scales. `ay=1.0287803494` and `loss_a=0.6555029629`,
   `loss_b=0.7412609893`, `loss_c=1.0856133113` came from one joint least-squares
   optimization of the training dataset above. Each condition has 328 rows:
   `path_a_only`, `path_b_only`, and `both_paths`. There are no distinct
   source-power or arm-transmission measurements supporting these individual
   values. The loss coordinates enter the field as `exp(log_loss)`; `ay` enters
   directly. Units are relative field amplitude, not watts or calibrated
   transmission. The implied input is before the PBS, but the dataset does not
   supply calibrated input/section reference powers at the corresponding planes.

2. **Were powers inserted directly as field coefficients?** No such conversion
   was found in this parameter-generation path. The loss coefficients were
   optimized field multipliers. Their ratio being close to `780/880` is not
   evidence that those two powers generated the coefficients. The correct
   conversion for a separately measured ratio is `sqrt(780/880) = 0.94147`,
   and the new reduction explicitly implements and tests it. The original raw
   source/planes for these two handoff numbers were not located.

3. **Is imbalance represented twice?** Input polarization and downstream loss
   are distinct *model locations*, but were not determined by distinct
   measurements. Their fitted effects trade off strongly. The normalized
   Jacobian-column cosine for `ay` and `log_loss_b` is **0.9923** (a sensitivity
   similarity, not a statistical correlation coefficient). Double-counting a
   particular raw reading is unproven; physical separation is unsupported.
   The new normalization is P0 immediately before the PBS; each section Jones
   matrix includes its measured transmission exactly once.

4. **What constrained the retarder axes and retardances?** All four were adjusted
   jointly against PD/PAX final powers and both-path PAX Stokes. There was no
   dedicated C/D input/output polarimetry reconstruction in this fit.

5. **Was it scalar-power-only?** No. The objective included three Stokes
   components for the 328 both-path rows. Power residuals were scaled by their
   dataset standard deviations; Stokes residuals by `max(component std, 0.08)`
   and weight 0.5. These are optimization weights, not instrument uncertainty.

6. **Were phase-dependent output powers in the objective?** Yes, both detector
   power channels for all 984 training rows. PD voltage had fitted gain/offset;
   PAX total power also had fitted gain/offset. ND transmission columns were
   recorded but the physical-fit objective used raw PD voltage with a free gain;
   its gain therefore cannot establish an absolute optical normalization.

7. **Which plots are held out?** Plots evaluating the saved parameter set on its
   training scans reconstruct fitted data. The historical field-report scans
   and contributor plots are model evaluations/ablation calculations, not new
   independent experimental tests. The later 714-row phi1 map was evaluated
   with the saved physical model and is the identifiable held-out test here.
   A separately fitted effective-analyzer description or inferred phase branch
   is a conditional analysis, not blind validation of the physical parameters.

8. **What was the held-out disagreement?** The saved summary reports standardized
   RMS errors of **1.0066 (PD power)** and **1.3213 (PAX power)**, and raw normalized-
   Stokes component RMS error **0.8266**. These are substantial predictive errors;
   the standardization is not a measurement-noise confidence test.

9. **Why 65/35 instead of near 50/50?** The optimizer was free to change NPBS1
   while compensating with other coordinates. Nothing enforces a separately
   measured splitter ratio. Applying the handoff's scalar separability formula
   to its five position datasets gives branch fractions **0.4669, 0.4653,
   0.4664, 0.4648, 0.4727** (port labeling can exchange T/R). These handoff values
   are not linked to an original raw acquisition file. The cross-ratio inference
   assumes separable transmission and receiver coupling. Its approximate
   stability does not itself prove nonseparability or measure a full NPBS Jones
   matrix. It is a reason to characterize NPBS1 directly, with both input ports
   and H/V states, rather than accept 65/35 as measured.

10. **Why is loss_c greater than one?** It is relative to the convention
    `loss_d=1`, not an absolute passive transmission. There is no calibrated
    power plane defining that unit. It can be an effective relative coefficient
    without implying real optical gain; it must not be placed into the new
    passive measured model as an absolute transmission.

11. **What prevents the retarders absorbing missing spatial physics?** Nothing
    in the historical objective. It has no route-dependent mode overlap or
    fiber projection. This makes such compensation possible, not proven. A
    scalar spatial-overlap factor alone also cannot generate interference
    between strictly orthogonal Jones vectors. Four route-dependent receiver
    projections can change component balance, so those couplings must be kept
    separate from free-space arm transmission.

12. **Were splitter H/V coefficients equal, and can this change?** The fitted
    NPBS model used one mixing angle per splitter and a fixed reflection-phase
    convention, identical for H/V. The new engine accepts full passive 4×4
    two-port scattering matrices, or separate amplitude/phase arrays. The
    acquisition builder populates independently measured H/V magnitudes for
    both incident ports and explicitly measured or nuisance phases. Zero
    cross-polarization coefficients are an explicit assumption to check.

13. **What is identifiable?** The full 17-coordinate vector is not unique. For
    any positive λ, transform `loss_a,b → λ loss_a,b` and
    `pd_gain,pax_gain → gain/λ²`. All predicted detector powers and normalized
    Stokes are unchanged. Fixing loss_d does not remove this gauge. A local
    finite-difference, column-normalized Jacobian has rank **16/17** at relative
    threshold 1e-7; its smallest singular value is about **7.1e-12**. The explicit
    gauge perturbation changes standardized predictions by only **2.2e-15**.
    This does not prove that each remaining named physical parameter is globally
    identifiable. Multistart fitting, confidence intervals and profile
    likelihoods were not performed; no such precision is claimed. An exact
    gauge already refutes uniqueness, and the goal is to retire this physical
    interpretation rather than optimize it further.

14. **Can retarders be disabled?** Yes. The saved contributor study resets them
    without refitting: at phi1 command 0.73, phi2 visibility falls from **0.5053
    to 0.1362 at E**, and **0.4954 to 0.1336 at F**. Retarders alone on the ideal
    reference give about **0.5232**; A/B imbalance alone about **0.1223**; input
    imbalance alone about **0.0284** at E. These interacting effects are not
    additive causal attributions. The remaining historical parameters are
    still fitted; turning off retarders does not turn them into direct measurements.

## Additional acquisition issues found in the code/data

The old predictor implements `path_a_only` by zeroing input `ay`, and
`path_b_only` by zeroing `ax`. The bench CLI describes blocking actual arms.
With nonzero PBS leakage those operations differ. The saved condition names
alone do not establish the physical blocker plane, so this is a potential
forward-model mismatch requiring bench clarification, not a demonstrated
reconstruction of the old blocker placement. The new schedule names actual
A/B and C/D route blockers.

Raw `dop` values exceed one in 95/328 both-path rows, 321/328 A-only rows and
178/328 B-only rows; maxima are 1.983, 10.543 and 1.036 respectively. The
polarimeter wrapper's angle-derived Stokes vectors have unit length regardless
of this quality field. Verify instrument DOP units, validity flags and optical
power range before using new data; a unit-length reconstructed direction is
not proof of fully polarized light. New Jones reconstruction gates both DOP
and Stokes norm. Low-purity data require a coherency/Mueller characterization,
not silent normalization.

For a **lossless** complete final scattering matrix and complete free-space
collection, summed E/F power equals incoming power. Mere passivity is a weaker
condition: a general lossy scattering matrix can have phase-dependent total
absorption. Fiber-projected E/F sums can also vary. The new comparison retains
both sums and does not impose constancy by normalization.

## Reproduce this audit

From any directory:

```bash
python /path/to/epscor-project-tools/python/field_propogation/archive/effective_fits/audit_identifiability.py
```

The saved numerical audit identifies the raw-data hash, weighting, singular
values, sensitivity similarities, gauge test and limitations. Historical fit
algorithms are archived for reproduction; the active acquisition/build/predict
path has no final-output fitting objective.
