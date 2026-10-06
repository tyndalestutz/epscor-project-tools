"""Offline behavior checks for Red Pitaya output transitions."""
from types import SimpleNamespace

import pytest

from polarization_locking.hardware.rp_interface import RPController


class _ASG:
    def __init__(self, offset):
        self.offset = offset
        self.setup_calls = []

    def setup(self, **settings):
        self.setup_calls.append(settings)
        for name, value in settings.items():
            setattr(self, name, value)


def test_ramp_configures_dc_once_and_only_writes_changed_offset(monkeypatch):
    clock = SimpleNamespace(value=0.0)

    def monotonic():
        return clock.value

    def sleep(seconds):
        clock.value += seconds

    monkeypatch.setattr("polarization_locking.hardware.rp_interface.time.monotonic", monotonic)
    monkeypatch.setattr("polarization_locking.hardware.rp_interface.time.sleep", sleep)
    config = SimpleNamespace(rp_output_min_voltage=0.0, rp_output_max_voltage=1.0)
    rp = RPController(config)
    rp.p = object()
    rp.asg1, rp.asg2 = _ASG(0.2), _ASG(0.4)
    rp._commanded_dc[:] = (0.2, 0.4)

    result = rp.ramp_output_voltage(0.7, 0.4, duration_s=1.0, updates_per_s=5.0)

    assert len(rp.asg1.setup_calls) == len(rp.asg2.setup_calls) == 1
    assert rp.asg1.offset == pytest.approx(0.7)
    assert rp.asg2.offset == pytest.approx(0.4)
    assert result["steps"] == 5
    assert result["offset_writes"] == 5
    assert result["changed_channels"] == [1]
    assert result["host_elapsed_s"] == pytest.approx(1.0)


def test_zero_duration_ramp_still_writes_exact_destination():
    config = SimpleNamespace(rp_output_min_voltage=0.0, rp_output_max_voltage=1.0)
    rp = RPController(config)
    rp.p = object()
    rp.asg1, rp.asg2 = _ASG(0.2), _ASG(0.4)
    rp._commanded_dc[:] = (0.2, 0.4)

    result = rp.ramp_output_voltage(0.1, 0.3, duration_s=0.0, updates_per_s=50.0)

    assert result["steps"] == 1
    assert result["offset_writes"] == 2
    assert result["asg_readback_v"] == pytest.approx([0.1, 0.3])


@pytest.mark.parametrize("axis", ["phi1", "phi2"])
def test_continuous_sine_overrides_persisted_start_phase(axis):
    config = SimpleNamespace(rp_output_min_voltage=0.0, rp_output_max_voltage=1.0)
    rp = RPController(config)
    rp.p = object()
    rp.asg1, rp.asg2 = _ASG(0.0), _ASG(0.0)

    rp.set_continuous_sine(
        target_axis=axis, fixed_voltage=0.2, center=0.4,
        amplitude=0.3, frequency_hz=0.1,
    )

    target = rp.asg1 if axis == "phi1" else rp.asg2
    assert target.setup_calls[-1]["start_phase"] == 0.0


def test_ramp_final_requested_offset_is_exact(monkeypatch):
    monkeypatch.setattr("polarization_locking.hardware.rp_interface.time.sleep", lambda seconds: None)
    config = SimpleNamespace(rp_output_min_voltage=0., rp_output_max_voltage=1.)
    rp = RPController(config)
    rp.p = object()
    rp.asg1, rp.asg2 = _ASG(.23), _ASG(.51)
    rp._commanded_dc[:] = (.23, .51)
    rp.ramp_output_voltage(.11, .17, duration_s=0., updates_per_s=50.)
    assert rp.asg1.offset == .11 and rp.asg2.offset == .17
