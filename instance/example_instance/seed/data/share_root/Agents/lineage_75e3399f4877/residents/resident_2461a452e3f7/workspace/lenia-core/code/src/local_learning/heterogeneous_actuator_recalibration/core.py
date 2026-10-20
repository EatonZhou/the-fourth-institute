"""Fixed half-plane actuation damage, hidden from the learning rules."""
import numpy as np
from local_learning.embodied_action_learning.core import MotorBody
from local_learning.local_actuator_recalibration.core import transition

def actuator_gain():
    g=np.ones((32,32));g[:16]=-1
    return g

class ReversedMotorBody(MotorBody):
    def face_rates(self,s,cue):
        original=self.motor;self.motor=original.copy();self.motor[1]*=actuator_gain()
        try:return super().face_rates(s,cue)
        finally:self.motor=original
