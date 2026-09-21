# IN2 visibility audit

The new passive run measured **21.23% apparent contrast**, with individual
captures of 20.95%, 20.71%, and 22.05%. The measurement assumes zero dark offset,
DC coupling, and an externally driven full-fringe excursion. No blocked-light
measurement or independent drive-frequency confirmation was available.
The recipe records the provisional 0.5 Hz external-drive frequency from the
earlier run. This is not a calibrated optical visibility measurement.

**Existing output activity matters:** a subsequent read-only register audit
found ASG1 routed to OUT2, with approximately 0.4 V offset, 0.4 V amplitude,
and 0.465661 Hz frequency. These match the earlier Copilot drive settings.
PID2 was also routed to OUT1; routing alone does not establish its signal.
We did not change either output. If OUT2 is cabled to actuator two, its motion
is part of this measurement. Disabling it and repeating requires resolving
that bench setup with the user. See `output-state-audit.json` for raw registers.

Copilot's saved 20:55:49 run reported 48.57% using the 5th and 95th percentiles
of a roughly -2 mV signal. Its raw-extrema calculation returned 104.9–131.3%,
which is not physically valid optical visibility. The percentile result included
the broadband noise envelope. It also had no measured dark-voltage correction.
The standalone script did not explicitly set FPGA averaging and advertised an
external frequency without using it to validate acquisition coverage.

The fresh acquisition enables FPGA decimation averaging and calculates extrema
from nonoverlapping averages over 1/64 of the declared drive period (about
31.2 ms here). It retains the actual sample interval and decimation.
The three peak-to-peak excursions are approximately 0.915, 0.887, and 0.968 mV.
Offline bandwidth sensitivity on these same captures gives mean contrasts:

| Bins per declared drive period | Mean apparent contrast |
| --- | --- |
| 32 | 20.26% |
| 64 (reported routine) | 21.23% |
| 128 | 21.87% |
| 256 | 22.12% |

`contrast-audit.png` compares an original noisy trace with the fresh trace.
`report.pdf` shows the new captures and their analysis. The original Copilot
datasets remain intact in the adjacent timestamped experiment folders.

Code cleanup removed today's standalone `pd_visibility_test.py` and replaced
its full-Pyrpl “scope-only” startup with a separate monitor transport, a
scope-register write guard, and raw-register restoration. Loading the full
saved Pyrpl profile could reapply output drives. The replacement is the normal
catalog entry `pd-visibility`, with recipes, CSV, traces, PDF, and run status.
Earlier September 18 Stokes/PAX source changes were preserved. Generated tracked
bytecode was restored. All 132 offline polarization-locking tests passed.
