# PAX mount comparison: standard post versus rubber caps

[PDF report](report.pdf) compares 2026-09-24/203850 (standard post mount) with 2026-09-25/204124 (rubber-cap-supported mount). The 203321 run supplies only the detailed mount comment; no measurements from it are included.

- `plots/`: six standalone visual diagnostics.
- `comparison.csv`: derived PD metrics, not duplicated raw samples.
- `analysis.json`: PD, Stokes and peak-band results.
- `provenance.json`: source hashes, assumptions and regeneration command.
- `analyze.py`: offline reproducible analysis using the suite report styling.

The original experiment directories remain unchanged. The report distinguishes reduced PD noise from changed polarization and optical operating point.
