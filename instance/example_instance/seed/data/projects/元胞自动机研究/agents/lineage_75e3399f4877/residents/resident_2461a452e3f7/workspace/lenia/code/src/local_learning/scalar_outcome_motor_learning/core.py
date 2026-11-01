"""Scalar paid outcomes only. K is used by transport, never as a policy input.
Local mixed-score credit is not an unbiased whole-body policy gradient.
"""
import numpy as np
from local_learning.affine_motor_baseline.core import CoupledBody,rotation,terrain
from local_learning.embodied_action_learning.core import RULE,divide,advect,initial as host_initial
SIZE=32;HOLD=8;SIGMA=.6;ALPHA=2.;PAY=.005
ARMS=['reward_delta','context_delta','run_tumble','random']

def unit(x):return divide(x,np.linalg.norm(x,axis=0))
def initial(seed):
 s,_=host_initial(seed);return s,np.zeros((21,SIZE,SIZE))

def choose(a,arm,eps,heading_noise):
 """Only carried associative state, own scalar receipts and exogenous noise."""
 a=a.copy();features=np.stack((np.ones((SIZE,SIZE)),a[16],np.clip(a[18],-1,1)))
 if arm=='reward_delta':features[1:]=0
 if arm in ('reward_delta','context_delta'):
  mean=np.einsum('ijyx,jyx->iyx',a[:6].reshape(2,3,SIZE,SIZE),features)
 elif arm=='run_tumble':
  change=(a[17]<.999)|(a[18]<0)
  a[6:8]=np.where(change,unit(heading_noise),a[6:8]);mean=a[6:8]
 else:mean=np.zeros((2,SIZE,SIZE))
 a[8:10]=.8*np.tanh(np.clip(mean,-2,2)+SIGMA*eps)
 a[10:12]=eps/SIGMA;a[12:15]=features;a[15]=a[16];a[20]=a[17];a[19]=0
 return a,mean

def observe(a,y,active,motion_received,arm,write,end_block):
 """No spatial derivatives, coordinates, K, map, or external phase flag."""
 a=a.copy();a[19]=np.maximum(a[19],motion_received)
 delta=np.zeros_like(y);update=np.zeros((6,SIZE,SIZE));valid=np.zeros_like(active)
 if end_block:
  valid=active&(a[20]>.999)&(a[19]>.999)
  delta=np.where(valid,y-a[15],0.)
  if write and arm in ('reward_delta','context_delta'):
   features=a[12:15];normal=np.maximum(np.sum(features*features,axis=0),1.)
   update=(ALPHA*a[10:12,None]*features[None,:]*delta/normal).reshape(6,SIZE,SIZE)
   a[:6]=np.clip(a[:6]+update,-2.,2.)
  a[18]=np.where(valid,delta,a[18])
 a[16]=np.where(active,y,a[16]);a[17]=np.where(active,1.,a[17])
 return a,delta,update,valid

def transition(model,s,a,arm,field,feedback,write,end_block):
 model.motor=a[8:10].copy()
 after,m=model.step(s,np.zeros((2,SIZE,SIZE)),np.zeros((SIZE,SIZE)),np.zeros((SIZE,SIZE)),plastic=False)
 K=m['transport_weights'];den=advect(s.body,K)
 carried=divide(advect(s.body*a,K),den)
 # Transported receipt that some actual donor motion was paid, no displacement.
 motion_received=divide(advect(s.body*(m['movement_paid']>0),K),den)
 observed=(after.body>=1e-4)&feedback
 requested=PAY*after.body*observed*np.abs(field)
 gain=np.where(field>0,np.minimum(requested,np.maximum(RULE.energy_capacity*after.body-after.energy,0)),0.)
 loss=np.where(field<0,np.minimum(requested,after.energy),0.)
 after.energy+=gain-loss;active=(gain+loss)>0;y=divide(gain-loss,PAY*after.body)
 carried,delta,update,valid=observe(carried,y,active,motion_received,arm,write,end_block)
 carried[:,after.body==0]=0
 error=float(after.energy.sum()-s.energy.sum()+m['energy_spent'].sum()+m['energy_lost_with_tissue'].sum()-gain.sum()+loss.sum())
 assert abs(error)<1e-8 and np.isfinite(carried).all()
 m.update(gain=gain,loss=loss,paid_observed=active,delta=delta,update=update,valid_block=valid,whole_energy_error=error,motor=model.motor)
 return after,carried,m
