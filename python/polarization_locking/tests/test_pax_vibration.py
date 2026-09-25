"""Synthetic noise, sphere wrapping, and static motor-on/off acquisition checks."""
import csv
import json
from math import isclose
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from polarization_locking.catalog import BY_KEY, default_options
from polarization_locking.config import PolarizationLockConfig
from polarization_locking.hardware.pax_interface import PAXController
from polarization_locking.routines import pax_vibration as vibration
from polarization_locking.settings import validate
from polarization_locking.tests.test_stokes_phase_sweep import Clock, reading


def test_normalized_variance_is_sample_variance_not_peak_to_peak():
    n, fs = 32768, 4096
    t = np.arange(n)/fs
    # Large slow drift lies below the band; 64-Hz vibration lies inside it.
    trace = -.02 - .2 + .04*np.sin(2*np.pi*.5*t) + .006*np.sin(2*np.pi*64*t)
    stats, normalized, freq, psd = vibration.trace_statistics(trace, 1/fs, -.02, (5, 1000))
    assert stats['variance_v2'] == pytest.approx(trace.var(ddof=1))
    assert stats['normalized_variance'] == pytest.approx(np.var(normalized, ddof=1))
    assert normalized.mean() == pytest.approx(1)
    assert stats['band_variance_v2'] == pytest.approx(.006**2/2, rel=1e-4)
    assert stats['normalized_band_variance'] == pytest.approx((.006/.2)**2/2, rel=1e-4)
    assert freq[np.argmax(psd[10:])+10] == 64


def test_pooled_variance_retains_between_capture_drift_and_signed_excess():
    rng = np.random.default_rng(71)
    traces = [.2 + rng.normal(0,.001,4096), .24 + rng.normal(0,.001,4096)]
    rows = []
    for i, trace in enumerate(traces):
        stats, *_ = vibration.trace_statistics(trace, 1/4096, .01, (5,1000))
        rows.append(stats | {'started_s': i, 'finished_s': i+1})
    summary = vibration.summarize_pd(rows, .01)
    joined = np.concatenate(traces)
    assert summary['variance_v2'] == pytest.approx(joined.var(ddof=1))
    assert summary['normalized_variance'] == pytest.approx(joined.var(ddof=1)/(joined.mean()-.01)**2)
    result = vibration.compare({'normalized_band_variance': 1}, {'normalized_band_variance': 2})
    assert result['normalized_band_variance_on_minus_off'] == -1
    assert result['normalized_band_variance_on_over_off'] == .5


def test_clipping_and_zero_baseline_leave_raw_stats_but_no_normalized_metrics():
    for trace in (np.full(4096, 1.), np.full(4096, .01)):
        stats, normalized, *_ = vibration.trace_statistics(trace, 1/4096, .01, (5,1000))
        assert stats['normalized_variance'] is None
        assert np.all(np.isnan(normalized))


def test_sphere_variance_unwraps_u_and_marks_poles():
    rows = [dict(pax_ptotal=.001, u_rad=np.deg2rad(u), v_rad=np.pi/2, dop=.99, elapsed_s=i)
            for i,u in enumerate((179,-179,178,-178))]
    result = vibration.summarize_pax(rows,1e-6)
    assert result['u_variance_rad2'] == pytest.approx(np.var(np.deg2rad([179,181,178,182]),ddof=1))
    assert result['v_variance_rad2'] == 0
    rows[0]['v_rad']=0
    assert vibration.summarize_pax(rows,1e-6)['u_variance_rad2'] is None


def test_motor_stop_uses_existing_serial_interface_and_resets_freshness():
    pax=PAXController(PolarizationLockConfig())
    pax.client=Mock()
    pax._last_fresh_record=(1,2)
    pax.stop_rotation()
    pax.client.direct_serial_write.assert_called_once_with(b'INPut:ROTation:STATe 0')
    assert pax._last_fresh_record is None


@pytest.mark.parametrize('changes', [dict(pd_input='out1'),dict(pax_vibration_scope_decimation=3),
    dict(pax_vibration_band_high_hz=100000),dict(pax_vibration_motor_settle_s=0),
    dict(pax_vibration_phi1_bias_voltage=2)])
def test_invalid_acquisition_settings(changes):
    case=BY_KEY['pax-vibration']
    assert default_options(case)['duration_s']==30
    with pytest.raises(ValueError):
        validate(PolarizationLockConfig(**changes),case,default_options(case))


@pytest.mark.parametrize('failure', [None, RuntimeError('PAX lost'), KeyboardInterrupt()])
def test_motor_on_off_capture_keeps_fast_data_and_cleans_up(tmp_path, monkeypatch, failure):
    clock=Clock()
    monkeypatch.setattr(vibration,'time',clock)
    monkeypatch.setattr('polarization_locking.routines.visibility.time',clock)
    monkeypatch.setattr(vibration,'_scope_sleep',clock.sleep)
    monkeypatch.setattr(vibration,'prepare_setup',lambda message: None)
    motor_off=False
    class Scope:
        input1='in1'; duration=.25; average=False; trigger_source='ext_positive_edge'; trigger_delay=.1
        ch1_active=False; ch2_active=True; rolling_mode=True; trace_average=4; _trigger_delay_register=77
        @property
        def sampling_time(self): return self.duration/4096
        def setup(self,**kw): vars(self).update(kw)
        def single(self,**kw): return np.full((2,4096),-.013)
        def stop(self): self.stopped=True
        def single_async(self):
            current=self
            class Future:
                cancelled=False
                deadline=clock.now+current.duration
                def done(self): return self.cancelled or clock.now>=self.deadline
                def cancel(self): self.cancelled=True
                def result(self):
                    assert self.done()
                    t=np.arange(4096)*current.sampling_time
                    amplitude=.001 if motor_off else .006
                    voltage=.2+amplitude*np.sin(2*np.pi*60*t)
                    return np.array([voltage,voltage])
            future=Future(); futures.append(future); return future
    scope=Scope(); futures=[]
    rp,pax=Mock(),Mock()
    rp.p.rp.scope=scope
    count=0
    def read():
        nonlocal count
        assert not motor_off  # no fabricated PAX readings in the off window
        count+=1; clock.sleep(.06)
        if failure and count==25: raise failure
        pax.last_raw_record={'measurement_id':count,'extra_status':42}
        return reading(count, s1=0,s2=np.cos(.2),s3=np.sin(.2),theta=np.pi/4,eta=.1)
    def stop_motor():
        nonlocal motor_off
        motor_off=True
    pax.read_fresh_polarization.side_effect=read
    pax.stop_rotation.side_effect=stop_motor
    pax.client.get_wavelength.return_value=830
    config=PolarizationLockConfig(pd_input='in2')
    if failure:
        with pytest.raises(type(failure)):
            vibration.acquire(rp,pax,config,str(tmp_path/'data.csv'),2.2)
    else:
        vibration.acquire(rp,pax,config,str(tmp_path/'data.csv'),2.2)
    assert motor_off and scope.stopped and scope.input1=='in1' and scope.duration==.25
    assert scope._trigger_delay_register==77
    pax.stop_rotation.assert_called_once()
    rp.set_output_voltage.assert_called_once_with(0,0)
    rp.set_phase_waveform.assert_not_called()
    assert not list(tmp_path.glob('*.npz'))
    assert list(tmp_path.glob('*.csv')) == [tmp_path/'data.csv']
    records=list(csv.DictReader((tmp_path/'data.csv').open()))
    assert tuple(records[0]) == vibration.CSV_FIELDS
    assert any(row['capture']=='-1' and float(row['voltage_v']) < 0 for row in records)
    derived = vibration.compact_report_rows(tmp_path/'data.csv')
    rows=[row for row in derived if row['record_type']=='pd_capture']
    pax_rows=[row for row in derived if row['record_type']=='pax']
    assert pax_rows and all(row['condition']=='pax_on' for row in pax_rows)
    assert float(pax_rows[0]['u_rad'])==pytest.approx(.2)
    assert float(pax_rows[0]['v_rad'])==pytest.approx(np.pi/2)
    summary=json.loads((tmp_path/'vibration.json').read_text())
    assert summary['pd']['pax_on']['sample_count']>0
    if failure:
        assert futures[-1].cancelled
        assert summary['pd']['pax_off']['sample_count']==0
    else:
        assert {row['condition'] for row in rows}=={'pax_on','pax_off'}
        assert summary['comparison']['normalized_band_variance_on_over_off']==pytest.approx(36,rel=.01)
        assert summary['pd']['pax_on']['sampling_rate_hz']==summary['pd']['pax_off']['sampling_rate_hz']
        samples=[row for row in records if row['capture']==str(rows[0]['capture'])]
        voltage=np.array([float(row['voltage_v']) for row in samples])
        normalized=(voltage-rows[0]['dark_voltage_v'])/(voltage.mean()-rows[0]['dark_voltage_v'])
        assert len(samples)==int(rows[0]['sample_count'])
        assert normalized.mean()==pytest.approx(1)
        assert np.var(normalized,ddof=1)==pytest.approx(float(rows[0]['normalized_variance']))
        assert any(row['record_type']=='spectrum' for row in derived)
        for condition in ('pax_on', 'pax_off'):
            rebuilt=vibration.summarize_pd([row for row in rows if row['condition']==condition], rows[0]['dark_voltage_v'])
            assert rebuilt['normalized_band_variance']==pytest.approx(summary['pd'][condition]['normalized_band_variance'])
        from polarization_locking.reports.run_report import create_run_report
        paths=SimpleNamespace(directory=tmp_path,csv=tmp_path/'data.csv',pdf=tmp_path/'report.pdf')
        case=BY_KEY['pax-vibration']
        status=create_run_report(case,paths,config,default_options(case),{'status':'completed'})
        assert status['report_status']=='completed'
        assert paths.pdf.read_bytes().startswith(b'%PDF-')
