"""Offline, reproducible comparison; run with PYTHONPATH=python from repo root."""
import csv
import hashlib
import textwrap
import json
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages
from polarization_locking.reports.run_report import ReportPages, text_page, plt, NAVY, TEAL
from polarization_locking.routines.pax_vibration import trace_statistics, summarize_pd, summarize_pax
from polarization_locking.control import sphere_angles_from_stokes

OUT = Path(__file__).resolve().parent
SOURCES = ('2026-09-24/203850_pax-vibration_pax-vibration', '2026-09-25/204124_pax-vibration_rubber-tip-base')
SOURCE_ROOT = OUT.parent.parent
MOUNT_COMMENT_SOURCE = SOURCE_ROOT / '2026-09-25/203321_pax-vibration_rubber-tip-base/run.json'
MOUNT_COMMENT = json.loads(MOUNT_COMMENT_SOURCE.read_text())['run_comment']
LABELS = ('Before: standard post mount', 'After: rubber-cap support')
COLORS = (NAVY, TEAL)
BAND = (5, 1000)

def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def read_run(name):
    path = SOURCE_ROOT / name
    setup = json.loads((path/'vibration-setup.json').read_text())
    saved = json.loads((path/'vibration.json').read_text())
    state = json.loads((path/'run.json').read_text())
    assert state['status'] == 'completed'
    meta = {str(m['capture']): m for m in setup['captures']}
    raw = {k: [] for k in meta}
    pax = []
    with (path/'data.csv').open(newline='') as f:
        for row in csv.DictReader(f):
            if row['capture'] == '':
                r = {k: float(v) for k, v in row.items() if v != ''}
                angles = sphere_angles_from_stokes(r['s1'], r['s2'], r['s3'])
                r.update(u_rad=angles.u, v_rad=angles.v)
                pax.append(r)
            else:
                raw[row['capture']].append(float(row['voltage_v']))
    rows, spectra = [], {}
    for key, values in raw.items():
        m = meta[key]
        assert len(values) == m['sample_count']
        x = np.array(values)
        stats, _, freq, psd = trace_statistics(x, m['sampling_time_s'], setup['dark_voltage_v'], BAND)
        condition = m['condition']
        spectra.setdefault(condition, []).append(psd)
        if condition != 'dark':
            rows.append(dict(**stats, capture=m['capture'], condition=condition,
                             started_s=m['started_s'], finished_s=m['finished_s']))
    spectra = {k: np.mean(v, axis=0) for k,v in spectra.items()}
    summaries = {c: summarize_pd([r for r in rows if r['condition']==c], setup['dark_voltage_v'])
                 for c in ('pax_on', 'pax_off')}
    for c, summary in summaries.items():
        for k,v in summary.items():
            if isinstance(v, (int, float)):
                np.testing.assert_allclose(v, saved['pd'][c][k], rtol=1e-12, atol=0)
    pax_summary = summarize_pax(pax, 1e-6)
    for k in ('mean_power_w','normalized_power_variance','u_variance_rad2','v_variance_rad2','mean_dop'):
        np.testing.assert_allclose(pax_summary[k], saved['pax'][k], rtol=1e-12, atol=0)
    s = np.array([[r[k] for k in ('s1','s2','s3')] for r in pax])
    t = np.array([r['elapsed_s'] for r in pax]); t -= t[0]
    unit = s / np.linalg.norm(s, axis=1)[:,None]
    direction = unit.mean(axis=0); direction /= np.linalg.norm(direction)
    angle = np.rad2deg(np.arccos(np.clip(unit@direction, -1,1)))
    steps = np.rad2deg(np.arccos(np.clip(np.sum(unit[1:]*unit[:-1],axis=1),-1,1)))
    stokes = dict(mean=s.mean(axis=0).tolist(), std=s.std(axis=0,ddof=1).tolist(),
        mean_direction=direction.tolist(), angular_rms_deg=float(np.sqrt(np.mean(angle**2))),
        successive_step_rms_deg=float(np.sqrt(np.mean(steps**2))),
        mean_dop=pax_summary['mean_dop'], power_uW=pax_summary['mean_power_w']*1e6,
        relative_power_rms_pct=100*np.sqrt(pax_summary['normalized_power_variance']),
        u_std_deg=float(np.rad2deg(np.sqrt(pax_summary['u_variance_rad2']))),
        v_std_deg=float(np.rad2deg(np.sqrt(pax_summary['v_variance_rad2']))),
        readings=len(pax), update_rate_hz=pax_summary['update_rate_hz'])
    return dict(path=path, setup=setup, comment=state.get("run_comment", ""), rows=rows, summaries=summaries, spectra=spectra,
                freq=freq, pax=pax, s=s, t=t, angle=angle, stokes=stokes)

runs = [read_run(name) for name in SOURCES]
for key in ('pd_input','band_hz','scope_decimation','fpga_average','phi1_bias_v','phi2_bias_v','pax_wavelength_nm','duration_per_condition_s','motor_settle_s'):
    assert runs[0]['setup'][key] == runs[1]['setup'][key], key
assert runs[0]['setup']['pd_input']=='in2'
assert np.array_equal(runs[0]['freq'], runs[1]['freq'])
freq=runs[0]['freq']; df=freq[1]; keep=(freq>=5)&(freq<=1000)
separation=float(np.rad2deg(np.arccos(np.clip(np.dot(runs[0]['stokes']['mean_direction'], runs[1]['stokes']['mean_direction']),-1,1))))
metrics=[]
for label,r in zip(LABELS,runs):
    for c in ('pax_on','pax_off'):
        m=r['summaries'][c]
        metrics.append(dict(placement=label,condition=c,samples=m['sample_count'],captures=m['capture_count'],
            dark_corrected_mean_mV=1e3*m['dark_corrected_mean_v'],variance_V2=m['band_variance_v2'],
            rms_uV=1e6*np.sqrt(m['band_variance_v2']),normalized_variance=m['normalized_band_variance'],
            relative_rms_pct=100*np.sqrt(m['normalized_band_variance'])))
with (OUT/'comparison.csv').open('w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=list(metrics[0]));w.writeheader();w.writerows(metrics)
peak_results=[]
for center in (228.1740307807922,238.41857910156247,278.465449810028):
    k=np.abs(freq-center)<=3
    excess=[float(np.sum((r['spectra']['pax_on']-r['spectra']['pax_off'])[k])*df) for r in runs]
    peak_results.append(dict(center_hz=center,half_width_hz=3,before_excess_V2=excess[0],after_excess_V2=excess[1],reduction_factor=excess[0]/excess[1] if excess[1]>0 else None))
(OUT/'analysis.json').write_text(json.dumps(dict(pd=metrics,stokes=[r['stokes'] for r in runs],mean_direction_separation_deg=separation,peak_bands=peak_results),indent=2)+'\n')

ratios = [r['summaries']['pax_on']['normalized_band_variance']/r['summaries']['pax_off']['normalized_band_variance'] for r in runs]
angular = [r['stokes']['angular_rms_deg'] for r in runs]
dop = [100*r['stokes']['mean_dop'] for r in runs]
peak_description = '; '.join(f"{p['center_hz']:.1f} Hz: before {p['before_excess_V2']:.3e} V², after {p['after_excess_V2']:.3e} V²" for p in peak_results)

plt.rcParams.update({'font.size':10,'axes.labelcolor':NAVY,'text.color':NAVY,'axes.titleweight':'bold','axes.spines.top':False,'axes.spines.right':False})

def figure(title,nrows=1,ncols=1):
    fig,axes=plt.subplots(nrows,ncols,figsize=(11.7,8.3),squeeze=False)
    fig.suptitle(title,x=.065,y=.94,ha='left',fontsize=20,color=NAVY,weight='bold')
    fig.subplots_adjust(left=.085,right=.95,bottom=.18,top=.84,hspace=.48,wspace=.32)
    return fig,axes


def save(pdf,fig,name,note):
    fig.text(.065,.045,note,fontsize=9,va='bottom')
    for ax in fig.axes:
        ax.grid(alpha=.18)
    pdf.savefig(fig)
    fig.savefig(OUT/'plots'/f'{name}.png',dpi=180)
    plt.close(fig)

def section_pages(pdf, title, sections):
    # Keep each prose section together while retaining the suite's typography.
    group, height = [], 0.0
    for section in sections:
        lines = sum(max(1, len(textwrap.wrap(line, width=110))) for line in str(section[1]).splitlines())
        needed = .046 + .024*lines
        if group and height + needed > .70:
            text_page(pdf, title, group)
            group, height = [], 0.0
        group.append(section)
        height += needed
    if group:
        text_page(pdf, title, group)

with PdfPages(OUT/'report.pdf',metadata={'Title':'PAX mount comparison: standard post versus rubber caps','Author':'Polarization diagnostics'}) as document:
    pdf=ReportPages(document)
    section_pages(pdf,'PAX mounts: before and after',[
        ('Comparison','Before: usual regular post holder. After: rubber-cap-supported assembly below. Labels describe the mounts, not measured damping coefficients.'),
        ('After: mounting stack',MOUNT_COMMENT),
        ('Mount description provenance',f"Latest comment: {runs[1]['comment']} Stack description comes from 203321, confirmed by the operator; its measurement data are not used."),
        ('Source experiments',f'Before: {SOURCES[0]}\nAfter: {SOURCES[1]}\nBoth completed; this is offline analysis of the saved raw measurements.'),
        ('Main finding',f"The normalized 5–1000 Hz motor-on/off variance ratio falls from {ratios[0]:.2f} to {ratios[1]:.2f}. Absolute and relative motor-on PD noise are lower with the rubber-cap-supported mount. The original peak bands are compared without clipping signed excess."),
        ('Polarization finding',f"Mean Stokes directions differ by {separation:.1f} degrees on the Poincare sphere. Angular RMS spread is {angular[0]:.2f} degrees before and {angular[1]:.2f} degrees after; mean DoP is {dop[0]:.2f}% and {dop[1]:.2f}%. Similar DoP does not mean identical polarization direction."),
        ('Interpretation','Consistent with reduced coupling, but mount geometry and optical state both differ. Rubber damping is not isolated from other changes; there is one run per configuration.'),
        ('Matched acquisition','IN2; zero static outputs; 30 s requested per motor state; 5 s settling after motor stop; 15,258.8 samples/s; 16,384 samples per capture; 830 nm PAX setting. Motor off means rotation stopped, not electrical power removed.')])
    # Numeric summary uses table rather than raw JSON.
    fig,axes=figure('Quantitative comparison');ax=axes[0,0];ax.axis('off')
    table_rows=[]
    def metric(label, values):
        table_rows.append([label, *values])
    for condition, name in (('pax_on','on'),('pax_off','off')):
        metric(f'PD band RMS, motor {name}',[f"{np.sqrt(r['summaries'][condition]['band_variance_v2'])*1e6:.1f} uV" for r in runs])
        metric(f'PD relative band RMS, motor {name}',[f"{100*np.sqrt(r['summaries'][condition]['normalized_band_variance']):.3f}%" for r in runs])
    metric('Normalized band variance: on / off',[f'{v:.3f}' for v in ratios])
    metric('PD light signal, motor on',[f"{r['summaries']['pax_on']['dark_corrected_mean_v']*1e3:.3f} mV" for r in runs])
    metric('Mean PAX optical power',[f"{r['stokes']['power_uW']:.1f} uW" for r in runs])
    metric('Mean DoP',[f'{v:.2f}%' for v in dop])
    metric('Stokes angular RMS spread',[f'{v:.2f} deg' for v in angular])
    metric('Successive-reading angular RMS step',[f"{r['stokes']['successive_step_rms_deg']:.2f} deg" for r in runs])
    metric('PAX power relative RMS',[f"{r['stokes']['relative_power_rms_pct']:.2f}%" for r in runs])
    metric('PD captures, on / off',[f"{r['summaries']['pax_on']['capture_count']} / {r['summaries']['pax_off']['capture_count']}" for r in runs])
    metric('PAX readings / approximate rate',[f"{r['stokes']['readings']} / {r['stokes']['update_rate_hz']:.2f} Hz" for r in runs])
    table=ax.table(cellText=table_rows,colLabels=['Metric','Before: standard post','After: rubber caps'],cellLoc='left',colLoc='left',loc='center',colWidths=[.49,.23,.28]);table.auto_set_font_size(False);table.set_fontsize(10);table.scale(1,2.0)
    for (i,j),cell in table.get_celld().items():
        cell.set_edgecolor('white');cell.set_facecolor(NAVY if i==0 else ('#edf4f5' if i%2 else '#f7f9fa'))
        if i==0:cell.get_text().set_color('white')
    save(pdf,fig,'01_metrics','PD metrics use the 5–1000 Hz band. PAX metrics use its slow, motor-on readings only.\nRelative PD power uses the measured dark-corrected mean; voltage noise is not fringe-contrast variance.')
    fig,axes=figure('PD spectra: suppression of the original peaks',2,2)
    for i,r in enumerate(runs):
        for c,ls in (('pax_on','-'),('pax_off','--'),('dark',':')):
            axes[0,i].loglog(freq[1:],r['spectra'][c][1:]*1e6,ls,label=c.replace('_',' '),lw=1)
        axes[0,i].set(xlim=(5,1000),ylim=(1e-9,2e-2),title=LABELS[i],xlabel='Frequency (Hz)',ylabel='Voltage PSD (mV²/Hz)');axes[0,i].legend(fontsize=9)
        for c,ls in (('pax_on','-'),('pax_off','--')):
            axes[1,0].semilogy(freq,r['spectra'][c]*1e6,color=COLORS[i],ls=ls,lw=1,label=f'{"Before" if i==0 else "After"}, {c[4:]}')
        axes[1,1].plot(freq,(r['spectra']['pax_on']-r['spectra']['pax_off'])*1e6,color=COLORS[i],label=LABELS[i],lw=1)
    axes[1,0].set(xlim=(200,310),ylim=(1e-9,2e-2),xlabel='Frequency (Hz)',ylabel='Voltage PSD (mV²/Hz)',title='Same axes: 228 / 238 Hz region');axes[1,0].legend(fontsize=8)
    axes[1,1].set(xlim=(200,310),xlabel='Frequency (Hz)',ylabel='On − off PSD (mV²/Hz)',title='Signed motor-associated excess');axes[1,1].axhline(0,color='gray',lw=.7)
    save(pdf,fig,'02_spectra','Identical Hann periodogram method and frequency grid (~0.93 Hz bins); same vertical scales in the log plots.\nDark traces show the electronics baseline. Frequency peaks alone do not identify mechanical causation.')
    fig,axes=figure('Where the noise power accumulates',1,2)
    for i,r in enumerate(runs):
        for c,ls in (('pax_on','-'),('pax_off','--')):
            cumulative=np.cumsum(r['spectra'][c][keep])*df
            axes[0,0].plot(freq[keep],cumulative*1e6,color=COLORS[i],ls=ls,label=f'{"Before" if i==0 else "After"}, {c[4:]}')
        excess=np.cumsum((r['spectra']['pax_on']-r['spectra']['pax_off'])[keep])*df
        axes[0,1].plot(freq[keep],excess*1e6,color=COLORS[i],label=LABELS[i])
    axes[0,0].set(xlabel='Upper integration frequency (Hz)',ylabel='Variance from 5 Hz (mV²)',title='Cumulative measured variance');axes[0,0].legend()
    axes[0,1].set(xlabel='Upper integration frequency (Hz)',ylabel='Signed excess variance (mV²)',title='Cumulative on-minus-off excess');axes[0,1].legend();axes[0,1].axhline(0,color='gray',lw=.7)
    save(pdf,fig,'03_cumulative_noise','Integration starts at 5 Hz. Sharp rises identify frequency regions contributing most of the noise.\nNegative contributions are retained; excess power is not clipped or claimed to be purely mechanical.')
    fig,axes=figure('PD trace width and light level across captures',2,2)
    for i,r in enumerate(runs):
        for c,ls,marker in (('pax_on','-','o'),('pax_off','--','s')):
            rows=[row for row in r['rows'] if row['condition']==c];t=np.array([row['started_s'] for row in rows]);t-=t[0]
            values=np.sqrt([row['band_variance_v2'] for row in rows])*1e6
            axes[0,i].plot(t,values,ls+marker,ms=3,label=c.replace('_',' '))
            means=np.array([row['mean_v']-row['dark_voltage_v'] for row in rows])*1e3
            axes[1,i].plot(t,means,ls+marker,ms=3,label=c.replace('_',' '))
        axes[0,i].set(title=LABELS[i],ylim=(0,360),xlabel='Elapsed within each motor state (s)',ylabel='5–1000 Hz RMS (uV)');axes[0,i].legend()
        axes[1,i].set(ylim=(2,10),xlabel='Elapsed within each motor state (s)',ylabel='Dark-corrected mean PD voltage (mV)')
    save(pdf,fig,'04_capture_variability','Each point is one ~1.074 s FPGA capture, not an independent placement trial. Motor windows are sequential.\nPD mean light level is lower after the mount change. Compare normalized variance as well as voltage noise.')
    fig,axes=figure('PAX trajectories: polarization, power and DoP',5,2)
    fig.subplots_adjust(hspace=.48,top=.85,bottom=.18)
    for i,r in enumerate(runs):
        for j in range(3):
            axes[j,i].plot(r['t'],r['s'][:,j],color=COLORS[i],lw=1)
            axes[j,i].set(ylim=(-1.05,1.05),ylabel=f's{j+1}')
        axes[0,i].set_title(LABELS[i])
        axes[3,i].plot(r['t'],[p['pax_ptotal']*1e6 for p in r['pax']],color=COLORS[i]);axes[3,i].set(ylabel='Power (uW)',ylim=(400,1150))
        axes[4,i].plot(r['t'],[p['dop'] for p in r['pax']],color=COLORS[i]);axes[4,i].set(ylabel='DoP',ylim=(.7,1.05),xlabel='Elapsed PAX motor-on time (s)')
        for j in range(4):axes[j,i].tick_params(labelbottom=False)
    save(pdf,fig,'05_stokes_time','Normalized Stokes direction is plotted separately from DoP. Identical axis limits enable direct comparison.\nPAX samples at ~6.4–6.8 Hz: these trajectories cannot resolve the PD peaks near 228 and 238 Hz.')
    fig=plt.figure(figsize=(11.7,8.3));fig.suptitle('Polarization geometry and angular fluctuations',x=.065,y=.94,ha='left',fontsize=20,weight='bold')
    ax=fig.add_subplot(121,projection='3d');right=fig.add_subplot(122)
    u=np.linspace(0,2*np.pi,30);v=np.linspace(0,np.pi,16)
    ax.plot_wireframe(np.outer(np.cos(u),np.sin(v)),np.outer(np.sin(u),np.sin(v)),np.outer(np.ones_like(u),np.cos(v)),color='gray',alpha=.15,lw=.5)
    for i,r in enumerate(runs):
        s=r['s'];ax.plot(s[:,0],s[:,1],s[:,2],color=COLORS[i],alpha=.7,lw=.8)
        ax.scatter(*r['stokes']['mean_direction'],color=COLORS[i],s=70,marker='*',label=LABELS[i])
        right.plot(r['t'],r['angle'],color=COLORS[i],label=LABELS[i],lw=1)
    ax.set(xlabel='s1',ylabel='s2',zlabel='s3',xlim=(-1,1),ylim=(-1,1),zlim=(-1,1));ax.set_box_aspect((1,1,1));ax.view_init(22,35)
    right.set(xlabel='Elapsed motor-on time (s)',ylabel='Angle from own mean direction (deg)',title='Coordinate-independent angular spread');right.legend(fontsize=8)
    fig.subplots_adjust(left=.045,right=.95,top=.84,bottom=.19,wspace=.28)
    save(pdf,fig,'06_poincare_angles',f'Mean directions are {separation:.1f}° apart; stars mark normalized mean directions, not polarization magnitude.\nAngular RMS: {angular[0]:.2f}° before, {angular[1]:.2f}° after. Slow Stokes spread is distinct from fast PD noise.')
    section_pages(pdf,'Methods, provenance and interpretation',[
        ('PD calculation','Signed voltages are retained. Each capture is mean-subtracted, Hann-windowed and converted to a one-sided periodogram. Periodograms are averaged within each motor state. Integrate 5–1000 Hz; RMS is the square root of variance. Normalize by the square of each state’s dark-corrected mean voltage. No dark-noise PSD subtraction is applied.'),
        ('Peak comparison','Integrate signed on-minus-off PSD over +/-3 Hz around the original peak centers. ' + peak_description + '. These are descriptive comparisons, not confidence bounds or mechanical transfer functions; negative excess remains signed.'),
        ('Stokes calculation','s1–s3 are the suite’s normalized Stokes direction; DoP is separate. Mean directions are normalized vector averages. Angular spread is RMS geodesic distance to each run’s own mean direction. Successive-step RMS uses adjacent readings at similar, but not identical, update rates. Slow drift and measurement noise are both included.'),
        ('Limits on attribution','Mount geometry, support and bracing changed together. Optical power, mean polarization and DoP also changed; a different optical operating point can change sensitivity to phase and polarization motion. One run per placement supplies no placement-level replication. Do not describe this as contrast variance or proof of a mechanical frequency.'),
        ('Next discriminating measurement','Repeat alternating placements or isolation states while preserving optical alignment, polarization operating point, PD input and gain. Record a vibration sensor synchronously if identifying mechanical coupling is the objective.'),
        ('Verification and reproducibility','Raw CSV values and setup metadata were read directly. Recomputed PD statistics and PAX summary metrics match the saved run summaries to 1e-12 relative tolerance. Source hashes and software versions are in provenance.json. analyze.py regenerates this report without instrument access; source experiments are not modified.')])

source_hashes={name:{f:sha(SOURCE_ROOT/name/f) for f in ('data.csv','vibration-setup.json','vibration.json','recipe.json','run.json')} for name in SOURCES}
import matplotlib,sys
provenance=dict(created_at=datetime.now(timezone.utc).isoformat(),analysis='offline placement comparison',sources=source_hashes,
 operator_description=dict(before=LABELS[0],after=LABELS[1],mount_stack=MOUNT_COMMENT,latest_comment=runs[1]['comment'],description_source=str(MOUNT_COMMENT_SOURCE.relative_to(SOURCE_ROOT)),description_source_sha256=sha(MOUNT_COMMENT_SOURCE),base_mass_kg=None),
 script_sha256=sha(Path(__file__)),python=sys.version,numpy=np.__version__,matplotlib=matplotlib.__version__,
 verification='Recomputed raw-data PD and PAX metrics agree with saved summaries at rtol=1e-12; matched acquisition settings asserted.',
 regeneration='env PYTHONPATH=python python experiments/polarization_locking/2026-09-25/comparison_standard-vs-rubber-caps/analyze.py')
(OUT/'provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
(OUT/'README.md').write_text('''# PAX mount comparison: standard post versus rubber caps

[PDF report](report.pdf) compares 2026-09-24/203850 (standard post mount) with 2026-09-25/204124 (rubber-cap-supported mount). The 203321 run supplies only the detailed mount comment; no measurements from it are included.

- `plots/`: six standalone visual diagnostics.
- `comparison.csv`: derived PD metrics, not duplicated raw samples.
- `analysis.json`: PD, Stokes and peak-band results.
- `provenance.json`: source hashes, assumptions and regeneration command.
- `analyze.py`: offline reproducible analysis using the suite report styling.

The original experiment directories remain unchanged. The report distinguishes reduced PD noise from changed polarization and optical operating point.
''')
print('Report:',OUT/'report.pdf');print('Pages:',pdf.page);print('Mean direction separation:',separation);print('Peak reductions:',peak_results)
