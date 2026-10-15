"""Launch selected existing bench tools with this suite's run storage."""
from dataclasses import replace
import json

from .config import DEFAULT_RESULTS

TOOLS = ("pax-live",)


def launch(key, *, profile=None, results_directory=None, dry_run=False):
    from polarization_locking.catalog import BY_KEY, default_options
    from polarization_locking.config import PolarizationLockConfig
    from polarization_locking.settings import load_recipe, recipe, validate

    if key not in TOOLS:
        raise ValueError("Unsupported shared tool")
    case = BY_KEY[key]
    config, options = PolarizationLockConfig(), default_options(case)
    if profile:
        loaded_case, config, options = load_recipe(profile)
        if loaded_case.key != key:
            raise ValueError("Shared tool does not match the polarization-locking recipe")
    # Old recipes may contain the locking suite's root. This launcher owns routing.
    config = replace(config, results_directory=results_directory or DEFAULT_RESULTS)
    validate(config, case, options)
    if dry_run:
        print(json.dumps(recipe(case, config, options), indent=2))
        return 0
    from polarization_locking.runner import execute
    state = execute(case, config, options)
    return 0 if state["status"] in {"completed", "discarded"} else 1
