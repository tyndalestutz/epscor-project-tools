"""Development diagnostics retain data when cleanup fails; no instrument access."""
import csv
import json
from types import SimpleNamespace

import pytest

from polarization_locking.analysis import verify_pax_power_hold as hold


def test_pax_cleanup_failure_does_not_lose_partial_records(tmp_path, monkeypatch):
    class RP:
        p = None
        def __init__(self, config):
            pass
        def connect(self):
            self.p = object()
        def ramp_output_voltage(self, *args, **kwargs):
            return {}
        def disconnect(self):
            self.p = None

    class PAX:
        last_raw_record = {"timestamp": 1.}
        count = 0
        def __init__(self, config):
            pass
        def connect(self):
            pass
        def read_fresh_polarization(self):
            self.count += 1
            if self.count == 3:
                raise KeyboardInterrupt()
            return SimpleNamespace(s1=1., s2=0., s3=0., dop=.9, ptotal=.001,
                                   theta=0., eta=0., revisions=1., adc_min=1., adc_max=2., rev_time=1.)
        def disconnect(self):
            raise RuntimeError('test cleanup failure')

    monkeypatch.setattr(hold, 'RPController', RP)
    monkeypatch.setattr(hold, 'PAXController', PAX)
    monkeypatch.setattr(hold.time, 'sleep', lambda seconds: None)
    with pytest.raises(RuntimeError, match='cleanup failure'):
        hold.run(tmp_path / 'run', duration_s=10., settle_s=0.)
    summary = json.loads((tmp_path / 'run/summary.json').read_text())
    assert summary['status'] == 'interrupted'
    assert 'KeyboardInterrupt' in summary['error']
    assert 'test cleanup failure' in summary['pax_cleanup_error']
    with (tmp_path / 'run/data.csv').open() as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 1 and rows[0]['pax_raw_json']


@pytest.mark.parametrize('duration,settle', [(0., 0.), (-1., 0.), (1., -1.), (float('nan'), 0.)])
def test_invalid_hold_timing_rejected_before_allocation(tmp_path, duration, settle):
    with pytest.raises(ValueError):
        hold.run(tmp_path / 'run', duration_s=duration, settle_s=settle)
    assert not (tmp_path / 'run').exists()
