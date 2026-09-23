"""Repeated electrical phase-actuator sweeps with paired RP/PAX snapshots."""
import csv
from datetime import datetime, timezone
import math
from pathlib import Path
import time


FIELDS = (
    "elapsed_s", "utc", "out1_command_estimated_v", "in1_reference_v", "rp_out2_v",
    "in1_started_s", "in1_finished_s", "pax_requested_s", "pax_received_s",
    "in1_std_v", "in1_min_v", "in1_max_v", "in1_sample_count",
    "scope_sampling_time_s", "scope_duration_s", "scope_decimation", "scope_fpga_average",
    "sine_frequency_hz", "sine_amplitude_v", "sine_offset_v",
    "pax_timestamp", "pax_revisions", "pax_adc_min", "pax_adc_max", "pax_rev_time",
    "pax_ptotal", "s1", "s2", "s3", "dop", "theta", "eta",
    "s1_over_s0", "s2_over_s0", "s3_over_s0",
)


def acquire_stokes_phase_sweep(rp, pax, config, output_file, duration_s):
    """Stream one row per fresh PAX record and following short IN1 capture.

    Separate host timing windows expose sequential acquisition latency. PAX's
    internal integration time/clock is not synchronized to the RP scope clock.
    The OUT1 waveform estimate is informational; IN1 is the measured reference.
    """
    frequency = config.stokes_phase_sweep_frequency_hz
    amplitude = config.stokes_phase_sweep_amplitude_v
    offset = config.stokes_phase_sweep_offset_v
    period = config.stokes_phase_sweep_sample_period_s
    print(f"Stokes phase sweep: OUT1 {frequency:g} Hz sine, {offset:g} +/- {amplitude:g} V, {duration_s:g} s.")
    print("IN1 is the measured OUT1 voltage reference. PAX and RP timing windows are logged; no phase is inferred.")
    try:
        with Path(output_file).open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            handle.flush()
            with rp.photodiode_monitor(input_channel="in1") as read_reference:
                rp.set_output_zero()
                pax.read_fresh_polarization()  # establish readiness before the drive/run clock
                rp.set_phi1_sine(offset=offset, amplitude=amplitude, frequency_hz=frequency)
                # Retain actual ASG settings after hardware quantization.
                frequency, amplitude, offset = map(float, (rp.asg1.frequency, rp.asg1.amplitude, rp.asg1.offset))
                scope = rp.p.rp.scope
                started = next_sample = time.monotonic()
                while time.monotonic() - started < duration_s:
                    time.sleep(max(0.0, next_sample - time.monotonic()))
                    if time.monotonic() - started >= duration_s:
                        break
                    pax_requested = time.monotonic() - started
                    reading = pax.read_fresh_polarization()
                    pax_received = time.monotonic() - started
                    in1_started = time.monotonic() - started
                    reference = read_reference()
                    in1_finished = time.monotonic() - started
                    elapsed = (in1_started + in1_finished) / 2
                    row = dict(
                        elapsed_s=elapsed, utc=datetime.now(timezone.utc).isoformat(),
                        out1_command_estimated_v=offset + amplitude * math.sin(2 * math.pi * frequency * elapsed),
                        in1_reference_v=reference.mean_voltage, rp_out2_v=0.0,
                        in1_started_s=in1_started, in1_finished_s=in1_finished,
                        pax_requested_s=pax_requested, pax_received_s=pax_received,
                        in1_std_v=reference.std_voltage, in1_min_v=reference.min_voltage,
                        in1_max_v=reference.max_voltage, in1_sample_count=reference.sample_count,
                        scope_sampling_time_s=scope.sampling_time, scope_duration_s=scope.duration,
                        scope_decimation=scope.decimation, scope_fpga_average=scope.average,
                        sine_frequency_hz=frequency, sine_amplitude_v=amplitude, sine_offset_v=offset,
                        pax_timestamp=reading.timestamp, pax_revisions=reading.revisions,
                        pax_adc_min=reading.adc_min, pax_adc_max=reading.adc_max, pax_rev_time=reading.rev_time,
                        pax_ptotal=reading.ptotal, s1=reading.s1, s2=reading.s2, s3=reading.s3,
                        dop=reading.dop, theta=reading.theta, eta=reading.eta,
                        # The suite's s1..s3 describe unit-length polarization direction.
                        # Also retain total-power-normalized Stokes for coherency analysis.
                        s1_over_s0=reading.dop * reading.s1,
                        s2_over_s0=reading.dop * reading.s2,
                        s3_over_s0=reading.dop * reading.s3,
                    )
                    writer.writerow(row)
                    handle.flush()
                    # Do not burst/catch up when instrument reads exceed the nominal cadence.
                    next_sample = max(next_sample + period, time.monotonic())
    finally:
        rp.set_output_zero()
    print(f"Stokes phase sweep saved to {output_file}")
