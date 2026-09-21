# Raw phi1 calibration

Specialist acquisition outside the menu recipe runner. Use this when you need
raw PAX records, command readbacks, and explicit electrical-loopback evidence.
It controls RP outputs; it is not a passive external-drive measurement.
Start with the [operating procedure](operating-procedure.md).

For direct calibration without fits, use the dedicated raw acquisition command:

```bash
PYTHONPATH=python python -m polarization_locking.raw_calibration --stop 0.8 --step 0.02 --repeats 3 --settle 1 --samples 5 --label repeatability
PYTHONPATH=python python -m polarization_locking.raw_calibration --hold-voltage 0.4 --hold-points 30 --samples 10 --label fixed-voltage
```

Use `--pax-location` and `--input-connections` to record the actual wiring.
`--levels 0,0.2,0.3,0.35,0.4,0.6,0.8` supplies an explicit ascending grid;
the command also visits it in reverse. All voltages are RP commands within 0–1 V.
Each run saves `data.csv`, exact PAX records in `pax_raw.jsonl`, command/register
readbacks in `commands.jsonl`, full scope snapshots in one `scope.npz`, active settings
in `run.json`, and a PDF in the dated experiments folder. Fixed-voltage mode
writes the command once and observes successive blocks without rewriting it.
During acquisition, snapshots are written incrementally; on exit they are packed
into the single archive and verified byte for byte before the temporary snapshots
are removed. Archive keys preserve the original point and array names, for example
`0000/samples`, `0000/analog_in1_samples` and `cleanup-zero/samples`.

The report shows every measurement and discrete π-change brackets from labelled
step medians, with wider brackets including the observed endpoint sample spread.
There are no fits, interpolated crossings, drift corrections, or assumed driver
gains. `atan2(S3, -S2)` is a projected equatorial phase; if S1 departs from zero,
its π-change is not an independent calibration of actuator retardance.
Internal OUT1 scope traces establish the digital pre-DAC signal, not an electrical
measurement of the RP BNC or piezo terminals. A verified electrical monitor is
needed to complete that voltage-chain mapping.

Regenerate these raw reports with their dedicated, fit-free report command:

```bash
PYTHONPATH=python python -m polarization_locking.reports.raw_calibration experiments/polarization_locking/YYYY-MM-DD/raw-run-folder
```

On the installed FPGA, the PyRPL `scope.voltage_out1/2` properties address trigger
timestamp registers, so this collector does not use them. It captures the scope
with an explicitly selected `out1` source instead. Similarly, scope register
inputs follow the selected source; physical IN1/IN2 are recorded only after
explicitly routing those sources. Earlier connection probes carry validity notes
in their `run.json`; do not treat their invalid register fields as voltages.


This command has its own `run.json` parameters/configuration schema; it does not
write a menu `recipe.json`. To repeat it, reconstruct its command from the saved
`parameters`, review the wiring, and create a new labelled run. Its `run.json`
does not currently include the menu runner's dependency-version/Git provenance.
Record the commit and environment with the experiment notes when using it.
