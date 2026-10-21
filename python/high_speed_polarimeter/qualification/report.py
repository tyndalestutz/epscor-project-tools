"""Compact plots/PDF, reusing the established report page/footer conventions."""
import numpy as np
from ..io import read_json
from .analyze import analyze,captures
from .config import QualificationConfig


def create_report(directory):
    from polarization_locking.reports.run_report import plt,PdfPages,ReportPages,text_page,NAVY
    result=analyze(directory);recipe=read_json(directory/'recipe.json');c=recipe['config']
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
    if c['mode']=='frequency_response':
        lines+=['Frequency normalization: reference '+str(c['reference_frequency_hz'])+' Hz at RP bias '+str(c['bias_v'])+' V.',
                'Coherent least squares includes DC, linear trend, fundamental and 2f only below 0.4 sample rate. Peak amplitudes; SNR=fundamental RMS/residual RMS. Phase includes detector and monitor path; not intrinsic EOM delay.',
                'Operating slope '+('operator-confirmed, evidence '+c['slope_validation_run'] if c['operating_slope_confirmed'] else 'UNVERIFIED: response cannot establish intrinsic EO behavior.')]
    if c['mode']=='frequency_response':
        lines.append('## Demodulation diagnostics (ranges across captures/cycles, not averaged optical response)')
        for frequency in c['frequencies_hz']:
            records=[r for r in result['normalized_response'] if r['requested_frequency_hz']==frequency]
            diagnostics=[]
            for label,channel,key in [('PD 1f V','pd','amplitude'),('PD 2f V','pd','second_harmonic_amplitude'),('monitor 1f V','monitor','amplitude'),('PD SNR','pd','snr_rms'),('monitor SNR','monitor','snr_rms')]:
                values=[r[channel][key] for r in records if r[channel]['status']=='AVAILABLE' and r[channel].get(key) is not None]
                diagnostics.append(f'{label}: {min(values):.4g}–{max(values):.4g} ({len(values)}/{len(records)} available)' if values else label+': unavailable')
            lines.append(f'{frequency:g} Hz: '+'; '.join(diagnostics))
    temporary=directory/'report.tmp.pdf'
    with PdfPages(temporary) as document:
        pdf=ReportPages(document)
        sections=[(heading,'\n'.join(block)) for heading,block in _sections(lines)]
        text_page(pdf,'EOM electro-optic qualification',sections[:3])
        text_page(pdf,'Measured evidence and Vpi',sections[3:5])
        text_page(pdf,'Repeatability, setup and provenance',sections[5:])
        fig,axes=plt.subplots(2,2,figsize=(11.7,8.3),constrained_layout=True)
        if c['mode']=='voltage_transfer':
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
        else:
            records=result['normalized_response']
            for cycle in range(c['repeats']):
                selected=[r for r in records if r['cycle']==cycle]
                if not selected: continue
                for ax,key in ((axes[0,0],'normalized_magnitude'),(axes[0,1],'relative_phase_deg')):
                    valid=[r for r in selected if key in r]
                    if valid: ax.semilogx([r['frequency_hz'] for r in valid],[r[key] for r in valid],'.-',lw=.7,label=f'cycle {cycle}')
                available=[r for r in selected if r['monitor']['status']=='AVAILABLE']
                if available: axes[1,0].semilogx([r['frequency_hz'] for r in available],[r['monitor']['amplitude'] for r in available],'.-',lw=.7,label=f'monitor {cycle}')
                available=[r for r in selected if r['pd']['status']=='AVAILABLE' and r['pd']['harmonic_ratio'] is not None]
                if available: axes[1,1].semilogx([r['frequency_hz'] for r in available],[r['pd']['harmonic_ratio'] for r in available],'.-',lw=.7,label=f'PD 2f/f {cycle}')
            axes[0,0].set(title='Reference-normalized PD / monitor',xlabel='Frequency (Hz)',ylabel='Relative magnitude')
            axes[0,1].set(title='Reference-normalized system phase',xlabel='Frequency (Hz)',ylabel='Principal phase (deg)')
            axes[1,0].set(title='Measured monitor response',xlabel='Frequency (Hz)',ylabel='Monitor fundamental peak (ADC V)')
            axes[1,1].set(title='System harmonic diagnostic',xlabel='Frequency (Hz)',ylabel='PD second/fundamental peak ratio')
            # Keep tiny fixture/noise deviations in physical context; all measured values remain visible.
            axes[0,0].set_ylim(0,max([1.1]+[r.get('normalized_magnitude',0)*1.1 for r in records]))
            extent=max([5.]+[abs(r.get('relative_phase_deg',0))*1.1 for r in records])
            axes[0,1].set_ylim(-extent,extent)
            axes[1,0].set_ylim(0,max([1e-6]+[r['monitor'].get('amplitude',0)*1.1 for r in records]))
            axes[1,1].set_ylim(0,max([.01]+[(r['pd'].get('harmonic_ratio') or 0)*1.1 for r in records]))
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
