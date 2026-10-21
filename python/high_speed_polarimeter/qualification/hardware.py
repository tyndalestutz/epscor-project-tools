"""Small two-channel extension; existing shared RP/PAX controllers stay unchanged."""
from datetime import datetime, timezone
import hashlib
import inspect
import time
from types import SimpleNamespace
import numpy as np


class QualificationSession:
    mode='hardware'

    def __init__(self, config):
        from polarization_locking.hardware.rp_interface import RPController
        self.config=config
        self.rp=RPController(SimpleNamespace(rp_hostname=config.rp_hostname,rp_config=config.rp_config,
            rp_output_min_voltage=0.,rp_output_max_voltage=1.))
        self.cleanup_evidence={}
        self._origin={'utc':datetime.now(timezone.utc).isoformat(),'monotonic_origin_s':time.monotonic(),'clock':'time.monotonic'}

    def clock(self): return time.monotonic()-self._origin['monotonic_origin_s']
    def origin(self): return dict(self._origin)
    def sleep(self, seconds): time.sleep(seconds)

    def connect(self):
        self.config.confirm_hardware()  # before constructing Pyrpl or applying its saved profile
        self.rp.connect()
        self.scope=self.rp.p.rp.scope
        self.asg=self.rp.asg1 if self.config.rp_output=='out1' else self.rp.asg2
        self.asg.output_direct='off'
        self._waveform='dc'
        for name in ('_rawdata_ch1','_rawdata_ch2','_write_pointer_trigger','_trigger_delay_register','_adc_we_keep'):
            # Do not read ADC buffers here; verify the inspected Scope API exists.
            if not hasattr(type(self.scope),name):
                raise RuntimeError(f'Unsupported Pyrpl Scope API: {name}')
        if self.scope.data_length!=16384:
            raise RuntimeError('This adapter requires the inspected 16384-sample Scope buffer')
        self.scope.setup(input1=self.config.pd_input,input2=self.config.monitor_input,
            average=False,trace_average=1,ch1_active=True,ch2_active=True,rolling_mode=False,
            trigger_source='immediately',trigger_delay=0.)
        if self.scope._adc_we_keep:
            raise RuntimeError('Continuous-write scope mode cannot provide a frozen synchronized buffer')

    def describe(self):
        source=inspect.getsourcefile(type(self.scope))
        from pathlib import Path
        return {'mode':self.mode,'rp_hostname':self.config.rp_hostname,'rp_config':self.config.rp_config,
                'pyrpl_scope_source':source,'pyrpl_scope_sha256':hashlib.sha256(Path(source).read_bytes()).hexdigest(),
                'synchronization':'One frozen Scope acquisition, two physical ADC inputs with common clock/trigger. Sequential network buffer reads after capture stops. Analog-channel delay is not calibrated.',
                'raw_format':'Signed 14-bit decimated codes; no FPGA or host averaging. Not continuous across captures.',
                'command_phase_reference':'Unavailable with PD+monitor channels; command-to-monitor amplitude only. PD/monitor relative phase uses the common ADC clock.'}

    def configure(self, waveform, offset, amplitude, frequency):
        c=self.config
        lo,hi=offset-amplitude,offset+amplitude
        if lo<c.command_min_v or hi>c.command_max_v:
            raise RuntimeError('Command envelope exceeds bounds')
        if waveform=='dc' and self._waveform=='dc':
            # Preserve the actual forward/reverse path: never return to zero between setpoints.
            start=float(self.asg.offset)
            self.asg.output_direct=c.rp_output
            count=max(1,int(round(c.ramp_s*20)))
            began=time.monotonic()
            for index in range(1,count+1):
                self.asg.offset=start+(offset-start)*index/count
                time.sleep(max(0,began+c.ramp_s*index/count-time.monotonic()))
        else:
            self.asg.output_direct='off'
            self.asg.setup(waveform='dc',offset=0.,amplitude=0.,trigger_source='immediately')
            self.asg.setup(waveform=waveform,offset=offset,amplitude=amplitude,
                           frequency=frequency or 1.,trigger_source='immediately')
        self._waveform=waveform
        # Settings are inspected before a newly configured waveform is routed.
        readback={'waveform':str(self.asg.waveform),'offset_v':float(self.asg.offset),
                  'amplitude_v':float(self.asg.amplitude),'frequency_hz':float(self.asg.frequency)}
        if readback['waveform']!=waveform or abs(readback['offset_v']-offset)>2/8192 or abs(readback['amplitude_v']-amplitude)>2/8192:
            raise RuntimeError(f'Unexpected ASG settings: {readback}')
        if not c.command_min_v<=readback['offset_v']-readback['amplitude_v']<=readback['offset_v']+readback['amplitude_v']<=c.command_max_v:
            raise RuntimeError('Effective ASG envelope outside declared bounds')
        if frequency and (not c.allowed_frequency_min_hz<=readback['frequency_hz']<=c.allowed_frequency_max_hz or abs(readback['frequency_hz']/frequency-1)>.001):
            raise RuntimeError('Effective generator frequency outside confirmed bounds/tolerance')
        dec=c.decimation(readback['frequency_hz'] if frequency else None)
        self.scope.decimation=dec
        settings=self.settings()
        if settings['fpga_average'] or settings['trace_average']!=1 or settings['duration_s']>=c.scope_timeout_s:
            raise RuntimeError('Unexpected scope averaging/duration')
        if frequency and (settings['sampling_time_s']*readback['frequency_hz']>1/c.samples_per_cycle or settings['duration_s']*readback['frequency_hz']<c.min_capture_cycles):
            raise RuntimeError('Effective scope rate/duration cannot resolve the requested waveform')
        self.asg.output_direct=c.rp_output
        return readback

    def settings(self):
        s=self.scope
        return dict(input1=str(s.input1),input2=str(s.input2),sample_count=int(s.data_length),
                    sampling_time_s=float(s.sampling_time),duration_s=float(s.duration),decimation=int(s.decimation),
                    fpga_average=bool(s.average),trace_average=int(s.trace_average),trigger_source=str(s.trigger_source),
                    trigger_delay_s=float(s.trigger_delay),rolling_mode=bool(s.rolling_mode),
                    full_scale_v=[self.config.pd_full_scale_v,self.config.monitor_full_scale_v],adc_bits=14)

    def capture(self):
        before=self.settings(); started=self.clock()
        returned=np.asarray(self.scope.single(timeout=self.config.scope_timeout_s))
        initially_frozen=bool(self.scope.curve_ready() and not self.scope._adc_we_keep)
        pointer=int(self.scope._write_pointer_trigger); delay=int(self.scope._trigger_delay_register)
        # Same roll and conversion as inspected Scope._data_ch1/_data_ch2, retaining codes.
        raw=np.stack([np.roll(np.asarray(self.scope._rawdata_ch1,dtype=np.int16),-(pointer+delay+1)),
                      np.roll(np.asarray(self.scope._rawdata_ch2,dtype=np.int16),-(pointer+delay+1))])
        after=self.settings()
        frozen=initially_frozen and self.scope.curve_ready() and not self.scope._adc_we_keep and pointer==int(self.scope._write_pointer_trigger) and delay==int(self.scope._trigger_delay_register)
        consistent=returned.shape==raw.shape and np.array_equal(returned,raw.astype(float)/8192)
        return raw,dict(settings=before,settings_after=after,requested_s=started,received_s=self.clock(),
                        trigger_pointer=pointer,trigger_delay_samples=delay,frozen_verified=bool(frozen),
                        public_trace_agrees=bool(consistent),same_capture=True)

    def disconnect(self):
        errors=[]
        if self.rp.p is not None:
            for name in ('asg1','asg2'):
                module=getattr(self.rp,name,None)
                if module is not None:
                    try:
                        module.output_direct='off'
                        module.setup(waveform='dc',offset=0.,amplitude=0.,trigger_source='immediately')
                    except BaseException as exc: errors.append(str(exc))
            self.cleanup_evidence={'output_routes_disabled':not errors,'final_asg_offset_settings_v':
                [float(self.rp.asg1.offset),float(self.rp.asg2.offset)] if self.rp.asg1 is not None and self.rp.asg2 is not None else None,
                'delivered_voltage_measured':False}
            if self.cleanup_evidence['final_asg_offset_settings_v'] is not None and any(abs(v)>1e-6 for v in self.cleanup_evidence['final_asg_offset_settings_v']):
                errors.append('Nonzero ASG cleanup setting')
        # Route off is the terminal safe state; do not re-enable through another helper.
        self.rp.p=None;self.rp.asg1=None;self.rp.asg2=None
        if errors: raise RuntimeError('; '.join(errors))


class QualificationMock:
    """Explicit software fixture, not a calibrated EOM/ADC simulator."""
    mode='mock'
    def __init__(self,config):
        self.config=config;self.elapsed=0.;self.drive=None;self.count=0
        self.cleanup_evidence={}
    def connect(self): pass
    def clock(self): return self.elapsed
    def origin(self): return {'utc':'2000-01-01T00:00:00+00:00','clock':'synthetic monotonic counter'}
    def sleep(self,seconds): self.elapsed+=seconds
    def describe(self): return {'mode':'mock','synchronization':'synthetic shared sample clock','raw_format':'synthetic signed codes'}
    def configure(self,waveform,offset,amplitude,frequency):
        if waveform=='dc': self.elapsed+=self.config.ramp_s
        self.drive=dict(waveform=waveform,offset_v=offset,amplitude_v=amplitude,frequency_hz=frequency or 0.)
        self.dec=self.config.decimation(frequency);return dict(self.drive)
    def capture(self):
        c=self.config;d=self.drive;dt=8e-9*self.dec;t=np.arange(16384)*dt
        phase=2*np.pi*d['frequency_hz']*t+.3
        if d['waveform']=='sin':
            monitor=d['offset_v']+d['amplitude_v']*np.sin(phase)
            pd=.05+2*d['amplitude_v']*np.sin(phase-.2)
        elif d['waveform']=='triangle':
            monitor=d['offset_v']+d['amplitude_v']*(2/np.pi*np.arcsin(np.sin(phase)))
            pd=.05+.03*np.cos(60*monitor+.3)
        else:
            monitor=np.full(len(t),d['offset_v']);pd=np.full(len(t),.05+.03*np.cos(60*d['offset_v']+.3))
        codes=np.rint(np.stack([pd/c.pd_full_scale_v,monitor/c.monitor_full_scale_v])*8192).astype(np.int16)
        # Deterministic sub-LSB-scale fixture variation, signed samples retained.
        codes[0]+=np.arange(16384,dtype=np.int16)%3-1
        settings=dict(input1=c.pd_input,input2=c.monitor_input,sample_count=16384,sampling_time_s=dt,
            duration_s=16384*dt,decimation=self.dec,fpga_average=False,trace_average=1,trigger_source='immediately',
            trigger_delay_s=0.,rolling_mode=False,full_scale_v=[c.pd_full_scale_v,c.monitor_full_scale_v],adc_bits=14)
        start=self.clock();self.elapsed+=16384*dt+.01;self.count+=1
        return codes,dict(settings=settings,settings_after=dict(settings),requested_s=start,received_s=self.clock(),
            same_capture=True,frozen_verified=True,public_trace_agrees=True,synthetic=True)
    def disconnect(self):
        self.drive=None
        self.cleanup_evidence={'output_routes_disabled':True,'final_asg_offset_settings_v':[0.,0.],'synthetic':True}
