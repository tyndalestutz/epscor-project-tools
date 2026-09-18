import math
import json
import numpy as np
import pytest
from polarization_locking.raw_calibration import command_plan, consolidate_scope
from polarization_locking.reports.raw_calibration import analyze


def test_raw_plan_covers_both_directions_and_all_repeats():
    plan=command_plan(0,.8,.02,3)
    assert len(plan)==246
    assert plan[0]==(1,'forward',0,0.0)
    assert plan[40][3]==.8 and plan[41][3]==.8
    assert plan[81][3]==0 and plan[-1][3]==0


@pytest.mark.parametrize('args',[(0,1.1,.1,1),(-.1,.5,.1,1),(0,.8,.03,1),(0,.8,0,1)])
def test_raw_plan_rejects_invalid_voltage_range_before_hardware(args):
    with pytest.raises(ValueError):command_plan(*args)


def test_direct_pi_bracket_does_not_interpolate():
    rows=[]
    # Measured points intentionally place pi strictly between 0.3 and 0.4.
    for point,(voltage,phase) in enumerate([(0,0),(.1,1),(.2,2),(.3,3),(.4,4)]):
        for sample in range(3):
            rows.append({'point':str(point),'cycle':'1','direction':'forward','step_index':str(point),
                         'requested_rp_v':str(voltage),'asg_offset_readback_v':str(voltage),
                         'equatorial_phase_rad':str(math.atan2(math.sin(phase),math.cos(phase))),
                         'pax_s1':'.1','pax_dop':'.9','pax_timestamp':str(point*3+sample)})
    result=analyze(rows)
    crossing=result['pi_crossing_brackets'][0]['crossings'][0]
    assert crossing['rp_command_delta_bracket_v']==[.3,.4]
    assert crossing['phase_excursion_at_bracket_rad']==pytest.approx([3,4])
    assert result['unique_pax_timestamps']==15

    # A phase change while voltage is fixed is drift, not a zero-volt V_pi.
    for row in rows:
        row['direction']='hold'
        row['requested_rp_v']='0.4'
    assert analyze(rows)['pi_crossing_brackets']==[]


def test_explicit_grid_preserves_measured_voltage_levels():
    levels=[0,.2,.33,.335,.34,.8]
    plan=command_plan(0,.8,.02,1,','.join(map(str,levels)))
    assert [entry[3] for entry in plan]==levels+levels[::-1]


@pytest.mark.parametrize('levels',['0,.5,.4','0,nan,.8','0,.5,1.01'])
def test_explicit_grid_rejects_unsafe_or_ambiguous_levels(levels):
    with pytest.raises(ValueError):command_plan(0,.8,.02,1,levels)


def test_scope_consolidation_preserves_all_arrays_and_removes_snapshots(tmp_path):
    (tmp_path/'scope').mkdir()
    (tmp_path/'run.json').write_text('{}')
    samples=np.array([[0.,float('nan')],[.5,1.]])
    np.savez_compressed(tmp_path/'scope/0000.npz',samples=samples,ch1='digital_out1')
    np.savez_compressed(tmp_path/'scope/cleanup-zero.npz',samples=np.zeros((2,4)))
    path=consolidate_scope(tmp_path)
    with np.load(path,allow_pickle=False) as archive:
        np.testing.assert_array_equal(archive['0000/samples'],samples)
        assert archive['0000/ch1']=='digital_out1'
        np.testing.assert_array_equal(archive['cleanup-zero/samples'],np.zeros((2,4)))
    assert not (tmp_path/'scope').exists()
    assert json.loads((tmp_path/'run.json').read_text())['scope_archive']['original_snapshot_count']==2
    assert consolidate_scope(tmp_path)==path


def test_scope_consolidation_never_overwrites_an_existing_archive(tmp_path):
    (tmp_path/'scope').mkdir()
    original=tmp_path/'scope/0000.npz'
    np.savez_compressed(original,samples=[1])
    (tmp_path/'scope.npz').write_bytes(b'previous archive')
    with pytest.raises(FileExistsError):consolidate_scope(tmp_path)
    assert original.exists()
    assert (tmp_path/'scope.npz').read_bytes()==b'previous archive'
