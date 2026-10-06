# High-speed polarimeter acquisition framework

Precision high-speed Stokes polarimetry: input polarization → EOM1 (nominal 0°)
→ EOM2 (nominal 45°) → PBS/analyzer → photodiode. The **PD is the mandatory primary
EOM response measurement**; the slower PAX is the polarization reference. Actual
axes, voltage response, reference-plane relationship and timing require bench
characterization. This package acquires data; it does not interpret the EOM yet.

## Experiments and recipes

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
Only acquisition portions of the two EOM experiments now exist. No Vπ, sinusoidal/
retardance/Mueller/Poincare fitting, axis extraction, hysteresis/drift metrics,
state optimization, Stokes reconstruction, calibration inversion, GUI, or broader
shared-infrastructure refactor was added. Basler/OAM workflows remain out of scope.
