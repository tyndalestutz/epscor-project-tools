#!/usr/bin/env python3
"""Run a reusable optical flow with a parameter file and write a derived PDF report.

Example (from repository root):
  python python/field_propogation/run_simulation.py \
    --flow python/field_propogation/configs/flows/hybrid_mzi.json \
    --params python/field_propogation/configs/parameters/ideal.json
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = 'field_propogation'

from .analysis import phase_scans, summarize
from .configuration import ConfigurationError, load_configuration, real
from .observables import state_label
from .propagation import compile_network
from .reporting import write_plots, write_report
from .symbolic_intensity import symbolic_intensity_data
from .provenance import interpretation


def serializable(value):
    """Standards-compliant JSON: undefined values are null, complex numbers pairs."""
    if isinstance(value, dict):
        return {str(k): serializable(v) for k,v in value.items()}
    if isinstance(value, (list, tuple)):
        return [serializable(v) for v in value]
    if isinstance(value, np.ndarray):
        return serializable(value.tolist())
    if isinstance(value, complex):
        return {'real': value.real, 'imag': value.imag}
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(serializable(value), indent=2, allow_nan=False)+'\n')


def write_scan_csv(scans: dict, output: Path) -> None:
    with (output/'phase-cuts.csv').open('w',newline='') as handle:
        writer=csv.writer(handle)
        writer.writerow(['phase','phase_rad','port','intensity','s1','s2','s3','theta_rad','eta_rad','u_rad','v_rad','dI_dphase','stokes_speed'])
        for phase,ports in scans['cuts'].items():
            for port,obs in ports.items():
                for i,value in enumerate(scans['axis']):
                    row=[phase,value,port,obs['intensity'][i],*obs['stokes'][i],
                         *[obs[k][i] for k in ('theta_rad','eta_rad','u_rad','v_rad','intensity_derivative','stokes_speed')]]
                    writer.writerow(['' if isinstance(x,(float,np.floating)) and not np.isfinite(x) else x for x in row])
    with (output/'phase-maps.csv').open('w',newline='') as handle:
        writer=csv.writer(handle)
        writer.writerow(['phase_x','phase_y','phase_x_rad','phase_y_rad','port','intensity','s1','s2','s3'])
        for phases,ports in scans['maps'].items():
            for port,obs in ports.items():
                for iy,y in enumerate(scans['axis']):
                    for ix,x in enumerate(scans['axis']):
                        row=[*phases,x,y,port,obs['intensity'][iy,ix],*obs['stokes'][iy,ix]]
                        writer.writerow(['' if isinstance(v,(float,np.floating)) and not np.isfinite(v) else v for v in row])


def run(flow_path: Path, parameter_path: Path, output: Path, *, pdf: bool = True,
        phase_overrides: dict[str,float] | None = None) -> dict:
    config=load_configuration(flow_path,parameter_path)
    if phase_overrides:
        unknown=set(phase_overrides)-set(config.phase_values)
        if unknown:
            raise ConfigurationError(f'Unknown phase overrides: {sorted(unknown)}')
        config.phase_values.update(phase_overrides)
    network=compile_network(config)
    if pdf and not shutil.which('pdflatex'):
        raise ConfigurationError('PDF output needs pdflatex. Install a LaTeX distribution or use --no-pdf to write LaTeX and numerical results.')
    if output.exists() and any(output.iterdir()):
        raise ConfigurationError(f'Output folder is not empty: {output}. Choose a new folder to preserve previous runs.')
    output.mkdir(parents=True,exist_ok=True)
    output=output.resolve()
    print(f'Compiled {len(network.steps)} elements; outputs: {", ".join(network.outputs)}',flush=True)
    scans=phase_scans(network)
    summary=summarize(network,scans)
    provenance={
        'flow file': flow_path.name,
        'flow SHA256': hashlib.sha256(flow_path.read_bytes()).hexdigest(),
        'parameter file': parameter_path.name,
        'parameter SHA256': hashlib.sha256(parameter_path.read_bytes()).hexdigest(),
        'UTC creation time': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'NumPy version': np.__version__,
    }
    # Hash the actual implementation so results remain auditable after edits.
    source_hash=hashlib.sha256()
    for path in sorted([*Path(__file__).parent.glob('*.py'),
                        *(Path(__file__).parent / 'templates').glob('*.tex')]):
        source_hash.update(path.name.encode());source_hash.update(path.read_bytes())
    provenance['simulation source SHA256']=source_hash.hexdigest()
    summary['provenance']=provenance
    summary['interpretation'] = interpretation(config.parameters)
    with (output/'parameter-provenance.csv').open('w', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['parameter','value','units','quantity','plane','sources','status','uncertainty','notes'])
        for name, value in config.parameters['values'].items():
            meta = config.parameters.get('provenance', {}).get(name, {})
            writer.writerow([name,value,meta.get('units','unspecified'),meta.get('quantity','unverified'),
                             meta.get('plane','unspecified'),';'.join(meta.get('sources',[])),meta.get('status','unverified'),
                             meta.get('uncertainty'),meta.get('notes','No independent provenance supplied')])
    summary['phase_values']=config.phase_values
    write_json(output/'summary.json',summary)
    write_json(output/'flow.json',config.flow)
    resolved_parameters=dict(config.parameters,phase_values=config.phase_values,scan=config.scan)
    write_json(output/'parameters.json',resolved_parameters)
    harmonics={port:{'field_modes':[dict(mode=list(mode),amplitude=a) for mode,a in network.fields[port].items()],
                     'stokes_modes':[dict(mode=list(mode),coefficients=c) for mode,c in network.stokes_harmonics(port).items()]}
               for port in network.outputs}
    write_json(output/'harmonics.json',harmonics)
    amplitude_data = symbolic_intensity_data(network)
    write_json(output/'symbolic-intensity.json', amplitude_data)
    write_scan_csv(scans,output)
    os.environ.setdefault('MPLCONFIGDIR','/tmp/matplotlib-field-propagation')
    os.environ.setdefault('XDG_CACHE_HOME','/tmp/field-propagation-cache')
    figures=write_plots(network,scans,output)
    tex=write_report(network,summary,figures,provenance,output,amplitude_data=amplitude_data)
    if pdf:
        print('Typesetting the derived report...',flush=True)
        for _ in range(2):
            process=subprocess.run(['pdflatex','-no-shell-escape','-interaction=nonstopmode','-halt-on-error',tex.name],
                                   cwd=output,capture_output=True,text=True,timeout=60)
            if process.returncode:
                raise RuntimeError(f'LaTeX compilation failed; inspect {output / "derivation.log"}\n'+process.stdout[-2000:])
        # Keep the compilation log for audit, remove auxiliary navigation files.
        for suffix in ('.aux','.out'):
            tex.with_suffix(suffix).unlink(missing_ok=True)
    (output/'README.md').write_text('# Simulation output\n\n'
        + ('Open [derivation.pdf](derivation.pdf) for the complete derivation and phase plots.\n' if pdf else 'Compile derivation.tex with pdflatex to obtain the article.\n')
        + '\n- `flow.json` and `parameters.json`: rerunnable input snapshots.\n'
        + '- `summary.json`: operating states, sampled sensitivities, and provenance.\n'
        + '- `harmonics.json`: full-precision complex field and raw-Stokes Fourier coefficients.\n'
        + '- `symbolic-intensity.json`: transfer and intensity coefficients with independent input amplitudes.\n'
        + '- `phase-cuts.csv` and `phase-maps.csv`: numerical scans; blank values are undefined.\n'
        + '- `derivation.tex` and plot PDFs: editable, standalone report sources.\n')
    for port,obs in summary['operating_point'].items():
        print(f'{port}: I={float(obs["intensity"]):.6g}; {state_label(obs)}; s={np.array2string(obs["stokes"],precision=5)}')
    if network.unused_parameters:
        print('Unused parameters: '+', '.join(network.unused_parameters))
    print(f'Report: {output / ("derivation.pdf" if pdf else "derivation.tex")}')
    return summary


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    defaults=Path(__file__).resolve().parent/'configs'
    parser.add_argument('--flow',type=Path,default=defaults/'flows/hybrid_mzi.json',help='Optical-flow JSON (default: hybrid MZI).')
    parser.add_argument('--params',type=Path,default=defaults/'parameters/ideal.json',help='Parameter JSON (default: ideal equal input).')
    parser.add_argument('--output',type=Path,help='New or empty output folder; default: dated experiments/field_propagation run.')
    parser.add_argument('--phase',action='append',default=[],metavar='NAME=RADIANS',help='Override an operating phase, e.g. --phase phi1=pi/2. Repeat for multiple phases.')
    parser.add_argument('--no-pdf',action='store_true',help='Write LaTeX, plots and numerical results without invoking pdflatex.')
    args=parser.parse_args()
    output=args.output or Path(__file__).resolve().parents[2]/'experiments/field_propagation'/(
        datetime.now(timezone.utc).strftime('%Y-%m-%d_%H%M%S_%f')+'_'+args.params.stem)
    try:
        overrides={}
        for item in args.phase:
            if '=' not in item:
                raise ConfigurationError('--phase expects NAME=RADIANS')
            name,value=item.split('=',1)
            if name in overrides:
                raise ConfigurationError(f'Duplicate --phase override: {name}')
            overrides[name]=real(value)
        run(args.flow,args.params,output,pdf=not args.no_pdf,phase_overrides=overrides)
    except (ConfigurationError,OSError,RuntimeError,subprocess.TimeoutExpired) as exc:
        parser.exit(2,f'Error: {exc}\n')


if __name__ == '__main__':
    main()
