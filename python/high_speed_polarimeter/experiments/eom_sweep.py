"""Single- and dual-EOM acquisition share exactly the same per-state lifecycle."""
from ..acquisition.eom import acquire_states


def iter_states(recipe):
    config = recipe.acquisition_config()
    if recipe.experiment == "single_eom_characterization":
        values = [config.start_command_v + (config.stop_command_v - config.start_command_v) * i / (config.points - 1) for i in range(config.points)]
        # Retain exact requested endpoint and duplicate turning points deliberately.
        values[-1] = config.stop_command_v
        sequences = [("forward", values)]
        if config.bidirectional:
            sequences.append(("reverse", list(reversed(values))))
    else:
        pairs = config.states_v if config.states_v is not None else [
            [v1, v2] for v1 in config.eom1_commands_v for v2 in config.eom2_commands_v]
        sequences = [("ordered_list" if config.states_v is not None else "grid", pairs)]
    index = 0
    for repeat in range(config.repeats):
        for direction, values in sequences:
            for step, value in enumerate(values):
                if recipe.experiment == "single_eom_characterization":
                    pair = [value, config.other_eom_command_v] if config.selected_eom == "eom1" else [config.other_eom_command_v, value]
                else:
                    pair = value
                outputs = {config.eom1_output: float(pair[0]), config.eom2_output: float(pair[1])}
                yield {"point": index, "repeat": repeat, "direction": direction, "step": step,
                       "eom_commands_v": list(map(float, pair)),
                       "rp_commands_v": [outputs["out1"], outputs["out2"]]}
                index += 1


def acquire(session, recipe, directory):
    return acquire_states(session, recipe, directory, iter_states(recipe), sum(1 for _ in iter_states(recipe)))
