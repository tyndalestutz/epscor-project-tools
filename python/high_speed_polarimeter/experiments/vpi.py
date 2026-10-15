"""Bounded forward/reverse PAX sweep, retaining every returned record."""
import csv
import json
import math
from datetime import datetime, timezone

from ..acquisition.eom import json_evidence
from ..io import write_json


def acquire(session, recipe, directory):
    config = recipe.acquisition_config()
    output = getattr(config, config.selected_eom + '_output')
    metadata = dict(schema_version=1, experiment=recipe.experiment, hardware=json_evidence(session.describe()),
                    timing_origin=session.origin(), expected_points=2*config.points*config.repeats,
                    samples_per_point=config.pax_samples_per_point,
                    definitions='s1/s2/s3 are PAX angle-derived direction, not DoP-scaled. No PD acquired. Commands/readbacks are not measured voltages.',
                    timing='Host elapsed time; fresh advancing PAX reads after ramp and settling. No shared hardware trigger.')
    write_json(directory/'acquisition.json', metadata)
    fields = ['sample', 'cycle', 'direction', 'point', 'sample_in_point', 'utc', 'elapsed_s',
              'requested_s', 'command_v', 'out1_command_v', 'out2_command_v', 'pax_timestamp',
              's1', 's2', 's3', 'dop', 'pax_ptotal', 'theta_deg', 'eta_deg', 'synthetic', 'pax_raw_json']
    with (directory/'events.jsonl').open('w') as events, (directory/'data.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        def emit(kind, **values):
            events.write(json.dumps(json_evidence(dict(kind=kind, elapsed_s=session.clock(), **values)), allow_nan=False)+'\n')
            events.flush()
        point = sample = 0
        def record(direction, cycle, point, index, voltage, values):
            nonlocal sample
            requested = session.clock()
            reading = session.read_pax()
            received = session.clock()
            emit('pax_sample', point=point, cycle=cycle, direction=direction, sample=index,
                 requested_s=requested, received_s=received, **reading)
            raw, value = reading['raw_record'], reading['interface_reading']
            if not raw:
                raise RuntimeError('PAX returned no raw record')
            writer.writerow(dict(sample=sample, cycle=cycle, direction=direction, point=point,
                sample_in_point=index, utc=datetime.now(timezone.utc).isoformat(), elapsed_s=received,
                requested_s=requested, command_v=voltage, out1_command_v=values[0], out2_command_v=values[1],
                pax_timestamp=value.get('timestamp', math.nan),
                **{k:value.get(k, math.nan) for k in ('s1','s2','s3','dop')},
                pax_ptotal=value.get('ptotal', math.nan), theta_deg=math.degrees(value.get('theta', math.nan)),
                eta_deg=math.degrees(value.get('eta', math.nan)), synthetic=reading.get('synthetic', False),
                pax_raw_json=json.dumps(json_evidence(raw), allow_nan=False)))
            stream.flush()
            sample += 1
        try:
            emit('command_requested', point=-1, direction='undriven', rp_commands_v=[0.,0.])
            ack = session.command([0.,0.])
            emit('command_completed', point=-1, acknowledgement=ack)
            if ack.get('acknowledged') is not True:
                raise RuntimeError('Undriven zero command not acknowledged')
            emit('stage_pause', stage='before_undriven', duration_s=config.stage_settle_s)
            session.sleep(config.stage_settle_s)
            started = session.clock()
            emit('baseline_started', requested_duration_s=config.undriven_baseline_s)
            index = 0
            while session.clock()-started < config.undriven_baseline_s:
                record('undriven', -1, -1, index, 0., [0.,0.])
                index += 1
            emit('baseline_completed', samples=index, actual_wall_span_s=session.clock()-started)
            print(f'Undriven baseline complete: {index} fresh records.', flush=True)
            emit('stage_pause', stage='before_forward', duration_s=config.stage_settle_s)
            session.sleep(config.stage_settle_s)
            for cycle in range(config.repeats):
                forward = [i*config.stop_command_v/(config.points-1) for i in range(config.points)]
                for direction, commands in [('forward', forward), ('backward', list(reversed(forward)))]:
                    if direction == 'backward':
                        emit('stage_pause', stage='turnaround', duration_s=config.stage_settle_s)
                        session.sleep(config.stage_settle_s)
                    for voltage in commands:
                        values = [voltage, 0.] if output == 'out1' else [0., voltage]
                        emit('command_requested', point=point, cycle=cycle, direction=direction, rp_commands_v=values)
                        ack = session.command(values)
                        emit('command_completed', point=point, acknowledgement=ack)
                        if ack.get('acknowledged') is not True:
                            raise RuntimeError('RP command not acknowledged')
                        session.sleep(config.settle_s)
                        for index in range(config.pax_samples_per_point):
                            record(direction, cycle, point, index, voltage, values)
                        emit('point_completed', point=point)
                        point += 1
                        print(f'{direction} {voltage:.3f} RP command V ({point}/{metadata["expected_points"]})', flush=True)
            emit('stage_pause', stage='returned_zero', duration_s=config.stage_settle_s)
            session.sleep(config.stage_settle_s)
            emit('acquisition_completed', completed_points=point, samples=sample)
        except BaseException:
            if hasattr(session, 'pax_error_evidence'):
                emit('pax_error_evidence', point=point, raw_record=session.pax_error_evidence())
            emit('acquisition_interrupted', completed_points=point, samples=sample)
            raise
    return dict(completed_points=point, samples=sample)
