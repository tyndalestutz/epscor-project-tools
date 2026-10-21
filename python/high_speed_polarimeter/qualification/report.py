"""Compact plots/PDF, reusing the established report page/footer conventions."""
import numpy as np
from ..io import read_json
from .analyze import analyze,captures
from .config import QualificationConfig


def create_report(directory):
    from polarization_locking.reports.run_report import plt,PdfPages,ReportPages,text_page,NAVY
    result=analyze(directory);recipe=read_json(directory/'recipe.json');c=recipe['config']
    if result['mode']=='frequency_response':
        _frequency_report(directory,result,QualificationConfig(**c))
        return
    lines=['# EOM PD/monitor qualification',
           f"Mode: {result['mode']}; acquisition: {result['acquisition_mode']}; wavelength {c['wavelength_nm']:g} nm.",
           f"Software execution: {result['execution_status']}; cleanup: {result['cleanup_status']}. These are not EOM PASS criteria.",
           '## Qualification summary']
    if result['acquisition_mode']!='hardware': lines.append('SYNTHETIC SOFTWARE FIXTURE: no physical EOM qualification established.')
    for row in result['qualification_summary']:
        lines.append(f"{row['question']} — {row['status']}: {row['evidence']}")
    lines+=['## Next measurements',
            'Investigate any flagged PD transient before intrinsic device claims; optical, detector/electrical and acquisition causes remain unresolved. Calibrate electrode volts; repeat fringes and fixed-command holds. Then use frequency mode at a verified slope and supply a compatible intact-EOM reference. RAM is outside this experiment.',
            '## Measured anomalies (zero-based capture / cycle IDs)',
            'Settled screening: SD > max(8x median SD, median+8 scaled MAD, 4 LSB); same-command peer deviation > max(10% PD span, 8 peer MAD, 8 LSB). Collapse = below peer baseline by >max(20% span, 8 MAD, 8 LSB) for >=5% of a buffer. Exact thresholds/peer IDs in analysis.json. No exclusions.']
    anomalous=result['diagnostics']['anomalous_captures']
    if not anomalous: lines.append('No settled screening flags; this does not establish absence of faults. Waveform runs are not screened with settled-state rules.')
    for number in anomalous:
        r=result['captures'][number];state=r['state'];collapse=r.get('collapse_diagnostic')
        lines.append(f"Capture {number}, cycle {state['cycle']} {state['direction']}, command {state['offset']:.6g} V; monitor mean {r['monitor_mean_v']:.6g} ADC V; PD min/max {r['pd_range_v'][0]:.6g}/{r['pd_range_v'][1]:.6g} V, SD {r['pd_std_v']:.4g} V; monitor SD {r['monitor_std_v']:.3g} V. "+', '.join(f.replace('LARGE_WITHIN_BUFFER_VARIATION','high SD').replace('INTER_CAPTURE_INCONSISTENCY','peer mismatch').replace('EXTENDED_PD_COLLAPSE_RELATIVE_TO_PEERS','extended collapse') for f in r['anomaly_flags'])+'.')
        if collapse and collapse['fraction']>=.05:
            lines.append(f"Capture {number}: PD below {collapse['threshold_v']:.5g} V for {collapse['duration_s']:.4g} s continuously, starting at buffer-relative {collapse['capture_relative_start_s']:.4g} s. Buffer gaps remain unobserved.")
    lines+=['## Vpi identifiability and uncertainty']
    for group in result['groups']:
        fit=group['fit'];label=f"{group['cycle']} {group['direction']} / {group['domain']}"
        if fit['status']=='UNAVAILABLE': lines.append(label+': unavailable — '+fit['reason'])
        else:
            spacing=fit['extrema_separations']
            lines.append(f"{label}: {'resolved fringe model' if spacing else 'EXTRAPOLATION'}; Vpi {fit['vpi']:.5g} +/- {fit['vpi_fit_standard_error']:.2g} fit SE; residual {fit['residual_rms']:.2g} PD V."+
                         (f" Sampled peak/valley spacings {', '.join(format(v,'.5g') for v in spacing)}." if spacing else '')+
                         (f" Unstable captures {group.get('anomalous_captures')}." if group.get('anomalous_captures') else ''))
    lines+=['Adjacent interior peak/valley spacing resolves a half-wave; a full period is unnecessary. Reversal excursion >=max(20% PD span, 4 residual RMS); opposite extrema must differ by >=max(75% span, 8 RMS). No smoothing; brackets are sample neighborhoods, not confidence bounds.',
            'Fit SE is conditional residual-covariance uncertainty, excluding drift, correlated errors and model bias. Electrode scale uncertainty is separate; unconfirmed scales are not applied.',
            '## Repeatability / direction differences (all measurements retained)']
    for domain,summary in result['repeatability'].items():
        if summary['total_groups']:
            lines.append(f"{domain}: {summary['supported_groups']}/{summary['total_groups']} accepted fits; Vpi sample SD "+(format(summary['vpi_sample_std'],'.4g') if summary['vpi_sample_std'] is not None else 'unavailable')+'. Branch/cycle spread, not intrinsic hysteresis or calibration uncertainty.')
    differences=result['same_command_direction_differences']
    if differences:
        worst=max(differences,key=lambda r:abs(r['pd_reverse_minus_forward_v']))
        lines.append(f"Same-command absolute PD differences: median {np.median([abs(r['pd_reverse_minus_forward_v']) for r in differences]):.5g} V (all pairs); maximum {abs(worst['pd_reverse_minus_forward_v']):.5g} V: cycle {worst['cycle']}, command {worst['command_v']:.6g} V, captures {worst['captures']}; monitor difference {format(worst['monitor_reverse_minus_forward_v'],'.4g') if worst['monitor_reverse_minus_forward_v'] is not None else 'unavailable'} ADC V; anomaly-associated: {worst['anomaly_associated']}. Includes drift/settling; a transient-dominated difference is not ordinary hysteresis evidence.")
    comparisons=result.get('cycle_comparisons',[])
    if comparisons:
        lines.append(f"Same-direction cycle comparisons: {len(comparisons)} matched commands, {sum(r['anomaly_associated'] for r in comparisons)} anomaly-associated. Median/maximum absolute PD differences {np.median([abs(r['pd_difference_v']) for r in comparisons]):.5g}/{max(abs(r['pd_difference_v']) for r in comparisons):.5g} V (all pairs). No anomaly-excluded result substitutes for the primary result.")
    for r in result['frequency_repeatability']: lines.append(f"{r['frequency_hz']:g} Hz: normalized magnitude mean {r.get('normalized_magnitude_mean')}, sample SD {r.get('normalized_magnitude_sample_std')}; phase circular SD {r.get('phase_circular_std_deg')} deg; {r['total_captures']}/{r['expected_captures']} captures.")
    lines+=['## Setup / limitations / provenance',
            f"EOM: {c['eom_identity'] or 'unconfirmed'}; amplifier: {c['amplifier_identity'] or 'unconfirmed'}; TEC: {c['tec_conditions'] or 'not recorded'}.",
            f"RP {c['rp_output']}; PD {c['pd_input']} +/-{c['pd_full_scale_v']} V; monitor {c['monitor_input']} +/-{c['monitor_full_scale_v']} V. ADC volts use declared full scales, not independent gain calibration.",
            f"Electrode calibration confirmed: {c['monitor_calibration_confirmed']}; scale {c['monitor_scale_v_per_v']} V/V, zero {c['monitor_zero_v']} V, polarity {c['monitor_polarity']}, relative scale uncertainty {c['monitor_scale_rel_uncertainty']}.",
            f"Command envelope {list(QualificationConfig(**c).envelope())} V; monitor stop window [{c.get('monitor_min_v',-1.)}, {c.get('monitor_max_v',1.)}] ADC V. Conditions/limits/declarations: recipe.json.",
            'PD is signed voltage, not calibrated intensity. PD/monitor response includes analyzer, detector and monitor paths; not intrinsic EOM bandwidth. Host request/receive bounds do not establish inter-buffer sample times.',
            'Flags: '+', '.join(result['flags']),
            'Source: '+directory.name+'. Paired codes: dual_raw.npy; capture IDs/settings/hashes: events.jsonl; source/evaluator hashes and all results: analysis.json. No smoothing, exclusions or gap interpolation.',
            'Settled raw selection: unflagged capture closest to median PD SD; worst = largest diagnostic score (earliest ties). Waveform reference = first validated buffer. Separate PD/monitor axes; all samples plotted.']
    temporary=directory/'report.tmp.pdf'
    with PdfPages(temporary) as document:
        pdf=ReportPages(document)
        sections=[(heading,'\n'.join(block)) for heading,block in _sections(lines)]
        text_page(pdf,'EOM electro-optic qualification',sections[:3])
        text_page(pdf,'Measured evidence and Vpi',sections[3:5])
        text_page(pdf,'Repeatability, setup and provenance',sections[5:])
        fig,axes=plt.subplots(2,2,figsize=(11.7,8.3),constrained_layout=True)
        for group in result['groups']:
            domain=group['domain']
            if domain=='inferred electrode V': continue
            ax=axes[0,0] if domain=='monitor ADC V' else axes[0,1]
            _transfer(ax,group,plt)
        axes[0,0].set(title='Measured monitor transfer',xlabel='Monitor ADC voltage (V)',ylabel='PD mean voltage (V)')
        axes[0,1].set(title='Command transfer',xlabel='RP command (V)',ylabel='PD mean voltage (V)')
        calibrated=[g for g in result['groups'] if g['domain']=='inferred electrode V']
        if calibrated:
            for group in calibrated: _transfer(axes[1,0],group,plt)
            axes[1,0].set(title='Calibrated monitor-derived electrode transfer',xlabel='Inferred electrode voltage (V)',ylabel='PD mean voltage (V)')
        else:
            selected=[g for g in result['groups'] if g['domain']=='monitor ADC V'] or result['groups']
            for g in selected:
                if g['fit']['status']!='UNAVAILABLE':
                    axes[1,0].plot(g['voltage'],g['fit']['residuals'],'.',ms=2,label=f"{g['cycle']} {g['direction']}")
            axes[1,0].set(title='Cosine residuals; electrode volts unavailable',xlabel=selected[0]['domain'] if selected else 'Voltage unavailable',ylabel='PD residual (V)')
        rows=[r for r in result['captures'] if r['status']=='AVAILABLE']
        if rows:
            ids=[r['capture'] for r in rows];means=np.array([r['pd_mean_v'] for r in rows])
            axes[1,1].vlines(ids,[r['pd_range_v'][0] for r in rows],[r['pd_range_v'][1] for r in rows],lw=.5,color='C0')
            axes[1,1].plot(ids,means,'.',ms=2,color='C0',label='Mean and raw min–max')
            flagged=[r for r in rows if r['capture'] in anomalous]
            axes[1,1].plot([r['capture'] for r in flagged],[r['pd_mean_v'] for r in flagged],'x',ms=4,color='crimson',label='Screening flags')
        axes[1,1].set(title='All captures; gaps not interpolated',xlabel='Capture ID (ordinal, not elapsed time)',ylabel='PD voltage (V)')
        for ax in axes.flat:
            if not ax.has_data() and not ax.texts: ax.text(.5,.5,'Unavailable; see flags',ha='center',transform=ax.transAxes,fontsize=9)
            ax.grid(alpha=.15)
            if ax.get_legend_handles_labels()[0]: ax.legend(fontsize=6)
            ax.tick_params(labelsize=8)
        fig.suptitle(f"EOM qualification — {c['mode'].replace('_', ' ')} ({result['acquisition_mode']})",fontsize=16,color=NAVY)
        pdf.savefig(fig);plt.close(fig)
        fig,raw_axes=plt.subplots(2,2,figsize=(11.7,8.3),constrained_layout=True)
        _raw_panel(raw_axes[0,0],directory,c,result['diagnostics']['normal_capture'],
                   'Representative normal' if c['mode']=='voltage_transfer' and c['strategy']=='settled' else 'Reference raw',raw_axes[1,0])
        _raw_panel(raw_axes[0,1],directory,c,result['diagnostics']['most_anomalous_capture'],'Most anomalous',raw_axes[1,1])
        for row in raw_axes:
            available=[ax for ax in row if ax.lines]
            if available:
                lo=min(float(ax.lines[0].get_ydata().min()) for ax in available);hi=max(float(ax.lines[0].get_ydata().max()) for ax in available)
                margin=max((hi-lo)*.05,4*c['pd_full_scale_v']/8192)
                for ax in available: ax.set_ylim(lo-margin,hi+margin)
        fig.suptitle('Paired raw evidence — all recorded samples, capture-relative time',color=NAVY,fontsize=14)
        pdf.savefig(fig);plt.close(fig)
    temporary.replace(directory/'report.pdf')
    (directory/'report.md').write_text('\n\n'.join(lines)+'\n')


def _transfer(ax,group,plt):
    x=np.asarray(group['voltage']);y=np.asarray(group['signal']);fit=group['fit']
    color=plt.get_cmap('tab10')(group['cycle']%10)
    ax.plot(x,y,'.'+(':' if group['direction']=='reverse' else '-'),ms=2,lw=.6,color=color,label=f"{group['cycle']} {group['direction']}")
    if fit['status']!='UNAVAILABLE':
        predicted=fit['offset']+fit['amplitude']*np.cos(fit['phase_at_center_rad']+fit['k_rad_per_v']*(x-fit['voltage_center']))
        ax.plot(x,predicted,'--',lw=.6,color='gray',label='Cosine model' if 'Cosine model' not in ax.get_legend_handles_labels()[1] else None)


def _raw_panel(ax,directory,c,number,label,monitor_ax):
    if number is not None:
        for i,(event,raw) in enumerate(captures(directory)):
            if i!=number: continue
            dt=event['metadata']['settings']['sampling_time_s'];t=np.arange(raw.shape[1])*dt
            ax.plot(t,raw[0]/8192*c['pd_full_scale_v'],lw=.45,color='#18354b',label='PD')
            monitor=monitor_ax
            monitor.plot(t,raw[1]/8192*c['monitor_full_scale_v'],lw=.35,alpha=.65,color='#007f86')
            monitor.set_ylabel('Monitor ADC (V)' if c['monitor_available'] else 'Unused monitor input (V)',color='#007f86',fontsize=8)
            monitor.tick_params(labelsize=8)
            monitor.set(title=f'Simultaneous monitor — capture {number}',xlabel='Buffer-relative time (s)')
            state=event['state']
            ax.set(title=f"{label}: capture {number}\nCycle {state['cycle']} {state['direction']}, command {state['offset']:.5g} V",xlabel='Buffer-relative time (s)',ylabel='PD voltage (V)')
            ax.tick_params(labelsize=8)
            return
    for target in (ax,monitor_ax):
        target.text(.5,.5,'No '+label.lower()+' buffer identified',ha='center',transform=target.transAxes,fontsize=9)


def _sections(lines):
    heading='Run';block=[]
    for line in lines[1:]:
        if line.startswith('## '):
            yield heading,block;heading=line[3:];block=[]
        else: block.append(line)
    yield heading,block


def _frequency_report(directory,result,c):
    from polarization_locking.reports.run_report import plt,PdfPages,ReportPages,text_page,NAVY
    records=result['normalized_response'];coverage=result['frequency_coverage']
    lines=['# EOM frequency waveform diagnostics',
        f"{directory.name}; {result['acquisition_mode']}; {c.wavelength_nm:g} nm; {c.frequency_waveform} drive.",
        f"Execution {result['execution_status']}; cleanup {result['cleanup_status']}. Software completion is not EOM qualification.",
        '## What was commanded and measured?',
        f"{c.rp_output}: bias {c.bias_v:g} V, peak amplitude {c.sine_amplitude_v:g} V (legacy sine_amplitude_v applies to both shapes). Actual generator readbacks remain associated with every paired capture.",
        f"PD {c.pd_input} +/-{c.pd_full_scale_v:g} V; monitor {c.monitor_input} +/-{c.monitor_full_scale_v:g} V. ADC volts use declared full scales. Electrode conversion confirmed: {c.monitor_calibration_confirmed}.",
        f"Reference {c.reference_frequency_hz:g} Hz; diagnostic {c.diagnostic_frequency_hz:g} Hz shown only if acquired. Raw example capture IDs: {[e['capture'] for e in result['waveform_examples']]}.",
        'Command A is reconstructed from readbacks, never measured. Phase alignment to monitor B is presentation only (arbitrary at low SNR), not a command phase/delay/polarity calibration.',
        'B/C share the ADC clock within each buffer. Bounded period windows center on the largest fitted residual; every sample in the window is plotted. Full buffers and gaps remain separate offline.',
        '## Electrical response before optical interpretation',
        'Harmonics through 7 only below 0.4 sample rate. Sine: shape change flags distortion; triangle: odd harmonics are inherent, and shape change can be rounding or distortion. Thresholds and selection rules are explicit in analysis.json.',
        'Gain = monitor fundamental peak ADC V / command fundamental peak V. Triangle command fundamental = 8/pi^2 times command peak; the legacy amplitude gain remains per command peak.',
        'DC(fit) is fitted DC; arithmetic sample mean can differ for noninteger periods. Shape error compares harmonics after fitting fundamental phase/gain; shape residual includes noise and unresolved content.',
        'PD harmonics with a clean monitor can reflect optical nonlinearity. Neither triangle rounding nor an amplitude peak identifies a faulty crystal or intrinsic EOM bandwidth.',
        '## Coverage and numerical evidence (all captures retained)']
    for row in coverage[:24]:
        selected=[r for r in records if r['requested_frequency_hz']==row['requested_frequency_hz']]
        fields=[]
        for label,channel,key in [('monitor DC(fit) V','monitor','mean'),('monitor 1f V','monitor','amplitude'),
                                  ('monitor THD','monitor','resolved_thd'),('shape RMS V','monitor','shape_residual_rms_v'),
                                  ('PD 1f V','pd','amplitude'),('PD 2f/f','pd','harmonic_ratio')]:
            values=[r[channel][key] for r in selected if r[channel]['status']=='AVAILABLE' and r[channel].get(key) is not None]
            fields.append(label+' '+('/'.join(format(v,'.4g') for v in (min(values),max(values))) if values else 'unavailable'))
        lines.append(f"{row['requested_frequency_hz']:g} Hz: {row['returned_captures']}/{row['expected_captures']} returned, {row['usable_captures']} reference-normalized; capture IDs {row['captures']}. "+'; '.join(fields)+'. Flags: '+(', '.join(row['flags']) or 'none')+'.')
    if len(coverage)>24: lines.append('Text capped at 24 frequencies; summary panels cover every requested frequency. All individual captures, settings, harmonic fits, SNR and flags remain available in analysis.json and raw event byte ranges.')
    lines+=['## Interpretation and provenance']
    for row in result['qualification_summary']: lines.append(f"{row['question']} — {row['status']}: {row['evidence']}")
    lines+=['No validated static cosine model is imported; static PD-waveform prediction is omitted. slope_validation_run records operator evidence only, not a validated prediction model.',
        'PD/monitor ratios include analyzer, detector, amplifier and monitor paths; no intrinsic crystal bandwidth or resonance attribution. Relative phase is principal PD-to-monitor phase from simultaneous samples; reference-normalized phase is also retained offline. No phase unwrapping or inferred timing delay.',
        'Source: recipe.json, acquisition.json, events.jsonl and immutable signed dual_raw.npy. Source/evaluator hashes and every frequency capture: analysis.json. Offline reevaluation needs no hardware. Flags: '+', '.join(result['flags'])]
    if result['acquisition_mode']!='hardware': lines.insert(1,'SYNTHETIC SOFTWARE FIXTURE — no physical validation.')
    temporary=directory/'report.tmp.pdf'
    with PdfPages(temporary) as document:
        pdf=ReportPages(document)
        sections=[(h,'\n'.join(b)) for h,b in _sections(lines)]
        # Put the bounded waveform evidence before detailed numerical discussion.
        text_page(pdf,'Frequency waveform diagnostics',sections[:3])
        _frequency_waveforms(pdf,directory,result,c)
        _frequency_summary(pdf,result,c)
        text_page(pdf,'Frequency evidence and limitations',sections[3:])
    temporary.replace(directory/'report.pdf')
    (directory/'report.md').write_text('\n\n'.join(lines)+'\n')


def _frequency_summary(pdf,result,c):
    from polarization_locking.reports.run_report import plt,NAVY
    records=result['normalized_response'];fig,axes=plt.subplots(2,3,figsize=(11.7,8.3),constrained_layout=True)
    panels=[(axes[0,0],'electrical_fundamental_gain_v_per_v','Electrical fundamental gain','Monitor ADC V / command fundamental V'),
            (axes[0,2],None,'Measured monitor DC','Monitor ADC V'),
            (axes[1,0],'normalized_magnitude','Optical-system modulation / reference','Normalized PD / monitor magnitude'),
            (axes[1,1],'pd_to_monitor_phase_deg','Simultaneous PD-to-monitor phase','Principal phase (deg)')]
    for ax,key,title,ylabel in panels:
        for cycle in range(c.repeats):
            selected=[r for r in records if r['cycle']==cycle and r['frequency_hz'] is not None]
            if key is None:
                valid=[r for r in selected if r['monitor']['status']=='AVAILABLE']
                if not valid: continue
                ax.semilogx([r['frequency_hz'] for r in valid],[r['monitor']['mean'] for r in valid],'.',label=f'{cycle} DC fit')
                ax.semilogx([r['frequency_hz'] for r in valid],[r['monitor']['sample_mean_v'] for r in valid],'+',ms=4,label=f'{cycle} sample mean')
            else:
                valid=[r for r in selected if key in r]
                if valid: ax.semilogx([r['frequency_hz'] for r in valid],[r[key] for r in valid],'.',label=f'cycle {cycle}')
        ax.set(title=title,ylabel=ylabel,xlabel='Generator readback frequency (Hz)')
    valid=[r for r in records if r['monitor']['status']=='AVAILABLE']
    for key,label,marker in [('resolved_thd','Resolved THD','.'),('harmonic_shape_error','Shape harmonic error','x')]:
        if valid: axes[0,1].semilogx([r['frequency_hz'] for r in valid],[r['monitor'].get(key,0) for r in valid],marker,ms=4,label=label)
    if valid:
        axes[0,1].semilogx([r['frequency_hz'] for r in valid],[r['monitor'].get('expected_resolved_thd',0) for r in valid],'_',label='Ideal shape THD')
    axes[0,1].set(title='Electrical shape / inherent harmonics',xlabel='Generator readback frequency (Hz)',ylabel='Ratio to monitor fundamental')
    rows=['Usable ratio','Electrical shape/gain/DC','PD 2f content','Ratio/ref unavailable','Invalid/missing']
    for row in result['frequency_coverage']:
        flags=row['flags'];frequency=row['requested_frequency_hz'];levels=[]
        if row['usable_captures']: levels.append(0)
        if any(f.startswith(('ELECTRICAL_','TRIANGLE_','MONITOR_')) for f in flags): levels.append(1)
        if any(f.startswith('PD_2F') for f in flags): levels.append(2)
        if any('RATIO_' in f or 'REFERENCE_' in f or 'COMMAND_' in f for f in flags): levels.append(3)
        if any(f in ('INVALID_CAPTURE','ADC_CLIPPING','MISSING_OR_EXTRA_CAPTURES') for f in flags): levels.append(4)
        axes[1,2].semilogx([frequency]*len(levels),levels,'x',ms=4,color=NAVY)
    axes[1,2].set(yticks=range(5),yticklabels=rows,title='Coverage and diagnostics (not pass/fail)',xlabel='Requested frequency (Hz)',ylim=(-.5,4.5))
    axes[0,0].set_ylim(0,max([1e-6]+[1.1*r.get('electrical_fundamental_gain_v_per_v',0) for r in records]))
    axes[1,0].set_ylim(0,max([1.1]+[1.1*r.get('normalized_magnitude',0) for r in records]))
    axes[0,1].set_ylim(0,max([.01]+[1.1*r['monitor'].get(key,0) for r in valid for key in ('resolved_thd','harmonic_shape_error','expected_resolved_thd')]))
    dc=[r['monitor'][key] for r in valid for key in ('mean','sample_mean_v')]
    if dc:
        lo,hi=min([0.]+dc),max([0.]+dc);margin=max(.05*(hi-lo),4*c.monitor_full_scale_v/8192)
        axes[0,2].set_ylim(lo-margin,hi+margin)
    axes[1,1].set_ylim(-185,185)
    for ax in axes.flat:
        if not ax.has_data(): ax.text(.5,.5,'Unavailable; see coverage',ha='center',transform=ax.transAxes,fontsize=8)
        ax.grid(alpha=.15);ax.tick_params(labelsize=7)
        if ax.get_legend_handles_labels()[0]: ax.legend(fontsize=6)
    fig.suptitle('Electrical chain first; PD response includes optical/detector/monitor paths',color=NAVY,fontsize=13)
    pdf.savefig(fig);plt.close(fig)


def _waveform_samples(raw,event,item,c):
    """Deterministic contiguous N-period window; original buffer-relative sample indices."""
    dt=event['metadata'].get('settings',{}).get('sampling_time_s');f=item['frequency_hz']
    if raw.ndim!=2 or raw.shape[0]!=2 or not np.isfinite(raw).all() or dt is None or f is None or not np.isfinite([dt,f]).all() or dt<=0 or f<=0: return None
    total=int(np.floor((raw.shape[1]-1)*dt*f));periods=min(c.waveform_plot_cycles,total)
    if periods<1: return None
    focus=item.get('waveform_focus_sample',int(np.argmax(abs(raw).max(axis=0))))
    first=max(0,min(int(focus*dt*f)-periods//2,total-periods))
    start=int(np.ceil(first/(dt*f)));stop=min(raw.shape[1],int(np.floor((first+periods)/(dt*f)))+1)
    return np.arange(start,stop),dt,periods


def _frequency_waveforms(pdf,directory,result,c):
    from polarization_locking.reports.run_report import plt,NAVY
    from .math import command_shape
    examples=result['waveform_examples'];ids={r['capture'] for r in examples}
    raw={i:pair for i,pair in enumerate(captures(directory)) if i in ids};records={r['capture']:r for r in result['normalized_response']}
    bounds=[[],[],[]]
    for example in examples:
        event,data=raw[example['capture']];item=records[example['capture']]
        if _waveform_samples(data,event,item,c) is None: continue
        ack=item['generator_readback']
        bounds[0].extend([ack['offset_v']-ack['amplitude_v'],ack['offset_v']+ack['amplitude_v']])
        bounds[1].extend([float(data[1].min())/8192*c.monitor_full_scale_v,float(data[1].max())/8192*c.monitor_full_scale_v])
        bounds[2].extend([float(data[0].min())/8192*c.pd_full_scale_v,float(data[0].max())/8192*c.pd_full_scale_v])
    for first in range(0,len(examples),2):
        selected=examples[first:first+2];fig,axes=plt.subplots(3,len(selected),squeeze=False,figsize=(11.7,8.3),constrained_layout=True)
        for column,example in enumerate(selected):
            number=example['capture'];event,data=raw[number];item=records[number];window=_waveform_samples(data,event,item,c)
            if window is None:
                for ax in axes[:,column]: ax.text(.5,.5,f'Capture {number}: no valid complete-period time window',ha='center',transform=ax.transAxes,fontsize=8)
                continue
            indices,dt,periods=window;t=indices*dt;f=item['frequency_hz'];units=1000. if f>=100 else 1.
            monitor=item['monitor'];aligned=monitor['status']=='AVAILABLE' and monitor['snr_rms']>=c.minimum_snr
            phase=monitor['phase_rad'] if aligned else 0.;ack=item['generator_readback']
            command=ack['offset_v']+ack['amplitude_v']*command_shape(2*np.pi*f*t+phase,item['waveform'])
            axes[0,column].plot(t*units,command,'--',lw=.7,color='gray')
            roles=', '.join(example['roles']).replace('largest diagnostic score; not a fault attribution','largest diagnostic score')
            axes[0,column].set(title=f"Capture {number}; {f:g} Hz; {roles}\nA — reconstructed {item['waveform']}; phase {'display-aligned' if aligned else 'arbitrary'}",ylabel='RP command setting (V)')
            axes[1,column].plot(t*units,data[1,indices]/8192*c.monitor_full_scale_v,lw=.5,color='#007f86')
            axes[1,column].set(title=f'B — measured {c.monitor_input} monitor' if c.monitor_available else f'B — unused {c.monitor_input} (monitor unavailable)',ylabel='Monitor ADC (V)')
            axes[2,column].plot(t*units,data[0,indices]/8192*c.pd_full_scale_v,lw=.5,color=NAVY)
            axes[2,column].set(title=f"C — measured {c.pd_input} PD; {item['status']}",ylabel='PD ADC (V)')
            for ax in axes[:,column]:
                ax.set_xlim(t[0]*units,t[-1]*units);ax.set_xlabel('Buffer-relative time ('+('ms' if units==1000 else 's')+')')
                ax.tick_params(labelsize=8);ax.grid(alpha=.12);ax.title.set_fontsize(9)
        # Common voltage scales across ALL selected examples/pages, without normalization.
        for row,values in zip(axes,bounds):
            if values:
                lo,hi=min(values),max(values);margin=max((hi-lo)*.05,1e-6)
                for ax in row: ax.set_ylim(lo-margin,hi+margin)
        fig.suptitle('Waveform evidence: reconstructed command A; simultaneous measured B/C\nCommand phase alignment is presentation only, not physical timing. Bounded periods shown; full raw buffers retained.',color=NAVY,fontsize=12)
        pdf.savefig(fig);plt.close(fig)
