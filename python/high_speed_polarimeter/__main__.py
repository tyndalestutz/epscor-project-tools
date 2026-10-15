"""Catalog, recipe validation, mock/bench acquisition and offline quality."""
import argparse
from dataclasses import replace
import json

from .catalog import EXPERIMENTS
from .config import Recipe, load_recipe
from .quality import reevaluate
from .runner import execute


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--list", action="store_true")
    action.add_argument("--dry-run", action="store_true")
    action.add_argument("--mock-run", action="store_true")
    action.add_argument("--run", action="store_true", help="Initialize bench hardware for the selected profile")
    action.add_argument("--reevaluate", metavar="RUN_DIRECTORY")
    parser.add_argument("--profile")
    parser.add_argument("--results-directory")
    parser.add_argument("--tool", choices=("pax-live",), help="Reuse an existing bench tool with high speed run storage")
    args = parser.parse_args(argv)
    if not any((args.list, args.dry_run, args.mock_run, args.run, args.reevaluate)):
        if args.profile or args.results_directory or args.tool:
            parser.error("profile/results-directory/tool require an explicit action")
        from .cli import HighSpeedMenu, LOCAL_SETTINGS
        try:
            HighSpeedMenu(state_path=LOCAL_SETTINGS).run()
        except (ValueError, OSError, TypeError) as exc:
            parser.error(str(exc))
        return 0
    if args.tool:
        if not (args.run or args.dry_run):
            parser.error("--tool requires --run or --dry-run")
        try:
            from .shared_tools import launch
            return launch(args.tool, profile=args.profile, results_directory=args.results_directory,
                          dry_run=args.dry_run)
        except (ValueError, OSError, TypeError, RuntimeError, ImportError) as exc:
            parser.error(str(exc))
    if (args.list or args.reevaluate) and (args.profile or args.results_directory):
        parser.error("profile/results-directory apply only to dry-run, mock-run or run")
    try:
        if args.list:
            for experiment in EXPERIMENTS:
                print(f"{experiment.key}: {experiment.description}")
            return 0
        if args.reevaluate:
            print(json.dumps(reevaluate(args.reevaluate).to_dict(), indent=2))
            return 0
        recipe = load_recipe(args.profile) if args.profile else Recipe()
        if args.results_directory:
            recipe = replace(recipe, config={**recipe.config, "results_directory": args.results_directory})
        recipe.validate()
        if args.dry_run:
            print(json.dumps(recipe.to_dict(), indent=2))
            return 0
        state = execute(recipe, mode="hardware" if args.run else "mock")
        print(f"Run folder: {state['directory']}")
        return 0 if state["status"] == "COMPLETED" and all(state[key] == "COMPLETED" for key in ("cleanup_status", "quality_evaluation_status", "report_status")) else 1
    except (ValueError, OSError, TypeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
