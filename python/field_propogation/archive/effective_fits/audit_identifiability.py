"""Historical local sensitivity audit, not a physical parameter estimator."""
from pathlib import Path
import csv,json,hashlib,sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import numpy as np
from field_propogation.configuration import read_json,from_dicts
from field_propogation.propagation import compile_network
from field_propogation.observables import describe
root=Path(__file__).resolve().parents[4];base=root/'python/field_propogation'
flow=read_json(base/'archive/effective_fits/configs/flows/hybrid_mzi_relative_fit.json')
params=read_json(base/'archive/effective_fits/configs/parameters/historical_fit.json')
source=root/'experiments/polarization_locking/2026-08-13/192530_field-model-calibration_phi2-power-model-0'
fit=read_json(source/'physical-jones-fit/physical-jones-fit.json')
rows=list(csv.DictReader((source/'data.csv').open()))
p1=np.array([float(r['phi1_rp_v']) for r in rows])*2*np.pi/(12.2/16.875)
p2=np.array([float(r['phi2_rp_v']) for r in rows])*2*np.pi/.2
conditions=np.array([r['condition'] for r in rows]);both=conditions=='both_paths'
pd=np.array([float(r['pd_mean_v']) for r in rows]);pax=np.array([float(r['pax_ptotal']) for r in rows]);s=np.array([[float(r[k]) for k in ('s1','s2','s3')] for r in rows])
labels=['pbs_leakage','mu1','mu2','log_loss_a','log_loss_b','log_loss_c','axis_c','rho_c','axis_d','rho_d','ay','phi1_offset','phi2_offset','pd_gain','pd_offset','pax_gain','pax_offset']
x=np.array([fit['pbs_leakage_rad'],fit['npbs1_mixing_rad'],fit['npbs2_mixing_rad'],np.log(fit['loss_a']),np.log(fit['loss_b']),np.log(fit['loss_c']),fit['retarder_c_axis_rad'],fit['retarder_c_retardance_rad'],fit['retarder_d_axis_rad'],fit['retarder_d_retardance_rad'],fit['input_ay_over_ax'],fit['phi1_offset_rad'],fit['phi2_offset_rad'],fit['pd_gain'],fit['pd_offset'],fit['pax_gain'],fit['pax_offset']])
def prediction(x):
 import copy
 local=copy.deepcopy(params)
 keys=['pbs_leakage_rad','npbs1_mixing_rad','npbs2_mixing_rad']
 for i,key in enumerate(keys):local['values'][key]=x[i]
 for i,a in enumerate('abc'):local['values']['loss_'+a]=np.exp(x[3+i])
 for i,key in enumerate(['axis_c','retardance_c','axis_d','retardance_d','ay','phi1_offset_rad','phi2_offset_rad'],6):local['values'][key]=x[i]
 pp=np.zeros(len(rows));px=np.zeros(len(rows));ss=np.zeros((len(rows),3))
 for cond in np.unique(conditions):
  sub=copy.deepcopy(local);mask=conditions==cond
  if cond=='path_a_only':sub['values']['ay']=0
  if cond=='path_b_only':sub['values']['ax']=0
  fields=compile_network(from_dicts(flow,sub)).evaluate({'phi1':p1[mask],'phi2':p2[mask]})
  obs={port:describe(field) for port,field in fields.items()}
  pp[mask]=x[14]+x[13]*obs[fit['pd_port']]['intensity'];px[mask]=x[16]+x[15]*obs[fit['pax_port']]['intensity'];ss[mask]=obs[fit['pax_port']]['stokes']
 return np.r_[pp/np.std(pd),px/np.std(pax),(.5*ss[both]/np.maximum(np.std(s,axis=0),.08)).ravel()]
columns=[]
for i in range(len(x)):
 h=1e-5*max(abs(x[i]),1e-4) if i>=13 else 1e-5
 a=x.copy();b=x.copy();a[i]+=h;b[i]-=h
 columns.append((prediction(a)-prediction(b))/(2*h))
jac=np.column_stack(columns);norms=np.linalg.norm(jac,axis=0);scaled=jac/norms
singular=np.linalg.svd(scaled,compute_uv=False)
cosines=scaled.T@scaled
pairs=sorted([(abs(cosines[i,j]),labels[i],labels[j],float(cosines[i,j])) for i in range(17) for j in range(i)],reverse=True)[:12]
changed=x.copy();changed[3:5]+=.1;changed[13]*=np.exp(-.2);changed[15]*=np.exp(-.2)
gauge=float(np.max(np.abs(prediction(changed)-prediction(x))))
notes={'zero':(111,111,89,116),'off_maximum':(76,87,63.5,96),'opposite':(75,59.4,54.5,56.5),'x_plus':(73.4,85.4,58,89.5),'x_minus':(79.5,60.3,65.7,62)}
cross={}
for k,(AD,AC,BD,BC) in notes.items():
 q=AC*BD/(AD*BC);cross[k]={'cross_ratio':q,'T_fraction_assuming_separability':np.sqrt(q)/(1+np.sqrt(q))}
report=dict(source=str(source.relative_to(root)/'data.csv'),source_sha256=hashlib.sha256((source/'data.csv').read_bytes()).hexdigest(),samples=len(rows),both_path_stokes_samples=int(both.sum()),parameters=labels,column_normalized_jacobian_singular_values=singular.tolist(),rank_at_relative_1e_7=int(np.sum(singular>singular[0]*1e-7)),largest_column_cosines=pairs,exact_scale_gauge_max_normalized_prediction_change=gauge,
 cross_ratios_from_user_handoff_not_original_raw_files=cross,dop={c:{'min':min(float(r['dop']) for r in rows if r['condition']==c),'max':max(float(r['dop']) for r in rows if r['condition']==c),'out_of_unit_interval':sum(not 0<=float(r['dop'])<=1 for r in rows if r['condition']==c)} for c in set(conditions)},limitations='Local numerical sensitivity diagnostic, not confidence intervals or a unique global identification. No optimizer rerun or multistart fit was performed; the exact gauge already rules out uniqueness.')
out=root/'experiments/field_propagation/historical_audit';out.mkdir(parents=True,exist_ok=True)
(out/'identifiability.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
