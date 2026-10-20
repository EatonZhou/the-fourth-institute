"""Local state-dependent Gaussian motor policy; no hidden field enters learner."""
import numpy as np
from local_learning.embodied_action_learning.core import (MotorBody,initial as old_initial,SIGMA,ALPHA,DECAY,PAY,divide,advect)
from local_learning.material_action_credit.core import transition as source_transition
from local_learning.paid_gradient_motor_control.core import paid_gradient

def initial(seed):
    s,_=old_initial(seed)
    return s,np.zeros((16,32,32))

def features(a):
    return np.concatenate((np.ones((1,32,32)),.5*a[12:14]),axis=0)

def readout(a,arm):
    w=a[:6].reshape(2,3,32,32);f=features(a)
    if arm in ('source','fixed','bias_frozen'):f=f.copy();f[1:]=0
    raw=(w*f).sum(1)
    return np.clip(raw,-2,2),raw,f

def transition(model,s,a,arm,noise,field,feedback=True,write=True):
    mean,raw,f=readout(a,arm)
    small=np.concatenate((mean,a[6:12]),axis=0)
    after,carried,m=source_transition(model,s,small,'donor_immediate',noise,field,feedback,False)
    K=m['transport_weights'];den=advect(s.body,K);local=a.copy();w=local[:6].reshape(2,3,32,32)
    valid=(a[9]>.999)&(m['coverage']>0)
    update=ALPHA*(noise/SIGMA)[:,None]*f[None]*m['delta']*valid
    # Derivative of clipped Gaussian mean. Bias-only baseline preserves old projected update.
    if arm in ('conditional','conditional_frozen'):update*= (np.abs(raw)<2)[:,None]
    if write and arm not in ('fixed','conditional_frozen','bias_frozen'):
        w[:]=np.clip(w+update,-2.,2.)
    out=divide(advect(s.body*local,K),den)
    out[6:12]=carried[2:8]
    y=divide(m['gain']-m['loss'],PAY*after.body);active=m['paid_observed']
    g=paid_gradient(y,active)
    avail=np.stack([active&(np.roll(active,1,ax)|np.roll(active,-1,ax)) for ax in (-2,-1)])
    out[12:14]=np.where(avail,g,out[12:14]);out[14:16]=np.where(avail,1.,out[14:16])
    if arm=='fixed' and write:
        target=np.clip(2*g,-2,2)
        ww=out[:6].reshape(2,3,32,32)
        ww[:,0]=np.where(avail,.5*ww[:,0]+.5*target,ww[:,0])
    out[:,after.body==0]=0
    assert np.isfinite(out).all() and np.max(np.abs(out[:6]))<=2+1e-12
    m.update(mean=mean,policy_features=f,parameter_update=update if write and arm!='fixed' else np.zeros_like(update),gradient_observed=avail)
    return after,out,m
