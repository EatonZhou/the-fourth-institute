"""A deformable material CA with local outcome-conditioned cue values.
The experimenter schedules stimuli; learner() receives no phase, clock, mapping,
coordinates, identity, global reward or future. Both registrations are public to
all three rules. Only associative values differ between update mechanisms.
"""
from dataclasses import asdict
import numpy as np
from scipy.ndimage import label
from local_learning.material_transport import MaterialBody,Rule,State,founder,advect

SIZE=32
RULE=Rule(dt=.1,mobility=1.,cohesion=3.,pressure=1.5,interaction_sigma=1.4,cue_gain=18.,
          learning_rate=0.,eligibility_time=1.6,memory_time=1e30,uptake_rate=.2,
          energy_capacity=4.,basal_cost=.012,movement_cost=.002,action_budget='donor_energy')
ARMS=('reactor','fast_delta','eligibility')

def divide(a,b):return np.divide(a,b,out=np.zeros_like(a),where=b>0)
def center(rho):
    ans=[]
    for axis in (0,1):
        marg=rho.sum(1-axis);z=(marg*np.exp(2j*np.pi*np.arange(SIZE)/SIZE)).sum()
        ans.append(float(np.angle(z)%(2*np.pi)*SIZE/(2*np.pi)))
    return np.array(ans)
def distance(c):
    yy,xx=np.mgrid[:SIZE,:SIZE];dy=(yy-c[0]+SIZE/2)%SIZE-SIZE/2;dx=(xx-c[1]+SIZE/2)%SIZE-SIZE/2
    return np.hypot(dy,dx)
def seed_state(seed):
    rng=np.random.Generator(np.random.PCG64(seed));s=founder(SIZE,24.,'single')
    s.body*=1+.03*rng.standard_normal(s.body.shape);s.body=np.maximum(s.body,0);s.body*=24/s.body.sum()
    s.energy=s.body*3.;s.memory[:]=.2;s.eligibility[:]=0
    return s,np.zeros_like(s.memory)
def largest_fraction(rho):
    shifted=np.roll(rho,tuple(np.rint(SIZE/2-center(rho)).astype(int)),(0,1))
    labs,_=label(shifted>max(1e-6,.001*shifted.max()))
    masses=np.bincount(labs.ravel(),weights=shifted.ravel());return float(max(masses[1:],default=0)/max(rho.sum(),1e-300))

def learner(after,last,cue,outcome,arm,write_enabled=True):
    # Common current cue registration and material-carried trace, including probes.
    total=cue.sum(0);seen=total>.05;norm=divide(cue,total)
    last=np.where(seen[None],norm,last)
    decay=np.exp(-1/16);after.eligibility=decay*after.eligibility+(1-decay)*cue
    phi=last if arm=='fast_delta' else divide(after.eligibility,after.eligibility.sum(0))
    delta=outcome-(after.memory*phi).sum(0)
    contact=(outcome!=0)&(after.body>0)
    if write_enabled and arm!='reactor':
        alpha=1. if arm=='fast_delta' else .5
        change=alpha*phi*delta[None]/np.maximum((phi*phi).sum(0),1e-12)*contact
        after.memory=np.clip(after.memory+change,-1,1)
    after.memory[:,after.body==0]=0;last[:,after.body==0]=0
    return after,last,delta

def one_step(model,s,last,cue,feedback,arm,write_enabled=True):
    before=s;after,m=model.step(s,cue,np.zeros_like(s.body),np.zeros_like(s.body),plastic=False)
    transported=advect(before.body,m['transport_weights'])
    last=divide(advect(before.body*last,m['transport_weights']),transported)
    # Completed local environmental benefit/cost; explicitly paid into/out of E.
    requested=.08*after.body*abs(feedback)
    gain=np.where(feedback>0,np.minimum(requested,np.maximum(RULE.energy_capacity*after.body-after.energy,0)),0.)
    loss=np.where(feedback<0,np.minimum(requested,after.energy),0.)
    after.energy+=gain-loss
    outcome=divide(gain-loss,requested) # realized local valence, no hidden map
    after,last,delta=learner(after,last,cue,outcome,arm,write_enabled)
    balance=float(after.energy.sum()-before.energy.sum()-m['resource_consumed'].sum()+m['energy_spent'].sum()+m['energy_lost_with_tissue'].sum()-gain.sum()+loss.sum())
    assert abs(balance)<1e-9
    return after,last,dict(m,feedback_gain=gain,feedback_loss=loss,feedback_requested=requested,local_outcome=outcome,prediction_error=delta,whole_energy_error=balance)

def protocol(seed,order,pairing):
    # Four training epochs, two before and two after unannounced reversal.
    rng=np.random.Generator(np.random.PCG64(seed+771));parts=[]
    def probe(tag,good,side):parts.append(dict(kind='probe',tag=tag,steps=64,good=good,side=side))
    probe('initial',order,1)
    for phase,good in (('acquisition',order),('reversal',1-order)):
        # 8 completed pulses per cue per phase. Each cue has 4+/4- unpaired.
        signs={i:rng.permutation(np.array([1.]*4+[-1.]*4)).reshape(2,4).tolist() for i in (0,1)}
        for epoch in (0,1):
            for ident in (0,1):
                vals=([1. if ident==good else -1.]*4) if pairing=='paired' else signs[ident][epoch]
                parts.append(dict(kind='lesson',tag=f'{phase}_{epoch}_cue{ident}',steps=48,cue=ident,good=good,pulses=vals))
            probe(f'{phase}_{epoch}',good,1 if epoch==0 else -1)
        parts.append(dict(kind='neutral',tag=phase+'_gap',steps=64,good=good))
        probe(phase+'_retention',good,1)
    assert sum(p['steps'] for p in parts)==960
    return parts

def fields(part,t,anchor):
    cue=np.zeros((2,SIZE,SIZE));feedback=np.zeros((SIZE,SIZE));spots=None
    if part['kind']=='lesson':
        g=np.exp(-.5*(distance(anchor)/6.)**2)
        if t<24:cue[part['cue']]=g
        if t in (32,36,40,44):feedback=g*part['pulses'][(t-32)//4]
    elif part['kind']=='probe':
        spots=np.stack([anchor+[0,-4*part['side']],anchor+[0,4*part['side']]])
        cue=np.stack([np.exp(-.5*(distance(c)/3.5)**2) for c in spots])
    return cue,feedback,spots
