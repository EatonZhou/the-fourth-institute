"""Ordinary local scalar-response moment estimator; no supplied direction.
Not a novel optimizer, or an unbiased organism-level causal derivative.
"""
import numpy as np
from local_learning.scalar_outcome_motor_learning.core import *
from local_learning.scalar_outcome_motor_learning.core import choose as base_choose,observe as base_observe
BETA=1/8

def choose(a,arm,eps,heading_noise):
 if arm!='spsa':return base_choose(a,arm,eps,heading_noise)
 a=a.copy();mean=unit(a[:2]);a[8:10]=.8*np.tanh(mean+SIGMA*eps)
 a[10:12]=eps/SIGMA;a[12:15]=0;a[15]=a[16];a[20]=a[17];a[19]=0
 return a,mean

def observe(a,y,active,motion_received,arm,write,end_block):
 if arm!='spsa':return base_observe(a,y,active,motion_received,arm,write,end_block)
 a=a.copy();a[19]=np.maximum(a[19],motion_received)
 delta=np.zeros_like(y);update=np.zeros((6,SIZE,SIZE));valid=np.zeros_like(active)
 if end_block:
  valid=active&(a[20]>.999)&(a[19]>.999)
  delta=np.where(valid,y-a[15],0.)
  if write:
   update[:2]=np.where(valid,BETA*(a[10:12]*delta-a[:2]),0.)
   a[:2]+=update[:2]
  a[18]=np.where(valid,delta,a[18])
 a[16]=np.where(active,y,a[16]);a[17]=np.where(active,1.,a[17])
 return a,delta,update,valid

# Same native, payment, transport; resolve only observer in this module namespace.

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
