import os,json,time,csv,traceback,signal
from pathlib import Path
import numpy as np
from .core import initial,transition,MotorBody,readout
from local_learning.embodied_action_learning.run import SRC,sha,dump
from local_learning.material_transport import State
from local_learning.material_action_credit.core import terrain,center,largest_fraction
from local_learning.local_terminal_feedback.experiment import control
TASK='state_gated_motor_gain_20261019';OUT=SRC/'local_learning/results_20261019'/TASK
STOP=False

def prepare():
 OUT.mkdir(parents=True,exist_ok=True);assert not (OUT/'plan.json').exists()
 old=json.loads((SRC/'local_learning/results_20261019/state_conditioned_local_motor_20261019/plan.json').read_text());files=[Path(__file__),Path(__file__).with_name('core.py')]+[Path(x) for x in old['sources']]
 p=dict(task=TASK,owner='resident_d3ccfa2d95b7',seeds=[202610199311,202610199312],signs=[1,-1],arms=['source','learned_gain','fixed_direction','zero_gain'],forks=['gain_frozen'],planned_native=7040,native_cap=7680,worker_cap=120,science_wall=48,raw_cap=128*1024**2,sources={str(f):sha(f) for f in files},
 hypothesis='Can an initially zero, acquired local response gain make movement conditional on current legal outcome direction, without an accumulating additive motor bias? Strong fixed-direction and zero-gain controls isolate learning necessity and preset sensing/action.',
 policy='Previous actual paid-neighbor gradient g gives d=g/||g|| for nonzero norm, d=0 otherwise. This normalizes a sensory direction, NEVER the reward or payment. Learned mean=kappa*d; kappa0=0, clip[-2,2], update .4*(noise/.6 dot d)*source-return delta*valid. Gaussian sampling and motor tanh, costs, all16 stored channels match. Standard one-parameter conditional Gaussian score learner; source baseline uses original2D bias and same raw g/K/payment access. fixed_direction has preset kappa=1; zero_gain has0; same stochastic exploration.',
 change='Structural no-additive-bias odd readout and unit gradient direction, not an alpha/trace scan. Direction normalization and taxis axes are prewired, not learned. This extra prior can explain success; no new algorithm claim. Existing gradients were small and conditional additive term negligible; all earlier failures remain.',
 schedule='320 acquisition,320 unannounced paid relation reversal,128 unchanged relation without new payments or weight writing. Actual learned_gain320 parents gain_frozen448 additional; freeze only kappa writes, actual observations/transport/body continue. No acquisition replay.',
 information='Actual local signed gain/loss divided by fixed tariff*rho. Neighbor fields only when both were paid; no missing rewards treated negative. No field/sign/position/clock given policy. After payments stop, last actual gradient still transported and read, no new reward sensation. No hidden sensor in retention.',
 gate='Same fixed per-phase last64 body field>.25 and positive mass fraction>=.6; survive M>=12 connected>=.75. Report original-direction command and displacement/turn separately so periodic traversal cannot establish adaptation. Relative to fixed simple control, no method advantage assumed.',
 opportunity='First64 actual steps record contact/paid quantities, motion and survival, not additional trial or efficacy filter.',old_replay=0)
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
    if arm=='learned_gain' and (dest/'state_0320.npz').exists():par=dest/'state_0320.npz'
   if par:
    for arm in p['forks']:execute(i,arm,par)
 except BaseException:err=traceback.format_exc()
 r=dict(task=TASK,status='complete' if err is None else 'partial',job=job,attempted=attempts,returned=returned,unknown=attempts-returned,old_replay=0,paths=paths,error=err,wall_seconds=time.monotonic()-start,raw=str(raw),raw_bytes=sum(f.stat().st_size for f in raw.rglob('*') if f.is_file()))
 dump(OUT/'result.json',r);dump(Path(os.environ['LENIA_RUN_DIR'])/'result.json',r);print({k:v for k,v in r.items() if k!='paths'})
if __name__=='__main__':
 import sys
 (prepare if sys.argv[1]=='prepare' else main)()
