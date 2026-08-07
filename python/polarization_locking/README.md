# Polarization locking framework

This folder is being structured as a small experimental framework for combining:

- Red Pitaya control via Pyrpl
- PAX1000 polarization reads
- real-time visualization
- a model-based rough alignment from PAX polarization readings to EOM/PZT DC voltages

## Proposed structure

- `config.py`: central configuration and tuned defaults
- `rp_interface.py`: Red Pitaya connection and PID helpers
- `pax_interface.py`: PAX1000 connection and reading helpers
- `control.py`: PAX-to-Stokes, Poincare-angle, phase, and voltage mapping
- `lock_app.py`: main loop / CLI entry point

## Plan

1. Keep the connection and device logic in separate modules so each piece can be tested independently.
2. Implement a clean polarization-state object that can represent current and target coordinates.
3. Use the hybrid-MZ model for a rough move, then add PID fine stabilization after that move is verified.
4. Add visualization and logging around the lock loop without coupling it tightly to control logic.
5. Replace the placeholder mapping with a calibrated model once the actuator response is measured.

## Rough-alignment convention

The PAX reports ellipse angles `(theta, eta)`, not the hybrid-MZ sphere angles.
The controller first forms the PAX Stokes vector:

`S = (cos(2eta) cos(2theta), cos(2eta) sin(2theta), sin(2eta))`.

It then uses the S1-polar sphere convention:

`u = atan2(S3, S2)` and `v = acos(S1)`.

For the present equal-amplitude, zero-static-offset hybrid-MZ model, the
canonical actuator phases are `phi1 = u - pi` and `phi2 = v`.  The actuator
voltage increments are therefore:

`dV_phi1_actuator = phi1_v_lambda * du / (2pi)`

`dV_phi2_actuator = phi2_v_lambda * dv / (2pi)`.

Those are not necessarily the Red Pitaya commands.  With electrical gain
`G_phi1` or `G_phi2` in actuator-volts per Red-Pitaya-volt, the commands are:

`dV_phi1_RP = dV_phi1_actuator / G_phi1`

`dV_phi2_RP = dV_phi2_actuator / G_phi2`.

The RP interface enforces the installed 0–1 V output range.  Rough alignment
also refuses to run until both gain chains are configured and the physical
phi1/phi2-to-output mapping is explicitly confirmed.  A negative Vlambda or
electrical gain represents the observed polarity.  The phi1 azimuthal
correction is suppressed at the S1 poles because azimuth there is undefined.

## Hardware test order

1. Use `live` to verify PAX stability and DOP at the intended operating point.
2. Run separate output sweeps to confirm the physical output routing, polarity,
   and actuator-volts-per-RP-volt gains.
3. Confirm that a phi1 sweep primarily changes `u` and a phi2 sweep primarily
   changes `v`; use those data to validate the signed Vlambda values.
4. Use `rough` for one measure → command → settle → verify move. It is not a
   repeated feedback loop.
5. Add PID only after rough moves work over the required target region.

## Original framework checks

The first version should prove three things:

- the PAX can be read
- the Red Pitaya can be connected through Pyrpl
- a basic loop can convert a polarization error into an actuator command without crashing
