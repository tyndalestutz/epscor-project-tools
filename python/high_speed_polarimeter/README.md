# High-speed polarimeter acquisition framework

Precision high-speed Stokes polarimetry: input polarization → EOM1 (nominal 0°)
→ EOM2 (nominal 45°) → PBS/analyzer → photodiode. The **PD is the mandatory primary
EOM response measurement**; the slower PAX is the polarization reference. Actual
axes, voltage response, reference-plane relationship and timing require bench
characterization. The PD qualification experiment below adds a diagnostic transfer model; it does not establish a calibrated Stokes reconstruction.

## Experiments and recipes

The menu includes **`eom_vpi_hysteresis`**, the PAX-only rough Vπ check. Defaults
are one 0→0.9→0 V command cycle (31 points per direction, duplicate turning point,
3 fresh PAX records per point), preceded by a **60-second undriven baseline**.
Both outputs are commanded zero before the baseline. **3-second stage pauses**
occur before baseline, before forward sweep, at reversal, and after return to zero;
individual points use a 0.15 s ramp and 0.25 s settling. The unselected output stays
zero. Edit `undriven_baseline_s`, `stage_settle_s`, `ramp_duration_s`, `settle_s`,
limits, points, repeats and sample count in the usual persistent menu.

Baseline `s1/s2/s3` time means are **not unit-renormalized**. Time integrals use
trapezoidal estimates over adjacent valid host-time records, excluding intervals
over 5× median cadence and reversed/invalid times. The report records valid time
coverage, excluded intervals, temporal variance/SD, angular RMS wander, DoP and
power. Temporal variance uses time-weighted population moments, not N−1 sample
variance. This is a downstream **zero-drive PAX reference**, not an independently
measured pre-EOM input state. Low-DoP samples are retained with uncertainty warnings.

The sweep saves every raw PAX record in `events.jsonl` and `data.csv`, with
`acquisition.json`, resolved `recipe.json`, `run.json`, source provenance and
zero-setting cleanup evidence. No PD buffers are acquired. `analysis.json`,
`quality.json`, `report.md` and `report.pdf` are derived offline. A circle-plane
model infers trajectory phase from explicit three-sample point means; configured
fit range defaults to 0–0.45 command V. These defaults reproduce the initial bench
check, not a universally established linear range. Model residual/coverage checks
are configurable; inspect them and the observed curve. Too-small/nonplanar or
ambiguous trajectories yield **Vπ unavailable**, while retaining baseline-relative
Poincaré excursions. Linear-branch π/slope estimates are labeled **extrapolated**
when π is unobserved; actual crossings are separately interpolated between adjacent
sampled commands. Plateaus stay visible. Phase unwrapping cannot rule out whole
unobserved turns between coarse command steps. Use sufficiently fine sampling.
Forward/reverse differences include hysteresis, drift and settling; temperature
and terminal voltage are not independently measured. Do not equate small response
with zero coupling or claim 100% efficiency without an axis/reference calibration.

Example recipe (hardware confirmation intentionally unset):

```bash
PYTHONPATH=python python -m high_speed_polarimeter --dry-run --profile python/high_speed_polarimeter/profiles/vpi-hysteresis-example.json
PYTHONPATH=python python -m high_speed_polarimeter --mock-run --profile python/high_speed_polarimeter/profiles/vpi-hysteresis-example.json --results-directory /tmp/vpi-mock
```

For hardware, edit/confirm physical channel mapping, limits, voltage chain and PAX
reference plane first; record input-polarization and temperature conditions in the
run comment. Select the menu's Vπ test and Run, or use `--run --profile` with your
local recipe. A constant mock PAX record validates workflow only and never produces
an optical Vπ. Offline `--reevaluate RUN_DIRECTORY` regenerates derived analysis and
reports without instrument access or changing source data.

Open the numbered interactive suite, just like polarization locking:

```bash
python python/high_speed_polarimeter/test-suite.py
# Or, from inside python/high_speed_polarimeter:
python test-suite.py
# Equivalent module launcher from the repository root:
PYTHONPATH=python python -m high_speed_polarimeter
```

Select a test by number or name. The detail menu supports `r run`, `e edit`,
`all edit all`, `s save`, `l load`, `d defaults`, `b back`, and `q quit`, followed
by optional run-name/comment prompts. Recipes and settings persist while the menu
is open. Edited settings, loaded recipes, and run names/comments also persist
across restarts in ignored local settings files, separately for each test.
`d defaults` resets the selected test persistently. Explicit command-line runs
continue to use their recipes/defaults rather than interactive saved settings.
Runs return to the detail menu; cleanup failure closes the suite.
The PAX entry reuses the polarization-locking detail menu and alignment panel.
Its recipes default to this package's `profiles` folder and loaded PAX recipes
are routed to high speed experiment storage. Acquisition recipes retain their
configured results directory. Browsing/editing does not connect instruments.
`mock_lifecycle` Run is explicitly synthetic; EOM Run uses hardware and retains
the existing output-map/voltage-chain/reference-plane validation. Planned tools
are displayed separately and cannot be run. Existing command-line actions remain
available for automation.

- `single_eom_characterization`: select `eom1` or `eom2`, sweep RP command volts
  from start to stop over a configured point count, hold the other EOM at its
  configured command, optionally reverse, and repeat. Both visits to the turning
  point/endpoints are retained. A decreasing start-to-stop sweep is also supported.
- `dual_eom_characterization`: a Cartesian grid (EOM1 outer loop, EOM2 inner loop)
  or explicit ordered `states_v: [[V1, V2], ...]`. Explicit lists take precedence
  over grid arrays and retain duplicates; repeats replay the same order.
- `mock_lifecycle`: original hardware-free framework smoke experiment.

All numerical command values are **RP command volts**, not measured driver/EOM
terminal voltages. `eom1_output`/`eom2_output` map distinct OUT1/OUT2 channels;
`selected_eom` chooses the single swept device. No gain, Vπ or retardance is assumed.

Version-1 JSON recipes retain the existing `experiment`, `label`, `comment` and
`config` structure. Unknown fields, nonfinite values, invalid channels/counts,
out-of-range commands and unsupported experiments fail before initialization.
Saved `recipe.json` contains **all resolved settings**, including defaults.
Sweep defaults (0–0.1 RP command V, 11 points, one repeat, one-second wait), scope
and PAX defaults follow `polarization_locking`; they do not establish a suitable
EOM operating range. Inspect and edit them before bench use.

Relevant controls include `bidirectional`, `repeats`, `settle_s`,
`other_eom_command_v`, `pd_input`, `pd_duration_s`, `pd_scope_decimation`,
`pd_scope_timeout_s`, `pd_fpga_average`, `pax_samples_per_point`, PAX polling/retry
settings/wavelength, addresses/profile, output limits, and notes. PAX reads are
individual records: no host averaging or new dwell model. `pax_measurement_wait_s`
is the legacy wait between requesting and retrieving a record.

Run these exact commands from the repository root (Python 3.10+):

```bash
# Inspect defaults/effective recipe without importing instrument drivers or allocating a run.
PYTHONPATH=python python -m high_speed_polarimeter --list
PYTHONPATH=python python -m high_speed_polarimeter --dry-run --profile python/high_speed_polarimeter/profiles/single-eom-example.json

# Hardware-free acquisitions, with deterministic synthetic counters/PD/PAX values.
PYTHONPATH=python python -m high_speed_polarimeter --mock-run --profile python/high_speed_polarimeter/profiles/single-eom-example.json --results-directory /tmp/polarimeter-single
PYTHONPATH=python python -m high_speed_polarimeter --mock-run --profile python/high_speed_polarimeter/profiles/single-eom-repeated-example.json --results-directory /tmp/polarimeter-repeated
PYTHONPATH=python python -m high_speed_polarimeter --mock-run --profile python/high_speed_polarimeter/profiles/dual-eom-grid-example.json --results-directory /tmp/polarimeter-dual
PYTHONPATH=python python -m high_speed_polarimeter --mock-run --profile python/high_speed_polarimeter/profiles/dual-eom-list-example.json --results-directory /tmp/polarimeter-list

# Real hardware: use an edited local recipe, not an unchanged example.
PYTHONPATH=python python -m high_speed_polarimeter --run --profile python/high_speed_polarimeter/local-config-eom.json

# Re-evaluate the printed run folder offline (no instrument access).
PYTHONPATH=python python -m high_speed_polarimeter --reevaluate /path/to/run/folder

python -m pytest -c python/high_speed_polarimeter/pytest.ini python/high_speed_polarimeter/tests
```

Runtime acquisition requires NumPy (`requirements.txt`); tests require pytest
(`requirements-dev.txt`). Bench mode uses the existing `polarization_locking`
driver environment, including Pyrpl/YAQC/YAQD and USB/VISA support; it adds no
new hardware or plotting dependency. Mock signals verify data flow, not physics.

## Shared PAX alignment panel

Launch the existing polarization-locking PAX panel from this suite, without copying
its implementation:

```bash
PYTHONPATH=python python -m high_speed_polarimeter --dry-run --tool pax-live
PYTHONPATH=python python -m high_speed_polarimeter --run --tool pax-live
```

The panel retains its Save/Discard controls. Saved runs use the existing dated
`pax-live` folder layout, CSV, recipe, metadata, console log and PDF under
`experiments/high_speed_polarimeter`. This tool retains the polarization-locking
run schema and reporting; high speed acquisition quality/re-evaluation does not
apply to it. Only PAX is connected, with the existing PAX worker/cleanup behavior.
Opening the panel requires the existing GUI and PAX driver environment.

Use `--profile python/polarization_locking/profiles/pax-live-example.json` for a
polarization-locking PAX recipe; EOM recipes are not compatible. The launcher
overrides any results folder in that recipe with the high speed default, or with
an explicit `--results-directory`. Other polarization-locking tools are not yet
exposed by this launcher.

For a local bench recipe, copy an example to `local-config-eom.json`, then edit
addresses/profile, PD input, EOM-to-output mapping, RP command limits, wavelength,
PAX settings and actual acquisition/sweep settings. Set `output_map_confirmed`
to true **after checking physical cabling**, and record nonempty `voltage_chain`
and `pax_reference_plane`. Hardware mode requires those fields. The examples leave
confirmation false so they cannot inadvertently use the legacy piezo mapping.
Record actual optical arrangement, power/detector gain and driver limits in
`comment`/`voltage_chain`; no delivered-voltage calibration is assumed. A PAX port
may observe a different plane or split branch; reference-plane equivalence remains
an experimental question. The existing daemon config's serial/model must match
the instrument. No hardware was accessed during software validation.

## Acquisition lifecycle and data format

The runner validates/resolves the recipe, allocates a dated UTC run folder,
records source/dependency/Git provenance, connects the session, initializes scope
settings, and records a pre-sweep PAX baseline. Each point writes a command request,
commands both RP outputs sequentially, records acknowledgement/ASG setting readback,
waits the configured settling interval, acquires all mandatory PD buffers, then
requests each PAX record. The idle EOM has an explicit command. ASG acknowledgement
means the API returned; it does not measure voltage or confirm simultaneous writes.

The scope uses immediate-trigger, one-channel, fixed 16384-sample buffers,
`trace_average=1`, and configured power-of-two decimation. Effective sample spacing,
duration, input and FPGA averaging are recorded after setup. `pd_duration_s` is a
**minimum summed nominal captured time per point**, rounded up to whole buffers;
all overshoot samples are retained. Buffers have host/network gaps and are not
continuous. The timeout applies to each buffer and must exceed its duration.
The upstream [Pyrpl scope implementation](https://github.com/pyrpl-fpga/pyrpl/blob/master/pyrpl/hardware_modules/scope.py)
sets a full post-trigger buffer in immediate mode (the configured trigger delay is
ignored in that mode). Effective trigger settings and scope settings are retained
per capture; changed settings are flagged offline. The installed bench version
must still be verified. Sample rate is scope-reported `1/sampling_time_s`, not estimated from API cadence.
The inherited default FPGA decimation averaging is explicitly recorded and can be
disabled in the recipe. These are returned scope voltages, not pristine ADC codes.
No host smoothing, clipping, normalization, dark subtraction or fitting is applied.
The configured delay is not proof of actuator settling; no PD is recorded during
that wait, so transients in that interval are unobserved.

Each EOM run stores a small number of coherent files:

| File | Contents |
| --- | --- |
| `recipe.json` | Resolved configuration and notes |
| `run.json` | Execution, independent cleanup/evaluation/report statuses, source hashes and provenance |
| `acquisition.json` | Effective hardware/scope settings, timing origin, units and format description |
| `events.jsonl` | One JSON object per command, settling event, PD buffer reference, PAX record, completion/error |
| `pd_raw.npy` | One append-only stream of consecutive NumPy `.npy` arrays; each array is signed float64 input volts |
| `quality.json` | Offline-reproducible structural checks and scientific `UNKNOWN`/failure verdict |
| `console.log`, `report.md` | Retained log and minimal textual report; no analysis plots |

A `pd_capture` event has `point`, `capture`, `byte_offset`, `byte_end`, `sample_count`,
`sampling_time_s`, units, and host request/receive times. Arrays are not duplicated
in CSV or JSON. Read a specific retained buffer with:

```python
import numpy as np
with open("pd_raw.npy", "rb") as stream:
    stream.seek(event["byte_offset"])
    voltage_v = np.load(stream, allow_pickle=False)
# Relative time within this buffer: np.arange(len(voltage_v)) * event["sampling_time_s"]
# This is not an exact host-clock time axis; acquisition start/trigger latency is unknown.
```

Point indices are zero-based. Command events carry repeat, direction, step,
`eom_commands_v` in EOM1/EOM2 identity order and `rp_commands_v` in OUT1/OUT2 order.
Register readback is separate from intended commands and is not measured voltage.
Each `pax_sample` stores its point/sample index, `raw_record` from the daemon and
`interface_reading` from the existing controller. The latter's `s1/s2/s3` are
**angle-derived unit polarization direction**, not raw absolute Stokes or S/S0.
Power `ptotal`, DoP, ellipse angles (radians), timestamp, revisions and available
ADC telemetry are retained. Any daemon-provided S0/Stokes fields remain in the raw
record; absent S0 is not manufactured. Power/device timestamp units remain those
of the SDK/daemon; no conversion or time-clock equivalence is assumed. Nonfinite
JSON telemetry uses explicit tags such as `{"nonfinite": "nan"}`; nonfinite PD
values remain in the binary arrays and are flagged structurally.

## Exact timing scheme

`run.json` uses UTC ISO timestamps for execution. Hardware `acquisition.json`
records a near-contemporaneous UTC/`time.monotonic()` origin pair; event
`elapsed_s`, `requested_s`, `received_s` are host monotonic seconds relative to
that origin. Request/receive times bracket API calls, **not physical integrations**.
PD sample spacing comes from the scope; exact trigger/start offset relative to
host time is unavailable. A new clock domain starts for every buffer. PAX's
original `timestamp` stays in `raw_record`/`interface_reading` with its independent
native clock. PD windows precede PAX request windows; they are associated by point,
not sample-synchronized. Integration may straddle a state transition despite an
advancing PAX timestamp. Determining that requires later timing characterization.
Mock origin is explicitly synthetic (2000-01-01 UTC, deterministic elapsed clock);
execution wall-clock timestamps/provenance are still real.

## Quality, failure handling and provenance

`COMPLETED` execution does not imply scientific PASS. Checks cover recipe command
order/mapping/repeats, acknowledgement, point completion/sequence, PD setup/counts/
raw boundaries/finiteness, PAX coverage/advancing native timestamps from the stored
baseline, host timing order, and execution/cleanup errors. All structural checks
can pass while overall quality remains `UNKNOWN`: no scientific thresholds are
established. Any structural failure yields `FAIL`. Generic aggregation remains
`FAIL > WARN > UNKNOWN > PASS`, with empty evidence `UNKNOWN`.

The existing PAX `read_polarization` path is reused, rather than its
`read_fresh_polarization` validity gate: every returned record is retained without
DoP/power/ADC acceptance filtering. Stale timestamps are visible as quality failure,
not hidden by waiting for acceptable data. Legacy timeout retries still apply.
Conversion failures retain the last available raw daemon record as error evidence.
No synthetic substitute is inserted on hardware failure. PD empty/incomplete
buffers are stored if returned, then stop acquisition; a capture/init failure
stops the run and marks quality FAIL. PAX/command failures also stop acquisition.

Events and completed binary buffers flush incrementally. Interrupted/failed runs
attempt scope restoration, RP zeroing and PAX cleanup, preserving all artifacts.
Both controller cleanups are attempted even if one fails. A returned, side-effect
free session constructor is required for cleanup after failed connect. Process
kill/power loss cannot guarantee cleanup or complete buffers; partial/unreferenced
trailing bytes and malformed events are retained and flagged offline. `RUNNING`
left on disk must not be treated as completion. Inspect outputs physically after
cleanup failure. No raw experiment data is committed: default experiment output,
package caches and `local-config*.json` are ignored; custom roots need their own
ignore policy.

Offline evaluation reads saved recipe/measurements/execution evidence, hashes raw
artifacts and evaluator sources, and replaces only derived `quality.json`/`report.md`.
The `run.json` evidence hash explicitly covers stable execution/cleanup fields,
not evaluator/report bookkeeping, so unchanged runs re-evaluate deterministically.
Raw artifacts and execution provenance are not rewritten. Preserve older derived
outputs separately if comparing changed evaluator versions.

Real runs automatically fingerprint both suites, the selected daemon config and
adjacent daemon implementation, plus an RP config file if `rp_config` names a file;
record NumPy/Pyrpl/YAQC/YAQD/VISA versions, Git commit and whole-repository dirty
state. Driver reuse imports no locking calibration/gain/model values. Fingerprints
identify contents but do not archive them: preserve dirty patches/source snapshots
for exact replay. If Pyrpl resolves a named profile externally, its resolved source
file is not automatically located or archived; hardware configuration/profile
archiving is deferred technical debt. Daemon log location is recorded; its existing
log is not copied into the run.

## Scope and remaining work

`polarization_locking` remains independent and unchanged. The thin `BenchSession`
reuses `RPController.connect`, `set_output_voltage`, `photodiode_monitor` scope
setup and `disconnect`, plus `PAXController.connect`, `read_polarization` and
`disconnect`. Full traces require direct access to its configured Pyrpl scope,
because the public PD callback returns summaries; that coupling is documented
technical debt. Local PAX connect retains existing behavior that stops lingering
PAX daemons and may start the configured daemon. RP connect retains its output-route
initialization and zeroing. Use exclusive bench ownership; physical EOM drive limits,
PD wiring/gain/range, wavelength/serial, scope settings/timeout, startup/cleanup safe
states, actual delivered voltages and PAX timing must be verified at the bench.

Scientific sequence remains reference-path characterization → independent EOM
characterization → combined-component comparison → analyzer-state design → empirical
instrument matrix calibration → withheld-state validation → high-speed reconstruction.
The original two EOM experiments remain acquisition-only; the separate PAX-only
Vπ/hysteresis routine adds the explicit rough fixed-axis model and direction
comparison described above. It does not establish calibrated terminal-voltage
retardance, an independently verified physical eigenaxis, or intrinsic hysteresis.
State optimization, Stokes reconstruction and calibration inversion remain pending.
Basler/OAM workflows remain out of scope.


## Single-EOM PD/monitor qualification

Select **`eom_pd_qualification`** in the same numbered menu. It uses the existing
run/edit/save/load/defaults commands and persistent local settings. No PAX or
separate RAM measurement is required. Keep one selected EOM in the optical path,
input and effective analyzer both at 45° to its eigenaxes, followed by PBS/PD.
Select OUT1 or OUT2 explicitly; assign PD and amplifier monitor to distinct
IN1/IN2 inputs. Record the device/serial, amplifier, wavelength and TEC conditions.

Start with the deliberately **unconfirmed** example:

```bash
PYTHONPATH=python python -m high_speed_polarimeter --dry-run --profile python/high_speed_polarimeter/profiles/pd-qualification-example.json
PYTHONPATH=python python -m high_speed_polarimeter --mock-run --profile python/high_speed_polarimeter/profiles/pd-qualification-example.json --results-directory /tmp/eom-pd-mock
PYTHONPATH=python python -m high_speed_polarimeter --reevaluate RUN_DIRECTORY
```

Before `--run` or menu Run, use a local recipe to confirm the optics, output map,
physical ADC jumpers (±1 or ±20 V), RP command bounds, preamp-output/HV-amplifier-input
limits, and electrode limits. Record signed gains, offsets and conservative margins
for **both** amplifier-input and complete electrode chains, including the zero
startup/cleanup condition. Waveforms also require confirmed frequency bounds.
Example amplitudes are software templates, **not safe limits for a repaired device**.
No nominal New Focus rating is used as a safety envelope.

The existing RP controller loads a Pyrpl profile before initializing ASGs. Hardware
runs therefore require `startup_zero_profile_confirmed` (independently inspect that
profile), independent electrical protection, and no competing RP clients. Software
checks occur after configuration/capture, not as a real-time HV interlock. They
cannot establish insulation safety. Cleanup disables both ASG routes and sets DC
zero, records failures separately, and does not claim a measured zero electrode
voltage. Independent protection remains necessary during startup, network loss,
waveform changes and cleanup.

* `mode=voltage_transfer`, `strategy=settled`: configure `start_command_v`,
  `stop_command_v`, `points`, `repeats`, `captures_per_point`, `ramp_s`, `settle_s`.
  Forward/reverse sequences include the turning point twice; DC ramps preserve the
  return path without inserting zero between points. Full buffers are averaged
  explicitly, then repeated buffers at each command receive equal weight.
* `strategy=triangle`: configure offset, amplitude, frequency and `triangle_cycles`
  (minimum complete cycles captured per repeat). The generator continues during
  settling/network gaps, so this is not an exact burst count. All identified complete
  cycles inside each buffer are analyzed independently; partial cycles remain raw.
  Noisy/flat monitors fail association. Explicit voltage-bin means retain counts;
  no averaging across cycles or ideal-waveform voltage substitution occurs.
* `mode=frequency_response`: configure safe `bias_v`, `sine_amplitude_v`, a preferably
  logarithmic `frequencies_hz`, `reference_frequency_hz`, repetitions and settling.
  Record `operating_slope_confirmed` and `slope_validation_run` only with independent
  transfer-curve evidence. Otherwise the report labels the operating slope unverified.

For electrode results, separately confirm the monitor calibration:
`V_electrode = (V_monitor_ADC - monitor_zero_v) * monitor_scale_v_per_v * monitor_polarity`.
Record its relative standard gain uncertainty and allowed `monitor_discrepancy_v`.
RP/ASG settings are not voltage measurements. An absent/unconfirmed monitor permits
settled command-domain observations, but no electrode Vπ; triangles need measured
monitor direction. Clipping, invalid capture metadata or calibrated-monitor
overrange/discrepancy abort acquisition after retaining returned raw evidence.
`monitor_min_v`/`monitor_max_v` separately bound every measured monitor ADC sample
even when electrode calibration is unconfirmed. These are nominal ADC volts, not
electrode limits; choose them inside the physical ADC range. Existing recipes use
a conservative ±1 V window until explicitly changed.

`dual_raw.npy` contains appended signed-int16 paired arrays (PD, monitor); event byte
ranges/hashes identify them. Do not treat this appended stream as a single `np.load`
array. `qualification.analyze.captures()` iterates it. Effective settings, host
request/receive bounds, pointers, command settings and calibration are retained in
the native run artifacts. The adapter was checked against local Pyrpl `Scope`:
one `single()` freezes both channels on the same ADC clock, followed by sequential
network reads of signed codes, with the same trigger-pointer rotation as the public
trace. Unsupported APIs, averaging, rolling capture, changed settings or mismatch
with the public trace fail explicitly. Automatic power-of-two decimation must fit
all requested cycles/resolution into one 16384-sample buffer; unsupported recipes
fail before connecting. Host bounds are not sample timestamps. Captures remain
separate; physical analog-channel delay and clock accuracy require bench checks.

Reports separate **measured monitor ADC volts**, RP-command volts and calibrated
monitor-derived electrode volts. An unconfirmed electrode conversion does not hide
the measured monitor transfer or produce an electrode Vπ. ADC volts use the
declared input full scales; an independent ADC calibration is not established.

Settled reports show every buffer's PD mean and raw min/max, plus paired raw
examples selected deterministically: the unflagged buffer nearest the median PD
SD and the largest diagnostic score (earliest ties). PD/monitor panels have common
scales across examples and retain all samples. Zero-based capture IDs, branch,
command, monitor mean/SD, PD range/SD and extended-collapse durations are printed.
Screening covers unusually large within-buffer SD, same-command peer mismatch
and sustained below-peer PD signal, including slow collapses missed by a neighbor-
step detector. Explicit thresholds, peer IDs and buffer-relative sample ranges
are retained in `analysis.json`. These are diagnostic rules, not fault probabilities;
they cannot distinguish optical interruption from detector/electrical or acquisition
causes. Settled rules are not applied to intentional triangle/sine modulation.
Missing buffers/gaps remain unobserved; no filtering or sample exclusions occur.

Partial identifiable fringes remain **model extrapolations**; ambiguous fits stay
unavailable. A full period is unnecessary when adjacent interior peak/valley
observations resolve a half-wave. Reversal excursion must exceed max(20% observed
PD span, 4 residual RMS); opposite extrema must differ by at least max(75% span,
8 RMS). Reported sampled spacings differ from fitted π/|k|; neighbor brackets are
sampling neighborhoods, not confidence bounds. Bounded fitting cannot exclude
unobserved high-order aliases. Conditional fit SE, branch/cycle spread and voltage-
scale uncertainty remain separate; correlated errors/model bias are outside the
simple covariance. PD-voltage visibility remains a diagnostic proxy in analysis,
not dark-corrected optical visibility.

All same-command forward/reverse and same-direction cycle comparisons retain
anomalous captures and measured monitor mismatches. Median and maximum absolute
differences summarize **all** pairs. Transient-associated differences are labeled;
they cannot establish intrinsic hysteresis. No anomaly-excluded replacement is
computed. The six-question qualification summary separates software completion
from physical evidence: Vπ, retardance linearity, repeatability, frequency response,
drift/hysteresis and intact-EOM comparison. Voltage transfer does not measure
frequency response. No automatic reference-run search or comparison is performed;
a compatible intact-EOM reference must be explicitly supplied for a future review.

Frequency analysis uses coherent DC/trend/1f/2f least squares (2f only when resolved),
peak amplitudes, fundamental-RMS/residual-RMS SNR, and PD/monitor complex ratios.
Normalization divides by the explicit same-cycle reference response; failed reference
captures are not silently dropped. Phase is reference-normalized principal phase,
not an unwrapped time delay. Command-to-monitor **amplitude** is recorded; its
absolute phase is unavailable without a third acquired ASG reference. PD/monitor
phase is available from simultaneous channels. Detector bandwidth, analyzer slope,
monitor dynamics and amplifier behavior remain in the system response; peaks/dips
are not automatically crystal resonances or intrinsic crystal bandwidth.

`analysis.json`, `quality.json`, Markdown and PDF are reproducible offline; source
buffers/config/events are unchanged. Mock reports always state **not hardware
verified**. Before first bench use independently verify startup/cleanup, output and
ADC mapping/ranges, monitor scale/polarity/offset, simultaneous injected-channel
phase/amplitude, actual timing, protection and safe low-amplitude envelopes. Then
validate settling, reversible fringes, operating slope and frequency coverage on
the actual repaired device. Software tests do not complete these bench checks.
