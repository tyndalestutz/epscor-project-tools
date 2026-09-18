#!/usr/bin/env python3
"""Temporary-fiber-probe spatial scenarios, with a derived free-space PDF report.

Efficiencies constrain illustrative Gaussian profiles, not operational arm loss.
No geometric parameter is fitted. A/B displacement signs and D-arm phase slope
are explicit scenario choices; the underlying splitter study is reused.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

import numpy as np

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = 'field_propogation'

from .acquisition.session import read_section
from .configuration import ConfigurationError, from_dicts, read_json
from .detection import detected_outputs, gaussian_mode_overlaps
from .propagation import compile_network
from .run_simulation import write_json
from .splitter_study import configurations

ROUTES = ('BD', 'BC', 'AD', 'AC')
MODES = ((0, 0), (0, 1), (1, 0), (1, 1))


def validate_settings(settings: dict) -> None:
    probe, model, scan = settings['probe'], settings['gaussian_model'], settings['scan']
    if settings['schema_version'] != 1 or probe['in_operating_path'] is not False:
        raise ConfigurationError('This study models temporary probes, not inline fiber loss')
    for key in ('efficiency_a', 'efficiency_b', 'common_throughput'):
        value = probe[key]
        if type(value) not in (int, float) or not np.isfinite(value) or not 0 < value <= 1:
            raise ConfigurationError(f'{key} must be a power fraction in (0,1]')
    if max(probe['efficiency_a'], probe['efficiency_b']) > probe['common_throughput']:
        raise ConfigurationError('Probe efficiency exceeds assumed common throughput')
    for key in ('w_m','wavelength_m'):
        if model[key] is not None and (type(model[key]) not in (int,float) or not np.isfinite(model[key]) or model[key]<=0):
            raise ConfigurationError(f'{key} must be null or a positive finite length in meters')
    if model['geometries'] != ['same_side', 'opposite_sides']:
        raise ConfigurationError('This paired ambiguity study uses same_side and opposite_sides')
    for key in ('d_arm_tilt_q', 'tilt_sweep_q_max'):
        if not np.isfinite(model[key]) or model[key] < 0:
            raise ConfigurationError('Dimensionless tilt and sweep endpoint must be finite and nonnegative')
    if type(scan['points']) is not int or not 9 <= scan['points'] <= 1001:
        raise ConfigurationError('Scan points must be an integer from 9 to 1001')
    if not np.isfinite([scan['phi1_fixed_deg'], scan['phi2_fixed_deg']]).all():
        raise ConfigurationError('Held phases must be finite')


def geometry(settings: dict, name: str, tilt: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return route displacements, phase slopes, Gram matrix and probe projection."""
    if name == 'aligned':
        da = db = 0.
    elif name in ('same_side', 'opposite_sides'):
        p = settings['probe']
        da = np.sqrt(np.log(p['common_throughput']/p['efficiency_a']))
        db = np.sqrt(np.log(p['common_throughput']/p['efficiency_b']))
        if name == 'opposite_sides': db = -db
    else:
        raise ConfigurationError('Unknown geometry scenario')
    positions = np.array([[da if route[0]=='A' else db, 0.] for route in ROUTES])
    slopes = np.array([[tilt if route[1]=='D' else 0., 0.] for route in ROUTES])
    gram, projection = gaussian_mode_overlaps(positions, slopes)
    return positions, slopes, gram, projection


def detectors(gram: np.ndarray) -> dict:
    return dict(ports={port:dict(kind='integrating', mode_keys=MODES, gram=gram) for port in ('E', 'F')})


def intensity_coefficients(network, gram: np.ndarray) -> dict:
    """Exact I=A+2 Re(K exp(i phi2)); collect path products, without fitting."""
    result = {}
    for port in network.outputs:
        terms = {}
        for i, first in enumerate(MODES):
            for j, second in enumerate(MODES):
                mode = tuple(b-a for a,b in zip(first,second))
                contribution = np.vdot(network.fields[port][first],network.fields[port][second])*gram[i,j]
                terms[mode] = terms.get(mode,0j)+contribution
        if any(abs(value)>1e-12 for mode,value in terms.items() if mode not in ((0,0),(0,1),(0,-1))):
            raise ConfigurationError('Flow contains additional intensity harmonics outside this derivation')
        mean, k = float(terms[(0,0)].real), complex(terms[(0,1)])
        result[port] = dict(mean=mean, k_real=k.real, k_imag=k.imag,
                            modulation_amplitude=2*abs(k), visibility=2*abs(k)/mean,
                            minimum=mean-2*abs(k), maximum=mean+2*abs(k))
    return result


def evaluate_study(settings: dict, powers: dict):
    validate_settings(settings)
    flow, cases = configurations(powers)
    for parameters in cases.values():
        parameters['phase_values'] = {phase:np.deg2rad(settings['scan'][phase+'_fixed_deg']) for phase in ('phi1','phi2')}
    networks = {name:compile_network(from_dicts(flow,params)) for name,params in cases.items()}
    results = {}
    tilt = settings['gaussian_model']['d_arm_tilt_q']
    axis = np.linspace(0,2*np.pi,settings['scan']['points'])
    for name in settings['gaussian_model']['geometries']:
        positions,slopes,gram,projection = geometry(settings,name,tilt)
        record = dict(displacements_over_w=positions, dimensionless_tilts=slopes,
                      hypothetical_probe_power_fractions=settings['probe']['common_throughput']*abs(projection)**2,
                      gamma_a=gram[2,3],gamma_b=gram[0,1],splitter_cases={})
        for case,network in networks.items():
            coefficients=intensity_coefficients(network,gram)
            scans=detected_outputs(network,{'phi2':axis},detectors(gram))
            coefficients['max_sum_error']=float(np.max(abs(scans['E']['intensity']+scans['F']['intensity']-2)))
            coefficients['dop_ranges']={port:[float(np.nanmin(obs['dop'])),float(np.nanmax(obs['dop']))] for port,obs in scans.items()}
            record['splitter_cases'][case]=coefficients
        results[name]=record
    return flow,cases,networks,results


def plots_and_csv(settings: dict, networks: dict, output: Path) -> None:
    import os
    os.environ.setdefault('MPLCONFIGDIR','/tmp/matplotlib-field-propagation')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    qaxis=np.linspace(0,settings['gaussian_model']['tilt_sweep_q_max'],241)
    fig,axes=plt.subplots(1,2,figsize=(10,4),layout='constrained')
    styles={'ideal':('black','--','Ideal splitters'), 'C_reference':('#0072b2','-','Measured C reference'),
            'D_reference':('#d55e00',':','Measured D reference')}
    with (output/'visibility-sweep.csv').open('w',newline='') as handle:
        writer=csv.writer(handle);writer.writerow(['geometry','splitters','tilt_q','port','mean','k_real','k_imag','visibility'])
        for ax,name in zip(axes,settings['gaussian_model']['geometries']):
            for case,network in networks.items():
                vis=[]
                for q in qaxis:
                    gram=geometry(settings,name,q)[2]
                    coefficients=intensity_coefficients(network,gram)
                    vis.append(coefficients['E']['visibility'])
                    for port,data in coefficients.items():
                        writer.writerow([name,case,q,port,data['mean'],data['k_real'],data['k_imag'],data['visibility']])
                color,style,label=styles[case]
                ax.plot(qaxis,vis,color=color,ls=style,label=label)
            ax.axhline(.6,color='#777777',lw=1,label='User contrast reference V=0.6')
            ax.axvline(settings['gaussian_model']['d_arm_tilt_q'],color='#777777',ls=':',lw=1)
            ax.set(title=name.replace('_',' '),xlabel=r'D-arm relative phase slope $q=kw\theta$',ylabel='E-port visibility V',ylim=(0,1))
            ax.grid(alpha=.2)
        axes[0].legend(fontsize=7)
    fig.suptitle('Same probe efficiencies, different spatial hypotheses; no fitting')
    fig.savefig(output/'visibility.pdf');fig.savefig(output/'visibility.png',dpi=150);plt.close(fig)
    # Stokes here are integrated coherency observables, not renormalized Jones directions.
    axis=np.linspace(0,2*np.pi,settings['scan']['points'])
    scenarios=[('aligned',0.,'black','--','Aligned spatial baseline'),
               ('same_side',settings['gaussian_model']['d_arm_tilt_q'],'#0072b2','-','Same-side offsets'),
               ('opposite_sides',settings['gaussian_model']['d_arm_tilt_q'],'#d55e00','-','Opposite-side offsets')]
    network=networks['C_reference']
    with (output/'phase-cuts.csv').open('w',newline='') as handle:
        writer=csv.writer(handle);writer.writerow(['scenario','phase','phase_deg','port','intensity','s1','s2','s3','dop'])
        for port in ('E','F'):
            fig,axes=plt.subplots(5,2,figsize=(10,11.5),sharex=True,layout='constrained')
            for col,phase in enumerate(('phi1','phi2')):
                fixed='phi2' if phase=='phi1' else 'phi1'
                for name,tilt,color,style,label in scenarios:
                    gram=geometry(settings,name,tilt)[2]
                    obs=detected_outputs(network,{phase:axis},detectors(gram))[port]
                    for index,angle in enumerate(np.degrees(axis)):
                        writer.writerow([name,phase,angle,port,obs['intensity'][index],*obs['stokes'][index],obs['dop'][index]])
                    for row,values in enumerate([obs['intensity'],*obs['stokes'].T,obs['dop']]):
                        axes[row,col].plot(np.degrees(axis),values,color=color,ls=style,lw=1.6,label=label)
                axes[0,col].set_title(f'{phase} sweep; {fixed}={settings["scan"][fixed+"_fixed_deg"]:g} degrees')
                for row,label in enumerate(('Intensity','s1','s2','s3','Degree of polarization')):
                    axes[row,col].set_ylabel(label);axes[row,col].grid(alpha=.2)
                    if row in (1,2,3): axes[row,col].set_ylim(-1.05,1.05)
                axes[-1,col].set_ylim(-.03,1.03);axes[-1,col].set_xlabel(phase+' (degrees)')
            axes[0,0].legend(fontsize=7)
            fig.suptitle(f'Port {port}: free-space collection, temporary fiber removed\nMeasured splitter C reference; illustrative spatial geometry')
            fig.savefig(output/f'phase-{port}.pdf');fig.savefig(output/f'phase-{port}.png',dpi=150);plt.close(fig)
    x=np.linspace(-3.5,3.5,600)
    fig,axes=plt.subplots(1,2,figsize=(10,3.5),layout='constrained')
    for ax,name in zip(axes,settings['gaussian_model']['geometries']):
        positions=geometry(settings,name,0)[0]
        ax.plot(x,np.exp(-2*x*x),'k--',label='Reference fiber mode')
        for origin,index,color in [('A',2,'#0072b2'),('B',0,'#d55e00')]:
            ax.plot(x,np.exp(-2*(x-positions[index,0])**2),color=color,label=origin)
        ax.set(title=name.replace('_',' '),xlabel='Transverse position x/w',ylabel='Normalized profile shape')
        ax.legend(fontsize=8);ax.grid(alpha=.2)
    fig.suptitle('Two Gaussian arrangements consistent with the same example probe powers')
    fig.savefig(output/'geometry.pdf');fig.savefig(output/'geometry.png',dpi=150);plt.close(fig)


def render_report(settings: dict, networks: dict, results: dict, output: Path, powers: dict) -> None:
    from .reporting import escape
    p=settings['probe'];g=settings['gaussian_model']
    da,db=np.sqrt(np.log(p['common_throughput']/np.array([p['efficiency_a'],p['efficiency_b']])))
    q=g['d_arm_tilt_q'];gaussian_factor=np.exp(-q*q/8)
    raw_center=np.sqrt(p['efficiency_a']*p['efficiency_b'])/p['common_throughput']
    residual=np.sqrt((1-p['efficiency_a']/p['common_throughput'])*(1-p['efficiency_b']/p['common_throughput']))
    tokens={k:f'{v:.6f}' for k,v in dict(ETA_A=p['efficiency_a'],ETA_B=p['efficiency_b'],ETA_0=p['common_throughput'],
        HA=np.sqrt(p['efficiency_a']/p['common_throughput']),HB=np.sqrt(p['efficiency_b']/p['common_throughput']),DA=da,DB=db,Q=q,
        GFACT=gaussian_factor,SEP_SAME=abs(da-db),SEP_OPPOSITE=da+db,
        GRAM_CENTER=raw_center,GRAM_RADIUS=residual,GRAM_LOW=max(0,raw_center-residual),GRAM_HIGH=min(1,raw_center+residual),
        INLINE_V=abs(p['efficiency_a']-p['efficiency_b'])/(p['efficiency_a']+p['efficiency_b']),
        ORDINARY_V=2*np.sqrt(p['efficiency_a']*p['efficiency_b'])/(p['efficiency_a']+p['efficiency_b'])).items()}
    tokens['ETA_A_PERCENT']=f"{100*p['efficiency_a']:g}"
    tokens['ETA_B_PERCENT']=f"{100*p['efficiency_b']:g}"
    tokens['INLINE_PERCENT']=f"{100*abs(p['efficiency_a']-p['efficiency_b'])/(p['efficiency_a']+p['efficiency_b']):.3f}"
    rows=[];numerics=[]
    for name in settings['gaussian_model']['geometries']:
        for case,data in results[name]['splitter_cases'].items():
            rows.append(f'{escape(name.replace("_"," "))} & {escape(case.replace("_"," "))} & {data["E"]["visibility"]:.6f} & {100*data["E"]["visibility"]:.3f} & {data["F"]["visibility"]:.6f} '+r'\\')
        e=results[name]['splitter_cases']['C_reference']['E']
        gamma_a=results[name]['gamma_a'];gamma_b=results[name]['gamma_b']
        def comp(v):return f'{v.real:.6f}{v.imag:+.6f}i'
        numerics.append(r'\paragraph{'+escape(name.replace('_',' '))+r'.} '+
                       r'$\Gamma_A='+comp(gamma_a)+r'$, $\Gamma_B='+comp(gamma_b)+r'$. Thus'+
                       r'\[I_E='+f'{e["mean"]:.9f}{2*e["k_real"]:+.9f}'+r'\cos\phi_2'+f'{-2*e["k_imag"]:+.9f}'+r'\sin\phi_2,\qquad V_E='+f'{e["visibility"]:.6f}'+r'.\]')
    tokens['RESULT_ROWS']='\n'.join(rows);tokens['NUMERICS']='\n'.join(numerics)
    splitter_rows=[]
    for label,transmitted,reflected in [('NPBS1 A/x','AC','AD'),('NPBS1 B/y','BD','BC'),
                                       ('NPBS2 C/x','ACE','ACF'),('NPBS2 C/y','BCE','BCF'),
                                       ('NPBS2 D/x','ADF','ADE'),('NPBS2 D/y','BDF','BDE')]:
        pt,pr=powers[transmitted],powers[reflected]
        splitter_rows.append(f'{label} & {pt:g}/{pt+pr:g} & {np.sqrt(pt/(pt+pr)):.6f} & {np.sqrt(pr/(pt+pr)):.6f} '+r'\\')
    tokens['SPLITTER_ROWS']='\n'.join(splitter_rows)
    tokens['PHYSICAL_GEOMETRY']='No displacement in micrometers or tilt in milliradians is inferred because beam radius and wavelength are unspecified.'
    if g['w_m'] is not None and g['wavelength_m'] is not None:
        theta=q*g['wavelength_m']/(2*np.pi*g['w_m'])
        tokens['PHYSICAL_GEOMETRY']=f'With the supplied radius and wavelength, the assumed displacement magnitudes are {da*g["w_m"]*1e6:.4f} and {db*g["w_m"]*1e6:.4f} micrometers; the assumed tilt is {theta*1e3:.4f} milliradians (paraxial approximation).'

    probe_powers=np.array(results['same_side']['hypothetical_probe_power_fractions'])
    tokens['PROBE_ROWS']='\n'.join(f'{route} & {power:.6f} & {power:.6f} '+r'\\' for route,power in zip(ROUTES,probe_powers))
    vals=networks['C_reference'].config.parameters['values']
    angles=[vals[k] for k in ('mu1_x','mu1_y','mu2_x','mu2_y')]
    tx,ty,ux,uy=np.cos(angles);rx,ry,vx,vy=np.sin(angles)
    tokens.update(X_WEIGHT=f'{tx*ux*rx*vx:.9f}',Y_WEIGHT=f'{ry*uy*ty*vy:.9f}',
                  MEAN=f'{intensity_coefficients(networks["C_reference"],np.ones((4,4)))["E"]["mean"]:.9f}')
    template=(Path(__file__).parent/'templates/spatial_derivation.tex').read_text()
    for key,value in tokens.items():template=template.replace('@@'+key+'@@',value)
    if '@@' in template:raise ConfigurationError('Unresolved spatial report template token')
    (output/'spatial-derivation.tex').write_text(template)
    for _ in range(2):
        result=subprocess.run(['pdflatex','-no-shell-escape','-interaction=nonstopmode','-halt-on-error','spatial-derivation.tex'],
                              cwd=output,capture_output=True,text=True,timeout=60)
        if result.returncode:raise RuntimeError(result.stdout[-3000:])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    root=Path(__file__).resolve().parents[2]
    parser.add_argument('--settings',type=Path,default=Path(__file__).parent/'configs/parameters/spatial_coupling_study.json')
    parser.add_argument('--campaign',type=Path,default=root/'experiments/field_propagation/measurement_campaign/2026-09-10')
    parser.add_argument('--dataset',default='direct_meter_user_2026_09_16')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    output=args.output or root/'experiments/field_propagation'/(datetime.now(timezone.utc).strftime('%Y-%m-%d_%H%M%S')+'_spatial_probe')
    if output.exists() and any(output.iterdir()):parser.error('Choose a new or empty output directory')
    settings=read_json(args.settings)
    observation=read_section(args.campaign,'path_power_observations')[args.dataset]
    flow,cases,networks,results=evaluate_study(settings,observation['powers_uw'])
    output.mkdir(parents=True,exist_ok=True)
    sources=[Path(__file__),Path(__file__).parent/'detection.py',Path(__file__).parent/'propagation.py',
             Path(__file__).parent/'splitter_study.py',Path(__file__).parent/'templates/spatial_derivation.tex']
    provenance=dict(created_utc=datetime.now(timezone.utc).isoformat(),
                    settings_sha256=hashlib.sha256(args.settings.read_bytes()).hexdigest(),
                    campaign_sha256=hashlib.sha256((args.campaign/'campaign.json').read_bytes()).hexdigest(),
                    implementation_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sources})
    write_json(output/'study-inputs.json',dict(settings=settings,splitter_source=observation,flow=flow,splitter_cases=cases,provenance=provenance))
    write_json(output/'results.json',results)
    plots_and_csv(settings,networks,output)
    render_report(settings,networks,results,output,observation['powers_uw'])
    (output/'README.md').write_text('# Temporary fiber probe spatial study\n\nOpen [spatial-derivation.pdf](spatial-derivation.pdf). '
        'The 50%/40% values are illustrative. Probe efficiencies are not applied as operating losses. '
        'Same-side and opposite-side Gaussian geometries are separate hypotheses with identical probe powers. '
        'Tilt is swept, never fitted. CSVs retain phase/Stokes/DOP curves and visibility scans.\n')
    print('Report:',output/'spatial-derivation.pdf')
    for name,record in results.items():
        print(name,{case:round(data['E']['visibility'],6) for case,data in record['splitter_cases'].items()})


if __name__=='__main__':main()
