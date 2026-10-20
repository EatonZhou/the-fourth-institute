"""One physical step, stage-separated records, then paid old-recipient delivery.

Records describe local mass-share contact/payment, not causal organism returns.
The physical host and material intention implementations remain immutable.
"""
from pathlib import Path
import json
import numpy as np
from environment_evolution.material_action_response.core import MaterialActionBody, DIRECTIONS
from local_learning.material_transport import advect

SCHEMA='local-terminal-feedback-v1'
FIELDS=('registered_mass','contact','motor_paid','basal_paid','action_y_time','action_x_time','exposure_mass_time')
ACTION_CODES=('none','gradient','euler_shear_x','material_shear_x')
PACKET_BYTES=256  # 4 action bins x 7 float64 fields + 32-byte identity/clock header
PRICE_PER_BYTE_HOP=1e-7
PACKET_PRICE=PACKET_BYTES*PRICE_PER_BYTE_HOP

def divide(a,b):
    return np.divide(a,b,out=np.zeros_like(a),where=b>0)

def send_once(packets, survivor, old_budget):
    """Pure settlement, no body advance. packets shape(stage,code,field,y,x).

    Each source/stage envelope is split by final old-neighbour mass. Each
    nonempty link carries the finite 256-byte schema and pays one full price.
    No retry/reallocation of an unpaid share; actor then rule, +y,-y,+x,-x.
    """
    shape=survivor.shape
    budget=old_budget.copy();incoming=np.zeros_like(packets[0]);paid=np.zeros(shape)
    fractions=np.zeros((2,4,*shape));accepted=np.zeros((2,4,*shape),dtype=bool)
    no_receiver=np.zeros_like(packets);unpaid=np.zeros_like(packets)
    requested_at_dest=np.zeros(shape);generated=packets.copy()
    eligible=np.stack([np.roll(survivor,-d,axis=ax) for d,ax in DIRECTIONS])
    shares=divide(eligible,eligible.sum(0))
    for stage in range(2):
        active=packets[stage,:,0].sum(0)>0
        no_receiver[stage]=packets[stage]*(eligible.sum(0)==0)
        for direction,(delta,axis) in enumerate(DIRECTIONS):
            fraction=shares[direction]*active
            fractions[stage,direction]=fraction
            wants=np.roll(fraction>0,delta,axis=axis)
            requested_at_dest+=wants*PACKET_PRICE
            can=wants & (budget>=PACKET_PRICE) & (survivor>0)
            charge=can*PACKET_PRICE;budget-=charge;paid+=charge
            accepted[stage,direction]=np.roll(can,-delta,axis=axis)
            actual=fraction*accepted[stage,direction]
            incoming+=np.roll(packets[stage]*actual,delta,axis=axis)
            unpaid[stage]+=packets[stage]*(fraction-actual)
    undelivered=no_receiver+unpaid
    error=np.max(np.abs(generated.sum(axis=(0,3,4))-undelivered.sum(axis=(0,3,4))-incoming.sum(axis=(-2,-1))))
    if error>1e-8:raise ArithmeticError('terminal delivery not conservative')
    return dict(generated=generated,incoming=incoming,undelivered_no_receiver=no_receiver,
                undelivered_unpaid=unpaid,fractions=fractions,accepted=accepted,
                paid=paid,requested=requested_at_dest,budget_before=old_budget.copy(),
                budget_after=budget,delivery_balance_error=float(error))

class TerminalFeedbackBody:
    def __init__(self,body):
        self.body=body
        shape=body.state['body'].shape
        self.direct=np.zeros((4,7,*shape))
        self.message=np.zeros_like(self.direct)
        self.unread=np.zeros_like(self.direct)
        self.message_support=np.zeros(shape)
        self.unit=0;self.unit_start=body.clock;self.registered=False
        self.consumed_clock=body.clock;self.last_completion_clock=body.clock
        self.registered_initial=0.;self.terminal_mass=0.;self.communication_heat=0.
        self.generated_total=np.zeros((4,7));self.delivered_total=np.zeros((4,7))
        self.undelivered_total=np.zeros((4,7));self.message_lost_total=np.zeros((4,7))

    @classmethod
    def from_formation(cls,*args,**kwargs):return cls(MaterialActionBody.from_formation(*args,**kwargs))

    @property
    def state(self):return self.body.state
    @property
    def clock(self):return self.body.clock

    def begin_unit(self,action_code):
        """Start a new finite local registration on CURRENT old material.

        Callers first consume the preceding completion if needed. Old direct
        records are explicitly closed here; message storage survives and is
        never retransmitted. A fresh unit legitimately registers current births.
        The initial pilot starts one unit only, lasting its whole 256-step path.
        """
        if self.last_completion_clock>self.consumed_clock:
            raise RuntimeError('consume or explicitly discard preceding completion first')
        codes=np.asarray(action_code)
        if not np.all(np.isin(codes,range(4))):
            raise ValueError('action code must be an integer in0..3')
        if codes.ndim==0:codes=np.full(self.state['body'].shape,int(codes))
        if codes.shape!=self.state['body'].shape or not np.all(np.isin(codes,range(4))):
            raise ValueError('action code must be an integer in0..3 on the old grid')
        closed=self.direct.copy();self.direct[:]=0
        for c in range(4):self.direct[c,0]=self.state['body']*(codes==c)
        self.unit+=1;self.unit_start=self.clock;self.registered=True
        self.registered_initial=float(self.state['body'].sum());self.terminal_mass=0.
        return closed

    def observe_local(self):
        return {**self.body.observe_local(),'direct':self.direct.copy(),'message':self.message.copy(),
                'message_support':self.message_support.copy(),'completion_clock':self.last_completion_clock}

    def consume_feedback(self,*,decision_clock):
        """Atomic once-only read; sources strictly precede the next decision.

        direct is cumulative live own-record, message_unread cumulative other
        received evidence since last read (minus subsequent recipient deaths).
        Neither field is a collection of independent samples.
        """
        if decision_clock!=self.clock+1 or decision_clock<=self.last_completion_clock:
            raise ValueError('decision must be the next physical clock')
        if self.consumed_clock>=self.last_completion_clock:return None
        result=dict(source_clock=self.last_completion_clock,unit_opaque=self.unit,
                    direct=self.direct.copy(),message_unread=self.unread.copy(),
                    direct_support=self.direct[:,0].sum(0),message_support=self.message_support.copy())
        self.unread[:]=0;self.consumed_clock=self.last_completion_clock
        return result

    def step(self,action,*,permit):
        if not self.registered:raise RuntimeError('register an action unit before stepping')
        # The immutable host consumes the sole native permit. Its actual maps
        # retain actor pre-rule fields; no prefix replay or future sample used.
        maps,checks=self.body.step(action,permit=permit)
        w=maps['K_weights'];t=maps['transported_body'];a=maps['actor_body_after_actor'];s=maps['survivor']
        actor_keep=divide(a,t);rule_keep=divide(s,a)
        moved=advect(self.direct,w)
        frac=divide(moved[:,0],t)
        dt=self.body.model.dt
        # Source requested actions belong to that actual step; not future flux.
        for k,ch in ((4,0),(5,1)):
            moved[:,k]+=advect(self.direct[:,0]*np.asarray(action)[ch]*dt,w)
        moved[:,1]+=frac*maps['uptake']
        moved[:,2]+=frac*maps['actor_movement_paid']
        moved[:,3]+=frac*maps['actor_basal_paid']
        moved[:,6]+=moved[:,0]*dt
        # Capture completed records immediately before each distinct deletion.
        actor_terminal=moved*(1-actor_keep)
        after_actor=moved*actor_keep
        rule_terminal=after_actor*(1-rule_keep)
        self.direct=after_actor*rule_keep
        packets=np.stack((actor_terminal,rule_terminal))
        self.terminal_mass+=float(packets[:,:,0].sum())
        transported_message=advect(self.message,w)
        lost_actor=transported_message*(1-actor_keep)
        lost_rule=transported_message*actor_keep*(1-rule_keep)
        self.message=transported_message*maps['keep']
        self.unread=advect(self.unread,w)*maps['keep']
        self.message_support=advect(self.message_support,w)*maps['keep']
        # Conservative OLD-energy provenance: charge all actual actor spending
        # to old transported E first, then both real deletion factors. Current
        # uptake and newborn endowment are not in this available budget.
        old_surviving_energy=np.maximum(advect(maps['old_energy'],w)-maps['actor_energy_spent'],0)*maps['keep']
        physical_own_energy=maps['actor_energy_after_actor']*rule_keep
        old_budget=np.minimum(old_surviving_energy,physical_own_energy)
        receipt=send_once(packets,s,old_budget)
        self.state['energy']-=receipt['paid'];self.state['heat']+=receipt['paid']
        self.body.model.validate(self.state)
        self.message+=receipt['incoming'];self.unread+=receipt['incoming']
        got=receipt['incoming'][:,0].sum(0)>0
        self.message_support=np.where(got,s,self.message_support)
        self.communication_heat+=float(receipt['paid'].sum())
        self.generated_total+=packets.sum(axis=(0,3,4))
        self.delivered_total+=receipt['incoming'].sum(axis=(-2,-1))
        self.undelivered_total+=(receipt['undelivered_no_receiver']+receipt['undelivered_unpaid']).sum(axis=(0,3,4))
        self.message_lost_total+=(lost_actor+lost_rule).sum(axis=(-2,-1))
        self.last_completion_clock=self.clock
        receipt.update(actor_keep=actor_keep,rule_keep=rule_keep,
                       direct_after=self.direct.copy(),message_after=self.message.copy(),
                       message_support=self.message_support.copy(),message_lost_actor=lost_actor,
                       message_lost_rule=lost_rule,receiver_old_survivor=s.copy(),
                       receiver_old_energy=old_budget,physical_own_energy=physical_own_energy,
                       postclock=self.clock,unit_opaque=self.unit,unit_start_clock=self.unit_start,
                       unknown_terminal_mass=0.,
                       initial_registration_error=float(self.direct[:,0].sum()+self.terminal_mass-self.registered_initial),
                       stored_message_error=float(np.max(abs(self.message.sum(axis=(-2,-1))+self.message_lost_total-self.delivered_total))))
        # Return the unchanged host fields plus distinctly named post-fee state.
        maps['communication_paid']=receipt['paid'];maps['energy_after_communication']=self.state['energy'].copy()
        checks['communication_energy_error']=float((self.state['energy']-maps['energy']).sum()+receipt['paid'].sum())
        checks['terminal_balance_error']=receipt['delivery_balance_error']
        return maps,receipt,checks

    def save(self,path):
        from environment_evolution.native_investment_action.run import npzfast
        final=Path(path)
        if final.exists():raise FileExistsError(final)
        p=final.with_name(final.name+'.pending');p.mkdir(parents=True,exist_ok=False)
        self.body.save(p/'body.npz')
        arrays={k:getattr(self,k) for k in ('direct','message','unread','message_support','generated_total','delivered_total','undelivered_total','message_lost_total')}
        npzfast(p/'records.npz',arrays)
        meta={k:getattr(self,k) for k in ('unit','unit_start','registered','consumed_clock','last_completion_clock','registered_initial','terminal_mass','communication_heat')}
        meta.update(schema=SCHEMA,packet_bytes=PACKET_BYTES,price_per_byte_hop=PRICE_PER_BYTE_HOP,clock=self.clock)
        (p/'metadata.json').write_text(json.dumps(meta,indent=2)+'\n')
        p.rename(final)

    @classmethod
    def restore(cls,path):
        p=Path(path);meta=json.loads((p/'metadata.json').read_text())
        if meta.pop('schema')!=SCHEMA or meta.pop('packet_bytes')!=PACKET_BYTES or meta.pop('price_per_byte_hop')!=PRICE_PER_BYTE_HOP:
            raise ValueError('schema/price mismatch')
        obj=cls(MaterialActionBody.restore(p/'body.npz'))
        if meta.pop('clock')!=obj.clock:raise ValueError('clock mismatch')
        with np.load(p/'records.npz',allow_pickle=False) as z:
            for k in z.files:setattr(obj,k,z[k].copy())
        obj.__dict__.update(meta)
        if obj.consumed_clock>obj.last_completion_clock or obj.last_completion_clock!=obj.clock:
            raise ValueError('completion chronology mismatch')
        return obj
