# Recipes

| Example | Intended use |
| --- | --- |
| [sweep-example.json](sweep-example.json) | Bounded 0–0.1 V phi1 data collection; no Vπ fit |
| [visibility-in2-example.json](visibility-in2-example.json) | Passive IN2 contrast; 0.5 Hz example, unknown dark offset |

Minimal templates inherit unspecified defaults from the current code. Open one
in the menu, edit the settings and `bench_notes`, then save a named recipe:

```bash
python python/polarization_locking/lock.py --profile python/polarization_locking/profiles/visibility-in2-example.json
```

Saved menu recipes and each run's `recipe.json` contain the full configuration
and options. `schema_version` identifies their format. `--dry-run` validates and
prints the effective recipe without hardware access. Unknown fields, invalid
types, and unsupported values are rejected before connection.

Use `--profile path/to/recipe.json --run TEST` for explicit execution; the test
must match the recipe. Each execution creates a new run folder. Review
`results_directory` and configuration paths on another workstation: older full
recipes can contain absolute paths. Record actual placement, cabling, detector
settings, and calibration evidence as in the [operating procedure](../docs/operating-procedure.md).

`target_mode=current` captures a new target each run; `explicit` uses `target_u`
and `target_v` in radians. Captured values appear in `run.json`'s effective
configuration. Target acquisition uses the DOP gate; existing PI loops retain
their historical DOP logging behavior.

Temporary suite-local `results/` defaults are migrated on load. Legacy
`report=none` becomes `pdf`; `png` becomes `both`. Originals are not rewritten.
Keep private credentials in the local Pyrpl configuration, outside shared recipes.
