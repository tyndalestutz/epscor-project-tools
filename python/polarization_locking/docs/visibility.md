# Contrast: PD, PAX or both; active or passive

Measures observed intensity contrast from signed PD voltage or PAX total
optical power. Choose passive acquisition under an existing drive, or actively
drive one RP phase output. Follow the
[operating procedure](operating-procedure.md).

```bash
python python/polarization_locking/lock.py --run pd-visibility --pd-input in2 --frequency 0.5
python python/polarization_locking/lock.py --run pd-visibility --source pax --frequency 0.5
python python/polarization_locking/lock.py --run pd-visibility --source both --mode active --frequency 0.5
```

Replace 0.5 with the actual drive frequency. Use the menu or
[example recipe](../profiles/visibility-in2-example.json) to record the setup in
`bench_notes`, edit duration, and save a recipe. If known, add `--dark-voltage VALUE`
using the signed blocked-light voltage at unchanged detector gain, coupling,
and RP range. The existing `pd-visibility` test key is retained for recipe
compatibility. In the interactive menu, entering contrast asks only two questions:
**PD / PAX / both**, then **passive / active**. The parameter table follows;
use **c** to change these choices. They are not rows in the parameter table.
CLI/recipes use `--source` / `visibility_source` and `--mode` / `visibility_mode`.
Existing recipes default to PD and passive.

In active mode, edit these ordinary table parameters (no further selection chain):

| Parameter | Default / choices |
| --- | --- |
| `visibility_axis` | `phi1` = OUT1; `phi2` = OUT2 |
| `visibility_waveform` | `sin` (default), `cos`, `triangle`, `sawtooth`, `square` |
| `visibility_frequency_hz` | 0.5 Hz requested in active mode; actual external frequency in passive mode |
| `visibility_amplitude_v` | 0.3875 V peak |
| `visibility_offset_v` | 0.3875 V, giving 0–0.775 V by default |
| `duration_s` | 12 s, in run options |

The other output is held at zero. Range/mapping validation happens before
connection. Active mode starts the waveform after PD baseline preparation and
PAX readiness, then returns both outputs to zero on completion/interruption.
`drive.json` retains requested and actual ASG frequency, waveform, amplitude,
offset and output. Capture duration and contrast calculations use actual ASG
frequency after quantization. These are electrical commands, not a fitted phase
calibration. Triangle/sawtooth sweeps may be more useful for traversing fringes
than square waves, which only sample two voltage levels.

When no `--dark-voltage` / `visibility_dark_voltage_v` is supplied, PD mode
prompts you to block all detector light, saves a signed dark trace and its
mean, then prompts you to unblock the detector. Keep gain, coupling, input
range and electronics unchanged. The timed light acquisition starts afterward.
Negative dark and signal voltages are retained exactly. Do not substitute a
fringe minimum for the blocked-light baseline. A provided baseline bypasses
the two prompts; explicitly providing zero means you have established zero.

## PD acquisition and definition

Records at least two declared drive periods per trace for roughly 12 seconds
by default, finishing the last trace. FPGA decimation averaging is enabled;
each capture is independent. Frequency must be approximately 0.233–1000 Hz:
slower drives cannot fit two periods in the supported buffer duration. Actual
duration, sample interval, decimation, input, averaging, and UTC start are logged.

The PD must be DC-coupled, linear, and unsaturated, and the drive must traverse
fringe maxima and minima. Declared time coverage does not verify generator
frequency or prove a full optical fringe excursion.

`I = polarity × (V − Vdark)` and `visibility = (Imax − Imin)/(Imax + Imin)`.
Polarity is inferred from the mean relative to the dark level. Extrema use
nonoverlapping averages of about 1/64 of a drive period, not raw noise extrema
or 5th/95th percentiles. The bin duration is recorded. Narrow fringes can be
attenuated; inspect saved traces and bandwidth sensitivity.

Clipping in recorded samples, invalid intensity baseline, or excursion below
six times the estimated combined bin standard error suppresses the percentage.
Within-bin scatter estimates noise; it is not a complete uncertainty model.
Capture scatter measures repeatability. Without a measured or explicitly
provided dark baseline, analysis retains signed voltage statistics but reports
no visibility. AC-coupled data cannot establish the required DC level.

## PAX acquisition and definition

PAX mode records each fresh `ptotal` in watts with elapsed/UTC time, host
request/receive times, device timestamp/revisions, ADC diagnostics and
polarization telemetry. It reuses the five-second daemon startup delay and
the shared advancing-record checks. No PD voltage correction is applied to PAX.
Low DOP is not a reason to reject a valid power reading.

`visibility = (Pmax - Pmin)/(Pmax + Pmin)` uses extrema of the recorded
instrument total power. This is observed contrast at the PAX sampling rate,
with no additional optical-background subtraction, cycle fitting or binning.
The default duration is 12 seconds, with a nominal 0.05-second polling period
(`visibility_pax_sample_period_s`); fresh measurements determine actual cadence.
The log and summary report cadence and samples per declared drive cycle.
At least two drive periods must be recorded; the final sample can extend the
requested duration. Choose a slow drive with enough samples to capture narrow
fringes. Hardware integration and missed extrema can affect observed contrast.
`visibility_dark_voltage_v` is a PD-only setting and is ignored for PAX power,
so switching detectors does not require clearing previously entered parameters.

## Both detectors

Both sensors must receive light during the same run, for example via a beam
split; record the collection arrangement in the setup notes. PD dark preparation
is unchanged. During each FPGA scope trace, the suite polls fresh PAX readings
through Pyrpl's asynchronous acquisition/event loop. There are no separate
sequential PD and PAX runs and no worker threads owning instrument clients.

`data.csv` retains PD capture statistics and per-capture PAX sample count,
power extrema and sampled contrast. `pax.csv` keeps every PAX snapshot with
matching `capture` ID and the same host elapsed-time origin. PD capture start/end
times and PAX request/receive times are retained. `visibility.json` contains PD
statistics and a nested PAX summary; the standard PDF includes both sources.
PD binned contrast and PAX sampled contrast have different bandwidths. Their
acquisitions overlap, but their integration windows are not hardware synchronized.

## Hardware contract

In passive PD/both mode, only the installed monitor transport starts on `rp_scope_port` (2223 by default).
The FPGA must already be Pyrpl. SSH details come from `rp_config`; saved module
settings are never applied. The scope client rejects actuator-register writes,
restores scope configuration registers, and closes its transport. Passive PAX-only
mode does not contact RP. All passive modes preserve existing output waveforms.
Active modes connect the normal RP controller and own its outputs; only modes
containing PAX connect PAX. Both connects both selected detectors. Concurrent
scope use by another session is not supported.

## Evidence

Alongside the normal recipe, status, log, and PDF:

| File | Contents |
| --- | --- |
| `data.csv` | PD: signed per-capture extrema, Vpp, baseline, noise, polarity and contrast. PAX: one row per fresh power measurement and diagnostics |
| `pax.csv` | Both mode: all PAX samples, common host timing and PD capture IDs |
| `drive.json` | Active: actual ASG drive readback. Passive: declared external frequency; existing outputs unchanged |
| `capture-NNN.npz` | PD: original signed `voltage_v` and `time_s`, saved before analysis |
| `dark.npz`, `dark.json` | PD automatic baseline: original signed dark trace and statistics/acquisition settings |
| `visibility.json` | Selected source, definition, summary, declared frequency; PD signed dark baseline/provenance or PAX power extrema/cadence |

Automatically measured dark values live in `dark.json`, `data.csv`, and
`visibility.json`; the recipe keeps null so replay measures a fresh baseline.
Reports plot signed PD signals and baseline, or PAX power versus time, with the
usual run metadata. Interrupted runs retain acquired data and an available
summary; too little PAX time coverage produces a null visibility.

A completed acquisition can have no valid visibility estimate. The
[September 21 audit](../../../experiments/polarization_locking/2026-09-21/README.md)
explains why an earlier ~49% percentile result was rejected, and why the new
~21% apparent contrast still is not a calibrated isolated-axis measurement.
These are historical observations, not expected values for another run.
