import os,json,time,csv,traceback,signal
from pathlib import Path
from dataclasses import asdict
import numpy as np
from .core import *
from local_learning.embodied_action_learning.run import SRC,dump,sha
from local_learning.embodied_action_learning.core import center,largest_fraction
from local_learning.local_terminal_feedback.experiment import control
TASK='scalar_outcome_motor_learning_20261019';OUT=SRC/'local_learning/results_20261019'/TASK
STOP=False

def prepare():
 OUT.mkdir(parents=True,exist_ok=True);assert not (OUT/'plan.json').exists()
 old=json.loads((SRC/'local_learning/results_20261019/affine_motor_baseline_20261019/plan.json').read_text())
 files=[Path(__file__),Path(__file__).with_name('core.py')]+[Path(f) for f in old['sources']]
 p=dict(task=TASK,owner='resident_d3ccfa2d95b7',seeds=[202610199431,202610199432],coupling_signs=[1,-1],arms=ARMS,rule=asdict(RULE),planned_native=6144,native_cap=8192,worker_cap=240,cpu=2,gpu=0,science_wall=95,raw_cap=128*1024**2,sources={str(f):sha(f) for f in files},
 hypothesis='Can scalar consequences of paid local actions, without donor displacement or precomputed reward gradient, support useful acquisition and reversal? Does scalar-state-conditioned score learning add anything beyond ordinary scalar-difference learning or fixed run/tumble? A negative result closes this construction, not all scalar learning.',
 schedule='320 acquisition+320 unannounced reward reversal+128same relation no payments/new reward observations or parameter writes. Unknown global +/-90 rotation fixed throughout. Original body/rule/energy/price unchanged. Two new seeds, opposite rotations, same initial reward meaning so the optimal commands differ. First64 of every actual trajectory are opportunity checks, not a separate rerun. No success-selected seeds.',
 learner='Every8 native an internal oscillator chooses .8tanh(clipped mean+.6epsilon), transports/holds motor between choices. Score epsilon/.6 and decision-time features travel as material. End-block actual local normalized paid receipt minus transported block-start receipt supplies scalar delta. Alpha2 fixed; six weights clipped[-2,2]. reward_delta only constant feature; context_delta [1,lastpaid_y,lastcompleted_block_delta]. Update ALPHA*score*delta*features/max(normfeatures^2,1). Normalization prevents context amplitude alone increasing update. No gain/alpha/trace scan.',
 baseline='Same actuator/noise/8-clock schedule. run_tumble keeps direction if completed scalar block delta>=0 else resamples isotropic heading, and still has same Gaussian motor noise. random has zero mean with same noise. All arms receive two independent2-vector noise fields per decision: Gaussian innovation and isotropic-heading source; unused fields consumed equally. Actual motion/cost differs and is reported, nominal capability/tariff matched. Ordinary reward_delta and fixedrun/tumble are strong simple baselines, not weak frozen controls.',
 information='Policy choose/observe never receives K, displacement, coordinates, field, reward gradient, correct action or change clock. ActualK used only by transport; it is not a sensing input. Scalars are actual energy gained/lost divided by fixedPAY*rho, not divided by field amplitude. No payment means missing, not negative. Four-neighbor or Gaussian body forces are original preset physics; no reward spatial comparison. Internal8-clock action timer is not a reversal cue.',
 boundaries='No birth; local continuous CA material deforms, not rigid point. Unknown coupling physical adapter hidden. Forward transport mixes action/score/receipts so score-weighted local update is not claimed unbiased whole-body REINFORCE. Standard run/tumble and score rules not novel. Extra memory/arithmetic unpriced. Main representation differs6 contextual vs2 active constant weights, disclose capacity.',
 gate='Perlife perphase: alive(M>=12 and largestcomponent>=.75),last64mean field>.25,positive mass/M>=.6. No crosslife mean rescue. Motion/energy/basal/deletion/bodytime alongside. No feedback window uses cached own receipt and continued noise, not motion freeze.',
 records='all scalar steps, actualframes every8, fullstate0/64/320/640/768 or failure inclRNG; first16 and321/328/640/641 actual receipts; pre-native pendingjournal, allattempts charged, unknown never replayed.',old_replay=0)
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
