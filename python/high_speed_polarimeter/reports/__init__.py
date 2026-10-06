"""Dependency-free execution and quality report; scientific analysis is deferred."""
from pathlib import Path
from ..io import read_json


def create_report(directory):
    directory = Path(directory)
    run = read_json(directory / "run.json")
    quality = read_json(directory / "quality.json")
    lifecycle_only = run["experiment"] == "mock_lifecycle"
    setup = "Hardware-free mock. Synthetic software events only." if lifecycle_only else (
        f"Experiment: {run['experiment']}; mode: {run['mode']}. "
        "PD is the primary response signal; PAX is the slower polarization reference. "
        "Synthetic values are test-only." if run["mode"] == "mock" else
        f"Experiment: {run['experiment']}; mode: hardware. PD is the primary response signal; PAX is the slower polarization reference.")
    artifacts = "recipe.json, run.json, quality.json and mock_events.jsonl" if lifecycle_only else "recipe.json, run.json, quality.json, acquisition.json, events.jsonl and pd_raw.npy"
    lines = ["# High-speed polarimeter lifecycle report", "",
             f"Execution: {run['status']}", f"Scientific/acquisition quality: {quality['status']}", "",
             "## Setup and acquisition", "", setup, "",
             "## Quality evidence and limitations", "",
             "Command settings are not measured EOM voltages. PD/PAX clocks are not sample-synchronous.", ""]
    for check in quality["checks"]:
        lines.append(f"- {check['name']}: {check['status']} — {check['message']}")
    lines += ["", "No calibrated, fitted, reconstructed or modelled optical quantities.", "",
              "## Provenance", "", f"See {artifacts}.",
              f"Git commit: {run.get('provenance', {}).get('git_commit')}",
              f"Evaluator: {quality['evaluator']} version {quality['evaluator_version']}", ""]
    (directory / "report.md").write_text("\n".join(lines), encoding="utf-8")
