#!/usr/bin/env python3
"""Isolate measured splitter ratios with equal input and ideal remaining optics.

No fit, extra loss, retarder or phase parameter is introduced. NPBS2 C- and
D-input calibrations are separate lossless completions because their measured
splits differ. Outputs are generated artifacts; campaign readings stay primary.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = 'field_propogation'

from .acquisition.reduction import path_power_summary
from .acquisition.session import read_section
from .configuration import ConfigurationError, from_dicts
from .observables import describe
from .propagation import compile_network
from .run_simulation import run, write_json


def configurations(powers: dict) -> tuple[dict, dict[str, dict]]:
    """Use output-normalized power fractions; never infer section transmission.

    Ideal PBS: A carries x and B carries y. This assignment and reciprocal
    lossless completion are baseline assumptions, not measured polarimetry.
    """
    summary = path_power_summary(powers)
    flow = dict(schema_version=1, name='Splitter-only hybrid MZI',
                description='Equal input; ideal PBS and scalar phase controls; measured NPBS split fractions. '
                'No losses, leakage, retarders, fitted offsets or spatial parameters. '
                'Ideal PBS assigns A to x and B to y; ideal reciprocal lossless completion with reflected phase +pi/2.',
                phases=['phi1','phi2'], input=dict(name='Ein',jones=['ax','ay']),
                elements=[
                    dict(name='PBS',type='pbs',inputs=['Ein'],outputs=['A','B']),
                    dict(name='phase_A',type='phase',inputs=['A'],outputs=['A_phase'],phase='phi1'),
                    dict(name='NPBS1',type='npbs',inputs=['A_phase','B'],outputs=['C','D'],
                         mixing_rad='mu1_x',mixing_y_rad='mu1_y'),
                    dict(name='phase_C',type='phase',inputs=['C'],outputs=['C_phase'],phase='phi2'),
                    dict(name='NPBS2',type='npbs',inputs=['C_phase','D'],outputs=['E','F'],
                         mixing_rad='mu2_x',mixing_y_rad='mu2_y')], outputs=['E','F'])
    cases = {}
    for case, branch in [('ideal',None),('C_reference','C'),('D_reference','D')]:
        fractions = [.5]*4 if branch is None else [
            summary['npbs1_collected_splits']['A']['transmitted_fraction'],
            summary['npbs1_collected_splits']['B']['transmitted_fraction'],
            summary['downstream_routes']['A'+branch]['transmitted_fraction'],
            summary['downstream_routes']['B'+branch]['transmitted_fraction']]
        values = dict(ax=1,ay=1)
        values.update(zip(('mu1_x','mu1_y','mu2_x','mu2_y'),
                          [float(np.arccos(np.sqrt(t))) for t in fractions]))
        sources = {
            'mu1_x':['AC','AD'], 'mu1_y':['BC','BD'],
            'mu2_x':['AC'+p for p in 'EF'] if branch=='C' else ['AD'+p for p in 'EF'],
            'mu2_y':['BC'+p for p in 'EF'] if branch=='C' else ['BD'+p for p in 'EF']}
        metadata = {}
        for key in values:
            ideal = branch is None or key in ('ax','ay')
            metadata[key] = dict(status='assumed' if ideal else 'derived',
                                 units='relative field' if key in ('ax','ay') else 'rad',
                                 quantity='input amplitude' if key in ('ax','ay') else 'splitter mixing angle',
                                 plane='P0' if key in ('ax','ay') else ('NPBS1' if key.startswith('mu1') else 'NPBS2'),
                                 sources=[] if ideal else sources[key],uncertainty=None,
                                 notes='Ideal baseline value; not a new measurement.' if ideal else
                                 'arccos(sqrt(Ptrans/(Ptrans+Prefl))); no fit. Unknown measurement uncertainty; '
                                 'ideal lossless reciprocal completion and reflection phase retained.')
        cases[case] = dict(schema_version=1,name='Ideal baseline' if branch is None else 'Measured splitter ratios: '+branch+'-side NPBS2 reference',
                           description='Conditional splitter-only study. Relative units, ax=ay=1. '
                           'Power-only data do not determine complex phase, polarization mixing, absolute loss or uncertainty. '
                           'C- and D-reference cases are separate completions, not confidence bounds.',
                           values=values,provenance=metadata,phase_values=dict(phi1=0,phi2=np.pi/2),
                           scan=dict(points=361,start_rad=0,stop_rad=2*np.pi,dark_threshold=1e-12))
    return flow,cases


def compare_models(flow: dict, cases: dict) -> tuple[dict, dict]:
    networks={name:compile_network(from_dicts(flow,params)) for name,params in cases.items()}
    axis=np.linspace(0,2*np.pi,721)
    results={}
    x,y=np.meshgrid(axis[::4],axis[::4])
    baseline={p:describe(f) for p,f in networks['ideal'].evaluate({'phi1':x,'phi2':y}).items()}
    for name,network in networks.items():
        ports={}
        for port in network.outputs:
            obs=describe(network.evaluate({'phi1':x,'phi2':y})[port])
            # Intensity has only a constant and cos(phi2) term in this restricted model.
            at_zero=describe(network.evaluate({'phi2':0})[port])['intensity']
            at_pi=describe(network.evaluate({'phi2':np.pi})[port])['intensity']
            mean=float((at_zero+at_pi)/2); cosine=float((at_zero-at_pi)/2)
            dots=np.sum(obs['stokes']*baseline[port]['stokes'],axis=-1)
            ports[port]=dict(intensity_mean=mean,intensity_cos_phi2=cosine,
                             intensity_min=mean-abs(cosine),intensity_max=mean+abs(cosine),
                             phi2_visibility=abs(cosine)/mean,
                             max_stokes_distance=float(np.max(np.linalg.norm(obs['stokes']-baseline[port]['stokes'],axis=-1))),
                             max_poincare_angle_deg=float(np.degrees(np.arccos(np.clip(dots,-1,1))).max()))
        fields=network.evaluate({'phi1':x,'phi2':y})
        total=sum(describe(f)['intensity'] for f in fields.values())
        results[name]=dict(ports=ports,max_total_power_error=float(np.max(abs(total-2))))
    return networks,results


def plots(networks: dict, output: Path) -> None:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    axis=np.linspace(0,2*np.pi,721)
    styles={'ideal':('black','--','Ideal'), 'C_reference':('#0068a8','-','Measured: C reference'),
            'D_reference':('#cc6d00',':','Measured: D reference')}
    for port in ('E','F'):
        fig,axes=plt.subplots(4,2,figsize=(10,10),sharex=True,layout='constrained')
        for col,phase in enumerate(('phi1','phi2')):
            fixed='phi2' if phase=='phi1' else 'phi1'
            for name,network in networks.items():
                obs=describe(network.evaluate({phase:axis})[port])
                color,style,label=styles[name]
                for row,data in enumerate([obs['intensity'],*obs['stokes'].T]):
                    axes[row,col].plot(np.degrees(axis),data,color=color,ls=style,label=label,lw=1.7)
                    axes[row,col].grid(alpha=.2)
            axes[0,col].set_title(f'{phase} sweep; {fixed} = {np.degrees(networks["ideal"].config.phase_values[fixed]):.0f}°')
            for row,label in enumerate(('Intensity (relative)','s1','s2','s3')):
                axes[row,col].set_ylabel(label)
            for row in range(1,4): axes[row,col].set_ylim(-1.05,1.05)
            axes[0,col].set_ylim(.985,1.015)
            axes[-1,col].set_xlabel(phase+' (degrees)')
        axes[0,0].legend(fontsize=8)
        fig.suptitle(f'Port {port}: measured splitter ratios only\nEqual input, ideal remaining optics; no fitted parameters')
        fig.savefig(output/f'comparison-{port}.pdf')
        fig.savefig(output/f'comparison-{port}.png',dpi=150)
        plt.close(fig)
    fig,axes=plt.subplots(2,2,figsize=(10,6),sharex=True,layout='constrained')
    for col,port in enumerate(('E','F')):
        for name in ('C_reference','D_reference'):
            network=networks[name]
            actual=describe(network.evaluate({'phi2':axis})[port])
            ideal=describe(networks['ideal'].evaluate({'phi2':axis})[port])
            color,style,label=styles[name]
            axes[0,col].plot(np.degrees(axis),100*(actual['intensity']-1),color=color,ls=style,label=label)
            axes[1,col].plot(np.degrees(axis),np.linalg.norm(actual['stokes']-ideal['stokes'],axis=-1),color=color,ls=style)
        axes[0,col].set_title('Port '+port)
        axes[0,col].set_ylabel('Intensity change from ideal (%)')
        axes[1,col].set_ylabel('Stokes vector distance from ideal')
        axes[1,col].set_xlabel('phi2 (degrees)')
        for ax in axes[:,col]: ax.grid(alpha=.2)
    axes[0,0].legend(fontsize=8)
    fig.suptitle('Deviation from equal-amplitude ideal model; phi1 = 0°')
    fig.savefig(output/'deviation.pdf');fig.savefig(output/'deviation.png',dpi=150)
    plt.close(fig)


def report(output: Path, powers: dict, results: dict) -> None:
    """Render the carried-coefficient derivation with data-derived substitutions."""
    flow,cases=configurations(powers)
    values=cases['C_reference']['values']
    angles=[values[key] for key in ('mu1_x','mu1_y','mu2_x','mu2_y')]
    tx,ty,ux,uy=np.cos(angles)
    rx,ry,vx,vy=np.sin(angles)
    alpha,beta,gamma,delta=tx*ux,rx*vx,ry*uy,ty*vy
    xdc,xac=alpha**2+beta**2,2*alpha*beta
    ydc,yac=gamma**2+delta**2,2*gamma*delta
    e=results['C_reference']['ports']['E']
    f=results['C_reference']['ports']['F']
    d=results['D_reference']['ports']['E']
    tokens={key:f'{value:.9f}' for key,value in dict(
        X_DC=xdc,X_AC=xac,Y_DC=ydc,Y_AC=yac,MEAN=e['intensity_mean'],
        COSINE=e['intensity_cos_phi2'],FMEAN=f['intensity_mean'],
        IMAX=e['intensity_max'],IMIN=e['intensity_min'],VE=e['phi2_visibility'],VF=f['phi2_visibility'],
        U0=alpha*gamma-beta*delta,UC=alpha*delta-beta*gamma,VS=alpha*delta+beta*gamma,
        S1DC=ydc-xdc,S1AC=yac+xac,DMEAN=d['intensity_mean'],DCOS=d['intensity_cos_phi2'],
        NEEDED=.6*e['intensity_mean']).items()}
    tokens.update(VE_PERCENT=f"{100*e['phi2_visibility']:.6f}",
                  VF_PERCENT=f"{100*f['phi2_visibility']:.6f}",
                  DVE=f"{100*d['phi2_visibility']:.6f}",PTP=f"{200*e['phi2_visibility']:.6f}",
                  RATIO=f"{.6/e['phi2_visibility']:.1f}",MAXMIN=f"{e['intensity_max']/e['intensity_min']:.6f}")
    rows=[('NPBS1, A / x','AC','AD'),('NPBS1, B / y','BD','BC'),
          ('NPBS2, C / x','ACE','ACF'),('NPBS2, C / y','BCE','BCF'),
          ('NPBS2, D / x','ADF','ADE'),('NPBS2, D / y','BDF','BDE')]
    fractions=[]
    for _,t,r in rows:
        total=powers[t]+powers[r]
        fractions.append((powers[t],powers[r],total))
    def frac(numerator,denominator):
        return rf'\frac{{{numerator:g}}}{{{denominator:g}}}'
    tokens['POWER_ROWS']='\n'.join(f'{label} & {powers[t]:g} & {powers[r]:g} & {powers[t]/(powers[t]+powers[r]):.6f} '+r'\\' for label,t,r in rows)
    coefficient_rows=[]
    expressions=[]
    for (name,(t,r,total)) in zip(('1x','1y','2x','2y'),fractions):
        expressions.append((frac(t,total),frac(r,total)))
        for symbol,power in [('t',t),('r',r)]:
            coefficient_rows.append(rf'${symbol}_{{{name}}}$ & $\sqrt{{{frac(power,total)}}}$ & {np.sqrt(power/total):.9f} '+r'\\')
    tokens['COEFFICIENT_ROWS']='\n'.join(coefficient_rows)
    txe,rxe=expressions[0];tye,rye=expressions[1]
    uxe,vxe=expressions[2];uye,vye=expressions[3]
    products=[txe+uxe,rxe+vxe,rye+uye,tye+vye]
    tokens['PRODUCT_ROWS']='\n'.join(rf'$\{name}$ & $\sqrt{{{expression}}}$ & {value:.9f} '+r'\\'
        for name,expression,value in zip(('alpha','beta','gamma','delta'),products,(alpha,beta,gamma,delta)))
    tokens.update(X_DC_EXPR=products[0]+'+'+products[1],Y_DC_EXPR=products[2]+'+'+products[3],
                  X_AC_EXPR=r'2\sqrt{'+products[0]+products[1]+'}',
                  Y_AC_EXPR=r'2\sqrt{'+products[2]+products[3]+'}',
                  DTX=frac(fractions[4][0],fractions[4][2]),DTY=frac(fractions[5][0],fractions[5][2]))
    for key,cindex,dindex in [('SX',2,4),('SY',3,5)]:
        tc=fractions[cindex][0]/fractions[cindex][2]
        td=fractions[dindex][0]/fractions[dindex][2]
        matrix=np.array([[np.sqrt(tc),1j*np.sqrt(1-td)],[1j*np.sqrt(1-tc),np.sqrt(td)]])
        tokens[key]=f'{np.linalg.svd(matrix,compute_uv=False)[0]:.6f}'
    text=(Path(__file__).parent/'templates/splitter_derivation.tex').read_text()
    for key,value in tokens.items():text=text.replace('@@'+key+'@@',value)
    if '@@' in text:raise ConfigurationError('Unresolved derivation template token')
    (output/'comparison.tex').write_text(text)
    for _ in range(2):
        process=subprocess.run(['pdflatex','-no-shell-escape','-interaction=nonstopmode','-halt-on-error','comparison.tex'],
                       cwd=output,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=60)
        if process.returncode:
            raise RuntimeError('Derivation PDF failed: '+process.stdout[-2500:])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    root=Path(__file__).resolve().parents[2]
    parser.add_argument('--campaign',type=Path,default=root/'experiments/field_propagation/measurement_campaign/2026-09-10')
    parser.add_argument('--dataset',default='direct_meter_user_2026_09_16')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    output=args.output or root/'experiments/field_propagation'/(datetime.now(timezone.utc).strftime('%Y-%m-%d_%H%M%S')+'_splitters_only')
    if output.exists() and any(output.iterdir()):parser.error('Choose a new or empty output directory')
    output.mkdir(parents=True,exist_ok=True)
    observations=read_section(args.campaign,'path_power_observations')
    if args.dataset not in observations:parser.error('Unknown measurement dataset')
    observation=observations[args.dataset]
    flow,cases=configurations(observation['powers_uw'])
    write_json(output/'flow.json',flow)
    write_json(output/'source-readings.json',observation)
    write_json(output/'source-provenance.json',dict(campaign=str(args.campaign.resolve()),dataset=args.dataset,
               campaign_sha256=hashlib.sha256((args.campaign/'campaign.json').read_bytes()).hexdigest()))
    networks,results=compare_models(flow,cases)
    for name,params in cases.items():
        file=output/(name+'.json');write_json(file,params)
        run(output/'flow.json',file,output/name)
    plots(networks,output)
    report(output,observation['powers_uw'],results)
    write_json(output/'comparison.json',results)
    (output/'README.md').write_text('# Splitter-only comparison\n\nOpen [comparison.pdf](comparison.pdf). '
        'All optics except measured splitter ratios retain the ideal baseline. '
        'C_reference and D_reference are separate, unfitted lossless completions; neither is a full measured complex matrix. '
        'Each case folder contains the shared runner\'s derivation, phase plots and numerical scans. '
        'Source readings and campaign hash are preserved.\n')
    print(json.dumps(results,indent=2));print('Comparison:',output/'comparison.pdf')


if __name__=='__main__':main()
