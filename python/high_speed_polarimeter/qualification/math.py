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
    order=np.argsort(x);sorted_y=y[order]
    # Extrema are raw neighboring observations, not smoothed/fitted extrema.
    turns=np.flatnonzero(np.diff(np.sign(np.diff(sorted_y)))!=0)+1
    extrema=[dict(voltage=float(x[order][i]+origin),signal=float(sorted_y[i])) for i in turns]
    observed=phase_span>=2*np.pi and len(extrema)>=2
    return dict(status='OBSERVED_FRINGE_MODEL' if observed else 'MODEL_BASED_EXTRAPOLATION',
        vpi=vpi,vpi_fit_standard_error=fit_se,vpi_monitor_scale_standard_uncertainty=abs(vpi*scale_uncertainty),
        vpi_combined_standard_uncertainty=float(np.hypot(fit_se,vpi*scale_uncertainty)),
        uncertainty_method='Linearized least-squares residual covariance, independent equal-variance point errors; monitor gain uncertainty added in quadrature. Correlated errors, monitor-path dynamics and model bias not covered.',
        offset=offset,amplitude=amplitude,phase_at_center_rad=phase,voltage_center=origin,k_rad_per_v=float(k),
        phase_span_rad=phase_span,residual_rms=rms,visibility_voltage_proxy=amplitude/offset if offset>0 else None,
        visibility_limitation='PD-voltage modulation proxy, not calibrated optical visibility without independent dark/background and detector calibration.',
        observed_neighbor_extrema=extrema,extrema_separations=[abs(extrema[i]['voltage']-extrema[i-1]['voltage']) for i in range(1,len(extrema))],
        residuals=residual.tolist(),search_cycles=[.125,float(upper)],
        scope='Cosine transfer model in the declared voltage domain. Vpi is pi/abs(k); not a voltage calibration or a claim of intrinsic crystal behavior.')


def demodulate(values,dt,frequency,min_cycles=5.):
    y=np.asarray(values,float);n=len(y)
    if n<16 or not np.isfinite(y).all() or dt<=0 or frequency<=0 or n*dt*frequency<min_cycles or frequency*dt>=.5:
        return {'status':'UNAVAILABLE','reason':'Insufficient finite samples, cycles or Nyquist coverage'}
    t=np.arange(n)*dt;phase=2*np.pi*frequency*t
    second=2*frequency*dt<.4
    design=np.column_stack([np.ones(n),(t-t.mean())/(n*dt),np.cos(phase),np.sin(phase)]+([np.cos(2*phase),np.sin(2*phase)] if second else []))
    if np.linalg.cond(design)>1e6: return {'status':'UNAVAILABLE','reason':'Ill-conditioned coherent demodulation'}
    co=np.linalg.lstsq(design,y,rcond=None)[0];residual=y-design@co
    fundamental=complex(co[2],-co[3]);harmonic=complex(co[4],-co[5]) if second else None
    noise=float(np.sqrt(np.mean(residual**2)))
    return dict(status='AVAILABLE',mean=float(co[0]),fundamental=[fundamental.real,fundamental.imag],
                amplitude=float(abs(fundamental)),phase_rad=float(np.angle(fundamental)),
                second_harmonic_amplitude=float(abs(harmonic)) if second else None,
                harmonic_ratio=float(abs(harmonic)/abs(fundamental)) if second and abs(fundamental)>0 else None,
                residual_rms=noise,snr_rms=float(abs(fundamental)/(np.sqrt(2)*max(noise,1e-30))),
                convention='y=mean+linear trend+Re(H exp(+i 2pi f t))+optional 2f; peak amplitudes. SNR=fundamental RMS/residual RMS, not a white-noise statistical significance.')


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
