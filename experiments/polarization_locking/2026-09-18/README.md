# September 18: phi1 calibration and timing development

This session combines supported acquisitions with later standalone development
captures. Raw files and original metadata are preserved. New measurements should
follow the [suite SOP](../../../python/polarization_locking/docs/operating-procedure.md).

## Existing calibration records

| Folder | Purpose / evidence |
| --- | --- |
| `191733_first-npbs-d-test_first-npbs-d-test` | Catalog D-port run with recipe/status/report and command-following analysis |
| `195031_raw-phi1_repeatability-1s` | Raw repeated phi1 steps with 1 s settling; independent raw-collector schema |
| `200038_raw-phi1_fixed-0p4-drift` | Fixed 0.4 V command observation |
| `200242_raw-phi1_fine-brackets-3s` | Fine step/bracket study with 3 s settling |
| `raw-phi1-session` | Session-specific comparison script, summary and derived report |

Consult each `run.json` and raw report for voltage definitions and limitations.
Readbacks of an FPGA command are not measured piezo terminal voltage.

## Standalone Stokes/timing captures

Row counts below describe the files present at cleanup; they are not a claim
that an intended protocol completed. Single-CSV runs lack the current suite's
recipe, environment, and completion-status records.

| Folder | Data rows | Assessment |
| --- | --- | --- |
| `214633_live-s123-phi1` | 1,048 | Exploratory live Stokes CSV; completion unknown |
| `215818_live-s123-phi1` | 258 | Exploratory live Stokes CSV; completion unknown |
| `215941_live-s123-phi1` | 174 | Exploratory live Stokes CSV; completion unknown |
| `220820_live-phase-diagnostic-phi1` | 422 | Exploratory phase/timing CSV; completion unknown |
| `222058_live-s123-phi1` | 1 | Insufficient standalone observation for repeatability |
| `222202_live-s123-phi1` | 260 | Exploratory live Stokes CSV; completion unknown |
| `222614_stokes-timing-test-phi1-0p5Hz-120s` | 1,052 | Timing development CSV; folder label alone is not a verified frequency/duration |
| `224027_stokes-timing-test-phi1-0p5Hz-120s` | 864 | Timing development CSV; same limitation |
| `225616_stokes-timing-test-phi1` | 0 dynamic | Header-only attempt; metadata has no completion record |
| `225751_stokes-timing-test-phi1` | 214 dynamic; 324 static | Dynamic/static dataset with metadata and run-local derived paper plots |

The last dataset's metadata says `completed`; the archived collector could also
write that status after an interruption. Validate intended coverage from the
rows and recorded parameters. Its `polarization_paper_plots.py` and `paper_plots/`
are session-specific derived analysis, not validated reusable suite routines.
Fits and provisional conversion factors in these plots do not establish a new
bench calibration automatically.

The [three archived acquisition tools](stokes-timing-tools/README.md) preserve
their original source bytes and document the limitations. Empty local folders
from other startup attempts have no Git-tracked content and no measurement evidence.
