# Experiment data layout

New polarization-locking commands create one self-contained run directory:

```text
experiments/polarization_locking/YYYY-MM-DD/
  HHMMSS_<test-kind>_<your-label>/
    data.csv
    report.pdf       # only when the command ends with `pdf`
```

For example:

```text
cross-sweep phi1 phi1-full pdf
```

creates a dated folder containing `data.csv` and `report.pdf`; the supplied
name is a human-readable label, not a path to manage manually. The CLI prints
the folder before a measurement starts.

`polarization_locking/legacy/flat-archive/` contains earlier results retained
with their original names. Existing `data/MMDD/` directories hold the separate
VNA/transfer-function data and are already organized by acquisition date.
