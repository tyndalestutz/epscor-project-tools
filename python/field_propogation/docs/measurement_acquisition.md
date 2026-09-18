# Measurement acquisition: characterize first, predict before validation

The software is ready to collect **independent measurements**. The real measured
model is not populated or validated yet. Start with the blank
[campaign](../../../experiments/field_propagation/measurement_campaign/2026-09-10/README.md).
Its single `campaign.json` contains all settings, a shared template for each reading kind, and one readings list per step. No measurement values are invented. All CLI commands
below import files or compute results; none connects to or moves bench hardware.

The scientific question is: do independently measured imperfections predict the
observed phase-dependent polarization and power? See the
[historical audit](historical_fit_audit.md) for why the old fitted parameters
cannot answer that question.

## 1. Establish the reference planes and instrument conventions

Use a sketch/photo of the actual bench to label these planes. Record that
sketch's location in `campaign.json → session`. Preserve the sketch with the campaign.

```text
P0 (ax, ay, sqrt(W)) → PBS → A0 → section A → A1 ┐
                         → B0 → section B → B1 ┴ NPBS1
                                      C0 → section C → C1 ┐
                                      D0 → section D → D1 ┴ NPBS2 → E0, F0
                                                                  ↓
                                                 free-space / camera / fiber receiver
```

A0/B0 are immediately after the PBS; A1/B1 immediately before NPBS1.
C0/D0 are immediately after NPBS1; C1/D1 immediately before NPBS2. E0/F0 are
immediately after NPBS2 and before detector optics. **Each section Jones matrix
already includes all attenuation between its two planes.** Do not multiply by
another loss inferred from the same readings. Input amplitudes live only at P0.

Fill `campaign.json → session`: operator, bench ID, wavelength in nm, instrument IDs mapped
to calibration records (date, wavelength, responsivity/gain, range, dark/offset,
ND attenuation if used, standard uncertainty), normalization plane, and explicit
boolean confirmations for the planes and basis. Include the source-state
preparation calibration, PAX validity conventions, actuator reference and
receiver alignment in the notes/supporting files. Each reading's `detectors`
object maps measurement roles to these instrument IDs, for example
`{"input_power":"meter1","output_power":"meter1","polarization":"pax1"}`.
Power values must already be in calibrated optical power units at the named
planes; do not enter photodiode volts as microwatts or bury ND correction in an
adjustable model gain. Keep original voltage/ND/calibration traces alongside
these import files and identify them in notes.

The repository uses

\[
 S_0=|E_x|^2+|E_y|^2,\quad S_1=|E_y|^2-|E_x|^2,\quad
 S_2=2\Re(E_x^*E_y),\quad S_3=2\Im(E_x E_y^*).
\]

Thus H=(1,0) has s1=-1; V=(0,1) has s1=+1; D=(1,1)/√2 has
s2=+1; the label R here means the vector (1,i)/√2 and has s3=-1.
Check these against actual instrument signs and every reflection's coordinate
basis. Do not rely on the name “right circular” alone. Here s_j=S_j/S0,
and DOP is a fraction, not percent. Angle-derived unit Stokes cannot substitute
for a DOP measurement. Verify PAX validity, power range and DOP units before use.
The historical dataset has troubling DOP values; see the audit.

Default quality gates in `campaign.json → plan`: three independently acquired repeats,
≤2% bracketing input drift, DOP ≥0.98 and ≤1.02, Stokes norm within 0.02 of
one, and ≤5% relative coherency error on section checks. These are declared
acquisition thresholds, not statistical confidence limits. Choose justified
thresholds before collection; never loosen them just to make an output fit.

## 2. Acquire source, splitter and section data

`campaign.json → plan` is the machine-readable checklist: **37 required steps × 3 repeats
= 111 reading records**, plus explicit optional phase/spatial steps. Its order
is for record organization; physically regroup steps to minimize realignment
while preserving the named planes. Record setup changes and reference drift.

| Measurements | Count per repeat | Preparation and collection |
| --- | ---: | --- |
| Operating input at P0 | 1 | Free-space total power, background, Stokes and DOP. Keep actual operating source polarization. |
| PBS | 4 | Inject separately calibrated H and V at P0; record both A0 and B0 powers for each. |
| NPBS1 | 8 | Inject H then V into A1 only, then B1 only; measure C0 and D0 for each. Other incident port blocked. |
| NPBS2 | 8 | Inject H then V into C1 only, then D1 only; measure E0 and F0 for each. |
| A/B/C/D sections | 16 | Inject H, V, D and R **directly at each section's input plane**, measuring output free-space power, Stokes and DOP at its output plane. |

For every power ratio, collect input before, output, input after, plus darks.
Hold detector aperture/range and collection geometry fixed. The collector must
capture the full intended beam; a fiber-coupled power reading belongs to the
receiver characterization, not a section transmission. Include actual blocker
planes, detector orientation, phase command/reference and alignment in notes.
Do not simulate a physical A-arm blocker by changing the source's polarization.

The reduction uses

\[
 \bar P_{\rm in}=(P_{\rm before}+P_{\rm after})/2-P_{\rm dark,in},\qquad
 T={P_{\rm out}-P_{\rm dark,out}\over\bar P_{\rm in}},\qquad t=\sqrt T.
\]

Every reading needs standard uncertainties in the same units as its values.
Common instrument uncertainty is retained across repeats rather than divided
by √N. Repeat scatter adds a standard error. The shared input-dark uncertainty
is counted once. Zero/below-detection leakage is rejected as a precise field
estimate: improve dynamic range or create an explicitly bounded alternative
parameter scenario; do not invent a tiny nonzero “measurement.” Ratios above
one and nonpassive combined matrices require a calibration/phase investigation,
not silent rescaling.

Power-only splitter characterization assumes no H↔V conversion. Inspect output
polarization for both input states and document that check. If conversion is
measurable, independently characterize the full complex scattering matrix and
supply it to the engine; the diagonal acquisition builder is then insufficient.
Both inputs are measured because port symmetry is not assumed. H/V magnitudes
and phases are separate quantities.

For a deterministic section, H and V determine the two Jones columns up to
individual phases; D fixes their relative phase. The code solves this relative
phase from coherency cross terms and checks rank/conditioning. R is unused in
that reconstruction and tests the result. A single output polarization state
cannot identify a section matrix. Absolute global section phase remains
unmeasured by polarimetry and belongs in the dedicated inter-arm phase
reference. If the data are depolarized or inconsistent across repeats, stop
Jones reconstruction and characterize a coherency/Mueller model instead.

## 3. Measure coherent phases and actuator response independently

`campaign.json → choices` starts with null values. Splitter powers do not determine
reflection/transmission phase or H/V differential phase. Optional `phase_*`
steps accept three repeated coherent-reference phase measurements in radians,
with uncertainties and the common port/basis gauge recorded in notes.

If a phase is inaccessible, leave it unmeasured and select an explicitly
bounded nuisance scenario with a reason in `campaign.json → choices`. It will remain
**CONDITIONAL** in the parameter provenance/report/frozen prediction. Do not
select it using the final validation fringe. Setting every phase to zero is
generally not a physical splitter: full-matrix passivity is checked. For a
symmetric lossless illustrative splitter the familiar transmitted-real,
reflected-π/2 assignment is a possible convention, but measured asymmetries and
extra polarization phases cannot be certified by that convention. Sweep
physically allowed nuisance scenarios separately if needed; do not label a
selected scenario an independently explained experiment.

Acquire `phi1_law` and `phi2_law` with a separate coherent reference: voltage,
unwrapped optical phase, direction, settling time, temperature/time drift, and
uncertainties. Keep forward and reverse branches. Use these records to fill
**every actuator command column** in `validation_schedule.csv`, and record the
calibration/mapping in session notes before hardware acquisition. The software
predicts in optical radians; it does not infer a linear voltage law, interpolate
hysteresis automatically, or command hardware. Record origins consistently with
the section-matrix reference to avoid counting the same static phase twice.
A build can be a conditional optical-radian scenario before voltage calibration;
that does not make its command schedule executable on the bench.

## 4. Characterize the receiver and four spatial routes

At **one common receiver alignment**, isolate AC, AD, BC and BD at E and F.
Record integrated free-space power and fiber power, coupler x/y position in µm,
two camera-plane z positions in m and x/y centroids in m, widths in m (define
RMS or 1/e² explicitly in notes). The same receiver position must be used for
all four routes. Repeat over a documented x/y grid; do not maximize each route
separately and call the four maxima one configuration. Beam slopes follow from
centroid differences divided by the z separation. Keep original images and
reference-plane metadata. These optional `spatial_*` records preserve the data;
the builder does not guess complex overlaps from centroids.

Let normalized route modes be u_a and their Jones fields be e_a. The detector
module uses

\[
 G_{ab}=\langle u_a,u_b\rangle,\qquad
 C_{pq}=\sum_{ab}e_{a,p}e_{b,q}^*G_{ba},\qquad
 S_j=\operatorname{tr}(H_j C).
\]

For a single-mode receiver, h_a=⟨u_f,u_a⟩ and replace G_ab by h_a* h_b.
The fiber field is Σ_a h_a e_a. This changes diagonal collection efficiencies
as well as interference. G must be Hermitian positive semidefinite with unit
diagonal; G−h* hᵀ must also be positive semidefinite. The code checks both.
Partial polarization after spatial integration is retained: |s| need not be 1.

**Isolated powers determine |h_a|², not its phase; centroids do not determine a
complex Gram matrix.** Optional `overlap_*` and `projection_*` templates record
real/imaginary normalized complex overlaps from dedicated coherent-reference
measurements, with raw trace identifiers, uncertainty and a shared phase gauge.
Pairwise interference must have known polarization overlap; orthogonal states
cannot determine G from an absent fringe. An independently characterized mode
profile/reference or a dedicated common-polarization preparation is required.
Align the spatial phase gauge with the Jones route coefficients to avoid
counting optical phase twice. Keep covariance/supporting reference data.

Enter characterized G and h in `campaign.json → detectors`, with status, notes and
registered source IDs. Modes are ordered explicitly by `mode_keys`:
BD=(0,0), BC=(0,1), AD=(1,0), AC=(1,1). This mapping applies to the generated
hybrid flow, not arbitrary flows where distinct spatial routes have merged
into the same phase harmonic. Each port is `integrating` or `fiber`; fiber
also needs a complex `projection` list. Complex values accept expressions such
as `"0.4 + 0.1*i"`. The initial all-ones G is explicitly an **assumed identical-
mode baseline**, never an acquired spatial explanation. Scalar-mode projection
assumes polarization-independent collection; polarization-dependent receiver
optics require an independently characterized extension before that claim.

Use separate frozen detection conditions for free-space, fiber and camera
collection, acquiring corresponding outputs. Finite camera apertures are not
a full-plane power meter. General spatially varying polarization, diffraction,
spectral averaging and time-dependent coherence are beyond this scalar-mode
extension; do not force them into fitted arm retarders.

## 5. Edit one campaign file, inspect, freeze and predict

Commands from the repository root (use the installed `jl-env` Python if needed):

```bash
# Already initialized blank campaign; use init only for a NEW campaign.
python python/field_propogation/acquire.py init experiments/field_propagation/measurement_campaign/NEW_RUN

# Edit CAMPAIGN/campaign.json directly: add repeats to measurements.STEP_ID.
# No per-reading import is required.
python python/field_propogation/acquire.py status CAMPAIGN
python python/field_propogation/acquire.py build CAMPAIGN --output NEW_BUILD
python python/field_propogation/run_simulation.py --flow NEW_BUILD/flow.json --params NEW_BUILD/parameters.json --output NEW_REPORT

# After independent characterization and predeclared detector/phase choices:
python python/field_propogation/acquire.py freeze CAMPAIGN --output NEW_FROZEN_BUNDLE
python python/field_propogation/acquire.py predict NEW_FROZEN_BUNDLE --output NEW_PREDICTION
```

`CAMPAIGN` and `NEW_*` are directory paths to replace. Open `campaign.json`:

- `session`: instrument metadata and basis/plane confirmations.
- `plan`: named measurement steps and quality thresholds.
- `choices` and `detectors`: phase and receiver settings.
- `reading_templates`: one reusable blank reading per measurement kind.
- `measurements`: step IDs mapped to lists of acquired repeats.

Copy the appropriate shared template into a step's list, then fill it. For
example, `measurements.PBS_A_H` holds three complete reading objects for its
three repeats. An empty list means not yet acquired. Templates stay blank.
ISO timestamps need a timezone, for example `2026-09-10T14:00:00+00:00`; use the
actual acquisition time. Each reading needs values, uncertainties, detector IDs
and setup notes. Readings receive stable content hashes when loaded; identical
copied readings cannot count as independent repeats. Editing this working
file is supported. Frozen bundles preserve the exact campaign snapshot.

If you already have an individual reading export, the optional
`acquire.py record CAMPAIGN --step PBS_A_H --file READING.json` appends it to the
same file; it creates no per-reading JSON files. Preserve original instrument
exports separately. `acquire.py compact CAMPAIGN` migrates an older split-file
campaign while preserving existing raw evidence.

`status` reports missing data/metadata/phases, while `build` additionally checks
measurement quality, tomography, uncertainty metadata and passivity.

The build separates `flow.json`, `parameters.json` and section reconstruction
diagnostics. Version-2 parameters carry physical quantity, units, plane, source
IDs, status and uncertainty. The report gives symbolic intensity with independent
ax, ay and input phase, then actual Jones/Stokes calculations, plus a visible
provenance table and CSV. **The report's coherent Jones scans are the baseline;
receiver-aware predictions are the separate `acquire.py predict` outputs.**
For partial-polarization receiver predictions use their coherency Stokes/DOP,
not the baseline report's pure-state ellipse classification.

A freeze copies the complete campaign (including all readings), generated parameters,
schedule, detector settings and implementation sources into a new hashed bundle. Prediction
rejects modified inputs or changed implementation. Hashes catch accidental
changes; they are not signatures or proof of laboratory provenance. Record
instrument/supporting calibration files and hashes in the campaign notes; files
merely referenced outside the campaign are not automatically embedded.

## 6. Acquire untouched validation data, then compare

The default preregistered schedule has forward and reverse 41-point phi1 and
phi2 scans, plus four physically isolated routes: 168 settings × E/F = **336
output observations**. Edit held-fixed phases and sampling before freeze to
match the intended experiment. Prediction writes the schedule's powers in W,
s1/s2/s3 and DOP, plus a blank validation CSV. Save predictions **before**
collecting these validation readings. Existing historical data cannot satisfy
this new prospective timestamp requirement.

Measure E/F simultaneously where practical, with both detectors independently
calibrated. Otherwise record timing and quantify source drift. Keep repeated
observations in raw records and aggregate with stated uncertainty for the
comparison CSV. Copy the generated template; fill calibrated powers, standard
uncertainties, Stokes, DOP, detector ID, actual UTC and notes. A dark port has
undefined polarization; do not claim a meaningful normalized Stokes residual
there. For an instrument-invalid polarization reading, leave all five polarization
fields (s1/s2/s3, stokes_sigma, dop) blank and explain the reason in notes.
The power comparison remains available, and polarization coverage is reported
separately; no polarization state is invented.

```bash
python python/field_propogation/acquire.py compare NEW_FROZEN_BUNDLE \
  --prediction NEW_PREDICTION --readings VALIDATION.csv --output NEW_COMPARISON
```

Comparison rejects altered predictions, unplanned/duplicate IDs and timestamps
predating prediction. It changes no optical parameter, gain, offset or phase.
It reports power residuals, **measurement-only** standardized power residuals,
Stokes errors, E+F sums and coverage. A lossless full free-space final splitter
conserves E+F; passive lossy optics or fiber projection need not give a constant
sum. Compare the actual detector prediction, not an imposed constant baseline.

Parameter uncertainties are retained, but input/Jones element uncertainty
estimates are approximate and shared covariance/model uncertainty is not yet
propagated through final outputs. Consequently the tool does **not** issue a
“physically validated within uncertainty” pass/fail. Completing that statistical
claim requires the acquired instrument covariance, nuisance constraints and
uncertainty propagation. This does not prevent acquiring the required evidence
now. Disagreement is a result to investigate through another independent
measurement campaign, not permission to refit the held-out fringe.

## Readiness checklist for the operator

- Confirm source/basis/instrument calibration and named planes.
- Collect the 111 required independent readings; preserve actual raw exports.
- Resolve below-detection bounds and failed purity/passivity checks explicitly.
- Independently constrain phases/voltage laws and spatial receiver overlaps, or
  label the corresponding prediction conditional and document what is missing.
- Fill actual voltage commands before hardware scans; freeze and predict first.
- Acquire both outputs for scans and isolated routes; compare without refitting.

Historical bench diagnostics remain available under
[polarization_locking/analysis](../../polarization_locking/analysis/README.md).
They are useful for commissioning and inspecting raw scans. Their old global
fitting recommendations have been retired from the active acquisition path.
