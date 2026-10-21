"""Offline diagnostic results from immutable paired ADC captures."""
import hashlib
import json
from pathlib import Path
import numpy as np
from ..config import load_recipe
from ..io import read_json,write_json
from .acquire import validate_capture
from .math import cosine_fit,demodulate,triangle_branches,bins


def captures(directory):
    """Load only arrays referenced by complete capture events; interrupted evidence stays visible."""
    events=directory/'events.jsonl';raw_path=directory/'dual_raw.npy'
    if not events.exists() or not raw_path.exists(): return
    with events.open() as log,raw_path.open('rb') as stream:
        for line in log:
            record=json.loads(line)
            if record['kind']!='capture': continue
            stream.seek(record['byte_offset']);raw=np.load(stream,allow_pickle=False)
            if stream.tell()!=record['byte_end'] or hashlib.sha256(raw.tobytes()).hexdigest()!=record['raw_sha256']:
                raise ValueError('Raw capture byte range/hash mismatch')
            yield record,raw


def analyze(directory):
    directory=Path(directory);recipe=load_recipe(directory/'recipe.json');c=recipe.acquisition_config();run=read_json(directory/'run.json')
    result=dict(schema_version=2,experiment=recipe.experiment,mode=c.mode,acquisition_mode=run['mode'],
        execution_status=run['status'],cleanup_status=run['cleanup_status'],
        source_hashes={name:hashlib.sha256((directory/name).read_bytes()).hexdigest() for name in ('recipe.json','acquisition.json','events.jsonl','dual_raw.npy') if (directory/name).exists()},
        evaluator_sources={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')},
        groups=[],captures=[],flags=[],normalized_response=[],repeatability={},
        limits='No PAX; PD is signed detector voltage, not calibrated optical intensity. Electrode volts are inferred only from an explicitly calibrated monitor. PD/monitor response includes analyzer slope, detector bandwidth and monitor path; not intrinsic crystal bandwidth. Gaps remain separate captures. Cosine uncertainty is conditional on its residual model; high-order aliases outside the recorded search/sampling cannot be excluded. No sample smoothing/outlier rejection.')
    if run['mode']!='hardware': result['flags'].append('SYNTHETIC_NOT_HARDWARE_VERIFIED')
    if run['status']!='COMPLETED' or run['cleanup_status']!='COMPLETED': result['flags'].append('INCOMPLETE_RUN_NO_VALID_CALIBRATION')
    if not c.monitor_available: result['flags'].append('MONITOR_UNAVAILABLE')
    if not c.monitor_calibration_confirmed: result['flags'].append('ELECTRODE_VOLTAGE_UNAVAILABLE')
    if c.mode=='frequency_response' and not c.operating_slope_confirmed: result['flags'].append('OPERATING_SLOPE_UNVERIFIED')
    validated=set()
    if (directory/'events.jsonl').exists():
        for line in (directory/'events.jsonl').read_text().splitlines():
            event=json.loads(line)
            if event['kind']=='capture_validated': validated.add((event['point'],event['repeat']))
    settled={};continuous=[];frequency_records=[]
    for number,(event,raw) in enumerate(captures(directory)):
        state=event['state'];meta=event['metadata'];entry=dict(capture=number,point=event['point'],repeat=event['repeat'],state=state,
            requested_s=meta.get('requested_s'),received_s=meta.get('received_s'),duration_s=meta.get('settings',{}).get('duration_s'),
            sampling_time_s=meta.get('settings',{}).get('sampling_time_s'),byte_offset=event['byte_offset'])
        result['captures'].append(entry)
        try: volts=validate_capture(raw,meta,c)
        except (RuntimeError,KeyError,ValueError,TypeError) as exc:
            entry.update(status='INVALID',reason=str(exc));result['flags'].append('INVALID_CAPTURE_RETAINED');continue
        dt=meta['settings']['sampling_time_s'];pd,monitor=volts
        if result['captures'][:-1] and result['captures'][-2]['received_s'] is not None:
            # This is a separation of host bounds, not an invented sample-clock gap estimate.
            previous=result['captures'][-2]
            entry['host_bound_separation_s']=entry['requested_s']-previous['received_s']
        safety_validated=(event['point'],event['repeat']) in validated
        if not safety_validated: result['flags'].append('UNVALIDATED_CAPTURE_NO_ELECTRODE_INFERENCE')
        electrode=(monitor-c.monitor_zero_v)*c.monitor_scale_v_per_v*c.monitor_polarity if c.monitor_calibration_confirmed and safety_validated else None
        steps=np.abs(np.diff(pd));typical=float(np.median(steps));lsb=c.pd_full_scale_v/8192
        threshold=max(.25*float(np.ptp(pd)),20*typical,4*lsb)
        entry['pd_neighbor_step_diagnostic']={'maximum_v':float(steps.max()),'threshold_v':threshold,
            'rule':'max(25% full capture span, 20x median absolute neighbor step, 4 LSB); diagnostic only, no sample removal.'}
        if steps.max()>threshold: result['flags'].append('POSSIBLE_ABRUPT_PD_DISCONTINUITY')
        entry.update(status='AVAILABLE',pd_range_v=[float(pd.min()),float(pd.max())],pd_mean_v=float(pd.mean()),pd_std_v=float(pd.std()),monitor_mean_v=float(monitor.mean()),monitor_std_v=float(monitor.std()),
                     monitor_range_v=[float(monitor.min()),float(monitor.max())],
                     electrode_range_v=[float(electrode.min()),float(electrode.max())] if electrode is not None else None)
        if c.mode=='frequency_response':
            f=event['asg_settings']['frequency_hz']
            p,m=demodulate(pd,dt,f,c.min_capture_cycles),demodulate(monitor,dt,f,c.min_capture_cycles)
            if not c.monitor_available: m={'status':'UNAVAILABLE','reason':'Monitor declared unavailable; raw input retained without interpreting as voltage evidence'}
            item=dict(capture=number,cycle=state['cycle'],frequency_hz=f,requested_frequency_hz=state['frequency'],pd=p,monitor=m,
                      voltage_domain='monitor ADC V',ratio=None,command_to_monitor_amplitude=None,
                      command_to_monitor_phase='UNAVAILABLE: no acquired ASG phase reference',flags=[])
            if c.monitor_available and m['status']=='AVAILABLE' and state['amplitude']>0:
                item['command_to_monitor_amplitude']=m['amplitude']/state['amplitude']
            if not c.monitor_available or p['status']!='AVAILABLE' or m['status']!='AVAILABLE' or min(p.get('snr_rms',0),m.get('snr_rms',0))<c.minimum_snr:
                item['flags'].append('RATIO_UNAVAILABLE_MONITOR_OR_PD_SNR')
            else:
                ratio=complex(*p['fundamental'])/complex(*m['fundamental'])
                item['ratio']=[ratio.real,ratio.imag]
                if electrode is not None:
                    ratio/=c.monitor_scale_v_per_v*c.monitor_polarity
                    item['electrode_ratio']=[ratio.real,ratio.imag]
            item['command_to_pd_amplitude']=p['amplitude']/state['amplitude'] if p['status']=='AVAILABLE' and state['amplitude']>0 else None
            frequency_records.append(item);entry['frequency_response']=item
        elif c.strategy=='settled':
            key=(state['cycle'],state['direction'])
            settled.setdefault(key,[]).append(dict(command_v=state['offset'],signal=float(pd.mean()),
                monitor_v=float(monitor.mean()) if c.monitor_available else None,
                electrode_v=float(electrode.mean()) if electrode is not None else None,capture=number))
        else:
            if not c.monitor_available:
                entry.update(status='INVALID',reason='Triangle needs a measured voltage monitor');continue
            coordinate=electrode if electrode is not None else monitor
            branches,why=triangle_branches(coordinate,c.triangle_cycles,meta['settings']['duration_s']*event['asg_settings']['frequency_hz'])
            if why:
                entry['association_error']=why;result['flags'].append('TRIANGLE_ASSOCIATION_UNAVAILABLE');continue
            # Explicit voltage binning within each observed half-cycle only; preserve original samples.
            for local,direction,start,end in branches:
                derived=bins(coordinate[start:end],pd[start:end],c.fit_bins)
                continuous.append((state['cycle'],number,local,direction,derived,start,end))
            entry['identified_cycles']=len(branches)//2
    for (cycle,direction),records in settled.items():
        # Repeated buffers at a setpoint are explicitly averaged, retaining capture IDs and counts.
        by_voltage={}
        for row in records: by_voltage.setdefault(row['command_v'],[]).append(row)
        points=[dict(command_v=key,signal=float(np.mean([r['signal'] for r in group])),
                     electrode_v=float(np.mean([r['electrode_v'] for r in group])) if c.monitor_calibration_confirmed and all(r['electrode_v'] is not None for r in group) else None,
                     monitor_v=float(np.mean([r['monitor_v'] for r in group])) if c.monitor_available else None,
                     captures=[r['capture'] for r in group]) for key,group in by_voltage.items()]
        for domain,key in [('RP command V','command_v'),('monitor ADC V','monitor_v'),('inferred electrode V','electrode_v')]:
            if key=='monitor_v' and not c.monitor_available: continue
            if key=='electrode_v' and (not c.monitor_calibration_confirmed or any(r['electrode_v'] is None for r in points)): continue
            x=[r[key] for r in points];y=[r['signal'] for r in points]
            fit=cosine_fit(x,y,c.fit_max_cycles,c.fit_max_residual_fraction,c.monitor_scale_rel_uncertainty if key=='electrode_v' else 0.)
            result['groups'].append(dict(cycle=cycle,direction=direction,domain=domain,points=points,
                voltage=x,signal=y,fit=fit,processing='Arithmetic mean of each complete buffer, then equal-weight mean of repeat buffers at the same commanded point; no raw samples removed.'))
    for cycle,number,local,direction,records,start,end in continuous:
        x=[r['voltage'] for r in records];y=[r['signal'] for r in records]
        domain='inferred electrode V' if c.monitor_calibration_confirmed and all((entry['point'],entry['repeat']) in validated for entry in result['captures'] if entry['capture']==number) else 'monitor ADC V'
        fit=cosine_fit(x,y,c.fit_max_cycles,c.fit_max_residual_fraction,c.monitor_scale_rel_uncertainty if domain=='inferred electrode V' else 0.)
        result['groups'].append(dict(cycle=cycle,capture=number,local_cycle=local,direction=direction,domain=domain,
            voltage=x,signal=y,bins=records,sample_range=[start,end],fit=fit,
            processing='Explicit equal-width measured-voltage bins within one identified monitor half-cycle; arithmetic bin means, counts retained. No binning across cycles/captures. Turning-point derivative deadband =1e-5 monitor span; noisy switching fails association.'))
    if frequency_records:
        for item in frequency_records:
            references=[r for r in frequency_records if r['cycle']==item['cycle'] and r['requested_frequency_hz']==c.reference_frequency_hz]
            if len(references)==c.captures_per_point and all(r['ratio'] is not None for r in references) and item['ratio'] is not None:
                reference=np.mean([complex(*r['ratio']) for r in references])
                if abs(reference)>0:
                    ratio=complex(*item['ratio'])/reference
                    item['normalized_magnitude']=float(abs(ratio));item['relative_phase_deg']=float(np.degrees(np.angle(ratio)))
                    item['normalization']='Complex ratio divided by equal-weight mean of ALL reference-frequency capture ratios in this cycle; no failed reference removed.'
            result['normalized_response'].append(item)
        result['frequency_reference']=dict(frequency_hz=c.reference_frequency_hz,bias_command_v=c.bias_v,
            electrical_bias='Measured DC monitor means recorded per capture; electrode bias only available with calibrated monitor.',
            phase='PD/monitor phase from simultaneous samples; reference-normalized principal phase, not unwrapped delay.')
    diagnose_settled(result,directory,c)
    result['same_command_direction_differences']=[]
    for cycle in range(c.repeats):
        forward=settled.get((cycle,'forward'),[]);reverse=settled.get((cycle,'reverse'),[])
        for command in sorted({r['command_v'] for r in forward}&{r['command_v'] for r in reverse}):
            f=[r for r in forward if r['command_v']==command];b=[r for r in reverse if r['command_v']==command]
            result['same_command_direction_differences'].append(dict(cycle=cycle,command_v=command,
                pd_reverse_minus_forward_v=float(np.mean([r['signal'] for r in b])-np.mean([r['signal'] for r in f])),
                captures=[r['capture'] for r in f+b],
                anomaly_associated=any(result['captures'][r['capture']].get('anomaly_flags') for r in f+b),
                monitor_reverse_minus_forward_v=float(np.mean([r['monitor_v'] for r in b])-np.mean([r['monitor_v'] for r in f])) if c.monitor_available else None,
                electrode_reverse_minus_forward_v=float(np.mean([r['electrode_v'] for r in b])-np.mean([r['electrode_v'] for r in f]))
                    if all(r['electrode_v'] is not None for r in f+b) else None,
                scope='At matched RP command, not necessarily matched delivered voltage; includes drift and settling.'))
    result['fitted_direction_differences']=[]
    paired={}
    for g in result['groups']:
        if g['fit']['status']=='UNAVAILABLE': continue
        key=(g['cycle'],g.get('capture'),g.get('local_cycle'),g['domain'])
        paired.setdefault(key,{})[g['direction']]=g
    for key,pair in paired.items():
        if set(pair)!={'forward','reverse'}: continue
        f,b=pair['forward'],pair['reverse'];lo=max(min(f['voltage']),min(b['voltage']));hi=min(max(f['voltage']),max(b['voltage']))
        if hi<=lo: continue
        middle=(lo+hi)/2
        phases=[g['fit']['phase_at_center_rad']+g['fit']['k_rad_per_v']*(middle-g['fit']['voltage_center']) for g in (f,b)]
        result['fitted_direction_differences'].append(dict(cycle=key[0],capture=key[1],local_cycle=key[2],domain=key[3],
            common_voltage=middle,reverse_minus_forward_phase_rad=float(np.angle(np.exp(1j*(phases[1]-phases[0])))),
            vpi_reverse_minus_forward=b['fit']['vpi']-f['fit']['vpi'],scope='Fitted phase at a common overlapping voltage, not measured retardance; drift/settling included.'))
    result['frequency_repeatability']=[]
    for frequency in c.frequencies_hz if c.mode=='frequency_response' else []:
        records=[r for r in frequency_records if r['requested_frequency_hz']==frequency]
        summary=dict(frequency_hz=frequency,total_captures=len(records),expected_captures=c.repeats*c.captures_per_point,
            scope='Equal-weight capture/cycle variability of normalized system response, not intrinsic EOM uncertainty.')
        if records and len(records)==summary['expected_captures'] and all('normalized_magnitude' in r for r in records):
            magnitudes=np.array([r['normalized_magnitude'] for r in records]);phases=np.radians([r['relative_phase_deg'] for r in records])
            length=float(abs(np.mean(np.exp(1j*phases))))
            summary.update(normalized_magnitude_mean=float(magnitudes.mean()),normalized_magnitude_sample_std=float(magnitudes.std(ddof=1)) if len(records)>1 else None,
                phase_circular_std_deg=float(np.degrees(np.sqrt(-2*np.log(min(1.,length))))) if length>1e-12 else None)
        else: summary['unavailable']='Incomplete or unusable reference/ratio records; no failed capture excluded from normalization.'
        result['frequency_repeatability'].append(summary)
    # Compare like domains/directions only. Differences include elapsed-time drift and settling.
    for domain in ('RP command V','inferred electrode V','monitor ADC V'):
        groups=[g for g in result['groups'] if g['domain']==domain]
        supported=[g for g in groups if g['fit']['status']!='UNAVAILABLE']
        values=[g['fit']['vpi'] for g in supported]
        result['repeatability'][domain]=dict(supported_groups=len(values),total_groups=len(groups),
            vpi_range=[min(values),max(values)] if values else None,
            vpi_sample_std=float(np.std(values,ddof=1)) if len(values)>1 else None,
            scope='Descriptive spread across explicitly listed branches/cycles; not intrinsic hysteresis or calibration uncertainty.')
        parameter_trends=[]
        for direction in ('forward','reverse'):
            baseline=None
            for cycle in range(c.repeats):
                matched=[g['fit'] for g in supported if g['cycle']==cycle and g['direction']==direction]
                if not matched: continue
                values={key:float(np.mean([fit[key] for fit in matched])) for key in ('offset','amplitude','k_rad_per_v')}
                if baseline is None: baseline=values
                parameter_trends.append(dict(cycle=cycle,direction=direction,groups=len(matched),means=values,
                    difference_from_first_cycle={key:values[key]-baseline[key] for key in values},
                    processing='Descriptive equal-weight means of independent fitted half-cycle parameters; no merged fit.'))
        result['repeatability'][domain]['parameter_trends']=parameter_trends
        for cycle in range(c.repeats):
            pair=[g for g in supported if g['cycle']==cycle and 'capture' not in g]
            if len(pair)==2:
                result['repeatability'][domain].setdefault('forward_reverse_vpi_difference',[]).append(dict(cycle=cycle,
                    delta=pair[1]['fit']['vpi']-pair[0]['fit']['vpi']))
    # Preserve requested gaps/anomalies and distinguish successful analysis from calibration validity.
    if any(g['fit']['status']=='MODEL_BASED_EXTRAPOLATION' for g in result['groups']): result['flags'].append('MODEL_EXTRAPOLATION_NOT_OBSERVED_VPI')
    if any(g['fit']['status']=='UNAVAILABLE' for g in result['groups']): result['flags'].append('UNSUPPORTED_BRANCH_FIT')
    if not any(g['fit']['status']!='UNAVAILABLE' for g in result['groups']) and c.mode=='voltage_transfer': result['flags'].append('VPI_UNAVAILABLE')
    if c.mode=='frequency_response' and any(item['flags'] for item in frequency_records): result['flags'].append('FREQUENCY_RATIO_UNAVAILABLE')
    result['flags']=list(dict.fromkeys(result['flags']))
    result['calibration_status']=('UNAVAILABLE_INCOMPLETE_RUN' if run['status']!='COMPLETED' or run['cleanup_status']!='COMPLETED' else
        ('NOT_HARDWARE_VERIFIED' if run['mode']!='hardware' else 'DIAGNOSTIC_ONLY_NOT_INDEPENDENTLY_VALIDATED'))
    result['qualification_summary']=qualification_summary(result,c)
    write_json(directory/'analysis.json',result)
    return result


def diagnose_settled(result,directory,c):
    """Measured-data screening only: flags never alter samples, branch means or fits."""
    rows=[r for r in result['captures'] if r['status']=='AVAILABLE']
    result['diagnostics']=dict(anomalous_captures=[],normal_capture=None,most_anomalous_capture=None,
        rule='Settled-only screening: PD SD > max(8x median capture SD, median+8x scaled MAD, 4 ADC LSB); '
             'same-command deviation > max(10% run PD-mean span, 8x peer scaled MAD, 8 LSB), >=2 peers. '
             'Collapse: below peer median minus max(20% run PD-mean span, 8x peer scaled MAD, 8 LSB) '
             'for >=5% of one buffer contiguously. No samples removed; thresholds are diagnostics, not fault probabilities. '
             'Peers include all other captures at the identical command, across directions/cycles. '
             'Host bounds do not establish continuity between captures.')
    if not rows: return
    result['diagnostics']['normal_capture']=rows[0]['capture']
    if c.mode!='voltage_transfer' or c.strategy!='settled': return
    stds=np.array([r['pd_std_v'] for r in rows]);median=float(np.median(stds));lsb=c.pd_full_scale_v/8192
    variation=max(8*median,median+8*1.4826*float(np.median(abs(stds-median))),4*lsb)
    span=float(np.ptp([r['pd_mean_v'] for r in rows]))
    by_command={}
    for r in rows: by_command.setdefault(r['state']['offset'],[]).append(r)
    raw_by_id=dict(enumerate(captures(directory)))
    for r in rows:
        r['anomaly_flags']=[];score=r['pd_std_v']/variation
        r['pd_variation_threshold_v']=variation
        if r['pd_std_v']>variation: r['anomaly_flags'].append('LARGE_WITHIN_BUFFER_VARIATION')
        peers=[p for p in by_command[r['state']['offset']] if p['capture']!=r['capture']]
        if len(peers)>=2:
            values=np.array([p['pd_mean_v'] for p in peers]);center=float(np.median(values))
            mad=1.4826*float(np.median(abs(values-center)))
            tolerance=max(.1*span,8*mad,8*lsb);delta=r['pd_mean_v']-center
            r['peer_comparison']=dict(captures=[p['capture'] for p in peers],pd_median_v=center,
                pd_difference_v=delta,threshold_v=tolerance,
                monitor_difference_v=r['monitor_mean_v']-float(np.median([p['monitor_mean_v'] for p in peers])) if c.monitor_available else None)
            score=max(score,abs(delta)/tolerance)
            if abs(delta)>tolerance: r['anomaly_flags'].append('INTER_CAPTURE_INCONSISTENCY')
            threshold=center-max(.2*span,8*mad,8*lsb)
            raw=raw_by_id[r['capture']][1];pd=raw[0].astype(float)*lsb;below=pd<threshold
            edges=np.diff(np.r_[False,below,False].astype(int));starts=np.flatnonzero(edges==1);ends=np.flatnonzero(edges==-1)
            if len(starts):
                index=int(np.argmax(ends-starts));a,b=int(starts[index]),int(ends[index]);fraction=(b-a)/len(pd)
                r['collapse_diagnostic']=dict(threshold_v=threshold,longest_sample_range=[a,b],
                    duration_s=(b-a)*r['sampling_time_s'],fraction=fraction,
                    capture_relative_start_s=a*r['sampling_time_s'])
                score=max(score,fraction/.05)
                if fraction>=.05: r['anomaly_flags'].append('EXTENDED_PD_COLLAPSE_RELATIVE_TO_PEERS')
        r['anomaly_score']=float(score)
        if r['anomaly_flags']: result['diagnostics']['anomalous_captures'].append(r['capture'])
    normal=[r for r in rows if not r['anomaly_flags']]
    result['diagnostics']['normal_capture']=min(normal,key=lambda r:(abs(r['pd_std_v']-median),r['capture']))['capture'] if normal else None
    if result['diagnostics']['anomalous_captures']:
        result['diagnostics']['most_anomalous_capture']=max(rows,key=lambda r:(r['anomaly_score'],-r['capture']))['capture']
        result['flags'].append('UNSTABLE_PD_CAPTURES_RETAINED')
    # Preserve all primary fits, but label every affected branch, even an apparently good cosine fit.
    for g in result['groups']:
        ids=[i for p in g.get('points',[]) for i in p['captures']]
        g['anomalous_captures']=[i for i in ids if i in result['diagnostics']['anomalous_captures']]
        g['stability']='FLAGGED' if g['anomalous_captures'] else 'NO_SCREENING_FLAG'
    result['cycle_comparisons']=[]
    for direction in ('forward','reverse'):
        for command,group in by_command.items():
            cycles={}
            for r in group:
                if r['state']['direction']==direction: cycles.setdefault(r['state']['cycle'],[]).append(r)
            if len(cycles)<2: continue
            first=min(cycles);base=cycles[first]
            for cycle,current in sorted(cycles.items()):
                if cycle==first: continue
                result['cycle_comparisons'].append(dict(direction=direction,command_v=command,reference_cycle=first,cycle=cycle,
                    captures=[r['capture'] for r in base+current],
                    pd_difference_v=float(np.mean([r['pd_mean_v'] for r in current])-np.mean([r['pd_mean_v'] for r in base])),
                    monitor_difference_v=float(np.mean([r['monitor_mean_v'] for r in current])-np.mean([r['monitor_mean_v'] for r in base])) if c.monitor_available else None,
                    anomaly_associated=any(r['anomaly_flags'] for r in base+current)))


def qualification_summary(result,c):
    """Answer only the physical questions this run can support; no automatic reference search."""
    groups=result['groups'];fits=[g for g in groups if g['fit']['status']!='UNAVAILABLE']
    observed=[g for g in fits if g['fit']['status']=='OBSERVED_FRINGE_MODEL']
    unstable=result['diagnostics']['anomalous_captures']
    complete=result['execution_status']==result['cleanup_status']=='COMPLETED'
    def item(question,status,evidence):
        return dict(question=question,status=status,evidence=evidence)
    vpi_evidence=f'{c.wavelength_nm:g} nm; '+('; '.join(
        f"{domain}: {s['supported_groups']}/{s['total_groups']} fits, range "+('–'.join(format(v,'.5g') for v in s['vpi_range']) if s['vpi_range'] else 'unavailable')
        for domain,s in result['repeatability'].items() if s['total_groups']) or 'no voltage-transfer branch fits')
    if not c.monitor_calibration_confirmed: vpi_evidence+='; electrode volts uncalibrated'
    vpi_evidence+=f'; {len(observed)} fits resolve adjacent extrema; others extrapolate'
    ratios=[r for r in result['normalized_response'] if 'normalized_magnitude' in r]
    comparisons=result.get('cycle_comparisons',[])
    return [
        item('Vpi at operating wavelength','NOT MEASURED' if c.mode!='voltage_transfer' else 'UNAVAILABLE' if not fits else 'SUPPORTED' if complete and c.monitor_calibration_confirmed and observed and len(observed)==len(groups) and not unstable else 'INCONCLUSIVE',vpi_evidence),
        item('Voltage-to-retardance linearity','INCONCLUSIVE' if fits else 'UNAVAILABLE',
             f'{len(fits)}/{len(groups)} accepted constant-k cosine fits; residuals test PD-transfer consistency only. PD cosine phase is ambiguous; it is not independently measured retardance.'),
        item('Cycle repeatability','SUPPORTED' if comparisons and not unstable and complete else 'INCONCLUSIVE' if comparisons else 'NOT MEASURED',
             f'{len(comparisons)} same-direction, same-command cycle comparisons; '+
             (f"median/maximum absolute PD change {np.median([abs(r['pd_difference_v']) for r in comparisons]):.5g}/{max(abs(r['pd_difference_v']) for r in comparisons):.5g} V (all pairs); " if comparisons else '')+
             f'flagged capture IDs {unstable}. Same command does not guarantee same measured voltage.'),
        item('Frequency response','SUPPORTED' if ratios and complete and c.operating_slope_confirmed and len(ratios)==len(result['normalized_response']) else 'INCONCLUSIVE' if c.mode=='frequency_response' else 'NOT MEASURED',
             f'{len(ratios)}/{len(result["normalized_response"])} reference-normalized PD/monitor ratios; operating slope verified: {c.operating_slope_confirmed}. Includes detector/analyzer/monitor paths.' if c.mode=='frequency_response' else 'Settled/triangle voltage transfer acquires no frequency-response sweep.'),
        item('Drift/hysteresis stability','INCONCLUSIVE' if groups else 'NOT MEASURED',
             f'Forward/reverse differences include settling, drift and voltage mismatch; unstable captures {unstable} remain in the primary result. No isolated intrinsic hysteresis measurement.'),
        item('Comparison with intact EOM','UNAVAILABLE','No explicit compatible intact-EOM reference data supplied; no unrelated runs searched.')]
