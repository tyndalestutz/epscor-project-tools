# Future physical regression checklist

Non-executable planning document. No instrument operation is authorized or
reported here. Before hardware-related refactoring, the operator/reviewer must
approve the setup, instrument ownership, safety limits, stop conditions,
measurement method, repetitions and quantitative acceptance bounds. Preserve
pre-change and post-change evidence under the [retention policy](data-retention.md).
Passing [offline tests](offline-testing.md) does not satisfy this checklist.

| Check | Required comparison evidence | Acceptance gate |
| --- | --- | --- |
| RP initialization/restoration | Recorded starting state, output mode/offset/readbacks and independent voltage observations through startup, completion, failure and interruption | Match the approved mode-specific contract, including passive monitoring; return to the approved safe state within pre-agreed bounds. Failed cleanup leaves physical state unverified until independently checked. |
| PAX freshness | Raw device timestamps/revisions plus host request/receive traces, integration settings and deliberate stale/unavailable cases | Demonstrate genuinely advancing records under the documented policy; stale/incomplete records are retained and rejected/flagged as specified. Host read time alone is insufficient. |
| Ownership/daemon | Process/service ownership, concurrent-client conditions, startup, disconnect, refusal and recovery observations | Only the intended owner starts/stops devices/services; unrelated sessions survive. No conflicting acquisition or orphan owner after recovery. |
| Routing | Recorded OUT/channel-to-driver-to-actuator path, reference planes, gain/sign and independently checked channel identity | Physical mapping matches the approved recipe and documented safe limits before active tests. |
| Actual voltage | Calibrated independent measurement at specified electrical reference plane, command/readback traces, loading, uncertainty and bandwidth | Delivered voltage agrees with the independently justified transfer relation and uncertainty bounds; register offset is never accepted as a delivered-voltage measurement. |
| Timing/alignment | Host/device clock domains, scope sample interval, PAX integration windows, external timing reference where needed, settling observations and event traces | Measured order, latency/jitter and alignment meet prospectively chosen bounds for the measurement. Sequential records must not be claimed sample-synchronous. |
| Repetition/interruption | Repeated complete runs plus authorized interruptions at initialization, active acquisition and cleanup; retained partial data/status and recovery traces | No unexplained loss of completed raw records; failures remain distinguishable; restart restores ownership and approved safe configuration reproducibly. |
| Optical response | Setup/alignment/power/wavelength/calibration, detector ranges, reference states, independent observables and uncertainties | Compare like conditions with predetermined observable-specific tolerances; retain disagreement, saturation, low-DoP and other anomalous records. Angle-derived direction is not absolute Stokes or evidence of full polarimeter calibration. |

Before comparing versions, freeze the test procedure and independently justified
bounds from instrument specifications, calibration uncertainties and experimental
needs. No universal numerical bench tolerance is invented here. Record sample
counts, repeat count, exclusions and failure criteria in the approved protocol.
Control or document alignment/drift and other changed conditions; retain raw
records and processing commands for both versions. Acceptance requires review
of discrepancies and uncertainty, not merely successful acquisition or a report.
If bench access is unavailable, mark the affected physical claim unverified and
limit conclusions to the software contract actually tested.
