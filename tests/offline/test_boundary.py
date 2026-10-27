"""Verify isolation in fresh Python children before running application suites."""
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


def child(script):
    result = subprocess.run([sys.executable, '-c', script], cwd=ROOT,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('module', ['pyrpl', 'yaqc', 'pyvisa', 'paramiko'])
def test_real_driver_import_is_blocked(module):
    child(f"""
try:
    __import__({module!r})
except ImportError as exc:
    assert 'offline test guard' in str(exc)
else:
    raise AssertionError('Instrument driver was imported')
""")


def test_network_and_process_access_is_blocked():
    child("""
import socket, sys
try:
    with socket.socket() as connection:
        connection.connect(('127.0.0.1', 9))
except RuntimeError as exc:
    assert 'offline test guard' in str(exc)
else:
    raise AssertionError('Network guard absent')
for event, args in [('socket.connect', (None, ('127.0.0.1', 9))),
                    ('socket.bind', (None, ('127.0.0.1', 9))),
                    ('os.kill', (0, 0)),
                    ('subprocess.Popen', (sys.executable, [sys.executable, 'pax1000_daemon.py'], None, None))]:
    try:
        sys.audit(event, *args)
    except RuntimeError as exc:
        assert 'offline test guard' in str(exc)
    else:
        raise AssertionError('Process guard absent')
try:
    sys.audit('subprocess.Popen', sys.executable, [sys.executable, '_pax_daemon.py'], None, None)
except RuntimeError as exc:
    assert 'offline test guard' in str(exc)
else:
    raise AssertionError('Owned PAX daemon launch guard absent')
""")


@pytest.mark.parametrize('script', [
    "from polarization_locking.cli import main; assert main(['--list']) == 0",
    "from high_speed_polarimeter.__main__ import main; assert main(['--dry-run', '--profile', 'python/high_speed_polarimeter/profiles/vpi-hysteresis-example.json']) == 0",
    "from high_speed_polarimeter.__main__ import main; assert main(['--dry-run', '--tool', 'pax-live']) == 0",
])
def test_preflight_does_not_import_session_or_drivers(script):
    child(script + "; import sys; assert not ({'numpy','pyrpl','yaqc','polarization_locking.app'} & set(sys.modules))")
