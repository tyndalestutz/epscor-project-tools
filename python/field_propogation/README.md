# Jones-field simulations

Start here to calculate how optical elements affect output polarization and
intensity. Commands below run from the repository root. The existing directory
spelling, `field_propogation`, is retained for the model import paths.

## Start with the models

| File | Purpose |
| --- | --- |
| [physical_hybrid_mzi.py](physical_hybrid_mzi.py) | Numerical Jones propagation through the hybrid MZI; the starting point for measured element parameters. |
| [ideal_hybrid_mzi.py](ideal_hybrid_mzi.py) | Symbolic reference, element order, Stokes/Poincaré conversion, and coordinate Jacobian. |

```bash
python python/field_propogation/physical_hybrid_mzi.py
python python/field_propogation/ideal_hybrid_mzi.py
```

The numerical model exposes `PhysicalParameters`, `Retarder`, and
`port_observables`. Current parameters include input x/y field amplitudes and
relative phase, PBS leakage, NPBS splitting, scalar arm amplitude transmission,
and arm retarders. Outputs include complex Jones fields, intensity, and Stokes
vectors for ports E and F. Angles are in radians; arm transmission factors act
on field amplitude, not power.

For the next modeling pass, put reusable element/propagation code alongside
these models and runnable simulation scenarios in a `simulations/` directory
when those scenarios are added. Measured spatial amplitude distributions and
general polarization-dependent element response still need implementation;
the present model describes a single Jones vector per path.

## Supporting workflows

| Location | Contents |
| --- | --- |
| [calibration/](calibration/) | Fit recorded measurements and validate model predictions. |
| [reports/](reports/) | Generate a tutorial/audit PDF from calibration artifacts. |
| [legacy/](legacy/) | Earlier interactive optics and Poincaré explorations, preserved for reference. |
| [../polarization_locking/analysis/](../polarization_locking/analysis/) | Bench-specific first-NPBS and phi1-step diagnostics. |
| [docs/hybrid_mzi_workflows.md](docs/hybrid_mzi_workflows.md) | Detailed equations, port conventions, data context, and calibration commands. |

The flat-folder scripts have moved into these locations; use the new paths.
The lock CLI's analysis commands and printed fit instructions follow the moves.
Recorded experiment artifacts and historical notebooks remain in their existing
locations. Model equations and fitting algorithms are unchanged in this reorganization.
