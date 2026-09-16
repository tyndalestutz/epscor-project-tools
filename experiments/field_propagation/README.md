# Jones simulations and measurement campaigns

Start with the [compact measurement campaign](measurement_campaign/2026-09-10/README.md)
and [acquisition protocol](../../python/field_propogation/docs/measurement_acquisition.md).
The [historical audit](../../python/field_propogation/docs/historical_fit_audit.md)
explains why the old fitted parameters are not independently measured optics.

Simulation PDFs, plots, numerical scans and copied input snapshots are generated
outputs. They remain available locally but are ignored by Git. Source configurations,
regression tests, the compact campaign and concise historical audit evidence belong
in version control. Real measurement data are not covered by these output ignores.

Generate reports from the repository root (choose a new output directory):

```bash
# Ideal hybrid MZI
python python/field_propogation/run_simulation.py

# Unequal input amplitudes
python python/field_propogation/run_simulation.py --params python/field_propogation/configs/parameters/unequal_input.json

# Single optical element
python python/field_propogation/run_simulation.py --flow python/field_propogation/configs/flows/single_element.json --params python/field_propogation/configs/parameters/single_element.json

# Historical fitted network, for reproduction only
python python/field_propogation/run_simulation.py --flow python/field_propogation/archive/effective_fits/configs/flows/hybrid_mzi_relative_fit.json --params python/field_propogation/archive/effective_fits/configs/parameters/historical_fit.json
```

The runner prints its dated output directory and PDF path. See
[validation details](../../python/field_propogation/docs/validation.md) and the
[historical contributor results](historical_contributors/README.md).
