"""Synthetic adaptive discovery; these tests cannot qualify an electrical chain."""
from dataclasses import asdict,replace
from pathlib import Path
import numpy as np
import pytest
from high_speed_polarimeter.config import Recipe
from high_speed_polarimeter.runner import execute
from high_speed_polarimeter.qualification.config import QualificationConfig
from high_speed_polarimeter.qualification.hardware import QualificationMock
from high_speed_polarimeter.qualification.adaptive import observed
from high_speed_polarimeter.qualification.analyze import analyze


def config(tmp_path,**changes):
    values=dict(results_directory=str(tmp_path),strategy='adaptive_vpi',start_command_v=.01,stop_command_v=.07,
        points=25,repeats=2,captures_per_point=2,command_min_v=-.3,command_max_v=.3,
        adaptive_extension_v=.05,adaptive_max_extensions=10,adaptive_direction='positive',
        adaptive_qualified=True,adaptive_qualification_evidence='Synthetic fixture only',
        adaptive_min_prominence_v=.01,adaptive_pd_std_max_v=.001,adaptive_pd_repeat_max_v=.001,adaptive_monitor_std_max_v=.001,
        optical_arrangement_confirmed=True,output_map_confirmed=True,limits_confirmed=True,
        startup_zero_profile_confirmed=True,independent_protection_confirmed=True,adc_ranges_confirmed=True,
        eom_identity='fixture',amplifier_identity='fixture',voltage_chain='synthetic 100 V/V',
        amplifier_input_gain_v_per_command_v=1.,amplifier_input_min_v=-1.,amplifier_input_max_v=1.,amplifier_input_margin_v=.01,
        chain_gain_v_per_command_v=100.,chain_margin_v=.5,electrode_min_v=-100.,electrode_max_v=100.,
        monitor_min_v=-.95,monitor_max_v=.95,monitor_calibration_confirmed=True,monitor_scale_v_per_v=100.,
        monitor_scale_rel_uncertainty=.001,monitor_discrepancy_v=.1)
    return QualificationConfig(**(values|changes))


def run(c,kind=QualificationMock):
    r=Recipe(experiment='eom_pd_qualification',label='adaptive-fixture',config=asdict(c))
    session=kind(c)
    state=execute(r,session_factory=lambda:session)
    directory=Path(state['directory'])
    return state,analyze(directory),directory,session


@pytest.mark.parametrize('full',[False,True])
def test_discovery_validation_and_reanalysis(tmp_path,full):
    c=config(tmp_path,adaptive_full_wave=full)
    state,result,directory,session=run(c)
    a=result['adaptive']
    assert state['status']=='COMPLETED' and state['report_status']=='COMPLETED'
    assert a['half_wave_observed'] and a['classification']=='OBSERVED'
    assert a['full_wave_observed']==full
    assert a['vpi_v']==pytest.approx(100*np.pi/60,abs=.3)
    assert len(a['branches'])==4 and a['vpi_between_branch_span_v']==0
    assert a['direction_comparisons'] and all(r['pd_delta_v']==0 for r in a['direction_comparisons'])
    assert max(p['command_v'] for p in a['points'])>c.stop_command_v
    assert all(c.command_min_v<=p['command_v']<=c.command_max_v for p in a['points'])
    assert analyze(directory)['adaptive']==a
    assert session.cleanup_evidence['output_routes_disabled']
    assert (directory/'report.pdf').stat().st_size>10000


@pytest.mark.parametrize('changes',[dict(command_max_v=.08),dict(electrode_max_v=8.5),
    dict(amplifier_input_max_v=.09),dict(monitor_max_v=.09)])
def test_independent_hard_bounds_stop_extension(tmp_path,changes):
    c=config(tmp_path,**changes)
    state,result,directory,session=run(c)
    a=result['adaptive']
    assert state['status']=='COMPLETED' and a['outcome']=='INSUFFICIENT_SAFE_VOLTAGE_RANGE'
    assert not a['half_wave_observed'] and a['vpi_v'] is None
    assert a['classification'] in {'FITTED_EXTRAPOLATED','UNAVAILABLE'}
    assert 'adaptive_extension_blocked' in (directory/'events.jsonl').read_text()
    assert session.cleanup_evidence['output_routes_disabled']


def test_negative_extension(tmp_path):
    state,result,_,_=run(config(tmp_path,start_command_v=-.04,stop_command_v=.02,adaptive_direction='negative'))
    assert result['adaptive']['half_wave_observed']
    assert result['adaptive']['command_range_v'][0]<-.04


@pytest.mark.parametrize('changes',[dict(monitor_calibration_confirmed=False),dict(adaptive_qualified=False),
    dict(monitor_scale_rel_uncertainty=0.),dict(captures_per_point=1),dict(repeats=1),dict(adaptive_pd_std_max_v=0.),
    dict(amplifier_input_margin_v=0.),dict(chain_margin_v=0.)])
def test_adaptive_preconditions_reject_before_resources(tmp_path,changes):
    c=config(tmp_path,**changes)
    with pytest.raises(ValueError): run(c)
    assert not list(tmp_path.iterdir())


def test_noisy_turns_and_same_intensity_return_not_cycle(tmp_path):
    c=config(tmp_path)
    x=np.linspace(0,10,81)
    def pts(y,noise=0.):
        return [dict(electrode_v=float(v),signal=float(s),noise_v=noise,repeat_span_v=0.,monitor_v=v/100,
                     command_v=v/100,voltage_variation_v=0.,captures=[i]) for i,(v,s) in enumerate(zip(x,y))]
    assert not observed(pts(.1+.001*np.sin(20*x),.001),c)['pairs']
    # One broad hump returns to its starting intensity but has only one turn.
    assert not observed(pts(.1+.05*np.sin(np.pi*x/10)),c)['full_wave']
    assert not observed(pts(.1+.05*np.cos(np.pi*x/20)),c)['pairs']


class Fault(QualificationMock):
    fault='collapse'
    def capture(self):
        raw,meta=super().capture()
        if self.count==4:
            if self.fault=='collapse': raw[0,4000:12000]=np.linspace(raw[0,4000],-50,8000).astype(np.int16)
            elif self.fault=='monitor': raw[1]+=300
            elif self.fault=='clip': raw[0,100]=8191
            elif self.fault=='repeat': raw[0]-=100
        return raw,meta


@pytest.mark.parametrize('fault',['collapse','monitor','clip','repeat'])
def test_fault_preserves_capture_and_shuts_down(tmp_path,fault):
    class Session(Fault): pass
    Session.fault=fault
    state,result,directory,session=run(config(tmp_path),Session)
    a=result['adaptive']
    assert state['status']=='FAILED' and state['cleanup_status']=='COMPLETED'
    assert a['outcome']=='FAULT_SHUTDOWN' and not a['half_wave_observed']
    assert len(result['captures'])==4 and (directory/'dual_raw.npy').stat().st_size>200000
    assert 'adaptive_stop' in (directory/'events.jsonl').read_text()
    assert session.cleanup_evidence['output_routes_disabled']
    assert state['report_status']=='COMPLETED'


def test_fraction_vpi_resolves_explicit_command_envelope(tmp_path):
    c=config(tmp_path,strategy='settled',mode='frequency_response',vpi_fraction_peak=.1,
        calibrated_vpi_v=50.,calibrated_vpi_uncertainty_v=1.,vpi_calibration_confirmed=True,
        vpi_calibration_evidence='compatible independently calibrated DC fixture reference',
        bias_v=.05,allowed_frequency_min_hz=1.,allowed_frequency_max_hz=1000.)
    c.confirm_hardware()
    assert c.frequency_peak()==pytest.approx(.05) and c.envelope()==pytest.approx((0.,.1))
    from high_speed_polarimeter.qualification.acquire import states
    assert next(states(c))['amplitude']==.05
    with pytest.raises(ValueError): replace(c,vpi_calibration_confirmed=False).validate()
    with pytest.raises(ValueError): replace(c,chain_margin_v=.01).validate()


def test_budget_partial_fringe_and_unstable_branch(tmp_path):
    _,result,_,_=run(config(tmp_path/'budget',adaptive_max_extensions=1,adaptive_extension_v=.005))
    assert result['adaptive']['outcome']=='DISCOVERY_BUDGET_EXHAUSTED'
    assert not result['adaptive']['half_wave_observed']
    class Drifting(QualificationMock):
        def capture(self):
            raw,meta=super().capture()
            if self.count>200: raw[0]+=60
            return raw,meta
    _,result,_,session=run(config(tmp_path/'drifting'),Drifting)
    assert not result['adaptive']['half_wave_observed']
    assert result['adaptive']['failed_checks'] or result['adaptive']['outcome']!='TARGET_ACQUIRED'
    assert session.cleanup_evidence['output_routes_disabled']
