# Measured polarization: A, B, and externally driven A+B

Source: `../data.csv`. All 723 rows retained. Each condition spans about 60 s at 4 samples/s.

## Direct averages

Mean ± sample SD; degree values are ellipse angles unless marked Poincare.

| Quantity | A only (H-like) | B only (V-like) | A+B (moving) |
| --- | --- | --- | --- |
| Samples | 241 | 241 | 241 |
| Span (s) | 60.000389 | 60.000372 | 60.000504 |
| theta / ellipse azimuth (deg) | 1.667607 +/- 0.003453 | -87.304329 +/- 0.002348 | 0.924560 +/- 39.573563 |
| eta / signed ellipticity (deg) | 2.135098 +/- 0.001049 | 1.870229 +/- 0.001680 | -5.221094 +/- 27.682354 |
| absolute ellipticity (deg) | 2.135098 +/- 0.001049 | 1.870229 +/- 0.001680 | 24.543820 +/- 13.739615 |
| minor/major ellipse axis ratio | 0.037282 +/- 0.000018 | 0.032653 +/- 0.000029 | 0.486286 +/- 0.289690 |
| S1 orientation | 0.995535 +/- 0.000007 | -0.993455 +/- 0.000011 | 0.078475 +/- 0.060394 |
| S2 orientation | 0.058016 +/- 0.000120 | -0.093758 +/- 0.000081 | -0.095625 +/- 0.640027 |
| S3 orientation | 0.074460 +/- 0.000037 | 0.065237 +/- 0.000059 | -0.158644 +/- 0.741785 |
| Raw DOP | 1.016701 +/- 0.000093 | 1.000054 +/- 0.000025 | 0.849456 +/- 0.032666 |
| Length of mean orientation vector | 0.999999992 | 0.999999995 | 0.201172379 |

## Static-state axis distances

Angles of normalized mean orientation vectors to signed Stokes poles, in **Poincare degrees**. These are not ellipse-angle rotations. H/V correspond to +S1/-S1 in this coordinate convention.

| Pole | A only | B only |
| --- | --- | --- |
| +S1 | 5.416420 | 173.441322 |
| -S1 | 174.583580 | 6.558678 |
| +S2 | 86.674055 | 95.379823 |
| -S2 | 93.325945 | 84.620177 |
| +S3 | 85.729805 | 86.259542 |
| -S3 | 94.270195 | 93.740458 |

A–B mean-direction separation: **171.730100°**; departure from antipodal: **8.269900°**.

## Driven A+B and the S1=0 circle

S1 = **0.078475 +/- 0.060394**; observed range **-0.074715 to 0.220344**.

Signed latitude from S1=0 = **4.509571 +/- 3.477757°**; RMS distance **5.690416°**; range **-4.284819 to 12.729229°**.

S2–S3 radius = **0.995077 +/- 0.005542**. The samples lie near, but not exactly on, the S1=0 circle. The shortest arc containing sampled `atan2(S3,S2)` angles is **302.968682°**, leaving a largest unsampled gap of **57.031318°**. This is sampled coverage, not the number or extent of rotations. No phase unwrapping is used.

## Definitions and limits

- All values are mean +/- sample SD (N-1), not uncertainty of the mean or absolute accuracy.
- External phi1 drive during A+B is operator-supplied context; no drive waveform, phase, or frequency is inferred.
- A/B are H-like/V-like in the recorded PAX frame (+S1/-S1); no axis rotation or model transfer is applied.
- A+B averages describe time occupancy of a moving state; signed ellipticity can cancel. No single ellipse represents it.
- CSV Stokes are reconstructed unit directions. Their mean length is not the PAX DOP or an intensity-weighted DOP.
- Raw DOP >1: A 241/241, B 239/241. Retained unchanged; absolute accuracy is not established.
- Run metadata says PAX at C; catalog setup says final output. Location was not independently verified in this report.
- No fitting, row rejection, smoothing, detrending, drive reconstruction, DOP clipping, or model updates.
- Ellipse minor/major ratio is `abs(tan(eta))`, computed per sample before averaging; signed ratio is also in `statistics.csv`.
- Axis distances are `acos(±s_j/||s||)`; circle latitude is `asin(s1)`. Stokes use twice the ellipse angles.
- Coordinate convention: [Thorlabs Poincare-sphere reference](https://www.thorlabs.com/newgrouppage9.cfm?objectgroup_id=12211); acquisition daemon declares theta/eta in radians. No vendor accuracy is assumed.

## Files and repeatability

`statistics.csv`: per-condition means, SD, extrema. `axis-angles.csv`: every signed-axis distance distribution plus static mean-direction angles. `block-means.csv`: six contiguous approximately 10 s blocks per condition; compare their means for slow variation without fitting. `summary.json`: full numerical summary, formulas, checks, source/script hashes and versions. `report.pdf`, `static-paths.png`, `combined-paths.png`: figures.

Rebuild offline with `python path/to/polarization-report/analyze.py`. Only files inside this analysis directory are regenerated; original CSV, recipe, run status and report remain unchanged.
