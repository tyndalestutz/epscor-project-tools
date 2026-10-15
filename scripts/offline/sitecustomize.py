"""Test-process guard, loaded only by the supported offline runner's PYTHONPATH.

Inherited by Python subprocesses. This is defense in depth, not an OS sandbox.
Existing tests can still inject fake modules/clients without importing drivers.
"""
import importlib.abc
import os
import sys

BLOCKED_DRIVERS = frozenset({'pyrpl', 'yaqc', 'yaqd', 'pyvisa', 'usb', 'paramiko'})


class OfflineDriverImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in BLOCKED_DRIVERS:
            raise ImportError('Instrument driver import blocked by offline test guard')


def offline_audit(event, args):
    if event in {'socket.__new__', 'socket.connect', 'socket.bind', 'os.kill', 'os.killpg'}:
        raise RuntimeError('Instrument/network/process access blocked by offline test guard')
    if event == 'subprocess.Popen':
        command = args[1]
        command = [command] if isinstance(command, (str, bytes)) else command
        names = {os.path.basename(os.fsdecode(part)) for part in command}
        if names & {'pax1000_daemon.py', 'yaqd-thorlabs-pax1000'}:
            raise RuntimeError('PAX daemon launch blocked by offline test guard')


sys.meta_path.insert(0, OfflineDriverImports())
sys.addaudithook(offline_audit)
OFFLINE_GUARD_ACTIVE = True
