#!/usr/bin/env bash
# Run from any directory. Optional arguments select application suites by name.
set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd -- "$repo_root"
python_bin=${PYTHON:-python}
suites=("$@")
if (( ${#suites[@]} == 0 )); then
    suites=(polarization_locking high_speed_polarimeter field_propogation)
fi
for suite in "${suites[@]}"; do
    case "$suite" in
        polarization_locking|high_speed_polarimeter|field_propogation) ;;
        *) echo "Unknown suite: $suite" >&2; exit 2 ;;
    esac
done

work_dir=$(mktemp -d "${TMPDIR:-/tmp}/epscor-offline.XXXXXXXX")
child_pid=
cleanup() {
    if [[ -n "$child_pid" ]]; then
        kill -TERM "$child_pid" 2>/dev/null || true
        wait "$child_pid" 2>/dev/null || true
    fi
    rm -rf -- "$work_dir"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$repo_root/scripts/offline:$repo_root/python"
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
export PYTEST_ADDOPTS= PYTEST_PLUGINS=
export QT_QPA_PLATFORM=offscreen QT_API=pyqt5 MPLBACKEND=Agg
export TMPDIR="$work_dir/tmp" MPLCONFIGDIR="$work_dir/matplotlib"
export XDG_CACHE_HOME="$work_dir/cache" XDG_RUNTIME_DIR="$work_dir/runtime"
mkdir -p -- "$TMPDIR" "$MPLCONFIGDIR" "$XDG_CACHE_HOME" "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"

# Fail visibly instead of silently skipping the existing TeX report regression.
if ! command -v pdflatex >/dev/null; then
    echo "pdflatex is required; see docs/engineering/offline-testing.md" >&2
    exit 2
fi
"$python_bin" - <<'PY'
import sys
import sitecustomize
assert sitecustomize.OFFLINE_GUARD_ACTIVE, 'Offline isolation guard did not load'
import pytest, numpy, matplotlib, qtpy
from PyQt5 import QtCore
print(f'Python {sys.version.split()[0]} ({sys.executable}); pytest {pytest.__version__}')
print(f'NumPy {numpy.__version__}; Matplotlib {matplotlib.__version__}; Qt {QtCore.QT_VERSION_STR}')
PY
echo "Revision: $(git rev-parse HEAD 2>/dev/null || echo unavailable)"
echo "Isolated outputs: $work_dir (removed on exit)"

status=0
results=()
run_suite() {
    local label=$1 path=$2 code=0
    echo "Running $label"
    "$python_bin" -m pytest -p no:cacheprovider "$path" --basetemp="$work_dir/$label" &
    child_pid=$!
    wait "$child_pid" || code=$?
    child_pid=
    case "$code" in
        0) results+=("$label: PASS") ;;
        130|143) exit "$code" ;;
        *) results+=("$label: FAIL (exit $code)"); status=1 ;;
    esac
}
run_suite offline_boundary tests/offline
for suite in "${suites[@]}"; do
    run_suite "$suite" "python/$suite/tests"
done
printf '\n%s\n' "${results[@]}"
exit "$status"
