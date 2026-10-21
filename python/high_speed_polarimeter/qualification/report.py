"""Compact plots/PDF, reusing the established report page/footer conventions."""
import numpy as np
from ..io import read_json
from .analyze import analyze,captures
from .config import QualificationConfig


def create_report(directory):
    from polarization_locking.reports.run_report import plt,PdfPages,ReportPages,text_page,NAVY,TEAL
    result=analyze(directory);recipe=read_json(directory/'recipe.json');c=recipe['config']
    lines=['# EOM PD/monitor qualification',f"Mode: {result['mode']}; acquisition: {result['acquisition_mode']}; calibration: {result['calibration_status']}",
           f"Execution: {result['execution_status']}; cleanup: {result['cleanup_status']}",
           '## Setup',f"EOM: {c['eom_identity'] or 'unconfirmed'}; amplifier: {c['amplifier_identity'] or 'unconfirmed'}; wavelength {c['wavelength_nm']} nm.",
           'Optics declared: one EOM; input and analyzer each 45° to eigenaxes; PBS and PD retained. Software cannot infer the arrangement.',
           f"RP {c['rp_output']}; PD {c['pd_input']}; monitor {c['monitor_input']}. ADC ranges ±{c['pd_full_scale_v']}, ±{c['monitor_full_scale_v']} V.",
           f"Chain: {c['voltage_chain']}; TEC: {c['tec_conditions'] or 'not recorded'}.",
           f"Monitor scale {c['monitor_scale_v_per_v']} V/V; polarity {c['monitor_polarity']}; zero {c['monitor_zero_v']} V; relative gain uncertainty {c['monitor_scale_rel_uncertainty']}.",
           f"Declared RP envelope {list(QualificationConfig(**c).envelope())} command V; permitted RP bounds [{c['command_min_v']}, {c['command_max_v']}] V.",
           f"Declared amplifier-input limits [{c['amplifier_input_min_v']}, {c['amplifier_input_max_v']}] V; electrode limits [{c['electrode_min_v']}, {c['electrode_max_v']}] V; operator limits confirmation: {c['limits_confirmed']}. Predictions are not measured voltage.",
           f"Run comment: {recipe.get('comment','')}", '## Findings / flags',', '.join(result['flags']) or 'No automatic flags (not proof of calibration validity).']
    if len(result['groups'])<=8:
        for group in result['groups']:
            fit=group['fit'];label=f"cycle {group['cycle']} / {group['direction']} / {group['domain']}"
            if fit['status']=='UNAVAILABLE': lines.append(label+': Vpi unavailable — '+fit['reason'])
            else: lines.append(f"{label}: {fit['status']}; Vpi {fit['vpi']:.6g} ± {fit['vpi_combined_standard_uncertainty']:.3g} (conditional standard uncertainty); PD-voltage visibility proxy {format(fit['visibility_voltage_proxy'],'.4g') if fit['visibility_voltage_proxy'] is not None else 'unavailable'}; residual RMS {fit['residual_rms']:.4g} PD V. No optical visibility calibration.")
    else:
        lines.append('Each half-cycle fitted independently. Individual fits/uncertainties/extrema/bin counts retained in analysis.json; compact descriptive ranges below, not a merged fit.')
    lines+=['## Repeatability / direction differences']
    for domain,summary in result['repeatability'].items():
        if not summary['total_groups']: continue
        lines.append(f"{domain}: {summary['supported_groups']}/{summary['total_groups']} supported branches; Vpi range {summary['vpi_range']}; sample SD {summary['vpi_sample_std']}. Direction differences: {summary.get('forward_reverse_vpi_difference',[])}. Drift/settling confounded.")
        trends=summary['parameter_trends']
        if trends:
            lines.append(f"{domain} fitted drift ranges relative to each direction's first supported cycle: "+'; '.join(f"{key}: {min(r['difference_from_first_cycle'][key] for r in trends):.4g} to {max(r['difference_from_first_cycle'][key] for r in trends):.4g}" for key in ('offset','amplitude','k_rad_per_v'))+'. Descriptive fit changes, not a drift correction.')
    if result['fitted_direction_differences']:
        lines.append(f"Maximum absolute fitted reverse/forward phase difference at common overlapping voltage: {max(abs(r['reverse_minus_forward_phase_rad']) for r in result['fitted_direction_differences']):.4g} rad; model-dependent and includes settling/drift.")
    differences=result['same_command_direction_differences']
    if differences: lines.append(f'Maximum absolute same-command PD reverse/forward difference: {max(abs(r["pd_reverse_minus_forward_v"]) for r in differences):.5g} V. Actual electrode voltages may differ; includes drift/settling.')
    for r in result['frequency_repeatability']: lines.append(f"{r['frequency_hz']:g} Hz repeatability: magnitude mean {r.get('normalized_magnitude_mean')}, sample SD {r.get('normalized_magnitude_sample_std')}; phase circular SD {r.get('phase_circular_std_deg')} deg; {r['total_captures']}/{r['expected_captures']} captures.")
    lines+=['## Interpretation and provenance',result['limits'],
            'Raw signed codes are in dual_raw.npy; byte offsets/hashes and common-clock settings are in events.jsonl; acquisition.json records capture-time limitations. All fitting/binning/demodulation is derived in analysis.json.',
            'Absolute ASG-command-to-monitor phase is unavailable: no acquired ASG reference. PD/monitor relative phase uses simultaneous channels.']
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
        text_page(pdf,'EOM electro-optic qualification',[(heading,'\n'.join(block)) for heading,block in _sections(lines)])
        fig,axes=plt.subplots(2,2,figsize=(11.7,8.3),constrained_layout=True)
        if c['mode']=='voltage_transfer':
            for group in result['groups']:
                x=np.asarray(group['voltage']);y=np.asarray(group['signal']);fit=group['fit'];label=f"{group['cycle']} {group['direction']} ({group['domain']})"
                # Keep different voltage domains on separate axes rather than overlay incompatible units.
                ax=axes[0,0] if group['domain']=='inferred electrode V' else axes[0,1]
                tag=f"cycle {group['cycle']} {group['direction']}"
                label=tag if tag not in ax.get_legend_handles_labels()[1] else None
                color=plt.get_cmap('tab10')(group['cycle']%10)
                ax.plot(x,y,'.'+(':' if group['direction']=='reverse' else '-'),ms=2,lw=.6,color=color,label=label)
                ax.set(xlabel=group['domain'],ylabel='PD voltage (V)',title='Measured transfer (point/bin means)')
                if fit['status']!='UNAVAILABLE':
                    predicted=fit['offset']+fit['amplitude']*np.cos(fit['phase_at_center_rad']+fit['k_rad_per_v']*(x-fit['voltage_center']))
                    ax.plot(x,predicted,'--',lw=.7,color='gray',label='cosine fit' if 'cosine fit' not in ax.get_legend_handles_labels()[1] else None)
            # Different-domain residuals require separate panels/units; use only the calibrated domain when present.
            calibrated=[g for g in result['groups'] if g['domain']=='inferred electrode V']
            selected=calibrated or [g for g in result['groups'] if g['domain']=='RP command V'] or result['groups']
            for g in selected:
                if g['fit']['status']!='UNAVAILABLE':
                    tag=f"{g['cycle']} {g['direction']}"
                    axes[1,0].plot(g['voltage'],g['fit']['residuals'],'.',ms=2,label=tag if tag not in axes[1,0].get_legend_handles_labels()[1] else None)
            axes[1,0].set(title='Cosine fit residuals',ylabel='PD residual (V)',xlabel=selected[0]['domain'] if selected else 'Voltage unavailable')
            axes[0,0].set(title='Monitor-derived electrode transfer',xlabel='Inferred electrode voltage (V)',ylabel='PD voltage (V)')
            if not calibrated: axes[0,0].text(.5,.5,'Electrode-voltage result unavailable',ha='center',transform=axes[0,0].transAxes,fontsize=9)
            _raw_panel(axes[1,1],directory,c)
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
        fig.suptitle(f"{c['eom_identity'] or 'EOM'} — {c['mode']} ({result['acquisition_mode']})",fontsize=16,color=NAVY)
        pdf.savefig(fig);plt.close(fig)
        if c['mode']=='frequency_response':
            fig,ax=plt.subplots(figsize=(11.7,4.5),constrained_layout=True);_raw_panel(ax,directory,c);pdf.savefig(fig);plt.close(fig)
    temporary.replace(directory/'report.pdf')
    (directory/'report.md').write_text('\n\n'.join(lines)+'\n')


def _raw_panel(ax,directory,c):
    for event,raw in captures(directory):
        if raw.ndim!=2 or raw.shape[0]!=2: continue
        dt=event['metadata']['settings']['sampling_time_s'];t=np.arange(raw.shape[1])*dt
        ax.plot(t,raw[0]/8192*c['pd_full_scale_v'],lw=.5,label='PD (V)')
        ax.plot(t,raw[1]/8192*c['monitor_full_scale_v'],lw=.5,label='Monitor ADC (V)' if c['monitor_available'] else 'Monitor input (unavailable)')
        ax.set(title='Returned paired raw buffer (see QC)',xlabel='Capture-relative time (s)',ylabel='ADC input voltage (V)')
        return
    ax.text(.5,.5,'No complete raw capture available',ha='center',transform=ax.transAxes)


def _sections(lines):
    heading='Run';block=[]
    for line in lines[1:]:
        if line.startswith('## '):
            yield heading,block;heading=line[3:];block=[]
        else: block.append(line)
    yield heading,block
