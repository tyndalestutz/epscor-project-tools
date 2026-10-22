# One-off paired EOM raw capture

Run from the repository root with the existing NumPy/Pyrpl environment. This
standalone tool has no qualification-suite imports. No automatic Vπ discovery,
fitting, waveform phase reconstruction or device pass/fail decisions.

```bash
python scripts/eom_raw_capture.py --config scripts/eom_raw_capture.json --preflight --stage all
```

Edit a local copy of the JSON, preferably `experiments/_local/eom_raw_config.json`.
The supplied settings are **a template**, not authorization: review electrical
limits/engineering margins and record the current device/TEC conditions. Explicitly
set all six physical confirmations only after verifying them. The connection uses
credentials from the existing named Pyrpl profile, verifies the configured SSH
host fingerprint, then creates an in-memory Pyrpl configuration with only its
connection parameters. Existing module recipes are neither loaded nor overwritten;
ASG routes are disabled and DC zero settings applied before use. No credential
file is copied to the data/archive. A wrong board key stops the connection.

The example uses IN1/LV ±1 V for PD and IN2/HV ±20 V for the monitor. Software
cannot sense jumper positions or independently establish repaired-device safety.
The ±6 V monitor window, nominal HV/30 conversion, chain estimates and engineering
margins are operator declarations. An uncalibrated conversion remains labeled
uncalibrated; software checks do not establish absolute electrode voltage.
Independent hardware protection must cover startup, network gaps and cleanup.
Cleanup disables routes and sets DC zero; it does not claim measured zero HV.

First run DC; choose a stable steep operating point from the raw data/quicklook:

```bash
python scripts/eom_raw_capture.py --config experiments/_local/eom_raw_config.json --stage dc
```

Each up/down cycle includes both endpoints, including the reversal point twice.
Set `dynamic.bias_v`, `dynamic.bias_confirmed=true` and `dynamic.bias_evidence`
with the measured DC run/reference or explicit operator override. Confirm the
frequency band and peak amplitude. Then append dynamics to that same folder:

```bash
python scripts/eom_raw_capture.py --config experiments/_local/eom_raw_config.json --preflight --stage dynamic
python scripts/eom_raw_capture.py --config experiments/_local/eom_raw_config.json --stage dynamic --append experiments/_local/eom_raw_TIMESTAMP_ID
```

Append requires a completed/cleaned-up DC run with unchanged hardware/DC settings
and acquisition source. Only the dynamic settings may change. Initial and final
configuration snapshots remain in the event log; `config.json` records the latest
complete configuration. Standalone dynamic acquisition or `--stage all` also works
with an explicitly confirmed bias. Nothing automatically chooses a bias.

The default dynamic plan has 50 logarithmic frequencies plus seven points near
5 kHz, two separate buffers per frequency and both frequency directions. No Fourier
analysis is performed. Requested frequencies are snapped to the installed ASG's
125 MHz / 2^30 register grid, then read back. Requested and actual values are stored
separately. AC peaks are rounded down to the 1/8192 V DAC grid to remain within the
requested ceiling; the actual ASG peak/offset readbacks are separate evidence.
Decimation is selected for >=5 cycles and >=32 samples/cycle, checked against the
actual scope settings and timeout. FPGA and trace averaging are disabled. Optional
second amplitude: add another positive value to `peak_amplitudes_v` and approve its
maximum limit. Optional symmetric triangles: populate `triangle_frequencies_hz`;
the installed Pyrpl name is `ramp`, not `triangle` or `halframp`.

The script reads both signed raw channel buffers only after a single scope
acquisition completes, checks unchanged pointers/settings and agreement with the
public trace, and stores `(2,16384)` int16 arrays. Row 0 is PD/IN1; row 1 is
monitor/IN2. Convert each using `ADC volts = codes / 8192 * declared full scale`.
The ring ordering is changed explicitly by the installed Pyrpl trigger/delay rule;
no sample values are filtered, clipped or discarded. Sample times are relative to
each immediate-trigger buffer. Host monotonic bounds include transport. Separate
buffers are not contiguous, and ASG command phase is not an acquired third channel.

A returned buffer is saved **before** clipping/monitor checks. Failed buffers
remain indexed with `accepted=False`. Ctrl+C retains earlier data, records
interruption and attempts cleanup, then verifies/exports the partial dataset.
Missing buffers are errors, never synthesized. `status.json` separates acquisition
state from cleanup evidence. Every run contains the executable source, configuration,
metadata, events, index and capture arrays; installed scope/ASG source is included
for hardware runs. Optional `quicklook.png` plots DC buffer means only.

The final line prints **`ZIP: /absolute/path/to/run.zip`**. The ZIP contains the
complete directory; CRC, filenames, capture hashes, signed codes and sample counts
are checked. Malformed returned buffers, if any, remain explicitly identified.
Recheck/rebuild an archive without connecting hardware:

```bash
python scripts/eom_raw_capture.py --export experiments/_local/eom_raw_TIMESTAMP_ID
python scripts/test_eom_raw_capture.py
```

The focused mock test checks raw/index/ZIP integrity, retained negative samples,
interruption cleanup and DC→dynamic append. Mock outputs establish no physical
synchronization, calibration or EOM performance. Raw folders/ZIPs belong only under
the Git-ignored `experiments/_local/`; do not commit them.
