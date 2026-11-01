"""Strong local paid-feedback controller; calibration ablation has same sensing."""
import numpy as np
from local_learning.material_action_credit.core import transition as source_transition
from local_learning.embodied_action_learning.core import DECAY,SIGMA,PAY,divide,advect,initial
from local_learning.heterogeneous_actuator_recalibration.core import ReversedMotorBody,actuator_gain

def paid_gradient(y,active):
    result=[]
    for axis in (-2,-1):
        pos=np.roll(active,-1,axis)&active;neg=np.roll(active,1,axis)&active
        numer=(np.roll(y,-1,axis)-y)*pos+(y-np.roll(y,1,axis))*neg
        result.append(divide(numer,pos.astype(float)+neg.astype(float)))
    return np.stack(result)

def transition(model,s,a,arm,noise,field,feedback,write):
    after,carried,m=source_transition(model,s,a[:8],'donor_immediate',noise,field,feedback,False)
    K=m['transport_weights'];den=advect(s.body,K);j=a[8].copy()
    if write:j=DECAY*j+(1-DECAY)*(noise[1]/SIGMA)*(K[2]-K[3])
    j=divide(advect(s.body*j,K),den);j[after.body==0]=0
    y=divide(m['gain']-m['loss'],PAY*after.body);active=m['paid_observed']
    target=np.clip(2*paid_gradient(y,active),-2.,2.)
    if arm=='calibrated_feedback':target[1]*=np.where(j<0,-1.,1.)
    if write:carried[:2]=np.where(active,.5*carried[:2]+.5*target,carried[:2])
    m['estimated_frame']=np.where(j<0,-1.,1.);m['update']=carried[:2] # receipt labelled target state, not RL score update
    return after,np.concatenate((carried,j[None])),m
