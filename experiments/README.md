# Experiment records

Reusable acquisition, analysis, and test code lives under `python/`. This tree
holds recorded runs, physical setup/validity notes, run-local analysis, and
clearly identified historical development scripts.

| Project | Records and instructions |
| --- | --- |
| Polarization locking | [Run index](polarization_locking/README.md) and [suite operating procedure](../python/polarization_locking/docs/operating-procedure.md) |
| Polarization visualization | [Experiment notes](polarization_visualization/README.md) |
| Field propagation | [Experiment notes](field_propagation/README.md) and [measurement protocol](../python/field_propogation/docs/measurement_acquisition.md) |

The polarization-locking menu writes one dated folder per run, containing the
recipe, status/provenance, console log, measurements, and a PDF when reporting
succeeds. The old positional `... pdf` command syntax is historical; use named
tests and recipes as documented in the suite README.

Preserve raw data and original metadata, including failed/partial runs. Put new
interpretations in separate analysis files and label limitations. Do not infer
missing completion status or wiring from a folder name. Acquisition success,
report generation, and physical validity are separate judgments.

Earlier polarization results remain in `polarization_locking/legacy/flat-archive/`.
The repository's `data/MMDD/` directories contain separate VNA/transfer-function
data. Neither location is the destination for new suite runs.
