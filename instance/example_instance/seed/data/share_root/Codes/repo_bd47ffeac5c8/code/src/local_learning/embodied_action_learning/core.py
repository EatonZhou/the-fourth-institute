"""Local motor adaptation on an actual deformable, no-birth material CA.
No controller receives coordinates, reward map, reversal clock or global sums.
"""
from dataclasses import replace
import numpy as np
from scipy.ndimage import gaussian_filter
from local_learning.material_transport import Rule, State, MaterialBody, founder, advect
from local_learning.lifetime_cue_reversal.core import divide, largest_fraction, center
SIZE=32
RULE=Rule(cue_gain=0., learning_rate=0., memory_time=1e30, energy_capacity=8.,
          movement_cost=.002, action_budget='donor_energy', polarity_gain=0.)
ARMS=('fixed','local_immediate','local_trace','neighbor_trace')
SIGMA=.6; ALPHA=.4; DECAY=float(np.exp(-1/16)); PAY=.005

def mix(x,rho):
    numerator=rho*x; denominator=rho.copy()
    for shift,axis in ((1,-1),(-1,-1),(1,-2),(-1,-2)):
        numerator=numerator+np.roll(rho*x,shift,axis)
        denominator=denominator+np.roll(rho,shift,axis)
    return divide(numerator,denominator)

class MotorBody(MaterialBody):
    def __init__(self): super().__init__(RULE); self.motor=None
    def face_rates(self,s,cue):
        p=RULE.cohesion*gaussian_filter(s.body,RULE.interaction_sigma,mode='wrap',truncate=2)-RULE.pressure*s.body**2
        return np.stack([RULE.mobility*np.maximum(np.tanh(np.roll(p,shift,axis)-p+
            self.motor[k//2]*(1 if k%2==0 else -1)),0.)
            for k,(shift,axis) in enumerate(((-1,-2),(1,-2),(-1,-1),(1,-1)))])

def initial(seed):
    rng=np.random.default_rng(seed); s=founder(SIZE,24.)
    s.body*=1+.03*rng.standard_normal(s.body.shape);s.body*=24/s.body.sum();s.energy=6*s.body
    # params, score eligibility, previous observed outcome, observed mask, perturbation
    a=np.zeros((8,SIZE,SIZE));return s,a

def terrain(sign):
    # Entire finite toroidal substrate is contact support; no gaussian tail.
    _,x=np.mgrid[:SIZE,:SIZE]
    return sign*np.sin(2*np.pi*(x-16)/SIZE)

def transition(model,s,a,arm,noise,field,feedback=True,write=True):
    theta=a[:2].copy()
    if arm=='fixed': theta[:]=0.;theta[1]=1.
    model.motor=.8*np.tanh(theta+SIGMA*noise)
    after,m=model.step(s,np.zeros((2,SIZE,SIZE)),np.zeros((SIZE,SIZE)),np.zeros((SIZE,SIZE)),plastic=False)
    K=m['transport_weights']; den=advect(s.body,K)
    carried=divide(advect(s.body*a,K),den)
    score=divide(advect(s.body*(noise/SIGMA),K),den)
    carried[2:4]=DECAY*carried[2:4]+(1-DECAY)*score
    observed=(after.body>=1e-4)&feedback
    requested=PAY*after.body*observed*abs(field)
    gain=np.where(field>0,np.minimum(requested,np.maximum(RULE.energy_capacity*after.body-after.energy,0)),0.)
    loss=np.where(field<0,np.minimum(requested,after.energy),0.)
    after.energy+=gain-loss
    active=(gain+loss)>0
    y=divide(gain-loss,PAY*after.body)
    # Baseline is the same material's previous paid observation, transported by K.
    delta=y-carried[4];valid=active&(carried[5]>.999)
    eligible=score if arm=='local_immediate' else carried[2:4]
    update=ALPHA*eligible*delta*valid
    if write and arm!='fixed': carried[:2]=np.clip(carried[:2]+update,-2.,2.)
    if arm=='neighbor_trace':carried[:2]=mix(carried[:2],after.body)
    carried[4]=np.where(active,y,carried[4]);carried[5]=np.where(active,1.,carried[5])
    carried[6:8]=divide(advect(s.body*noise,K),den)
    carried[:,after.body==0]=0
    err=float(after.energy.sum()-s.energy.sum()+m['energy_spent'].sum()+m['energy_lost_with_tissue'].sum()-gain.sum()+loss.sum())
    assert abs(err)<1e-8 and np.isfinite(carried).all()
    m.update(gain=gain,loss=loss,paid_observed=active,delta=delta,update=update,
             whole_energy_error=err,motor=model.motor)
    return after,carried,m
