from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

import numpy as np

from .config import CameraConfig


class BaslerError(RuntimeError):
    """Base exception for camera discovery, configuration, and acquisition."""


class CameraNotFoundError(BaslerError):
    pass


class CameraConfigurationError(BaslerError):
    pass


class CameraAcquisitionError(BaslerError):
    pass


@dataclass(frozen=True)
class DeviceInfo:
    model: str
    serial: str
    device_class: str
    user_name: str = ""


@dataclass(frozen=True)
class FrameStatistics:
    minimum: float
    maximum: float
    mean: float
    std: float
    saturation_fraction: float


@dataclass(frozen=True)
class FrameRecord:
    image: np.ndarray
    timestamp_utc: str
    statistics: FrameStatistics
    metadata: dict[str, Any]


def _node(node_map: Any, name: str) -> Any:
    value = getattr(node_map, name, None)
    if value is None:
        raise CameraConfigurationError(f"Camera feature {name!r} is unavailable")
    return value


def _set_enum(node_map: Any, name: str, value: str) -> str:
    feature = _node(node_map, name)
    if not feature.IsWritable():
        raise CameraConfigurationError(f"Camera feature {name!r} is read-only")
    try:
        feature.SetValue(value)
    except Exception as exc:
        raise CameraConfigurationError(f"Cannot set {name}={value!r}: {exc}") from exc
    return str(feature.GetValue())


def _align_number(feature: Any, value: float) -> float:
    lower, upper = float(feature.GetMin()), float(feature.GetMax())
    bounded = min(max(float(value), lower), upper)
    get_inc = getattr(feature, "GetInc", None)
    if get_inc is not None:
        try:
            increment = float(get_inc())
        except Exception:
            increment = 0.0
        if increment > 0:
            bounded = lower + round((bounded - lower) / increment) * increment
            bounded = min(max(bounded, lower), upper)
    return bounded


def _set_number(node_map: Any, name: str, value: float) -> float:
    feature = _node(node_map, name)
    if not feature.IsWritable():
        raise CameraConfigurationError(f"Camera feature {name!r} is read-only")
    applied = _align_number(feature, value)
    current = feature.GetValue()
    if isinstance(current, (int, np.integer)) and not isinstance(current, bool):
        applied = int(round(applied))
    try:
        feature.SetValue(applied)
    except Exception as exc:
        raise CameraConfigurationError(f"Cannot set {name}={applied}: {exc}") from exc
    return float(feature.GetValue())


def _optional_string(device: Any, method_name: str) -> str:
    method = getattr(device, method_name, None)
    if method is None:
        return ""
    try:
        return str(method())
    except Exception:
        return ""


class BaslerCamera:
    """Thread-safe Basler lifecycle and deterministic quantitative acquisition.

    The object owns at most one pylon camera. ``open`` is idempotent, every
    returned image owns its memory, and ``close`` is safe to call repeatedly.
    Failed grabs are retried after a full reconnect so transient USB failures
    do not leave a stale InstantCamera behind.
    """

    def __init__(self, config: CameraConfig | None = None, *, pylon_module: Any = None) -> None:
        self.config = config or CameraConfig()
        self._pylon = pylon_module
        self._camera: Any = None
        self._device_info: DeviceInfo | None = None
        self._configured: dict[str, Any] = {}
        self._lock = threading.RLock()

    @property
    def camera(self) -> Any:
        """Underlying pylon camera for exceptional advanced use."""
        return self._camera

    @property
    def is_open(self) -> bool:
        with self._lock:
            return bool(self._camera is not None and self._camera.IsOpen())

    @property
    def device_info(self) -> DeviceInfo | None:
        return self._device_info

    @property
    def actual_settings(self) -> dict[str, Any]:
        return dict(self._configured)

    def _get_pylon(self) -> Any:
        if self._pylon is None:
            try:
                from pypylon import pylon
            except ImportError as exc:
                raise BaslerError("pypylon is required; activate the camera-enabled jl-env") from exc
            self._pylon = pylon
        return self._pylon

    def enumerate(self) -> list[DeviceInfo]:
        try:
            devices = self._get_pylon().TlFactory.GetInstance().EnumerateDevices()
        except Exception as exc:
            raise BaslerError(f"Basler device enumeration failed: {exc}") from exc
        return [self._describe(device) for device in devices]

    @staticmethod
    def _describe(device: Any) -> DeviceInfo:
        return DeviceInfo(
            model=_optional_string(device, "GetModelName"),
            serial=_optional_string(device, "GetSerialNumber"),
            device_class=_optional_string(device, "GetDeviceClass"),
            user_name=_optional_string(device, "GetUserDefinedName"),
        )

    def _find_device(self) -> Any:
        pylon = self._get_pylon()
        try:
            devices = list(pylon.TlFactory.GetInstance().EnumerateDevices())
        except Exception as exc:
            raise BaslerError(f"Basler device enumeration failed: {exc}") from exc
        matches = [d for d in devices if _optional_string(d, "GetSerialNumber") == self.config.serial_number]
        if len(matches) != 1:
            found = [_optional_string(d, "GetSerialNumber") for d in devices]
            hint = " Close pylon Viewer if it owns the camera." if self.config.serial_number in found else ""
            raise CameraNotFoundError(
                f"Expected one Basler serial {self.config.serial_number}; detected serials: {found}.{hint}"
            )
        return matches[0]

    def open(self) -> "BaslerCamera":
        with self._lock:
            if self.is_open:
                return self
            last_error: Exception | None = None
            for attempt in range(self.config.open_retries + 1):
                try:
                    pylon = self._get_pylon()
                    descriptor = self._find_device()
                    camera = pylon.InstantCamera(pylon.TlFactory.GetInstance().CreateDevice(descriptor))
                    camera.Open()
                    self._camera = camera
                    self._device_info = self._describe(descriptor)
                    self.configure()
                    return self
                except CameraNotFoundError:
                    self.close()
                    raise
                except Exception as exc:
                    last_error = exc
                    self.close()
                    if attempt < self.config.open_retries:
                        time.sleep(self.config.retry_delay_s)
            raise BaslerError(
                f"Could not open/configure Basler {self.config.serial_number} after "
                f"{self.config.open_retries + 1} attempt(s): {last_error}"
            ) from last_error

    def configure(self) -> None:
        with self._lock:
            if not self.is_open:
                raise BaslerError("Camera is not open")
            nodes = self._camera.GetNodeMap()
            settings: dict[str, Any] = {
                "exposure_auto": _set_enum(nodes, "ExposureAuto", "Off"),
                "gain_auto": _set_enum(nodes, "GainAuto", "Off"),
                "pixel_format": _set_enum(nodes, "PixelFormat", self.config.pixel_format),
            }
            # Reset position before changing dimensions, then apply position.
            _set_number(nodes, "OffsetX", 0)
            _set_number(nodes, "OffsetY", 0)
            width = self.config.width if self.config.width is not None else _node(nodes, "Width").GetMax()
            height = self.config.height if self.config.height is not None else _node(nodes, "Height").GetMax()
            _set_number(nodes, "Width", width)
            _set_number(nodes, "Height", height)
            settings.update(
                width=int(_node(nodes, "Width").GetValue()),
                height=int(_node(nodes, "Height").GetValue()),
                offset_x=int(_set_number(nodes, "OffsetX", self.config.offset_x)),
                offset_y=int(_set_number(nodes, "OffsetY", self.config.offset_y)),
                exposure_us=_set_number(nodes, "ExposureTime", self.config.exposure_us),
                gain=_set_number(nodes, "Gain", self.config.gain),
            )
            self._configured = settings

    def close(self) -> None:
        with self._lock:
            camera, self._camera = self._camera, None
            if camera is None:
                return
            try:
                if getattr(camera, "IsGrabbing", lambda: False)():
                    camera.StopGrabbing()
            finally:
                if camera.IsOpen():
                    camera.Close()

    def __enter__(self) -> "BaslerCamera":
        return self.open()

    def __exit__(self, *_: object) -> None:
        self.close()

    def acquire(self) -> FrameRecord:
        with self._lock:
            if not self.is_open:
                raise BaslerError("Camera is not open; use open() or a with block")
            last_error: Exception | None = None
            for attempt in range(self.config.grab_retries + 1):
                try:
                    return self._grab_once()
                except Exception as exc:
                    last_error = exc
                    if attempt < self.config.grab_retries:
                        self.close()
                        time.sleep(self.config.retry_delay_s)
                        self.open()
            raise CameraAcquisitionError(
                f"Grab failed after {self.config.grab_retries + 1} attempt(s): {last_error}"
            ) from last_error

    def _grab_once(self) -> FrameRecord:
        result = self._camera.GrabOne(self.config.timeout_ms)
        try:
            if not result.GrabSucceeded():
                raise CameraAcquisitionError(
                    f"Basler grab failed: {result.GetErrorCode()} {result.GetErrorDescription()}"
                )
            image = np.array(result.Array, copy=True)
            frame_metadata = {
                "camera_timestamp": int(getattr(result, "TimeStamp", 0)),
                "block_id": int(getattr(result, "BlockID", 0)),
            }
        finally:
            result.Release()
        return self._record(image, averaged_frames=1, frame_metadata=frame_metadata)

    def acquire_average(self, count: int) -> FrameRecord:
        average, _ = self.acquire_average_with_sources(count)
        return average

    def acquire_average_with_sources(self, count: int) -> tuple[FrameRecord, list[FrameRecord]]:
        """Return an average and its source records so callers can preserve raw frames."""
        if count < 1:
            raise ValueError("count must be at least one")
        records = [self.acquire() for _ in range(count)]
        average = np.mean([record.image.astype(np.float64) for record in records], axis=0)
        return (
            self._record(
                average,
                averaged_frames=count,
                frame_metadata={
                    "source_timestamps_utc": [record.timestamp_utc for record in records],
                    "source_block_ids": [record.metadata.get("block_id", 0) for record in records],
                },
            ),
            records,
        )

    def acquire_many(self, count: int) -> Iterator[FrameRecord]:
        if count < 1:
            raise ValueError("count must be at least one")
        for _ in range(count):
            yield self.acquire()

    def _record(
        self,
        image: np.ndarray,
        *,
        averaged_frames: int,
        frame_metadata: dict[str, Any] | None = None,
    ) -> FrameRecord:
        if image.size == 0:
            raise CameraAcquisitionError("Camera returned an empty image")
        saturation_code = self._saturation_code(image)
        threshold = max(0, saturation_code - self.config.saturation_margin_codes)
        stats = FrameStatistics(
            minimum=float(np.min(image)),
            maximum=float(np.max(image)),
            mean=float(np.mean(image)),
            std=float(np.std(image)),
            saturation_fraction=float(np.mean(image >= threshold)),
        )
        timestamp = datetime.now(timezone.utc).isoformat()
        metadata = {
            "schema_version": 1,
            "timestamp_utc": timestamp,
            "camera": asdict(self._device_info) if self._device_info else {"serial": self.config.serial_number},
            "averaged_frames": averaged_frames,
            "image_shape": list(image.shape),
            "image_dtype": str(image.dtype),
            "saturation_code": saturation_code,
            "settings": self.actual_settings,
            **(frame_metadata or {}),
        }
        return FrameRecord(image=image, timestamp_utc=timestamp, statistics=stats, metadata=metadata)

    def _saturation_code(self, image: np.ndarray) -> int:
        digits = "".join(c for c in self.config.pixel_format if c.isdigit())
        if digits:
            return (1 << int(digits)) - 1
        if np.issubdtype(image.dtype, np.integer):
            return int(np.iinfo(image.dtype).max)
        raise CameraConfigurationError("Cannot infer saturation code from pixel format")


def save_record(record: FrameRecord, output_stem: Path) -> tuple[Path, Path]:
    """Save a lossless array and self-describing adjacent JSON metadata."""
    output_stem = Path(output_stem)
    output_stem.parent.mkdir(parents=True, exist_ok=True)
    image_path = output_stem.with_suffix(".npy")
    metadata_path = output_stem.with_suffix(".json")
    np.save(image_path, record.image, allow_pickle=False)
    payload = {**record.metadata, "statistics": asdict(record.statistics), "raw_file": image_path.name}
    metadata_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return image_path, metadata_path


def save_records(records: Sequence[FrameRecord], frames_dir: Path) -> list[tuple[Path, Path]]:
    return [save_record(record, Path(frames_dir) / f"frame_{index:04d}") for index, record in enumerate(records)]
