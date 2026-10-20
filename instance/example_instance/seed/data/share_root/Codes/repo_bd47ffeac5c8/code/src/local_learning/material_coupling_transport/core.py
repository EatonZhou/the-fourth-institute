"""Intrinsic motor couplings co-transported with real material; no policy access."""
import numpy as np
from local_learning.material_action_map.core import *
from local_learning.material_action_map.core import transition as basic_transition
class MaterialCoupledBody(MotorBody):
    def __init__(self,sign):
        super().__init__()
        _,x=np.mgrid[:32,:32]
        angle=sign*(np.pi/2+np.pi/3*np.tanh((x-16)/2))
        c,s=np.cos(angle),np.sin(angle)
        self.coupling=np.array([[c,-s],[s,c]])
    def face_rates(self,state,cue):
        old=self.motor;self.motor=np.einsum('ijyx,jyx->iyx',self.coupling,old)
        try:return super().face_rates(state,cue)
        finally:self.motor=old

def transition(model,s,a,arm,noise,field,feedback=True,write=True):
    old_coupling=model.coupling.copy()
    after,aa,m=basic_transition(model,s,a,arm,noise,field,feedback,write)
    den=advect(s.body,m['transport_weights'])
    model.coupling=divide(advect(s.body*old_coupling,m['transport_weights']),den)
    # No births, so no invented actuator type. Each extensive component follows
    # exactly the same transport and proportional death as its actual material.
    m['physical_coupling_before']=old_coupling
    return after,aa,m
