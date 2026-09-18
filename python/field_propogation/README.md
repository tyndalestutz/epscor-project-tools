# Jones-field simulations

Run one propagation script with different optical layouts and parameter files.
The result is a typeset PDF retaining independent input amplitudes `ax` and
`ay` before numerical substitution, and deriving final Jones fields, Stokes parameters,
polarization state, intensity, and phase sensitivities for every output.

## Start here

For the real bench, follow the **[measurement acquisition protocol](docs/measurement_acquisition.md)**
and use the [blank campaign](../../experiments/field_propagation/measurement_campaign/2026-09-10/README.md).
Edit one `campaign.json` containing all settings and measurement repeats.
The active sequence is `acquire.py init → edit campaign.json → status → build → freeze → predict → compare`.
It preserves independent raw readings, converts power ratios to field amplitudes,
requires parameter provenance, and fixes inputs before held-out comparison.
Unknown phases and spatial assumptions remain visibly conditional.

The **[historical model audit](docs/historical_fit_audit.md)** answers the
fourteen diagnostic questions. Old fitting algorithms/configurations are in
[archive/effective_fits](archive/effective_fits/README.md); their raw experimental
evidence is preserved. They do not supply measured optic properties to the new workflow.

For an ideal mathematical baseline, from the repository root, run the ideal hybrid MZI:

```bash
python python/field_propogation/run_simulation.py
```

The command prints the report path in a new dated folder under
`experiments/field_propagation/`. Open `derivation.pdf`. It includes the optical
flow, substituted element matrices, explicit output-field and Stokes equations,
operating-point polarization, intensity maps, and one-axis Stokes scans.

Requirements: Python 3.10+, NumPy, Matplotlib, and `pdflatex` with the standard
article/math/graphics packages and Latin Modern fonts. SymPy is needed for the
independent symbolic reference; SciPy is needed only for archived fits. The repo's
`jl-env` environment has the Python dependencies. Use `--no-pdf` to generate the
LaTeX source, vector plots, and numerical results without a LaTeX installation.

## Change parameters, then change the layout

For the same optical bench with different input amplitudes:

```bash
python python/field_propogation/run_simulation.py \
  --params python/field_propogation/configs/parameters/unequal_input.json
```

For a different optical layout, select both files:

```bash
python python/field_propogation/run_simulation.py \
  --flow python/field_propogation/configs/flows/single_element.json \
  --params python/field_propogation/configs/parameters/single_element.json
```

Copy a flow to save a new optical arrangement. Copy a parameter file for an exploratory scenario; use the acquisition builder
to create a version-2 parameter set from independent measurements. The **[configuration guide](docs/configuration.md)**
explains each element, units, conventions, and how to add an optic.

| File to edit | What belongs there |
| --- | --- |
| `configs/flows/*.json` | Optical elements, their order, path connections, and output ports. |
| `configs/parameters/*.json` | Input amplitudes, measured element values, operating phases, and scan settings. |

A quick operating-point override needs no file edit:

```bash
python python/field_propogation/run_simulation.py --phase phi1=pi/2 --phase phi2=pi/3
```

The held-fixed phases are printed on each plot and in its caption. Different
Stokes component amplitudes across cuts are expected because different
coordinates are fixed; no per-curve amplitude normalization is applied.
All other phases are held at the operating point in each one-axis scan. Every
phase pair also gets an intensity map. Phases are optical radians, not voltages.

## Read the results

- `derivation.pdf` / `derivation.tex`: publication-style article with the actual
  configured calculation; coefficients are displayed to five significant figures.
- `parameter-provenance.csv`: value, units, quantity, plane, measurement IDs,
  status, uncertainty and notes; the PDF also distinguishes provenance visually.
- `summary.json`: output states, sampled intensity ranges, visibility, and phase
  sensitivities. Undefined polarization/angles are `null`.
- `symbolic-intensity.json`: optical transfer coefficients and the intensity
  quadratic in independent `ax`, `ay`, and relative input phase.
- `harmonics.json`: full-precision complex coefficients of the analytic field and
  unnormalized Stokes phase sums.
- `phase-cuts.csv`, `phase-maps.csv`: numerical scans; undefined entries are blank.
- `flow.json`, `parameters.json`: input snapshots, including phase overrides,
  that can be supplied to the same runner to reproduce the simulation.

Specify `--output <new-folder>` to choose the destination. Existing nonempty
folders are rejected so earlier runs remain reproducible.

Large `max_abs_dI_dphase` means strong intensity sensitivity. Large
`max_stokes_speed` means strong polarization motion per radian, even when power
is constant. Sampled visibility is a finite-grid diagnostic, not a certified
bound. Dark-port polarization and singular angles are explicitly undefined.

## Code map

| Module | Responsibility |
| --- | --- |
| [run_simulation.py](run_simulation.py) | Shared CLI and result export. |
| [configuration.py](configuration.py) | Versioned input validation and safe numeric expressions. |
| [elements.py](elements.py) | Reusable Jones element matrices. |
| [propagation.py](propagation.py) | Optical connections, analytic phase coefficients, vectorized propagation. |
| [observables.py](observables.py) | Stokes quantities, ellipse/sphere angles, dark-port handling. |
| [analysis.py](analysis.py) | Phase scans and analytic sensitivities. |
| [symbolic_intensity.py](symbolic_intensity.py) | Independent-input transfer functions and amplitude quadratics. |
| [acquire.py](acquire.py) / [acquisition/](acquisition/) | Raw measurements, reduction, freeze, prospective predictions and comparison. |
| [provenance.py](provenance.py) | Required physical planes, source IDs, status and uncertainty. |
| [detection.py](detection.py) | Route spatial overlap, passive fiber projection and coherency Stokes. |
| [reporting.py](reporting.py) | LaTeX derivation and vector plots. |
| [physical_hybrid_mzi.py](physical_hybrid_mzi.py) | Historical compatibility API; not the measured-model entry point. |
| [ideal_hybrid_mzi.py](ideal_hybrid_mzi.py) | Independent symbolic reference retained for comparison. |

The engine compiles the coherent path coefficients once and evaluates phase
arrays using NumPy broadcasting. It does not repeatedly simplify symbolic
expressions or fit a sinusoid to derive the report equations.

## Examples and regression checks

The provided cases cover ideal equal input, unequal input, a single element
with independent polarization amplitudes/phases, and the historical fitted
network. The latter needs `hybrid_mzi_relative_fit.json`: its relative arm scales
are allowed to exceed one and are not calibrated passive transmissions.

See [validation against existing findings](docs/validation.md) and the
[example report commands](../../experiments/field_propagation/README.md).

```bash
PYTHONPATH=python python -m unittest discover -s python/field_propogation/tests -v
```

To reproduce the historical parameter-contributor study:

```bash
python python/field_propogation/archive/effective_fits/parameter_study.py
```

It compares group-at-a-time resets and single-group additions without refitting.
See [the results](../../experiments/field_propagation/historical_contributors/README.md).
These parameter effects interact; they are not additive causal contributions.

## Other workflows

- [archive/effective_fits/](archive/effective_fits/): historical fitting algorithms and tutorial generators.
- [legacy/](legacy/): earlier interactive explorations.
- [Bench diagnostics](../polarization_locking/analysis/README.md).
- [Historical hybrid-MZI equations and workflows](docs/hybrid_mzi_workflows.md).

The coherent engine supports independent amplitude and phase, arbitrary 2×2
section matrices and passive 4×4 two-port scattering matrices. Receiver-aware
predictions separately use four route modes, a spatial Gram matrix and optional
fiber projection, retaining partial polarization after integration. The report
runner describes the coherent Jones baseline; `acquire.py predict` applies the
explicit detector model. Full diffraction, spectral averaging and spatially
varying polarization are outside this extension. The directory spelling
`field_propogation` is retained for existing imports.

## Isolate the measured splitter ratios

```bash
python python/field_propogation/splitter_study.py
```

Reads the 2026-09-16 direct-meter route powers from the compact campaign. Keeps
ax=ay=1 and all other optics ideal. Generates one comparison PDF and reusable
flow/parameter files for the shared runner, with ideal and two measured-ratio
cases. The two cases use NPBS2 C-input or D-input measurements separately;
no averaging, fitted phases or unmeasured loss is introduced. Reflection phase
and reciprocal lossless completion remain explicit ideal assumptions. Output
ratios are conditional collected split fractions, not absolute transmissions.

The C/D readings are slightly inconsistent with one exact lossless matrix;
showing both completions preserves that ambiguity. The model assumes the ideal
PBS assigns A to x and B to y. It does not claim these power readings measured
the complete H/V complex splitter matrices. Generated reports remain ignored
by Git; the script and campaign are the reproducible sources.

## Temporary fiber probes and spatial mismatch

```bash
python python/field_propogation/spatial_study.py
```

Edit one [spatial scenario file](configs/parameters/spatial_coupling_study.json).
It currently uses the illustrative 50%/40% coupling example, not new experimental
readings. Temporary steering mirrors and fibers are removed from the operating
path, so their efficiencies are not inserted as arm losses. The study compares
two equal-waist Gaussian geometries consistent with the same probe powers,
then sweeps a relative C/D angular phase ramp without fitting anything.

The generated `spatial-derivation.pdf` carries spatial overlaps through Jones
fields, integrated coherency/Stokes, degree of polarization and fringe
visibility, with measured-splitter and ideal-splitter comparisons. All outputs
remain under ignored dated experiment folders. The settings file labels the
unknown fixed-probe alignment, common throughput and unmeasured geometry.
Measurements through different temporary probe fibers cannot be multiplied
as serial losses; use independent relay calibration and isolated-route data.

[Local report requirements](AGENTS.md) preserve the requested symbolic-first,
numerical-substitution-afterward PDF format for future studies.
