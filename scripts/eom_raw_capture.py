#!/usr/bin/env python3
"""One-off paired raw EOM acquisition. No suite imports, fitting or classification."""
import argparse
import csv
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from uuid import uuid4
import zipfile
import numpy as np

N = 16384
FIELDS = ['capture', 'stage', 'cycle', 'direction', 'waveform', 'command_v',
          'amplitude_v', 'requested_frequency_hz', 'frequency_hz', 'repeat',
          'decimation', 'sample_interval_s', 'duration_s', 'requested_monotonic_s',
          'received_monotonic_s', 'settle_s', 'raw_file', 'sha256', 'accepted']


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
    temporary.replace(path)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sampling(c, frequency=None):
    candidates = [c['dc']['decimation']] if frequency is None else [2**i for i in range(17)]
    for dec in candidates:
        dt = 8e-9 * dec / c['frequency_correction']
        if dec not in {2**i for i in range(17)} or N*dt >= c['capture_timeout_s']:
            continue
        if frequency is None or (frequency*N*dt >= c['dynamic']['min_cycles'] and
                                 dt*frequency <= 1/c['dynamic']['min_samples_per_cycle']):
            return dec, dt
    raise ValueError('Cannot resolve required cycles/samples inside one frozen buffer and timeout')


def envelope(c, offset, amplitude=0., frequency=0.):
    """Fixed affine engineering envelope; nominal HV estimates are labeled as such."""
    limits, chain = c['limits'], c['chain']
    for command in (0., offset-amplitude, offset+amplitude):
        if not limits['command_v'][0] <= command <= limits['command_v'][1]:
            raise ValueError('Command envelope exceeds approved bounds, including zero cleanup')
        for gain, intercept, margin, key in [
            (chain['preamp_gain'], chain['preamp_offset_v'], chain['amplifier_input_margin_v'], 'amplifier_input_v'),
            (chain['electrode_gain_per_command_v'], chain['electrode_offset_v'], chain['electrode_margin_v'], 'electrode_v')]:
            predicted = gain*command + intercept
            if predicted-margin < limits[key][0] or predicted+margin > limits[key][1]:
                raise ValueError(f'Predicted {key} including margin exceeds approved bounds')
    if amplitude and (amplitude > limits['ac_peak_command_max_v'] or
                      not limits['frequency_hz'][0] <= frequency <= limits['frequency_hz'][1]):
        raise ValueError('AC amplitude/frequency combination outside approved envelope')


def plan(c, stage):
    """Precompute the finite plan without contacting the RP; no adaptive decisions."""
    def finite(value):
        if isinstance(value,dict):
            for item in value.values(): finite(item)
        elif isinstance(value,list):
            for item in value: finite(item)
        elif isinstance(value,(int,float)) and not math.isfinite(value): raise ValueError('All numeric settings must be finite')
    finite(c)
    required={'wiring_and_optics','adc_jumpers','chain_model_and_limits','independent_hardware_protection','zero_startup_profile','exclusive_rp_access'}
    if set(c['confirmed']) != required or any(type(v) is not bool for v in c['confirmed'].values()):
        raise ValueError('All six explicit physical confirmation fields are required')
    if c['rp_output'] not in ('out1', 'out2') or c['adc_full_scale_v'] not in ([1., 20.], [1., 1.], [20., 1.], [20., 20.]):
        raise ValueError('Select one output and explicit physical IN1/IN2 ADC ranges')
    for lo, hi in (c['limits'][k] for k in ('command_v', 'amplifier_input_v', 'electrode_v', 'monitor_adc_v', 'frequency_hz')):
        if not lo < hi: raise ValueError('Limits must be strictly ordered')
    if not -1 <= c['limits']['command_v'][0] <= 0 <= c['limits']['command_v'][1] <= 1:
        raise ValueError('RP bounds must lie within +/-1 V and contain zero')
    if not -c['adc_full_scale_v'][1] <= c['limits']['monitor_adc_v'][0] < c['limits']['monitor_adc_v'][1] <= c['adc_full_scale_v'][1]:
        raise ValueError('Monitor stop window exceeds physical ADC range')
    if c['frequency_correction'] <= 0 or c['capture_timeout_s'] <= 0 or min(c['settle_s'], c['ramp_s']) < 0:
        raise ValueError('Invalid clock, capture timeout or settling/ramp time')
    chain = c['chain']
    if (chain['preamp_gain'] == 0 or chain['electrode_gain_per_command_v'] == 0 or
        chain['monitor_scale_v_per_v'] <= 0 or chain['monitor_polarity'] not in (-1, 1) or
        min(chain['amplifier_input_margin_v'], chain['electrode_margin_v'], chain['monitor_disagreement_electrode_v']) <= 0):
        raise ValueError('Record nonzero chain gains and positive engineering margins/disagreement tolerance')
    output = []
    def add(kind, cycle, direction, waveform, offset, amplitude, requested_f, count):
        if amplitude: amplitude = math.floor(amplitude*8192)/8192  # Never round AC peak above the requested ceiling.
        if waveform != 'dc' and amplitude<=0: raise ValueError('AC peak below one DAC step')
        step = 125e6*c['frequency_correction']/2**30
        f = round(requested_f/step)*step if requested_f else 0.
        envelope(c, offset, amplitude, f)
        dec, dt = sampling(c, f or None)
        output.append(dict(stage=kind, cycle=cycle, direction=direction, waveform=waveform,
            command_v=float(offset), amplitude_v=float(amplitude), requested_frequency_hz=float(requested_f),
            frequency_hz=float(f), buffers=count, decimation=dec, sample_interval_s=dt, duration_s=N*dt))
    if stage in ('dc', 'all'):
        dc = c['dc']
        if dc['points'] < 5 or dc['cycles'] not in (2, 3) or dc['buffers_per_point'] < 1 or dc['low_v'] >= dc['high_v']:
            raise ValueError('DC needs ordered endpoints, >=5 points, 2–3 cycles and >=1 buffer per point')
        values = np.linspace(dc['low_v'], dc['high_v'], dc['points'])
        for cycle in range(dc['cycles']):
            for direction, points in [('ascending', values), ('descending', values[::-1])]:
                for value in points: add('dc', cycle, direction, 'dc', value, 0., 0., dc['buffers_per_point'])
    if stage in ('dynamic', 'all'):
        d = c['dynamic']
        if (d['log_points'] < 2 or d['buffers_per_frequency'] < 2 or d['min_cycles'] < 3 or
            d['min_samples_per_cycle'] < 16 or not d['peak_amplitudes_v'] or
            any(a <= 0 for a in d['peak_amplitudes_v']) or not d['passes'] or
            any(p not in ('ascending', 'descending') for p in d['passes']) or
            not 0 < d['frequency_low_hz'] < d['frequency_high_hz']):
            raise ValueError('Dynamic plan requires positive amplitudes, >=2 buffers, resolved cycles and ordered positive frequencies')
        frequencies = sorted(set(np.geomspace(d['frequency_low_hz'], d['frequency_high_hz'], d['log_points']).tolist() + d['extra_frequencies_hz']))
        for amplitude in d['peak_amplitudes_v']:
            for cycle, direction in enumerate(d['passes']):
                for f in (frequencies if direction == 'ascending' else frequencies[::-1]):
                    add('dynamic', cycle, direction, 'sin', d['bias_v'], amplitude, f, d['buffers_per_frequency'])
            for f in d['triangle_frequencies_hz']:
                add('dynamic', 0, 'selected', 'ramp', d['bias_v'], amplitude, f, d['buffers_per_frequency'])
    return output


class Bench:
    """Direct installed Pyrpl API; only one scope acquisition owns both inputs."""
    def __init__(self, c):
        self.c, self.p = c, None

    def connect(self):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from pyrpl import Pyrpl
        from pyrpl.directories import user_config_dir
        import yaml
        import paramiko
        import base64
        profile=Path(self.c['rp_config']).expanduser()
        if not profile.is_file(): profile=Path(user_config_dir)/(self.c['rp_config']+'.yml')
        connection=yaml.safe_load(profile.read_text())['redpitaya']
        # Verify the known board key before giving credentials to Pyrpl's SSH client.
        transport=paramiko.Transport((self.c['rp_hostname'],connection.get('sshport',22)))
        try:
            transport.start_client(timeout=5)
            key=transport.get_remote_server_key()
            fingerprint=base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
            if fingerprint != self.c['ssh_host_key_sha256']: raise RuntimeError('RP SSH host fingerprint mismatch')
        finally: transport.close()
        allowed={'port','sshport','user','password','delay','serverdirname','monitor_server_name','frequency_correction'}
        settings={k:v for k,v in connection.items() if k in allowed}
        # Connection settings only, in RAM: never load/save the user's module settings or archive credentials.
        self.p = Pyrpl.__new__(Pyrpl)  # Retain partial initialization for cleanup on constructor failure.
        Pyrpl.__init__(self.p, hostname=self.c['rp_hostname'], config='', gui=False, **settings)
        self.p._autosave_active = False
        rp = self.p.rp
        if not math.isclose(rp.frequency_correction, self.c['frequency_correction'], rel_tol=1e-12):
            raise RuntimeError('Actual Pyrpl clock correction differs from preflight')
        self.outputs = [getattr(rp, n) for n in ('asg0', 'asg1', 'pid0', 'pid1', 'pid2', 'iq0', 'iq1', 'iq2')]
        for m in self.outputs: m._autosave_active = False; m.output_direct = 'off'
        self.asg = rp.asg0 if self.c['rp_output'] == 'out1' else rp.asg1
        for a in (rp.asg0, rp.asg1):
            a.setup(waveform='dc', offset=0., amplitude=0., trigger_source='immediately')
        self.scope = rp.scope
        self.scope._autosave_active = False
        self.scope.stop()
        self.scope.setup(input1='in1', input2='in2', rolling_mode=False, trace_average=1,
                         trigger_source='immediately', trigger_delay=0.)
        self.scope.average = False
        import pyrpl
        import re
        marker=rp.ssh.ask('cat /tmp/loaded_fpga.inf')
        if 'pyrpl' not in marker.lower(): raise RuntimeError('RP is not marked as running Pyrpl FPGA')
        identity=rp.ssh.ask('cat /sys/class/net/eth0/address')
        mac=re.search(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}',identity)
        return dict(pyrpl_version=pyrpl.__version__,pyrpl_source=inspect.getfile(Pyrpl),
                    frequency_correction=rp.frequency_correction,fpga_marker='pyrpl',
                    ethernet_mac=mac.group() if mac else 'unavailable',ssh_fingerprint=self.c['ssh_host_key_sha256'])

    def readback(self):
        a = self.asg
        return dict(waveform=a.waveform, offset_v=float(a.offset), amplitude_v=float(a.amplitude),
                    frequency_hz=float(a.frequency), output_direct=a.output_direct)

    def configure(self, point):
        c, a = self.c, self.asg
        envelope(c, point['command_v'], point['amplitude_v'], point['frequency_hz'])
        # Keep a DC route active while ramping DC; waveform changes start at routed zero.
        current = self.readback()
        if current['waveform'] != 'dc':
            a.output_direct = 'off'
            a.setup(waveform='dc', offset=0., amplitude=0., trigger_source='immediately')
        start = float(a.offset)
        for value in np.linspace(start, point['command_v'], max(2, int(c['ramp_s']/.01)+1)):
            envelope(c, float(value))
            a.offset = float(value)
            if c['ramp_s']: time.sleep(c['ramp_s']/max(1, int(c['ramp_s']/.01)))
        if point['waveform'] != 'dc': a.output_direct = 'off'
        a.setup(waveform=point['waveform'], offset=point['command_v'], amplitude=point['amplitude_v'],
                frequency=point['frequency_hz'] or 1., trigger_source='immediately')
        self.scope.decimation = point['decimation']
        actual = self.readback()
        envelope(c, actual['offset_v'], actual['amplitude_v'], actual['frequency_hz'] if point['amplitude_v'] else 0.)
        if (actual['waveform'] != point['waveform'] or abs(actual['offset_v']-point['command_v']) > 2/8192 or
            abs(actual['amplitude_v']-point['amplitude_v']) > 2/8192 or
            (point['frequency_hz'] and abs(actual['frequency_hz']-point['frequency_hz']) > 1e-6*point['frequency_hz'])):
            raise RuntimeError('ASG readback disagrees with planned settings')
        a.output_direct = c['rp_output']
        return self.readback()

    def settings(self):
        s = self.scope
        return dict(input1=s.input1, input2=s.input2, decimation=int(s.decimation),
            sample_interval_s=float(s.sampling_time), duration_s=float(s.duration), sample_count=int(s.data_length),
            fpga_average=bool(s.average), trace_average=int(s.trace_average), rolling_mode=bool(s.rolling_mode),
            trigger_source=s.trigger_source)

    def capture(self):
        s = self.scope
        before = self.settings()
        requested = time.monotonic()
        s._start_trace_acquisition()
        deadline = requested + self.c['capture_timeout_s']
        while not s.curve_ready():
            if time.monotonic() > deadline: raise TimeoutError('Scope capture timeout')
            time.sleep(.002)
        # Ready state has ended the single write. Verify the ring stays frozen across BOTH reads.
        pointer = int(s._write_pointer_current)
        trigger = int(s._write_pointer_trigger)
        delay = int(s._trigger_delay_register)
        raw = np.stack([s._rawdata_ch1, s._rawdata_ch2])
        frozen = s.curve_ready() and pointer == s._write_pointer_current and trigger == s._write_pointer_trigger and delay == s._trigger_delay_register
        raw = np.roll(raw, -(trigger+delay+1), axis=1).astype(np.int16)
        public_agrees = np.array_equal(raw.astype(float)/8192, s._get_trace())
        return raw, dict(requested_monotonic_s=requested, received_monotonic_s=time.monotonic(),
            scope=before, scope_after=self.settings(), frozen=bool(frozen), public_trace_agrees=public_agrees,
            write_pointer=pointer, trigger_pointer=trigger, trigger_delay_samples=delay,
            asg_after=self.readback(),trigger_timestamp_cycles=int(s.trigger_timestamp),
            frozen_after_public_read=bool(s.curve_ready() and pointer==s._write_pointer_current))

    def close(self):
        if self.p is None: return dict(status='NOT_CONNECTED')
        errors = []
        # Each operation is attempted independently, even after connection/setup failure.
        for name in ('asg0', 'asg1', 'pid0', 'pid1', 'pid2', 'iq0', 'iq1', 'iq2'):
            try: getattr(self.p.rp, name).output_direct = 'off'
            except Exception as exc: errors.append(f'{name} route off: {exc}')
        final = {}
        for name in ('asg0', 'asg1'):
            try:
                a = getattr(self.p.rp, name)
                a.setup(waveform='dc', offset=0., amplitude=0., trigger_source='immediately')
                final[name] = dict(offset_v=float(a.offset), amplitude_v=float(a.amplitude), output_direct=a.output_direct)
                if a.output_direct != 'off' or a.offset != 0 or a.amplitude != 0: errors.append(name+' cleanup readback failed')
            except Exception as exc: errors.append(f'{name} zero: {exc}')
        try: self.scope.stop()
        except Exception as exc: errors.append(f'scope stop: {exc}')
        try: self.p.rp.end_all()
        except Exception as exc: errors.append(f'connection release: {exc}')
        return dict(status='FAILED' if errors else 'COMPLETED', errors=errors, asg_readbacks=final,
                    meaning='Routes off and DC zero settings; no measured zero-electrode claim')


class MockBench:
    """Deterministic raw-storage fixture, never a physical calibration."""
    def __init__(self, c): self.c, self.point = c, None
    def connect(self): return dict(synthetic=True)
    def configure(self, point):
        self.point = point
        self.actual = dict(waveform=point['waveform'], offset_v=point['command_v'],
            amplitude_v=point['amplitude_v'], frequency_hz=point['frequency_hz'], output_direct=self.c['rp_output'])
        return self.actual.copy()
    def capture(self):
        p, c = self.point, self.c
        t = np.arange(N)*p['sample_interval_s'];phase = 2*np.pi*p['frequency_hz']*t+.31
        wave = 2/np.pi*np.arcsin(np.sin(phase)) if p['waveform'] == 'ramp' else np.sin(phase)
        command = p['command_v'] + p['amplitude_v']*wave
        ch = c['chain']
        monitor = (ch['electrode_gain_per_command_v']*command+ch['electrode_offset_v'])/(ch['monitor_scale_v_per_v']*ch['monitor_polarity'])+ch['monitor_zero_v']
        pd = -.01+.03*np.cos(60*command+.3)+.0001*np.sin(np.arange(N)*1.1)
        raw = np.rint(np.stack([pd,monitor])/np.array(c['adc_full_scale_v'])[:,None]*8192).astype(np.int16)
        settings = dict(input1='in1', input2='in2', decimation=p['decimation'], sample_interval_s=p['sample_interval_s'],
            duration_s=p['duration_s'], sample_count=N, fpga_average=False, trace_average=1, rolling_mode=False, trigger_source='immediately')
        return raw, dict(requested_monotonic_s=time.monotonic(), received_monotonic_s=time.monotonic(),
            scope=settings, scope_after=settings.copy(), frozen=True, public_trace_agrees=True, asg_after=self.actual.copy())
    def close(self): return dict(status='COMPLETED', synthetic=True, output_routes='off', offsets_v=[0.,0.])


def check_capture(raw, meta, point, actual, c):
    s = meta['scope']
    if (raw.shape != (2,N) or raw.dtype.kind != 'i' or np.any(raw < -8192) or np.any(raw > 8191)):
        raise RuntimeError('Malformed signed paired ADC buffer; returned data retained')
    if (s != meta['scope_after'] or not meta['frozen'] or not meta.get('frozen_after_public_read',True) or not meta['public_trace_agrees'] or
        s['input1'] != 'in1' or s['input2'] != 'in2' or s['sample_count'] != N or
        s['fpga_average'] or s['trace_average'] != 1 or s['rolling_mode'] or s['trigger_source'] != 'immediately' or
        s['decimation'] != point['decimation'] or not math.isclose(s['sample_interval_s'],point['sample_interval_s'],rel_tol=1e-8) or
        not math.isclose(s['duration_s'],N*s['sample_interval_s'],rel_tol=1e-8) or meta['asg_after'] != actual):
        raise RuntimeError('Scope/ASG changed, capture not frozen or unsupported sample configuration; raw retained')
    if np.any(np.abs(raw.astype(np.int32)) >= 8190): raise RuntimeError('ADC clipping; raw retained')
    if meta['received_monotonic_s'] < meta['requested_monotonic_s']: raise RuntimeError('Reversed host bounds')
    volts = raw.astype(float)/8192*np.array(c['adc_full_scale_v'])[:,None]
    monitor = volts[1];lo, hi = c['limits']['monitor_adc_v']
    if monitor.min() < lo or monitor.max() > hi: raise RuntimeError('Measured IN2 stop window exceeded; raw retained')
    ch = c['chain']
    electrode = (monitor-ch['monitor_zero_v'])*ch['monitor_scale_v_per_v']*ch['monitor_polarity']
    lo, hi = c['limits']['electrode_v'];margin = ch['electrode_margin_v']
    if electrode.min()-margin < lo or electrode.max()+margin > hi:
        raise RuntimeError('Monitor-converted electrode engineering envelope exceeded; conversion calibration is declared in config')
    expected = ch['electrode_gain_per_command_v']*actual['offset_v']+ch['electrode_offset_v']
    span = abs(ch['electrode_gain_per_command_v']*actual['amplitude_v'])+ch['monitor_disagreement_electrode_v']
    if np.any(abs(electrode-expected) > span): raise RuntimeError('Unexpected monitor/command voltage disagreement; raw retained')
    return dict(pd_min_v=float(volts[0].min()),pd_max_v=float(volts[0].max()),
                monitor_min_v=float(monitor.min()),monitor_max_v=float(monitor.max()),monitor_mean_v=float(monitor.mean()))


def quicklook(directory,c):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    with (directory/'index.csv').open() as source: rows=list(csv.DictReader(source))
    fig,axes=plt.subplots(1,2,figsize=(10,4),layout='constrained')
    groups={};malformed=0
    for r in rows:
        if r['stage']!='dc': continue
        raw=np.load(directory/r['raw_file'],allow_pickle=False)
        if raw.shape!=(2,N): malformed+=1;continue
        volts=raw.astype(float)/8192*np.array(c['adc_full_scale_v'])[:,None]
        key=(r['cycle'],r['direction'],r['accepted'])
        groups.setdefault(key,[]).append((float(r['command_v']),float(volts[1].mean()),float(volts[0].mean())))
    for (cycle,direction,accepted),values in groups.items():
        for i,ax in enumerate(axes):
            ax.plot([v[i] for v in values],[v[2] for v in values],'.',ms=3,label=f'cycle {int(cycle)+1} {direction}'+(' FAILED check' if accepted!='True' else ''))
    for ax,label in zip(axes,('RP command (V)','Measured IN2 monitor (ADC V)')):
        ax.set(xlabel=label,ylabel='PD buffer mean (V)');ax.grid(alpha=.15)
    if groups: axes[0].legend(fontsize=6)
    fig.suptitle(f'DC sanity plot: explicit complete-buffer means; no fitting. Malformed buffers not plotted: {malformed}')
    fig.savefig(directory/'quicklook.png',dpi=150);plt.close(fig)


def verify_and_zip(directory):
    """Verify indexed bytes, signed samples and ZIP CRC/contents, including failed buffers."""
    with (directory/'index.csv').open() as source: rows = list(csv.DictReader(source))
    files = {r['raw_file'] for r in rows}
    if files != {p.relative_to(directory).as_posix() for p in (directory/'captures').glob('*.npy')}:
        raise RuntimeError('Index and complete capture filenames disagree; no files removed')
    invalid = []
    for row in rows:
        path = directory/row['raw_file']
        if sha(path) != row['sha256']: raise RuntimeError('Saved capture hash mismatch: '+str(path))
        raw = np.load(path,allow_pickle=False)
        if raw.shape != (2,N) or raw.dtype.kind != 'i' or np.any(raw < -8192) or np.any(raw > 8191):
            invalid.append(row['capture'])
            if row['accepted'] == 'True': raise RuntimeError('Previously accepted buffer fails shape/signed-code integrity')
    target = directory.with_suffix('.zip');temporary = target.with_suffix('.zip.part')
    with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(directory.rglob('*')):
            if path.is_file(): archive.write(path,Path(directory.name)/path.relative_to(directory))
    with zipfile.ZipFile(temporary) as archive:
        if archive.testzip() is not None: raise RuntimeError('ZIP CRC check failed')
        expected = {str(Path(directory.name)/p.relative_to(directory)) for p in directory.rglob('*') if p.is_file()}
        if set(archive.namelist()) != expected: raise RuntimeError('ZIP file list mismatch')
        for row in rows:
            name = directory.name+'/'+row['raw_file']
            if hashlib.sha256(archive.read(name)).hexdigest() != row['sha256']: raise RuntimeError('ZIP capture hash mismatch')
    temporary.replace(target)
    return dict(zip=str(target.resolve()),captures=len(rows),accepted=sum(r['accepted']=='True' for r in rows),
                malformed_returned_capture_ids=invalid,zip_verified=True)


def acquire(c, stage, *, mock=False, append=None, interrupt_after=None):
    points = plan(c,stage)
    if not mock:
        if not all(c['confirmed'].values()): raise ValueError('Physical confirmations required: '+', '.join(k for k,v in c['confirmed'].items() if not v))
        if stage in ('dynamic','all') and (not c['dynamic']['bias_confirmed'] or not c['dynamic']['bias_evidence'].strip()):
            raise ValueError('Confirm the dynamic bias after DC, or record an explicit operator override')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    directory = Path(append) if append else Path(c['output_directory'])/f'eom_raw_{stamp}_{uuid4().hex[:6]}'
    previous = {}
    if append:
        previous = json.loads((directory/'status.json').read_text())
        if sha(directory/'eom_raw_capture.py') != sha(Path(__file__)): raise ValueError('Acquisition source changed; use a new run folder')
        old = json.loads((directory/'config.json').read_text())
        if stage != 'dynamic' or previous.get('mode') != ('mock' if mock else 'hardware') or previous.get('state') != 'COMPLETED' or previous.get('cleanup',{}).get('status') != 'COMPLETED' or previous.get('completed_stages') != ['dc']:
            raise ValueError('Append only a dynamic stage to a completed, cleaned-up DC run in the same mode')
        if {k:v for k,v in old.items() if k != 'dynamic'} != {k:v for k,v in c.items() if k != 'dynamic'}:
            raise ValueError('Hardware/DC configuration changed; use a new run directory')
        verify_and_zip(directory)
    else:
        (directory/'captures').mkdir(parents=True,exist_ok=False)
        shutil.copy2(__file__,directory/'eom_raw_capture.py')
        try: revision = subprocess.check_output(['git','rev-parse','HEAD'],text=True,stderr=subprocess.DEVNULL).strip()
        except (subprocess.CalledProcessError,FileNotFoundError): revision = 'unavailable'
        write_json(directory/'metadata.json',dict(created_utc=datetime.now(timezone.utc).isoformat(),hardware=c['hardware'],
            host=c['rp_hostname'],pyrpl_profile_name=c['rp_config'],git_revision=revision,script_sha256=sha(Path(__file__)),
            python=sys.version,numpy=np.__version__,mode='mock' if mock else 'hardware',
            raw='shape (2,16384), signed 14-bit ADC codes stored as int16; row0=IN1 PD, row1=IN2 monitor; volts=codes/8192*declared channel full scale',
            timing='Within each buffer only: index * measured sample_interval_s, relative to immediate trigger. Host monotonic bounds include acquisition/transport. No continuous clock across buffers. ASG is not an independently sampled third channel; command phase relative to ADC is unknown.',
            voltage_conversion=c['chain'],adc_full_scale_v=c['adc_full_scale_v'],
            configuration_history='Full settings and stage plans recorded in stage_started events; config.json is latest complete user configuration. No private Pyrpl credential file is copied.'))
    write_json(directory/'config.json',c)
    state=dict(state='RUNNING',mode='mock' if mock else 'hardware',stage=stage,
               completed_stages=previous.get('completed_stages',[]),cleanup={'status':'PENDING'})
    write_json(directory/'status.json',state)
    bench = MockBench(c) if mock else Bench(c)
    with (directory/'events.jsonl').open('a') as events,(directory/'index.csv').open('a',newline='') as index:
        def emit(kind,**values):
            events.write(json.dumps(dict(kind=kind,utc=datetime.now(timezone.utc).isoformat(),monotonic_s=time.monotonic(),**values),allow_nan=False)+'\n');events.flush()
        writer=csv.DictWriter(index,fieldnames=FIELDS)
        if not index.tell(): writer.writeheader();index.flush()
        capture=len(list((directory/'captures').glob('*.npy')))
        emit('stage_started',stage=stage,config=c,plan=points)
        try:
            connection=bench.connect()
            metadata=json.loads((directory/'metadata.json').read_text());metadata['connection']=connection
            write_json(directory/'metadata.json',metadata)
            emit('connected',details=connection)
            if not mock:
                # Archive exactly the installed implementation of raw decoding and waveform generation.
                import pyrpl.hardware_modules.scope as scope_source
                import pyrpl.hardware_modules.asg as asg_source
                for module in (scope_source,asg_source):
                    source=Path(inspect.getfile(module));shutil.copy2(source,directory/('pyrpl_'+source.name))
                    emit('installed_source',module=module.__name__,sha256=sha(source))
            for number, point in enumerate(points):
                emit('command_requested',point=number,settings=point)
                actual=bench.configure(point)
                emit('command_readback',point=number,settings=actual)
                start=time.monotonic()
                if not mock: time.sleep(c['settle_s'])
                emit('settled',point=number,requested_s=c['settle_s'],actual_host_s=time.monotonic()-start)
                for repeat in range(point['buffers']):
                    if interrupt_after is not None and capture >= interrupt_after: raise KeyboardInterrupt('mock interruption')
                    emit('capture_requested',capture=capture,point=number,repeat=repeat)
                    raw,meta=bench.capture()
                    if raw is None: raise RuntimeError('Missing ADC buffer; no replacement samples created')
                    raw=np.asarray(raw)
                    filename=f'captures/{capture:05d}.npy';path=directory/filename
                    with path.with_suffix('.npy.part').open('wb') as dest:
                        np.save(dest,raw,allow_pickle=False);dest.flush();os.fsync(dest.fileno())
                    path.with_suffix('.npy.part').replace(path)
                    row={k:point[k] for k in FIELDS if k in point}
                    row.update(capture=capture,repeat=repeat,raw_file=filename,sha256=sha(path),accepted=False,
                        requested_monotonic_s=meta['requested_monotonic_s'],received_monotonic_s=meta['received_monotonic_s'],settle_s=c['settle_s'],
                        frequency_hz=actual['frequency_hz'] if point['waveform']!='dc' else 0.,
                        decimation=meta['scope']['decimation'],sample_interval_s=meta['scope']['sample_interval_s'],duration_s=meta['scope']['duration_s'])
                    emit('capture_returned',capture=capture,raw_file=filename,sha256=row['sha256'],metadata=meta,command=point,asg_readback=actual)
                    try:
                        observed=check_capture(raw,meta,point,actual,c)
                        row['accepted']=True
                        emit('capture_checked',capture=capture,**observed)
                    finally:
                        writer.writerow(row);index.flush()
                    capture+=1
                print(f"{number+1}/{len(points)} {point['stage']} {point['direction']} command={point['command_v']:.6g} V f={point['frequency_hz']:.6g} Hz",flush=True)
            state['state']='COMPLETED'
            state['completed_stages']+=['dc','dynamic'] if stage=='all' else [stage]
        except BaseException as exc:
            state.update(state='INTERRUPTED' if isinstance(exc,KeyboardInterrupt) else 'FAILED',error=f'{type(exc).__name__}: {exc}')
            emit('error',error=state['error'])
        finally:
            try: state['cleanup']=bench.close()
            except BaseException as exc: state['cleanup']=dict(status='FAILED',error=f'{type(exc).__name__}: {exc}')
            emit('cleanup',evidence=state['cleanup'])
            state['finished_utc']=datetime.now(timezone.utc).isoformat()
            write_json(directory/'status.json',state)
    try: quicklook(directory,c)
    except Exception as exc: print('Optional quicklook unavailable:',exc)
    try: archive=verify_and_zip(directory)
    except Exception as exc:
        archive=dict(zip_verified=False,error=f'{type(exc).__name__}: {exc}')
    print(json.dumps(dict(directory=str(directory.resolve()),**state,**archive),indent=2))
    if archive.get('zip'): print('\nZIP: '+archive['zip'],flush=True)
    return directory,state,archive


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=Path(__file__).with_suffix('.json'))
    parser.add_argument('--stage',choices=['dc','dynamic','all'],default='dc')
    parser.add_argument('--preflight',action='store_true',help='Validate/print the full plan without loading Pyrpl or enabling outputs')
    parser.add_argument('--mock',action='store_true',help='Synthetic storage/cleanup fixture; never hardware evidence')
    parser.add_argument('--append',type=Path,help='Append dynamic captures to one completed DC folder')
    parser.add_argument('--export',type=Path,help='Verify/rebuild ZIP from an existing finished folder; no hardware')
    parser.add_argument('--interrupt-after',type=int,help='Mock-only interruption test after N returned buffers')
    args=parser.parse_args()
    if args.export:
        if json.loads((args.export/'status.json').read_text())['state']=='RUNNING': parser.error('Refuse to archive a possibly active run')
        print(json.dumps(verify_and_zip(args.export),indent=2));return
    c=json.loads(args.config.read_text(),parse_constant=lambda value:(_ for _ in ()).throw(ValueError('Non-finite JSON number: '+value)))
    points=plan(c,args.stage)
    if args.preflight:
        print(json.dumps(dict(stage=args.stage,points=len(points),buffers=sum(p['buffers'] for p in points),
            confirmations=c['confirmed'],dynamic_bias_confirmed=c['dynamic']['bias_confirmed'],plan=points),indent=2));return
    if args.interrupt_after is not None and not args.mock: parser.error('--interrupt-after is mock-only')
    directory,state,archive=acquire(c,args.stage,mock=args.mock,append=args.append,interrupt_after=args.interrupt_after)
    if state['state']!='COMPLETED' or state['cleanup']['status']!='COMPLETED' or not archive.get('zip_verified'): sys.exit(1)


if __name__ == '__main__': main()
