# Fit and validate recorded measurements

These scripts consume experiment data; forward Jones models live one level up.
Run a script with `--help` for its inputs and output options, for example:

```bash
python python/field_propogation/calibration/fit_physical_jones_network.py --help
```

| Script | Purpose |
| --- | --- |
| `fit_physical_jones_network.py` | Fit a constrained physical Jones network. |
| `validate_physical_jones_fit.py` | Test frozen fitted parameters against fringe-map data. |
| `fit_phi2_interference.py` | Fit phi2 interference/effective analyzer coefficients. |
| `fit_phi1_fringe_map.py` | Fit fringe phase and contrast versus phi1 voltage. |
| `predict_phi2_amplitude.py` | Compare transferred analyzer predictions with measured contrast. |
| `validate_phi2_power_model.py` | Run the combined state-matched empirical validation workflow. |

See [the workflow guide](../docs/hybrid_mzi_workflows.md) for acquisition context,
model limitations, and full command examples.
