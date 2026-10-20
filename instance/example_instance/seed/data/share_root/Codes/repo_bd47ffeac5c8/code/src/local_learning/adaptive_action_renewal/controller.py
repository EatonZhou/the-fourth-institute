"""Carried local run/brake actor and TD critic. Biased under mixing/deletion.
Only prepare reads old controller state; settle consumes already-paid receipts.
No world, schedule, resource geometry or global signal enters this module.
"""
from dataclasses import dataclass, asdict, replace
import numpy as np
from local_learning.material_transport import MaterialBody, advect

@dataclass(frozen=True)
class LearningRule:
    hold_time: float = .8
    food_time: float = .8
    discount_time: float = 6.4
    trace_time: float = 3.2
    actor_rate: float = .1
    critic_rate: float = .1
    reward_scale: float = .02
    loss_price: float = .08
    theta_bound: float = 3.
    value_bound: float = 2.

NAMES=('theta','value','trace','food','run','remaining')
def initial(shape):
    return {k:np.zeros(((2,)+shape) if k in ('theta','value','trace') else shape) for k in NAMES}

def features(food):return np.stack((1-food,food))
def probability(c):
    return 1/(1+np.exp(-np.sum(c['theta']*features(c['food']),axis=0)))

def prepare(c, body, draw, dt, rule=LearningRule()):
    f=features(c['food']);p=probability(c);sample=(c['remaining']<=1e-10)&(body>0)
    selected=(draw<p).astype(float)
    run=np.where(sample,selected,c['run'])
    remaining=np.where(sample,rule.hold_time,c['remaining'])
    score=f*(selected-p)*sample
    trace=c['trace']*np.exp(-dt/rule.trace_time)+score
    prepared={**c,'run':run,'remaining':remaining,'trace':trace}
    return prepared,dict(probability=p,sampled=sample,selected=np.where(sample,selected,-1.),
                         score=score,old_features=f,old_theta=c['theta'],old_value=c['value'])

class RunBrakeBody(MaterialBody):
    def __init__(self,physical):super().__init__(physical);self.readout=None
    def face_rates(self,state,cue):
        if self.readout is None:raise RuntimeError('prepare readout before plant')
        # Only the proxy used by face_rates changes; the actual p payload is untouched.
        return MaterialBody.face_rates(self,replace(state,polarity=state.polarity*self.readout),cue)

def settle(old_body,prepared,decision,after,metrics,dt,uptake_rate,learn,rule=LearningRule()):
    w=metrics['transport_weights'];b=advect(old_body,w);den=np.maximum(b,1e-300)
    def carry(a):return advect(old_body*a,w)/den
    c={k:carry(prepared[k]) for k in NAMES}
    old_features=carry(decision['old_features'])
    loss=metrics['mass_injury']+metrics['mass_starvation']
    full_reward=metrics['resource_consumed']-metrics['movement_paid']-metrics['basal_paid']-metrics['energy_lost_with_tissue']-rule.loss_price*loss
    local_reward=full_reward/den
    food_now=np.clip(metrics['resource_consumed']/(dt*uptake_rate*den),0,1)
    c['food']=np.clip(c['food']*np.exp(-dt/rule.food_time)+(1-np.exp(-dt/rule.food_time))*food_now,0,1)
    next_features=features(c['food'])
    old_estimate=np.sum(c['value']*old_features,axis=0)
    next_estimate=np.sum(c['value']*next_features,axis=0)*(after.body>0)
    delta=local_reward+np.exp(-dt/rule.discount_time)*next_estimate-old_estimate
    increment=rule.actor_rate*(delta/rule.reward_scale)*c['trace']
    if learn:c['theta']=np.clip(c['theta']+increment,-rule.theta_bound,rule.theta_bound)
    c['value']=np.clip(c['value']+rule.critic_rate*delta*old_features,-rule.value_bound,rule.value_bound)
    c['remaining']=np.maximum(c['remaining']-dt,0)
    for k in NAMES:c[k][...,after.body==0]=0
    audit=dict(transported_body=b,full_reward=full_reward,local_reward=local_reward,
               delta=delta,theta_proposal=increment,food_now=food_now)
    return c,audit
