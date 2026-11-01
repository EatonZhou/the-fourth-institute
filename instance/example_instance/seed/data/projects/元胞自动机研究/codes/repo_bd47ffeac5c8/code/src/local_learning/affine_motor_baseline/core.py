"""Local instrumental-variable motor calibration in a deformable material CA.
Hidden actuator map lives exclusively in the physical adapter, never in readout.
"""
import numpy as np
from local_learning.embodied_action_learning.core import MotorBody,RULE,SIGMA,PAY,divide,advect,initial as old_initial
from local_learning.paid_gradient_motor_control.core import paid_gradient
NLMS_RATE=.5
BETA=1/32
RIDGE=1e-4

class CoupledBody(MotorBody):
    def __init__(self):
        super().__init__();self.coupling=np.eye(2)
    def face_rates(self,s,cue):
        original=self.motor
        self.motor=np.einsum('ij,jyx->iyx',self.coupling,original)
        try:return super().face_rates(s,cue)
        finally:self.motor=original

def rotation(angle):
    c,s=np.cos(angle),np.sin(angle)
    return np.array([[c,-s],[s,c]])

def terrain(sign):
    y,x=np.mgrid[:32,:32]
    return sign*(.6*np.sin(2*np.pi*(x-16)/32)+.4*np.sin(2*np.pi*(y-16)/32))

def initial(seed):
    s,_=old_initial(seed)
    return s,np.zeros((16,32,32))

def inv2(a,ridge=0.):
    aa=a.copy();aa[0,0]+=ridge;aa[1,1]+=ridge
    det=aa[0,0]*aa[1,1]-aa[0,1]*aa[1,0]
    out=np.array([[aa[1,1],-aa[0,1]],[-aa[1,0],aa[0,0]]])
    return np.divide(out,det,out=np.zeros_like(out),where=np.abs(det)>1e-20)

def mm(a,b):return np.einsum('ijyx,jkyx->ikyx',a,b)
def mv(a,b):return np.einsum('ijyx,jyx->iyx',a,b)
def unit(x):return divide(x,np.linalg.norm(x,axis=0))

def estimate(a):
    # Independent Gaussian innovations are instruments. No covariance of total
    # flow with the chosen action is interpreted as a causal plant coefficient.
    B=divide(a[4:8].reshape(2,2,32,32),a[12])
    H=divide(a[8:12].reshape(2,2,32,32),a[12])
    BT=B.swapaxes(0,1)
    return mm(mm(H,BT),inv2(mm(B,BT),RIDGE))

def readout(a,arm):
    d=unit(a[13:15]);J=a[:4].reshape(2,2,32,32) if arm.startswith("nlms") else estimate(a)
    if arm=='reflex':return d,J
    JT=J.swapaxes(0,1)
    return unit(mv(mm(inv2(mm(JT,J),RIDGE),JT),d)),J

def transition(model,s,a,arm,noise,field,feedback=True,write=True):
    mean,J=readout(a,arm)
    command=.8*np.tanh(mean+SIGMA*noise)
    model.motor=command
    after,m=model.step(s,np.zeros((2,32,32)),np.zeros((32,32)),np.zeros((32,32)),plastic=False)
    K=m['transport_weights'];den=advect(s.body,K)
    velocity=np.stack((K[0]-K[1],K[2]-K[3]))
    # Action and completed movement are same source/clock. No reward or motion
    # inferred when payment was denied. Proprioceptive K sensing is explicit.
    valid=(s.body>=1e-4)&(m['movement_paid']>0)
    local=a.copy()
    if write and arm.startswith('nlms'):
        # Source-registered executed command and completed consequence, same K.
        # Affine normalized LMS; a[:4]=J, a[4:6]=drift intercept.
        pred=mv(a[:4].reshape(2,2,32,32),command)+a[4:6]
        residual=velocity-pred
        step=NLMS_RATE*residual/(1+np.sum(command*command,axis=0))
        local[:4]=a[:4]+np.where(valid,(step[:,None]*command[None,:]).reshape(4,32,32),0.)
        local[4:6]=a[4:6]+np.where(valid,step,0.)
        local[12]=np.where(valid,(1-BETA)*a[12]+BETA,a[12])
    elif write and arm!='reflex':
        innovation_u=command-a[:2];innovation_v=velocity-a[2:4]
        moments=np.concatenate((command,velocity,
            (innovation_u[:,None]*noise[None,:]).reshape(4,32,32),
            (innovation_v[:,None]*noise[None,:]).reshape(4,32,32),np.ones((1,32,32))))
        local[:13]=np.where(valid,(1-BETA)*a[:13]+BETA*moments,a[:13])
    carried=divide(advect(s.body*local,K),den)
    if arm=='euler':carried[:13]=local[:13]  # same estimator, fixed Euler grid
    carried[13:]=divide(advect(s.body*a[13:],K),den)
    if arm!='euler':carried[:,after.body==0]=0
    observed=(after.body>=1e-4)&feedback
    requested=PAY*after.body*observed*np.abs(field)
    gain=np.where(field>0,np.minimum(requested,np.maximum(RULE.energy_capacity*after.body-after.energy,0)),0.)
    loss=np.where(field<0,np.minimum(requested,after.energy),0.)
    after.energy+=gain-loss;active=(gain+loss)>0
    y=divide(gain-loss,PAY*after.body)
    available=np.stack([(active&(np.roll(active,-1,axis)|np.roll(active,1,axis))) for axis in (-2,-1)])
    carried[13:15]=np.where(available,paid_gradient(y,active),carried[13:15]);carried[15]=np.where(active,1,carried[15])
    err=float(after.energy.sum()-s.energy.sum()+m['energy_spent'].sum()+m['energy_lost_with_tissue'].sum()-gain.sum()+loss.sum())
    assert abs(err)<1e-8 and np.isfinite(carried).all()
    m.update(mean=mean,motor=command,J=J,velocity=velocity,gain=gain,loss=loss,paid_observed=active,gradient_observed=available,map_sample=valid,whole_energy_error=err)
    return after,carried,m

def transition_with_material(model,s,a,arm,noise,field,feedback=True,write=True):
    old=model.coupling.copy()
    after,aa,m=transition(model,s,a,arm,noise,field,feedback,write)
    if old.ndim==4:
        model.coupling=divide(advect(s.body*old,m['transport_weights']),advect(s.body,m['transport_weights']))
    m['physical_coupling_before']=old
    return after,aa,m
