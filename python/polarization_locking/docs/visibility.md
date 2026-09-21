# Passive photodiode visibility

Measures observed intensity contrast on a declared RP input under external
actuator drive. It does not command RP outputs. Follow the
[operating procedure](operating-procedure.md).

```bash
python python/polarization_locking/lock.py --run pd-visibility --pd-input in2 --frequency 0.5
```

Replace 0.5 with the actual drive frequency. Use the menu or
[example recipe](../profiles/visibility-in2-example.json) to record the setup in
`bench_notes`, edit duration, and save a recipe. If known, add `--dark-voltage VALUE`
using the signed blocked-light voltage at unchanged detector gain, coupling,
and RP range.

## Acquisition and definition

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
Capture scatter measures repeatability. Without a dark measurement, results
are **apparent contrast assuming zero dark offset**. AC-coupled data cannot
establish the required DC level.

## Hardware contract

Only the installed monitor transport starts on `rp_scope_port` (2223 by default).
The FPGA must already be Pyrpl. SSH details come from `rp_config`; saved module
settings are never applied. The scope client rejects actuator-register writes,
restores scope configuration registers, and closes its transport. PAX is never
connected. Existing output waveforms remain unchanged: record them and their
cabling in the setup notes. Concurrent scope use is not supported.

## Evidence

Alongside the normal recipe, status, log, and PDF:

| File | Contents |
| --- | --- |
| `data.csv` | Per-capture extrema, Vpp, noise, polarity, contrast and validity |
| `capture-NNN.npz` | `voltage_v` and `time_s`, saved before analysis |
| `visibility.json` | Definition, summary, declared frequency, dark level and caveats |

A completed acquisition can have no valid visibility estimate. The
[September 21 audit](../../../experiments/polarization_locking/2026-09-21/README.md)
explains why an earlier ~49% percentile result was rejected, and why the new
~21% apparent contrast still is not a calibrated isolated-axis measurement.
These are historical observations, not expected values for another run.
