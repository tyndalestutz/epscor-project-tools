# Historical-fit intensity contributors

Full-model phi2 visibility: E = 50.53%, F = 49.54%.

Full phi2 fringe at phi1 command = 0.73 rad, including the saved phase offsets. Visibility is (Imax-Imin)/(Imax+Imin). Optical intensities exclude detector gain/offset.

| Parameter group | Reset group: E | Reset group: F | Group alone: E | Group alone: F |
| --- | ---: | ---: | ---: | ---: |
| Input amplitude ratio | 50.29% | 48.97% | 2.84% | 2.84% |
| A/B arm amplitudes | 47.12% | 45.23% | 12.23% | 12.23% |
| C/D arm amplitudes | 49.30% | 50.72% | 0.00% | 0.00% |
| PBS leakage | 50.14% | 49.06% | 0.00% | 0.00% |
| NPBS1 splitting | 54.65% | 52.21% | 0.00% | 0.00% |
| NPBS2 splitting | 52.33% | 52.33% | 0.00% | 0.00% |
| C/D retarders | 13.62% | 13.36% | 52.32% | 52.32% |

Reset group: restore only that group to ideal values, leaving the rest of the fitted model unchanged. Group alone: add only that group to an ideal model. These effects interact and are not additive.

The JSON includes individual-parameter resets and visibility ranges/means across 360 phi1 values. No measurement was refitted. These are model interventions, not experimentally identified causes.

The full-model output sum has maximum phi2 peak-to-peak variation 4.44e-16, consistent with roundoff for this lossless final combiner.

The figure and CSV are generated outputs, kept locally but excluded from Git.
Regenerate them with:

```bash
python python/field_propogation/archive/effective_fits/parameter_study.py
```

The retained `contributions.json` records the full numerical audit and inputs.
