import unittest
from dataclasses import replace

import numpy as np

from polarization_visualization.analyzer import AnalyzerController
from polarization_visualization.config import AnalyzerConfig, SYSTEM_TEST_ANALYZER_CONFIG


class FakeASG:
    def __init__(self):
        self.output_direct = "off"
        self.settings = {}
        self.history = []

    def setup(self, **settings):
        self.settings = settings
        self.history.append(dict(settings))


class FakeModule:
    def __init__(self):
        self.output_direct = "off"


class FakeScope:
    def __init__(self):
        self.input1 = "in1"
        self.duration = 0.02
        self.decimation = 8

    def single(self, timeout):
        return [np.asarray([0.1, 0.2, 0.3]), np.zeros(3)]


class FakeRP:
    def __init__(self):
        self.asg0 = FakeASG()
        self.asg1 = FakeASG()
        self.pid0 = FakeModule()
        self.pid1 = FakeModule()
        self.pid2 = FakeModule()
        self.iq0 = FakeModule()
        self.iq1 = FakeModule()
        self.iq2 = FakeModule()
        self.scope = FakeScope()


class FakePyrpl:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.rp = FakeRP()


class AnalyzerSafetyTests(unittest.TestCase):
    def test_locked_config_cannot_construct_controller(self):
        with self.assertRaises(RuntimeError):
            AnalyzerController(AnalyzerConfig())

    def test_documented_command_mapping(self):
        verified = replace(
            SYSTEM_TEST_ANALYZER_CONFIG,
            lcvr_bipolar_output_verified=True,
            lcvr_terminal_gain_verified=True,
        )
        controller = AnalyzerController(verified, pyrpl_factory=FakePyrpl)
        self.assertAlmostEqual(controller.eom_rp_command_for_device_voltage(150.0), 1.0)
        self.assertAlmostEqual(controller.lcvr_rp_amplitude_for_cell_vpp(4.0), 4.0 / 33.75)

    def test_limits_reject_eom_and_lcvr_overdrive(self):
        controller = AnalyzerController(SYSTEM_TEST_ANALYZER_CONFIG, pyrpl_factory=FakePyrpl)
        with self.assertRaises(ValueError):
            controller.eom_rp_command_for_device_voltage(150.01)
        with self.assertRaises(ValueError):
            controller.lcvr_rp_amplitude_for_cell_vpp(0.1)
        with self.assertRaises(ValueError):
            controller.lcvr_rp_amplitude_for_cell_vpp(1.0)
        with self.assertRaises(ValueError):
            controller.lcvr_rp_amplitude_for_cell_vpp(4.01)
        with self.assertRaises(ValueError):
            controller.lcvr_rp_amplitude_for_cell_vpp(5.01)

    def test_lcvr_is_zero_mean_square_and_close_routes_off(self):
        verified = replace(
            SYSTEM_TEST_ANALYZER_CONFIG,
            lcvr_bipolar_output_verified=True,
            lcvr_terminal_gain_verified=True,
        )
        controller = AnalyzerController(verified, pyrpl_factory=FakePyrpl)
        controller.connect()
        controller.set_lcvr_vpp(4.0)
        self.assertEqual(controller.asg_lcvr.settings["waveform"], "square")
        self.assertEqual(controller.asg_lcvr.settings["offset"], 0.0)
        self.assertEqual(controller.asg_lcvr.settings["frequency"], 2000.0)
        self.assertAlmostEqual(controller.asg_lcvr.settings["amplitude"], 4.0 / 33.75)
        eom, lcvr = controller.asg_eom, controller.asg_lcvr
        controller.close()
        self.assertEqual(eom.output_direct, "off")
        self.assertEqual(lcvr.output_direct, "off")
        self.assertEqual(lcvr.settings["amplitude"], 0.0)

    def test_pd_uses_in2_and_restores_scope(self):
        controller = AnalyzerController(SYSTEM_TEST_ANALYZER_CONFIG, pyrpl_factory=FakePyrpl)
        with controller:
            scope = controller.p.rp.scope
            reading = controller.read_photodiode()
            self.assertEqual(scope.input1, "in1")
            self.assertEqual(scope.duration, 0.02)
            self.assertEqual(scope.decimation, 8)
        self.assertAlmostEqual(reading.mean_voltage, 0.2)
        self.assertEqual(reading.sample_count, 3)


if __name__ == "__main__":
    unittest.main()
