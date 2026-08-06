# Polarization locking framework

This folder is being structured as a small experimental framework for combining:

- Red Pitaya control via Pyrpl
- PAX1000 polarization reads
- real-time visualization
- a simple lock loop driven by an error term between current and target polarization coordinates

## Proposed structure

- `config.py`: central configuration and tuned defaults
- `rp_interface.py`: Red Pitaya connection and PID helpers
- `pax_interface.py`: PAX1000 connection and reading helpers
- `control.py`: polarization coordinate math and lock error mapping
- `lock_app.py`: main loop / CLI entry point

## Plan

1. Keep the connection and device logic in separate modules so each piece can be tested independently.
2. Implement a clean polarization-state object that can represent current and target coordinates.
3. Start with a simple proportional error mapping from polarization coordinate error to actuator voltages.
4. Add visualization and logging around the lock loop without coupling it tightly to control logic.
5. Replace the placeholder mapping with a calibrated model once the actuator response is measured.

## Immediate next step

The first version should prove three things:

- the PAX can be read
- the Red Pitaya can be connected through Pyrpl
- a basic loop can convert a polarization error into an actuator command without crashing
