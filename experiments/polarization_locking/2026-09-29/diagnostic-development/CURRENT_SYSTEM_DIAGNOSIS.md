# Interferometer diagnosis checkpoint - 2026-09-29

## Current bottom line

The acquisition masked some system behavior, but did not create the main optical result.

1. **Phi2 has strong real power coupling.** Its original within-segment robust power contrast was 59.9-62.0% at every fixed phi1 bias. Mean power changed only 2.15% across fixed phi1 bias.
2. **The system is history dependent.** Returning to the same command after a phi2 excursion changed mean Stokes by 9.86 degrees and mean power by 11.7%.
3. **OUT2 to IN2 is now a valid reference.** IN2 was physically jumpered for HV while IN1 was jumpered for LV. Moving IN2 to LV changed its DC slope from 0.04007 to 1.01624 measured V per commanded OUT2 V. No software compensation was applied.
4. **The former phi2 inversion was separate.** The persisted Pyrpl profile had ASG1 start phase 180 degrees. Waveform setup now explicitly commands 0 degrees; the post-fix phi1 and phi2 sine phases agree within 0.33 degrees.
5. **RP trace timing is now FPGA anchored.** In the compact live validation, all four captures used the FPGA trigger/current counters. Saved RP sample-time uncertainty was about 1.25 ms, and fitted electrical phase was within 0.11 degrees of the command clock on both axes.

The timestamp and electrical-reference layers now pass their calibration checks, but this does not by itself establish that every PAX polarization record is a faithful physical state. A controlled optical validation is still required before a quantitative comparison to the ideal model. The qualitative phi2-dependent amplitude in the old PAX data remains robust.

## Electrical loopback before and after the jumper correction

| Quantity | Before: IN2 HV | After: IN2 LV |
|---|---:|---:|
| OUT1 to IN1 DC slope | 1.00940 V/V | 1.00941 V/V |
| OUT2 to IN2 DC slope | 0.0400696 V/V | 1.01624 V/V |
| OUT1 sine measured amplitude | 0.39091 V | 0.39089 V |
| OUT2 sine measured amplitude | 0.015545 V | 0.39359 V |
| Commanded sine amplitude | 0.38745 V | 0.38745 V |
| Phi1 sine phase vs capture start | 11.36 deg | 11.12 deg |
| Phi2 sine phase vs capture start | -169.14 deg | 10.79 deg |

The post-fix DC residual RMS is 0.107 mV for OUT2 to IN2 and the correlation is 0.99999993. Electrical cross-pickup remains negligible. Both RP outputs were returned to zero by the verifier cleanup.

## Compensation audit

No IN2 attenuation correction exists in the active acquisition or reporting path:

- Pyrpl scope arrays are converted directly to NumPy voltage arrays.
- IN1 and IN2 are boxcar-decimated identically.
- Interpolation selects the matching channel without channel-specific multiplication or division.
- Reports load measured_in1_v, measured_in2_v, and measured_target_v directly.
- The cross-sweep reference validation computes measured amplitude divided by commanded amplitude only as a quality metric; it does not rescale the data.

The current actuator-terminal conversion is 16.875 actuator V per RP V for both axes. That is a separate downstream driver-gain model, not an IN2 correction. Repository history contains an obsolete phi2 value of 150 V/V for a former x8.89 preamp configuration, but that value is not active. One unit test deliberately supplies 150 V/V to test generic conversion arithmetic; it does not configure an experiment.

The post-jumper unity slope is also runtime evidence against hidden compensation: any retained 20x or 25x correction would now produce a grossly non-unity result.

## Other acquisition corrections

- Old 1 s transitions took roughly 2.7-3.2 s and performed 100 networked ASG setup operations. Ten corrected electrical transitions took about 1.0001 s each.
- Cross-sweep sine setup now explicitly resets start phase to 0 degrees for either ASG.
- PAX rows now use elapsed_s = transaction midpoint minus the configured empirical latency. The current default is 0.120 s.
- Raw PAX request, receive, midpoint, applied correction, and transaction half-width remain preserved.
- Reference validation rejects gross gain, phase, or residual failures rather than treating any present loopback as trustworthy.

## Corrected short optical-run snapshot

This run predates the IN2 jumper repair but follows the ramp fix. Its PAX quantities remain useful; its phi2 voltage reference is not used as calibrated delivered voltage.

| Quantity | Phi1 driven | Phi2 driven |
|---|---:|---:|
| Mean PAX power | 536 uW | 706 uW |
| Robust power contrast | 24.15% | 60.32% |
| Median DoP | 0.942 | 0.942 |
| Fraction DoP below 0.9 | 1.7% | 18.2% |
| Plane-fit residual | 0.0428 | 0.0520 |
| Pointwise angular scatter | 8.69 deg | 4.18 deg above DoP 0.9; 9.11 deg below |

## End-to-end timing calibration result

A short two-axis calibration run was completed after the IN2 jumper repair. Both reference channels passed their automatic sanity checks: phi1 gain 1.00991 and phi2 gain 1.01720. Using the preserved raw PAX transaction midpoint, a 0-250 ms lag scan compared rising and falling branches at equal nominal ASG voltage.

The configured 120 ms correction improved the combined normalized branch-consistency score by 42.6% relative to no correction. The combined optimum was 136 ms and the 5%-of-best interval was 108-158 ms, so retaining 120 ms is supported at the resolution justified by this run. Axis-level normalized-Stokes optima were 110 ms for phi1 and 137 ms for phi2. PAX power and DoP comparisons independently favored a positive lag.

That first run validated PAX timing relative to the nominal ASG command clock only. Its host-derived RP trace timestamp uncertainty was about 60-203 ms. Phi1 had no analysis rows above DoP 0.9 and phi2 had only 18.2%, so its exact optimum remains an empirical estimate rather than a precision latency constant.

## FPGA-timestamp validation

The subsequent compact validation anchored every RP scope capture to the FPGA trigger/current 125-MHz counters. Per-capture host reads bracket the current-counter query; the old host request/completion estimate is retained only as an explicit fallback. All four captures used the FPGA path. The saved sample-time uncertainty was 1.249-1.280 ms, compared with 61.9-206.9 ms for the retained host fallback. Reference fits measured phi1 gain 1.00978 and phase +0.1025 degrees, and phi2 gain 1.01815 and phase +0.1089 degrees. Both axes passed the reference sanity gate.

The PAX audit was then repeated against the measured FPGA-timed RP voltage, interpolating only within each capture and never across capture gaps. The combined optimum was 106 ms; the 5%-of-best interval was 98-131 ms. The configured 120 ms correction improved the combined score by 50.5% versus zero lag and lies inside that 5% interval. The nominal command-clock analysis of the same run gave a 116 ms optimum and a 103-134 ms 5% interval.

The two clocks therefore independently support the current 120 ms correction at the precision available here. This is not a 1-ms measurement of PAX latency: each PAX transaction lasted about 143.6 ms, corresponding to a saved midpoint uncertainty of about 71.8 ms, and individual optical observables did not share one sharp optimum. In particular, phi1 Stokes favored 152 ms while phi2 Stokes favored 97 ms; phi1 power did not favor the correction. The aggregate result supports a practical association time, not an instrument-internal timestamp or proof of zero optical hysteresis.

## Next calibration priority

The electrical amplitude path, RP trace clock, and the 120 ms PAX association now pass the current calibration checks. The remaining measurement-validity risk is optical/PAX state quality rather than a gross RP clock error. The next controlled validation should use static or stepped states with generous settling to test PAX repeatability, DoP/ADC quality, and state dependence independently of sweep timing. Only after that should an optical design vary bias order to separate voltage dependence from time, creep, and hysteresis.

## Provenance

- Original phi1: experiments/polarization_locking/2026-09-29/185740_cross-sweep_quick-test
- Original phi2: experiments/polarization_locking/2026-09-29/191137_cross-sweep_quick-test-phi2
- Corrected short optical run: experiments/polarization_locking/2026-09-29/diagnostic-development/2026-09-29/194533_cross-sweep_short-low-power-verification
- Pre-jumper electrical loopback: experiments/polarization_locking/2026-09-29/diagnostic-development/rp-loopback-after-transition-fix
- Post-jumper electrical loopback: experiments/polarization_locking/2026-09-29/diagnostic-development/rp-loopback-after-in2-lv-jumper-verified
- Post-repair timing calibration: experiments/polarization_locking/2026-09-29/diagnostic-development/2026-09-29/211549_cross-sweep_timing-calibration-post-in2-lv
- Static PAX hold: experiments/polarization_locking/2026-09-29/diagnostic-development/pax-static-high-low-high
- Failed no-hardware attempt (wrong Python environment, no RP connection): experiments/polarization_locking/2026-09-29/diagnostic-development/rp-loopback-after-in2-lv-jumper
- FPGA-timestamp validation: experiments/polarization_locking/2026-09-29/diagnostic-development/2026-09-29/213414_cross-sweep_fpga-timestamp-validation

No source experiment data were altered. Acquisition changes affect future runs only.
