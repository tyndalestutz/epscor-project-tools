"""Consistent PDF reports for every bench test; reuse the established plotters."""
from collections import Counter
import csv
from importlib import import_module
import json
import os
from pathlib import Path
import textwrap

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib-polarization-reports")
os.environ.setdefault("XDG_CACHE_HOME", "/tmp")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
import numpy as np

NAVY = "#18354b"
TEAL = "#007f86"


class ReportPages:
    """Add consistent page numbers without altering existing scientific plots."""
    def __init__(self, pdf):
        self.pdf, self.page = pdf, 0

    def savefig(self, fig):
        self.page += 1
        fig.text(.985, .008, f"Polarization diagnostics  |  {self.page}", ha="right", fontsize=7, color=NAVY)
        self.pdf.savefig(fig)


def text_page(pdf, title, sections):
    """Fixed margins and bounded text blocks keep summaries away from plots."""
    fig = None
    y = 0.0
    for heading, content in sections:
        lines = [(heading.upper(), True)]
        for line in str(content).splitlines():
            lines.extend((wrapped, False) for wrapped in (textwrap.wrap(line, width=110) or [""]))
        for line, is_heading in lines:
            if fig is None or y < .095:
                if fig is not None:
                    pdf.savefig(fig)
                    plt.close(fig)
                fig = plt.figure(figsize=(11.7, 8.3), facecolor="white")
                fig.text(.065, .92, title, fontsize=21, weight="bold", color=NAVY)
                y = .84
            fig.text(.065, y, line, fontsize=9 if is_heading else 10,
                     weight="bold" if is_heading else "normal", color=TEAL if is_heading else NAVY, va="top")
            y -= .029 if is_heading else .024
        y -= .017
    if fig is not None:
        pdf.savefig(fig)
        plt.close(fig)


def column(rows, key):
    values = []
    for row in rows:
        try:
            values.append(float(row.get(key, "nan")))
        except (TypeError, ValueError):
            values.append(float("nan"))
    return np.asarray(values)


def add_overview(pdf, case, paths, config, options, state, rows):
    stages = Counter(row.get("stage", row.get("condition", "acquisition")) for row in rows)
    samples = f"{len(rows):,} recorded samples"
    if stages:
        samples += "; " + ", ".join(f"{k}: {v:,}" for k, v in stages.items())
    bench_context = f"{config.bench_pax_location}\n{config.bench_voltage_chain}"
    if case.scope_only:
        bench_context = (f"Source: {config.visibility_source}; mode: {config.visibility_mode}; "
                         f"PD input: {config.pd_input}; PAX location: {config.bench_pax_location}; "
                         f"requested/declared drive: {config.visibility_frequency_hz:g} Hz. ")
        bench_context += (f"Active {config.visibility_axis}, {config.visibility_waveform}, "
                          f"{config.visibility_offset_v:g} +/- {config.visibility_amplitude_v:g} V; outputs zero on exit."
                          if config.visibility_mode == "active" else "RP outputs unchanged.")
    sections = [
        ("Run", f"{paths.directory.name}\nStatus: {state.get('status', 'unknown')} | Started: {state.get('started_at', 'not recorded')}"),
        ("Purpose", case.description),
        ("Required optical setup", case.setup),
        ("Recorded bench context (recipe)", bench_context),
        ("Operator setup notes", config.bench_notes),
        ("Data", samples + "\nSource: data.csv; full acquisition settings: recipe.json; status: run.json"),
    ]
    if rows and any(np.isfinite(column(rows, "dop"))):
        dop = column(rows, "dop")
        finite = dop[np.isfinite(dop)]
        sections.append(("Raw DOP", f"Mean {finite.mean():.4f}; range {finite.min():.4f} to {finite.max():.4f}. "
                         f"{np.sum((finite < 0) | (finite > 1))} samples outside 0–1. Telemetry is not a new calibration."))
    if "D port" in case.setup and "C port" in config.bench_pax_location:
        sections.append(("Placement note", "The recipe records C, but this test's instructions specify D. Actual placement was not independently logged; confirm it before interpreting the D-port model."))
    if state.get("error") or state.get("cleanup_error"):
        sections.append(("Run issue", state.get("error", state.get("cleanup_error"))))
    text_page(pdf, case.title + " — run report", sections)


def add_parameters(pdf, case, config, options):
    shared = ("phi1_v_lambda", "phi2_v_lambda", "phi1_actuator_volts_per_rp_volt", "phi2_actuator_volts_per_rp_volt", "rp_output_min_voltage", "rp_output_max_voltage", "pax_rotation_velocity_hz", "pax_measurement_mode", "pax_wavelength_nm")
    names = [name for name in case.config_names() if name in shared or name.startswith(case.groups)]
    items = [(k, json.dumps(v)) for k, v in options.items()]
    items += [(name, json.dumps(getattr(config, name))) for name in names]
    for start in range(0, len(items), 24):
        fig, ax = plt.subplots(figsize=(11.7, 8.3))
        ax.axis("off")
        fig.text(.065, .92, "Active parameters", fontsize=21, weight="bold", color=NAVY)
        fig.text(.065, .86, f"Contrast: {config.visibility_source}, {config.visibility_mode}. Active drive readback is in drive.json; voltages are RP command volts." if case.scope_only else "RP commands: volts, bounded to 0–1. V_lambda: terminal volts for 2π. Gains: terminal V / RP V.", fontsize=10)
        table = ax.table(cellText=items[start:start+24], colLabels=["Parameter", "Recorded value"], colWidths=[.64, .36], cellLoc="left", loc="upper left", bbox=[0, 0, 1, .94])
        table.auto_set_font_size(False)
        table.set_fontsize(9)
        for (row, _), cell in table.get_celld().items():
            cell.set_edgecolor("white")
            cell.set_facecolor(NAVY if row == 0 else ("#edf4f7" if row % 2 else "#ffffff"))
            if row == 0:
                cell.set_text_props(color="white", weight="bold")
        fig.subplots_adjust(left=.065, right=.94, top=.81, bottom=.075)
        pdf.savefig(fig)
        plt.close(fig)


def add_telemetry(pdf, rows):
    if not rows:
        return
    groups = [
        ("Normalized Stokes", ("s1", "s2", "s3"), "Stokes"),
        ("Raw degree of polarization", ("dop",), "DOP"),
        ("Actuator command history", ("rp_out1_v", "rp_out2_v", "phi1_rp_voltage", "phi2_rp_voltage", "phi1_rp_command_estimated_v"), "RP command (V)"),
        ("Photodiode signal", ("pd_mean_v", "pd_v"), "PD (V)"),
        ("PAX total power", ("pax_ptotal", "ptotal"), "PAX native power units"),
        ("Sphere coordinates", ("u", "v"), "Angle (rad)"),
    ]
    fig, axes = plt.subplots(3, 2, figsize=(11.7, 8.3))
    fig.subplots_adjust(top=.88, bottom=.09, left=.08, right=.97, hspace=.65, wspace=.3)
    fig.suptitle("Recorded telemetry", fontsize=19, weight="bold", color=NAVY, y=.96)
    fig.text(.08, .92, "Acquisition order preserves stage changes; no timing alignment or transfer gain is inferred.", fontsize=9)
    for ax, (title, fields, unit) in zip(axes.flat, groups):
        plotted = False
        for field in fields:
            values = column(rows, field)
            if np.any(np.isfinite(values)):
                ax.plot(np.arange(len(rows)), values, linewidth=.85, label=field)
                plotted = True
        ax.set(title=title, xlabel="Sample index", ylabel=unit)
        ax.grid(alpha=.2)
        if plotted:
            ax.legend(fontsize=7, loc="best")
        else:
            ax.text(.5, .5, "Not recorded", transform=ax.transAxes, ha="center", color="gray")
    pdf.savefig(fig)
    plt.close(fig)


def add_specialized(pdf, case, paths, options):
    kind = case.report
    if kind == "stokes-phase-sweep":
        from .plot_calibration_tests import add_stokes_phase_sweep_page
        add_stokes_phase_sweep_page(pdf, paths.csv)
    elif kind == "visibility":
        summary = json.loads((paths.directory / "visibility.json").read_text())
        source = summary.get("source", "pd")
        text_page(pdf, f"{source.upper()} contrast", [(key.replace("_", " "), str(value)) for key, value in summary.items()])
        drive_path = paths.directory / "drive.json"
        if drive_path.exists():
            text_page(pdf, "Contrast drive", [(key.replace("_", " "), str(value)) for key, value in json.loads(drive_path.read_text()).items()])
        if source in {"pax", "both"}:
            pax_path = paths.csv if source == "pax" else paths.directory / "pax.csv"
            with pax_path.open(newline="") as handle:
                rows = list(csv.DictReader(handle))
            fig, ax = plt.subplots(figsize=(11.7, 8.3), constrained_layout=True)
            ax.plot(column(rows, "elapsed_s"), column(rows, "pax_ptotal") * 1e6, ".-", lw=.8, ms=3)
            ax.set(title="PAX total optical power — recorded samples", xlabel="Host elapsed time (s)", ylabel="Power (µW)")
            ax.grid(alpha=.2)
            pdf.savefig(fig)
            plt.close(fig)
            if source == "pax":
                return
        dark_path = paths.directory / "dark.npz"
        if dark_path.exists():
            with np.load(dark_path) as data:
                fig, ax = plt.subplots(figsize=(11.7, 8.3), constrained_layout=True)
                ax.plot(data["time_s"], data["voltage_v"] * 1000, lw=.5)
            ax.axhline(summary["dark_voltage_v"] * 1000, color=TEAL, label="Measured signed dark baseline")
            ax.set(title="Blocked-light PD capture", xlabel="Capture time (s)", ylabel="Signed PD voltage (mV)")
            ax.legend()
            pdf.savefig(fig)
            plt.close(fig)
        captures = sorted(paths.directory.glob("capture-*.npz"))
        for start in range(0, len(captures), 3):
            fig, axes = plt.subplots(3, 1, figsize=(11.7, 8.3), constrained_layout=True)
            for ax, path in zip(axes, captures[start:start + 3]):
                from ..routines.visibility import analyze_trace
                with np.load(path) as data:
                    t, v = data["time_s"], data["voltage_v"]
                dt = float(np.median(np.diff(t)))
                result, means = analyze_trace(v, dt, summary["external_frequency_hz"], summary["dark_voltage_v"])
                ax.plot(t, v * 1000, color="0.7", lw=.4, label="Recorded samples (FPGA averaging on)")
                ax.plot(t[0] + (np.arange(len(means)) + .5) * result["bin_s"], means * 1000, color=TEAL, label="1/64-period averages")
                if summary["dark_voltage_v"] is not None:
                    ax.axhline(summary["dark_voltage_v"] * 1000, color="tab:red", linestyle="--", label="Signed dark baseline")
                ax.set(title=f"{summary['pd_input']} / {path.stem}: {result['status']}", xlabel="Capture time (s)", ylabel="PD voltage (mV)")
                ax.legend(fontsize=8)
                ax.grid(alpha=.2)
            for ax in axes[len(captures[start:start + 3]):]:
                ax.set_visible(False)
            pdf.savefig(fig)
            plt.close(fig)
    elif kind.startswith("analyze_"):
        analyzer = import_module(f"polarization_locking.analysis.{kind}")
        analyzer.create_report(paths.csv, options.get("report", "pdf"), document=pdf)
    elif kind == "power":
        from .plot_power_balance import add_scope_page, add_summary_table_page
        add_scope_page(pdf, paths.csv, paths.directory / "normalized-scope.png" if options.get("report") == "both" else None)
        add_summary_table_page(pdf, paths.csv, paths.directory / "power-summary.png" if options.get("report") == "both" else None)
    elif kind in {"pid", "single-axis-pid", "phi1-d-lock", "phi1-pd-lock", "cross"}:
        modules = {"pid": "plot_pid_tests", "single-axis-pid": "plot_single_axis_pid", "phi1-d-lock": "plot_phi1_d_lock", "phi1-pd-lock": "plot_phi1_pd_lock", "cross": "plot_cross_tests"}
        module = import_module(f"polarization_locking.reports.{modules[kind]}")
        getattr(module, "add_report" if kind == "cross" else "add_page")(pdf, paths.csv)
    elif kind != "overview":
        from . import plot_calibration_tests as plots
        methods = {"sweep": "add_axis_sweep_page", "bidirectional": "add_bidirectional_page", "diagnostic": "add_diagnostic_page", "intensity": "add_intensity_diagnostic_page", "phi2-path-test": "add_phi2_path_balance_page", "pax-path-hold": "add_pax_path_hold_page"}
        args = (options["axis"],) if kind == "sweep" else ()
        getattr(plots, methods[kind])(pdf, paths.csv, *args)


def create_run_report(case, paths, config, options, state):
    rows = []
    if paths.csv.exists():
        with paths.csv.open(newline="") as handle:
            rows = list(csv.DictReader(handle))
    temporary = paths.pdf.with_suffix(".tmp.pdf")
    plot_error = None
    with PdfPages(temporary, metadata={"Title": case.title, "Subject": paths.directory.name, "Author": "Polarization diagnostics"}) as document:
        pdf = ReportPages(document)
        add_overview(pdf, case, paths, config, options, state, rows)
        if rows:
            try:
                add_specialized(pdf, case, paths, options)
            except Exception as exc:
                plot_error = f"{type(exc).__name__}: {exc}"
                plt.close("all")
                text_page(pdf, "Specialized analysis unavailable", [("Reason", plot_error), ("Data retained", "Raw telemetry follows. The original CSV and recipe remain available for reanalysis.")])
            if not case.scope_only:
                add_telemetry(pdf, rows)
        else:
            log = paths.directory / "console.log"
            lines = log.read_text().splitlines() if log.exists() else []
            for start in range(0, len(lines), 18):
                text_page(pdf, "Run log", [("Recorded output", "\n".join(lines[start:start+18]))])
        add_parameters(pdf, case, config, options)
    temporary.replace(paths.pdf)
    print(f"PDF report saved to {paths.pdf}")
    if plot_error:
        print(f"Report contains raw data; specialized analysis unavailable: {plot_error}")
    return {"report_file": paths.pdf.name, "report_status": "partial" if plot_error else "completed", **({"report_error": plot_error} if plot_error else {})}


def main(argv=None):
    import argparse
    from types import SimpleNamespace
    from ..catalog import BY_KEY, default_options
    from ..config import PolarizationLockConfig
    from ..settings import write_json
    parser = argparse.ArgumentParser(description="Generate report.pdf from an existing run; no instrument access.")
    parser.add_argument("run_directory", type=Path)
    args = parser.parse_args(argv)
    directory = args.run_directory.resolve()
    saved = json.loads((directory / "recipe.json").read_text())
    state = json.loads((directory / "run.json").read_text())
    case = BY_KEY[saved["test"]]
    config = PolarizationLockConfig(**state.get("effective_config", saved["config"]))
    options = default_options(case) | saved.get("options", {}) | {"report": "pdf"}
    paths = SimpleNamespace(directory=directory, csv=directory / "data.csv", pdf=directory / "report.pdf")
    state.update(create_run_report(case, paths, config, options, state))
    write_json(directory / "run.json", state)


if __name__ == "__main__":
    main()
