"""Finite tissue transport with carried plastic concentrations and contact outcomes.

Environment owns cue/resource/hazard fields. Only cue is an external motion input;
the motion rule can also read the organism's old body, energy and internal state.
No density upper clipping, global direction, hidden relation, or future label.
"""
from dataclasses import dataclass, asdict, replace
import math
import numpy as np
from scipy.ndimage import gaussian_filter
from simulation.snapshot import save_snapshot, load_snapshot


@dataclass(frozen=True)
class Rule:
    transport_scheme: str = 'face_potential'
    dt: float = .1
    mobility: float = 1.
    cohesion: float = 3.
    pressure: float = 1.5
    interaction_sigma: float = 1.4
    cue_gain: float = 18.
    learning_rate: float = 1.
    eligibility_time: float = .8
    memory_time: float = 200.
    uptake_rate: float = .2
    energy_capacity: float = 2.
    basal_cost: float = .012
    movement_cost: float = .012
    starvation_rate: float = .15
    starvation_energy: float = .08
    food_sensor_scale: float = 5.
    injury_sensor_scale: float = 25.
    action_budget: str = 'soft'
    polarity_gain: float = 0.
    polarity_turn_rate: float = .04
    polarity_relaxation_rate: float = .5
    polarity_energy_gate: str = 'none'
    energy_transfer_conductivity: float = 0.
    energy_transfer_loss: float = .01


@dataclass
class State:
    body: np.ndarray
    energy: np.ndarray
    memory: np.ndarray
    eligibility: np.ndarray
    polarity: np.ndarray | None = None

    def clone(self):
        return State(*(x.copy() for x in (self.body,self.energy,self.memory,self.eligibility)),
                     None if self.polarity is None else self.polarity.copy())


def founder(size=32,mass=24.,kind='single'):
    y,x=np.mgrid[:size,:size]-size//2
    a=np.exp(-.5*((x/2.5)**2+(y/2.)**2))
    if kind=='double':a=np.exp(-.5*(((x-3)/1.6)**2+(y/1.8)**2))+.8*np.exp(-.5*(((x+3)/1.6)**2+(y/1.8)**2))
    a[a<1e-10]=0;a*=mass/a.sum()
    return State(a,a.copy(),np.zeros((2,size,size)),np.zeros((2,size,size)))


def gradient(a):
    return tuple((np.roll(a,-1,axis=ax)-np.roll(a,1,axis=ax))*.5 for ax in (-2,-1))


def flux_weights(vy,vx,dt):
    fractions=np.stack((np.maximum(vy,0),np.maximum(-vy,0),np.maximum(vx,0),np.maximum(-vx,0)))*dt
    total=fractions.sum(0);scale=np.minimum(1.,.8/np.maximum(total,1e-300))
    return fractions*scale,total>.8


def advect(extensive,weights):
    """Donor-cell conservative map shared by every extensive tissue quantity."""
    out=extensive*(1-weights.sum(0))
    for w,shift,axis in zip(weights,(1,-1,1,-1),(-2,-2,-1,-1)):
        out=out+np.roll(extensive*w,shift,axis=axis)
    return out


class MaterialBody:
    def __init__(self,rule=Rule()):
        self.rule=rule
        if rule.dt<=0 or rule.mobility<0 or rule.eligibility_time<=0 or rule.memory_time<=0:raise ValueError('invalid rule')
        if rule.transport_scheme not in ('central_velocity','face_potential'):raise ValueError('unknown transport rule')
        if rule.action_budget not in ('soft','donor_energy'):raise ValueError('unknown action budget')
        if not np.isfinite([rule.polarity_gain,rule.polarity_turn_rate,rule.polarity_relaxation_rate]).all():raise ValueError('nonfinite polarity rule')
        if rule.polarity_gain<0 or rule.polarity_relaxation_rate<0:raise ValueError('negative polarity rate')
        if rule.polarity_gain and rule.transport_scheme!='face_potential':raise ValueError('polarity requires face transport')
        if rule.polarity_energy_gate not in ('none','local_specific'):raise ValueError('unknown polarity energy gate')
        if rule.polarity_energy_gate!='none':
            if rule.transport_scheme!='face_potential':raise ValueError('polarity energy gate requires face transport')
            if rule.starvation_energy<=0:raise ValueError('positive starvation energy required by gate')
        if rule.action_budget=='donor_energy':
            if rule.transport_scheme!='face_potential':raise ValueError('donor energy budget requires face transport')
            if rule.basal_cost<0 or rule.movement_cost<0:raise ValueError('negative energy cost')
        if not np.isfinite([rule.energy_transfer_conductivity,rule.energy_transfer_loss]).all():raise ValueError('nonfinite energy transfer rule')
        if rule.energy_transfer_conductivity<0 or rule.energy_transfer_loss<0:raise ValueError('negative energy transfer coefficient')
        if rule.energy_transfer_conductivity and (rule.basal_cost<0 or rule.energy_capacity<0):raise ValueError('invalid transfer reserve/capacity')

    def motion(self,state,cue):
        if self.rule.transport_scheme=='face_potential':
            rates=self.face_rates(state,cue)
            return rates[0]-rates[1],rates[2]-rates[3]
        r=self.rule;a=state.body
        cohesion=gaussian_filter(a,r.interaction_sigma,mode='wrap',truncate=2.)
        body_potential=r.cohesion*cohesion-r.pressure*a*a
        by,bx=gradient(body_potential);cy,cx=gradient(cue)
        # A cell's own memory weights neighboring visible cues; memory gradients
        # do not themselves generate a force or leak field outcomes to motion.
        vy=r.mobility*np.tanh(by+r.cue_gain*(state.memory*cy).sum(0))
        vx=r.mobility*np.tanh(bx+r.cue_gain*(state.memory*cx).sum(0))
        return vy,vx

    def polarity_gate(self,state):
        """Read old local energy only; this gate does not modify carried p."""
        if self.rule.polarity_energy_gate=='none':return np.ones_like(state.body)
        specific=np.divide(state.energy,state.body,out=np.zeros_like(state.body),where=state.body>0)
        return np.clip(specific/self.rule.starvation_energy,0.,1.)

    def face_rates(self,state,cue):
        r=self.rule;a=state.body
        potential=r.cohesion*gaussian_filter(a,r.interaction_sigma,mode='wrap',truncate=2.)-r.pressure*a*a
        gate=self.polarity_gate(state) if r.polarity_gain and r.polarity_energy_gate!='none' else None
        rates=[]
        for direction,(shift,axis) in enumerate(((-1,-2),(1,-2),(-1,-1),(1,-1))):
            neighbor=np.roll(potential,shift,axis=axis)
            cue_delta=np.roll(cue,shift,axis=axis)-cue
            drive=neighbor-potential+r.cue_gain*(state.memory*cue_delta).sum(0)
            if r.polarity_gain:
                if state.polarity is None:raise ValueError('active polarity requires carried state')
                component=state.polarity[direction//2]
                if gate is not None:component=gate*component
                drive=drive+r.polarity_gain*component*(1 if direction%2==0 else -1)
            rates.append(r.mobility*np.maximum(np.tanh(drive),0))
        return np.stack(rates)

    def transfer_energy(self,state):
        """One concurrent four-neighbour operation, using only old rho/E.

        Donors cannot spend same-operation incoming energy, and recipient room
        does not borrow outgoing room. Each accepted unit costs eta at source.
        """
        r=self.rule;a=state.body;e=state.energy
        u=np.divide(e,a,out=np.zeros_like(e),where=a>0)
        directions=((-1,-2),(1,-2),(-1,-1),(1,-1))
        requested=[]
        for shift,axis in directions:
            neighbour=np.roll(a,shift,axis=axis)
            h=np.divide(a*neighbour,a+neighbour,out=np.zeros_like(a),where=(a>0)&(neighbour>0))
            requested.append(r.dt*r.energy_transfer_conductivity*h*np.maximum(u-np.roll(u,shift,axis=axis),0))
        requested=np.stack(requested);raw=requested.sum(0)
        available=np.maximum(e-r.dt*r.basal_cost*a,0)
        scale=np.minimum(1.,np.divide(available,(1+r.energy_transfer_loss)*raw,
                                      out=np.ones_like(e),where=raw>0))
        donor_flows=requested*scale

        def incoming(flows):
            return sum(np.roll(q,-shift,axis=axis) for q,(shift,axis) in zip(flows,directions))

        incoming_requested=incoming(donor_flows)
        room=np.maximum(r.energy_capacity*a-e,0)
        receiver_scale=np.minimum(1.,np.divide(room,incoming_requested,
                                               out=np.ones_like(e),where=incoming_requested>0))
        flows=np.stack([q*np.roll(receiver_scale,shift,axis=axis)
                        for q,(shift,axis) in zip(donor_flows,directions)])
        outgoing=flows.sum(0);received=incoming(flows);fee=r.energy_transfer_loss*outgoing
        residual=e-(1+r.energy_transfer_loss)*outgoing
        # At a fully drained donor, floating summation can cross zero by an ulp.
        # Record this correction separately; no finite deficit is hidden.
        correction=np.maximum(-residual,0)
        if np.any(correction>1e-14*np.maximum(1.,e)):raise ArithmeticError('transfer overdraw')
        energy=np.maximum(residual,0)+received
        diagnostics=dict(energy_transfer_requested_source=raw,
            energy_transfer_accepted_source=outgoing,energy_transfer_received=received,
            energy_transfer_donor_denied_source=raw-donor_flows.sum(0),
            energy_transfer_recipient_denied_source=donor_flows.sum(0)-outgoing,
            energy_transfer_dissipation=fee,energy_transfer_flows=flows,
            energy_transfer_roundoff_correction=correction,energy_after_transfer=energy,
            energy_transfer_balance_error=float(energy.sum()-e.sum()+fee.sum()))
        return replace(state,energy=energy),diagnostics

    def step(self,state,cue,resource,hazard,*,plastic=True,memoryless=False):
        if not self.rule.energy_transfer_conductivity:
            return self._body_step(state,cue,resource,hazard,plastic=plastic,memoryless=memoryless)
        transferred,transfer=self.transfer_energy(state)
        after,metrics=self._body_step(transferred,cue,resource,hazard,plastic=plastic,memoryless=memoryless)
        # Transfer fees occur at old donors; body payments occur after motion.
        # Their map sum is the complete expenditure, while components stay apart.
        metrics['energy_spent']=metrics['energy_spent']+transfer['energy_transfer_dissipation']
        metrics.update(transfer)
        metrics['energy_balance_error']=float(after.energy.sum()-state.energy.sum()
            -metrics['resource_consumed'].sum()+metrics['energy_spent'].sum()
            +metrics['energy_lost_with_tissue'].sum())
        return after,metrics

    def _body_step(self,state,cue,resource,hazard,*,plastic=True,memoryless=False):
        r=self.rule;a=state.body
        cue=np.broadcast_to(np.asarray(cue,float),state.memory.shape)
        resource=np.broadcast_to(np.asarray(resource,float),a.shape)
        hazard=np.broadcast_to(np.asarray(hazard,float),a.shape)
        if np.any(cue<0) or np.any(resource<0) or np.any(hazard<0):raise ValueError('negative world field')
        m=np.zeros_like(state.memory) if memoryless else state.memory
        used=State(a,state.energy,m,state.eligibility,state.polarity)
        if r.transport_scheme=='face_potential':
            weights=r.dt*self.face_rates(used,cue);total=weights.sum(0);capped=total>.8
            weights*=np.minimum(1.,.8/np.maximum(total,1e-300))
            # Cost per actual transported tissue-distance, not net cancellation.
            source_work=r.movement_cost*a*weights.sum(0)
        else:
            vy,vx=self.motion(used,cue);weights,capped=flux_weights(vy,vx,r.dt)
            source_work=r.dt*r.movement_cost*a*(vy*vy+vx*vx)
        intended_work=source_work.copy()
        budget_scale=np.ones_like(a)
        if r.action_budget=='donor_energy':
            # Reserve this donor's basal requirement from OLD energy. New food
            # cannot finance a move that has already taken place.
            available=np.maximum(state.energy-r.dt*r.basal_cost*a,0.)
            budget_scale=np.minimum(1.,np.divide(available,source_work,
                out=np.ones_like(a),where=source_work>0))
            weights*=budget_scale
            source_work=r.movement_cost*a*weights.sum(0)
        payload=(a[None],state.energy[None],a[None]*m,a[None]*state.eligibility)
        if state.polarity is not None:
            if state.polarity.shape!=(2,*a.shape):raise ValueError('two polarity components required')
            payload=(*payload,a[None]*state.polarity)
        transported=advect(np.concatenate(payload),weights)
        b,e=transported[:2];den=np.maximum(b,1e-300)
        nm=transported[2:4]/den;nz=transported[4:6]/den
        nm[:,b==0]=0;nz[:,b==0]=0
        requested_work=advect(source_work,weights)
        room=np.maximum(r.energy_capacity*b-e,0.)
        uptake=np.minimum(resource,np.minimum(r.dt*r.uptake_rate*b,room))
        energy_before_cost=e+uptake
        requested_cost=r.dt*r.basal_cost*b+requested_work
        spent=np.minimum(energy_before_cost,requested_cost)
        basal_requested=r.dt*r.basal_cost*b
        if r.action_budget=='soft':
            # Historical rule: payment does not constrain previous movement.
            basal_paid=np.minimum(energy_before_cost,basal_requested)
            movement_paid=np.minimum(np.maximum(energy_before_cost-basal_requested,0),requested_work)
        else:
            # The positive common transport map carries enough donor funding
            # for admitted motor work. A poor stationary recipient must not
            # consume incoming motor funding to pay its own basal deficit.
            movement_paid=np.minimum(energy_before_cost,requested_work)
            basal_paid=np.minimum(basal_requested,np.maximum(spent-movement_paid,0.))
        e=energy_before_cost-spent
        hazard_fraction=-np.expm1(-r.dt*hazard)
        injury=b*hazard_fraction
        specific=e/den
        starvation_fraction=-np.expm1(-r.dt*r.starvation_rate*np.clip(1-specific/r.starvation_energy,0,1))
        starvation=(b-injury)*starvation_fraction
        retained=(1-hazard_fraction)*(1-starvation_fraction)
        energy_lost=e*(1-retained)
        if plastic and not memoryless:
            rho=math.exp(-r.dt/r.eligibility_time)
            nz=rho*nz+(1-rho)*cue
            # Direct local ingestion and tissue injury, measured after motion.
            target=np.tanh(r.food_sensor_scale*uptake/(r.dt*den)-r.injury_sensor_scale*injury/(r.dt*den))
            contact=(uptake+injury)>1e-14
            rate=-np.expm1(-r.dt*r.learning_rate*nz)*contact
            nm=nm+rate*(target[None]-nm)
            nm*=math.exp(-r.dt/r.memory_time)
        if memoryless:nm[:]=0;nz[:]=0
        npolarity=None;polarity_error=0.
        if state.polarity is not None:
            polarity_error=float(np.max(abs(transported[6:8].sum((1,2))-(a[None]*state.polarity).sum((1,2)))))
            mixed=transported[6:8]/den
            # Autonomous motion state: local rotation and amplitude recovery.
            # No cue, contact, resource, hazard or world metadata enters here.
            angle=r.dt*r.polarity_turn_rate;c=math.cos(angle);s=math.sin(angle)
            npolarity=np.stack((c*mixed[0]-s*mixed[1],s*mixed[0]+c*mixed[1]))
            norm=np.hypot(*npolarity)
            amplitude=1+(norm-1)*math.exp(-r.dt*r.polarity_relaxation_rate)
            npolarity*=np.divide(amplitude,norm,out=np.zeros_like(norm),where=norm>0)
            npolarity[:,b*retained==0]=0
        next_state=State(b*retained,e*retained,nm,nz,npolarity)
        next_state.memory[:,next_state.body==0]=0;next_state.eligibility[:,next_state.body==0]=0
        metrics=dict(resource_consumed=uptake,mass_injury=injury,mass_starvation=starvation,
            energy_spent=spent,energy_lost_with_tissue=energy_lost,
            movement_requested=requested_work,movement_paid=movement_paid,movement_unpaid=requested_work-movement_paid,
            basal_requested=basal_requested,basal_paid=basal_paid,basal_unpaid=basal_requested-basal_paid,
            movement_intended_source=intended_work,movement_denied_source=intended_work-source_work,
            transport_weights=weights,source_budget_scale=budget_scale,
            mass_transport_error=float(b.sum()-a.sum()),
            energy_balance_error=float(next_state.energy.sum()-state.energy.sum()-uptake.sum()+spent.sum()+energy_lost.sum()),
            transport_memory_error=float(np.max(abs(transported[2:4].sum((1,2))-(a[None]*m).sum((1,2))))),
            transport_polarity_error=polarity_error,
            cfl_capped_fraction=float(capped.mean()),max_density=float(next_state.body.max()))
        return next_state,metrics

    def save(self,path,state,step,metadata=None):
        fields=dict(body=state.body,energy=state.energy,memory=state.memory,eligibility=state.eligibility)
        if state.polarity is not None:fields['polarity']=state.polarity
        save_snapshot(path,fields,
            step=step,metadata=dict(metadata or {},rule=asdict(self.rule),model='local_learning.material_transport.MaterialBody',
            model_time=step*self.rule.dt,dynamic_fields=list(fields),
            polarity_semantics='Carried autonomous motion state; rotates/relaxes even when plastic=False or learning_rate=0. Not acquired predictive memory.',
            internal_transport='body-weighted concentrations; same conservative donor map',external_fields='cue,resource,hazard owned by world'))


def load_state(path):
    s=load_snapshot(path,required_fields=('body','energy','memory','eligibility'))
    rule=dict(s.metadata['rule']);rule.setdefault('transport_scheme','central_velocity')
    polarity=s.fields.get('polarity')
    return MaterialBody(Rule(**rule)),State(*(s.fields[k].copy() for k in ('body','energy','memory','eligibility')),
        None if polarity is None else polarity.copy()),s
