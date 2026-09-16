# Validation against existing findings

[Back to simulations](../README.md)

The configurable runner is a refactor of the existing optical calculation,
with new layouts and reporting. Agreement with the old model is distinct from
agreement with the actual bench.

## Independent checks

The automated tests verify:

- The complex ideal E field and its normalized Stokes vector over both phases,
  including values outside the canonical 0–pi branch.
- The unequal-amplitude laws
  `I_E = ax² sin²(phi2/2) + ay² cos²(phi2/2)` and
  `I_F = ax² cos²(phi2/2) + ay² sin²(phi2/2)`.
- Total-power conservation with PBS leakage, imbalanced NPBSs, and rotated
  lossless retarders.
- Stokes Fourier coefficients against direct Jones evaluation, and analytic
  phase derivatives against centered finite differences.
- Independent principal-axis amplitude/phase, supplied Jones matrices, dark
  ports, singular angles, repeated phase controls, and invalid configurations.
- Actual LaTeX-to-PDF compilation and rerunning the saved input snapshots.

The implementation was also compared directly with the pre-refactor source
from Git across 150 randomized parameter/phase combinations. All nine historical
checkpoints matched; the maximum complex-field difference was `6.48e-16`.
This includes input phase before PBS leakage, all four rotated retarders, and
relative arm factors greater than one.

A local timing check on 10,000 phase pairs took approximately **1.26 s** for
the former scalar propagation loop and **0.0095 s** for the compiled vectorized
engine after compilation, about **133×** faster in this particular run. This
compares the old all-checkpoint scalar API with the new final-port batch API;
it is not a general performance guarantee and excludes report generation.

## Archived physical fit

The historical parameter example reproduces
[physical-jones-fit.json](../../../experiments/polarization_locking/2026-08-13/192530_field-model-calibration_phi2-power-model-0/physical-jones-fit/physical-jones-fit.json).
Its field-model topology and phase offsets are preserved. Tests apply the saved
detector gains/offsets *after* optical propagation and reproduce every archived
baseline, amplitude, and contrast in
[jones-fringe-predictions.csv](../../../experiments/polarization_locking/2026-08-13/192530_field-model-calibration_phi2-power-model-0/physical-jones-fit/jones-fringe-predictions.csv)
to 12 decimal places.

Tests also run the frozen model on the later 714-sample fringe-map data and
reproduce the existing
[validation summary](../../../experiments/polarization_locking/2026-08-13/200934_phi1-fringe-map_phi1-fringe-map-1/physical-jones-validation/validation-summary.json):

| Observable | Preserved held-out error |
| --- | --- |
| PD power, RMS divided by measured standard deviation | 1.00658336 |
| PAX power, RMS divided by measured standard deviation | 1.32129625 |
| Normalized Stokes components, unscaled RMS | 0.82661543 |

These are substantial discrepancies. Reproducing them confirms that the
refactor preserves the calculation; it does not validate the historical fit as
an accurate set of individually measured optic parameters. Its C-arm factor
`1.0856` is explicitly a relative scale rather than a passive transmission.

## Relationship to the effective-analyzer paper

The existing
[phi2-amplitude scientific paper](../../../experiments/polarization_locking/2026-08-13/200934_phi1-fringe-map_phi1-fringe-map-1/phi1-fringe-fit/phi2-amplitude-prediction/phi2-amplitude-scientific-paper.tex)
reports conditional effective-analyzer contrast predictions: means 0.571 (PD)
and 0.619 (PAX), versus measured 0.585 and 0.657, with mean absolute errors
0.027 and 0.041. That method uses an empirical phase branch from the later run.
It is a different model from the frozen physical Jones network. Those fitting
scripts and results remain available; the new optical report does not claim
those agreement levels for a blind physical-model forecast.

The ideal baseline, the scalar-amplitude extension, and the fitted nonideal
example therefore serve different purposes. Use the new report to examine
how declared optical parameters change the result, then compare separately to
measured outputs in the correct detector units and port/Stokes convention.

## Symbolic-amplitude and contributor checks

Additional tests reconstruct the configured intensity with independent input
amplitudes and arbitrary relative phase, including a blocked input component.
The general quadratic is
`I = ax² A + ay² B + 2 ax ay [Re(G) cos(delta) + Im(G) sin(delta)]`,
where `A`, `B`, and `G` are derived from the optical transfer matrix.
The aligned differential-retarder identity is checked separately against
propagation with unequal input amplitudes.

The [controlled parameter study](../../../experiments/field_propagation/historical_contributors/README.md)
uses the full phi2 harmonic, holding phi1 command at 0.73 rad (with saved
phase offsets applied), and also repeats over 360 phi1 values.
Full-model optical visibility is 50.53% at E and 49.54% at F. Resetting the
C/D retarders leaves 13.62% and 13.36%; adding just those retarders to the
ideal model gives 52.32% at both ports. The fitted input amplitude ratio
alone gives 2.84%. These interventions do not refit anything and do not
identify a unique physical cause on the bench.

The sum of output powers is constant during each phi2 scan to numerical
precision, as required by the unitary final combiner. Individual-port
modulation represents redistribution of power between ports. It does not
imply a phase-dependent loss of total power.

## Measurement workflow regression coverage

The acquisition tests exercise a synthetic 111-reading campaign through build,
freeze, 336 output predictions and fixed-parameter comparison. They cover direct
editing of the compact campaign, duplicate readings, low-DOP rejection, passive
polarization-dependent scattering, spatial coherency/fiber projection, frozen
input tampering and validation timestamps. A blank campaign must remain incomplete.

```bash
PYTHONPATH=python python -m unittest discover -s python/field_propogation/tests -v
```

Synthetic tests do not populate real campaigns or contact hardware. Physical
validation still requires independent bench measurements and uncertainty
propagation; no physical-validation pass/fail is inferred from these software tests.
