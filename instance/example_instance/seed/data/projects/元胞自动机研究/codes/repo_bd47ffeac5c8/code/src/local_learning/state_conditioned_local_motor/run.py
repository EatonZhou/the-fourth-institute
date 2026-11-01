import os,json,time,csv,traceback,signal
from pathlib import Path
import numpy as np
from .core import initial,transition,MotorBody,readout
from local_learning.embodied_action_learning.run import SRC,sha,dump
from local_learning.material_transport import State
from local_learning.material_action_credit.core import terrain,center,largest_fraction
from local_learning.local_terminal_feedback.experiment import control
TASK='state_conditioned_local_motor_20261019';OUT=SRC/'local_learning/results_20261019'/TASK
STOP=False

def prepare():
 OUT.mkdir(parents=True,exist_ok=True);assert not (OUT/'plan.json').exists()
 files=[Path(__file__),Path(__file__).with_name('core.py'),SRC/'local_learning/material_transport.py',SRC/'local_learning/embodied_action_learning/core.py',SRC/'local_learning/material_action_credit/core.py',SRC/'local_learning/paid_gradient_motor_control/core.py',SRC/'local_learning/lifetime_cue_reversal/core.py']
 p=dict(task=TASK,owner='resident_d3ccfa2d95b7',seeds=[202610199301,202610199302],signs=[1,-1],arms=['source','conditional','fixed'],forks=['conditional_frozen','bias_frozen'],planned_native=6400,native_cap=7168,worker_cap=120,science_wall=48,raw_cap=128*1024**2,sources={str(f):sha(f) for f in files},
 hypothesis='Does a learned locally state-dependent motor readout change direction/stop after an unannounced paid-outcome reversal, beyond a state-independent bias and a strong preset immediate paid-gradient filter? Same-parent forks separate ongoing weight writing from state readout.',
 policy='16 channels all arms; W(2x3), score EMA2, previous paid y+mask, last noise2, previous actually-paid adjacent gradient2+ever-observed2. Features=(1,g_y/2,g_x/2) from previous actual paid contact. Gaussian motor .8*tanh(clip(Wf,-2,2)+.6noise). Source baseline uses only bias. Conditional update .4*(noise/.6)*feature*source-return delta*valid*derivative of clipped mean. W projected[-2,2]. Ordinary linear conditional score learner, no algorithm novelty.',
 fixed='Same raw inputs, memory storage, exploration, physical actuator/prices; preset target clip(2g,-2,2), bias <- .5bias+.5target after actual payment. Extra one-hop communication and compute unpriced. No J/calibration or altered actuator in this batch.',
 schedule='Each new life 320 acquisition,320 hidden reward reversal,128 unchanged relation without payment/weight writing. From the same conditional actual320 parent additional conditional_frozen and bias_frozen each448. Frozen means no W update; sensory records, traces, actual K transport and body continue. Bias_frozen removes only W-g term from readout, leaves W and all transport.',
 information='No coordinate, hidden reward sign/field, target or clock supplied to policy. Physical pay computes finite local energy transfer; reward y normalized by fixed tariff*rho, never field amplitude. No payment means NO new gradient or outcome observation; last legally observed gradient is still transported and read, explicitly stale memory, not persistent current sensing.',
 gate='Survive M>=12 and connected>=.75. Each phase last64 actual body field>.25 and positive-region mass fraction>=.6. Full life all3 gates. Forks use parent acquisition result, no new acquisition replay. Report absolute good mass, delay to first new payment, new-direction movement and gate recovery separately, expenses and all failures. No need all costs improve.',
 opportunity='First64 of each actual acquisition recorded, not extra pilot: alive, actual paid mass>0 and net centroid displacement; no efficacy-based seed exclusion.',
 scope='No-birth soft material CA. Preset cohesive body, sensing, Cartesian motor axes and finite environment. No whole-body policy-gradient/unbiasedness or generalization claim from local routed reward.',old_replay=0)
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
  sign0=p['signs'][i];begin=0
  if parent:
   z=np.load(parent);s=State(*(z[k].copy() for k in ('body','energy','memory','eligibility')));a=z['aux'].copy();rng=np.random.default_rng();rng.bit_generator.state=json.loads(str(z['rng']));begin=320
  else:s,a=initial(p['seeds'][i]);rng=np.random.default_rng(p['seeds'][i]+50000)
  dest=raw/f'c{i}_{arm}';dest.mkdir();rows=[];frames=[];model=MotorBody();oldc=center(s.body);disp=0.
  def save(t):np.savez_compressed(dest/f'state_{t:04}.npz',body=s.body,energy=s.energy,memory=s.memory,eligibility=s.eligibility,aux=a,rng=np.asarray(json.dumps(rng.bit_generator.state)),clock=t)
  def frame(t):frames.append((t,s.body.copy(),s.energy.copy(),readout(a,arm)[0].copy()))
  save(begin);frame(begin)
  for t in range(begin+1,769):
   if STOP or attempts>=p['native_cap'] or time.monotonic()-start>p['science_wall']:raise RuntimeError('budget_or_stop')
   sign=sign0 if t<=320 else -sign0;feedback=t<=640;write=feedback and arm not in p['forks'];phase='acquisition' if t<=320 else 'reversal' if t<=640 else 'retention';fld=terrain(sign)
   dump(OUT/'pending.json',dict(job=job,path=str(dest),step=t,attempt=attempts+1));attempts+=1
   s,a,m=transition(model,s,a,arm,rng.standard_normal((2,32,32)),fld,feedback,write);returned+=1;(OUT/'pending.json').unlink()
   c=center(s.body);dx=float((c[1]-oldc[1]+16)%32-16);disp+=dx;oldc=c;mass=float(s.body.sum());conn=largest_fraction(s.body)
   rows.append(dict(t=t,phase=phase,mass=mass,energy=float(s.energy.sum()),connected=conn,field_score=float((s.body*fld).sum()/mass),positive_mass=float(s.body[fld>0].sum()),strong_mass=float(s.body[fld>.5].sum()),signed_dx=disp*sign0,step_dx=dx*sign,mean_x=float((s.body*m['mean'][1]).sum()/mass),movement=float(m['movement_paid'].sum()),unpaid=float(m['movement_unpaid'].sum()),basal=float(m['basal_paid'].sum()),death=float((m['mass_injury']+m['mass_starvation']).sum()),gain=float(m['gain'].sum()),loss=float(m['loss'].sum()),paid_mass=float(s.body[m['paid_observed']].sum()),gradient_observed_mass=float(s.body[np.any(m['gradient_observed'],axis=0)].sum()),balance=m['whole_energy_error']))
   if t-begin<=8 or t in (321,322,640,641):np.savez_compressed(dest/f'receipt_{t:04}.npz',K=m['transport_weights'],motor=m['motor'],mean=m['mean'],features=m['policy_features'],gain=m['gain'],loss=m['loss'],delta=m['delta'],update=m['parameter_update'])
   if t%8==0:frame(t)
   if t in (64,320,640,768):save(t)
   if mass<12 or conn<.75:save(t);break
  with (dest/'steps.csv').open('w') as f:w=csv.DictWriter(f,fieldnames=rows[0]);w.writeheader();w.writerows(rows)
  np.savez_compressed(dest/'frames.npz',clock=np.array([f[0] for f in frames]),body=np.stack([f[1] for f in frames]),energy=np.stack([f[2] for f in frames]),mean=np.stack([f[3] for f in frames]))
  r=dict(condition=i,arm=arm,path=str(dest),initial_sign=sign0,start_clock=begin,end_clock=t,steps=t-begin,parent=str(parent) if parent else None,alive=bool(mass>=12 and conn>=.75),phases={})
  for phase in dict.fromkeys(x['phase'] for x in rows):
   rr=[x for x in rows if x['phase']==phase];tail=rr[-64:];score=float(np.mean([x['field_score'] for x in tail]));pm=float(np.mean([x['positive_mass']/x['mass'] for x in tail]))
   r['phases'][phase]=dict(score=score,positive_fraction=pm,gate=bool(r['alive'] and len(tail)==64 and score>.25 and pm>=.6),first_feedback=next((x['t'] for x in rr if x['paid_mass']>0),None),final=rr[-1],gain=sum(x['gain'] for x in rr),loss=sum(x['loss'] for x in rr),movement=sum(x['movement'] for x in rr))
  r['all_observed_phases_gate']=all(v['gate'] for v in r['phases'].values());paths.append(r);dump(dest/'outcome.json',r);dump(OUT/'progress.json',dict(native=returned,paths=paths));print(json.dumps({k:r[k] for k in ('condition','arm','steps','all_observed_phases_gate')}),flush=True)
  return dest
 try:
  for i in range(2):
   par=None
   for arm in p['arms']:
    dest=execute(i,arm)
    if arm=='conditional' and (dest/'state_0320.npz').exists():par=dest/'state_0320.npz'
   if par:
    for arm in p['forks']:execute(i,arm,par)
 except BaseException:err=traceback.format_exc()
 r=dict(task=TASK,status='complete' if err is None else 'partial',job=job,attempted=attempts,returned=returned,unknown=attempts-returned,old_replay=0,paths=paths,error=err,wall_seconds=time.monotonic()-start,raw=str(raw),raw_bytes=sum(f.stat().st_size for f in raw.rglob('*') if f.is_file()))
 dump(OUT/'result.json',r);dump(Path(os.environ['LENIA_RUN_DIR'])/'result.json',r);print({k:v for k,v in r.items() if k!='paths'})
if __name__=='__main__':
 import sys
 (prepare if sys.argv[1]=='prepare' else main)()
