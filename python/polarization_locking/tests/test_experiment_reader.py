"""Lossless text evidence, explicit diagnostics and bounded read-only inspection."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from experiment_reader import (
    METADATA_BYTES, ReaderError, RunInfo, inspect_run, iter_records, read_metadata,
)


def write_json(path, value):
    path.write_text(json.dumps(value))


def snapshot(root):
    return {str(p.relative_to(root)): (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
            for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('state,family', [
    ({'test': 'cross-sweep', 'status': 'cleanup_failed'}, 'polarization_locking'),
    ({'schema_version': 1, 'experiment': 'single_eom_characterization', 'status': 'RUNNING'}, 'high_speed_polarimeter'),
    ({'schema_version': 1, 'purpose': 'steps', 'measurement_definitions': {}}, 'raw_calibration'),
])
def test_family_native_metadata_and_independent_outcomes(tmp_path, state, family):
    state.update(cleanup_status='FAILED', quality_evaluation_status='COMPLETED', report_status='COMPLETED', unknown={'channel': 'IN2', 'unit': 'V'})
    write_json(tmp_path/'run.json', state)
    write_json(tmp_path/'quality.json', {'schema_version': 1, 'status': 'UNKNOWN'})
    # Recipe/acquisition versions are independent and never borrowed from run.json.
    write_json(tmp_path/'recipe.json', {'schema_version': 8})
    write_json(tmp_path/'acquisition.json', {'format_version': 1, 'clock': 'native'})
    before = snapshot(tmp_path)
    run = inspect_run(tmp_path)
    assert run.family == family
    assert run.metadata['run.json'] == state
    assert run.outcomes == dict(execution=state.get('status'), cleanup='FAILED', quality_evaluation='COMPLETED', scientific_verdict='UNKNOWN', reporting='COMPLETED')
    assert run.versions['acquisition.json'] == {'format_version': 1}
    assert run.versions['recipe.json'] == {'schema_version': 8}
    assert any(d.code == 'unsupported' and d.path.name == 'recipe.json' for d in run.diagnostics)
    assert snapshot(tmp_path) == before


def test_unknown_outcomes_ambiguity_and_future_schema(tmp_path):
    write_json(tmp_path/'run.json', {'test': 'sweep', 'status': 'cleanup_failed'})
    run = inspect_run(tmp_path)
    assert run.outcomes == dict(execution='cleanup_failed', cleanup=None, quality_evaluation=None, scientific_verdict=None, reporting=None)
    write_json(tmp_path/'run.json', {'test': 'sweep', 'schema_version': 1, 'experiment': 'mock_lifecycle'})
    run = inspect_run(tmp_path)
    assert run.family == 'unknown'
    assert any(d.code == 'ambiguous' for d in run.diagnostics)
    write_json(tmp_path/'run.json', {'schema_version': 99, 'experiment': 'mock_lifecycle'})
    run = inspect_run(tmp_path)
    assert run.family == 'unknown'
    assert run.metadata['run.json']['schema_version'] == 99
    assert any(d.code == 'unsupported' for d in run.diagnostics)


def test_exact_csv_and_jsonl_unknowns_and_incomplete_events(tmp_path):
    (tmp_path/'data.csv').write_bytes(b'raw_v,command_v,channel,note\r\n-0.00,0,IN2,"a,b\r\nc"\r\n')
    event = {'kind': 'acquisition_error', 'elapsed_s': 1.25, 'raw': {'nonfinite': 'nan'}, 'extra': [None, True, -3]}
    (tmp_path/'events.jsonl').write_text(json.dumps(event))  # valid final record without newline
    run = inspect_run(tmp_path/'data.csv')
    assert list(iter_records(run, 'data.csv')) == [{'raw_v': '-0.00', 'command_v': '0', 'channel': 'IN2', 'note': 'a,b\r\nc'}]
    assert list(iter_records(run, 'events.jsonl')) == [event]
    assert run.headers['data.csv'] == ['raw_v', 'command_v', 'channel', 'note']


@pytest.mark.parametrize('text,code', [
    ('a,a\n1,2\n', 'ambiguous'),
    ('a,b\n1\n', 'malformed'),
    ('a\n1,2\n', 'malformed'),
    ('a\n\n', 'malformed'),
    ('a\n"unterminated', 'malformed'),
    ('a\n"x"junk\n', 'malformed'),
    (',a\n1,2\n', 'malformed'),
])
def test_malformed_csv_never_silently_discards_or_fills(tmp_path, text, code):
    (tmp_path/'data.csv').write_text(text)
    before = snapshot(tmp_path)
    with pytest.raises(ReaderError) as failure:
        list(iter_records(RunInfo(tmp_path), 'data.csv'))
    assert failure.value.diagnostic.code == code
    assert failure.value.diagnostic.path == tmp_path/'data.csv'
    assert failure.value.diagnostic.line
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize('text,code', [
    ('', 'malformed'), ('{"a":1,"a":2}', 'malformed'),
    ('{"nested":{"a":1,"a":2}}', 'malformed'),
    ('{"a":', 'malformed'), ('[]', 'unsupported'),
])
def test_bad_metadata_and_jsonl_have_source_diagnostics(tmp_path, text, code):
    (tmp_path/'run.json').write_text(text)
    (tmp_path/'events.jsonl').write_text(text + '\n')
    before = snapshot(tmp_path)
    run = inspect_run(tmp_path)
    assert any(d.path.name == 'run.json' and d.code == code for d in run.diagnostics)
    with pytest.raises(ReaderError) as failure:
        read_metadata(run, 'run.json')
    assert failure.value.diagnostic.code == code
    with pytest.raises(ReaderError) as failure:
        list(iter_records(run, 'events.jsonl'))
    assert failure.value.diagnostic.code == code
    assert failure.value.diagnostic.line == 1
    assert snapshot(tmp_path) == before


def test_missing_empty_header_only_and_unsupported(tmp_path):
    run = inspect_run(tmp_path/'absent')
    assert run.diagnostics[0].code == 'missing'
    run = RunInfo(tmp_path)
    for name, text in [('empty.csv', ''), ('header.csv', 'a,b\n'), ('empty.jsonl', '')]:
        (tmp_path/name).write_text(text)
        assert list(iter_records(run, name)) == []
    for operation in [lambda: read_metadata(run, 'absent.json'), lambda: list(iter_records(run, 'absent.csv'))]:
        with pytest.raises(ReaderError, match='missing'):
            operation()
    with pytest.raises(ReaderError, match='unsupported'):
        list(iter_records(run, 'array.npy'))


def test_jsonl_is_lazy_and_stops_at_malformed_record(tmp_path):
    (tmp_path/'events.jsonl').write_text('{"n":1}\n{"n":\n{"n":3}\n')
    iterator = iter_records(RunInfo(tmp_path), 'events.jsonl')
    assert next(iterator) == {'n': 1}
    with pytest.raises(ReaderError) as failure:
        next(iterator)
    assert failure.value.diagnostic.line == 2


def test_bounds_and_explicit_large_metadata(tmp_path):
    write_json(tmp_path/'run.json', {'test': 'sweep', 'large': 'x' * METADATA_BYTES})
    run = inspect_run(tmp_path)
    assert 'run.json' not in run.metadata
    assert any(d.code == 'oversized' for d in run.diagnostics)
    assert read_metadata(run, 'run.json', max_bytes=None)['test'] == 'sweep'
    (tmp_path/'data.csv').write_text('a\n"123\n456"\n')
    with pytest.raises(ReaderError, match='oversized'):
        list(iter_records(run, 'data.csv', max_record_bytes=6))


def test_inspection_never_reads_bulk_or_external_references(tmp_path, monkeypatch):
    write_json(tmp_path/'run.json', {'test': 'sweep', 'external': '/not/a/source.json'})
    (tmp_path/'data.csv').write_text('a\n1\n')
    (tmp_path/'pd_raw.npy').write_bytes(b'not decoded')
    (tmp_path/'campaign.json').write_text('deliberately not parsed')
    original = Path.open

    def bounded_open(path, *args, **kwargs):
        assert path.name not in {'pd_raw.npy', 'campaign.json', 'source.json'}
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', bounded_open)
    run = inspect_run(tmp_path)
    assert run.family == 'polarization_locking'
    assert 'pd_raw.npy' in run.artifacts
    iterator = iter_records(run, 'missing.csv')  # creating iterator performs no IO
    iterator.close()


def test_changed_file_diagnosed_on_iteration(tmp_path):
    path = tmp_path/'events.jsonl'
    path.write_text('{"n":1}\n{"n":2}\n')
    iterator = iter_records(RunInfo(tmp_path), 'events.jsonl')
    assert next(iterator) == {'n': 1}
    with path.open('a') as stream:
        stream.write('{"n":3}\n')
    with pytest.raises(ReaderError, match='changed'):
        next(iterator)


def test_no_external_symlink_or_parent_access(tmp_path):
    run_dir = tmp_path/'run'
    run_dir.mkdir()
    write_json(tmp_path/'external.json', {'secret': 'not opened'})
    (run_dir/'run.json').symlink_to(tmp_path/'external.json')
    run = inspect_run(run_dir)
    assert any(d.code == 'unsupported' for d in run.diagnostics)
    for name in ['../external.json', str(tmp_path/'external.json'), 'run.json']:
        with pytest.raises(ReaderError, match='unsupported'):
            read_metadata(run, name)


def test_read_only_inputs_unchanged_on_success_and_failure(tmp_path):
    write_json(tmp_path/'run.json', {'test': 'sweep'})
    (tmp_path/'data.csv').write_text('a\n-1\n')
    (tmp_path/'bad.jsonl').write_text('{broken\n')
    before = snapshot(tmp_path)
    entries = sorted(p.name for p in tmp_path.iterdir())
    for path in tmp_path.iterdir():
        path.chmod(0o444)
    tmp_path.chmod(0o555)
    try:
        run = inspect_run(tmp_path)
        assert list(iter_records(run, 'data.csv')) == [{'a': '-1'}]
        with pytest.raises(ReaderError):
            list(iter_records(run, 'bad.jsonl'))
        assert snapshot(tmp_path) == before
        assert sorted(p.name for p in tmp_path.iterdir()) == entries
    finally:
        tmp_path.chmod(0o755)
        for path in tmp_path.iterdir():
            path.chmod(0o644)


def test_reader_in_fresh_guarded_process(tmp_path):
    write_json(tmp_path/'run.json', {'schema_version': 1, 'experiment': 'eom_vpi_hysteresis'})
    (tmp_path/'events.jsonl').write_text('{"kind":"acquisition_interrupted"}\n')
    script = '''
import sys, sitecustomize
assert sitecustomize.OFFLINE_GUARD_ACTIVE
from experiment_reader import inspect_run, read_metadata, iter_records
run = inspect_run(sys.argv[1])
assert read_metadata(run, 'run.json')['experiment'] == 'eom_vpi_hysteresis'
assert list(iter_records(run, 'events.jsonl'))
assert not any(name.startswith(('polarization_locking', 'high_speed_polarimeter', 'numpy', 'matplotlib', 'pyrpl', 'yaqc', 'pyvisa')) for name in sys.modules)
'''
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def test_inspection_bounds_directory_header_and_invalid_utf8(tmp_path):
    (tmp_path/'data.csv').write_bytes(b'a\n\xff\n')
    run = inspect_run(tmp_path)
    assert run.headers['data.csv'] == ['a']  # body is not decoded during inspection
    with pytest.raises(ReaderError, match='malformed'):
        list(iter_records(run, 'data.csv'))
    (tmp_path/'data.csv').write_bytes(b'a' * (64 * 1024 + 1))
    assert any(d.code == 'oversized' for d in inspect_run(tmp_path).diagnostics)
    (tmp_path/'data.csv').unlink()
    for index in range(257):
        (tmp_path/f'{index}.bin').touch()
    run = inspect_run(tmp_path)
    assert len(run.artifacts) == 256
    assert any(d.code == 'oversized' for d in run.diagnostics)


def test_changing_metadata_and_malformed_jsonl_tail(tmp_path, monkeypatch):
    import experiment_reader as reader
    write_json(tmp_path/'run.json', {'test': 'sweep'})
    original = reader._signature
    calls = []

    def changed_signature(path):
        signature = original(path)
        calls.append(path)
        return signature if len(calls) == 1 else (*signature[:-1], signature[-1] + 1)

    with monkeypatch.context() as patch:
        patch.setattr(reader, '_signature', changed_signature)
        with pytest.raises(ReaderError, match='changed'):
            read_metadata(RunInfo(tmp_path), 'run.json')
    (tmp_path/'events.jsonl').write_text('{"kind":"pax_sample"}\n{"kind":')
    iterator = iter_records(RunInfo(tmp_path), 'events.jsonl')
    assert next(iterator) == {'kind': 'pax_sample'}
    with pytest.raises(ReaderError) as failure:
        next(iterator)
    assert failure.value.diagnostic.line == 2


def test_relative_location_and_missing_summary_remain_explicit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path.parent)
    write_json(tmp_path/'chosen.json', {'native': '-0.00'})
    run = RunInfo(tmp_path.name)
    assert read_metadata(run, 'chosen.json') == {'native': '-0.00'}
    missing = inspect_run(tmp_path/'not-created')
    assert missing.outcomes == dict.fromkeys(('execution', 'cleanup', 'quality_evaluation', 'scientific_verdict', 'reporting'))
