"""Bounded PAX-only acquisition, explicit averaging and analytic model checks."""
import csv
import json
from dataclasses import replace
from pathlib import Path

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


def synthetic_hardware_evidence(tmp_path):
    """Hardware-shaped test evidence only; this session never imports drivers."""
    class SyntheticSession(MockAcquisitionSession):
        mode = 'hardware'

        def read_pax(self):
            reading = super().read_pax()
            phase = np.pi*self.commands[0]/.775
            reading['interface_reading'].update(zip(('s1','s2','s3'), directions(np.array([phase]))[0]))
            # Exercise the hardware-evidence branch using explicitly synthetic fixtures.
            reading['synthetic'] = False
            return reading

    recipe = small_recipe(tmp_path, output_map_confirmed=True,
                          voltage_chain='SYNTHETIC TEST ONLY', pax_reference_plane='NO HARDWARE')
    session = SyntheticSession(recipe.acquisition_config())
    state = execute(recipe, mode='hardware', session_factory=lambda: session)
    assert state['status'] == state['cleanup_status'] == state['report_status'] == 'COMPLETED'
    return Path(state['directory'])


def test_offline_vpi_checks_complete_evidence_without_claiming_calibration(tmp_path):
    from high_speed_polarimeter.quality_vpi import evaluate
    directory = synthetic_hardware_evidence(tmp_path)
    raw = {name:(directory/name).read_bytes() for name in
           ('recipe.json','run.json','acquisition.json','events.jsonl','data.csv')}
    quality = evaluate(directory).to_dict()
    assert quality['status'] == 'UNKNOWN'
    assert next(check for check in quality['checks'] if check['name']=='acquisition_evidence')['status'] == 'PASS'
    analysis = json.loads((directory/'analysis.json').read_text())
    assert analysis['cycles'][0]['status'] == 'MODEL_SUPPORTED'
    assert analysis['cycles'][0]['fits'][0]['vpi_linear_branch_command_v'] == pytest.approx(.775)
    assert raw == {name:(directory/name).read_bytes() for name in raw}


@pytest.mark.parametrize('fault,expected', [
    ('missing_metadata','acquisition.json'), ('missing_events','events.jsonl'),
    ('malformed_events','events.jsonl'), ('incomplete_events','completion'),
    ('missing_sample','event count'), ('direction_mismatch','disagree'),
    ('constant_host_time','elapsed_s'), ('constant_device_time','pax_timestamp'),
])
def test_offline_vpi_rejects_invalid_evidence_before_model_fit(tmp_path, monkeypatch, fault, expected):
    from high_speed_polarimeter.quality import reevaluate
    directory = synthetic_hardware_evidence(tmp_path)
    events_path, csv_path = directory/'events.jsonl', directory/'data.csv'
    if fault == 'missing_metadata':
        (directory/'acquisition.json').unlink()
    elif fault == 'missing_events':
        events_path.unlink()
    elif fault == 'malformed_events':
        events_path.write_text('{broken\n')
    elif fault in {'incomplete_events','missing_sample'}:
        events = [json.loads(line) for line in events_path.read_text().splitlines()]
        if fault == 'incomplete_events':
            events.pop()
        else:
            events.pop(next(i for i,event in enumerate(events) if event['kind']=='pax_sample'))
        events_path.write_text(''.join(json.dumps(event)+'\n' for event in events))
    else:
        with csv_path.open(newline='') as stream:
            reader = csv.DictReader(stream)
            fields, rows = reader.fieldnames, list(reader)
        if fault == 'direction_mismatch':
            rows[-1]['s1'] = '.9'
        else:
            field = 'elapsed_s' if fault == 'constant_host_time' else 'pax_timestamp'
            for row in rows:
                row[field] = '1'
            events = [json.loads(line) for line in events_path.read_text().splitlines()]
            for event in events:
                if event['kind'] == 'pax_sample':
                    if field == 'elapsed_s':
                        event['received_s'] = 1.
                    else:
                        event['interface_reading']['timestamp'] = 1.
                        event['raw_record']['timestamp'] = 1.
            events_path.write_text(''.join(json.dumps(event)+'\n' for event in events))
        with csv_path.open('w',newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader(); writer.writerows(rows)
    raw = {path.name:path.read_bytes() for path in directory.iterdir()
           if path.name in {'recipe.json','run.json','acquisition.json','events.jsonl','data.csv'}}
    monkeypatch.setattr('high_speed_polarimeter.analysis.vpi.estimate',
                        lambda *args: pytest.fail('Invalid acquisition reached optical model'))
    quality = reevaluate(directory).to_dict()
    assert quality['status'] == 'FAIL'
    check = next(check for check in quality['checks'] if check['name']=='acquisition_evidence')
    assert check['status'] == 'FAIL' and expected in check['message']
    analysis = json.loads((directory/'analysis.json').read_text())
    assert analysis['status'] == 'INVALID_ACQUISITION' and analysis['cycles'] == []
    assert 'Vπ unavailable:' in (directory/'report.md').read_text()
    assert raw == {name:(directory/name).read_bytes() for name in raw}
