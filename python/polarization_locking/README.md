# Polarization rough-alignment test framework

This package tests the open-loop voltage model and a conservative, logged PID
experiment before any production stabilization is introduced. Its control path is:

`PAX theta/eta -> Stokes -> hybrid-MZ u/v -> phase increment -> RP command`

## Coordinate convention

The PAX reports polarization-ellipse angles `(theta, eta)`. They are converted
to normalized Stokes coordinates:

`S = (cos(2eta) cos(2theta), cos(2eta) sin(2theta), sin(2eta))`.

The hybrid-MZ coordinates use S1 as the polar axis:

`u = atan2(S3, S2)`

`v = acos(S1)`.

For the current ideal hybrid-MZ model, `u = phi1 + pi` and `v = phi2`; hence
the controller uses `d_phi1 = wrap(u_target - u_measured)` and
`d_phi2 = v_target - v_measured`.

## Voltage chain

`phi1_v_lambda = 12.2 V` (current candidate) and `phi2_v_lambda = 30 V` are actuator-side voltages
for a 2pi phase shift. The RP command increments are:

`dV_RP1 = 12.2 * d_phi1 / (2pi * 16.875)`

`dV_RP2 = 30 * d_phi2 / (2pi * 150)`.

The configured chains are OUT1 -> phi1 with gain `16.875` actuator V/RP command
V, and OUT2 -> phi2 with gain `150`. RP commands are constrained to 0–1 V.

## Scripts

[Offline bench diagnostics](analysis/README.md) contains the first-NPBS and
phi1-step analysis scripts invoked by the lock CLI.
[Jones models and independent measurement acquisition](../field_propogation/README.md) live in
`../field_propogation/`; old fitted-data workflows are archived in its
`archive/effective_fits/` folder. Start new physical characterization with its
[measurement protocol](../field_propogation/docs/measurement_acquisition.md).

- `lock.py`: interactive one-shot rough move and live PAX monitor. Targets are
  entered directly as `u v`; it includes a bounded PID test mode. Every command
  that records data creates a dated experiment folder containing `data.csv` and,
  when requested with the final `pdf` token, `report.pdf`. Use `live` for
  console-only monitoring or `live <label>` to log raw PAX data and converted
  `u,v` into its own folder.
- `calibration.py`: reusable one-axis sweep collector that writes raw PAX,
  Stokes, DOP, and converted `u,v` data to CSV. It also supports cross-sweeps
  and forward/reverse sweeps that hold one phase at a fixed bias.
- `control.py`: coordinate conversion and voltage-chain math only.
- `pax_interface.py` and `rp_interface.py`: instrument wrappers with DOP and
  0–1 V safety checks.
- `test_control.py`: offline coordinate and voltage-chain tests.
- `plot_pid_tests.py`: creates a multi-page PDF report from PID-test CSVs,
  including target tracking, errors, actuator commands, PI state, DOP, and
  phase-period recenter events.

## Hardware test sequence

1. Run `live` and confirm high, stable DOP at the operating point.
2. Run `sweep phi1 phi1.csv`. A 0.01 V RP command step predicts roughly
   0.0964 rad of azimuthal (`u`) motion.
3. Run `sweep phi2 phi2.csv`. A 0.01 V RP command step predicts roughly
   0.3142 rad of polar (`v`) motion while staying on one canonical branch.
4. Run `cross-sweep phi1 phi1-cross pdf` and `cross-sweep phi2 phi2-cross pdf`.
   Each scan covers one V_lambda of the selected phase axis: 0–0.7230 V RP
   command for phi1 and 0–0.2000 V for phi2. For each full fine sweep, the
   other axis steps through eleven equally spaced biases spanning its own full
   V_lambda (ten intervals, including zero and one V_lambda). Append `pdf` to
   any sweep, diagnostic, or PID test command to automatically create a report
   beside its CSV in the same dated run folder.
5. Inspect the CSVs for the dominant predicted axis and cross-coupling. Confirm
   polarity and effective Vlambda before attempting a target move.
6. Run `bidirectional-sweep phi1 phi1-hysteresis.csv` and
   `bidirectional-sweep phi2 phi2-hysteresis.csv`. They cover one V_lambda in
   both directions with a 0.5 s dwell, holding phi2=0.1 V for phi1 and
   phi1=0.2 V for phi2. The CSV `direction` column identifies the branch.
7. Run `diagnostic-suite static-diagnostic.csv` to record 60-second PAX holds
   at baseline and at 0, 1/4, 1/2, 3/4, and 1 V_lambda for each phase. It
   records the commanded RP outputs alongside every PAX reading, so stable
   state-dependent DOP can be separated from motion artifacts. The default
   suite takes about 11.5 minutes including 3-second settling at each state.
8. Run `intensity-diagnostic final-port-amplitude pdf` with the final-output
   photodiode connected to Red Pitaya `in1`. It independently sweeps phi1 with
   phi2=0 and phi2 with phi1=0, logging PD mean/noise and PAX Stokes/DOP at
   every point. The PD arm's OD 2.0 filter is recorded and corrected as a
   100x pre-filter-equivalent PD signal; PAX `ptotal` is logged separately.
   Its report directly plots final-port amplitude and DOP correlation,
   providing a check of the equal-amplitude assumption behind the ideal
   hybrid-MZ model.
9. Set a target with `set <u> <v>` (or create one with `capture`), then issue
   `rough`. It performs one measure -> move -> settle -> verify operation.
10. For a bounded feedback experiment, set a target and run
   `pid-test 120 pid-test.csv`. It seeds both outputs at mid-range, performs
   one 70%-scaled rough correction, then applies conservative incremental PI
   corrections for 120 seconds. The CSV includes every error, integral,
   correction, output, saturation flag, and raw PAX reading for gain tuning.
   If an actuator is rail-limited with a substantial remaining error, the test
   can recenter it by one V_lambda (recorded as `pid-recenter`) to regain
   headroom without changing the ideal phase state.
   With the current raw-DOP issue, use `capture-unchecked` to capture the
   present angular target for this diagnostic experiment; ordinary `capture`
   and `rough` still retain their DOP safety gate.
11. Use `pid-live 600 pid-live.csv` for the same PID test with the PyVista
    Poincare sphere. The visualizer receives the PID loop's PAX readings—it
    never opens a second PAX client. Cyan marks the target, red marks the live
    state and trace; move the `u`/`v` sliders or use J/L and I/K while locking
    to change the target, R to clear the trace, and Q to stop.
    The live renderer is decoupled from control sampling so rendering cannot
    throttle the feedback loop. The PAX daemon configuration uses an 80 Hz
    waveplate velocity; restart the daemon after changing `pax1000.toml`.

Avoid using a target at an S1 pole during the first test: phi1 azimuth is
unobservable there, so a PAX reading alone cannot identify the absolute phi1
phase required to leave that pole.
