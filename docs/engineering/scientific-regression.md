# Scientific regression coverage and limits

PR 3 starts at integrated `42e8d61c7d5d4b8198510cc34eb3df5fb33187b6`.
The existing 445 tests remain intact: 8 offline boundary, 302 locking,
97 high-speed and 38 field. See [offline setup](offline-testing.md) and
[fixture qualification](../../tests/fixtures/scientific/README.md).

## Source-reviewed coverage inventory

| Contract | Existing protection | PR 3 addition / remaining gap |
| --- | --- | --- |
| Run lifecycle/status/interruption | Locking test_suite/test_reports/test_diagnostic_cleanup; high-speed test_framework/test_eom_acquisition/test_vpi cover independent acquisition, cleanup and postprocessing faults | Compound high-speed acquisition interruption/read failure plus cleanup failure, retained RUNNING state before connection, final error/status separation |
| Raw measurements, units, provenance | EOM signed buffers, byte offsets, corruption/nonfinite retention; locking partial CSV/session provenance; field acquisition/freeze inputs | Compact external synthetic fixture locks full event/call ordering, complete raw-buffer coverage, exact units/schema and unchanged replay inputs |
| RP/PAX state contracts | test_rp_interface ramps/DC/phase, test_session_provenance ownership/startup, EOM adapter mapping and cleanup, Vπ fresh-PAX-before-RP | New mock call sequence protects baseline-before-command and acquisition-before-disconnect; device behavior still requires bench evidence |
| Jones/Stokes/geometry | Field analytic MZI fields, harmonics/derivatives, conservation, dark/singular states; spatial independent integration; locking Poincare/control/trajectory helpers | Independent integer Jones basis/elliptical states, Q3 sign, global phase/power scaling and exact dark threshold boundary |
| Rough Vπ and invalid acquisition | test_vpi covers known rotation, plateau without observed pi, unavailable fits, synthetic-only results, missing metadata/events, malformed/incomplete events, PAX/CSV mismatch and nonadvancing host/device time; invalid evidence cannot reach fit | Preserve these assertions unchanged; no new physical coefficient or acceptance threshold. Broader corruption/schema variants remain future work |
| Offline output | Locking PDF/report tests, high-speed offline quality replay, field TeX/PDF and held-out comparisons | New compound-fault report/quality replay must preserve raw inputs and stable semantic Markdown; no arbitrary timestamp/PDF byte goldens |

This is a targeted inventory, not a claim of exhaustive path coverage. The new
22 cases span two applications: 19 analytic field cases and three synthetic
high-speed lifecycle cases. Locking's existing 302 cases remain unchanged.
Production code, scientific tolerances and archived measurements are unchanged.

## Retained measured evidence

Existing `python/field_propogation/tests/test_simulation.py` reads the two
2026-08-13 locking runs listed in the [data inventory](data-inventory.md).
The calibration fit JSON and fringe-prediction CSV are historical fitted/derived
quantities, not independent measurements. The held-out map `data.csv` is measured
input; its validation summary is derived comparison evidence. Existing tests
preserve saved numerical relationships and the held-out disagreement with the
model, rather than claiming agreement or re-estimating parameters.

These records remain read-only in place, with their original provenance; no new
subset or copied acquisition is introduced. Their setup and historical model
limitations restrict their meaning. Existing archive-comparison tolerances stay
unchanged. Fixture selection does not certify the archive's physical validity.

## Deferred coverage

Representative measured high-speed calibration/Stokes reconstruction references
do not exist as completed validated capabilities. Do not turn synthetic Vπ
rotation into such a reference. Future characterization should qualify additional
locking command/event traces, PAX malformed/stale telemetry combinations,
postprocessing fault combinations and versioned schema variants based on source
review and actual consumer needs. Any demonstrated production bug needs a
separate correction; none is asserted by this fixture set.

Physical timing, delivered voltage, optical validity and real restoration remain
outside software validation. The [bench checklist](bench-regression-checklist.md)
is a non-executable protocol outline requiring separate authorization.
