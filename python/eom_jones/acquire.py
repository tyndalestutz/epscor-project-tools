"""Plumbing for the EOM polarization-transfer notebook (PAX at seats A and B, RP sine drive).

Raw records only: no averaging, fitting, lag correction or exclusion happens here.
Files are the state. Each stage writes one ``<stage>.npz`` + ``<stage>.json`` into its block folder,
atomically and incrementally, and refuses to run if its file exists or the previous stage's file is missing.
Hardware objects come in through factories (``pax_factory()``, ``drive_factory()``) so tests use fakes.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import time

import numpy as np

STAGES = {"eom": ("input_A", "baseline_B", "sweep_B", "baseline_B_post", "return_A"),
          "blank": ("input_A", "baseline_B")}
PAX_FIELDS = ("timestamp", "revisions", "theta", "eta", "dop", "ptotal", "adc_min", "adc_max", "rev_time")
TIMING_FIELDS = ("requested_monotonic_s", "received_monotonic_s")
SAVE_EVERY_S = 10.0
PAX_PORT = 38400


# ---------- files ----------

def anchor(clock=time):
    """One reading of each clock, so host monotonic times can be placed in UTC later."""
    return {"monotonic_s": clock.monotonic(), "unix_s": time.time(),
            "utc": datetime.now(timezone.utc).isoformat()}


def _write_json(path, record):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(record, indent=1, default=str))
    os.replace(tmp, path)


def _write_npz(path, arrays):
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as handle:
        np.savez_compressed(handle, **arrays)
    os.replace(tmp, path)


def _records_to_arrays(records, extra=()):
    """PAX raw records (+ host timing) -> float arrays, one entry per accepted read. No values changed."""
    fields = PAX_FIELDS + TIMING_FIELDS + ("measurement_id",) + tuple(extra)
    num = lambda v: np.nan if v is None else float(v)
    return {name: np.array([num(r.get(name)) for r in records], dtype=float) for name in fields}


def new_session(results_dir, eom_tag, settings, clock=time):
    """Create the session folder and session.json (settings and provenance as given by the notebook)."""
    tag = re.sub(r"[^A-Za-z0-9-]+", "-", eom_tag).strip("-")
    session = Path(results_dir) / f"jones_{tag}_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    session.mkdir(parents=True)
    (session / "blocks").mkdir()
    _write_json(session / "session.json", {"settings": settings, "created": anchor(clock)})
    return session


def provenance(repo_root, files):
    """Git revision/dirty state and sha256 + text of the named files (viewer script, PAX config, this module)."""
    git = lambda *a: subprocess.run(["git", "-C", str(repo_root), *a], capture_output=True, text=True).stdout.strip()
    out = {"git_commit": git("rev-parse", "HEAD"), "git_dirty": git("status", "--porcelain"), "files": {}}
    for f in files:
        data = Path(f).read_bytes()
        out["files"][str(f)] = {"sha256": hashlib.sha256(data).hexdigest(), "text": data.decode(errors="replace")}
    return out


def new_block(session, label, typed, kind="eom", clock=time):
    """Block folder NN_label with block.json (typed bench entries: PSG dials, TEC read, note)."""
    if kind not in STAGES:
        raise ValueError(f"kind must be one of {list(STAGES)}")
    if not str(label).strip():
        raise ValueError("Type the state label (e.g. 'H', 'D', 'R')")
    blocks = Path(session) / "blocks"
    existing = sorted(p for p in blocks.iterdir() if p.is_dir())
    if existing and {f.name for f in existing[-1].iterdir()} == {"block.json"}:
        (existing[-1] / "block.json").unlink()   # latest block never started a stage: replace it
        existing[-1].rmdir()
        existing.pop()
    number = 1 + max([int(p.name.split("_")[0]) for p in existing] or [0])
    block = blocks / f"{number:02d}_{re.sub(r'[^A-Za-z0-9-]+', '-', str(label)).strip('-')}"
    block.mkdir()
    _write_json(block / "block.json", {"label": label, "kind": kind, "typed": typed, "created": anchor(clock)})
    return block


def block_info(block):
    return json.loads((Path(block) / "block.json").read_text())


def _begin_stage(block, stage):
    """Enforce the stage order with files: refuse a repeat, refuse skipping the previous stage."""
    block = Path(block)
    order = STAGES[block_info(block)["kind"]]
    if stage not in order:
        raise ValueError(f"{stage!r} is not a stage of this block; stages are {order}")
    if (block / f"{stage}.json").exists():
        raise FileExistsError(f"{block.name}/{stage} already exists. Start a new block to redo it.")
    index = order.index(stage)
    if index and not (block / f"{order[index - 1]}.json").exists():
        raise RuntimeError(f"Run {order[index - 1]!r} for {block.name} before {stage!r}")
    return block / f"{stage}.npz", block / f"{stage}.json"


def stage_done(block, stage):
    """True if this block already has the stage (or verdict) file: lets a notebook cell skip an accidental rerun."""
    return (Path(block) / f"{stage}.json").exists()


def load_stage(block, stage):
    """(arrays, record) for one saved stage, read from disk."""
    block = Path(block)
    record = json.loads((block / f"{stage}.json").read_text())
    with np.load(block / f"{stage}.npz") as z:
        arrays = {k: z[k] for k in z.files}
    return arrays, record


# ---------- PAX ----------

def _read_loop(pax, duration_s, read_timeout_s, records, attempts, save, clock, tag=None):
    """read_fresh() until duration_s has passed. Every accepted record and every attempt is kept,
    including the attempts of a read that raised."""
    tag = tag or {}
    end, next_save = clock.monotonic() + duration_s, clock.monotonic() + SAVE_EVERY_S
    while clock.monotonic() < end:
        try:
            raw = pax.read_fresh(timeout_s=read_timeout_s)
        finally:
            attempts.extend(dict(a, **tag) for a in pax.last_attempts)
        records.append({**raw, **dict(pax.last_read_timing or {}), **tag})
        if clock.monotonic() >= next_save:
            save()
            next_save = clock.monotonic() + SAVE_EVERY_S


def _pax_start(pax, wavelength_nm):
    """Set the wavelength and require the readback to match (0.5 nm)."""
    pax.set_wavelength(wavelength_nm)
    status = pax.read_status()
    if abs(float(status["wavelength_nm"]) - wavelength_nm) > 0.5:
        raise RuntimeError(f"PAX wavelength reads {status['wavelength_nm']} nm, requested {wavelength_nm} nm")
    return {"status": status, "session_configuration": getattr(pax, "session_configuration", None)}


def _finish(record, device, error, save, n_records, files):
    """Record status, PAX shutdown evidence and daemon log; save without hiding the original error.

    A stage that failed before ANY record was taken leaves no stage files (so it can simply be rerun);
    the attempt is logged as one line in the session's failed_starts.log. Partial data is always kept.
    """
    if error is not None:
        record.update(status="interrupted" if isinstance(error, KeyboardInterrupt) else "failed",
                      error=f"{type(error).__name__}: {error}")
    if device is not None:
        record.update(pax_shutdown=getattr(device, "shutdown_evidence", None),
                      pax_daemon_log=getattr(device, "daemon_log", None))
    elif error is not None:
        record["pax_daemon_log"] = getattr(error, "daemon_log", None)
    try:
        if error is not None and n_records == 0:
            for f in files:
                Path(f).unlink(missing_ok=True)
            session = next(p for p in Path(files[0]).parents if (p / "session.json").exists())
            with open(session / "failed_starts.log", "a") as log:
                log.write(json.dumps({"file": str(Path(files[0]).relative_to(session)), "time": record["end"],
                                      "error": record["error"]}, default=str) + "\n")
            return
        save()
    except Exception as save_error:
        if error is None:
            raise
        print(f"WARNING: saving after the error failed too: {type(save_error).__name__}: {save_error}")


def capture(block, stage, *, pax_factory, duration_s=30.0, wavelength_nm=830.0, read_timeout_s=5.0, stop=None,
            clock=time):
    """Undriven PAX capture (input_A, baseline_B, baseline_B_post, return_A): raw records for duration_s."""
    npz, meta = _begin_stage(block, stage)
    clear_pax(Path(block).parent.parent, f"{Path(block).name}/{stage}", stop, clock)
    records, attempts = [], []
    record = {"stage": stage, "status": "running", "duration_s": duration_s, "wavelength_nm_requested": wavelength_nm,
              "start": anchor(clock), "meaning": "PAX raw records; s1-s3 not stored (derive from theta, eta)"}
    save = lambda: (_write_npz(npz, _records_to_arrays(records)),
                    _write_json(meta, dict(record, n_accepted=len(records), attempts=attempts)))
    save()
    device = error = None
    try:
        with pax_factory() as pax:
            device = pax
            record["pax"] = _pax_start(pax, wavelength_nm)
            _read_loop(pax, duration_s, read_timeout_s, records, attempts, save, clock)
        record["status"] = "completed"
    except BaseException as e:
        error = e
        raise
    finally:
        record["end"] = anchor(clock)
        _finish(record, device, error, save, len(records), (npz, meta))
    return npz


def ambient(session, *, pax_factory, duration_s=10.0, wavelength_nm=830.0, stop=None, clock=time):
    """Beam blocked: read_raw() loop (read_fresh may refuse zero power). Session-level file ambient.npz/json."""
    npz, meta = Path(session) / "ambient.npz", Path(session) / "ambient.json"
    if meta.exists():
        raise FileExistsError("ambient already recorded for this session")
    clear_pax(session, "ambient", stop, clock)
    records = []
    record = {"stage": "ambient", "status": "running", "start": anchor(clock),
              "meaning": "beam blocked; read_raw records, may repeat (not freshness-checked)"}
    save = lambda: (_write_npz(npz, _records_to_arrays(records)), _write_json(meta, dict(record, n_records=len(records))))
    device = error = None
    try:
        with pax_factory() as pax:
            device = pax
            record["pax"] = _pax_start(pax, wavelength_nm)
            end = clock.monotonic() + duration_s
            while clock.monotonic() < end:
                raw = pax.read_raw()
                records.append({**raw, **dict(pax.last_read_timing or {})})
        record["status"] = "completed"
    except BaseException as e:
        error = e
        raise
    finally:
        record["end"] = anchor(clock)
        _finish(record, device, error, save, len(records), (npz, meta))
    return npz


# ---------- RP sine sweep with the PAX reading ----------

def sweep(block, *, pax_factory, drive_factory, confirm_hv_on, output, offset_v, peak_v, segments, tee=None,
          wavelength_nm=830.0, read_timeout_s=5.0, stop=None, clock=time):
    """sweep_B: PAX session up -> RP drive session (zero/off confirmed on connect) -> sine on `output`.

    segments: [{"period_s": 10.0, "cycles": 20}, ...]; later segments change only the frequency
    (set_frequency; the sine's phase after the change is not known to the host).
    confirm_hv_on() is called after the RP has connected with outputs confirmed zero/off and before any drive;
    the HV driver is switched on then. Anything but True stops the sweep with nothing driven.
    tee: None or {"decimation": 65536, "full_scale_v": {"in1": 1., "in2": 1.}}: one RP buffer of the drive
    before and after each segment's PAX reads. Its host-time precision is not characterised (the receive time is
    taken after the buffer download), so it is a phase anchor of unknown accuracy, not a verified one.
    The drive session's exit returns both outputs to zero/off.
    """
    npz, meta = _begin_stage(block, "sweep_B")
    clear_pax(Path(block).parent.parent, f"{Path(block).name}/sweep_B", stop, clock)
    records, attempts, tee_raw = [], [], {}
    record = {"stage": "sweep_B", "status": "running", "output": output, "offset_v": offset_v, "peak_v": peak_v,
              "segments": [dict(s) for s in segments], "tee": tee, "wavelength_nm_requested": wavelength_nm,
              "start": anchor(clock), "commands": [], "tee_captures": [],
              "meaning": "PAX raw records with host timing; commands are ASG settings/readbacks, "
                         "not measured delivered voltage"}
    save = lambda: (_write_npz(npz, dict(_records_to_arrays(records, extra=("segment",)), **tee_raw)),
                    _write_json(meta, dict(record, n_accepted=len(records), attempts=attempts)))
    save()
    device = drive = error = None

    def tee_capture(name):
        cap = drive.capture(decimation=tee["decimation"], full_scale_v=tee["full_scale_v"])
        tee_raw[name] = cap["raw_counts"]
        record["tee_captures"].append({"name": name, "metadata": cap["metadata"]})
        save()

    try:
        with pax_factory() as pax:
            device = pax
            record["pax"] = _pax_start(pax, wavelength_nm)
            drive = drive_factory()
            with drive:
                record["drive_connection"] = drive.connection_evidence
                if confirm_hv_on() is not True:
                    raise RuntimeError("HV driver ON not confirmed; nothing was driven")
                record["hv_on_confirmed"] = anchor(clock)
                for i, segment in enumerate(segments):
                    frequency = 1.0 / segment["period_s"]
                    if i == 0:
                        drive.set_waveform(output, waveform="sin", offset_v=offset_v, peak_v=peak_v,
                                           frequency_hz=frequency)
                    else:
                        drive.set_frequency(output, frequency_hz=frequency)
                    command = drive.last_command
                    record["commands"].append(dict(command, segment=i))
                    save()
                    if tee:
                        tee_capture(f"tee_seg{i}_pre")
                    actual_hz = command["actual"]["frequency_hz"]
                    _read_loop(pax, segment["cycles"] / actual_hz, read_timeout_s, records, attempts, save, clock,
                               tag={"segment": i})
                    if tee:
                        tee_capture(f"tee_seg{i}_post")
        record["status"] = "completed"
    except BaseException as e:
        error = e
        raise
    finally:
        record["end"] = anchor(clock)
        if drive is not None:
            record.update(drive_cleanup=drive.cleanup_evidence, drive_last_command=drive.last_command)
        _finish(record, device, error, save, len(records), (npz, meta))
    return npz


# ---------- viewer, verdict, ledger ----------

def port_in_use(port, host="127.0.0.1"):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def launch_viewer(session, script, python, *, popen=subprocess.Popen, stop=None, clock=time):
    """Run the PyVista live viewer until its window is closed; log its output in the session folder.

    Every other PAX daemon is stopped first (the viewer starts its own). If the viewer exits and leaves its
    daemon running (e.g. it crashed), that daemon is stopped too and the event is logged.
    """
    session = Path(session)
    clear_pax(session, "before viewer", stop, clock)
    n = len(list(session.glob("viewer_*.json"))) + 1
    log = session / f"viewer_{n:02d}.log"
    record = {"script": str(script), "python": str(python), "start": anchor(clock), "returncode": None,
              "measurement_mode": "upstream yaqd-thorlabs-pax1000 daemon, SENS:CALC 9 (hard-coded upstream)"}
    try:
        with open(log, "w") as handle:
            # The RP drive session sets QT_QPA_PLATFORM=offscreen in this kernel; a viewer inheriting it dies with
            # an X BadWindow error before its window opens. Give the viewer the normal desktop platform.
            env = {k: v for k, v in os.environ.items() if k != "QT_QPA_PLATFORM"}
            record["removed_env"] = {"QT_QPA_PLATFORM": os.environ.get("QT_QPA_PLATFORM")}
            process = popen([str(python), str(script)], stdout=handle, stderr=subprocess.STDOUT, env=env)
            try:
                record["returncode"] = process.wait()
            except KeyboardInterrupt:
                process.send_signal(signal.SIGINT)   # the viewer's own shutdown stops its daemon
                record["returncode"] = process.wait()
                record["interrupted"] = True
                raise
    finally:
        record["end"] = anchor(clock)
        record["daemons_left_running"] = clear_pax(session, "after viewer", stop, clock)
        _write_json(session / f"viewer_{n:02d}.json", record)
    return record


def verdict(block, decision, note, clock=time):
    """Operator verdict: 'accept' or 'redo' with a note. Written once; the block's files are never deleted."""
    block = Path(block)
    if decision not in ("accept", "redo"):
        raise ValueError("decision must be 'accept' or 'redo'")
    if (block / "verdict.json").exists():
        raise FileExistsError(f"{block.name} already has a verdict")
    if not (block / "input_A.json").exists():
        raise RuntimeError("Nothing captured in this block yet")
    _write_json(block / "verdict.json", {"decision": decision, "note": note, "time": anchor(clock)})


def ledger(session):
    """One row per block: label, kind, stage statuses, verdict."""
    rows = []
    for block in sorted(p for p in (Path(session) / "blocks").iterdir() if p.is_dir()):
        info = block_info(block)
        statuses = {s: json.loads((block / f"{s}.json").read_text())["status"]
                    for s in STAGES[info["kind"]] if (block / f"{s}.json").exists()}
        v = block / "verdict.json"
        rows.append({"block": block.name, "label": info["label"], "kind": info["kind"], "stages": statuses,
                     "verdict": json.loads(v.read_text())["decision"] if v.exists() else None})
    return rows


def stop_pax_daemons(timeout_s=10.0, clock=time):
    """Stop every yaqd-thorlabs-pax1000 / project PAX daemon of this user.

    SIGTERM first (a yaqd daemon then runs its close(): motor stop, USB close); SIGKILL if it has not exited
    after timeout_s. Returns [{pid, command, how}].
    """
    daemon_names = {"yaqd-thorlabs-pax1000", "_pax_daemon.py", "pax1000_daemon.py"}
    found = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            argv = (proc / "cmdline").read_bytes().split(b"\0")[:2]
        except OSError:
            continue
        # Match the program itself (argv[0], or the script run by python as argv[1]), never other arguments.
        if any(Path(a.decode(errors="replace")).name in daemon_names for a in argv if a):
            found.append((int(proc.name), " ".join(a.decode(errors="replace") for a in argv)))
    for pid, _ in found:
        os.kill(pid, signal.SIGTERM)
    deadline = clock.monotonic() + timeout_s
    alive = lambda pid: os.path.exists(f"/proc/{pid}")
    while any(alive(pid) for pid, _ in found) and clock.monotonic() < deadline:
        clock.sleep(0.25)
    result = []
    for pid, command in found:
        how = "SIGTERM (daemon close: motor stop, USB close)"
        if alive(pid):
            os.kill(pid, signal.SIGKILL)
            how = "SIGKILL after SIGTERM timeout: motor stop NOT confirmed"
        result.append({"pid": pid, "command": command, "how": how})
    end = clock.monotonic() + 5.0
    while port_in_use(PAX_PORT) and clock.monotonic() < end:
        clock.sleep(0.25)
    return result


def clear_pax(session, reason, stop=None, clock=time):
    """Scientist's rule (2026-11-14): whoever is about to use the PAX gets it; every other PAX daemon is stopped.

    Logged in the session's pax_cleanup.log. Raises if the port is still busy afterwards.
    """
    stopped = (stop or stop_pax_daemons)()
    if stopped:
        with open(Path(session) / "pax_cleanup.log", "a") as log:
            log.write(json.dumps({"time": anchor(clock), "reason": reason, "stopped": stopped}, default=str) + "\n")
        for s in stopped:
            print(f"Stopped PAX daemon pid {s['pid']} ({s['how']})")
    if (stop is None) and port_in_use(PAX_PORT):
        raise RuntimeError(f"Port {PAX_PORT} is still busy after stopping every PAX daemon; something else holds it")
    return stopped
