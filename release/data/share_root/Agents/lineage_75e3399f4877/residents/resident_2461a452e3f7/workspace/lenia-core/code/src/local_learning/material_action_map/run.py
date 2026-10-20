import os,json,time,csv,traceback,signal
from pathlib import Path
import numpy as np
from .core import *
from local_learning.embodied_action_learning.run import SRC,sha,dump
from local_learning.material_transport import State
from local_learning.embodied_action_learning.core import center,largest_fraction
from local_learning.local_terminal_feedback.experiment import control
TASK='material_action_map_20261019';OUT=SRC/'local_learning/results_20261019'/TASK
STOP=False

def prepare():
 OUT.mkdir(parents=True,exist_ok=True);assert not (OUT/'plan.json').exists()
 old=json.loads((SRC/'local_learning/results_20261019/state_gated_motor_gain_20261019/plan.json').read_text())
 files=[Path(__file__),Path(__file__).with_name('core.py')]+[Path(x) for x in old['sources']]
 # Pure algebra only; no Actor, no K generation, no synthetic behavioral result.
 for angle in (np.pi/2,-np.pi/4):
  A=rotation(angle);assert np.allclose(A.T@A,np.eye(2),atol=1e-14)
 a=np.zeros((16,32,32));assert np.max(np.abs(estimate(a)))==0
 p=dict(task=TASK,owner='resident_d3ccfa2d95b7',seeds=[202610199401,202610199402],signs=[1,-1],arms=['carried','euler','reflex'],forks=['frozen','erase_frozen'],planned_native=6400,native_cap=8192,worker_cap=240,cpu=2,gpu=0,science_wall=95,raw_cap=128*1024**2,sources={str(f):sha(f) for f in files},
 hypothesis='Can a local full 2x2 action-consequence estimate make a deforming body act usefully through unknown cross-coupled actuators and recalibrate after change? Does actual-material transport outperform the identical Euler estimator? No scalar gain or preset correct inverse.',
 estimator='Ordinary online instrumental-variable local response estimate. a0:2 EMA command,2:4 EMA donor K displacement,4:8 EMA[(command-old_command_mean)*epsilon^T],8:12 EMA[(velocity-old_velocity_mean)*epsilon^T],12 EMA valid weight. Beta1/32; J=H B^T (B B^T+1e-4I)^-1. Innovation epsilon iid standard normal independent of old state. Baselines predating epsilon. Records finite-window effective response, not exact/unbiased global gradient or exact true actuator map. Passive flow remains in outcome; IV separates it in expectation, not exact finite sample.',
 policy='Actual last paid one-hop local gradient normalized d, command mean=unit((J^T J+1e-4I)^-1 J^T d), then .8*tanh(mean+.6epsilon). Zero J gives zero mean. Fixed reflex mean=d. All arms have same proprioception, local paid sensing, motor/noise limits and native charges. Euler same IV statistics remain on grid; carried statistics transported with actual rho*K. No global pooling.',
 schedule='Each life320 acquisition,320 unannounced coupled change,128 unchanged/no payment/no map write. Initial physical actuator rotation sign*pi/2; after320 rotation -sign*pi/4. Reward terrain sign*(.6sin(x)+.4sin(y)), sign reverses after320. Both change together: initial batch does not separately attribute actuator and reward adaptation. Policy receives neither angle/sign/position/clock nor field. First64 retained as opportunity diagnostic; no outcome filtering. Same carried320 parent frozen/erase_frozen448; freeze only map moments; erase first13channels only, preserve body/E/sensory/RNG, all future transport/action/sensing retained.',
 information='Actual source motor command and completed donor K displacement with paid motion; no denied action interpreted as observation. Actual finite substrate paid outcome drives neighboring gradient. Entire finite torus support; no tail normalization. Retention removes outcome payments and new outcome sensing, carries last gradient; K still recorded but map writes frozen.',
 gate='Survive M>=12 connected>=.75; each phase last64 mean body field>.25 and positive occupied mass fraction>=.6. Also absolute masses, 2D unwrapped displacement, feedback wait and command-response alignment, learning sample counts. Map changes unobservable before actual new response; first actual postchange map sample reported, not demand prediction. No averaging away a failed life.',
 boundaries='No birth; cohesive deformable material host, fixed local axes and desirable-gradient taxis objective preinstalled. Sensing of donor flow, one-hop paid gradients, IV arithmetic/storage unpriced. Learning a local inverse of a supplied actuator is not emergence of all sensorimotor competence or new algorithm. Current scalar-gain evidence stays closed.',old_replay=0)
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
  dest=raw/f'c{i}_{arm}';dest.mkdir();rows=[];frames=[];model=CoupledBody();oldc=np.array(center(s.body));disp=np.zeros(2)
  def save(t):np.savez_compressed(dest/f'state_{t:04}.npz',body=s.body,energy=s.energy,memory=s.memory,eligibility=s.eligibility,aux=a,rng=np.asarray(json.dumps(rng.bit_generator.state)),clock=t)
  def frame(t):frames.append((t,s.body.copy(),s.energy.copy(),readout(a,arm)[0].copy()))
  save(begin);frame(begin)
  for t in range(begin+1,769):
   if STOP or attempts>=p['native_cap'] or time.monotonic()-start>p['science_wall']:raise RuntimeError('budget_or_stop')
   relation=sg if t<=320 else -sg;feedback=t<=640;write=feedback and arm not in p['forks'];phase='acquisition' if t<=320 else 'change' if t<=640 else 'retention';fld=terrain(relation)
   model.coupling=rotation(sg*np.pi/2 if t<=320 else -sg*np.pi/4)
   dump(OUT/'pending.json',dict(job=job,path=str(dest),step=t,attempt=attempts+1));attempts+=1
   before_rho=s.body.copy();s,a,m=transition(model,s,a,arm,rng.standard_normal((2,32,32)),fld,feedback,write);returned+=1;(OUT/'pending.json').unlink()
   c=np.array(center(s.body));delta=(c-oldc+16)%32-16;disp+=delta;oldc=c;mass=float(s.body.sum());conn=largest_fraction(s.body)
   d=unit(a[13:15]);v=m['velocity'];align=float((before_rho*np.sum(d*v,axis=0)).sum()/mass)
   rows.append(dict(t=t,phase=phase,mass=mass,energy=float(s.energy.sum()),connected=conn,field_score=float((s.body*fld).sum()/mass),positive_mass=float(s.body[fld>0].sum()),strong_mass=float(s.body[fld>.5].sum()),dy=float(disp[0]),dx=float(disp[1]),mean_y=float((before_rho*m['mean'][0]).sum()/mass),mean_x=float((before_rho*m['mean'][1]).sum()/mass),response_alignment=align,movement=float(m['movement_paid'].sum()),unpaid=float(m['movement_unpaid'].sum()),basal=float(m['basal_paid'].sum()),death=float((m['mass_injury']+m['mass_starvation']).sum()),gain=float(m['gain'].sum()),loss=float(m['loss'].sum()),paid_mass=float(s.body[m['paid_observed']].sum()),map_sample_mass=float(before_rho[m['map_sample']].sum()),map_writes=bool(write and arm!='reflex'),gradient_observed_mass=float(s.body[np.any(m['gradient_observed'],axis=0)].sum()),balance=m['whole_energy_error']))
   if t-begin<=8 or t in (321,322,640,641):np.savez_compressed(dest/f'receipt_{t:04}.npz',K=m['transport_weights'],motor=m['motor'],mean=m['mean'],J=m['J'],velocity=m['velocity'],gain=m['gain'],loss=m['loss'],physical_coupling=model.coupling)
   if t%8==0:frame(t)
   if t in (64,320,640,768):save(t)
   if mass<12 or conn<.75:save(t);break
  with (dest/'steps.csv').open('w') as f:w=csv.DictWriter(f,fieldnames=rows[0]);w.writeheader();w.writerows(rows)
  np.savez_compressed(dest/'frames.npz',clock=np.array([f[0] for f in frames]),body=np.stack([f[1] for f in frames]),energy=np.stack([f[2] for f in frames]),mean=np.stack([f[3] for f in frames]))
  r=dict(condition=i,arm=arm,path=str(dest),initial_sign=sg,start_clock=begin,end_clock=t,steps=t-begin,parent=str(parent) if parent else None,alive=bool(mass>=12 and conn>=.75),phases={})
  for phase in dict.fromkeys(x['phase'] for x in rows):
   rr=[x for x in rows if x['phase']==phase];tail=rr[-64:];score=float(np.mean([x['field_score'] for x in tail]));pm=float(np.mean([x['positive_mass']/x['mass'] for x in tail]))
   r['phases'][phase]=dict(score=score,positive_fraction=pm,gate=bool(r['alive'] and len(tail)==64 and score>.25 and pm>=.6),first_feedback=next((x['t'] for x in rr if x['paid_mass']>0),None),first_map_sample=next((x['t'] for x in rr if x['map_sample_mass']>0),None),final=rr[-1],gain=sum(x['gain'] for x in rr),loss=sum(x['loss'] for x in rr),movement=sum(x['movement'] for x in rr))
  r['all_observed_phases_gate']=all(v['gate'] for v in r['phases'].values());paths.append(r);dump(dest/'outcome.json',r);dump(OUT/'progress.json',dict(native=returned,paths=paths));print(json.dumps({k:r[k] for k in ('condition','arm','steps','all_observed_phases_gate')}),flush=True)
  return dest
 try:
  for i in range(2):
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
