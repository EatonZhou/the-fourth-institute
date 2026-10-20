"""One journalled runner job: opportunity check then frozen local-motor comparison."""
import os,json,hashlib,time,signal,traceback,csv
from pathlib import Path
import numpy as np
from dataclasses import asdict
from .core import *
from local_learning.local_terminal_feedback.experiment import control
SRC=Path(__file__).resolve().parents[2]
TASK='embodied_action_learning_20261019'
OUT=SRC/'local_learning/results_20261019'/TASK
STOP=False

def dump(p,v):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,ensure_ascii=False,indent=2,allow_nan=False,default=lambda x:x.item() if isinstance(x,np.generic) else str(x))+'\n')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def prepare():
 OUT.mkdir(parents=True,exist_ok=True);assert not (OUT/'plan.json').exists()
 files=[Path(__file__),Path(__file__).with_name('core.py'),SRC/'local_learning/material_transport.py',SRC/'local_learning/lifetime_cue_reversal/core.py',SRC/'local_learning/local_terminal_feedback/experiment.py']
 plan=dict(task=TASK,owner='resident_d3ccfa2d95b7',hypothesis='One occupied-neighbor convex mixing of acquired motor parameters improves coherent movement and unannounced reversal over independent local score-trace learning; this is a fixed coordination test, not novel RL.',
 sources={str(p):sha(p) for p in files},rule=asdict(RULE),arms=list(ARMS),seeds=[202610199101,202610199102],signs=[1,-1],
 pilot={'arms':['fixed'],'steps':256,'conditions':2,'go':'both alive; endpoint toroidal displacement >=2; actual paid contact occurred. No efficacy selection.'},
 science={'acquisition':320,'reversal':320,'no_feedback_unchanged_relation':256,'steps':7168},planned_native=7680,native_cap=8192,worker_cap=240,cpu=2,science_wall_cap=95,total_wall_cap=115,raw_cap=128*1024**2,
 information='Only locally paid signed energy divided by fixed per-tissue tariff; previous observation and score transported with true K. No coordinate/cue gradient/map/clock/global reward to policy.',
 mechanism='theta(2) initial0; Gaussian motor .8*tanh(theta+.6*epsilon), fresh iid epsilon each native; score epsilon/.6 carried once by actualK. theta clip[-2,2], alpha.4 times score*(paid_y-carried_previous_y). trace score EMA exp(-1/16). neighbor additionally one five-cell occupied mass-convex theta mixing every step.',
 baseline='local_immediate uses same alpha and current transported score; local_trace standard local reward-modulated eligibility; fixed uses preset east theta=(0,1), same iid exploration, no reward update. Fixed directional prior disclosed; not an optimized oracle.',
 boundaries='No birth; actual conservative soft deformable CA with paid movement and starvation. Symmetric local isotropic cohesion is preset; motor axes and stochastic actuator preset, not learned morphology. Neighbor communication and arithmetic unpriced. No unbiased organism policy-gradient claim from local material-mixed credit.',
 environment='32x32 periodic finite substrate covers grid. y=sign*sin(2pi*(x-16)/32), tariff .005*rho, gain capped room and loss capped E; detector rho>=1e-4, no normalization by field amplitude. InitialE=6rho, capacity8rho. Native basal and motor prices unchanged. Reversal only environment at step321. Last256 no payment/write, transport and motor continue.',
 survival='M>=12 and largest connected component fraction>=.75; failure retained, no revival',
 endpoint_gate='alive and last64 mean tissue-weighted field>.25 and last64 mean fraction in positive field>=.6. All three phases reported; neither mean across lives nor cost-free status assumed.',
 records='all scalar steps; actual frames every8, fullstate0/256/320/640/896; first16 motor/K/payment receipts; every attempt journalled. Never rerun unknown.',old_replay=0)
 dump(OUT/'plan.json',plan);print(json.dumps({'plan':str(OUT/'plan.json'),'sha256':sha(OUT/'plan.json')}))

def main():
 global STOP
 plan=json.loads((OUT/'plan.json').read_text());assert all(sha(p)==h for p,h in plan['sources'].items())
 assert not (OUT/'result.json').exists() and not (OUT/'pending.json').exists()
 dump(OUT/'launch_control.json',control());t0=time.monotonic()
 job=os.environ['LENIA_JOB_ID'];raw=Path('/data/dataset/lenia-research/lineage_75e3399f4877')/TASK/job;raw.mkdir(parents=True,exist_ok=False)
 def stop(*_):
  global STOP
  STOP=True
 signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 recovery=json.loads((OUT/'recovery.json').read_text()) if (OUT/'recovery.json').exists() else None
 attempts=returned=0 if recovery is None else recovery['prior_native'];paths=[];status='running';error=None
 def execute(stage,idx,arm,steps):
  nonlocal attempts,returned
  if recovery and stage=='pilot' and idx==0:
   recovered=recovery['path'];paths.append(recovered);return recovered
  seed=plan['seeds'][idx];sgn=plan['signs'][idx];s,a=initial(seed);rng=np.random.default_rng(seed+50000)
  p=raw/f'{stage}_c{idx}_{arm}';p.mkdir();rows=[];frames=[];model=MotorBody();c0=center(s.body)
  def save(t):
   np.savez_compressed(p/f'state_{t:04}.npz',body=s.body,energy=s.energy,memory=s.memory,eligibility=s.eligibility,aux=a,rng=np.asarray(json.dumps(rng.bit_generator.state)),clock=t)
  save(0);frames.append((0,s.body.copy(),s.energy.copy(),a[:2].copy()))
  for t in range(1,steps+1):
   if STOP or attempts>=plan['native_cap'] or time.monotonic()-t0>plan['science_wall_cap']:raise RuntimeError('budget_or_stop')
   feedback=(stage=='pilot' or t<=640);sign=sgn if stage=='pilot' or t<=320 else -sgn;field=terrain(sign)
   noise=rng.standard_normal((2,SIZE,SIZE));dump(OUT/'pending.json',{'job':job,'path':str(p),'step':t,'attempt':attempts+1});attempts+=1
   s,a,m=transition(model,s,a,arm,noise,field,feedback,feedback);returned+=1;(OUT/'pending.json').unlink()
   mass=float(s.body.sum());conn=largest_fraction(s.body);cen=center(s.body)
   row=dict(t=t,phase=('pilot' if stage=='pilot' else ('acquisition' if t<=320 else 'reversal' if t<=640 else 'retention')),mass=mass,energy=float(s.energy.sum()),connected=conn,
      field_score=float((s.body*field).sum()/mass),positive_mass=float(s.body[field>0].sum()),strong_positive_mass=float(s.body[field>.5].sum()),
      gain=float(m['gain'].sum()),loss=float(m['loss'].sum()),movement=float(m['movement_paid'].sum()),unpaid=float(m['movement_unpaid'].sum()),basal=float(m['basal_paid'].sum()),death=float((m['mass_injury']+m['mass_starvation']).sum()),
      contact_mass=float(s.body[m['paid_observed']].sum()),written_mass=float(s.body[np.any(m['update']!=0,axis=0)&feedback].sum()) if arm!='fixed' else 0.,
      cy=float(cen[0]),cx=float(cen[1]),theta_y=float((s.body*a[0]).sum()/mass),theta_x=float((s.body*a[1]).sum()/mass),
      theta_coherence=float(np.linalg.norm((s.body*a[:2]).sum((1,2)))/max((s.body*np.linalg.norm(a[:2],axis=0)).sum(),1e-30)),balance=m['whole_energy_error'])
   rows.append(row)
   if t<=16:np.savez_compressed(p/f'receipt_{t:04}.npz',K=m['transport_weights'],motor=m['motor'],gain=m['gain'],loss=m['loss'],delta=m['delta'],update=m['update'])
   if t%8==0:frames.append((t,s.body.copy(),s.energy.copy(),a[:2].copy()))
   if t in (256,320,640,steps):save(t)
   if mass<12 or conn<.75:save(t);break
  with (p/'steps.csv').open('w') as f:
   w=csv.DictWriter(f,fieldnames=rows[0].keys());w.writeheader();w.writerows(rows)
  np.savez_compressed(p/'frames.npz',clock=np.array([x[0] for x in frames]),body=np.stack([x[1] for x in frames]),energy=np.stack([x[2] for x in frames]),theta=np.stack([x[3] for x in frames]))
  alive=rows[-1]['mass']>=12 and rows[-1]['connected']>=.75
  out=dict(stage=stage,condition=idx,arm=arm,path=str(p),steps=len(rows),alive=alive,initial_sign=sgn,
   endpoint_displacement=float(np.linalg.norm((center(s.body)-c0+16)%32-16)),first_feedback=next((x['t'] for x in rows if x['contact_mass']>0),None),phases={})
  for phase in dict.fromkeys(x['phase'] for x in rows):
   rr=[x for x in rows if x['phase']==phase];tail=rr[-64:]
   out['phases'][phase]=dict(steps=len(rr),last64_score=float(np.mean([x['field_score'] for x in tail])),last64_positive_fraction=float(np.mean([x['positive_mass']/x['mass'] for x in tail])),
     gain=sum(x['gain'] for x in rr),loss=sum(x['loss'] for x in rr),movement=sum(x['movement'] for x in rr),end_energy=rr[-1]['energy'],end_mass=rr[-1]['mass'],
     gate=bool(alive and len(tail)==64 and np.mean([x['field_score'] for x in tail])>.25 and np.mean([x['positive_mass']/x['mass'] for x in tail])>=.6))
  dump(p/'outcome.json',out);paths.append(out);dump(OUT/'progress.json',{'native':returned,'paths':paths});print(json.dumps({k:out[k] for k in ('stage','condition','arm','steps','alive','phases')}),flush=True)
  return out
 try:
  pilot=[execute('pilot',i,'fixed',256) for i in range(2)]
  go=all(x['alive'] and x['endpoint_displacement']>=2 and x['first_feedback'] is not None for x in pilot)
  dump(OUT/'pilot_decision.json',{'go':go,'criteria':plan['pilot']['go'],'native':returned})
  if go:
   for i in range(2):
    for arm in ARMS:execute('science',i,arm,896)
   status='complete'
  else:status='opportunity_failed'
 except BaseException:
  status='partial';error=traceback.format_exc()
 finally:
  result=dict(task=TASK,status=status,job=job,attempted=attempts,returned=returned,unknown=attempts-returned,old_replay=0,paths=paths,error=error,wall_seconds=time.monotonic()-t0,raw=str(raw),raw_bytes=sum(p.stat().st_size for p in raw.rglob('*') if p.is_file()))
  dump(OUT/'result.json',result);dump(Path(os.environ['LENIA_RUN_DIR'])/'result.json',result);print(json.dumps({k:v for k,v in result.items() if k!='paths'}),flush=True)
if __name__=='__main__':
 import sys
 (prepare if sys.argv[1]=='prepare' else main)()
