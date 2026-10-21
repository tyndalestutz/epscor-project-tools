"""Small explicit models. Every estimate remains separate from retained ADC samples."""
import numpy as np


def cosine_fit(voltage,signal,max_cycles=6.,max_residual_fraction=.1,scale_uncertainty=0.):
    x,y=np.asarray(voltage,float),np.asarray(signal,float)
    unavailable=lambda why: {'status':'UNAVAILABLE','reason':why}
    if len(x)<12 or not np.isfinite(x).all() or not np.isfinite(y).all() or np.ptp(x)<=0 or np.ptp(y)<=0:
        return unavailable('Need >=12 finite, distinct-span voltage/intensity observations; no invalid observations removed')
    origin=float(np.mean(x));x=x-origin;span=np.ptp(x)
    upper=min(max_cycles,(len(np.unique(x))-1)/4)
    if upper<=.125: return unavailable('Insufficient voltage sampling')
    ks=np.linspace(.25*np.pi/span,2*np.pi*upper/span,512)
    def solve(k):
        design=np.column_stack([np.ones(len(x)),np.cos(k*x),np.sin(k*x)])
        coefficients=np.linalg.lstsq(design,y,rcond=None)[0]
        residual=y-design@coefficients
        return float(residual@residual),coefficients
    losses=np.array([solve(k)[0] for k in ks])
    minima=[i for i in range(1,len(ks)-1) if losses[i]<=losses[i-1] and losses[i]<=losses[i+1]]
    if not minima: return unavailable('Optimum at search boundary; fringe scale not identified')
    candidates=[]
    for index in minima:
        lo,hi=ks[index-1],ks[index+1]
        for _ in range(45):
            a=hi-(hi-lo)*.61803398875;b=lo+(hi-lo)*.61803398875
            if solve(a)[0]<solve(b)[0]: hi=b
            else: lo=a
        k=(lo+hi)/2;loss,co=solve(k);candidates.append((loss,k,co))
    candidates.sort(key=lambda item:item[0]);loss,k,co=candidates[0]
    if len(candidates)>1 and candidates[1][0]<=loss+max(loss/max(1,len(x)-4)*4,1e-12*np.var(y)*len(y)):
        return unavailable('Multiple indistinguishable fringe solutions within declared search; alias/phase ambiguity')
    offset=float(co[0]);amplitude=float(np.hypot(co[1],co[2]));phase=float(np.arctan2(-co[2],co[1]))
    residual=y-(offset+amplitude*np.cos(phase+k*x))
    rms=float(np.sqrt(np.mean(residual**2)));phase_span=float(k*span)
    jac=np.column_stack([np.ones(len(x)),np.cos(phase+k*x),-amplitude*np.sin(phase+k*x),-amplitude*x*np.sin(phase+k*x)])
    if np.linalg.matrix_rank(jac)<4 or np.linalg.cond(jac)>1e8 or phase_span<np.pi/2:
        return unavailable('Partial fringe parameters unidentifiable / ill-conditioned')
    covariance=np.linalg.inv(jac.T@jac)*loss/(len(x)-4)
    kse=float(np.sqrt(max(0,covariance[3,3])))
    if kse/abs(k)>.25 or rms/max(amplitude,1e-30)>max_residual_fraction:
        return unavailable('Cosine residuals or phase-sensitivity uncertainty too large for a defensible Vpi')
    vpi=float(np.pi/abs(k));fit_se=float(np.pi*kse/k**2)
    extrema=resolved_extrema(x+origin,y,rms)
    # A peak and adjacent valley resolve a half-wave; a full 2pi period is unnecessary.
    pairs=[dict(left=a,right=b,spacing_v=abs(b['voltage']-a['voltage']))
           for a,b in zip(extrema,extrema[1:])
           if a['kind']!=b['kind'] and abs(a['signal']-b['signal'])>=max(.75*float(np.ptp(y)),8*rms)]
    observed=bool(pairs)
    return dict(status='OBSERVED_FRINGE_MODEL' if observed else 'MODEL_BASED_EXTRAPOLATION',
        vpi=vpi,vpi_fit_standard_error=fit_se,vpi_monitor_scale_standard_uncertainty=abs(vpi*scale_uncertainty),
        vpi_combined_standard_uncertainty=float(np.hypot(fit_se,vpi*scale_uncertainty)),
        uncertainty_method='Linearized least-squares residual covariance, independent equal-variance point errors; monitor gain uncertainty added in quadrature. Correlated errors, monitor-path dynamics and model bias not covered.',
        offset=offset,amplitude=amplitude,phase_at_center_rad=phase,voltage_center=origin,k_rad_per_v=float(k),
        phase_span_rad=phase_span,residual_rms=rms,visibility_voltage_proxy=amplitude/offset if offset>0 else None,
        visibility_limitation='PD-voltage modulation proxy, not calibrated optical visibility without independent dark/background and detector calibration.',
        extrema_method='Unsmoothed observations; reversal excursion >= max(20% observed PD span, 4 residual RMS); opposite extrema must differ by >=max(75% observed PD span, 8 residual RMS). Endpoints excluded; neighbor brackets are sampling neighborhoods, not confidence bounds.',
        observed_neighbor_extrema=extrema,resolved_peak_valley_pairs=pairs,extrema_separations=[p['spacing_v'] for p in pairs],
        residuals=residual.tolist(),search_cycles=[.125,float(upper)],
        scope='Cosine transfer model in the declared voltage domain. Vpi is pi/abs(k); not a voltage calibration or a claim of intrinsic crystal behavior.')


def resolved_extrema(voltage,signal,noise):
    """Prominent opposite turns in recorded observations; never smooth or fit their locations."""
    order=np.argsort(voltage,kind='stable');x=np.asarray(voltage)[order];y=np.asarray(signal)[order]
    threshold=max(.2*float(np.ptp(y)),4*noise)
    if threshold<=0 or len(x)<3 or np.any(np.diff(x)<=0): return []
    direction=0;low=high=0;turns=[]
    for i in range(1,len(y)):
        if direction==0:
            if y[i]<y[low]: low=i
            if y[i]>y[high]: high=i
            if y[i]-y[low]>=threshold: direction=1;high=i
            elif y[high]-y[i]>=threshold: direction=-1;low=i
        elif direction==1:
            if y[i]>=y[high]: high=i
            elif y[high]-y[i]>=threshold:
                if high>0: turns.append((high,'peak'))
                direction=-1;low=i
        else:
            if y[i]<=y[low]: low=i
            elif y[i]-y[low]>=threshold:
                if low>0: turns.append((low,'valley'))
                direction=1;high=i
    return [dict(voltage=float(x[i]),signal=float(y[i]),kind=kind,
                 voltage_bracket=[float(x[i-1]),float(x[i+1])]) for i,kind in turns]


def demodulate(values,dt,frequency,min_cycles=5.,max_harmonic=2):
    y=np.asarray(values,float);n=len(y)
    if n<16 or not np.isfinite(y).all() or not np.isfinite([dt,frequency]).all() or dt<=0 or frequency<=0 or n*dt*frequency<min_cycles or frequency*dt>=.4:
        return {'status':'UNAVAILABLE','reason':'Insufficient finite samples, cycles or conservative Nyquist coverage'}
    t=np.arange(n)*dt;phase=2*np.pi*frequency*t
    orders=[h for h in range(1,max_harmonic+1) if h*frequency*dt<.4]
    design=np.column_stack([np.ones(n),(t-t.mean())/(n*dt)]+[v for h in orders for v in (np.cos(h*phase),np.sin(h*phase))])
    if np.linalg.cond(design)>1e6: return {'status':'UNAVAILABLE','reason':'Ill-conditioned coherent demodulation'}
    co=np.linalg.lstsq(design,y,rcond=None)[0];residual=y-design@co
    components={h:complex(co[2+2*i],-co[3+2*i]) for i,h in enumerate(orders)}
    fundamental=components[1];harmonic=components.get(2);noise=float(np.sqrt(np.mean(residual**2)))
    return dict(status='AVAILABLE',mean=float(co[0]),sample_mean_v=float(y.mean()),linear_trend_v_over_capture=float(co[1]),
                fundamental=[fundamental.real,fundamental.imag],amplitude=float(abs(fundamental)),phase_rad=float(np.angle(fundamental)),
                second_harmonic_amplitude=float(abs(harmonic)) if harmonic is not None else None,
                harmonic_ratio=float(abs(harmonic)/abs(fundamental)) if harmonic is not None and abs(fundamental)>0 else None,
                harmonics={str(h):dict(complex=[v.real,v.imag],amplitude_v=float(abs(v)),ratio=float(abs(v)/abs(fundamental)) if abs(fundamental)>0 else None) for h,v in components.items()},
                resolved_harmonics=orders,largest_residual_sample=int(np.argmax(abs(residual))),residual_rms=noise,snr_rms=float(abs(fundamental)/(np.sqrt(2)*max(noise,1e-30))),
                convention='Coherent DC+linear trend+resolved harmonic least squares; Re(H exp(+i h 2pi f t)), peak amplitudes; harmonics only below 0.4 sample rate. SNR=fundamental RMS/full-model residual RMS, not a noise significance or intrinsic bandwidth.')


def command_shape(phase,waveform):
    """Ideal shape in a fundamental-cosine phase convention; absolute ASG phase is unknown."""
    return np.cos(phase) if waveform=='sin' else 2/np.pi*np.arcsin(np.clip(np.cos(phase),-1,1))


def waveform_diagnostics(values,dt,frequency,waveform,min_cycles=5.):
    result=demodulate(values,dt,frequency,min_cycles,max_harmonic=7)
    if result['status']!='AVAILABLE' or result['amplitude']<=0: return result
    amplitude=result['amplitude'];phase=result['phase_rad'];t=np.arange(len(values))*dt
    factor=1. if waveform=='sin' else 8/np.pi**2
    expected=result['mean']+result['linear_trend_v_over_capture']*(t-t.mean())/(len(t)*dt)+amplitude/factor*command_shape(2*np.pi*frequency*t+phase,waveform)
    harmonics=result['harmonics'];errors=[]
    for order in result['resolved_harmonics'][1:]:
        ratio=1/order**2 if waveform=='triangle' and order%2 else 0.
        expected_h=amplitude*ratio*np.exp(1j*order*phase)
        errors.append(abs(complex(*harmonics[str(order)]['complex'])-expected_h)**2)
    result.update(resolved_thd=float(np.sqrt(sum(h['amplitude_v']**2 for k,h in harmonics.items() if k!='1'))/amplitude),
        expected_resolved_thd=float(np.sqrt(sum(1/h**4 for h in result['resolved_harmonics'][1:] if h%2))) if waveform=='triangle' else 0.,
        harmonic_shape_error=float(np.sqrt(sum(errors))/amplitude),
        largest_shape_residual_sample=int(np.argmax(abs(np.asarray(values)-expected))),
        shape_residual_rms_v=float(np.sqrt(np.mean((np.asarray(values)-expected)**2))),
        shape_residual_fraction=float(np.sqrt(np.mean((np.asarray(values)-expected)**2))/(amplitude/np.sqrt(2))),
        shape_model='Ideal '+waveform+' with measured fitted fundamental phase/amplitude, DC and trend. Derived comparison only; phase matching is not command timing. Triangle odd harmonics are inherent; rounding/phase dispersion, noise, aliasing and faults can all contribute to shape residual.')
    return result


def triangle_branches(voltage,cycles,expected_cycles=None):
    """Identify complete rise/fall pairs inside this buffer only; never bridge captures."""
    x=np.asarray(voltage,float)
    if len(x)<32 or not np.isfinite(x).all() or np.ptp(x)<=0:
        return [],'Missing/flat monitor; continuous direction/cycles unavailable'
    # Derivative deadband is explicit; applies to cycle identification only, not data/fit values.
    delta=np.diff(x);threshold=np.ptp(x)*1e-5
    significant=np.flatnonzero(np.abs(delta)>threshold)
    if not len(significant): return [],'No monitor direction evidence'
    signs=np.sign(delta[significant]);changes=significant[1:][np.diff(signs)!=0]+1
    boundaries=changes.tolist();result=[]
    # Reject noisy direction switching rather than silently smoothing the monitor.
    allowed=np.ceil(expected_cycles) if expected_cycles is not None else cycles+1
    if len(boundaries)>2*(allowed+2): return [],'Monitor turning points noisy/ambiguous; no smoothing applied'
    local_cycle=0
    for i in range(0,len(boundaries)-2,2):
        a,b,c=boundaries[i:i+3]
        if min(b-a,c-b)<8: return [],'Triangle half-cycle underresolved'
        for left,right in ((a,b),(b,c)):
            result.append((local_cycle,'forward' if x[right]>x[left] else 'reverse',left,right+1))
        local_cycle+=1
    if local_cycle<cycles: return [],'Requested complete triangle cycles not identifiable inside one contiguous buffer'
    return result,None


def bins(x,y,count):
    """Explicit voltage-bin arithmetic means; empty bins omitted with counts retained."""
    x,y=np.asarray(x),np.asarray(y);edges=np.linspace(x.min(),x.max(),count+1)
    index=np.minimum(np.searchsorted(edges,x,side='right')-1,count-1)
    records=[]
    for i in range(count):
        selected=index==i
        if selected.any(): records.append(dict(voltage=float(x[selected].mean()),signal=float(y[selected].mean()),count=int(selected.sum()),bin=i))
    return records
