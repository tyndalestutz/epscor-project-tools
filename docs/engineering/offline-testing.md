# Offline regression testing

Run `bash scripts/test-offline.sh` from the repository root after activating a
software-only environment. The same command runs in
[GitHub Actions](../../.github/workflows/offline-tests.yml). It never installs
packages or changes bench configuration. The historical `lock.py` launcher was
intentionally retired; `python/polarization_locking/test-suite.py` remains supported.

## Set up a separate environment

The initial supported CI platform is Ubuntu 24.04, Python 3.10. Keep this
environment separate from `jl-env`; do not install instrument runtime requirements.
From a fresh checkout:

```bash
sudo apt-get update
sudo apt-get install --no-install-recommends -y libegl1 libgl1 texlive-latex-base texlive-latex-recommended texlive-latex-extra lmodern
python3.10 -m venv /tmp/epscor-offline-venv
source /tmp/epscor-offline-venv/bin/activate
python -m pip install -r requirements-offline.txt
bash scripts/test-offline.sh
```

Some Ubuntu installations also need their Python venv support package. Use an
available Python 3.10 installation; the workflow obtains it through setup-python.
The environment path is an example and must not overwrite an existing environment.

[requirements-offline.txt](../../requirements-offline.txt) pins pytest, NumPy,
Matplotlib, QtPy and PyQt5 plus their resolved Python dependencies. These are test
environment pins, not changes to bench/application requirements. SciPy, SymPy,
PyVista, Pyrpl, YAQC/YAQD, VISA and USB driver packages are not needed by the current
offline suites. Other optional research tools can require additional packages;
they are outside this command's coverage. Review dependency changes separately
from physical driver upgrades.

Qt's real widget/thread tests use PyQt5 with `QT_QPA_PLATFORM=offscreen` and
`QT_API=pyqt5`. Static plots use `MPLBACKEND=Agg`; this does not replace those Qt
interaction tests. The TeX report regression requires `pdflatex`, Latin Modern
fonts and the standard/extra LaTeX packages listed above. Missing `pdflatex` fails
preflight instead of allowing the existing conditional PDF test to skip silently.
Other missing TeX packages cause a visible report-test failure.

## What runs

| Suite | Existing coverage | Integrated baseline count |
| --- | --- | ---: |
| `instruments` | Driver-free imports, fake RP monitor/readback/restoration/failures, PAX client ownership/raw records/freshness and shared-source provenance | New increment; see actual run results |
| `polarization_locking` | Catalog/recipes, menu persistence, numerical/control helpers, mocked controller/lifecycle behavior, interruption, logging, real offscreen Qt, analysis and PDFs | 302 |
| `high_speed_polarimeter` | Mock lifecycle/EOM acquisition, retained buffers/events, mappings, quality/reporting, rough Vπ guards, shared tools and CLI subprocesses | 97 |
| `field_propogation` | Jones/Stokes propagation, physical conventions, measured-input/freeze/held-out workflows, spatial models, selected archived comparisons and TeX/PDF regeneration | 38 |

Counts are evidence from the reviewed baseline, not required totals. No existing
tests are removed or excluded. Eight additional checks in
[tests/offline](../../tests/offline) verify the offline boundary and driver-free
preflight in fresh Python children. All application suites run in separate pytest
processes using their existing configurations; field tests retain their unittest
semantics under pytest. Selected archived measurements are read-only regression
inputs for specific comparisons, not universal scientific golden references.

To run an individual suite with the same isolation and boundary checks:

```bash
bash scripts/test-offline.sh polarization_locking
bash scripts/test-offline.sh instruments
bash scripts/test-offline.sh high_speed_polarimeter
bash scripts/test-offline.sh field_propogation
# Select an interpreter without activating it:
PYTHON=/tmp/epscor-offline-venv/bin/python bash scripts/test-offline.sh
```

The runner sets `PYTHONDONTWRITEBYTECODE=1`, an explicit source/guard `PYTHONPATH`,
headless Qt/plot settings, and fresh temporary directories for pytest, Python
tempfiles, Matplotlib and Qt caches. It disables pytest cache writes and external
plugin autoloading. Inherited `PYTEST_ADDOPTS`/`PYTEST_PLUGINS` are cleared to avoid
unnoticed test selection or workstation plugins; the existing tests need only
pytest's built-in fixtures. It reports every suite and returns nonzero if any
suite fails, including collection/preflight failures. Ctrl+C/termination stops
the active pytest child and removes the runner's temporary directory. It does
not remove pre-existing files or touch experiment output directories.

## Hardware boundary and limits

The runner adds [scripts/offline](../../scripts/offline) to `PYTHONPATH`, activating
a test-only `sitecustomize` guard before collection and in inherited Python
subprocesses. The guard blocks real Pyrpl, YAQC/YAQD, VISA, USB and SSH driver
imports, Python socket creation/connections/binds, real process signals and
recognized PAX daemon launches. Tests still inject fake modules/controllers to
exercise the real dispatch, bounds and lifecycle code. Production adapters are
unchanged; tests are not patched to conceal failures.

This is defense in depth, **not an OS security sandbox**: native extensions,
external programs or deliberately bypassed Python startup are not comprehensively
contained. Use a software-only environment and an ordinary GitHub-hosted runner,
never a bench-connected self-hosted runner. Do not add credentials, device mounts,
live services or real acquisition to this workflow. Current suites require no
instrument-dependent test exclusions. Tests mentioning hardware mode inject
synthetic sessions or verify rejection before connection; daemon/signal tests
use fake processes. Mock signals are not experimental measurements.

## Read failures and add coverage

Expand the failing Actions step and find the suite name, pytest failure and
traceback. Reproduce it locally with the same dependency file and command. Fix
regressions; distinguish missing system dependencies and platform assumptions
before changing an expectation. Never change a failure into an unexplained skip
or xfail. The suite summary and process exit status propagate directly to the
GitHub check; no `continue-on-error` masks them. This PR does not configure branch
protection or make the check mandatory for merging.

Future tests should use `tmp_path`/`TemporaryDirectory`, injected clients and
deterministic records, and retain assertions about units, conventions, raw data,
failure/cleanup and provenance. Never write a report into a historical run during
a test. Keep test subprocesses under the inherited offline environment. If a
new dependency or tool is needed, justify it in the PR and update this inventory.

CI verifies software behavior covered by these tests. It does not establish
physical voltage routing, delivered voltage, FPGA state, RP/PAX synchronization,
integration windows, settling, noise, feedback performance, daemon/device recovery
or repeated bench-run stability. Those require separately authorized physical
protocols with recorded setup and acceptance criteria.
