"""Odd state-conditioned gain policy; direction feature is a declared preset."""
import numpy as np
from local_learning.state_conditioned_local_motor.core import initial
from local_learning.embodied_action_learning.core import MotorBody,SIGMA,ALPHA,PAY,divide,advect
from local_learning.material_action_credit.core import transition as source_transition
from local_learning.paid_gradient_motor_control.core import paid_gradient

def direction(a):
 g=a[12:14];return divide(g,np.linalg.norm(g,axis=0))
def readout(a,arm):
 d=direction(a)
 if arm=='source':mean=np.stack((a[0],a[3]))
 elif arm=='fixed_direction':mean=d
 elif arm=='zero_gain':mean=np.zeros_like(d)
 else:mean=a[0]*d
 return mean,mean,d

def transition(model,s,a,arm,noise,field,feedback=True,write=True):
 mean,_,d=readout(a,arm);small=np.concatenate((mean,a[6:12]),axis=0)
 after,carried,m=source_transition(model,s,small,'donor_immediate',noise,field,feedback,False)
 local=a.copy();valid=(a[9]>.999)&(m['coverage']>0);delta=m['delta']*valid
 u=ALPHA*(noise/SIGMA)*delta;gain_update=ALPHA*np.sum((noise/SIGMA)*d,axis=0)*delta
 if write and arm=='source':local[[0,3]]=np.clip(local[[0,3]]+u,-2,2)
 if write and arm=='learned_gain':local[0]=np.clip(local[0]+gain_update,-2,2)
 K=m['transport_weights'];den=advect(s.body,K);out=divide(advect(s.body*local,K),den);out[6:12]=carried[2:8]
 y=divide(m['gain']-m['loss'],PAY*after.body);active=m['paid_observed'];g=paid_gradient(y,active);avail=np.stack([active&(np.roll(active,1,ax)|np.roll(active,-1,ax)) for ax in (-2,-1)])
 out[12:14]=np.where(avail,g,out[12:14]);out[14:16]=np.where(avail,1.,out[14:16]);out[:,after.body==0]=0
 assert np.isfinite(out).all() and np.max(np.abs(out[:6]))<=2+1e-12
 m.update(mean=mean,policy_features=d,parameter_update=gain_update if arm=='learned_gain' and write else np.zeros_like(gain_update),gradient_observed=avail)
 return after,out,m
