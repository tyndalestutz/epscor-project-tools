# Offline bench diagnostics

These scripts analyze lock-acquisition CSV files and write plots/reports.
The lock CLI invokes them after the corresponding acquisition. Each supports
`--help` when run directly from the repository root, for example:

```bash
python python/polarization_locking/analysis/analyze_first_npbs_d.py --help
```

- `analyze_first_npbs_d.py`: first-NPBS D-port scan.
- `analyze_first_npbs_d_isolation.py`: blocked-path isolation scan.
- `analyze_first_npbs_d_polarizer.py`: D-port analyzer-polarizer scan.
- `analyze_phi1_step_map.py`: phi1 voltage-step response.

Reusable Jones models and model fitting are in
[../../field_propogation/](../../field_propogation/README.md).
