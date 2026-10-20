import os,json,time,csv,traceback,signal
from pathlib import Path
from dataclasses import asdict
import numpy as np
from .core import *
from local_learning.embodied_action_learning.run import SRC,dump,sha
from local_learning.embodied_action_learning.core import center,largest_fraction
from local_learning.local_terminal_feedback.experiment import control
TASK='scalar_response_estimation_20261019';OUT=SRC/'local_learning/results_20261019'/TASK
STOP=False

def prepare():
 OUT.mkdir(parents=True,exist_ok=True);assert not (OUT/'plan.json').exists()
 oldout=SRC/'local_learning/results_20261019/scalar_outcome_motor_learning_20261019';old=json.loads((oldout/'plan.json').read_text());r=json.loads((oldout/'result.json').read_text())
 files=[Path(__file__),Path(__file__).with_name('core.py')]+[Path(f) for f in old['sources']]
 refs=[dict(condition=x['condition'],arm=x['arm'],path=x['path'],outcome_sha=sha(Path(x['path'])/'outcome.json'),steps_sha=sha(Path(x['path'])/'steps.csv'),initial_sha=sha(Path(x['path'])/'state_0000.npz')) for x in r['paths']]
 p={k:v for k,v in old.items() if k not in ('sources','task','hypothesis','learner','baseline','planned_native','arms')}
 p.update(task=TASK,arms=['spsa'],sources={str(f):sha(f) for f in files},planned_native=1536,native_cap=2048,worker_cap=120,science_wall=48,raw_cap=64*1024**2,read_only_references=refs,
 hypothesis='Does current local scalar-response estimation help reversal relative to accumulation of a persistent policy bias? Reuse every frozen original seed/protocol and actual baseline read-only, not select successful worlds. Standard simple method may suffice; no new algorithm claim.',
 learner='Fixed beta1/8 per completed8-step action block. g <- (1-beta)g+beta*(transported Gaussian score)*(actualpaid_y minus transported block-start paid_y) on valid receipt only. Mean=unit(g), then same .8tanh(mean+.6epsilon). g and receipts follow actual material; no displacement/K/space/reward-gradient inputs. Zero initialg. A standard online random-perturbation scalar response estimate, not paired simultaneous counterfactual SPSA or exact global derivative. Beta fixed as8 completed blocks before first native; no scan.',
 baseline='All original reward_delta/context_delta/run_tumble/random frozen trajectories under exactly same initialstates/seeds/hiddencoupling/phase/payment/hold8/noisesequence/survival/readout limits retained read-only. Fixedrun_tumble also has unit mean; reward_delta permits up to2 per latent component. Current method has no larger force or additional observation. Model differs in normalizing learned direction vs integrating theta; do not attribute differences solely to statistical estimator quality.',
 evaluation='Same perphase endpoint gate and costs. Reversal negativity from old batch never revised. Verify scalar receipt binding and same first completed action from raw initialstates/receipts without native rerun.')
 dump(OUT/'plan.json',p);print({'plan_sha256':sha(OUT/'plan.json')})

def main():
 global STOP
 p=json.loads((OUT/'plan.json').read_text());assert all(sha(f)==h for f,h in p['sources'].items());assert not (OUT/'result.json').exists() and not (OUT/'pending.json').exists();dump(OUT/'launch_control.json',control())
 start=time.monotonic();job=os.environ['LENIA_JOB_ID'];raw=Path('/data/dataset/lenia-research/lineage_75e3399f4877')/TASK/job;raw.mkdir(parents=True,exist_ok=False);attempts=returned=0;paths=[];error=None
 def stop(*_):
  global STOP
  STOP=True
 signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 def execute(i,arm):
  nonlocal attempts,returned
  s,a=initial(p['seeds'][i]);rng=np.random.default_rng(p['seeds'][i]+50000);model=CoupledBody();model.coupling=rotation(p['coupling_signs'][i]*np.pi/2)
  dest=raw/f'c{i}_{arm}';dest.mkdir();rows=[];frames=[];oldc=np.array(center(s.body));disp=np.zeros(2)
  def save(t):np.savez_compressed(dest/f'state_{t:04}.npz',body=s.body,energy=s.energy,memory=s.memory,eligibility=s.eligibility,aux=a,physical_coupling=model.coupling,rng=np.asarray(json.dumps(rng.bit_generator.state)),clock=t)
  def frame(t):frames.append((t,s.body.copy(),s.energy.copy(),a[8:10].copy()))
  save(0);frame(0)
  for t in range(1,769):
   if STOP or attempts>=p['native_cap'] or time.monotonic()-start>p['science_wall']:raise RuntimeError('budget_or_stop')
   if (t-1)%HOLD==0:a,_=choose(a,arm,rng.standard_normal((2,SIZE,SIZE)),rng.standard_normal((2,SIZE,SIZE)))
   feedback=t<=640;phase='acquisition' if t<=320 else 'reversal' if t<=640 else 'retention';field=terrain(1 if t<=320 else -1)
   dump(OUT/'pending.json',dict(job=job,path=str(dest),step=t,attempt=attempts+1));attempts+=1
   s,a,m=transition(model,s,a,arm,field,feedback,feedback,t%HOLD==0);returned+=1;(OUT/'pending.json').unlink()
   c=np.array(center(s.body));disp+=(c-oldc+16)%32-16;oldc=c;mass=float(s.body.sum());conn=largest_fraction(s.body)
   rows.append(dict(t=t,phase=phase,mass=mass,energy=float(s.energy.sum()),connected=conn,field_score=float((s.body*field).sum()/mass),positive_mass=float(s.body[field>0].sum()),strong_mass=float(s.body[field>.5].sum()),dy=float(disp[0]),dx=float(disp[1]),movement=float(m['movement_paid'].sum()),unpaid=float(m['movement_unpaid'].sum()),basal=float(m['basal_paid'].sum()),death=float((m['mass_injury']+m['mass_starvation']).sum()),gain=float(m['gain'].sum()),loss=float(m['loss'].sum()),paid_mass=float(s.body[m['paid_observed']].sum()),valid_block_mass=float(s.body[m['valid_block']].sum()),written_mass=float(s.body[np.any(m['update']!=0,axis=0)].sum()),weight_norm=float((s.body*np.linalg.norm(a[:6],axis=0)).sum()/mass),balance=m['whole_energy_error']))
   if t<=16 or t in (321,328,640,641):np.savez_compressed(dest/f'receipt_{t:04}.npz',K=m['transport_weights'],motor=m['motor'],gain=m['gain'],loss=m['loss'],delta=m['delta'],update=m['update'],valid=m['valid_block'])
   if t%8==0:frame(t)
   if t in (64,320,640,768):save(t)
   if mass<12 or conn<.75:save(t);break
  with (dest/'steps.csv').open('w') as f:w=csv.DictWriter(f,fieldnames=rows[0]);w.writeheader();w.writerows(rows)
  np.savez_compressed(dest/'frames.npz',clock=np.array([f[0] for f in frames]),body=np.stack([f[1] for f in frames]),energy=np.stack([f[2] for f in frames]),motor=np.stack([f[3] for f in frames]))
  r=dict(condition=i,arm=arm,path=str(dest),steps=t,end_clock=t,alive=bool(mass>=12 and conn>=.75),phases={})
  for phase in dict.fromkeys(x['phase'] for x in rows):
   rr=[x for x in rows if x['phase']==phase];tail=rr[-64:];score=float(np.mean([x['field_score'] for x in tail]));pm=float(np.mean([x['positive_mass']/x['mass'] for x in tail]))
   r['phases'][phase]=dict(score=score,positive_fraction=pm,gate=bool(r['alive'] and len(tail)==64 and score>.25 and pm>=.6),steps=len(rr),final=rr[-1],first_feedback=next((x['t'] for x in rr if x['paid_mass']>0),None),first_valid_block=next((x['t'] for x in rr if x['valid_block_mass']>0),None),gain=sum(x['gain'] for x in rr),loss=sum(x['loss'] for x in rr),movement=sum(x['movement'] for x in rr))
  paths.append(r);dump(dest/'outcome.json',r);dump(OUT/'progress.json',dict(native=returned,paths=paths));print(json.dumps(dict(condition=i,arm=arm,steps=t,alive=r['alive'])),flush=True)
 try:
  for i in range(2):
   for arm in p['arms']:execute(i,arm)
 except BaseException:error=traceback.format_exc()
 r=dict(task=TASK,status='complete' if error is None else 'partial',job=job,attempted=attempts,returned=returned,unknown=attempts-returned,old_replay=0,paths=paths,error=error,wall_seconds=time.monotonic()-start,raw=str(raw),raw_bytes=sum(f.stat().st_size for f in raw.rglob('*') if f.is_file()))
 dump(OUT/'result.json',r);dump(Path(os.environ['LENIA_RUN_DIR'])/'result.json',r);print({k:v for k,v in r.items() if k!='paths'})
if __name__=='__main__':
 import sys
 (prepare if sys.argv[1]=='prepare' else main)()
