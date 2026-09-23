"""Numbered bench menu. Browsing and editing only require the standard library."""
import argparse
from copy import deepcopy
import json
from pathlib import Path

from .catalog import BY_KEY, TESTS, default_options
from .config import PolarizationLockConfig
from .settings import TYPES, load_recipe, parse_value, recipe, validate, write_json

PROFILES = Path(__file__).resolve().parent / "profiles"
BACK = {"b", "back"}
QUIT = {"q", "quit", "exit"}


class DiagnosticsMenu:
    def __init__(self, config=None, *, input_fn=input, output=print, run_test=None):
        self.base_config = config or PolarizationLockConfig()
        self.input, self.print = input_fn, output
        self.sessions = {}
        self.run_test = run_test

    def ask(self, prompt):
        return self.input(prompt).strip()

    def settings(self, case):
        return self.sessions.setdefault(case.key, (deepcopy(self.base_config), default_options(case)))

    def show(self, case, config, options, *, all_fields=False):
        self.print(f"\n{case.title} [{case.key}]\n{case.description}\nRequired setup: {case.setup}")
        if not case.scope_only:
            self.print("Voltages ending in rp_v/rp_voltage are RP commands (0-1 V). V_lambda is terminal voltage for 2pi; V_pi is half of V_lambda. Historical gains are editable candidates.")
        entries = [("option", key, value) for key, value in options.items()]
        names = list(TYPES) if all_fields else case.config_names()
        if case.key == "pd-visibility":
            self.print(f"Detector: {config.visibility_source.upper()} | Mode: {config.visibility_mode.upper()} (c to change)")
            names = [name for name in names if name not in {"visibility_source", "visibility_mode"}]
            if not all_fields:
                drive_names = {"visibility_axis", "visibility_waveform", "visibility_amplitude_v", "visibility_offset_v", "phase_output_map_confirmed", "rp_output_max_voltage"}
                if config.visibility_mode == "passive":
                    names = [name for name in names if name not in drive_names]
                if config.visibility_source == "pd":
                    names = [name for name in names if not name.startswith("pax_") and name not in {"bench_pax_location", "visibility_pax_sample_period_s"}]
                elif config.visibility_source == "pax":
                    names = [name for name in names if name not in {"pd_input", "visibility_dark_voltage_v", "rp_scope_port"}]
                    if config.visibility_mode == "passive":
                        names = [name for name in names if name not in {"rp_hostname", "rp_config"}]
        entries += [("config", key, getattr(config, key)) for key in names]
        hints = {
            "axis": "phi1 = OUT1; phi2 = OUT2",
            "duration_s": "seconds; calibration/settling adds time",
            "report": "pdf/both" if case.report.startswith("analyze_") or case.report == "power" else "pdf",
            "target_mode": "current = capture each run; explicit = target_u/target_v",
            "visibility_axis": "phi1 = OUT1; phi2 = OUT2",
            "visibility_waveform": "sin, cos, triangle, sawtooth, square",
            "visibility_amplitude_v": "peak amplitude; output spans offset +/- amplitude",
        }
        for index, (_, key, value) in enumerate(entries, 1):
            hint = f"  ({hints[key]})" if key in hints else ""
            self.print(f"  {index:2}. {key} = {json.dumps(value)}{hint}")
        self.print("Shared acquisition settings are included above. 'all' exposes every configuration field.")
        return entries

    def edit(self, case, config, options, *, all_fields=False):
        while True:
            entries = self.show(case, config, options, all_fields=all_fields)
            try:
                selection = self.ask("Parameter number/name; Enter or b = back: ")
                if not selection or selection.lower() in BACK:
                    return
                if selection.lower() in QUIT:
                    raise EOFError
                if selection.isdigit() and 1 <= int(selection) <= len(entries):
                    scope, key, current = entries[int(selection)-1]
                else:
                    match = next((item for item in entries if item[1] == selection), None)
                    if match is None:
                        raise ValueError("Choose a displayed parameter number or name")
                    scope, key, current = match
                text = self.ask(f"{key} [{json.dumps(current)}]; Enter = keep, b = cancel: ")
                if not text or text.lower() in BACK:
                    continue
                kind = TYPES[key] if scope == "config" else type(default_options(case)[key])
                value = parse_value(text, kind)
                if scope == "config":
                    setattr(config, key, value)
                else:
                    options[key] = value
                self.print("Updated for this test. Values are checked together before save/run.")
            except KeyboardInterrupt:
                self.print("Edit cancelled.")
                return
            except ValueError as exc:
                self.print(f"Invalid value: {exc}")

    def save(self, case, config, options):
        validate(config, case, options)
        default = PROFILES / f"{case.key}.json"
        text = self.ask(f"Save recipe [{default}]; b = cancel: ")
        if text.lower() in BACK:
            return
        write_json(text or default, recipe(case, config, options))
        self.print(f"Recipe saved: {text or default}")

    def load(self):
        text = self.ask("Recipe JSON path; Enter or b = cancel: ")
        if not text or text.lower() in BACK:
            return None
        case, config, options = load_recipe(text)
        self.sessions[case.key] = (config, options)
        return case

    def choose_contrast(self, config):
        """Two short selections outside the parameter table; no hardware access."""
        chosen = []
        for title, choices, current in (
            ("Detector", ("pd", "pax", "both"), config.visibility_source),
            ("Mode", ("passive", "active"), config.visibility_mode),
        ):
            while True:
                value = self.ask(f"{title}: {' / '.join(choices)} [{current}]; b = back: ").lower()
                if value in BACK:
                    return False
                if value in QUIT:
                    raise EOFError
                if not value or value in choices:
                    chosen.append(value or current)
                    break
                self.print(f"Choose {' / '.join(choices)}.")
        config.visibility_source, config.visibility_mode = chosen
        return True

    def detail(self, case):
        if case.key == "pd-visibility":
            self.print(f"\n{case.title}\nRequired setup: {case.setup}")
            if not self.choose_contrast(self.settings(case)[0]):
                return
        while True:
            config, options = self.settings(case)
            self.show(case, config, options)
            try:
                selection = "c detector/mode | " if case.key == "pd-visibility" else ""
                action = self.ask(selection + "r run | e edit | all edit all | s save | l load | d defaults | b back | q quit: ").lower()
                if not action or action in BACK:
                    return
                if action in QUIT:
                    raise EOFError
                if action == "c" and case.key == "pd-visibility":
                    self.choose_contrast(config)
                elif action in {"e", "edit", "all"}:
                    self.edit(case, config, options, all_fields=action == "all")
                elif action in {"s", "save"}:
                    self.save(case, config, options)
                elif action in {"l", "load"}:
                    case = self.load() or case
                elif action in {"d", "defaults"}:
                    self.sessions.pop(case.key, None)
                elif action in {"r", "run"}:
                    validate(config, case, options)
                    if self.run_test is None:
                        from .runner import execute
                        self.run_test = execute
                    self.print("Starting hardware run. Ctrl+C stops acquisition; b cancels a setup prompt.")
                    state = self.run_test(case, deepcopy(config), dict(options))
                    self.print(f"Run status: {state['status']}")
                    if state.get("report_status"):
                        self.print(f"PDF report: {state['report_status']}")
                    if state["status"] == "cleanup_failed":
                        raise EOFError
                else:
                    self.print("Choose one of the displayed actions.")
            except KeyboardInterrupt:
                self.print("Cancelled; returning to test list.")
                return
            except (ValueError, OSError, RuntimeError, ImportError) as exc:
                self.print(f"Cannot continue: {exc}")

    def run(self, selected=None):
        self.print("Polarization diagnostics — browse and edit offline; only Run connects instruments.")
        try:
            if selected:
                self.detail(selected)
            while True:
                self.print("\nAvailable bench tests:")
                for index, case in enumerate(TESTS, 1):
                    self.print(f"  {index:2}. {case.title} [{case.key}]")
                try:
                    choice = self.ask("Test number/name | l load recipe | q quit: ")
                    if choice.lower() in QUIT:
                        return
                    if choice.lower() in {"l", "load"}:
                        case = self.load()
                    elif choice.isdigit() and 1 <= int(choice) <= len(TESTS):
                        case = TESTS[int(choice)-1]
                    else:
                        case = BY_KEY.get(choice)
                        if choice and case is None:
                            self.print("Choose a listed test number or name.")
                    if case:
                        self.detail(case)
                except (ValueError, OSError) as exc:
                    self.print(f"Cannot load recipe: {exc}")
        except (EOFError, KeyboardInterrupt):
            self.print("Menu closed.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--list", action="store_true", help="list tests without connecting")
    group.add_argument("--show", choices=BY_KEY, metavar="TEST", help="show parameters without connecting")
    group.add_argument("--run", choices=BY_KEY, metavar="TEST", help="explicitly start a hardware run")
    parser.add_argument("--profile", type=Path, help="load a saved recipe (or a previous run's recipe.json)")
    parser.add_argument("--dry-run", action="store_true", help="validate and print recipe; never connect")
    parser.add_argument("--pd-input", choices=("in1", "in2"), help="PD input for contrast measurement")
    parser.add_argument("--source", choices=("pd", "pax", "both"), help="contrast detector selection")
    parser.add_argument("--mode", choices=("passive", "active"), help="contrast drive mode; configure axis/waveform in the parameter table or recipe")
    parser.add_argument("--frequency", type=float, help="external or requested active drive frequency in Hz for contrast measurement")
    parser.add_argument("--dark-voltage", type=float, help="blocked-light PD voltage for contrast measurement")
    args = parser.parse_args(argv)
    menu = DiagnosticsMenu()
    case = None
    try:
        if args.profile:
            case, config, options = load_recipe(args.profile)
            menu.sessions[case.key] = (config, options)
        requested = args.show or args.run
        if requested:
            if case and case.key != requested:
                raise ValueError("Requested test does not match the recipe")
            case = BY_KEY[requested]
        if any(value is not None for value in (args.source, args.mode, args.pd_input, args.frequency, args.dark_voltage)):
            if case is None or case.key != "pd-visibility":
                raise ValueError("Contrast overrides (--source, --mode, --pd-input, --frequency, --dark-voltage) require pd-visibility")
            config, options = menu.settings(case)
            for name, value in (("visibility_source", args.source), ("visibility_mode", args.mode), ("pd_input", args.pd_input), ("visibility_frequency_hz", args.frequency), ("visibility_dark_voltage_v", args.dark_voltage)):
                if value is not None:
                    setattr(config, name, value)
        if args.list:
            for index, item in enumerate(TESTS, 1):
                print(f"{index:2}. {item.key}: {item.title}")
        elif args.dry_run:
            if case is None:
                raise ValueError("--dry-run requires --profile, --show TEST, or --run TEST")
            config, options = menu.settings(case)
            validate(config, case, options)
            print(json.dumps(recipe(case, config, options), indent=2))
        elif args.show:
            menu.show(case, *menu.settings(case))
        elif args.run:
            from .runner import execute
            state = execute(case, *menu.settings(case))
            return 0 if state["status"] == "completed" else 1
        else:
            menu.run(case)
    except (ValueError, OSError, RuntimeError, ImportError) as exc:
        parser.exit(2, f"Error: {exc}\n")
    return 0
