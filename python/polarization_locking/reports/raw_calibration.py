"""Direct plots and measured pi-crossing brackets. No regression, interpolation, or fitted correction."""
from collections import defaultdict
import csv
import json
import math
from pathlib import Path

import numpy as np
from .run_report import plt, PdfPages, ReportPages, text_page, NAVY
from ..settings import write_json


def number(rows,key):
    return np.asarray([float(row[key]) for row in rows])


def analyze(rows):
    """Preserve every reading; use medians only for explicitly labelled step summaries."""
    points=defaultdict(list)
    for row in rows:points[int(row['point'])].append(row)
    summaries=[]
    for point,group in sorted(points.items()):
        # Unwrapping only adds multiples of 2pi across the atan2 branch cut.
        raw_phase=np.unwrap(number(group,'equatorial_phase_rad'))
        summaries.append({'point':point,'cycle':int(group[0]['cycle']),'direction':group[0]['direction'],
                          'step_index':int(group[0]['step_index']),'command_v':float(group[0]['requested_rp_v']),
                          'offset_readback_v':float(group[0]['asg_offset_readback_v']),
                          'phase_median_rad':float(np.median(raw_phase)),
                          'phase_min_rad':float(raw_phase.min()),'phase_max_rad':float(raw_phase.max()),
                          's1_min':float(number(group,'pax_s1').min()),'s1_max':float(number(group,'pax_s1').max()),
                          'dop_min':float(number(group,'pax_dop').min()),'dop_max':float(number(group,'pax_dop').max()),
                          'sample_count':len(group)})
    passes=defaultdict(list)
    for item in summaries:passes[(item['cycle'],item['direction'])].append(item)
    brackets=[]
    for (cycle,direction),group in passes.items():
        phase=np.unwrap([x['phase_median_rad'] for x in group])
        change=phase-phase[0]
        sign=1 if change[-1]>=0 else -1
        excursion=sign*change
        for item,value in zip(group,change):item['phase_change_from_pass_start_rad']=float(value)
        if direction in ('hold','probe'):
            continue
        matches=[]
        for i in range(1,len(group)):
            if excursion[i-1] <= math.pi <= excursion[i]:
                ref=group[0]['command_v']
                bounds=sorted([abs(group[i-1]['command_v']-ref),abs(group[i]['command_v']-ref)])
                matches.append({'rp_command_delta_bracket_v':bounds,
                                'command_levels_v':[group[i-1]['command_v'],group[i]['command_v']],
                                'phase_excursion_at_bracket_rad':[float(excursion[i-1]),float(excursion[i])],
                                'points':[group[i-1]['point'],group[i]['point']]})
        # A conservative observed-data envelope includes the full spread at the
        # reference hold and at each endpoint, rather than inventing precision
        # between closely spaced commands.
        ref_low=group[0]['phase_min_rad']-group[0]['phase_median_rad']
        ref_high=group[0]['phase_max_rad']-group[0]['phase_median_rad']
        excursion_ranges=[]
        for item,delta in zip(group,change):
            low=delta+(item['phase_min_rad']-item['phase_median_rad'])-ref_high
            high=delta+(item['phase_max_rad']-item['phase_median_rad'])-ref_low
            excursion_ranges.append(sorted([float(sign*low),float(sign*high)]))
        spread_bracket=None
        above=next((i for i,bounds in enumerate(excursion_ranges) if bounds[0]>math.pi),None)
        if above is not None:
            below=[i for i in range(above) if excursion_ranges[i][1]<math.pi]
            if below:
                i=below[-1];ref=group[0]['command_v']
                spread_bracket={'rp_command_delta_bracket_v':sorted([abs(group[i]['command_v']-ref),abs(group[above]['command_v']-ref)]),
                                'points':[group[i]['point'],group[above]['point']],
                                'endpoint_excursion_ranges_rad':[excursion_ranges[i],excursion_ranges[above]]}
        brackets.append({'cycle':cycle,'direction':direction,'reference_command_v':group[0]['command_v'],
                         'reference_point':group[0]['point'],'phase_excursion_end_rad':float(change[-1]),
                         'crossings':matches,'observed_sample_spread_bracket':spread_bracket,'note':'Brackets from adjacent observed step medians; no interpolation. Includes drift and hysteresis.'})
    # Compare identical voltage settings without aligning, de-trending, or rotating curves.
    paired=[]
    index={(x['cycle'],x['direction'],round(x['command_v'],8)):x for x in summaries}
    for cycle in sorted({x['cycle'] for x in summaries}):
        for (_,direction,v),forward in list(index.items()):
            if forward['cycle']!=cycle or direction!='forward':continue
            reverse=index.get((cycle,'reverse',v))
            if reverse:
                delta=math.atan2(math.sin(reverse['phase_median_rad']-forward['phase_median_rad']),math.cos(reverse['phase_median_rad']-forward['phase_median_rad']))
                paired.append({'cycle':cycle,'command_v':v,'reverse_minus_forward_phase_rad':delta,
                               'forward_point':forward['point'],'reverse_point':reverse['point']})
    timestamps=number(rows,'pax_timestamp') if rows else np.array([])
    return {'method':'Raw records; step medians and extrema; atan2 and wrap bookkeeping only. No fitting, interpolation, smoothing, or gain conversion.',
            'rows':len(rows),'unique_pax_timestamps':len(np.unique(timestamps)),
            'voltage_basis':'RP requested command volts. Physical MDT input and piezo terminal voltages are not established by internal digital traces.',
            'step_summaries':summaries,'pi_crossing_brackets':brackets,'forward_reverse_differences':paired}


def create_report(directory):
    directory=Path(directory)
    run=json.loads((directory/'run.json').read_text())
    with (directory/'data.csv').open(newline='') as handle:rows=list(csv.DictReader(handle))
    analysis=analyze(rows)
    write_json(directory/'raw-analysis.json',analysis)
    if analysis['step_summaries']:
        with (directory/'step-summary.csv').open('w',newline='') as handle:
            writer=csv.DictWriter(handle,fieldnames=list(analysis['step_summaries'][0]));writer.writeheader();writer.writerows(analysis['step_summaries'])
    p=run['parameters']
    hold=p.get('hold_voltage')
    temporary=directory/'report.tmp.pdf'
    with PdfPages(temporary,metadata={'Title':'Raw phi1 voltage calibration','Author':'Polarization diagnostics'}) as document:
        pdf=ReportPages(document)
        text_page(pdf,'Raw phi1 voltage calibration',[
            ('Run',f"{directory.name}\nStatus: {run['status']}; {len(rows)} individual PAX readings"),
            ('Measurement',f"Fixed command {hold} V; {p.get('hold_points')} blocks of {p.get('samples')} readings. Command written only on initial change.") if hold is not None else ('Measurement',f"{p.get('start')} to {p.get('stop')} RP command V, step {p.get('step')} V; {p.get('repeats')} forward/reverse cycles.\nSettling: {p.get('settle')} s; {p.get('samples')} consecutive readings per held level."),
            ('Explicit voltage levels',p['levels']) if p.get('levels') else ('Voltage grid','Single fixed command.' if hold is not None else 'Uniform grid specified above.'),
            ('Physical setup',p.get('pax_location','not recorded')+'\n'+p.get('input_connections','not recorded')),
            ('What is recorded','Requested RP voltage, ASG register readback, full FPGA OUT1 scope trace, explicitly selected IN1/IN2 scope traces, raw PAX records and acquisition times.'),
            ('What is not established','Internal OUT1 data is the signal before the DAC. It is not an independent BNC or piezo-terminal voltage measurement. No historical driver gain is applied.'),
            ('Analysis policy','No curve fits, interpolated V_pi, drift subtraction, or optimized coordinate rotation. Phase is atan2(S3,-S2); unwrapping only adds 2pi at coordinate wraps. Step medians are labelled summaries; all individual measurements remain in data.csv and pax_raw.jsonl.')]
            + [('Data validity note',note) for note in run.get('quality_notes',[])])
        if rows:
            fig,axes=plt.subplots(2,2,figsize=(11.7,8.3));fig.subplots_adjust(left=.08,right=.97,bottom=.12,top=.87,hspace=.42,wspace=.3)
            fig.suptitle('Individual PAX readings at held voltages',fontsize=18,weight='bold',color=NAVY,y=.96)
            grouped=defaultdict(list)
            for row in rows:grouped[(row['cycle'],row['direction'])].append(row)
            for n,(key,group) in enumerate(grouped.items()):
                label=f'Cycle {key[0]} {key[1]}';color=f'C{n%10}'
                v=number(group,'requested_rp_v');s1=number(group,'pax_s1');s2=number(group,'pax_s2');s3=number(group,'pax_s3')
                axes[0,0].scatter(v,s1,s=9,alpha=.65,color=color,label=label)
                axes[0,1].scatter(s2,s3,s=9,alpha=.65,color=color,label=label)
                axes[1,0].scatter(v,number(group,'pax_dop'),s=9,alpha=.65,color=color)
                phases=np.unwrap(number(group,'equatorial_phase_rad'))
                axes[1,1].scatter(v,phases-phases[0],s=9,alpha=.65,color=color)
            axes[0,0].axhline(0,color='black',ls='--',lw=1)
            axes[0,0].set(title='Departure from the S1 = 0 plane',xlabel='Requested RP command (V)',ylabel='S1')
            angle=np.linspace(0,2*np.pi,361);axes[0,1].plot(np.cos(angle),np.sin(angle),'k--',lw=.8,label='Ideal S1=0 reference')
            axes[0,1].set(title='Raw orientation trajectory',xlabel='S2',ylabel='S3',aspect='equal')
            axes[1,0].set(title='Raw PAX DOP',xlabel='Requested RP command (V)',ylabel='DOP')
            axes[1,1].set(title='Measured phase change from each pass start',xlabel='Requested RP command (V)',ylabel='Equatorial phase change (rad)')
            axes[0,0].legend(fontsize=7,ncol=2)
            for ax in axes.flat:ax.grid(alpha=.2)
            fig.text(.08,.04,'Orientation Stokes are unit-normalized from PAX angles; DOP is shown separately. No fitted or corrected trajectories.',fontsize=9)
            pdf.savefig(fig);plt.close(fig)
            if hold is not None:
                elapsed=number(rows,'host_before_s')
                elapsed-=elapsed[0]
                phase=np.unwrap(number(rows,'equatorial_phase_rad'))
                fig,axes=plt.subplots(2,2,figsize=(11.7,8.3))
                fig.subplots_adjust(left=.08,right=.97,bottom=.12,top=.87,hspace=.42,wspace=.3)
                fig.suptitle(f'Fixed {hold:g} V command: individual measurements over time',fontsize=17,weight='bold',color=NAVY,y=.96)
                axes[0,0].plot(elapsed,phase-phase[0],'.',ms=3)
                axes[0,0].set(title='Equatorial phase relative to first reading',ylabel='Phase change (rad)')
                axes[0,1].plot(elapsed,number(rows,'pax_s1'),'.',ms=3)
                axes[0,1].axhline(0,color='black',ls='--',lw=.8)
                axes[0,1].set(title='Departure from S1 = 0',ylabel='S1')
                axes[1,0].plot(elapsed,number(rows,'pax_dop'),'.',ms=3)
                axes[1,0].set(title='Degree of polarization',ylabel='DOP')
                axes[1,1].plot(elapsed,number(rows,'pax_ptotal')*1e3,'.',ms=3)
                axes[1,1].set(title='Total optical power',ylabel='Power (mW)')
                for ax in axes.flat:ax.set_xlabel('Time since first recorded reading (s)');ax.grid(alpha=.2)
                fig.text(.08,.04,'No command changes between blocks. Scatter includes settling, drift and measurement variation; none is subtracted.',fontsize=9)
                pdf.savefig(fig);plt.close(fig)
            fig,axes=plt.subplots(2,2,figsize=(11.7,8.3));fig.subplots_adjust(left=.08,right=.97,bottom=.1,top=.88,hspace=.48,wspace=.3)
            fig.suptitle('Readbacks and direct return-path comparisons',fontsize=18,weight='bold',color=NAVY,y=.96)
            v=number(rows,'requested_rp_v');offset=number(rows,'asg_offset_readback_v')
            digital=number(rows,'digital_out1_scope_median_v') if 'digital_out1_scope_median_v' in rows[0] else np.full(len(rows),np.nan)
            axes[0,0].plot(v,offset-v,'.',ms=3,label='ASG register - requested');axes[0,0].plot(v,digital-v,'.',ms=3,label='Saved digital scope median - requested')
            axes[0,0].set(title='Software command versus internal readbacks',xlabel='Requested RP command (V)',ylabel='Difference (V)');axes[0,0].legend(fontsize=7)
            for key in ('scope_analog_in1_median_v','scope_analog_in2_median_v'):
                if key in rows[0]:axes[0,1].plot(v,number(rows,key),'.',ms=3,label=key)
            axes[0,1].set(title='Explicitly routed analog input scope records',xlabel='Requested RP command (V)',ylabel='ADC-reported input (V)')
            if axes[0,1].lines:axes[0,1].legend(fontsize=7)
            for cycle in sorted({x['cycle'] for x in analysis['forward_reverse_differences']}):
                items=[x for x in analysis['forward_reverse_differences'] if x['cycle']==cycle]
                axes[1,0].plot([x['command_v'] for x in items],[x['reverse_minus_forward_phase_rad'] for x in items],'o',ms=3,label=f'Cycle {cycle}')
            axes[1,0].axhline(0,color='black',ls='--',lw=.8);axes[1,0].set(title='Same-voltage return difference: drift + hysteresis',xlabel='Requested RP command (V)',ylabel='Reverse - forward phase (rad)')
            if analysis['forward_reverse_differences']:axes[1,0].legend(fontsize=7)
            axes[1,1].plot(number(rows,'host_before_s'),number(rows,'pax_s1'),'.',ms=3,label='S1');axes[1,1].set(title='Time record — no drift subtraction',xlabel='Host elapsed time (s)',ylabel='S1')
            for ax in axes.flat:ax.grid(alpha=.2)
            pdf.savefig(fig);plt.close(fig)
        sections=[]
        for item in analysis['pi_crossing_brackets']:
            text='No pi crossing bracket observed in this pass.'
            if item['crossings']:
                text='; '.join(f"ΔRP command {x['rp_command_delta_bracket_v'][0]:.4f}–{x['rp_command_delta_bracket_v'][1]:.4f} V; observed excursion {x['phase_excursion_at_bracket_rad'][0]:.3f}–{x['phase_excursion_at_bracket_rad'][1]:.3f} rad" for x in item['crossings'])
            if item.get('observed_sample_spread_bracket'):
                lo,hi=item['observed_sample_spread_bracket']['rp_command_delta_bracket_v']
                text+=f'\nBracket including all observed reference/endpoint sample spreads: {lo:.4f}–{hi:.4f} V.'
            sections.append((f"Cycle {item['cycle']} {item['direction']} — reference {item['reference_command_v']:.3f} V",text))
        if hold is None:
            sections.append(('Interpretation','Each interval brackets pi between adjacent measured step medians; no interpolation is applied. Drift, hysteresis and finite step size remain in the result. These are RP command-voltage brackets for projected equatorial phase, not calibrated terminal volts or an independent measurement of actuator retardance. Departure from S1=0 limits the ideal-model interpretation.'))
            text_page(pdf,'Observed pi-crossing brackets',sections)
        text_page(pdf,'Active acquisition parameters',[(key,str(value)) for key,value in p.items()]+[('PAX settings',f"Wavelength {run['configuration']['pax_wavelength_nm']} nm; daemon mode {run['configuration']['pax_measurement_mode']}; configured rotation {run['configuration']['pax_rotation_velocity_hz']} Hz"),('Cleanup evidence',str(run.get('cleanup_digital_output_medians_v',run.get('cleanup_errors','not available'))))])
    temporary.replace(directory/'report.pdf')
    print(f'Raw report: {directory / "report.pdf"}')
    return analysis


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory',type=Path)
    args=parser.parse_args();create_report(args.directory)

if __name__=='__main__':main()
