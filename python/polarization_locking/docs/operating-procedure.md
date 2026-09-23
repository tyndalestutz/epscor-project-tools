# Standard operating procedure

Use this for menu/catalog measurements. Start from the [suite README](../README.md)
and choose a pipeline in the [test guide](test-guide.md). [Raw calibration](raw-calibration.md)
is a specialist exception with a different command and artifact schema.

## 1. Define the measurement

Record the question and selected test before changing the bench. PD visibility,
PAX polarization response, electrical calibration, path balance, and lock stability
measure different quantities. Open test details without instrument access:

```bash
python python/polarization_locking/lock.py --show pd-visibility
```

Use test names in shared instructions; menu numbers are navigation conveniences.
The detail screen lists purpose, required setup, relevant settings, and run options.

## 2. Record the physical setup

Load a recipe or example. Edit these before running:

| Record | Where |
| --- | --- |
| Operator, purpose, setup identifier/sketch and reference run | `bench_notes` |
| Actual PAX plane/port, wavelength, optical paths and analyzer orientation | `bench_pax_location`, PAX settings, `bench_notes` |
| OUT1/OUT2 cabling, actuator mapping, driver channel/gain and calibration evidence | `bench_voltage_chain`, axis gain/Vλ fields, `bench_notes` |
| PD plane, RP input, detector gain/range, coupling, attenuation and scope termination | `pd_input`, relevant PD settings, `bench_notes` |
| External drive source, frequency, range, other connected active outputs | visibility frequency and `bench_notes` |
| Signed blocked-light voltage and measurement method | `visibility_dark_voltage_v`, `bench_notes` |

`bench_notes` also appears on the visibility detail screen; some other settings
only appear under **all**. Default notes do not establish the current setup.
The last recorded PAX location was C; D-port/final-output tests require actual
relocation and updated notes.

RP commands are bounded to 0–1 V. `phi*_v_lambda` is **terminal** voltage for 2π,
twice Vπ; actuator gain is terminal volts per RP command volt. Both arms now use
matching piezos and the same configured calibration: **Vλ = 12.2 V, Vπ = 6.1 V,
and 16.875 actuator V per RP command V**. These are phi1's existing calibration
candidates applied to both axes, not a new measurement. The former phi2 defaults
of 30 V and 150 V/V describe the previous setup. Saved full recipes retain their
explicit values; update both axis fields before reusing an old recipe on this
setup. A driver model name is not a gain measurement. Use a bounded explicit
`sweep` when the conversion is unknown.

## 3. Check instrument ownership and output behavior

| Mode | Connection and cleanup |
| --- | --- |
| Ordinary menu test, including `live` | Full RP and PAX connection; initializes RP outputs to zero, runs the routine, attempts zero on exit |
| `pax-live` | PAX only, initialized/read/closed by one worker; RP untouched and unmeasured; Stop/close/Ctrl+C offers Save or Discard after cleanup |
| `pd-visibility`, passive PD/both | Scope transport only; requires already loaded Pyrpl FPGA; restores scope registers; measures signed dark baseline unless provided; both also connects PAX |
| `pd-visibility`, passive PAX | PAX connection only; records total optical power; never connects RP or changes actuator outputs |
| `pd-visibility`, active (any detector choice) | Normal RP connection; selected phase actuator/waveform, other output zero; PAX connects when selected; both outputs returned to zero on exit |

Do not run competing control sessions or concurrent RP scope acquisitions.
For passive work, establish which outputs are connected and already active:
unchanged outputs are not necessarily off. The September 21
[audit](../../../experiments/polarization_locking/2026-09-21/215158_pd-visibility_pd-visibility/audit.md)
found an existing OUT2 drive; that is historical evidence, not a live status check.

On local Linux connections, the PAX adapter stops existing PAX daemon processes
before connecting, because only one can own the device. This includes lingering
YAQD and project daemons: it sends SIGTERM, waits up to two seconds, then uses
SIGKILL if needed. Another PAX session will lose its daemon. It then starts the
project daemon on connection refusal when autostart is enabled. Disconnect stops
the daemon started by this session. Remote PAX connections do not kill local
processes. Confirm the serial in [pax1000.toml](../hardware/pax1000.toml).

## 4. Save, validate, acquire

1. Use a descriptive `label`, such as `in2-both-paths-dc-gain10`.
2. Save edited settings with **s**. Share reviewed recipes, not private credentials.
3. Validate with `python python/polarization_locking/lock.py --profile path/to/recipe.json --dry-run`.
4. Select **r**, or use `--profile path/to/recipe.json --run TEST`. This explicitly
   starts hardware acquisition. Record the printed run folder.
5. Follow manual beam-block prompts and verify each condition physically.

Duration on path comparisons means **per condition**. Other timing can include
calibration, settling, and a final complete scope capture. Ctrl+C stops a run;
`pax-live` then offers Save/Discard; until a choice, its flushed CSV stays recoverable.
**b** at a setup prompt aborts it. Keep the folder after either. Cleanup failure
closes the menu; verify instrument state before another run.

## 5. Review before interpreting

Read `run.json`: `status`, `error`/`cleanup_error`, `report_status`, and
`report_error`. `completed` means the routine returned, not that every physical
inference is valid. `interrupted`/`failed` may retain useful partial data;
`cleanup_failed` means hardware cleanup was not confirmed. Report failure does
not erase raw data.

Inspect the PDF and raw measurements for repeatability, drift, saturation,
and coverage. Visibility requires checking `visibility.json` and per-capture
`status`: null means invalid/unresolved. PD mode needs a measured/provided signed
dark baseline; PAX contrast uses instrument-reported watts. Review actual PAX
cadence and fringe coverage. Vpp alone is not contrast. For locking, review residual error,
outputs, saturation, and DOP. Existing PI loops log DOP without continuously
gating feedback.

Put interpretation and limitations in `notes.md` or a separate analysis folder.
Keep raw data and original recipes unchanged. Record new analysis parameters
and code version. A plausible plot does not validate placement, electrical gain,
or the phase model.

## 6. Repeat and hand over

Load the run's `recipe.json`, verify wiring/environment, and create a new labelled
run. Compare source hash, Git revision/dirty state, dependency versions, physical
notes, and scatter. `target_mode=current` captures a new target each time; use
`explicit` and the recorded effective `target_u`/`target_v` to repeat a fixed target.

Hand over the test name, recipe, setup sketch/notes, code/environment revision,
one reference run with validity notes, and a question-specific acceptance
criterion. Retain failed runs as diagnostic evidence, not reference measurements.
