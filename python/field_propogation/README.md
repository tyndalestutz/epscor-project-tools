# Hybrid-MZI field propagation

`ideal_hybrid_mzi.py` is a standalone symbolic reference for the two-NPBS
hybrid MZI. It does not import or modify the lock controller.

Run it with:

```bash
python python/field_propogation/ideal_hybrid_mzi.py
```

Its element order is deliberately printed before the results, because that
order must be checked against the physical bench before fitting a non-ideal
model. The exact ideal output port used by the lock is:

\[
\mathbf E_E =
\begin{bmatrix}
a_x e^{i(\delta+\phi_1)}(e^{i\phi_2}-1)/2\\
i a_y(1+e^{i\phi_2})/2
\end{bmatrix}.
\]

For equal input amplitudes (`a_x=a_y=1`), both final-port intensities are
constant:

\[
I_E=I_F=1,
\]

and the PAX-compatible Stokes convention for this reference port is:

\[
(S_1,S_2,S_3)=
(\cos\phi_2,\sin\phi_2\cos(\phi_1+\delta),
\sin\phi_2\sin(\phi_1+\delta)).
\]

Thus, on the canonical branch \(0\le\phi_2\le\pi\),
\(u=\phi_1+\delta\pmod {2\pi}\) and \(v=\phi_2\).

Importantly, the script does **not** insert those ideal relations by hand.
For any final Jones field it derives

\[
(E_x,E_y)\rightarrow(S_0,S_1,S_2,S_3)
\rightarrow\left(u=\operatorname{atan2}(S_3,S_2),\;v=\arccos(S_1)\right).
\]

It also exposes `poincare_jacobian(field)`, which returns
\(\partial(u,v)/\partial(\phi_1,\phi_2)\). Its off-diagonal terms are the
local phi2-to-\(u\) and phi1-to-\(v\) coupling terms. A non-ideal model can
therefore reuse the same coordinate pipeline, for example:

```python
coordinates = jones_to_poincare(modified_final_field)
coupling = poincare_jacobian(modified_final_field)
```

The equal-amplitude ideal Jacobian reduces to a diagonal form away from the
polar singularities; it is a result to compare against, not an assumption
applied to a fitted model.

This phase offset differs by \(\pi\) from the original notebook port
labeling because this reference labels the *transmitted* output of each NPBS
first: `C`/`E`, and the reflected output second: `D`/`F`. It is a
reference-port convention, not a proposed change to the established live-lock
coordinate frame. Before using this model for a fit, identify whether the PAX
is physically connected to the transmitted (`E`) or reflected (`F`) final
port.

Before any general Jones/Mueller fit, the same ideal propagation also exposes
the smallest constrained non-ideal candidate:

\[
I_E=a_x^2\sin^2(\phi_2/2)+a_y^2\cos^2(\phi_2/2),\qquad
I_F=a_x^2\cos^2(\phi_2/2)+a_y^2\sin^2(\phi_2/2).
\]

It becomes constant only when the two *effective* amplitudes are equal. This
is a future fit parameterization, not something applied to the lock code.

## Data available for the next fitting step

The best targeted data sets are in `experiments/polarization_locking`:

- `011516_intensity-diagnostic...`: initial independent phi1/phi2 amplitude diagnostic.
- `012946_intensity-diagnostic...`: same diagnostic with PAX `ptotal`.
- `014544_phi2-path-test...`: stepped phi2 scans for path A, path B, and both.
- `215440_power-balance_upstream-inputs-long`: long, sine-driven phi2 fringe measurements at the upstream blocking plane.
- `215818_power-balance_final-bs-inputs-long`: corresponding long measurement with blocking immediately before the final BS.
- `001807_cross-sweep-phi1...` and `002644_cross-sweep-phi2...`: PAX-only cross-coupling/Vlambda maps.

The first fit should extend only the scalar amplitudes / visibilities and a
relative phase offset. It should not jump directly to an arbitrary Jones or
Mueller matrix: the present data measure Stokes quantities and relative port
power in different instrument units, so an unconstrained full-matrix fit would
not be identifiable without additional calibrated measurements.

## First data-fitted extension: phi2-to-power response

`fit_phi2_interference.py` fits the stepped path-balance experiment, where
phi1 was fixed and phi2 was stepped through one estimated \(V_\lambda\). Run:

```bash
python python/field_propogation/fit_phi2_interference.py \
  experiments/polarization_locking/2026-08-12/014544_phi2-path-test_path-balance-0/data.csv
```

It writes a PNG plus CSV/JSON coefficients in a `field-propagation-fit` folder
next to the source data. The fitted, identifiable model is

\[
P(\phi_2)=B+C\cos\phi_2+D\sin\phi_2.
\]

This is not merely a convenient curve fit: at fixed phi1 it is the general
power response linear in the ideal Stokes vector. In an ideal total-power
measurement \(C=D=0\). Nonzero terms quantify effective phase-to-power
leakage, while `both_paths - path_a_only - path_b_only` exposes a residual
interference term. It cannot yet assign that residual uniquely to a particular
optic, because the A, B, and both conditions were acquired sequentially and
the interferometer drifts.

## Intentional two-axis calibration

Use this lock CLI command for data that can fit a full effective analyzer
vector rather than a single fixed-phi1 projection:

```text
field-model-calibration <run-name>
```

It steps phi1 through 0, 1/4, 1/2, and 3/4 of its current RP \(V_\lambda\),
then sweeps phi2 through one RP \(V_\lambda\) at each bias. At every bias it
records A-only, B-only, and both-path sweeps in forward order, then repeats in
reverse order. Those repeated, alternating block sequences deliberately
bracket slow interferometer drift. The generated CSV is recognized by the same
fit command, which then fits

\[
P=B+h_1S_1+h_2S_2+h_3S_3,
\]

using the ideal propagated Stokes vector. The fitted \(h_1,h_2,h_3\) values
are the smallest constrained, data-driven model of each detector/condition's
phase-to-power sensitivity.

## Focused phi1 fringe map

The full calibration gives a coarse four-bias phi1 result. To determine the
actual phi1 voltage-to-fringe-phase map without manual blocking drift, run:

```text
phi1-fringe-map <run-name>
```

Keep both paths open throughout. It makes dense forward and reverse phi1
passes, and at every phi1 command measures a complete phi2 fringe. Analyze
the completed run with:

```bash
python python/field_propogation/fit_phi1_fringe_map.py <run>/data.csv
```

The result plots measured fringe phase and contrast against phi1 RP voltage;
forward/reverse disagreement is the direct hysteresis diagnostic.

## Numerical phi2-amplitude prediction

After a field-model calibration and a high-visibility phi1 fringe map, compare
the data-fitted propagation model with the measured phi2-dependent amplitude:

```bash
python python/field_propogation/predict_phi2_amplitude.py \
  <field-model-run>/field-propagation-fit/effective-analyzer-fit.csv \
  <phi1-fringe-run>/phi1-fringe-fit/phi1-fringe-map-fit.csv
```

It transfers the fitted both-path detector vectors through
\(P=B+\mathbf h\cdot\mathbf S\), uses the two detectors jointly to select an
empirical phi1 phase branch, and reports predicted versus measured phi2 fringe
contrast for both final-port instruments. The comparison is intentionally a
transfer test: disagreement is evidence that the effective analyzer changed
between runs or that the current model omits an optical term.

For a fresh, high-visibility field-model calibration, use the one-command
state-matched workflow instead of running three individual fit commands:

```bash
python python/field_propogation/validate_phi2_power_model.py \
  <new-field-model-calibration>/data.csv \
  <high-visibility-phi1-fringe-map>/data.csv
```

It writes the analyzer fit, fringe-map fit, predicted-versus-measured
comparison, and numerical contrast errors into
`state-matched-model-validation/` under the new calibration folder.

New calibrations also write `field-model-context.json`, preserving the exact
RP \(V_\lambda\), electrical gain, detector assignment, and scan settings used
for that run. The validation command reads it automatically; explicit command
line values remain available only to reproduce or intentionally override a
historical analysis.

## Tutorial / audit report

To generate a detailed, data-driven PDF explaining every fitted coefficient,
normalization, branch-selection step, worked numerical example, and aggregate
contrast error, run:

```bash
python python/field_propogation/write_phi2_amplitude_tutorial_report.py \
  <field-model-run>/data.csv \
  <phi1-fringe-run>/data.csv
```

The report is generated from the stored CSV artifacts and records SHA-256
prefixes for every input; it explicitly distinguishes the current conditional
amplitude transfer result from a blind phase-and-amplitude forecast.

The current scientific-paper version, with compact displayed derivations, is
stored alongside the numerical comparison as
`phi2-amplitude-scientific-paper.tex`.

## Physical Jones-network model

`physical_hybrid_mzi.py` is the next modeling layer beyond the empirical
effective analyzer. It propagates the complex Jones fields through the named
PBS, both NPBSs, phi1, phi2, and optional physically constrained imperfections
(PBS leakage, NPBS imbalance, arm loss, and A/B/C/D retarders), returning the
fundamental output fields `E` and `F`, their powers, and their Stokes vectors.

Its default parameters reduce exactly to the ideal symbolic field model and
therefore predict zero phi2 power-fringe contrast. The planned physical fit
will vary a deliberately small subset of those interpretable parameters
against a state-matched A/B/both calibration, and compare the resulting
fundamental Jones-port predictions with PAX Stokes/power and PD power.
