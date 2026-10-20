import os,json,time,csv,traceback,signal
from pathlib import Path
from dataclasses import asdict
import numpy as np
from local_learning.scalar_response_estimation.core import *
from local_learning.material_transport import State
from local_learning.embodied_action_learning.run import SRC,dump,sha
from local_learning.embodied_action_learning.core import center,largest_fraction
from local_learning.local_terminal_feedback.experiment import control
TASK='scalar_actuator_relearning_20261019';OUT=SRC/'local_learning/results_20261019'/TASK
STOP=False

def prepare():
 OUT.mkdir(parents=True,exist_ok=True);assert not (OUT/'plan.json').exists()
 oldout=SRC/'local_learning/results_20261019/scalar_response_estimation_20261019';old=json.loads((oldout/'plan.json').read_text());r=json.loads((oldout/'result.json').read_text())
 files=[Path(__file__)]+[Path(f) for f in old['sources']]
 parents={str(x['condition']):dict(path=str(Path(x['path'])/'state_0320.npz'),sha256=sha(Path(x['path'])/'state_0320.npz')) for x in r['paths']}
 p=dict(task=TASK,owner='resident_d3ccfa2d95b7',seeds=old['seeds'],coupling_signs=old['coupling_signs'],arms=['continue','frozen','run_tumble'],parents=parents,planned_native=2688,native_cap=3072,worker_cap=120,cpu=2,gpu=0,science_wall=48,raw_cap=64*1024**2,sources={str(f):sha(f) for f in files},
 hypothesis='Can scalar-only response learning adapt to a changed hidden actuator while reward meaning stays fixed? Separate actuator adaptation from the preceding reward reversal. Fixed scalar run/tumble receives the same acquired direction at the same actual parent as a strong simple adaptation control.',
 schedule='Two actualspsa320 parents, allbody/E/sensory/RNG/g identical; original+/-90 physicalmap changes at321 toopposite45, terrain sign+ unchanged. 320paid future+128no newpayments/writes. continue updatesg; frozen onlystops gupdate; runtumble initializes heading=unit(g), uses same pastscalarblockdelta and exactsame Gaussianinnovation/heading randomness, no map/coordinates/clock flag. Allfullstate branches ready320, no oldtail replay. This initialization gives fixedcontroller acquired directional prior, deliberately strong; not independent life0 learning.',
 comparisons='Same gates/costs/no-selected-parent as before. Firstgood cannot be read as relearning because acquiredparents already useful. Fixedsimplecontrol gets sameinput/actions but uses feedback through fixedrule not gupdate. This tests adaptive availability of scalar consequences, not a newalgorithm.',
 policy=old['learner'],information=old['information'],gate=old['gate'],boundaries=old['boundaries'],old_replay=0)
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
  par=p['parents'][str(i)];assert sha(par['path'])==par['sha256'];z=np.load(par['path']);s=State(*(z[k].copy() for k in ('body','energy','memory','eligibility')));a=z['aux'].copy();rng=np.random.default_rng();rng.bit_generator.state=json.loads(str(z['rng']));model=CoupledBody();model.coupling=z['physical_coupling'].copy()
  if arm=='run_tumble':a[6:8]=unit(a[:2])
  learner='run_tumble' if arm=='run_tumble' else 'spsa'
  dest=raw/f'c{i}_{arm}';dest.mkdir();rows=[];frames=[];oldc=np.array(center(s.body));disp=np.zeros(2)
  def save(t):np.savez_compressed(dest/f'state_{t:04}.npz',body=s.body,energy=s.energy,memory=s.memory,eligibility=s.eligibility,aux=a,physical_coupling=model.coupling,rng=np.asarray(json.dumps(rng.bit_generator.state)),clock=t)
  def frame(t):frames.append((t,s.body.copy(),s.energy.copy(),a[8:10].copy()))
  save(320);frame(320)
  model.coupling=rotation(-p['coupling_signs'][i]*np.pi/4)
  for t in range(321,769):
   if STOP or attempts>=p['native_cap'] or time.monotonic()-start>p['science_wall']:raise RuntimeError('budget_or_stop')
   if (t-1)%HOLD==0:a,_=choose(a,learner,rng.standard_normal((2,SIZE,SIZE)),rng.standard_normal((2,SIZE,SIZE)))
   feedback=t<=640;phase='acquisition' if t<=320 else 'reversal' if t<=640 else 'retention';field=terrain(1)
   dump(OUT/'pending.json',dict(job=job,path=str(dest),step=t,attempt=attempts+1));attempts+=1
   s,a,m=transition(model,s,a,learner,field,feedback,feedback and arm=='continue',t%HOLD==0);returned+=1;(OUT/'pending.json').unlink()
   c=np.array(center(s.body));disp+=(c-oldc+16)%32-16;oldc=c;mass=float(s.body.sum());conn=largest_fraction(s.body)
   rows.append(dict(t=t,phase=phase,mass=mass,energy=float(s.energy.sum()),connected=conn,field_score=float((s.body*field).sum()/mass),positive_mass=float(s.body[field>0].sum()),strong_mass=float(s.body[field>.5].sum()),dy=float(disp[0]),dx=float(disp[1]),movement=float(m['movement_paid'].sum()),unpaid=float(m['movement_unpaid'].sum()),basal=float(m['basal_paid'].sum()),death=float((m['mass_injury']+m['mass_starvation']).sum()),gain=float(m['gain'].sum()),loss=float(m['loss'].sum()),paid_mass=float(s.body[m['paid_observed']].sum()),valid_block_mass=float(s.body[m['valid_block']].sum()),written_mass=float(s.body[np.any(m['update']!=0,axis=0)].sum()),weight_norm=float((s.body*np.linalg.norm(a[:6],axis=0)).sum()/mass),balance=m['whole_energy_error']))
   if t<=336 or t in (640,641,768):np.savez_compressed(dest/f'receipt_{t:04}.npz',K=m['transport_weights'],motor=m['motor'],gain=m['gain'],loss=m['loss'],delta=m['delta'],update=m['update'],valid=m['valid_block'])
   if t%8==0:frame(t)
   if t in (64,320,640,768):save(t)
   if mass<12 or conn<.75:save(t);break
  with (dest/'steps.csv').open('w') as f:w=csv.DictWriter(f,fieldnames=rows[0]);w.writeheader();w.writerows(rows)
  np.savez_compressed(dest/'frames.npz',clock=np.array([f[0] for f in frames]),body=np.stack([f[1] for f in frames]),energy=np.stack([f[2] for f in frames]),motor=np.stack([f[3] for f in frames]))
  r=dict(condition=i,arm=arm,path=str(dest),steps=t-320,end_clock=t,start_clock=320,reward_schedule='unchanged',alive=bool(mass>=12 and conn>=.75),phases={})
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
