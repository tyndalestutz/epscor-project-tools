from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

from .basler import BaslerCamera, BaslerError, save_record
from .config import CameraConfig


def _add_camera_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--serial", default="24934175")
    parser.add_argument("--exposure-us", type=float, default=100.0)
    parser.add_argument("--gain", type=float, default=0.0)
    parser.add_argument("--pixel-format", default="Mono12")
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    parser.add_argument("--offset-x", type=int, default=0)
    parser.add_argument("--offset-y", type=int, default=0)
    parser.add_argument("--timeout-ms", type=int, default=5_000)
    parser.add_argument("--open-retries", type=int, default=2)
    parser.add_argument("--grab-retries", type=int, default=2)


def _config(args: argparse.Namespace) -> CameraConfig:
    return CameraConfig(
        serial_number=args.serial,
        exposure_us=args.exposure_us,
        gain=args.gain,
        pixel_format=args.pixel_format,
        timeout_ms=args.timeout_ms,
        width=args.width,
        height=args.height,
        offset_x=args.offset_x,
        offset_y=args.offset_y,
        open_retries=args.open_retries,
        grab_retries=args.grab_retries,
    )


def _run_directory(output_root: Path, label: str) -> Path:
    return output_root / datetime.now().strftime(f"%Y%m%d_%H%M%S_{label}")


def _print_stats(prefix: str, record) -> None:
    stats = record.statistics
    print(
        f"{prefix} min={stats.minimum:g} max={stats.maximum:g} "
        f"mean={stats.mean:.3f} std={stats.std:.3f} saturated={stats.saturation_fraction:.3%}"
    )


def _preview_image(image: np.ndarray) -> np.ndarray:
    low, high = np.percentile(image, (0.5, 99.5))
    if high <= low:
        return np.zeros(image.shape, dtype=np.uint8)
    return np.clip((image - low) * 255.0 / (high - low), 0, 255).astype(np.uint8)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reusable Basler camera acquisition tools")
    commands = parser.add_subparsers(dest="command", required=True)

    list_parser = commands.add_parser("list", help="enumerate cameras without opening them")
    _add_camera_options(list_parser)

    info_parser = commands.add_parser("info", help="open the selected camera and print applied settings")
    _add_camera_options(info_parser)

    capture = commands.add_parser("capture", help="capture and save one or more records")
    _add_camera_options(capture)
    capture.add_argument("--average", type=int, default=1, help="raw frames averaged into each saved record")
    capture.add_argument("--count", type=int, default=1, help="number of records to save")
    capture.add_argument("--label", default="camera-capture")
    capture.add_argument("--output-root", type=Path, default=Path("experiments/polarization_visualization"))

    live = commands.add_parser("live", help="alignment preview; press Q or Escape to quit")
    _add_camera_options(live)
    live.add_argument("--window", default="Basler alignment preview")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        camera = BaslerCamera(_config(args))
        if args.command == "list":
            devices = camera.enumerate()
            if not devices:
                print("No Basler cameras detected")
                return 1
            for device in devices:
                suffix = f" name={device.user_name}" if device.user_name else ""
                print(f"{device.model} serial={device.serial} class={device.device_class}{suffix}")
            return 0

        if args.command == "info":
            with camera:
                print(f"device={camera.device_info}")
                for name, value in camera.actual_settings.items():
                    print(f"{name}={value}")
            return 0

        if args.command == "capture":
            if args.average < 1 or args.count < 1:
                raise ValueError("--average and --count must be at least one")
            frames_dir = _run_directory(args.output_root, args.label) / "frames"
            with camera:
                for index in range(args.count):
                    record, sources = camera.acquire_average_with_sources(args.average)
                    if args.average > 1:
                        raw_dir = frames_dir.parent / "raw_frames" / f"average_{index:04d}"
                        for source_index, source in enumerate(sources):
                            save_record(source, raw_dir / f"frame_{source_index:04d}")
                    image_path, metadata_path = save_record(record, frames_dir / f"frame_{index:04d}")
                    print(f"saved {image_path} and {metadata_path}")
                    if args.average > 1:
                        print(f"preserved {args.average} component frames under {raw_dir}")
                    _print_stats(f"frame {index}", record)
            return 0

        try:
            import cv2
        except ImportError as exc:
            raise BaslerError("OpenCV is required only for the live preview command") from exc
        with camera:
            print("Preview active; press Q or Escape to close")
            while True:
                record = camera.acquire()
                display = _preview_image(record.image)
                stats = record.statistics
                cv2.putText(
                    display,
                    f"mean {stats.mean:.1f}  max {stats.maximum:.0f}  sat {stats.saturation_fraction:.3%}",
                    (12, 28),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.65,
                    255,
                    1,
                    cv2.LINE_AA,
                )
                cv2.imshow(args.window, display)
                if cv2.waitKey(1) & 0xFF in (27, ord("q"), ord("Q")):
                    break
        cv2.destroyAllWindows()
        return 0
    except (BaslerError, ValueError) as exc:
        print(f"camera error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nCamera closed after interrupt", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
