"""Bounded PAX-only acquisition, explicit averaging and analytic model checks."""
import csv
import json
from dataclasses import replace

import numpy as np
import pytest

from high_speed_polarimeter.analysis.baseline import time_average
from high_speed_polarimeter.analysis.vpi import estimate
from high_speed_polarimeter.config import Recipe, VpiConfig
from high_speed_polarimeter.hardware import MockAcquisitionSession
from high_speed_polarimeter.runner import execute


def directions(phase, offset=.1):
    radius=np.sqrt(1-offset**2)
    return np.column_stack([np.full(len(phase),offset),radius*np.cos(phase),radius*np.sin(phase)])


def test_known_rotation_and_direction_offset():
    volts=np.linspace(0,.9,31);slope=np.pi/.775
    result=estimate(volts,directions(slope*volts),directions(slope*volts-.01),VpiConfig())
    assert result['status']=='MODEL_SUPPORTED'
    assert all(fit['pi_reached'] for fit in result['fits'])
    assert result['fits'][0]['pi_crossing_command_v']==pytest.approx(.775)
    assert [fit['vpi_linear_branch_command_v'] for fit in result['fits']]==pytest.approx([.775,.775])
    assert result['max_direction_difference_deg']==pytest.approx(np.degrees(.01))


def test_plateau_never_claims_observed_pi():
    volts=np.linspace(0,.9,31);phase=np.pi*np.minimum(volts,.5)/.775
    result=estimate(volts,directions(phase),directions(phase),VpiConfig())
    assert result['status']=='MODEL_SUPPORTED'
    assert result['fits'][0]['vpi_linear_branch_command_v']==pytest.approx(.775)
    assert not any(fit['pi_reached'] for fit in result['fits'])
    assert all(fit['pi_crossing_command_v'] is None for fit in result['fits'])


def test_stable_state_does_not_yield_vpi():
    volts=np.linspace(0,.9,31);xyz=directions(np.zeros(31))
    assert estimate(volts,xyz,xyz,VpiConfig())['status']=='UNAVAILABLE'
    xyz[5,1]=np.nan
    assert estimate(volts,xyz,xyz,VpiConfig())['status']=='UNAVAILABLE'


def test_time_average_nonuniform_samples_and_no_gap_bridging():
    result=time_average([0,1,3],[0,1,3])
    assert result['mean']==pytest.approx([1.5])
    assert result['temporal_variance']==pytest.approx([1.25])
    result=time_average([0,1,2,100,101,102],[0,0,0,1,1,1])
    assert result['mean']==pytest.approx([.5])
    assert result['covered_s']==4 and result['excluded_intervals']==1


def small_recipe(tmp_path, **changes):
    config=dict(results_directory=str(tmp_path),points=7,pax_samples_per_point=2,
                undriven_baseline_s=.2,stage_settle_s=.01,vpi_fit_max_command_v=.45)
    return Recipe(experiment='eom_vpi_hysteresis',label='test',config=config|changes)


def test_mock_lifecycle_baseline_order_bounds_turnaround_and_report(tmp_path):
    session=MockAcquisitionSession(small_recipe(tmp_path).acquisition_config())
    state=execute(small_recipe(tmp_path),session_factory=lambda:session)
    from pathlib import Path
    directory=Path(state['directory'])
    assert state['status']==state['cleanup_status']==state['report_status']=='COMPLETED'
    rows=list(csv.DictReader((directory/'data.csv').open()))
    baseline=[row for row in rows if row['direction']=='undriven']
    assert baseline and rows[0]['direction']=='undriven'
    assert all(float(row['out1_command_v'])==float(row['out2_command_v'])==0 for row in baseline)
    for direction,expected in [('forward',np.linspace(0,.9,7)),('backward',np.linspace(.9,0,7))]:
        selected=[row for row in rows if row['direction']==direction]
        assert [float(row['command_v']) for row in selected[::2]]==pytest.approx(expected)
        assert all(float(row['out2_command_v'])==0 for row in selected)
    events=[json.loads(line) for line in (directory/'events.jsonl').read_text().splitlines()]
    assert len([event for event in events if event['kind']=='stage_pause'])==4
    assert session.commands==[0.,0.]
    assert json.loads((directory/'analysis.json').read_text())['status']=='SYNTHETIC_ONLY'
    assert (directory/'report.pdf').read_bytes().startswith(b'%PDF')
    assert not (directory/'pd_raw.npy').exists()


def test_failure_retains_partial_data_and_zeros(tmp_path):
    class Broken(MockAcquisitionSession):
        def read_pax(self):
            if self.pax_index>=2:
                raise RuntimeError('PAX lost')
            return super().read_pax()
    config=small_recipe(tmp_path)
    session=Broken(config.acquisition_config())
    state=execute(config,session_factory=lambda:session)
    from pathlib import Path
    assert state['status']=='FAILED' and state['cleanup_status']=='COMPLETED'
    assert session.commands==[0.,0.]
    assert len(list(csv.DictReader((Path(state['directory'])/'data.csv').open())))==2


@pytest.mark.parametrize('changes',[{'stop_command_v':1.}, {'other_eom_command_v':.1},
    {'ramp_duration_s':float('nan')},{'undriven_baseline_s':-1},{'points':4}])
def test_invalid_settings_before_session_or_artifacts(tmp_path,changes):
    calls=[]
    with pytest.raises(ValueError):
        execute(small_recipe(tmp_path,**changes),session_factory=lambda:calls.append(True))
    assert not calls and not list(tmp_path.iterdir())


def test_hardware_requires_mapping_and_chain_before_connect(tmp_path):
    with pytest.raises(ValueError,match='output_map_confirmed'):
        execute(small_recipe(tmp_path),mode='hardware',session_factory=lambda:pytest.fail('connected'))
    assert not list(tmp_path.iterdir())


def test_pax_only_adapter_orders_read_before_rp_and_records_zero():
    from types import SimpleNamespace
    from polarization_locking.hardware.pax_interface import PAXReading
    from high_speed_polarimeter.hardware.vpi import VpiSession
    calls=[]
    class RP:
        p=None
        asg1=SimpleNamespace(offset=0.)
        asg2=SimpleNamespace(offset=0.)
        def connect(self): self.p=True;calls.append('rp_connect')
        def ramp_output_voltage(self,*values,**options):
            calls.append(('ramp',values,options))
            self.asg1.offset,self.asg2.offset=values
        def set_output_zero(self): self.asg1.offset=self.asg2.offset=0.
        def disconnect(self): self.set_output_zero();self.p=None
    class PAX:
        last_raw_record={'timestamp':1.}
        def connect(self): calls.append('pax_connect')
        def read_fresh_polarization(self):
            calls.append('fresh_read')
            return PAXReading(timestamp=1,theta=0,eta=0,s1=1,s2=0,s3=0,dop=.1,ptotal=.001)
        def disconnect(self): calls.append('pax_disconnect')
    session=VpiSession(VpiConfig())
    session.rp,session.pax=RP(),PAX()
    session.connect()
    assert calls[:3]==['pax_connect','fresh_read','rp_connect']
    assert session.describe()['pd'] is None
    assert session.command([.1,0])['asg_offset_settings_v']==[.1,0]
    assert session.read_pax()['interface_reading']['dop']==.1  # Retained, no purity cut.
    session.disconnect()
    assert session.cleanup_evidence['final_asg_offset_settings_v']==[0.,0.]
