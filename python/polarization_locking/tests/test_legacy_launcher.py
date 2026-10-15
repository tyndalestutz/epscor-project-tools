"""Preserve the historical launcher and its hardware-free preflight paths."""
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('arguments', [
    ['--list'], ['--show', 'sweep'],
    ['--profile', 'profiles/visibility-in2-example.json', '--dry-run'],
])
def test_legacy_launcher_matches_current_preflight_without_drivers(arguments):
    package = Path(__file__).resolve().parents[1]
    outputs = []
    for name in ('lock.py', 'test-suite.py'):
        script = (
            'import runpy,sys; sys.argv=sys.argv[1:]; '
            '\ntry: runpy.run_path(sys.argv[0],run_name="__main__")'
            '\nexcept SystemExit as exc: assert exc.code == 0'
            '\nassert not ({"pyrpl","yaqc","numpy","polarization_locking.app"} & set(sys.modules))'
        )
        result = subprocess.run([sys.executable, '-c', script, name, *arguments],
                                cwd=package, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        outputs.append(result.stdout)
    assert outputs[0] == outputs[1]
