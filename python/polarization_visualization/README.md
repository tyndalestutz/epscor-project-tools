# Polarization visualization

Reusable Basler acquisition and analyzer-diagnostic code. Camera support is usable independently by other repository systems; analyzer outputs remain locked until their electrical transfer chains are measured.

## Camera guarantees

- Selects the camera by serial number (`24934175` by default), never by enumeration order.
- Disables auto exposure and auto gain every time it opens.
- Applies and records the actual pixel format, exposure, gain, and ROI.
- Restores the full sensor when no ROI is requested, even if another application previously left a smaller ROI on the camera.
- Aligns ROI dimensions and offsets to the camera's allowed increments.
- Copies each pylon grab buffer before releasing it.
- Retries open failures and reconnects after transient grab failures using bounded retry counts.
- Makes `open()` and `close()` idempotent and closes on normal exit, exceptions, and Ctrl-C when used as a context manager.
- Saves lossless `.npy` arrays with adjacent versioned JSON metadata and intensity/saturation statistics. CLI averaging also preserves every component frame under `raw_frames/`.
- Imports `pypylon` and OpenCV lazily, so analysis code can import saved-frame types without camera libraries installed.

The current conservative default is `Mono12`, 100 microseconds, gain 0. The first camera check found a maximum of 3443/4095 with zero saturated pixels at that setting; recheck exposure whenever the optical state changes.

## Command line

Activate the environment containing `pypylon`, then run from the repository root:

```bash
conda activate jl-env
export PYTHONPATH=python

python -m polarization_visualization.camera_cli list
python -m polarization_visualization.camera_cli info
python -m polarization_visualization.camera_cli capture --count 3 --average 4
python -m polarization_visualization.camera_cli live
```

Useful ROI example:

```bash
python -m polarization_visualization.camera_cli capture \
  --width 960 --height 600 --offset-x 480 --offset-y 300 \
  --exposure-us 100 --gain 0 --count 5
```

`live` is for alignment only and contrast-stretches each displayed frame. It does not alter or save the raw pixels. Press Q or Escape to close it. OpenCV is required only for this command.

## Python integration

Other systems should depend on the public package API rather than accessing pylon directly:

```python
from polarization_visualization import BaslerCamera, CameraConfig, save_record

config = CameraConfig(
    serial_number="24934175",
    exposure_us=100.0,
    gain=0.0,
    pixel_format="Mono12",
    width=960,
    height=600,
    offset_x=480,
    offset_y=300,
)

with BaslerCamera(config) as camera:
    frame = camera.acquire()             # one raw frame
    average = camera.acquire_average(4)  # float64 mean of four raw frames
    average, sources = camera.acquire_average_with_sources(4)  # retain components
    print(camera.device_info)
    print(camera.actual_settings)

save_record(frame, "experiments/polarization_visualization/example/frame_0000")
```

For a sequence without averaging, use `camera.acquire_many(count)`. Catch `BaslerError` for a common application-level failure boundary, or the narrower `CameraNotFoundError`, `CameraConfigurationError`, and `CameraAcquisitionError` classes when recovery differs.

Only one process can own this USB camera. Close pylon Viewer before opening it from Python. Direct USB enumeration may also be unavailable inside a restricted sandbox even when the host can see the camera.

## Known Red Pitaya stack and analyzer lock

- Existing interface: PyRPL, host `192.168.1.98`, config `scope_config`.
- Existing routing: ASG0 -> OUT1 and ASG1 -> OUT2. In the present analyzer wiring, OUT1 reaches the EOM through the x8.89 preamp and x15 MDT690; OUT2 has no preamp.
- Gains in `polarization_locking/config.py` describe older `phi1`/`phi2` paths and are not reused for the new EOM/LCVR chain.
- Intended analyzer routing: OUT1 -> EOM chain and OUT2 -> LCVR driver; precise transfer functions remain unverified.

Before enabling analyzer control, verify OUT1-to-EOM voltage scaling, determine whether OUT2 controls the LCVR cell or a driver input, measure the cell's zero-mean waveform/frequency/Vpp, and enter conservative limits in `AnalyzerConfig`. The generation-interferometer piezo remains outside this package and fixed during analyzer tests.

## Conservative analyzer systems test

`systems_test.py` integrates camera frames with the Red Pitaya IN2 photodiode. The default configuration stays locked. OUT2 is presently connected to a Thorlabs MDT690 set for up to x15 gain. Thorlabs specifies this controller for a 0–10 V external input and 0–150 V output; it is not a supported bipolar LCVR driver. Therefore all nonzero LCVR commands are now locked on this hardware path. There is no x8.89 preamp on OUT2.

Meadowlark specifies a symmetric, zero-DC, 2 kHz square wave for standard nematic LCVRs. Voltage-retardance curves are conventionally given in RMS volts; a symmetric 5 Vpp square wave is +/-2.5 V and 2.5 Vrms. Use a proper bipolar LC controller/amplifier, then measure Vpp and DC mean across the connected cell before setting both `lcvr_bipolar_output_verified=True` and `lcvr_terminal_gain_verified=True` in a dedicated site configuration.

```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=python python -m polarization_visualization.systems_test preflight
QT_QPA_PLATFORM=offscreen PYTHONPATH=python python -m polarization_visualization.systems_test baseline
QT_QPA_PLATFORM=offscreen PYTHONPATH=python python -m polarization_visualization.systems_test run --axis eom
QT_QPA_PLATFORM=offscreen PYTHONPATH=python python -m polarization_visualization.systems_test run --axis lcvr
PYTHONPATH=python python -m polarization_visualization.analyze_systems_test RUN_DIRECTORY
```

Every run begins from a safe state, saves raw camera components and averaged frames, and routes both outputs off on normal completion, exceptions, or Ctrl-C. The LCVR waveform is configured while OUT2 is disconnected, then routed only after it is a bounded zero-mean 2 kHz square wave.
