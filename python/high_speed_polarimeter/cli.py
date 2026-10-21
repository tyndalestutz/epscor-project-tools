"""Numbered bench menu using the polarization-locking interaction pattern."""
from dataclasses import replace
import json
from pathlib import Path

from polarization_locking.cli import BACK, QUIT, DiagnosticsMenu
from polarization_locking.settings import parse_value

from .catalog import BY_KEY, EXPERIMENTS, PLANNED
from .config import AcquisitionConfig, VpiConfig, DEFAULT_RESULTS, Recipe, load_recipe
from .io import write_json

PROFILES = Path(__file__).resolve().parent / "profiles"
LOCAL_SETTINGS = Path(__file__).resolve().parent / ".local-settings.json"


class PaxMenu(DiagnosticsMenu):
    """Reuse the PAX detail menu, with recipes and runs owned by this suite."""
    def __init__(self, *, input_fn=input, output=print, state_path=None):
        from polarization_locking.config import PolarizationLockConfig
        super().__init__(PolarizationLockConfig(results_directory=DEFAULT_RESULTS),
                         input_fn=input_fn, output=output, run_test=self.execute, state_path=state_path)

    def execute(self, case, config, options):
        if case.key != "pax-live":
            raise ValueError("Only the PAX alignment panel is exposed here")
        from polarization_locking.runner import execute
        return execute(case, config, options)

    def load(self):
        from polarization_locking.settings import load_recipe as load_pax
        text = self.ask("Recipe JSON path; Enter or b = cancel: ")
        if not text or text.lower() in BACK:
            return None
        case, config, options = load_pax(text)
        if case.key != "pax-live":
            raise ValueError("Choose a pax-live recipe")
        config.results_directory = DEFAULT_RESULTS
        self.sessions[case.key] = (config, options)
        self.persist()
        return case

    def save(self, case, config, options):
        from polarization_locking.settings import recipe, validate, write_json
        validate(config, case, options)
        default = PROFILES / "pax-live.json"
        text = self.ask(f"Save recipe [{default}]; b = cancel: ")
        if text.lower() in BACK:
            return
        write_json(text or default, recipe(case, config, options))
        self.print(f"Recipe saved: {text or default}")


class HighSpeedMenu(DiagnosticsMenu):
    def __init__(self, *, input_fn=input, output=print, run_test=None, state_path=None):
        super().__init__(input_fn=input_fn, output=output, run_test=run_test)
        self.recipes = {}
        self.state_path = Path(state_path) if state_path else None
        self.pax = PaxMenu(input_fn=input_fn, output=output,
                           state_path=self.state_path.with_name(".local-pax-settings.json") if self.state_path else None)
        if self.state_path and self.state_path.exists():
            raw = json.loads(self.state_path.read_text())
            if raw.get("schema_version") != 1 or not isinstance(raw.get("tests"), dict):
                raise ValueError("Unsupported local settings schema")
            for key, entry in raw["tests"].items():
                if key not in BY_KEY or entry.get("experiment") != key:
                    raise ValueError("Unknown experiment in local settings")
                current = Recipe(**entry)
                # Retain drafts (including values still awaiting run validation).
                if not isinstance(current.config, dict):
                    raise ValueError("Invalid stored acquisition configuration")
                self.recipes[key] = current

    def persist(self):
        if self.state_path:
            from dataclasses import asdict
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            write_json(self.state_path, {"schema_version": 1, "tests": {
                key: asdict(current) for key, current in self.recipes.items()}})

    def settings(self, case):
        if case.key not in self.recipes:
            self.recipes[case.key] = Recipe(experiment=case.key, label=case.key).resolved()
        return self.recipes[case.key]

    def show(self, case, recipe, *, all_fields=False):
        self.print(f"\n{case.key}\n{case.description}")
        if case.key=='eom_pd_qualification':
            self.print('One selected EOM, fixed 45° input/analyzer, PBS/PD installed. IN1/IN2 simultaneous PD/monitor capture. No PAX. Physical confirmation required before Run.')
        else:
            self.print("PAX-only Vpi check; no PD acquisition. Verify EOM cabling, command limits, wavelength and reference plane. Both outputs zero on exit."
                   if case.key == 'eom_vpi_hysteresis' else "RP command volts are not measured EOM terminal voltages. PD and PAX setup must be verified at the bench.")
        entries = [("option", "label", recipe.label), ("option", "comment", recipe.comment)]
        hidden = {'bidirectional', 'states_v', 'eom1_commands_v', 'eom2_commands_v', 'pd_input', 'rp_scope_port'}
        entries += [("config", key, value) for key, value in recipe.config.items()
                    if all_fields or case.key != 'eom_vpi_hysteresis' or (key not in hidden and not key.startswith('pd_'))]
        for index, (_, key, value) in enumerate(entries, 1):
            self.print(f"  {index:2}. {key} = {json.dumps(value)}")
        return entries

    def edit(self, case, *, all_fields=False):
        while True:
            current = self.settings(case)
            entries = self.show(case, current, all_fields=all_fields)
            try:
                selection = self.ask("Parameter number/name; Enter or b = back: ")
                if not selection or selection.lower() in BACK:
                    return
                if selection.lower() in QUIT:
                    raise EOFError
                if selection.isdigit() and 1 <= int(selection) <= len(entries):
                    scope, key, value = entries[int(selection)-1]
                else:
                    scope, key, value = next((item for item in entries if item[1] == selection), (None, None, None))
                    if scope is None:
                        raise ValueError("Choose a displayed parameter number or name")
                text = self.ask(f"{key} [{json.dumps(value)}]; Enter = keep, b = cancel: ")
                if not text or text.lower() in BACK:
                    continue
                if key == "states_v":
                    value = json.loads(text)  # nullable ordered list of command pairs
                else:
                    if case.key=='eom_pd_qualification':
                        from .qualification.config import QualificationConfig
                        defaults=QualificationConfig()
                    else:
                        defaults = VpiConfig() if case.key == "eom_vpi_hysteresis" else AcquisitionConfig()
                    kind = type(getattr(defaults, key)) if scope == "config" and case.key != "mock_lifecycle" else type(value)
                    value = parse_value(text, kind)
                if scope == "option":
                    updated = replace(current, **{key: value})
                else:
                    updated = replace(current, config={**current.config, key: value})
                self.recipes[case.key] = updated
                self.persist()
                self.print("Updated for this test. Values are checked together before save/run.")
            except KeyboardInterrupt:
                self.print("Edit cancelled.")
                return
            except ValueError as exc:
                self.print(f"Invalid value: {exc}")

    def save(self, case):
        current = self.settings(case).resolved()
        default = PROFILES / f"{case.key}.json"
        text = self.ask(f"Save recipe [{default}]; b = cancel: ")
        if text.lower() in BACK:
            return
        path = Path(text or default).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, current.to_dict())
        self.print(f"Recipe saved: {path}")

    def load(self):
        text = self.ask("Recipe JSON path; Enter or b = cancel: ")
        if not text or text.lower() in BACK:
            return None
        path = Path(text).expanduser()
        raw = json.loads(path.read_text())
        if isinstance(raw, dict) and raw.get("test") == "pax-live":
            from polarization_locking.settings import load_recipe as load_pax
            case, config, options = load_pax(path)
            config.results_directory = DEFAULT_RESULTS
            self.pax.sessions[case.key] = (config, options)
            self.pax.persist()
            return "pax-live"
        current = load_recipe(path)
        self.recipes[current.experiment] = current.resolved()
        self.persist()
        return current.experiment

    def detail(self, case):
        while True:
            self.show(case, self.settings(case))
            try:
                action = self.ask("r run | e edit | all edit all | s save | l load | d defaults | b back | q quit: ").lower()
                if not action or action in BACK:
                    return
                if action in QUIT:
                    raise EOFError
                if action in {"e", "edit", "all"}:
                    self.edit(case, all_fields=action == "all")
                elif action in {"s", "save"}:
                    self.save(case)
                elif action in {"l", "load"}:
                    key = self.load()
                    if key == "pax-live":
                        self.open_tool(key)
                    elif key:
                        case = BY_KEY[key]
                elif action in {"d", "defaults"}:
                    self.recipes.pop(case.key, None)
                    self.persist()
                elif action in {"r", "run"}:
                    current = self.settings(case).resolved()
                    options = self.run_details(case, {"label": current.label, "comment": current.comment})
                    current = replace(current, **options).resolved()
                    self.recipes[case.key] = current
                    self.persist()
                    mode = "mock" if case.key == "mock_lifecycle" else "hardware"
                    if self.run_test is None:
                        from .runner import execute
                        self.run_test = execute
                    self.print("Starting synthetic run." if mode == "mock" else "Starting hardware run. Ctrl+C stops acquisition.")
                    state = self.run_test(current, mode=mode)
                    self.print(f"Run folder: {state['directory']}\nRun status: {state['status']}\nReport: {state['report_status']}")
                    if state["cleanup_status"] == "FAILED":
                        self.print("Instrument cleanup failed; check hardware before continuing.")
                        raise EOFError
                else:
                    self.print("Choose one of the displayed actions.")
            except KeyboardInterrupt:
                self.print("Cancelled; returning to test list.")
                return
            except (ValueError, OSError, RuntimeError, ImportError) as exc:
                self.print(f"Cannot continue: {exc}")

    def open_tool(self, key):
        from polarization_locking.catalog import BY_KEY as LOCKING
        self.pax.detail(LOCKING[key])

    def run(self):
        self.print("High speed polarimeter — browse and edit offline; only Run connects instruments.")
        keys = [case.key for case in EXPERIMENTS] + ["pax-live"]
        try:
            while True:
                self.print("\nAvailable bench tests:")
                for index, case in enumerate(EXPERIMENTS, 1):
                    self.print(f"  {index:2}. {case.key}: {case.description}")
                self.print(f"  {len(keys):2}. PAX manual alignment panel [pax-live]")
                self.print("Planned (not runnable): " + ", ".join(PLANNED))
                try:
                    choice = self.ask("Test number/name | l load recipe | q quit: ")
                    if choice.lower() in QUIT:
                        return
                    if choice.lower() in {"l", "load"}:
                        key = self.load()
                    elif choice.isdigit() and 1 <= int(choice) <= len(keys):
                        key = keys[int(choice)-1]
                    else:
                        key = choice if choice in keys else None
                        if choice and key is None:
                            self.print("Choose a listed test number or name.")
                    if key == "pax-live":
                        self.open_tool(key)
                    elif key:
                        self.detail(BY_KEY[key])
                except (ValueError, OSError, RuntimeError, ImportError) as exc:
                    self.print(f"Cannot continue: {exc}")
        except (EOFError, KeyboardInterrupt):
            self.print("Menu closed.")
