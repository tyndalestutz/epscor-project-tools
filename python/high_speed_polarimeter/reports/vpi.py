"""Compact report using the existing polarization report/PDF infrastructure."""
import csv
import io
import textwrap

import numpy as np

from ..analysis.vpi import analyze
from ..io import read_json


def create_report(directory):
    from polarization_locking.reports.run_report import plt, PdfPages, ReportPages, NAVY, TEAL
    from polarization_locking.reports.poincare_trajectory import draw_trajectory
    result = analyze(directory)
    run = read_json(directory/'run.json')
    quality = read_json(directory/'quality.json')
    path = directory/'data.csv'
    with (path.open(newline='') if path.exists() else io.StringIO('')) as stream:
        rows = list(csv.DictReader(stream))
    config = read_json(directory/'recipe.json')['config']
    lines = ['# Rough EOM Vpi / forward–reverse check', '', f'Mode: {run["mode"]}; execution: {run["status"]}; cleanup: {run["cleanup_status"]}; quality: {quality["status"]}.',
             '', f'Run: {directory.name}', '', result['method'], '', result['limitations'], '']
    temporary = directory/'report.tmp.pdf'
    with PdfPages(temporary) as document:
        pdf = ReportPages(document)
        baseline = result['undriven_baseline']
        from polarization_locking.reports.run_report import text_page
        if baseline['status']=='AVAILABLE':
            def number(value, digits=4):
                return f'{value:.{digits}f}' if value is not None else 'unavailable'
            mean = ', '.join(f'{value:.6f}' for value in baseline['mean'])
            std = ', '.join(f'{value:.6g}' for value in baseline['temporal_std'])
            dop_mean = baseline['dop'].get('mean',[None])[0]
            power_mean = baseline['power_W'].get('mean',[None])[0]
            description = (f'Mode: {run["mode"]}. Requested {config["undriven_baseline_s"]:g} s; {baseline["samples"]} samples; recorded span {baseline["recorded_span_s"]:.3f} s; '
                           f'valid integrated coverage {baseline["covered_s"]:.3f} s.\nTime-mean (s1,s2,s3) = ({mean}). No unit renormalization.\n'
                           f'Temporal SD = ({std}); angular RMS wander {number(baseline["angular_rms_deg"])} deg.\n'
                           f'Distance of mean direction to PAX +S1/H: {number(baseline["distance_to_pax_H_deg"],3)} deg. This does not independently identify the EOM eigenaxis.\n'
                           f'Invalid rows {baseline["invalid_rows"]}; excluded intervals {baseline["excluded_intervals"]}.')
            text_page(pdf,'Undriven polarization — time-average reference',[
                ('Baseline',description),('Reference plane',baseline['reference_plane']),('Processing',baseline['method']),
                ('DoP and power',f'Time averages: DoP {number(dop_mean,5)}; power {number(power_mean*1e6 if power_mean is not None else None,3)} µW.'),
                ('Raw DoP limitation',f'{result["dop_above_one_count"]} raw DoP readings above 1 in the complete run; retained uncorrected. Above-one values are a measurement/calibration limitation, not physical DoP above 100%.'),
                ('Interpretation','A small response can be consistent with near-eigenpolarization alignment, but cannot establish zero coupling or 100% response without an independent axis/reference calibration.')])
            lines += ['## Undriven time average',description,baseline['reference_plane'],baseline['method']]
        else:
            text_page(pdf,'Undriven reference unavailable',[('Reason',baseline['reason'])])
        for cycle in result['cycles'] or [dict(status=result['status'],reason=result.get('reason','No complete cycle'),cycle=None)]:
            fig = plt.figure(figsize=(11.7,8.3),facecolor='white')
            fig.text(.065,.94,f'{config["selected_eom"].upper()} — rough Vπ / forward–reverse check',fontsize=18,color=NAVY,weight='bold')
            fig.text(.065,.90,f'Mode: {run["mode"]} | cycle: {cycle["cycle"]} | RP commands: 0–{config["stop_command_v"]:g} V | cleanup: {run["cleanup_status"]}',fontsize=10,color=NAVY)
            grid = fig.add_gridspec(1,3,left=.065,right=.95,bottom=.47,top=.82,wspace=.4)
            ax = fig.add_subplot(grid[0,0]); hx = fig.add_subplot(grid[0,1])
            selected = rows if cycle['cycle'] is None else [row for row in rows if int(row['cycle'])==cycle['cycle']]
            caption = draw_trajectory(fig,grid[0,2],selected)
            summary = []
            if 'phase_deg' in cycle:
                volts = np.array(cycle['command_v'])
                for name,phase,fit,color in zip(('forward','reverse'),cycle['phase_deg'],cycle['fits'],(TEAL,'#bc7040')):
                    ax.plot(volts,phase,'.-',ms=3,lw=.8,color=color,label=name)
                    if fit['fit_supported']:
                        ax.plot(volts,np.degrees(fit['slope_rad_per_v']*volts+fit['intercept_rad']),'--',lw=.6,color=color)
                    number = f'{fit["vpi_linear_branch_command_v"]:.4f} V' if fit['fit_supported'] else 'unsupported'
                    crossing = f'{fit["pi_crossing_command_v"]:.4f} V (adjacent-point interpolation)' if fit['pi_crossing_command_v'] is not None else 'NOT reached'
                    summary.append(f'{name}: linear-branch Vπ {number}; π crossing: {crossing}; fit RMS {fit["fit_rms_deg"]:.3f}°.')
                ax.axhline(180,color='gray',lw=.6); ax.legend(fontsize=7)
                hx.plot(volts,cycle['difference_deg'],'.-',ms=3,lw=.8,color=TEAL);hx.axhline(0,color='gray',lw=.6)
                if not all(fit['pi_reached'] for fit in cycle['fits']):
                    summary.insert(0,'π NOT established on both branches. Linear-branch Vπ is extrapolated; inspect any response plateau.')
                summary.append(f'Apparent direction difference: max {cycle["max_direction_difference_deg"]:.3f}°; RMS {cycle["rms_direction_difference_deg"]:.3f}°; zero-return separation {cycle["return_separation_deg"]:.3f}°.')
                summary.append(f'Plane RMS {cycle["plane_rms"]:.3g} Stokes units; fit interval {cycle["fit_range_command_v"]} RP command V.')
            else:
                summary.append('Vπ unavailable: '+cycle.get('reason',cycle['status']))
                if 'excursion_from_undriven_deg' in cycle:
                    for name, values, color in zip(('forward','reverse'),cycle['excursion_from_undriven_deg'],(TEAL,'#bc7040')):
                        ax.plot(cycle['command_v'],values,'.-',lw=.8,ms=3,color=color,label=name)
                    hx.plot(cycle['command_v'],cycle['direction_poincare_difference_deg'],'.-',lw=.8,ms=3,color=TEAL)
                    ax.legend(fontsize=7)
                    summary.append(f'Forward/reverse Poincaré separation: max {cycle["max_direction_poincare_difference_deg"]:.4f}°; zero-return separation {cycle["zero_return_poincare_difference_deg"]:.4f}°.')
            ax.set(xlabel='RP command (V)',ylabel='Inferred phase change (deg)',title='Point means; dashed = linear model')
            hx.set(xlabel='RP command (V)',ylabel='Reverse − forward phase (deg)',title='Apparent hysteresis + drift / settling')
            if 'phase_deg' not in cycle and 'excursion_from_undriven_deg' in cycle:
                ax.set(ylabel='Excursion from undriven mean (deg)',title='Measured Poincaré response; no phase model')
                hx.set(ylabel='Forward/reverse separation (deg)',title='Apparent direction difference')
            if 'max_excursion_from_undriven_deg' in cycle:
                summary.append(f'Maximum Poincaré excursion from undriven mean: {cycle["max_excursion_from_undriven_deg"]:.3f}°.')
            for item in (ax,hx):
                item.grid(alpha=.15);item.title.set_fontsize(9);item.tick_params(labelsize=8)
            summary_text='\n'.join(line for text in summary for line in textwrap.wrap(text,135))
            fig.text(.065,.405,summary_text,fontsize=9,color=NAVY,va='top',linespacing=1.6)
            notes = result['method']+' '+result['limitations']+' Sphere: raw recorded directions; gaps >5× median cadence and invalid/inconsistent time intervals unconnected. Wavelength setting: '+str(config['pax_wavelength_nm'])+' nm. DoP range: '+str(result['dop_range'])+'. Run notes: '+read_json(directory/'recipe.json').get('comment','None recorded')
            wrapped = textwrap.wrap(notes,170)
            fig.text(.065,.23,'\n'.join(wrapped[:8])+ ('\nFull method/run notes follow.' if len(wrapped)>8 else ''),fontsize=7.5,color=NAVY,va='top',linespacing=1.25)
            fig.text(.065,.06,'Sources: recipe.json, run.json, acquisition.json, events.jsonl, data.csv; derived analysis.json / quality.json.',fontsize=8,color=NAVY)
            pdf.savefig(fig);plt.close(fig)
            if len(wrapped)>8:
                from polarization_locking.reports.run_report import text_page
                text_page(pdf,'Method and run notes',[('Notes',notes)])
            lines += summary+['']
    temporary.replace(directory/'report.pdf')
    (directory/'report.md').write_text('\n\n'.join(lines)+'\n')
