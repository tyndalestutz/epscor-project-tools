"""One event log and append-only signed dual-channel raw stream."""
import hashlib
import json
import math
from ..acquisition.eom import json_evidence
from ..io import write_json


def states(c):
    import numpy as np
    for cycle in range(c.repeats):
        if c.mode=='frequency_response':
            for f in c.frequencies_hz:
                yield dict(cycle=cycle,direction='frequency',waveform=c.frequency_waveform,offset=c.bias_v,
                           amplitude=c.sine_amplitude_v,frequency=f,captures=c.captures_per_point)
        elif c.strategy=='triangle':
            yield dict(cycle=cycle,direction='continuous',waveform='triangle',offset=c.triangle_offset_v,
                       amplitude=c.triangle_amplitude_v,frequency=c.triangle_frequency_hz,captures=1)
        else:
            values=np.linspace(c.start_command_v,c.stop_command_v,c.points)
            for direction,branch in [('forward',values),('reverse',values[::-1])]:
                for value in branch:
                    yield dict(cycle=cycle,direction=direction,waveform='dc',offset=float(value),
                               amplitude=0.,frequency=None,captures=c.captures_per_point)


def validate_capture(raw,meta,c):
    import numpy as np
    before,after=meta['settings'],meta['settings_after']
    if raw.shape!=(2,16384) or raw.dtype.kind!='i' or not np.isfinite(raw).all() or np.any((raw < -8192)|(raw>8191)):
        raise RuntimeError('Missing, malformed or out-of-range signed dual-channel buffer')
    if before!=after or before['input1']!=c.pd_input or before['input2']!=c.monitor_input:
        raise RuntimeError('Effective scope settings/channel mapping changed')
    if (before['sample_count']!=16384 or before['fpga_average'] or before['trace_average']!=1
        or before['rolling_mode'] or before['trigger_source']!='immediately'
        or before['adc_bits']!=14 or before['sampling_time_s']<=0
        or before['full_scale_v']!=[c.pd_full_scale_v,c.monitor_full_scale_v]
        or not math.isclose(before['sampling_time_s'],8e-9*before['decimation'],rel_tol=1e-8)
        or not math.isclose(before['duration_s'],16384*before['sampling_time_s'],rel_tol=1e-8)):
        raise RuntimeError('Unsupported effective ADC format/rate/averaging/trigger')
    if not all(meta.get(k) is True for k in ('same_capture','frozen_verified','public_trace_agrees')):
        raise RuntimeError('Synchronized frozen capture not verified')
    if meta['received_s']<meta['requested_s']:
        raise RuntimeError('Reversed host acquisition bounds')
    clips=[int(np.sum(np.abs(channel.astype(np.int32))>=8190)) for channel in raw]
    if clips[0] or (c.monitor_available and clips[1]):
        raise RuntimeError(f'ADC clipping; both complete buffers retained (counts={clips})')
    return raw.astype(float)/8192*np.array([c.pd_full_scale_v,c.monitor_full_scale_v])[:,None]


def monitor_guard(volts,state,meta,c,hardware):
    import numpy as np
    if hardware and c.monitor_available:
        if not np.isfinite(volts[1]).all() or volts[1].min()<c.monitor_min_v or volts[1].max()>c.monitor_max_v:
            raise RuntimeError('Measured monitor ADC safety window exceeded; complete raw capture retained')
    if not (hardware and c.monitor_calibration_confirmed): return
    electrode=(volts[1]-c.monitor_zero_v)*c.monitor_scale_v_per_v*c.monitor_polarity
    # Expand measured extrema by declared monitor gain uncertainty before safety comparison.
    lower=electrode-np.abs(electrode)*c.monitor_scale_rel_uncertainty
    upper=electrode+np.abs(electrode)*c.monitor_scale_rel_uncertainty
    if lower.min()<c.electrode_min_v or upper.max()>c.electrode_max_v:
        raise RuntimeError('Measured electrode envelope/uncertainty exceeds confirmed limits; raw retained')
    expected=c.chain_gain_v_per_command_v*state['offset']+c.chain_offset_v
    finite_window=0.
    if state['frequency']:
        cycles=meta['settings']['duration_s']*state['frequency']
        expected_amplitude=abs(c.chain_gain_v_per_command_v*state['amplitude'])
        finite_window=expected_amplitude/cycles
        measured_amplitude=float(np.ptp(electrode))/2
        uncertainty=measured_amplitude*c.monitor_scale_rel_uncertainty
        resolution=2*c.monitor_full_scale_v/8192*c.monitor_scale_v_per_v
        if abs(measured_amplitude-expected_amplitude)>c.monitor_discrepancy_v+uncertainty+resolution:
            raise RuntimeError('Unexpected monitor AC amplitude/drive discrepancy; raw retained')
    if abs(float(electrode.mean())-expected)>c.monitor_discrepancy_v+finite_window:
        raise RuntimeError('Unexpected monitor DC offset/drive discrepancy; raw retained')


def acquire(session,recipe,directory):
    import numpy as np
    c=recipe.acquisition_config(); hardware=session.mode=='hardware'
    write_json(directory/'acquisition.json',dict(schema_version=2,experiment=recipe.experiment,
        mode=session.mode,hardware=json_evidence(session.describe()),timing_origin=session.origin(),
        raw_format='dual_raw.npy: appended np.save signed int16 arrays, shape (PD,monitor)xN; event byte ranges and hashes identify complete returned buffers.',
        sample_time='Within each frozen buffer only: sample index * effective sampling_time_s, relative to immediate capture trigger. Host request/receive times bound acquisition and transport, not individual sample epochs. No seamless time axis across buffers.',
        monitor_conversion='(monitor_ADC_volts - monitor_zero_v) * monitor_scale_v_per_v * monitor_polarity; requires explicit valid calibration.',
        command_waveform='Generator settings/readbacks only, not independently sampled. Any ideal command phase alignment to the monitor is presentation only; no command-to-monitor phase/delay measurement.',
        waveform_limits='Generator runs during settling and network gaps; triangle_cycles is a minimum recorded-cycle target, not a precise burst count. Only monitor-identified complete cycles inside each buffer are analyzed.'))
    captures=0
    with (directory/'events.jsonl').open('w') as events,(directory/'dual_raw.npy').open('wb') as stream:
        def emit(kind,**values):
            events.write(json.dumps(json_evidence(dict(kind=kind,elapsed_s=session.clock(),**values)),allow_nan=False)+'\n');events.flush()
        emit('acquisition_started')
        try:
            for point,state in enumerate(states(c)):
                emit('command_requested',point=point,**state)
                ack=session.configure(state['waveform'],state['offset'],state['amplitude'],state['frequency'])
                emit('command_completed',point=point,settings=ack)
                session.sleep(c.settle_s)
                for repeat in range(state['captures']):
                    emit('capture_requested',point=point,repeat=repeat)
                    raw,meta=session.capture()
                    if raw is None:
                        raise RuntimeError('Driver returned no ADC buffer')
                    raw=np.asarray(raw)
                    offset=stream.tell();np.save(stream,raw,allow_pickle=False);stream.flush()
                    emit('capture',point=point,repeat=repeat,state=state,asg_settings=ack,metadata=meta,
                         byte_offset=offset,byte_end=stream.tell(),raw_sha256=hashlib.sha256(raw.tobytes()).hexdigest())
                    volts=validate_capture(raw,meta,c)  # Persist first, including failed buffers.
                    if state['frequency']:
                        f=ack['frequency_hz'];dt=meta['settings']['sampling_time_s'];duration=meta['settings']['duration_s']
                        if f<=0 or dt*f>1/c.samples_per_cycle or duration*f<c.min_capture_cycles:
                            raise RuntimeError('Effective waveform/capture cannot resolve required cycles')
                        if c.mode=='voltage_transfer' and duration*f<c.triangle_cycles+1:
                            raise RuntimeError('Cannot associate requested complete triangle cycles inside one buffer')
                    monitor_guard(volts,state,meta,c,hardware)
                    captures+=1
                    emit('capture_validated',point=point,repeat=repeat)
                print(f'Qualification point {point+1}: {state["direction"]}, {state["frequency"] or state["offset"]:g}',flush=True)
            emit('acquisition_completed',captures=captures)
        except BaseException as exc:
            emit('acquisition_failed',error=f'{type(exc).__name__}: {exc}',completed_captures=captures)
            raise
    return {'captures':captures,'raw_channels':['pd','monitor'],'pax_required':False}
