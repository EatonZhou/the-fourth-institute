import os,json,time,csv,traceback,signal
from pathlib import Path
import numpy as np
from .core import *
from local_learning.material_coupling_transport.core import MaterialCoupledBody
from local_learning.embodied_action_learning.run import SRC,sha,dump
from local_learning.material_transport import State
from local_learning.embodied_action_learning.core import center,largest_fraction
from local_learning.local_terminal_feedback.experiment import control
TASK='affine_motor_baseline_20261019';OUT=SRC/'local_learning/results_20261019'/TASK
STOP=False

def prepare():
 OUT.mkdir(parents=True,exist_ok=True);assert not (OUT/'plan.json').exists()
 old=json.loads((SRC/'local_learning/results_20261019/material_coupling_transport_20261019/plan.json').read_text())
 files=[Path(__file__),Path(__file__).with_name('core.py')]+[Path(x) for x in old['sources']]
 p=dict(task=TASK,owner='resident_d3ccfa2d95b7',seeds=[202610199421,202610199422,202610199421,202610199422],signs=[1,-1,1,-1],physical_types=['global','global','material','material'],arms=['iv','nlms'],forks=[],planned_native=6144,native_cap=8192,worker_cap=240,cpu=2,gpu=0,science_wall=95,raw_cap=128*1024**2,sources={str(f):sha(f) for f in files},
 hypothesis='Can a strong ordinary online affine normalized-LMS model, carrying actual source statistics with exactly the same material K, reproduce IV behavior and recovery/cost? Only a justified advantage beyond this core could support added representation. Spatial storage baseline remains a separate prior transport question.',
 estimator='NLMS: vhat=J*u+b; e=v-vhat; [J,b] <- [J,b]+0.5*e*[u_y,u_x,1]/(1+u.u). Only source with rho>=1e-4 and completed paid motion updates. Zero initialization; intercept accommodates passive/local drift. Same completed registered u/v sample and same iid Gaussian motor innovation as IV. alpha .5 fixed before result, no scan. IV unchanged beta1/32, independent-innovation moment ratio. Neither is claimed novel, unbiased global RL or an exact plant Jacobian under arbitrary nonlinear drift.',
 readout='Both use the same damped J inverse applied to last paid local gradient then unit normalization and .8tanh(mean+.6epsilon). Fitted intercept is a nuisance in forward fit, not an extra goal/force controller; directional Jacobian readout identical. Same 16 transported channels; NLMS6 active fitted coefficients+one validity accumulator, IV13 empirical statistics; unequal effective storage/compute disclosed despite same physical payload. No label/map/position/clock input.',
 physical='Global: initial unknown +/-90deg, at321 opposite45deg plus reward reversal. Material: rotation field intrinsic to material, follows actualrhoK; at321 reward-only reversal. No old worlds replayed; 2 fresh seeds x 2 host schedules, same seed/opposite meaning paired by condition. Every arm starts from matching life0 body/E/RNG. The two maps per seed are related model contexts, not independent worlds.',
 schedule='320acquisition+320change+128same relation no external payments/new outcome sensing/estimator writing. Existing scalar gates unchanged. First64 actual actions are opportunity check; no postselection. Record failed/disconnected/dead and unavailable feedback, no revival.',
 coverage='Per path and phase, mass-weighted source command and independent innovation first/second moments, covariance eigenvalues, valid-source mass-time and steps. These diagnose two-direction excitation, not cell-level independent samples or p values. No eventual state/phase clock reaches learner.',
 policy=old['policy'],information=old['information'],gate=old['gate'],boundaries=old['boundaries'],old_replay=0)
 dump(OUT/'plan.json',p);print({'plan_sha256':sha(OUT/'plan.json')})

def main():
 global STOP
 p=json.loads((OUT/'plan.json').read_text());assert all(sha(f)==h for f,h in p['sources'].items());assert not (OUT/'result.json').exists() and not (OUT/'pending.json').exists();dump(OUT/'launch_control.json',control())
 start=time.monotonic();job=os.environ['LENIA_JOB_ID'];raw=Path('/data/dataset/lenia-research/lineage_75e3399f4877')/TASK/job;raw.mkdir(parents=True,exist_ok=False);attempts=returned=0;paths=[];err=None
 def stop(*_):
  global STOP
  STOP=True
 signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 def execute(i,arm,parent=None):
  nonlocal attempts,returned
  sg=p['signs'][i];begin=0
  if parent:
   z=np.load(parent);s=State(*(z[k].copy() for k in ('body','energy','memory','eligibility')));a=z['aux'].copy();rng=np.random.default_rng();rng.bit_generator.state=json.loads(str(z['rng']));begin=320
   if arm=='erase_frozen':a[:13]=0
  else:s,a=initial(p['seeds'][i]);rng=np.random.default_rng(p['seeds'][i]+50000)
  dest=raw/f'c{i}_{arm}';dest.mkdir();rows=[];frames=[];model=MaterialCoupledBody(sg) if p['physical_types'][i]=='material' else CoupledBody();coverage={};oldc=np.array(center(s.body));disp=np.zeros(2)
  def save(t):np.savez_compressed(dest/f'state_{t:04}.npz',body=s.body,energy=s.energy,memory=s.memory,eligibility=s.eligibility,aux=a,physical_coupling=model.coupling,rng=np.asarray(json.dumps(rng.bit_generator.state)),clock=t)
  def frame(t):frames.append((t,s.body.copy(),s.energy.copy(),readout(a,arm)[0].copy()))
  
  if p['physical_types'][i]=='global':model.coupling=rotation(sg*np.pi/2)
  save(begin);frame(begin)
  for t in range(begin+1,769):
   if STOP or attempts>=p['native_cap'] or time.monotonic()-start>p['science_wall']:raise RuntimeError('budget_or_stop')
   relation=sg if t<=320 else -sg;feedback=t<=640;write=feedback and arm not in p['forks'];phase='acquisition' if t<=320 else 'change' if t<=640 else 'retention';fld=terrain(relation)
   if p['physical_types'][i]=='global':model.coupling=rotation(sg*np.pi/2 if t<=320 else -sg*np.pi/4)
   dump(OUT/'pending.json',dict(job=job,path=str(dest),step=t,attempt=attempts+1));attempts+=1
   before_rho=s.body.copy();noise=rng.standard_normal((2,32,32));s,a,m=transition_with_material(model,s,a,arm,noise,fld,feedback,write);returned+=1;(OUT/'pending.json').unlink()
   acc=coverage.setdefault(phase,dict(mass_time=0.,u=np.zeros(2),uu=np.zeros((2,2)),eps=np.zeros(2),epseps=np.zeros((2,2)),completed_steps=0))
   ww=before_rho*m['map_sample'];acc['mass_time']+=float(ww.sum());acc['completed_steps']+=bool(ww.sum()>0)
   for key,fieldarr in (('u',m['motor']),('eps',noise)):
    acc[key]+=(fieldarr*ww).sum((1,2));acc['uu' if key=='u' else 'epseps']+=np.einsum('iyx,jyx,yx->ij',fieldarr,fieldarr,ww)
   c=np.array(center(s.body));delta=(c-oldc+16)%32-16;disp+=delta;oldc=c;mass=float(s.body.sum());conn=largest_fraction(s.body)
   d=unit(a[13:15]);v=m['velocity'];align=float((before_rho*np.sum(d*v,axis=0)).sum()/mass)
   rows.append(dict(t=t,phase=phase,mass=mass,energy=float(s.energy.sum()),connected=conn,field_score=float((s.body*fld).sum()/mass),positive_mass=float(s.body[fld>0].sum()),strong_mass=float(s.body[fld>.5].sum()),dy=float(disp[0]),dx=float(disp[1]),mean_y=float((before_rho*m['mean'][0]).sum()/mass),mean_x=float((before_rho*m['mean'][1]).sum()/mass),response_alignment=align,movement=float(m['movement_paid'].sum()),unpaid=float(m['movement_unpaid'].sum()),basal=float(m['basal_paid'].sum()),death=float((m['mass_injury']+m['mass_starvation']).sum()),gain=float(m['gain'].sum()),loss=float(m['loss'].sum()),paid_mass=float(s.body[m['paid_observed']].sum()),map_sample_mass=float(before_rho[m['map_sample']].sum()),map_writes=bool(write and arm!='reflex'),gradient_observed_mass=float(s.body[np.any(m['gradient_observed'],axis=0)].sum()),balance=m['whole_energy_error']))
   if t-begin<=8 or t in (321,322,640,641):np.savez_compressed(dest/f'receipt_{t:04}.npz',K=m['transport_weights'],motor=m['motor'],mean=m['mean'],J=m['J'],velocity=m['velocity'],gain=m['gain'],loss=m['loss'],physical_coupling=m['physical_coupling_before'])
   if t%8==0:frame(t)
   if t in (64,320,640,768):save(t)
   if mass<12 or conn<.75:save(t);break
  with (dest/'steps.csv').open('w') as f:w=csv.DictWriter(f,fieldnames=rows[0]);w.writeheader();w.writerows(rows)
  np.savez_compressed(dest/'frames.npz',clock=np.array([f[0] for f in frames]),body=np.stack([f[1] for f in frames]),energy=np.stack([f[2] for f in frames]),mean=np.stack([f[3] for f in frames]))
  r=dict(condition=i,arm=arm,physical_type=p['physical_types'][i],actuator_schedule='material_transport' if p['physical_types'][i]=='material' else 'global_change',path=str(dest),initial_sign=sg,start_clock=begin,end_clock=t,steps=t-begin,parent=str(parent) if parent else None,alive=bool(mass>=12 and conn>=.75),phases={})
  for phase in dict.fromkeys(x['phase'] for x in rows):
   rr=[x for x in rows if x['phase']==phase];tail=rr[-64:];score=float(np.mean([x['field_score'] for x in tail]));pm=float(np.mean([x['positive_mass']/x['mass'] for x in tail]))
   r['phases'][phase]=dict(score=score,positive_fraction=pm,gate=bool(r['alive'] and len(tail)==64 and score>.25 and pm>=.6),first_feedback=next((x['t'] for x in rr if x['paid_mass']>0),None),first_map_sample=next((x['t'] for x in rr if x['map_sample_mass']>0),None),final=rr[-1],gain=sum(x['gain'] for x in rr),loss=sum(x['loss'] for x in rr),movement=sum(x['movement'] for x in rr))
  cv={}
  for phase,acc in coverage.items():
   w=acc['mass_time'];item=dict(mass_time=w,completed_steps=acc['completed_steps'])
   for key,second in (('u','uu'),('eps','epseps')):
    cov=acc[second]/max(w,1e-30)-np.outer(acc[key],acc[key])/max(w*w,1e-30)
    item[key+'_covariance']=cov.tolist();item[key+'_eigenvalues']=np.linalg.eigvalsh(cov).tolist()
   cv[phase]=item
  dump(dest/'action_coverage.json',cv)
  r['all_observed_phases_gate']=all(v['gate'] for v in r['phases'].values());paths.append(r);dump(dest/'outcome.json',r);dump(OUT/'progress.json',dict(native=returned,paths=paths));print(json.dumps({k:r[k] for k in ('condition','arm','steps','all_observed_phases_gate')}),flush=True)
  return dest
 try:
  for i in range(4):
   par=None
   for arm in p['arms']:
    dest=execute(i,arm)
    if arm=='carried' and (dest/'state_0320.npz').exists():par=dest/'state_0320.npz'
   if par:
    for arm in p['forks']:execute(i,arm,par)
 except BaseException:err=traceback.format_exc()
 r=dict(task=TASK,status='complete' if err is None else 'partial',job=job,attempted=attempts,returned=returned,unknown=attempts-returned,old_replay=0,paths=paths,error=err,wall_seconds=time.monotonic()-start,raw=str(raw),raw_bytes=sum(f.stat().st_size for f in raw.rglob('*') if f.is_file()))
 dump(OUT/'result.json',r);dump(Path(os.environ['LENIA_RUN_DIR'])/'result.json',r);print({k:v for k,v in r.items() if k!='paths'})
if __name__=='__main__':
 import sys
 (prepare if sys.argv[1]=='prepare' else main)()
