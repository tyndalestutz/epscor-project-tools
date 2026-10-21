"""Software mathematics/lifecycle checks, never repaired-EOM performance claims."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json
import numpy as np
import pytest

from high_speed_polarimeter.config import Recipe,load_recipe
from high_speed_polarimeter.runner import execute
from high_speed_polarimeter.quality import reevaluate
from high_speed_polarimeter.qualification.config import QualificationConfig
from high_speed_polarimeter.qualification.hardware import QualificationMock,QualificationSession
from high_speed_polarimeter.qualification.analyze import analyze,captures
from high_speed_polarimeter.qualification.math import cosine_fit,demodulate,triangle_branches


def recipe(tmp_path,**changes):
    return Recipe(experiment='eom_pd_qualification',label='fixture',config=dict(results_directory=str(tmp_path),points=13,repeats=1,captures_per_point=1)|changes)


def confirmed(**changes):
    values=dict(optical_arrangement_confirmed=True,output_map_confirmed=True,limits_confirmed=True,
        startup_zero_profile_confirmed=True,independent_protection_confirmed=True,adc_ranges_confirmed=True,
        eom_identity='synthetic test device',amplifier_identity='test amplifier',voltage_chain='test chain gain 100',
        amplifier_input_gain_v_per_command_v=1.,amplifier_input_min_v=-1.,amplifier_input_max_v=1.,
        chain_gain_v_per_command_v=100.,electrode_min_v=-20.,electrode_max_v=20.,
        allowed_frequency_min_hz=.1,allowed_frequency_max_hz=1000.)
    return QualificationConfig(**(values|changes))


@pytest.mark.parametrize('changes',[{'command_max_v':1.1},{'triangle_amplitude_v':.3,'strategy':'triangle'},
    {'bias_v':.09,'sine_amplitude_v':.02,'mode':'frequency_response'}, {'pd_input':'in2'},
    {'monitor_polarity':0},{'monitor_calibration_confirmed':True},{'frequencies_hz':[10,float('nan')]},
    {'scope_decimation':3},{'mode':'frequency_response','frequencies_hz':[1e9],'reference_frequency_hz':1e9},
    {'mode':'frequency_response','min_capture_cycles':1.},{'frequency_waveform':'sawtooth'},{'waveform_plot_cycles':0},{'diagnostic_frequency_hz':-1.},
    {'scope_timeout_s':1e-8},{'points':True},{'chain_margin_v':-1}])
def test_invalid_recipe_before_resources(tmp_path,changes):
    calls=[]
    with pytest.raises(ValueError): execute(recipe(tmp_path,**changes),session_factory=lambda:calls.append(1))
    assert not calls and not list(tmp_path.iterdir())


def test_hardware_confirmations_and_full_chain_envelope(tmp_path):
    with pytest.raises(ValueError,match='explicit'):
        execute(recipe(tmp_path),mode='hardware',session_factory=lambda:pytest.fail('hardware accessed'))
    confirmed().confirm_hardware()
    confirmed(chain_gain_v_per_command_v=-100.).confirm_hardware()
    for changes in ({'chain_offset_v':21.},{'chain_margin_v':21.},{'electrode_max_v':9.},{'adc_ranges_confirmed':False},{'amplifier_input_offset_v':2.}):
        with pytest.raises(ValueError): replace(confirmed(),**changes).confirm_hardware()
    with pytest.raises(ValueError): confirmed(mode='frequency_response',allowed_frequency_max_hz=30.).confirm_hardware()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('vpi',(20.,75.,150.))
def test_voltage_cosine_recovers_other_eom_coefficients_with_uncertainty(vpi):
    x=np.linspace(-2*vpi,2*vpi,121)
    y=.2+.12*np.cos(.37+np.pi*x/vpi)+.0004*np.sin(np.arange(len(x))*1.31)
    fit=cosine_fit(x,y,scale_uncertainty=.02)
    assert fit['status']=='OBSERVED_FRINGE_MODEL'
    assert fit['vpi']==pytest.approx(vpi,rel=.002)
    assert fit['vpi_fit_standard_error']>0
    assert fit['vpi_combined_standard_uncertainty']>=.019*fit['vpi']
    assert fit['visibility_voltage_proxy']==pytest.approx(.6,rel=.01)
    assert fit['observed_neighbor_extrema']


def test_insufficient_or_ambiguous_fringe_never_observed_vpi():
    x=np.linspace(0,1,41)
    assert cosine_fit(x,.2+.1*np.cos(.2+.1*np.pi*x))['status']=='UNAVAILABLE'
    weak=cosine_fit(x,.2+.1*np.cos(.3+np.pi*x))
    assert weak['status']!='OBSERVED_FRINGE_MODEL'
    assert cosine_fit(x,np.sin(2*np.pi*18*x),max_cycles=3)['status']=='UNAVAILABLE'
    assert cosine_fit(x,np.full(41,.1))['status']=='UNAVAILABLE'


def test_coherent_amplitude_phase_harmonic_and_relative_delay():
    dt=1/16000;f=123.7;t=np.arange(16384)*dt
    monitor=.03+.01*np.cos(2*np.pi*f*t+.7)
    pd=-.02+.003*np.cos(2*np.pi*f*t+.7-.45)+.0002*np.cos(4*np.pi*f*t+.2)
    m=demodulate(monitor,dt,f);p=demodulate(pd,dt,f)
    ratio=complex(*p['fundamental'])/complex(*m['fundamental'])
    assert abs(ratio)==pytest.approx(.3,rel=1e-8)
    assert np.angle(ratio)==pytest.approx(-.45,abs=1e-8)
    assert p['second_harmonic_amplitude']==pytest.approx(.0002,rel=1e-8)
    assert p['mean']==pytest.approx(-.02)
    assert demodulate(pd,dt,10000)['status']=='UNAVAILABLE'


def test_complete_triangle_cycles_no_fabricated_cross_buffer_direction():
    phase=np.linspace(.2,8*np.pi+.2,16000)
    x=np.arcsin(np.sin(phase))
    branches,why=triangle_branches(x,2,expected_cycles=4)
    assert why is None and len(branches)>=4
    assert {'forward','reverse'}=={b[1] for b in branches}
    assert triangle_branches(np.zeros(100),1)[1]
    assert triangle_branches(np.tile([0,1,0,-1],100),1,expected_cycles=1)[1]


def test_settled_signed_buffers_gaps_domains_and_immutable_offline_report(tmp_path):
    r=recipe(tmp_path,monitor_available=False)
    class Signed(QualificationMock):
        def capture(self):
            raw,meta=super().capture();raw[0,:8]=-100;return raw,meta
    state=execute(r,session_factory=lambda:Signed(r.acquisition_config()))
    assert state['status']==state['cleanup_status']==state['report_status']=='COMPLETED'
    folder=Path(state['directory']);pairs=list(captures(folder))
    assert len(pairs)==26 and all(raw.dtype==np.int16 and raw.shape==(2,16384) for _,raw in pairs)
    assert all(raw[0].min()<0 for _,raw in pairs)
    analysis=json.loads((folder/'analysis.json').read_text())
    assert analysis['calibration_status']=='NOT_HARDWARE_VERIFIED'
    assert all(group['domain']=='RP command V' for group in analysis['groups'])
    assert analysis['captures'][1]['host_bound_separation_s']>0
    assert 'ELECTRODE_VOLTAGE_UNAVAILABLE' in analysis['flags']
    names=('dual_raw.npy','events.jsonl','acquisition.json','recipe.json','run.json')
    hashes={name:hashlib.sha256((folder/name).read_bytes()).hexdigest() for name in names}
    reevaluate(folder);first=(folder/'analysis.json').read_bytes()
    reevaluate(folder)
    assert (folder/'analysis.json').read_bytes()==first
    assert all(hashlib.sha256((folder/name).read_bytes()).hexdigest()==digest for name,digest in hashes.items())
    assert (folder/'report.pdf').read_bytes().startswith(b'%PDF')
    assert not (folder/'pd_raw.npy').exists()  # Native paired stream, no duplicate channel files.


@pytest.mark.parametrize('mode,strategy',[('voltage_transfer','triangle'),('frequency_response','settled')])
def test_waveform_mock_lifecycle_and_complete_raw_evidence(tmp_path,mode,strategy):
    r=recipe(tmp_path,mode=mode,strategy=strategy,min_capture_cycles=2.,triangle_cycles=1)
    state=execute(r);folder=Path(state['directory'])
    assert state['status']==state['report_status']=='COMPLETED'
    result=read_result(folder)
    assert result['calibration_status']=='NOT_HARDWARE_VERIFIED'
    if mode=='frequency_response':
        assert len(result['normalized_response'])==3
        assert all(abs(x['normalized_magnitude']-1)<.01 for x in result['normalized_response'])
        assert all(abs(x['relative_phase_deg'])<.1 for x in result['normalized_response'])
        summary={row['question']:row for row in result['qualification_summary']}
        assert summary['Vpi at operating wavelength']['status']=='NOT MEASURED'
        assert summary['Voltage-to-retardance linearity']['status']=='UNAVAILABLE'
        assert summary['Frequency response']['status']=='INCONCLUSIVE'  # Operating slope unverified.
        assert summary['Comparison with intact EOM']['status']=='UNAVAILABLE'
    else:
        assert result['groups'] and all('sample_range' in g and 'bins' in g for g in result['groups'])


def read_result(folder): return json.loads((folder/'analysis.json').read_text())


@pytest.mark.parametrize('failure',('exception','missing','clip','unfrozen','settings'))
def test_failures_keep_returned_evidence_and_cleanup(tmp_path,failure):
    class Broken(QualificationMock):
        def capture(self):
            if failure=='exception': raise RuntimeError('capture lost')
            if failure=='missing': return None,{}
            raw,meta=super().capture()
            if failure=='clip': raw[0,0]=-8192
            if failure=='unfrozen': meta['frozen_verified']=False
            if failure=='settings': meta['settings_after']['decimation']*=2
            return raw,meta
    r=recipe(tmp_path);session=Broken(r.acquisition_config());state=execute(r,session_factory=lambda:session)
    assert state['status']=='FAILED' and state['cleanup_status']=='COMPLETED'
    assert session.drive is None
    folder=Path(state['directory']);result=read_result(folder)
    assert 'INCOMPLETE_RUN_NO_VALID_CALIBRATION' in result['flags']
    if failure not in ('exception','missing'): assert list(captures(folder))
    assert state['report_status']=='COMPLETED'


def test_monitor_overrange_retained_and_no_calibrated_fit(tmp_path):
    from high_speed_polarimeter.qualification.acquire import monitor_guard
    c=confirmed(monitor_calibration_confirmed=True,monitor_scale_v_per_v=100.,monitor_discrepancy_v=1.)
    raw=np.array([[0.,0.],[.3,.3]])
    with pytest.raises(RuntimeError,match='envelope'):
        monitor_guard(raw,{'offset':0.,'amplitude':0.,'frequency':None},{},c,True)


@pytest.mark.parametrize('output,pd,monitor',[('out1','in1','in2'),('out2','in2','in1')])
def test_scope_adapter_simultaneous_capture_and_terminal_route_off(monkeypatch,output,pd,monitor):
    c=confirmed(ramp_s=0.,rp_output=output,pd_input=pd,monitor_input=monitor)
    class Scope:
        data_length=16384
        _adc_we_keep=False
        _write_pointer_trigger=0
        _trigger_delay_register=16384
        decimation=1
        @property
        def sampling_time(self): return self.decimation*8e-9
        @property
        def duration(self): return self.sampling_time*self.data_length
        @property
        def _rawdata_ch1(self): return np.arange(16384,dtype=np.int16)%100-50
        @property
        def _rawdata_ch2(self): return np.arange(16384,dtype=np.int16)%200-100
        def setup(self,**kw): self.__dict__.update(kw)
        def single(self,**kw):
            self.calls=getattr(self,'calls',0)+1
            return np.stack([np.roll(self._rawdata_ch1,-1),np.roll(self._rawdata_ch2,-1)])/8192
        def curve_ready(self): return True
    class ASG:
        waveform='dc';amplitude=0.;offset=0.;frequency=1.;output_direct='off'
        def setup(self,**kw): self.__dict__.update(kw)
    class RP:
        def __init__(self,cfg): self.p=None;self.asg1=ASG();self.asg2=ASG()
        def connect(self): self.p=SimpleNamespace(rp=SimpleNamespace(scope=Scope()))
    monkeypatch.setattr('polarization_locking.hardware.rp_interface.RPController',RP)
    session=QualificationSession(c);session.connect()
    scope=session.scope
    assert session.asg is getattr(session.rp,'asg1' if output=='out1' else 'asg2')
    assert scope.input1==pd and scope.input2==monitor
    session.configure('dc',.05,0.,None);session.configure('dc',.03,0.,None)
    assert session.asg.offset==.03  # Direct monotonic return branch, no hidden zero between points.
    raw,meta=session.capture()
    assert scope.calls==1 and raw.dtype==np.int16 and raw.min()<0
    assert meta['public_trace_agrees'] and meta['frozen_verified']
    selected=session.asg
    ack=session.configure('triangle',.05,.005,10.)
    assert selected.waveform==ack['waveform']=='ramp' and ack['requested_waveform']=='triangle'
    session.disconnect()
    assert selected.offset==0 and selected.output_direct=='off'
    assert session.cleanup_evidence['output_routes_disabled']


@pytest.mark.parametrize('interrupt,cleanup_fail',[(True,False),(False,True)])
def test_interruption_and_cleanup_failure_are_separate_retained_statuses(tmp_path,interrupt,cleanup_fail):
    class Interrupted(QualificationMock):
        def capture(self):
            if interrupt: raise KeyboardInterrupt('operator stop')
            return super().capture()
        def disconnect(self):
            super().disconnect()
            if cleanup_fail: raise RuntimeError('synthetic cleanup failure')
    r=recipe(tmp_path);session=Interrupted(r.acquisition_config())
    state=execute(r,session_factory=lambda:session)
    assert state['status']==('INTERRUPTED' if interrupt else 'COMPLETED')
    assert state['cleanup_status']==('FAILED' if cleanup_fail else 'COMPLETED')
    assert session.drive is None
    assert state['report_status']=='COMPLETED'
    result=read_result(Path(state['directory']))
    assert result['calibration_status']=='UNAVAILABLE_INCOMPLETE_RUN'


def test_calibrated_monitor_detects_missing_ac_drive_and_unexpected_offset():
    from high_speed_polarimeter.qualification.acquire import monitor_guard
    c=confirmed(monitor_calibration_confirmed=True,monitor_scale_v_per_v=100.,monitor_discrepancy_v=.1)
    with pytest.raises(RuntimeError,match='AC amplitude'):
        monitor_guard(np.array([[0.,0.],[.05,.05]]),dict(offset=.05,amplitude=.005,frequency=10.),{'settings':{'duration_s':1.}},c,True)
    with pytest.raises(RuntimeError,match='DC offset'):
        monitor_guard(np.array([[0.,0.],[.03,.03]]),dict(offset=.05,amplitude=0.,frequency=None),{},c,True)


def test_calibrated_synthetic_domains_direction_and_cycle_statistics(tmp_path):
    r=recipe(tmp_path,points=21,repeats=2,monitor_calibration_confirmed=True,
             monitor_scale_v_per_v=100.,monitor_scale_rel_uncertainty=.02)
    class Directional(QualificationMock):
        def capture(self):
            raw,meta=super().capture()
            branch=(self.count-1)//21;cycle=branch//2;reverse=branch%2
            monitor=raw[1].astype(float)/8192
            raw[0]=np.rint((.05+.03*np.cos(60*monitor+.3+.12*reverse+.03*cycle))*8192).astype(np.int16)
            return raw,meta
    state=execute(r,session_factory=lambda:Directional(r.acquisition_config()))
    assert state['status']==state['report_status']=='COMPLETED'
    result=read_result(Path(state['directory']))
    assert result['calibration_status']=='NOT_HARDWARE_VERIFIED'
    assert {g['domain'] for g in result['groups']}=={'RP command V','inferred electrode V','monitor ADC V'}
    for g in result['groups']:
        factor=100 if g['domain']=='inferred electrode V' else 1
        assert g['fit']['vpi']==pytest.approx(np.pi/60*factor,rel=.01)
    assert len(result['same_command_direction_differences'])==42
    assert max(abs(r['pd_reverse_minus_forward_v']) for r in result['same_command_direction_differences'])>.002
    assert all(r['reverse_minus_forward_phase_rad']==pytest.approx(.12,abs=.01) for r in result['fitted_direction_differences'])
    assert result['repeatability']['inferred electrode V']['supported_groups']==4
    assert len(result['repeatability']['inferred electrode V']['parameter_trends'])==4


@pytest.mark.parametrize('monitor',([-.26,0.],[0.,5.01],[float('nan'),0.]))
def test_uncalibrated_hardware_monitor_window_is_still_enforced(monitor):
    from high_speed_polarimeter.qualification.acquire import monitor_guard
    c=QualificationConfig(monitor_full_scale_v=20.,monitor_min_v=-.25,monitor_max_v=5.)
    assert not c.monitor_calibration_confirmed
    with pytest.raises(RuntimeError,match='ADC safety window'):
        monitor_guard(np.array([[0.,0.],monitor]),{}, {},c,True)
    monitor_guard(np.array([[0.,0.],[0.,4.99]]),{}, {},c,True)


def test_monitor_window_rejects_invalid_adc_bounds():
    for values in ({'monitor_min_v':2.},{'monitor_min_v':-2.},{'monitor_max_v':2.}):
        with pytest.raises(ValueError,match='Monitor ADC safety window'):
            QualificationConfig(**values).validate()


def test_half_wave_extrema_do_not_require_full_period():
    x=np.linspace(-.3,1.3,161)
    y=.2+.1*np.cos(np.pi*x)
    fit=cosine_fit(x,y)
    assert np.pi<fit['phase_span_rad']<2*np.pi
    assert fit['status']=='OBSERVED_FRINGE_MODEL'
    assert fit['vpi']==pytest.approx(1.,rel=1e-5)
    assert fit['extrema_separations']==pytest.approx([1.],abs=.011)
    for turn in fit['observed_neighbor_extrema']:
        assert turn['voltage_bracket'][0]<turn['voltage']<turn['voltage_bracket'][1]


@pytest.mark.parametrize('spike',[False,True])
def test_noise_turns_do_not_resolve_a_partial_fringe(spike):
    x=np.linspace(0,1,121)
    y=.2+.1*np.cos(.3+.8*np.pi*x)+.0003*np.sin(np.arange(121)*2.)
    if spike: y[40]+=.05
    fit=cosine_fit(x,y)
    assert fit['status']=='MODEL_BASED_EXTRAPOLATION'
    assert not fit['extrema_separations']
    assert fit['vpi_fit_standard_error']>0


@pytest.mark.parametrize('collapse',[False,True])
def test_slow_collapse_stable_monitor_and_normal_repeatability(tmp_path,collapse):
    r=recipe(tmp_path,points=21,repeats=2,command_max_v=.45,stop_command_v=.45,
             monitor_full_scale_v=20.,monitor_min_v=-.25,monitor_max_v=5.)
    class Transient(QualificationMock):
        def capture(self):
            raw,meta=super().capture();number=self.count-1;n=raw.shape[1]
            command=self.drive['offset_v'];value=.35+.12*np.cos(.2+6*command)
            pd=np.full(n,value)
            if collapse:
                if number==47: pd[n//4:]=np.linspace(value,-.006,n-n//4)
                if number==48: pd[:]=-.006
                if number==49: pd[:n//2]=np.linspace(-.006,value,n//2)
            pd+=.0001*np.sin(np.arange(n)*.79)
            raw[0]=np.rint(pd*8192).astype(np.int16)
            raw[1]=np.rint((9.6*command-.06)/20*8192).astype(np.int16)
            return raw,meta
    state=execute(r,session_factory=lambda:Transient(r.acquisition_config()));folder=Path(state['directory'])
    result=read_result(folder);diagnostics=result['diagnostics']
    assert state['status']==state['report_status']=='COMPLETED'
    assert len(result['captures'])==84
    assert {g['domain'] for g in result['groups']}=={'RP command V','monitor ADC V'}
    assert all(r['electrode_range_v'] is None for r in result['captures'])
    summary={row['question']:row for row in result['qualification_summary']}
    assert summary['Frequency response']['status']=='NOT MEASURED'
    assert summary['Comparison with intact EOM']['status']=='UNAVAILABLE'
    assert summary['Vpi at operating wavelength']['status']=='INCONCLUSIVE'
    assert summary['Voltage-to-retardance linearity']['status']=='INCONCLUSIVE'
    assert len(result['cycle_comparisons'])==42
    report=(folder/'report.md').read_text()
    assert 'not EOM PASS criteria' in report
    if collapse:
        assert diagnostics['anomalous_captures']==[47,48,49]
        assert summary['Cycle repeatability']['status']=='INCONCLUSIVE'
        assert result['captures'][48]['collapse_diagnostic']['fraction']==1
        assert result['captures'][47]['monitor_std_v']==0
        worst=max(result['same_command_direction_differences'],key=lambda p:abs(p['pd_reverse_minus_forward_v']))
        assert worst['anomaly_associated'] and 48 in worst['captures']
        assert worst['monitor_reverse_minus_forward_v']==0
        for i in (47,48,49): assert f'Capture {i}, cycle 1 forward' in report
        assert any(g['anomalous_captures'] for g in result['groups'])
        from high_speed_polarimeter.qualification.report import _raw_panel
        from matplotlib.figure import Figure
        fig=Figure();ax,monitor=fig.subplots(2);number=diagnostics['most_anomalous_capture']
        _raw_panel(ax,folder,r.resolved().config,number,'Most anomalous',monitor)
        values=ax.lines[0].get_ydata()
        assert len(values)==16384 and values.min()<0 and values.max()>.3
        assert f'capture {number}' in ax.get_title()
    else:
        assert diagnostics['anomalous_captures']==[]
        assert diagnostics['most_anomalous_capture'] is None
        assert summary['Cycle repeatability']['status']=='SUPPORTED'
        assert all(row['pd_difference_v']==0 for row in result['cycle_comparisons'])
        assert all(not row['anomaly_associated'] for row in result['same_command_direction_differences'])
    names=('dual_raw.npy','events.jsonl','recipe.json','acquisition.json','run.json')
    hashes={name:hashlib.sha256((folder/name).read_bytes()).hexdigest() for name in names}
    reevaluate(folder)
    assert all(hashlib.sha256((folder/name).read_bytes()).hexdigest()==digest for name,digest in hashes.items())
    assert read_result(folder)['diagnostics']==diagnostics


def test_clean_and_distorted_electrical_and_optical_harmonics():
    from high_speed_polarimeter.qualification.math import waveform_diagnostics
    dt=1/100000;f=137.;t=np.arange(16384)*dt;phase=2*np.pi*f*t+.37
    monitor=.04+.01*np.cos(phase)
    m=waveform_diagnostics(monitor,dt,f,'sin')
    p=demodulate(.12+.003*np.cos(phase-.4)+.001*np.cos(2*phase+.2),dt,f,max_harmonic=7)
    assert m['amplitude']==pytest.approx(.01,rel=1e-9)
    assert m['mean']==pytest.approx(.04,abs=1e-10)
    assert m['resolved_thd']<1e-9 and m['harmonic_shape_error']<1e-9
    assert abs(complex(*p['fundamental'])/complex(*m['fundamental']))==pytest.approx(.3,rel=1e-9)
    assert np.angle(complex(*p['fundamental'])/complex(*m['fundamental']))==pytest.approx(-.4,abs=1e-9)
    assert p['harmonic_ratio']==pytest.approx(1/3,rel=1e-9)
    distorted=waveform_diagnostics(monitor+.003*np.cos(2*phase),dt,f,'sin')
    assert distorted['resolved_thd']==pytest.approx(.3,rel=1e-9)
    assert distorted['shape_residual_fraction']==pytest.approx(.3,rel=.01)


def test_triangle_harmonics_and_bandwidth_rounding_are_not_sine_distortion():
    from high_speed_polarimeter.qualification.math import waveform_diagnostics,command_shape
    f=1000.;dt=1/200000;t=np.arange(16384)*dt;phase=2*np.pi*f*t+.2
    clean=waveform_diagnostics(.04+.01*command_shape(phase,'triangle'),dt,f,'triangle')
    assert clean['amplitude']==pytest.approx(.01*8/np.pi**2,rel=.002)
    assert clean['resolved_thd']==pytest.approx(clean['expected_resolved_thd'],rel=.02)
    assert clean['harmonic_shape_error']<.003 and clean['shape_residual_fraction']<.003
    # Known first-order low-pass response applied independently to each ideal odd harmonic.
    cutoff=500.;rounded=.04+sum(.01*8/np.pi**2/h**2/np.sqrt(1+(h*f/cutoff)**2)*np.cos(h*phase-np.arctan(h*f/cutoff)) for h in range(1,40,2))
    m=waveform_diagnostics(rounded,dt,f,'triangle')
    assert m['amplitude']==pytest.approx(.01*8/np.pi**2/np.sqrt(5),rel=.002)
    assert m['resolved_thd']<m['expected_resolved_thd']
    assert m['harmonic_shape_error']>.05
    assert 'rounding' in m['shape_model']


class FrequencyFixture(QualificationMock):
    """Known paired waveforms; distort one electrical frequency or only the optical channel."""
    effect='clean'
    def capture(self):
        if self.effect=='missing' and self.count==2: raise RuntimeError('Synthetic missing final capture')
        raw,meta=super().capture();d=self.drive;dt=meta['settings']['sampling_time_s'];t=np.arange(raw.shape[1])*dt
        phase=2*np.pi*d['frequency_hz']*t+.37
        if self.config.frequency_waveform=='triangle':
            # First-order bandwidth limit, with known complex harmonic attenuation.
            monitor=.04+sum(.01*8/np.pi**2/h**2/np.sqrt(1+(h*d['frequency_hz']/2000)**2)*np.cos(h*phase-np.arctan(h*d['frequency_hz']/2000)) for h in range(1,30,2))
        else: monitor=.04+.01*np.cos(phase)
        pd=.12+.003*np.cos(phase-.4)
        if self.effect=='electrical' and d['frequency_hz']==5000.: monitor+=.004*np.cos(2*phase)
        if self.effect=='optical': pd+=.001*np.cos(2*phase+.1)
        if self.effect=='low_snr': pd=.12+.001*np.sin(np.arange(len(t))*1.237)
        if self.effect=='ambiguous': pd=.12+.003*np.cos(phase+(0 if self.count%2 else np.pi))
        raw[:]=np.rint(np.stack([pd/self.config.pd_full_scale_v,monitor/self.config.monitor_full_scale_v])*8192).astype(np.int16)
        if self.effect=='clip' and self.count==2: raw[1,100]=8191
        return raw,meta


def frequency_recipe(tmp_path,**changes):
    return recipe(tmp_path,**(dict(mode='frequency_response',frequencies_hz=[10.,5000.,10000.],reference_frequency_hz=10.,repeats=1)|changes))


@pytest.mark.parametrize('effect',['clean','electrical','optical','triangle'])
def test_frequency_waveforms_selection_units_and_immutable_reports(tmp_path,effect):
    from high_speed_polarimeter.qualification.report import _frequency_waveforms,_waveform_samples
    from high_speed_polarimeter.qualification.math import command_shape
    from matplotlib import pyplot as plt
    r=frequency_recipe(tmp_path,frequency_waveform='triangle' if effect=='triangle' else 'sin',monitor_full_scale_v=20. if effect=='electrical' else 1.)
    session=FrequencyFixture(r.acquisition_config());session.effect=effect
    state=execute(r,session_factory=lambda:session);folder=Path(state['directory']);result=read_result(folder)
    assert state['status']==state['cleanup_status']==state['report_status']=='COMPLETED'
    records=result['normalized_response'];assert len(records)==3
    assert all('pd_to_monitor_phase_deg' in row for row in records)
    assert all('UNAVAILABLE' in row['command_to_monitor_phase'] for row in records)
    flags=[f for row in records for f in row['flags']]
    assert any('ELECTRICAL_SINE_DISTORTION'==f for f in flags)==(effect=='electrical')
    assert any('PD_2F_CONTENT' in f for f in flags)==(effect=='optical')
    if effect=='clean':
        for row in records:
            assert row['electrical_fundamental_gain_v_per_v']==pytest.approx(2.,rel=.004)
            assert abs(complex(*row['ratio']))==pytest.approx(.3,rel=.02)
            assert row['pd_to_monitor_phase_deg']==pytest.approx(np.degrees(-.4),abs=.3)
    if effect=='electrical':
        worst=max(records,key=lambda row:row['diagnostic_score'])
        assert worst['requested_frequency_hz']==5000.
        assert next(x for x in result['waveform_examples'] if 'requested diagnostic' in x['roles'])['capture']==worst['capture']
    if effect=='triangle':
        assert 'TRIANGLE_SHAPE_CHANGE_ROUNDING_OR_DISTORTION' in flags
        assert 'MONITOR_FUNDAMENTAL_LOSS_VS_REFERENCE' in flags
        assert all(row['generator_readback']['waveform']=='ramp' for row in records)
    pairs=dict(enumerate(captures(folder)));shown=[]
    class Pages:
        def savefig(self,fig):
            shown.extend(ax for ax in fig.axes if ax.lines)
    _frequency_waveforms(Pages(),folder,result,r.acquisition_config())
    # Every plotted B/C value must be an actual retained ADC sample in its declared voltage domain.
    for example in result['waveform_examples']:
        number=example['capture'];event,raw=pairs[number];row=records[number]
        index,dt,periods=_waveform_samples(raw,event,row,r.acquisition_config())
        assert 1<=periods<=r.acquisition_config().waveform_plot_cycles
        command_ax=next(ax for ax in shown if ax.get_title().startswith(f'Capture {number};'))
        axes=command_ax.figure.axes;column=axes.index(command_ax);columns=len(axes)//3
        mon_ax=axes[columns+column];pd_ax=axes[2*columns+column]
        np.testing.assert_array_equal(mon_ax.lines[0].get_ydata(),raw[1,index]/8192*r.acquisition_config().monitor_full_scale_v)
        np.testing.assert_array_equal(pd_ax.lines[0].get_ydata(),raw[0,index]/8192*r.acquisition_config().pd_full_scale_v)
        np.testing.assert_allclose(mon_ax.lines[0].get_xdata(),index*dt*(1000 if row['frequency_hz']>=100 else 1))
        np.testing.assert_array_equal(pd_ax.lines[0].get_xdata(),mon_ax.lines[0].get_xdata())
        expected=row['generator_readback']['offset_v']+row['generator_readback']['amplitude_v']*command_shape(2*np.pi*row['frequency_hz']*index*dt+row['monitor']['phase_rad'],row['waveform'])
        np.testing.assert_allclose(command_ax.lines[0].get_ydata(),expected)
    assert len(result['waveform_examples'])<=4
    assert any('highest acquired frequency' in e['roles'] for e in result['waveform_examples'])
    assert 'presentation only' in (folder/'report.md').read_text()
    hashes={name:hashlib.sha256((folder/name).read_bytes()).hexdigest() for name in ('dual_raw.npy','events.jsonl','recipe.json','acquisition.json','run.json')}
    reevaluate(folder)
    assert read_result(folder)['waveform_examples']==result['waveform_examples']
    assert all(hashlib.sha256((folder/n).read_bytes()).hexdigest()==v for n,v in hashes.items())
    plt.close('all')


@pytest.mark.parametrize('effect',['missing','clip','low_snr','ambiguous'])
def test_frequency_missing_invalid_and_ambiguous_evidence_remains_visible(tmp_path,effect):
    r=frequency_recipe(tmp_path,captures_per_point=2 if effect=='ambiguous' else 1)
    session=FrequencyFixture(r.acquisition_config());session.effect=effect
    state=execute(r,session_factory=lambda:session);folder=Path(state['directory']);result=read_result(folder)
    assert state['cleanup_status']==state['report_status']=='COMPLETED'
    rows=result['frequency_coverage'];records=result['normalized_response']
    if effect in ('missing','clip'):
        assert state['status']=='FAILED' and rows[-1]['returned_captures']==0
        assert 'MISSING_OR_EXTRA_CAPTURES' in rows[-1]['flags']
    if effect=='clip':
        clipped=next(r for r in records if r['status']=='INVALID')
        assert 'ADC_CLIPPING' in clipped['flags']
        assert any(e['capture']==clipped['capture'] for e in result['waveform_examples'])
        assert list(captures(folder))[clipped['capture']][1][1,100]==8191
    if effect in ('low_snr','ambiguous'):
        assert all('normalized_magnitude' not in row for row in records)
    if effect=='ambiguous': assert any('REFERENCE_PHASE_AMBIGUITY' in row['flags'] for row in records)
    if effect=='low_snr': assert all('pd_to_monitor_phase_deg' not in row for row in records)
