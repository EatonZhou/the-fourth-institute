"""Hidden physical x-actuator reversal; unchanged local learner and prices."""
import numpy as np
from local_learning.material_action_credit.core import transition as source_transition
from local_learning.embodied_action_learning.core import MotorBody, mix

class ReversedMotorBody(MotorBody):
    def face_rates(self,s,cue):
        original=self.motor
        self.motor=original.copy();self.motor[1]*=-1
        try:return super().face_rates(s,cue)
        finally:self.motor=original

def transition(model,s,a,arm,noise,field,feedback,write):
    after,carried,m=source_transition(model,s,a,'donor_immediate',noise,field,feedback,write)
    if arm=='aligned_score':carried[:2]=mix(carried[:2],after.body)
    return after,carried,m
