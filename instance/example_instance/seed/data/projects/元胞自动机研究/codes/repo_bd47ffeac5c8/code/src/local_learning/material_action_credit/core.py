"""Source-return credit: one-hop paid outcome messages along executed K edges.
No hidden world field reaches this update; only actually paid recipient outcomes.
"""
import numpy as np
from local_learning.embodied_action_learning.core import (SIZE,RULE,SIGMA,ALPHA,DECAY,PAY,MotorBody,initial,terrain,mix,divide,advect,largest_fraction,center)
ARMS=('donor_immediate','donor_trace','donor_neighbor')

def pull(y,K):
    out=(1-K.sum(0))*y
    for w,shift,axis in zip(K,(1,-1,1,-1),(-2,-2,-1,-1)):
        out=out+w*np.roll(y,-shift,axis)
    return out

def transition(model,s,a,arm,noise,field,feedback=True,write=True):
    model.motor=.8*np.tanh(a[:2]+SIGMA*noise)
    after,m=model.step(s,np.zeros((2,SIZE,SIZE)),np.zeros((SIZE,SIZE)),np.zeros((SIZE,SIZE)),plastic=False)
    K=m['transport_weights'];den=advect(s.body,K);observed=(after.body>=1e-4)&feedback
    requested=PAY*after.body*observed*abs(field)
    gain=np.where(field>0,np.minimum(requested,np.maximum(RULE.energy_capacity*after.body-after.energy,0)),0.)
    loss=np.where(field<0,np.minimum(requested,after.energy),0.);after.energy+=gain-loss
    y=divide(gain-loss,PAY*after.body);active=(gain+loss)>0
    local=a.copy();score=noise/SIGMA;local[2:4]=DECAY*a[2:4]+(1-DECAY)*score
    returned=pull(y,K);coverage=pull(active.astype(float),K)
    # Unobserved descendant fractions contribute zero, never a loss.
    delta=returned-a[4]*coverage;valid=(a[5]>.999)&(coverage>0)
    eligibility=score if arm=='donor_immediate' else local[2:4]
    update=ALPHA*eligibility*delta*valid
    if write:local[:2]=np.clip(local[:2]+update,-2.,2.)
    carried=divide(advect(s.body*local,K),den)
    if arm=='donor_neighbor':carried[:2]=mix(carried[:2],after.body)
    carried[4]=np.where(active,y,carried[4]);carried[5]=np.where(active,1.,carried[5]);carried[6:8]=divide(advect(s.body*noise,K),den);carried[:,after.body==0]=0
    err=float(after.energy.sum()-s.energy.sum()+m['energy_spent'].sum()+m['energy_lost_with_tissue'].sum()-gain.sum()+loss.sum());assert abs(err)<1e-8
    m.update(gain=gain,loss=loss,paid_observed=active,delta=delta,update=update,whole_energy_error=err,motor=model.motor,returned=returned,coverage=coverage)
    return after,carried,m
