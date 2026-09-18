"""Raw, settled phi1 calibration. No fitting or assumed RP-to-terminal conversion."""
from __future__ import annotations
import argparse
import csv
import hashlib
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time
import traceback

import numpy as np
from .config import PolarizationLockConfig
from .hardware.pax_interface import PAXController
from .hardware.rp_interface import RPController
from .settings import write_json


def utc():
    return datetime.now(timezone.utc).isoformat()


def consolidate_scope(directory):
    """Keep one lossless NPZ per test; verify every array before removing snapshots."""
    directory=Path(directory)
    files=sorted((directory/'scope').glob('*.npz'))
    if not files:
        return directory/'scope.npz'
    destination=directory/'scope.npz'
    if destination.exists():
        raise FileExistsError(f'Refusing to overwrite {destination}')
    arrays={}
    fingerprints={}
    for path in files:
        with np.load(path,allow_pickle=False) as snapshot:
            for name in snapshot.files:
                key=f'{path.stem}/{name}'
                value=snapshot[name]
                arrays[key]=value
                fingerprints[key]=(value.dtype.str,value.shape,hashlib.sha256(value.tobytes()).hexdigest())
    temporary=directory/'scope.tmp.npz'
    np.savez_compressed(temporary,**arrays)
    with np.load(temporary,allow_pickle=False) as archive:
        if set(archive.files)!=set(fingerprints):
            raise RuntimeError('Scope archive member verification failed')
        for key,expected in fingerprints.items():
            value=archive[key]
            actual=(value.dtype.str,value.shape,hashlib.sha256(value.tobytes()).hexdigest())
            if actual!=expected:
                raise RuntimeError(f'Scope archive verification failed: {key}')
    temporary.replace(destination)
    manifest=json.loads((directory/'run.json').read_text())
    manifest['scope_archive']={'file':'scope.npz','original_snapshot_count':len(files),
                               'key_format':'<original filename without .npz>/<original array name>',
                               'verification':'All array dtypes, shapes and SHA-256 byte digests matched before deleting original snapshots.'}
    write_json(directory/'run.json',manifest)
    for path in files:path.unlink()
    if not any((directory/'scope').iterdir()):(directory/'scope').rmdir()
    return destination


def command_plan(start, stop, step, repeats, levels=None):
    if not 0 <= start < stop <= 1 or step <= 0 or repeats < 1:
        raise ValueError('Require 0 <= start < stop <= 1 V, positive step and repeats')
    if levels is None:
        count = int(round((stop-start)/step))
        if count < 2 or not math.isclose(start+count*step,stop,abs_tol=1e-9):
            raise ValueError('Choose a step that divides the range into at least two intervals')
        values = np.linspace(start,stop,count+1).tolist()
    else:
        values = [float(v) for v in levels.split(',')]
        if len(values)<3 or any(not math.isfinite(v) or not 0<=v<=1 for v in values) or any(b<=a for a,b in zip(values,values[1:])):
            raise ValueError('Explicit levels must be >=3 strictly increasing voltages within 0–1 V')
    return [(cycle,direction,index,value) for cycle in range(1,repeats+1)
            for direction,sequence in [('forward',values),('reverse',values[::-1])]
            for index,value in enumerate(sequence)]


def acquire(args):
    cfg = PolarizationLockConfig()
    cfg.bench_pax_location = args.pax_location
    now = datetime.now()
    directory = Path(cfg.results_directory)/now.strftime('%Y-%m-%d')/f'{now:%H%M%S}_raw-phi1_{args.label}'
    directory.mkdir(parents=True,exist_ok=False)
    (directory/'scope').mkdir()
    manifest = {'schema_version':1,'purpose':'Direct phi1 phase response and voltage-chain evidence; no fitting',
                'status':'connecting','started_at':utc(),'parameters':vars(args),'configuration':asdict(cfg),
                'measurement_definitions':{
                    'requested_rp_v':'Requested ASG command, not measured electrical voltage',
                    'asg_offset_readback_v':'FPGA ASG setting readback, not electrical voltage',
                    'digital_out1_scope_median_v':'Median of saved FPGA scope trace routed from out1; not physical output voltage',
                    'scope_analog_in1_median_v':'Median of saved scope trace explicitly routed from physical IN1; connection recorded separately',
                    'scope_analog_in2_median_v':'Median of saved scope trace explicitly routed from physical IN2; connection recorded separately',
                    'stokes':'Orientation Stokes calculated from raw PAX theta/eta; unit normalized separately from DOP',
                    'terminal_voltage':'Not measured unless an independently verified monitor is provided. No gain conversion applied.'}}
    write_json(directory/'run.json',manifest)
    print(f'RAW RUN DIRECTORY: {directory}',flush=True)
    rp,pax = RPController(cfg),PAXController(cfg)
    started = time.monotonic()
    rows=0
    scope_settings=None
    raw_file=(directory/'pax_raw.jsonl').open('w')
    events=(directory/'commands.jsonl').open('w')
    data_file=(directory/'data.csv').open('w',newline='')
    writer=None
    try:
        # Start/read PAX before changing the RP configuration.
        pax.connect()
        first=pax.read_polarization()
        print('PAX CONNECTED',json.dumps(asdict(first)),flush=True)
        rp.connect()
        scope=rp.p.rp.scope
        scope_settings={k:getattr(scope,k) for k in ('input1','input2','duration','trigger_source','ch1_active','ch2_active')}
        scope.input1='out1'
        scope.input2='in2'
        scope.duration=.01
        scope.trigger_source='immediately'
        scope.ch1_active=True
        scope.ch2_active=True
        manifest['scope_inputs']={'ch1':'out1 (internal digital DAC signal)','ch2':'in2 (physical ADC; see connection note)'}
        manifest['scope_duration_readback_s']=float(scope.duration)
        manifest['status']='acquiring'
        write_json(directory/'run.json',manifest)
        if args.probe:
            plan=[(0,'probe',0,0.0)]
        elif args.hold_voltage is not None:
            plan=[(1,'hold',i,args.hold_voltage) for i in range(args.hold_points)]
        else:
            plan=command_plan(args.start,args.stop,args.step,args.repeats,args.levels)
        previous_voltage=None
        command_at=None
        for point,(cycle,direction,index,value) in enumerate(plan):
            command_issued=(value!=previous_voltage)
            if command_issued:
                rp.set_output_voltage(value,0.0)
                command_at=time.monotonic()
                previous_voltage=value
            event={'utc':utc(),'elapsed_s':command_at-started,'point':point,'cycle':cycle,'direction':direction,
                   'step_index':index,'requested_rp_v':value,'command_issued':command_issued,
                   'asg_offset_readback_v':float(rp.asg1.offset),'asg_amplitude_readback_v':float(rp.asg1.amplitude),
                   'asg_frequency_readback_hz':float(rp.asg1.frequency),'asg_waveform':str(rp.asg1.waveform),
                   'asg_output_route':str(rp.asg1.output_direct),'out2_offset_readback_v':float(rp.asg2.offset)}
            events.write(json.dumps(event)+'\n');events.flush()
            if not math.isclose(event['asg_offset_readback_v'],value,abs_tol=.001):
                raise RuntimeError('ASG offset readback disagrees with command')
            time.sleep(args.settle)
            scope_start=time.monotonic()
            trace=np.asarray(scope.single(timeout=3),dtype=float)
            scope_end=time.monotonic()
            scope.input2='in1'
            in1_scope_start=time.monotonic()
            trace_in1=np.asarray(scope.single(timeout=3),dtype=float)[1]
            in1_scope_end=time.monotonic()
            scope.input2='in2'
            np.savez_compressed(directory/'scope'/f'{point:04d}.npz',samples=trace,times_s=np.asarray(scope.times),
                                host_start_s=scope_start-started,host_end_s=scope_end-started,
                                ch1='digital_out1',ch2='analog_in2',analog_in1_samples=trace_in1,
                                in1_host_start_s=in1_scope_start-started,in1_host_end_s=in1_scope_end-started)
            digital_out1=float(np.median(trace[0]))
            if not math.isclose(digital_out1,value,abs_tol=.002):
                raise RuntimeError('Recorded FPGA scope out1 trace disagrees with the requested DC level')
            for sample in range(args.samples):
                before=time.monotonic()
                electrical={'digital_out1_scope_median_v':digital_out1,
                            'scope_analog_in1_median_v':float(np.median(trace_in1)),
                            'scope_analog_in2_median_v':float(np.median(trace[1]))}
                reading=pax.read_polarization()
                after=time.monotonic()
                # Preserve the daemon record in addition to the existing angle conversion.
                payload=pax.last_raw_record
                raw_file.write(json.dumps({'point':point,'sample':sample,'host_before_s':before-started,
                                           'host_after_s':after-started,'record':payload},default=float)+'\n')
                raw_file.flush()
                row={**event,'sample':sample,'host_before_s':before-started,'host_after_s':after-started,
                     'since_command_s':before-command_at,**electrical,**{f'pax_{k}':v for k,v in asdict(reading).items()}}
                # atan2 is the direct equatorial coordinate, not a fitted phase.
                row['equatorial_phase_rad']=math.atan2(reading.s3,-reading.s2)
                if writer is None:
                    writer=csv.DictWriter(data_file,fieldnames=list(row));writer.writeheader()
                writer.writerow(row);data_file.flush();rows+=1
            print(f'point {point+1}/{len(plan)} cycle={cycle} {direction} requested={value:.5f} '
                  f'offset_readback={event["asg_offset_readback_v"]:.5f} digital_scope={digital_out1:.5f} '
                  f'in1_scope={electrical["scope_analog_in1_median_v"]:.5f} in2_scope={electrical["scope_analog_in2_median_v"]:.5f} '
                  f'S1={reading.s1:+.4f} DOP={reading.dop:.4f}',flush=True)
        manifest['status']='completed'
    except (KeyboardInterrupt,EOFError):
        manifest['status']='interrupted'
        print('Stopped; collected records retained.',flush=True)
    except Exception as exc:
        manifest.update(status='failed',error=f'{type(exc).__name__}: {exc}')
        traceback.print_exc()
    finally:
        for handle in (data_file,raw_file,events):handle.close()
        cleanup=[]
        try:
            if rp.p is not None:
                rp.set_output_zero()
                rp.p.rp.scope.input1='out1'
                rp.p.rp.scope.input2='out2'
                cleanup_trace=np.asarray(rp.p.rp.scope.single(timeout=3),dtype=float)
                np.savez_compressed(directory/'scope/cleanup-zero.npz',samples=cleanup_trace,ch1='digital_out1',ch2='digital_out2')
                manifest['cleanup_digital_output_medians_v']=np.median(cleanup_trace,axis=1).tolist()
                if scope_settings:
                    for k,v in scope_settings.items():setattr(rp.p.rp.scope,k,v)
            rp.disconnect()
        except Exception as exc:cleanup.append(f'RP: {exc}')
        try:pax.disconnect()
        except Exception as exc:cleanup.append(f'PAX: {exc}')
        if cleanup:manifest['cleanup_errors']=cleanup
        manifest.update(finished_at=utc(),rows=rows)
        write_json(directory/'run.json',manifest)
        print('FINAL',json.dumps({'directory':str(directory),'status':manifest['status'],'rows':rows,'cleanup_errors':cleanup}),flush=True)
    try:
        consolidate_scope(directory)
        manifest=json.loads((directory/'run.json').read_text())
    except Exception as exc:
        manifest['scope_archive_error']=f'{type(exc).__name__}: {exc}'
        traceback.print_exc()
    try:
        from .reports.raw_calibration import create_report
        create_report(directory)
        manifest['report_file']='report.pdf'
    except Exception as exc:
        manifest['report_error']=f'{type(exc).__name__}: {exc}'
        traceback.print_exc()
    write_json(directory/'run.json',manifest)
    return 0 if manifest['status']=='completed' and not cleanup else 1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe',action='store_true',help='Zero outputs and record a single point; no sweep')
    parser.add_argument('--start',type=float,default=0.0)
    parser.add_argument('--stop',type=float,default=.8)
    parser.add_argument('--step',type=float,default=.02)
    parser.add_argument('--repeats',type=int,default=3)
    parser.add_argument('--levels',help='Explicit ascending RP voltages, comma separated; overrides start/stop/step')
    parser.add_argument('--settle',type=float,default=1.0)
    parser.add_argument('--samples',type=int,default=5)
    parser.add_argument('--hold-voltage',type=float,help='Hold one RP voltage without rewriting the command between blocks')
    parser.add_argument('--hold-points',type=int,default=20)
    parser.add_argument('--label',default='forward-reverse')
    parser.add_argument('--pax-location',default='Physical position awaiting confirmation; no assumed port')
    parser.add_argument('--input-connections',default='IN1/IN2 physical mapping awaiting confirmation')
    args=parser.parse_args()
    if args.hold_voltage is not None and not 0<=args.hold_voltage<=1:
        parser.error('Hold voltage must be within 0–1 V')
    if args.hold_points<1 or args.samples<1 or not math.isfinite(args.settle) or args.settle<0 or '/' in args.label:
        parser.error('Require samples >=1, settle >=0, and a label without slashes')
    command_plan(args.start,args.stop,args.step,args.repeats,args.levels)
    raise SystemExit(acquire(args))

if __name__=='__main__':main()
