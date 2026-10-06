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
- `analyze_cross_sweep_timing.py`: non-destructive PAX latency audit against FPGA-timed measured RP voltage when available, with the nominal ASG clock retained for comparison.
- `verify_rp_loopbacks.py`: bounded dual-channel electrical loopback verification.
- `verify_pax_power_hold.py`: static high/low/high PAX stability check.

Reusable Jones models and independent measurement acquisition are in
[../../field_propogation/](../../field_propogation/README.md).

### Development diagnostics

Run the hardware verifiers only in the established bench environment, with the
loopback wiring/reference plane checked. Output directories must be new:

```bash
PYTHONPATH=python python -m polarization_locking.analysis.verify_rp_loopbacks /path/to/new-loopback-run
PYTHONPATH=python python -m polarization_locking.analysis.verify_pax_power_hold /path/to/new-pax-hold --duration 20 --settle 5
PYTHONPATH=python python -m polarization_locking.analysis.analyze_cross_sweep_timing /path/to/existing-cross-sweep
```

The latency scan is a non-destructive association diagnostic. Its branch-matching
optimum can include physical hysteresis, drift and PAX effects; it is not an
independent instrument-latency measurement. Reference sanity criteria are explicit
heuristics, not delivered-actuator calibration. Existing raw timing remains saved.
