"""Bounded settled discovery and replayable, measurement-only extrema evidence.

No fit selects commands or certifies an observed fringe. All point summaries are
explicit buffer means; the signed source buffers remain in the existing stream.
"""
import numpy as np
from .math import resolved_extrema, cosine_fit


def point_summary(rows,c):
    """Repeatability is full-buffer variability, never an independent-sample SEM."""
    return dict(command_v=rows[0]['state']['offset'],signal=float(np.mean([r['pd_mean_v'] for r in rows])),
        monitor_v=float(np.mean([r['monitor_mean_v'] for r in rows])),
        electrode_v=float(np.mean([(r['monitor_mean_v']-c.monitor_zero_v)*c.monitor_scale_v_per_v*c.monitor_polarity for r in rows])),
        noise_v=max(r['pd_std_v'] for r in rows),
        voltage_variation_v=max(r['monitor_std_v'] for r in rows)*c.monitor_scale_v_per_v,
        repeat_span_v=float(np.ptp([r['pd_mean_v'] for r in rows])),captures=[r['capture'] for r in rows])


def stability(rows,c):
    reasons=[]
    for r in rows:
        if r.get('status')!='AVAILABLE': reasons.append(f"capture {r['capture']}: invalid buffer")
        elif r['pd_std_v']>c.adaptive_pd_std_max_v or r['monitor_std_v']>c.adaptive_monitor_std_max_v:
            reasons.append(f"capture {r['capture']}: within-buffer instability")
    if len(rows)>1 and all('pd_mean_v' in r for r in rows) and np.ptp([r['pd_mean_v'] for r in rows])>c.adaptive_pd_repeat_max_v:
        reasons.append('inter-capture PD inconsistency: '+str([r['capture'] for r in rows]))
    return reasons


def observed(points,c):
    """Prominent turns, sampled on both sides, independent of any cosine model.

    At least eight voltage intervals per half-wave, three samples beyond each
    turn, >=6x measured variability contrast; no smoothing or outlier exclusion.
    Brackets are sampling-resolution intervals, not confidence intervals.
    """
    p=sorted(points,key=lambda r:r['electrode_v'])
    if len(p)<17: return dict(extrema=[],pairs=[],full_wave=False)
    x=np.array([r['electrode_v'] for r in p]);y=np.array([r['signal'] for r in p])
    noise=max(max(r['noise_v'],r['repeat_span_v']) for r in p)
    prominence=max(c.adaptive_min_prominence_v,6*noise,4*c.pd_full_scale_v/8192)
    turns=resolved_extrema(x,y,prominence/4)
    extrema=[]
    for t in turns:
        i=int(np.argmin(abs(x-t['voltage'])))
        if i<3 or i>len(p)-4: continue
        # Include indistinguishable near-turn samples in the location bracket.
        left=right=i
        while left>0 and abs(y[left-1]-y[i])<=max(3*noise,2*c.pd_full_scale_v/8192): left-=1
        while right<len(p)-1 and abs(y[right+1]-y[i])<=max(3*noise,2*c.pd_full_scale_v/8192): right+=1
        if left==0 or right==len(p)-1: continue
        bracket=[float(x[left-1]),float(x[right+1])]
        extrema.append(dict(t,index=i,monitor_v=p[i]['monitor_v'],command_v=p[i]['command_v'],
            voltage_bracket=bracket,location_resolution_v=max(float(x[i])-bracket[0],bracket[1]-float(x[i])),
            voltage_variation_v=p[i]['voltage_variation_v'],captures=p[i]['captures']))
    pairs=[]
    for a,b in zip(extrema,extrema[1:]):
        if (a['kind']==b['kind'] or b['index']-a['index']<8 or abs(a['signal']-b['signal'])<prominence
            or a['voltage_bracket'][1]>=b['voltage_bracket'][0]): continue
        spacing=b['voltage']-a['voltage']
        section=slice(a['index'],b['index']+1)
        if (np.max(np.diff(x[section]))>spacing/4
            or np.max(np.abs(np.diff(y[section])))>.4*abs(a['signal']-b['signal'])):
            continue  # Undersampled voltage/optical jumps remain data, never direct fringe evidence.
        # Conservative resolution bound + observed buffer variation + 3x scale u;
        # not a statistical confidence interval and not conditional model-fit SE.
        uncertainty=a['location_resolution_v']+b['location_resolution_v']+a['voltage_variation_v']+b['voltage_variation_v']+3*spacing*c.monitor_scale_rel_uncertainty
        pairs.append(dict(kinds=[a['kind'],b['kind']],vpi_v=spacing,uncertainty_bound_v=uncertainty,
                          extrema=[a,b],captures=a['captures']+b['captures']))
    full=any(a['extrema'][1]['index']==b['extrema'][0]['index'] for a,b in zip(pairs,pairs[1:]))
    return dict(extrema=extrema,pairs=pairs,full_wave=full)


def sufficient(evidence,c):
    return bool(evidence['pairs']) and (not c.adaptive_full_wave or evidence['full_wave'])


def discovery(c,emit):
    """Feedback generator. Caller persists/checks each buffer before sending means."""
    values=list(np.linspace(min(c.start_command_v,c.stop_command_v),max(c.start_command_v,c.stop_command_v),c.points))
    step=values[1]-values[0];points=[];last=0.;extensions=0;blocked={}
    def state(v,phase,cycle,direction):
        return dict(cycle=cycle,direction=direction,phase=phase,waveform='dc',offset=float(v),amplitude=0.,frequency=None,captures=c.captures_per_point)
    for v in values:
        c.validate_dc_path(0.,last,float(v));last=float(v)
        points.append((yield state(v,'discovery',-1,'forward')))
    while not sufficient(observed(points,c),c):
        if extensions>=c.adaptive_max_extensions:
            emit('adaptive_stop',outcome='DISCOVERY_BUDGET_EXHAUSTED',reason='Configured extension count reached; optical range remains unresolved')
            return
        directions=([1,-1] if c.adaptive_direction=='both' else [1 if c.adaptive_direction=='positive' else -1])
        # Alternate edges without expanding any electrical bound.
        directions=sorted(directions,key=lambda d:(d in blocked, d!=(1 if extensions%2==0 else -1)))
        chosen=None
        for direction in directions:
            if direction in blocked: continue
            edge=max(values) if direction>0 else min(values)
            trial=[edge+direction*step*j for j in range(1,max(1,int(np.floor(c.adaptive_extension_v/step+1e-9)))+1)]
            safe=[]
            for v in trial:
                try: c.validate_dc_path(0.,last,float(v))
                except ValueError as exc:
                    blocked[direction]=str(exc)
                    emit('adaptive_extension_blocked',command_v=float(v),direction=direction,reason=str(exc));break
                safe.append(float(v))
            if safe: chosen=safe;break
        if chosen is None:
            emit('adaptive_stop',outcome='INSUFFICIENT_SAFE_VOLTAGE_RANGE',reason='All permitted extension directions blocked by fixed electrical bounds',blocked=blocked)
            return
        extensions+=1
        emit('adaptive_extension',commands_v=chosen,extension=extensions)
        for v in chosen:
            c.validate_dc_path(0.,last,v);last=v;values.append(v)
            points.append((yield state(v,'discovery',-1,'extension')))
    emit('adaptive_candidate',evidence=observed(points,c),command_range_v=[min(values),max(values)])
    # Validate the entire discovered interval, not a favorable subsection.
    reference_rows=None
    for cycle in range(c.repeats):
        for direction,branch in [('forward',sorted(values)),('reverse',sorted(values,reverse=True))]:
            rows=[]
            for v in branch:
                c.validate_dc_path(0.,last,v);last=v
                rows.append((yield state(v,'validation',cycle,direction)))
            evidence=observed(rows,c)
            emit('adaptive_branch',cycle=cycle,direction=direction,evidence=evidence)
            if reference_rows is not None:
                reference_evidence=observed(reference_rows,c)
                a,b=reference_evidence['extrema'],evidence['extrema']
                stable=([t['kind'] for t in a]==[t['kind'] for t in b] and all(
                    abs(x['voltage']-y['voltage'])<=x['location_resolution_v']+y['location_resolution_v']+x['voltage_variation_v']+y['voltage_variation_v']
                    for x,y in zip(a,b)))
                ref={r['command_v']:r for r in reference_rows}
                stable=stable and all(abs(r['signal']-ref[r['command_v']]['signal'])<=c.adaptive_pd_repeat_max_v for r in rows)
                if not stable:
                    emit('adaptive_stop',outcome='AMBIGUOUS_VALIDATION',reason=f'Cycle {cycle} {direction} extrema/PD repeatability failed against first branch; all captures retained')
                    return
            else: reference_rows=rows
            if not sufficient(evidence,c):
                emit('adaptive_stop',outcome='AMBIGUOUS_VALIDATION',reason=f'Extrema not resolved on cycle {cycle} {direction}; no exclusions')
                return
    emit('adaptive_stop',outcome='TARGET_ACQUIRED',reason='Completed requested forward/reverse validation; offline reproducibility checks determine observed status')


def summarize(entries,events,c,completed):
    groups={};faults=[]
    for r in entries: groups.setdefault((r['state'].get('phase'),r['state']['cycle'],r['state']['direction'],r['point']),[]).append(r)
    branches={};allpoints=[]
    for (phase,cycle,direction,point),rows in groups.items():
        reasons=stability(rows,c)
        reasons.extend(f"capture {r['capture']}: acquisition acceptance not validated" for r in rows if not r.get('safety_validated'))
        if len(rows)!=c.captures_per_point: reasons.append(f'point {point}: incomplete repeated captures')
        faults.extend(reasons)
        if any('pd_mean_v' not in r for r in rows): continue
        p=point_summary(rows,c);p.update(phase=phase,cycle=cycle,direction=direction,point=point,failed_checks=reasons)
        allpoints.append(p);branches.setdefault((phase,cycle,direction),[]).append(p)
    validation=[]
    for (phase,cycle,direction),points in branches.items():
        if phase=='validation': validation.append(dict(cycle=cycle,direction=direction,points=points,**observed(points,c)))
    stop=next((r for r in reversed(events) if r['kind']=='adaptive_stop'),dict(outcome='INTERRUPTED',reason='No completed discovery outcome recorded'))
    reference=validation[0]['extrema'] if validation else []
    displacements=[];matching=True
    for b in validation:
        if [t['kind'] for t in b['extrema']]!=[t['kind'] for t in reference]: matching=False;continue
        for i,(a,t) in enumerate(zip(reference,b['extrema'])):
            delta=t['voltage']-a['voltage'];tolerance=a['location_resolution_v']+t['location_resolution_v']+a['voltage_variation_v']+t['voltage_variation_v']
            displacements.append(dict(cycle=b['cycle'],direction=b['direction'],extremum=i,delta_v=delta,tolerance_v=tolerance))
            if abs(delta)>tolerance: matching=False
    peers={}
    for b in validation:
        for p in b['points']: peers.setdefault(p['command_v'],[]).append(p)
    for command,rows in peers.items():
        if np.ptp([p['signal'] for p in rows])>c.adaptive_pd_repeat_max_v:
            faults.append(f"Cycle/direction PD inconsistency at command {command:.6g} V, captures {[p['captures'] for p in rows]}")
    accepted=(completed and stop['outcome']=='TARGET_ACQUIRED' and len(validation)==2*c.repeats
              and all(sufficient(b,c) for b in validation) and matching and not faults)
    if not matching: faults.append('Extrema displacement/order exceeds sampling-resolution reproducibility tolerance')
    discovery_points=[p for p in allpoints if p['phase']=='discovery']
    fit=cosine_fit([p['command_v'] for p in discovery_points],[p['signal'] for p in discovery_points],c.fit_max_cycles,c.fit_max_residual_fraction)
    pairs=[p for b in validation for p in b['pairs']]
    branch_means=[float(np.mean([p['vpi_v'] for p in b['pairs']])) for b in validation if b['pairs']]
    comparisons=[]
    for cycle in range(c.repeats):
        f={p['command_v']:p for p in allpoints if p['phase']=='validation' and p['cycle']==cycle and p['direction']=='forward'}
        r={p['command_v']:p for p in allpoints if p['phase']=='validation' and p['cycle']==cycle and p['direction']=='reverse'}
        for v in sorted(f.keys()&r.keys()):
            comparisons.append(dict(cycle=cycle,command_v=v,pd_delta_v=r[v]['signal']-f[v]['signal'],monitor_delta_v=r[v]['monitor_v']-f[v]['monitor_v'],captures=f[v]['captures']+r[v]['captures']))
    verified=[r for r in entries if r.get('safety_validated') and 'monitor_range_v' in r]
    verified_ranges=None
    if verified:
        monitor_range=[min(r['monitor_range_v'][0] for r in verified),max(r['monitor_range_v'][1] for r in verified)]
        verified_ranges=dict(command_v=[min(r['state']['offset'] for r in verified),max(r['state']['offset'] for r in verified)],
            monitor_adc_v=monitor_range,electrode_v=sorted((v-c.monitor_zero_v)*c.monitor_scale_v_per_v*c.monitor_polarity for v in monitor_range))
    return dict(verified_ranges=verified_ranges,outcome=stop['outcome'],stop_reason=stop['reason'],classification='OBSERVED' if accepted else ('AMBIGUOUS' if pairs or faults else ('FITTED_EXTRAPOLATED' if fit['status']!='UNAVAILABLE' else 'UNAVAILABLE')),
        half_wave_observed=accepted,full_wave_observed=accepted and all(b['full_wave'] for b in validation),points=allpoints,branches=validation,
        extrema_displacements=displacements,direction_comparisons=comparisons,failed_checks=faults,
        vpi_v=float(np.mean([p['vpi_v'] for p in pairs])) if accepted else None,
        vpi_uncertainty_bound_v=max(p['uncertainty_bound_v'] for p in pairs) if accepted else None,
        vpi_between_branch_span_v=float(np.ptp(branch_means)) if branch_means else None,
        measured_monitor_range_v=[min(r['monitor_range_v'][0] for r in entries if 'monitor_range_v' in r),max(r['monitor_range_v'][1] for r in entries if 'monitor_range_v' in r)] if allpoints else None,
        command_range_v=[min(p['command_v'] for p in allpoints),max(p['command_v'] for p in allpoints)] if allpoints else None,
        model_guidance=dict(fit=fit,meaning='All discovery buffer means, command-domain cosine only; never authorizes commands or certifies a cycle.'),
        uncertainty='Conservative sampling location bounds + measured buffer voltage variability + 3x declared relative scale uncertainty; not a confidence interval. Between-branch variation reported separately. Unknown drift/model/systematic errors excluded.')
