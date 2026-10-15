"""Synthetic acquisition contracts, including compound acquisition/cleanup faults."""
import json
from pathlib import Path

import numpy as np
import pytest

from high_speed_polarimeter.config import Recipe
from high_speed_polarimeter.hardware import MockAcquisitionSession
from high_speed_polarimeter.quality import reevaluate
from high_speed_polarimeter.runner import execute

CONTRACT = json.loads((Path(__file__).resolve().parents[3] /
                       'tests/fixtures/scientific/contracts.json').read_text())['eom']


@pytest.mark.parametrize('fault', [None, 'interrupt', 'read_failure'])
def test_ordered_acquisition_retains_raw_evidence_after_compound_fault(tmp_path, fault):
    recipe = Recipe(experiment='single_eom_characterization', label='synthetic-contract',
                    config={'results_directory': str(tmp_path), **CONTRACT['config']}).resolved()
    observed = []

    class Session(MockAcquisitionSession):
        def connect(self):
            # The retained RUNNING state must exist before connecting.
            run = next(tmp_path.glob('*/*/run.json'))
            state = json.loads(run.read_text())
            assert state['status'] == 'RUNNING'
            assert state['cleanup_status'] == 'NOT_STARTED'
            assert state['schema_version'] == 1 and state['mode'] == 'mock'
            assert state['provenance']['sources'][0]['files']['runner.py']
            observed.append('connect')
            super().connect()

        def read_pax(self):
            observed.append('pax')
            if fault and self.pax_index == 1:
                raise KeyboardInterrupt('synthetic interrupt') if fault == 'interrupt' else RuntimeError('synthetic read failure')
            return super().read_pax()

        def command(self, values):
            observed.append(('command', list(values)))
            return super().command(values)

        def capture_pd(self):
            observed.append('pd')
            return super().capture_pd()

        def disconnect(self):
            observed.append('disconnect')
            super().disconnect()
            if fault:
                raise RuntimeError('synthetic cleanup failure after mock zero')

    session = Session(recipe.acquisition_config())
    state = execute(recipe, session_factory=lambda: session)
    directory = Path(state['directory'])
    events = [json.loads(line) for line in (directory/'events.jsonl').read_text().splitlines()]
    expected = CONTRACT['prefix'] + (CONTRACT['partial_events'] if fault else
               CONTRACT['point_events'] * 2 + ['acquisition_completed'])
    assert [event['kind'] for event in events] == expected
    expected_calls = ['connect', 'pax', ('command', [0., 0.]), 'pd', 'pax']
    if not fault:
        expected_calls += [('command', [.1, 0.]), 'pd', 'pax']
    assert observed == expected_calls + ['disconnect']
    assert state['status'] == {None: 'COMPLETED', 'interrupt': 'INTERRUPTED',
                               'read_failure': 'FAILED'}[fault]
    assert state['cleanup_status'] == ('FAILED' if fault else 'COMPLETED')
    assert state['quality_evaluation_status'] == state['report_status'] == 'COMPLETED'
    assert session.commands == [0., 0.]  # Mock state only; no delivered-voltage claim.
    if fault:
        assert 'synthetic cleanup failure' in state['cleanup_error']
        assert ('KeyboardInterrupt' if fault == 'interrupt' else 'RuntimeError') in state['error']
    metadata = json.loads((directory/'acquisition.json').read_text())
    assert metadata['schema_version'] == 1 and metadata['mode'] == 'mock'
    assert metadata['expected_points'] == 2
    assert metadata['command_units'] == {
        'eom_commands_v': 'RP command volts, EOM1/EOM2 identity order',
        'rp_commands_v': 'RP command volts, OUT1/OUT2 order',
        'asg_offset_settings_v': 'ASG register/settings readback; not measured delivered voltage'}
    captures = [event for event in events if event['kind'] == 'pd_capture']
    with (directory/'pd_raw.npy').open('rb') as stream:
        for event in captures:
            assert stream.tell() == event['byte_offset']
            trace = np.load(stream, allow_pickle=False)
            assert stream.tell() == event['byte_end']
            assert trace.dtype == np.float64 and trace.shape == (16384,)
            assert event['units'] == 'V' and event['sample_count'] == 16384
            np.testing.assert_array_equal(trace, np.tile(CONTRACT['signed_pd_period_v'], 2048))
        assert stream.read() == b''
    assert len(captures) == (1 if fault else 2)
    raw_names = ['recipe.json', 'run.json', 'acquisition.json', 'events.jsonl', 'pd_raw.npy']
    original = {name: (directory/name).read_bytes() for name in raw_names}
    quality_before = (directory/'quality.json').read_bytes()
    report_before = (directory/'report.md').read_text()
    assert reevaluate(directory).status == ('FAIL' if fault else 'UNKNOWN')
    assert quality_before == (directory/'quality.json').read_bytes()
    assert report_before == (directory/'report.md').read_text()
    assert original == {name: (directory/name).read_bytes() for name in raw_names}
    assert f"Execution: {state['status']}" in report_before
