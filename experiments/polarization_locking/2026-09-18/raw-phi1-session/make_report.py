"""Rebuild this session summary from preserved measurements; no fits."""
import csv
import json
from pathlib import Path
import numpy as np
from polarization_locking.reports.raw_calibration import analyze, number
from polarization_locking.reports.run_report import plt, PdfPages, ReportPages, text_page, NAVY

HERE=Path(__file__).resolve().parent
NAMES=['195031_raw-phi1_repeatability-1s','200038_raw-phi1_fixed-0p4-drift','200242_raw-phi1_fine-brackets-3s']
runs=[]
for name in NAMES:
    path=HERE.parent/name
    run=json.loads((path/'run.json').read_text())
    assert run['status'] in ('completed','interrupted'),name
    with (path/'data.csv').open() as f:rows=list(csv.DictReader(f))
    runs.append((run,rows,analyze(rows)))

def observed(rows):
    return {'readings':len(rows),'s1_range':[float(number(rows,'pax_s1').min()),float(number(rows,'pax_s1').max())],
            'dop_range':[float(number(rows,'pax_dop').min()),float(number(rows,'pax_dop').max())],
            'max_internal_command_error_v':float(np.max(np.abs(number(rows,'digital_out1_scope_median_v')-number(rows,'requested_rp_v'))))}

stats=[observed(rows) for _,rows,_ in runs]
hold=runs[1][1];phase=np.unwrap(number(hold,'equatorial_phase_rad'));time=number(hold,'host_before_s');time-=time[0]
hold_stats={'duration_s':float(time[-1]),'phase_peak_to_peak_rad':float(np.ptp(phase)),
            'first_to_last_block_median_change_rad':float(np.median(phase[-10:])-np.median(phase[:10])),
            'last_45s_phase_peak_to_peak_rad':float(np.ptp(phase[time>time[-1]-45]))}
summary={'method':'Direct raw observations and explicitly labelled medians/ranges. No fits or interpolations.',
         'source_runs':NAMES,'observations':stats,'fixed_voltage':hold_stats,
         'pi_brackets':{NAMES[i]:runs[i][2]['pi_crossing_brackets'] for i in (0,2)},
         'status':'Actuator-to-polarization response demonstrated. Drift is accepted; terminal-voltage mapping remains unmeasured.'}
(HERE/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')

with PdfPages(HERE/'report.pdf',metadata={'Title':'Raw phi1 calibration — measured findings','Author':'Polarization diagnostics'}) as output:
    pdf=ReportPages(output)
    text_page(pdf,'Raw phi1 calibration — measured findings',[
        ('Main result','A clear actuator-to-polarization response is demonstrated. Drift is accepted for this objective. Increasing the RP command drives the measured equatorial phase in one direction; reversing the sweep reverses that motion. This result does not require a fitted model or drift correction.'),
        ('Direct evidence','Endpoint step medians give forward phase changes of -7.547, -7.376 and -7.330 rad over 0–0.8 V, each exceeding one full turn. Reverse sweeps give +7.430, +8.465 and +7.098 rad. Internal OUT1 medians agree with the requested command within 0.12 mV.'),
        ('Scope and stop status','18 September 2026. Three complete forward/reverse cycles, one completed fixed-voltage hold, and one finer scan interrupted at the user request. Both outputs returned to zero; saved digital cleanup traces confirm zero. No further acquisition was started.'),
        ('Qualification: trajectory geometry',f"S1 ranges from {stats[0]['s1_range'][0]:.4f} to {stats[0]['s1_range'][1]:.4f} in the repeat scan. Its departure is organized by measured phase across cycles. Three-second settling also gives nonzero S1, up to {stats[2]['s1_range'][1]:.4f}. The response therefore demonstrates actuation without establishing an exact S1=0 circle."),
        ('Drift context',f"At fixed 0.4 V, phase spans {hold_stats['phase_peak_to_peak_rad']:.3f} rad over {hold_stats['duration_s']:.1f} s; the first-to-last block medians change by {hold_stats['first_to_last_block_median_change_rad']:.3f} rad. Internal OUT1 remains at 0.39990234375 V. This variation is much smaller than the >7 rad commanded full-sweep excursion, although durations differ and this is not a noise-subtracted comparison."),
        ('Voltage interpretation','The following page brackets a pi change in atan2(S3,-S2) using measured RP command steps. Internal FPGA readings are not electrical BNC or terminal measurements: the expected 13 V terminal V_pi remains unverified. No fitted calibration or assumed driver gain is applied. Actual PAX port and physical IN1/IN2 connections await confirmation.')])
    cells=[]
    for i in (0,2):
        for b in runs[i][2]['pi_crossing_brackets']:
            median='; '.join(f"{x['rp_command_delta_bracket_v'][0]:.3f}–{x['rp_command_delta_bracket_v'][1]:.3f}" for x in b['crossings']) or 'No crossing'
            spread=b['observed_sample_spread_bracket']
            spread='–'.join(f'{x:.3f}' for x in spread['rp_command_delta_bracket_v']) if spread else 'Not bracketed'
            cells.append(['1 s' if i==0 else '3 s',str(b['cycle']),b['direction'],f"{b['reference_command_v']:.1f}",median,spread])
    fig,ax=plt.subplots(figsize=(11.7,8.3));ax.axis('off')
    fig.text(.065,.92,'Observed equatorial π-change brackets',fontsize=21,weight='bold',color=NAVY)
    fig.text(.065,.84,'All intervals are ΔRP requested command volts relative to the stated pass reference.',fontsize=11)
    table=ax.table(cellText=cells,colLabels=['Settle','Cycle','Direction','Reference V','Step-median bracket','Sample-spread bracket'],cellLoc='center',bbox=[0,.18,1,.62],colWidths=[.08,.07,.13,.13,.28,.31])
    table.auto_set_font_size(False);table.set_fontsize(10)
    for (row,col),cell in table.get_celld().items():
        cell.set_edgecolor('white');cell.set_facecolor(NAVY if row==0 else ('#edf4f7' if row%2 else 'white'))
        if row==0:cell.set_text_props(color='white',weight='bold')
    fig.subplots_adjust(left=.065,right=.94,top=.87,bottom=.15)
    fig.text(.065,.15,'Step-median brackets use adjacent measured levels. Wider brackets include all observed reference/endpoint samples.',fontsize=10)
    fig.text(.065,.11,'No interpolation. The sample-spread bracket is an observed range, not a statistical confidence interval.',fontsize=10)
    fig.text(.065,.07,'Different grids and timing expose drift as well as settling; the 3 s run is not an isolated settling-time experiment.',fontsize=10)
    pdf.savefig(fig);plt.close(fig)
    fig,axes=plt.subplots(2,2,figsize=(11.7,8.3));fig.subplots_adjust(left=.08,right=.97,bottom=.12,top=.87,hspace=.42,wspace=.3)
    fig.suptitle('Demonstrated actuator response — raw measurements',fontsize=19,weight='bold',color=NAVY,y=.96)
    for cycle in (1,2,3):
        for direction in ('forward','reverse'):
            group=[x for x in runs[0][1] if int(x['cycle'])==cycle and x['direction']==direction]
            measured=np.unwrap(number(group,'equatorial_phase_rad'))
            axes[0,0].scatter(number(group,'requested_rp_v'),measured-measured[0],s=6,alpha=.5,label=f'{cycle} {direction}')
    axes[0,0].legend(fontsize=7,ncol=2)
    axes[0,0].set(title='Commanded sweeps drive phase in both directions',xlabel='Requested RP command (V)',ylabel='Phase change from pass start (rad)')
    for cycle in (1,2,3):
        group=[x for x in runs[0][1] if int(x['cycle'])==cycle]
        axes[1,0].scatter(number(group,'equatorial_phase_rad'),number(group,'pax_s1'),s=6,alpha=.5,label=f'Cycle {cycle}')
    axes[1,0].axhline(0,color='black',ls='--');axes[1,0].legend(fontsize=8)
    axes[1,0].set(title='Trajectory shape repeats across cycles',xlabel='atan2(S3, -S2) (rad)',ylabel='S1')
    axes[0,1].plot(time,phase-phase[0],'.',ms=3);axes[0,1].set(title='Fixed 0.4 V: phase changes without commands',xlabel='Time from first recorded reading (s)',ylabel='Phase change (rad)')
    for i,label in [(0,'1 s settling'),(2,'3 s settling')]:
        axes[1,1].scatter(number(runs[i][1],'requested_rp_v'),number(runs[i][1],'pax_s1'),s=6,alpha=.45,label=label)
    axes[1,1].axhline(0,color='black',ls='--');axes[1,1].legend(fontsize=8);axes[1,1].set(title='Longer waiting does not restore S1 = 0',xlabel='Requested RP command (V)',ylabel='S1')
    for ax in axes.flat:ax.grid(alpha=.2)
    fig.text(.08,.035,'Every dot is a recorded PAX reading. Original axes are retained; no fitted rotation, drift removal or smoothing.',fontsize=10)
    pdf.savefig(fig);plt.close(fig)
    sections=[]
    for name,(run,rows,a) in zip(NAMES,runs):
        p=run['parameters']
        command=f"Fixed {p['hold_voltage']} V, {p['hold_points']} blocks" if p.get('hold_voltage') is not None else (f"Explicit ascending levels: {p['levels']}" if p.get('levels') else f"{p['start']}–{p['stop']} V, {p['step']} V steps")
        sections.append((name,f"Status: {run['status']}. {command}\n{p['settle']} s wait; {p['samples']} readings/block; {p['repeats']} cycles (ignored in hold mode). {len(rows)} raw readings, {a['unique_pax_timestamps']} unique PAX timestamps.\nFull settings, exact raw records, one consolidated scope.npz and individual PDF are in this source folder."))
    sections += [('PAX acquisition',f"Configured wavelength {runs[0][0]['configuration']['pax_wavelength_nm']} nm; mode {runs[0][0]['configuration']['pax_measurement_mode']}; rotation {runs[0][0]['configuration']['pax_rotation_velocity_hz']} Hz. Orientation Stokes are normalized from measured angles; DOP is preserved separately."),
                 ('Next discriminating measurements','Confirm PAX port and physical input connections. Measure RP BNC/MDT input and piezo terminals with an independent, appropriately rated instrument while repeating known held commands. Compare their time records with PAX. Once voltage stability is established, check optical state preparation and the PAX reference axes to identify the systematic S1 departure.'),
                 ('Interpretation limit','These tests characterize static held commands. A reliable sinusoidal trajectory is not established by these scans; it needs a time-resolved test after the mapping and drift are understood.')]
    text_page(pdf,'Active parameters and remaining measurements',sections)
print(HERE/'report.pdf')
