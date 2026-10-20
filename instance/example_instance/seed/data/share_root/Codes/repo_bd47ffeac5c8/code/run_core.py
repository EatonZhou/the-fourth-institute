"""Portable launcher around unchanged frozen numerical kernels; no team/Portal."""
from pathlib import Path
import argparse,csv,json,sys
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'src'))
import numpy as np
from local_learning.scalar_response_estimation import core as C
from local_learning.embodied_action_learning.core import largest_fraction
from local_learning.material_transport import State

p=argparse.ArgumentParser()
p.add_argument('--arm',choices=['spsa','reward_delta','context_delta','run_tumble','random'],default='spsa')
p.add_argument('--condition',type=int,choices=[0,1],default=0)
p.add_argument('--branch',choices=['clear','rotate90'])
p.add_argument('--steps',type=int,default=768)
p.add_argument('--output',type=Path,default=Path('core-output'))
p.add_argument('--check',action='store_true')
a=p.parse_args()
plan=json.loads((ROOT/'config/scalar_response_estimation.json').read_text())
if a.check:
    s,aux=C.initial(plan['seeds'][a.condition])
    assert s.body.shape==(32,32) and np.isfinite(aux).all()
    for f in (ROOT/'initial').rglob('*.npz'):
        with np.load(f,allow_pickle=False) as z:
            assert all(k in z for k in ('body','energy','memory','eligibility','aux','physical_coupling','rng'))
    print('PASS: numerical kernels import, initial construction, four parent archives readable; no trajectory executed')
    raise SystemExit(0)
if not 1<=a.steps<=768:p.error('--steps must be in 1..768')
if a.branch and a.arm!='spsa':p.error('content interventions use --arm spsa')
a.output.mkdir(parents=True,exist_ok=False)
model=C.CoupledBody();start=0
if a.branch:
    file=ROOT/f'initial/scalar_response_content/c{a.condition}_state_0640.npz'
    with np.load(file,allow_pickle=False) as z:
        s=State(*(z[k].copy() for k in ('body','energy','memory','eligibility')))
        aux=z['aux'].copy();rng=np.random.default_rng();rng.bit_generator.state=json.loads(str(z['rng']))
        model.coupling=z['physical_coupling'].copy()
    if a.branch=='clear':aux[:2]=0
    else:aux[:2]=np.stack((-aux[1],aux[0]))
    start=640
else:
    seed=plan['seeds'][a.condition];s,aux=C.initial(seed);rng=np.random.default_rng(seed+50000)
    model.coupling=C.rotation(plan['coupling_signs'][a.condition]*np.pi/2)
rows=[];clocks=[start];bodies=[s.body.copy()];energies=[s.energy.copy()]
def save(t):
    np.savez_compressed(a.output/f'state_{t:04}.npz',body=s.body,energy=s.energy,memory=s.memory,eligibility=s.eligibility,aux=aux,physical_coupling=model.coupling,rng=np.asarray(json.dumps(rng.bit_generator.state)),clock=t)
save(start)
for t in range(start+1,a.steps+1):
    if (t-1)%C.HOLD==0:aux,_=C.choose(aux,a.arm,rng.standard_normal((2,C.SIZE,C.SIZE)),rng.standard_normal((2,C.SIZE,C.SIZE)))
    feedback=t<=640;field=C.terrain(1 if t<=320 else -1)
    s,aux,m=C.transition(model,s,aux,a.arm,field,feedback,feedback,t%C.HOLD==0)
    mass=float(s.body.sum());connected=largest_fraction(s.body)
    rows.append(dict(t=t,phase='acquisition' if t<=320 else 'reversal' if t<=640 else 'retention',mass=mass,energy=float(s.energy.sum()),connected=connected,field_score=float((s.body*field).sum()/mass) if mass else 0,positive_mass=float(s.body[field>0].sum()),movement=float(m['movement_paid'].sum()),gain=float(m['gain'].sum()),loss=float(m['loss'].sum()),balance=m['whole_energy_error']))
    if t%8==0:clocks.append(t);bodies.append(s.body.copy());energies.append(s.energy.copy())
    if t in (64,320,640,768):save(t)
    if mass<12 or connected<.75:save(t);break
if not rows:p.error('--steps must be greater than branch starting step 640')
with (a.output/'steps.csv').open('w',newline='') as f:
    writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
np.savez_compressed(a.output/'frames.npz',clock=np.asarray(clocks),body=np.stack(bodies),energy=np.stack(energies))
(a.output/'run.json').write_text(json.dumps(dict(arm=a.arm,condition=a.condition,branch=a.branch,seed=plan['seeds'][a.condition],start=start,end=rows[-1]['t'],note='Portable selected-core rerun; no change to archived research'),indent=2))
print(f"Saved {len(rows)} native steps to {a.output.resolve()}")
