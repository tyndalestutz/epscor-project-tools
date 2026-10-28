"""Monitor-only RP access using the repository's established Pyrpl transport.

No FPGA loading, output initialization, or experiment configuration. Scope access
still changes shared scope registers; coordinate with other scope users.
"""
from datetime import datetime, timezone
from pathlib import Path
import time
import secrets
from types import SimpleNamespace


_SAVED_REGISTERS = (0x40100004, 0x40100010, 0x40100014, 0x40100028,
                    0x40380000, 0x40390000)


class _ScopeClient:
    """Limit a borrowed monitor connection to scope and input-mux writes."""

    def __init__(self, client):
        self.client = client

    def reads(self, address, length):
        result = self.client.reads(address, length)
        if result is None or len(result) != length:
            raise RuntimeError("RP scope read failed")
        return result

    def writes(self, address, values):
        # ASG input muxes share module IDs with outputs. Never allow +4 routing.
        for offset in range(len(values)):
            register = address + 4 * offset
            if not (0x40100000 <= register <= 0x40100030 or
                    register in (0x40380000, 0x40390000)):
                raise RuntimeError(f"Scope session refused register write {register:#x}")
        if not self.client.writes(address, values):
            raise RuntimeError("RP scope write failed")


class _ScopeSession:
    """Minimal Pyrpl scope parent; no output modules or persisted settings."""

    _autosave_active = False
    frequency_correction = 1.0  # Existing monitor assumption, not a measured clock.


class RPCleanupError(RuntimeError):
    """All restoration/close operations were attempted; individual errors remain."""

    def __init__(self, errors):
        self.errors = tuple(errors)
        super().__init__("RP cleanup failed: " + "; ".join(
            f"{operation}: {error}" for operation, error in errors))


def _close_scope_monitor(p, ssh):
    errors = []
    if p is not None:
        session = p.rp
        for address, values in session.previous.items():
            try:
                session.client.writes(address, values)
            except BaseException as error:
                errors.append((f"restore {address:#x}", error))
        try:
            session.client.client.close()
        except BaseException as error:
            errors.append(("monitor close", error))
        # Installed MonitorClient.close() suppresses socket errors and can skip
        # its socket.close() when sending the close header fails.
        raw_socket = getattr(session.client.client, "socket", None)
        if raw_socket is not None:
            try:
                raw_socket.close()
            except BaseException as error:
                errors.append(("monitor socket close", error))
    if ssh is not None:
        try:
            ssh.close()
        except BaseException as error:
            errors.append(("SSH close", error))
    if errors:
        failure = RPCleanupError(errors)
        for _, error in errors:
            if not isinstance(error, Exception):
                raise error from failure
        raise failure from errors[0][1]


def _open_scope_monitor(host, profile, port, *, allow_port_fallback=False):
    """Shared implementation for notebooks and legacy scope-only connections."""
    import os
    import shlex
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from pyrpl.hardware_modules.scope import Scope
        from pyrpl.memory import MemoryTree
        from pyrpl.redpitaya_client import MonitorClient
        from pyrpl.directories import user_config_dir
        import paramiko
        import yaml
    except ImportError as error:
        raise RuntimeError("Pyrpl and its SSH/YAML dependencies are required") from error

    def unavailable():
        raise RuntimeError("Cannot attach to scope monitor; no FPGA reload or output initialization attempted")

    profile_path = Path(profile).expanduser()
    if not profile_path.is_file():
        profile_path = Path(user_config_dir) / f"{profile}.yml"
    # Read connection credentials only, never persisted module settings.
    connection = yaml.safe_load(profile_path.read_text()).get("redpitaya", {})
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    p = None
    try:
        ssh.connect(host, port=connection.get("sshport", 22),
                    username=connection.get("user", "root"), password=connection.get("password"),
                    timeout=5, banner_timeout=5, auth_timeout=5)
        _, stdout, _ = ssh.exec_command("cat /tmp/loaded_fpga.inf", timeout=5)
        if "pyrpl" not in stdout.read().decode().lower():
            raise RuntimeError("RP is not marked as running the Pyrpl FPGA; refusing to reload it during passive measurement")
        executable = str(Path(connection.get("serverdirname", "/opt/pyrpl")) /
                         connection.get("monitor_server_name", "monitor_server"))
        selected_port = port
        tried = {port}
        attempts = 4 if allow_port_fallback else 1
        for attempt in range(attempts):
            _, stdout, stderr = ssh.exec_command(f"{shlex.quote(executable)} {selected_port}", timeout=5)
            time.sleep(0.2)
            if not stdout.channel.exit_status_ready():
                break
            message = stderr.read().decode().strip()
            if "address already in use" not in message.lower() or attempt + 1 == attempts:
                raise RuntimeError(f"Scope monitor did not start on port {selected_port}: {message}")
            # Launch our own monitor elsewhere. Never connect to or kill the
            # listener that already owns the requested port.
            selected_port = 20000 + secrets.randbelow(30000)
            while selected_port in tried:
                selected_port = 20000 + (selected_port - 20000 + 1) % 30000
            tried.add(selected_port)
        client = MonitorClient(host, selected_port, restartserver=unavailable)
        session = _ScopeSession()
        session.client = _ScopeClient(client)
        session.previous = {}
        session.monitor_port = selected_port
        p = SimpleNamespace(rp=session)
        # Capture incrementally so a partial failure can restore captured values.
        for address in _SAVED_REGISTERS:
            session.previous[address] = session.client.reads(address, 1).copy()
        session.c = MemoryTree()
        session.parent = session
        session.scope = Scope(session, "scope")
    except BaseException as error:
        try:
            _close_scope_monitor(p, ssh)
        except Exception as cleanup_error:
            raise error from cleanup_error
        raise
    return p, ssh


class RedPitaya:
    """A monitor connection. Construction does nothing to the instrument.

    ``attach_monitor`` starts a separate monitor server and constructs a Pyrpl
    Scope, saving the same six registers as the legacy monitor. It never loads
    an FPGA or initializes outputs. ``close`` restores those saved registers;
    it does not establish a zero-output state or undo arbitrary scope writes.
    """

    def __init__(self, host, *, profile="scope_config", monitor_port=None):
        if not host or not profile:
            raise ValueError("Specify an RP host and a Pyrpl profile")
        if monitor_port is not None and (type(monitor_port) is not int or not 1 <= monitor_port <= 65535):
            raise ValueError("monitor_port must be an integer in 1..65535")
        self.host, self.profile = host, profile
        self._auto_monitor_port = monitor_port is None
        self._preferred_monitor_port = 2223 if monitor_port is None else monitor_port
        self.monitor_port = self._preferred_monitor_port
        self._p = self._ssh = None

    @staticmethod
    def drive_session(host, *, profile="scope_config", ssh_host_key_sha256,
                      command_limits_v, frequency_limits_hz, max_peak_v,
                      frequency_correction=1.0):
        """Explicit active session: initializes outputs at zero/off, then owns them.

        The returned context manager supports set_waveform() and capture(). Exit
        attempts zero/off on both outputs and releases its transports. This can
        initialize the FPGA/primary Pyrpl monitor; exclusive RP access is required.
        The caller must independently validate the downstream amplifier/EOM chain.
        """
        from ._rp_drive import RPDrive
        return RPDrive(host, profile, ssh_host_key_sha256, command_limits_v,
                       frequency_limits_hz, max_peak_v, frequency_correction)

    def attach_monitor(self):
        if self._p is not None:
            raise RuntimeError("RP monitor already attached; close it before attaching again")
        self._p, self._ssh = _open_scope_monitor(self.host, self.profile, self._preferred_monitor_port,
                                               allow_port_fallback=self._auto_monitor_port)
        self.monitor_port = self._p.rp.monitor_port
        return self

    @property
    def scope(self):
        """Live Pyrpl Scope; configuration/capture is an explicit caller action."""
        if self._p is None:
            raise RuntimeError("Attach the RP monitor first")
        return self._p.rp.scope

    def read_status(self):
        """Read current FPGA registers. This is not fresh ADC or output evidence."""
        if self._p is None:
            raise RuntimeError("Attach the RP monitor first")
        requested = time.monotonic()
        registers = {f"{address:#010x}": int(self._p.rp.client.reads(address, 1)[0])
                     for address in _SAVED_REGISTERS}
        return {"host": self.host, "mode": "monitor_only", "monitor_port": self.monitor_port,
                "preferred_monitor_port": self._preferred_monitor_port,
                "scope_registers": registers,
                "requested_monotonic_s": requested, "received_monotonic_s": time.monotonic(),
                "received_utc": datetime.now(timezone.utc).isoformat()}

    def close(self):
        """Restore saved registers and close owned transports; repeatable on success.

        Cleanup errors propagate, and references remain available for inspection
        or another cleanup attempt. A failed cleanup prevents a new attachment.
        """
        _close_scope_monitor(self._p, self._ssh)
        self._p = self._ssh = None
