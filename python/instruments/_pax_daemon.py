"""Supervised project daemon, launched only by PAX.local_session().

stdin is a parent-lifetime pipe. EOF requests graceful YAQD shutdown. A receipt
records command/USB-close success, never a claim of measured motor standstill.
"""
import json
import os
from pathlib import Path
import signal
import sys
import threading


def run(directory):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from polarization_locking.hardware.pax1000_daemon import ProjectPAX1000

    receipt = {"usb_opened": False, "motor_stop_command_succeeded": False, "usb_closed": False,
               "errors": [], "parent_pipe_closed": False}

    class OwnedPAX1000(ProjectPAX1000):
        instance = None

        def __init__(self, *args, **kwargs):
            type(self).instance = self  # Also retain partially initialized USB.
            self._owned_closed = False
            super().__init__(*args, **kwargs)

        def close(self):
            if self._owned_closed:
                return
            instrument = getattr(self, "inst", None)
            if instrument is not None:
                receipt["usb_opened"] = True
                try:
                    instrument.write("INPut:ROTation:STATe 0")
                    receipt["motor_stop_command_succeeded"] = True
                except BaseException as error:
                    receipt["errors"].append(f"motor stop: {type(error).__name__}: {error}")
                finally:
                    try:
                        instrument.close()
                        receipt["usb_closed"] = True
                    except BaseException as error:
                        receipt["errors"].append(f"USB close: {type(error).__name__}: {error}")
            self._owned_closed = True

    def parent_watchdog():
        sys.stdin.buffer.read(1)
        receipt["parent_pipe_closed"] = True
        # YAQD installs its SIGTERM handler before constructing the device.
        # Signals during its blocking startup settle are handled by the event
        # loop after initialization; the finally below also closes partial init.
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=parent_watchdog, daemon=True).start()
    sys.argv = [sys.argv[0], "-c", str(directory / "pax1000.toml"), "-v"]
    try:
        OwnedPAX1000.main()
    finally:
        if OwnedPAX1000.instance is not None:
            OwnedPAX1000.instance.close()
        (directory / "shutdown.json").write_text(json.dumps(receipt, indent=2) + "\n")


if __name__ == "__main__":
    run(Path(sys.argv[1]).resolve())
