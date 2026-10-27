# Instruments for interactive notebooks

Small device interfaces for **connect → verify → acquire → inspect → save →
analyze** work. Importing or constructing an interface does not contact hardware.
There is no experiment runner, file writer, plotting, automatic calibration or
background acquisition here. Existing suite entry points and configurations remain
supported; notebooks need not import experiment-specific configuration.

## Environment

Use the established laboratory kernel and drivers. Make the repository's `python/`
directory importable (the existing `PYTHONPATH=python` convention, or the notebook's
explicit source-path setup). Do not install/upgrade drivers inside acquisition
cells. The current bench dependencies live in
[polarization_locking/requirements.txt](../polarization_locking/requirements.txt);
they are not a pinned environment snapshot. Record the actual interpreter,
dependency versions, repository revision/dirty source and effective device settings
alongside experimental data. No driver versions are changed by this increment.

## Red Pitaya: attach and verify

```python
from instruments import RedPitaya

if "rp" in globals():
    rp.close()  # Only the RedPitaya instance created by this notebook.
rp = RedPitaya("192.168.1.98", profile="scope_config")
rp.attach_monitor()
rp.read_status()
```

The returned dictionary contains six current scope/input-mux register values and
host request/receive timing. This verifies live FPGA register communication, not a
fresh ADC capture, physical board identity or measured output voltage. To release:

```python
rp.close()
```

`profile` accepts an existing Pyrpl profile name or a profile file path. Only its
connection credentials and monitor executable location are read. The named-profile
lookup uses Pyrpl's own user-config directory. The existing SSH trust behavior
(`AutoAddPolicy`, no fingerprint pinning) is retained; status does not authenticate
a particular physical board beyond this transport. Credentials are not included
in returned status.

**Attachment side effects:** sets `QT_QPA_PLATFORM=offscreen` if unset, opens SSH,
requires `/tmp/loaded_fpga.inf` to identify an already-running Pyrpl FPGA, starts a
separate monitor server (default port 2223), snapshots six registers and constructs
a Pyrpl Scope. Scope construction/configuration can change shared scope state.
There is no full Pyrpl initialization, FPGA reload, persisted module setup or
output-routing/voltage command. Existing outputs can remain active.

`rp.scope` exposes the actual Pyrpl Scope for subsequent explicit configuration
and acquisition. Scope/input-mux writes are guarded against output-register writes.
The existing monitor assumes `frequency_correction = 1.0`; this is not a measured
clock calibration. Do not infer timing accuracy or ADC range from attachment.

`close()` attempts restoration of every saved register and release of both monitor
and SSH transports, even if one restoration fails. It restores the same six
registers as the established passive path, not every arbitrary change made through
`rp.scope`. Closing does not zero outputs. A successful close is idempotent;
cleanup failures propagate and block a new attachment on that instance. The
monitor driver sends its close command; remote-process termination/repeated-port
reuse still needs bench verification. No process search or forced takeover occurs.
Coordinate scope access: another session's changes could be overwritten by
restoration. Repeated attach without closing raises an error.

This interface intentionally provides no active output initialization or drive
methods yet. Existing suite controllers retain those functions and their voltage
bounds and cleanup. A notebook drive API needs a separately bounded change with
explicit command limits, routing, initialization and safe-output behavior.

## PAX: notebook-managed session

For notebook operations, put the work in a `with` block. This starts and stops
an owned local daemon within the cell; no separate terminal is needed:

```python
from instruments import PAX

with PAX.local_session() as pax:
    display(pax.read_status())
    pax_raw = pax.read_fresh()

pax_raw
```

Entering opens the configured USB PAX, applies the existing daemon's autorange,
rotation velocity and measurement mode, starts its motor and waits the existing
five seconds for settling. Startup progress is printed. The default config is
`polarization_locking/hardware/pax1000.toml` (currently serial `M01303584`, port
38400, 60 Hz rotation, mode 5, autorange enabled). Select another existing config
with `config_path=...`; its original contents and the effective temporary config
remain in `pax.session_configuration`. Wavelength is read from the instrument,
not implicitly changed. Inspect status or explicitly set the actual wavelength
inside the block before acquiring.

The daemon runs under the notebook's Python interpreter with the existing bench
dependencies. A unique temporary config restricts it to IPv4 loopback, and the
client verifies that config path and serial before yielding. An occupied port is
rejected before launch. The session never searches for, adopts, or terminates an
unrelated daemon. If an older terminal-launched daemon is still running, close
that owning session once before using this interface. This does not establish
exclusive ownership against other software accessing the same USB instrument.

Leaving the block, including on an exception or Ctrl-C, closes the client, requests
owned-child shutdown by closing its parent-lifetime pipe, and waits for the child
to exit. Its supervised close attempts motor stop and USB close independently,
including after partial initialization. The same pipe's EOF requests shutdown if
the kernel exits. `pax.shutdown_evidence` records stop-command success, USB-close
success and child cleanup errors; this is not a measured motor-standstill signal.
The child log remains in `pax.daemon_log`. Temporary files are removed after exit.

Startup's polling budget defaults to 15 s and graceful shutdown's wait to 10 s;
both are configurable. In-progress RPC/USB calls can extend startup. A child that
fails to exit is killed/reaped and a cleanup error is raised; forced termination
does **not** establish that the motor stopped. Body and cleanup failures are both
preserved. Startup failures show daemon-log context and expose the captured log
as `exception.daemon_log`, even if the context never yielded. A hung USB driver,
unplugged device, OS failure or forced kill cannot be made physically safe by a
Python `finally`; actual shutdown and repeated sessions still require bench
verification. This initial supervised mode requires POSIX signal handling.

Keep repeated reads inside one block when they belong to one acquisition, so the
motor stays settled through that acquisition. A later block starts a new session
and incurs the settling wait again. Raw records and timing remain in Python after
the block, while the PAX connection is closed. Do not call `pax.connect()` after
the block to resume it; start a new `local_session()` instead.

## PAX: attach to an existing daemon

The original attach-only API remains available for tools with an independently
managed daemon. Its `close()` affects only its client socket:

```python
from instruments import PAX

if "pax" in globals():
    pax.close()
pax = PAX(host="localhost", port=38400)
pax.connect()
pax.read_status()
```

`connect()` opens a YAQC client and performs its handshake/identity exchange.
It does **not** stop/start a daemon, claim the USB device, configure the motor,
change measurement mode or set wavelength. The already-running daemon owns those
settings and must be configured/settled before acquisition. `read_status()` performs
daemon reads and returns identity, wavelength in nm and unmodified channel-unit
metadata; this is not proof of fresh measurements. If needed, explicitly call
`pax.set_wavelength(830.0)` and read the status back; 830 nm is an example, not an
assumed wavelength for every experiment.

Acquire one fresh power record separately:

```python
raw = pax.read_fresh()
raw["ptotal"]  # Instrument total power at the PAX input, watts by repo convention.
```

`read_raw()` triggers `measure()`, waits the explicit `measurement_wait_s` (default
0.06 s), then fetches `get_measured()`. Returned fields are copied without
normalization, clipping, angle-to-Stokes conversion or unit conversion. It does
not guarantee freshness. Host timing and the YAQC measurement ID are available in
`last_read_timing`; device timestamps remain in their native unspecified units.
The project daemon currently declares no `ptotal` unit: watts are the existing
repository convention, not a newly verified unit declaration.

`read_fresh(timeout_s=5.0)` uses the existing shared validity policy: finite,
initialized diagnostic values; positive power, timestamp, revisions and revolution
time; ADC bounds `0 <= adc_min < adc_max < 65520`; bounded angles; nonnegative DoP.
There is no low-DoP rejection, and small DoP excursions above one are retained.
Both timestamp and revisions must advance. The first call establishes advancement
with two valid records; subsequent calls compare against the last accepted record.
An advancing record does not establish integration wholly after the request or
synchronization with the RP.

Every attempted raw record and its host timing remains in `last_attempts`, including
invalid and duplicate records; copy/save these before the next `read_fresh()` call,
which replaces that list. `last_raw_record` retains the most recent successful
fetch. Stale/invalid-only acquisition raises `PAXNotReady`; RPC failures propagate
without wrapper-level retriggering. `timeout_s` is a polling budget: an in-progress
RPC or measurement wait may extend past it. `rpc_timeout_s` defaults to 2 s for
the initial YAQC socket. YAQC itself can reconnect/retry a call after connection
errors and uses its own defaults for that replacement socket; this wrapper does
not replace the driver's retry implementation. Do not treat these values as a
hard wall-clock acquisition deadline.

`pax.close()` closes only its client socket. It neither shuts down the shared
daemon nor stops its motor. The installed YAQC 2023.6.0 has no public client-close
method, so this adapter explicitly closes `client._socket._socket`; this private
dependency is isolated in `_close_yaqc_client` and needs checking when YAQC changes.
Cleanup failures propagate, and another connection cannot replace the live client
until close succeeds. Successful close is idempotent. Do not share an instance
between acquisition threads.

## Compatibility and validation

Legacy `RPController.connect_scope_only()` uses the same monitor transport and
write guard. Its active `connect()` and output methods remain unchanged. Passive
cleanup now attempts every restoration before reporting collected failures, and
failed/partial scope construction attempts restoration before closing transports.
Legacy `PAXController` keeps its existing daemon replacement/startup, wavelength,
retry, trigger/wait/fetch and freshness loops. It reuses shared parsing/validity
helpers and re-exports `PAXReading` and `PAXNotReady` at the original import path;
the reading's defining module is now `instruments.pax`. The `s1/s2/s3` legacy fields
remain angle-derived unit-direction coordinates, not measured absolute Stokes.
Run provenance now includes the shared instrument source. No scientific records,
notebook cells, recipes or historical formats are rewritten.

Run software checks using the isolated environment described in
[the offline guide](../../docs/engineering/offline-testing.md):

```bash
bash scripts/test-offline.sh instruments
bash scripts/test-offline.sh
```

These tests use fake drivers/processes and the existing network/process guard.
The owned-daemon launcher is also blocked by that guard. `tomli-w==1.2.0` is added
to the isolated software test requirements to exercise real temporary TOML config
serialization; it is already a dependency of the established bench environment.
Bench checks
remain necessary for repeated RP attachment/monitor-port release, saved-register
restoration and unchanged physical outputs; PAX fresh raw records, units and
actual device configuration; client-only close while the daemon/motor remain
running; supervised motor/USB/child shutdown on success, startup failure, Ctrl-C
and kernel exit; repeated owned-session/port reuse; and acquisition timing. No
hardware was operated to validate this change.
