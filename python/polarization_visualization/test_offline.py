import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from polarization_visualization.basler import (
    BaslerCamera,
    BaslerError,
    CameraNotFoundError,
    save_record,
)
from polarization_visualization.config import AnalyzerConfig, CameraConfig


class FakeNode:
    def __init__(self, value, minimum=0, maximum=10_000, increment=1, writable=True):
        self.value = value
        self.minimum = minimum
        self.maximum = maximum
        self.increment = increment
        self.writable = writable

    def IsWritable(self):
        return self.writable

    def SetValue(self, value):
        if isinstance(self.value, int) and not isinstance(value, int):
            raise TypeError("integer node requires an integer")
        self.value = value

    def GetValue(self):
        return self.value

    def GetMin(self):
        return self.minimum

    def GetMax(self):
        return self.maximum

    def GetInc(self):
        return self.increment


class FakeNodeMap:
    def __init__(self):
        self.ExposureAuto = FakeNode("Continuous")
        self.GainAuto = FakeNode("Continuous")
        self.PixelFormat = FakeNode("Mono8")
        self.ExposureTime = FakeNode(5000.0, 20.0, 1_000_000.0, 1.0)
        self.Gain = FakeNode(2.0, 0.0, 24.0, 0.1)
        self.Width = FakeNode(1920, 16, 1920, 8)
        self.Height = FakeNode(1200, 16, 1200, 2)
        self.OffsetX = FakeNode(0, 0, 1904, 8)
        self.OffsetY = FakeNode(0, 0, 1184, 2)


class FakeDescriptor:
    def __init__(self, serial="24934175"):
        self.serial = serial

    def GetModelName(self):
        return "acA1920-40um"

    def GetSerialNumber(self):
        return self.serial

    def GetDeviceClass(self):
        return "BaslerUsb"

    def GetUserDefinedName(self):
        return "test-camera"


class FakeGrab:
    def __init__(self, success=True, image=None):
        self.success = success
        self.Array = np.array([[0, 4095], [100, 200]], dtype=np.uint16) if image is None else image
        self.TimeStamp = 12345
        self.BlockID = 7
        self.released = False

    def GrabSucceeded(self):
        return self.success

    def GetErrorCode(self):
        return 99

    def GetErrorDescription(self):
        return "simulated USB failure"

    def Release(self):
        self.released = True


class FakeInstantCamera:
    def __init__(self, grabs):
        self.nodes = FakeNodeMap()
        self.grabs = list(grabs)
        self.opened = False
        self.close_count = 0

    def Open(self):
        self.opened = True

    def Close(self):
        self.opened = False
        self.close_count += 1

    def IsOpen(self):
        return self.opened

    def IsGrabbing(self):
        return False

    def GetNodeMap(self):
        return self.nodes

    def GrabOne(self, _timeout):
        return self.grabs.pop(0)


class FakeFactory:
    def __init__(self, descriptors, cameras):
        self.descriptors = descriptors
        self.cameras = list(cameras)

    def EnumerateDevices(self):
        return self.descriptors

    def CreateDevice(self, descriptor):
        return descriptor


class FakePylon:
    def __init__(self, descriptors=None, cameras=None):
        self.factory = FakeFactory(descriptors or [], cameras or [])
        outer = self

        class TlFactory:
            @staticmethod
            def GetInstance():
                return outer.factory

        self.TlFactory = TlFactory

    def InstantCamera(self, _device):
        return self.factory.cameras.pop(0)


class OfflineSafetyTests(unittest.TestCase):
    def test_analyzer_is_locked_by_default(self):
        with self.assertRaises(RuntimeError):
            AnalyzerConfig().require_verified_transfer_chain()

    def test_camera_config_rejects_unsafe_values(self):
        with self.assertRaises(ValueError):
            CameraConfig(exposure_us=0)
        with self.assertRaises(ValueError):
            CameraConfig(offset_x=-1)
        with self.assertRaises(ValueError):
            CameraConfig(grab_retries=-1)

    def test_missing_serial_is_specific_error(self):
        camera = BaslerCamera(CameraConfig(open_retries=0), pylon_module=FakePylon([FakeDescriptor("other")]))
        with self.assertRaises(CameraNotFoundError) as caught:
            camera.open()
        self.assertIn("other", str(caught.exception))

    def test_context_configures_and_closes(self):
        fake_camera = FakeInstantCamera([FakeGrab()])
        config = CameraConfig(exposure_us=100, width=1001, height=501, offset_x=17, offset_y=9)
        camera = BaslerCamera(config, pylon_module=FakePylon([FakeDescriptor()], [fake_camera]))
        with camera:
            self.assertTrue(camera.is_open)
            self.assertEqual(camera.actual_settings["exposure_auto"], "Off")
            self.assertEqual(camera.actual_settings["gain_auto"], "Off")
            self.assertEqual(camera.actual_settings["width"], 1000)
            self.assertEqual(camera.actual_settings["height"], 500)
            self.assertEqual(camera.actual_settings["offset_x"], 16)
            self.assertEqual(camera.actual_settings["offset_y"], 8)
        self.assertFalse(camera.is_open)
        self.assertEqual(fake_camera.close_count, 1)

    def test_default_roi_restores_full_sensor(self):
        fake_camera = FakeInstantCamera([FakeGrab()])
        fake_camera.nodes.Width.value = 640
        fake_camera.nodes.Height.value = 480
        camera = BaslerCamera(CameraConfig(), pylon_module=FakePylon([FakeDescriptor()], [fake_camera]))
        with camera:
            self.assertEqual(camera.actual_settings["width"], 1920)
            self.assertEqual(camera.actual_settings["height"], 1200)

    def test_frame_statistics_and_metadata(self):
        fake_camera = FakeInstantCamera([FakeGrab()])
        camera = BaslerCamera(CameraConfig(), pylon_module=FakePylon([FakeDescriptor()], [fake_camera]))
        with camera:
            record = camera.acquire()
        self.assertEqual(record.statistics.minimum, 0)
        self.assertEqual(record.statistics.maximum, 4095)
        self.assertEqual(record.statistics.saturation_fraction, 0.25)
        self.assertGreater(record.statistics.std, 0)
        self.assertEqual(record.metadata["camera"]["serial"], "24934175")
        self.assertEqual(record.metadata["block_id"], 7)

    def test_grab_failure_reconnects(self):
        failed = FakeGrab(success=False)
        first_camera = FakeInstantCamera([failed])
        second_camera = FakeInstantCamera([FakeGrab(image=np.ones((2, 2), dtype=np.uint16))])
        config = CameraConfig(open_retries=0, grab_retries=1, retry_delay_s=0)
        camera = BaslerCamera(config, pylon_module=FakePylon([FakeDescriptor()], [first_camera, second_camera]))
        with camera:
            record = camera.acquire()
        self.assertTrue(failed.released)
        self.assertEqual(record.statistics.mean, 1.0)
        self.assertEqual(first_camera.close_count, 1)
        self.assertEqual(second_camera.close_count, 1)

    def test_acquire_requires_open_camera(self):
        with self.assertRaises(BaslerError):
            BaslerCamera(pylon_module=FakePylon()).acquire()

    def test_average_count_is_positive(self):
        with self.assertRaises(ValueError):
            BaslerCamera(pylon_module=FakePylon()).acquire_average(0)

    def test_average_can_return_raw_sources(self):
        grabs = [FakeGrab(image=np.full((2, 2), value, dtype=np.uint16)) for value in (2, 4)]
        fake_camera = FakeInstantCamera(grabs)
        camera = BaslerCamera(CameraConfig(), pylon_module=FakePylon([FakeDescriptor()], [fake_camera]))
        with camera:
            average, sources = camera.acquire_average_with_sources(2)
        self.assertEqual(len(sources), 2)
        self.assertEqual(average.statistics.mean, 3.0)
        self.assertEqual(sources[0].image.dtype, np.uint16)

    def test_save_record_round_trip(self):
        camera = BaslerCamera(CameraConfig(), pylon_module=FakePylon())
        record = camera._record(np.array([[1, 2]], dtype=np.uint16), averaged_frames=1)
        with tempfile.TemporaryDirectory() as directory:
            image_path, metadata_path = save_record(record, Path(directory) / "frame")
            np.testing.assert_array_equal(np.load(image_path, allow_pickle=False), record.image)
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(metadata["raw_file"], "frame.npy")
            self.assertEqual(metadata["statistics"]["mean"], 1.5)


if __name__ == "__main__":
    unittest.main()
