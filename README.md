# EPSCoR project tools

Laboratory software for polarization/interferometry acquisition, control,
analysis and optical modelling. Software regressions can run without instruments;
physical measurement validity requires a recorded, verified bench setup.

## Find the right subsystem

| Subsystem | Responsibility and starting points |
| --- | --- |
| [Polarization locking](python/polarization_locking/README.md) | Bench diagnostics, feedback, PAX monitoring and reusable recipes; [operating procedure](python/polarization_locking/docs/operating-procedure.md), [profiles](python/polarization_locking/profiles), [offline analysis](python/polarization_locking/analysis/README.md) |
| [High-speed polarimeter](python/high_speed_polarimeter/README.md) | Mock and hardware EOM acquisition, retained raw buffers/events and rough PAX-only Vπ characterization; [profiles](python/high_speed_polarimeter/profiles). Full instrument calibration and Stokes reconstruction remain future work. |
| [Jones-field models](python/field_propogation/README.md) | Independent propagation/detector models and measured-input workflows; [measurement protocol](python/field_propogation/docs/measurement_acquisition.md), [historical fit audit](python/field_propogation/docs/historical_fit_audit.md) |
| [Polarization visualization](python/polarization_visualization/README.md) | Visualization and associated experiment tools |

The package spelling `field_propogation` is intentional for compatibility.
The supported bench launcher is `python/polarization_locking/test-suite.py`;
the former `lock.py` command is retired.

## Software-only setup and validation

Use a separate development environment. The [offline testing guide](docs/engineering/offline-testing.md)
provides the supported Ubuntu/Python setup, system report dependencies and pinned
[requirements-offline.txt](requirements-offline.txt). After following that setup:

```bash
source /tmp/epscor-offline-venv/bin/activate
bash scripts/test-offline.sh
```

This runs the same hardware-free suites as GitHub Actions, with temporary outputs,
offscreen Qt and driver/network guards. See the guide for individual suites and
interpreter selection. It does not install into or replace the laboratory `jl-env`.
Do not install instrument runtime dependencies or upgrade bench drivers to run tests.

Safe starting points include the unified tests and documented list/dry-run
interfaces, which inspect configuration without acquisition:

```bash
python python/polarization_locking/test-suite.py --list
PYTHONPATH=python python -m high_speed_polarimeter --dry-run --profile python/high_speed_polarimeter/profiles/vpi-hysteresis-example.json
```

Mocks, numerical analysis and offline reports use software inputs and supported
optional dependencies; a mock trace is not measured evidence. Check each tool's
instructions and choose a new output location rather than overwriting a run.
Interactive acquisition menus, hardware-mode execution and instrument utilities
belong in a verified laboratory environment: they may connect to RP/PAX, launch
services or command actuator outputs. Follow the operating procedure, confirm
routing, limits, ownership and cleanup, and obtain bench authorization. Passing CI
does not establish delivered voltage, timing, calibration or feedback performance.

## Records and reproducibility

[Experiments](experiments/README.md) indexes historical and current records.
Locking runs use dated folders containing resolved recipes, `run.json`, console
logs, measurements and optional reports. High-speed runs additionally retain raw
buffers, acquisition events and source provenance. Field workflows distinguish
measured inputs, frozen parameters, predictions and held-out comparisons.
Preserve failures and original metadata; reporting success is separate from
physical validity. `data/` holds supporting historical data, `notebooks/`
exploration, `reports/` supporting documents and `papers/` literature/proposals.

The [inventory](docs/engineering/data-inventory.md) records actual tracked/local
sizes and existing regression inputs. The [retention policy](docs/engineering/data-retention.md)
defines Git versus future backed-up scientific storage, manifests, disclosure and
recovery. No archive or historical migration is configured. Future local records
may explicitly use ignored `experiments/_local/`; this is optional, needs backups
and changes no output defaults. Keep important source and reusable recipes in Git.

## Add an experiment safely

Read [AGENTS.md](AGENTS.md), the [campaign roadmap](docs/engineering/refactor-roadmap.md)
and the subsystem's instructions first. Follow the locking
[maintenance guide](python/polarization_locking/docs/maintaining.md) and analogous
catalog/recipe/runner implementations. Keep reusable implementation under `python/`,
record resolved conditions and provenance, retain raw/partial results and preserve
cleanup behavior. Add deterministic software coverage with temporary outputs and
run the unified tests. Document remaining bench checks separately; changes to
hardware semantics, acquisition timing or scientific interpretation need explicit
review and a physical validation protocol. Work within one bounded PR.
