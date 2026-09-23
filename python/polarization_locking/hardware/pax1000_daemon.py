#!/usr/bin/env python3
"""Project-local PAX daemon matching the known-good Windows configuration."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import time

import pyvisa
from yaqd_core import HasMeasureTrigger, IsSensor, UsesSerial
from yaqd_core._is_daemon import classproperty
import yaqd_thorlabs._thorlabs_pax1000 as upstream_pax


class ProjectPAX1000(UsesSerial, HasMeasureTrigger, IsSensor):
    """YAQD-compatible PAX1000 daemon without the upstream hard-coded mode 9."""

    _kind = "thorlabs-pax1000"

    @classproperty
    def _avro_protocol(cls):
        """Reuse the installed PAX protocol while keeping this daemon local."""
        protocol = Path(upstream_pax.__file__).with_name("thorlabs-pax1000.avpr")
        with protocol.open() as handle:
            return json.load(handle)

    def __init__(self, name, config, config_filepath):
        super().__init__(name, config, config_filepath)
        manager = pyvisa.ResourceManager() if sys.platform.startswith("win32") else pyvisa.ResourceManager("@py")
        for resource in manager.list_resources():
            try:
                if resource.split("::")[3] == self._config["serial"]:
                    self.inst = manager.open_resource(resource)
                    break
            except (IndexError, KeyError):
                continue
        else:
            raise ConnectionError(f"No VISA resource matches configured PAX serial {self._config['serial']!r}")
        self._channel_units = {
            "revisions": None, "timestamp": None, "adc_min": None, "adc_max": None, "rev_time": None,
            "theta": "radian", "eta": "radian", "dop": None, "ptotal": None,
        }
        self._channel_names = list(self._channel_units)
        self.inst.write(f"SENSe1:POWer:RANGe:AUTO {int(self._config['autorange'])}")
        self.inst.write(f"INPut:ROTation:VELocity {float(self._config['velocity']):g}")
        self.inst.write(f"SENS:CALC {int(self._config.get('measurement_mode', 5))}")
        self._wavelength = float(self.inst.query("SENSe:CORRection:WAVelength?")) * 1e9
        self.inst.write("INPut:ROTation:STATe 1")
        # Wait once at spin-up, before any client can acquire measurements.
        # The motor and autorange need time to settle, even if rotation is on.
        time.sleep(5.0)

    def close(self):
        self.inst.write("INPut:ROTation:STATe 0")
        self.inst.close()

    def direct_serial_write(self, message: bytes):
        """YAQD requests bytes, whereas PyVISA's write expects text."""
        self.inst.write(message.decode())

    def get_wavelength(self) -> float:
        return self._wavelength

    def set_wavelength(self, wavelength: float):
        self.inst.write(f"SENSe:CORRection:WAVelength {format(wavelength / 1e9, '.3e')}")
        self._wavelength = float(self.inst.query("SENSe:CORRection:WAVelength?")) * 1e9

    async def _measure(self):
        await asyncio.sleep(0.03)
        values = [float(value) for value in self.inst.query("SENSe:DATA:PRIMary:LATest?").split(",")]
        return {
            "revisions": values[0], "timestamp": values[1], "adc_min": values[5], "adc_max": values[6],
            "rev_time": values[7], "theta": values[9], "eta": values[10], "dop": values[11], "ptotal": values[12],
        }


if __name__ == "__main__":
    ProjectPAX1000.main()
