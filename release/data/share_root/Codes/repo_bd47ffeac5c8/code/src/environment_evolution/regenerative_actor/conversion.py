"""Local algebra and template support; contains no body/energy time integrator."""
from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True)
class Conversion:
    density_capacity: float = 1.
    endowment: float = .2
    synthesis_heat: float = .2
    template_heat: float = .02

    def __post_init__(self):
        values = (self.density_capacity, self.endowment, self.synthesis_heat, self.template_heat)
        if not all(np.isfinite(v) and v > 0 for v in values):
            raise ValueError('capacities, endowment and paid synthesis/template costs must be positive')

    @property
    def fuel_price(self):
        return self.endowment + self.synthesis_heat + self.template_heat


def divide(numerator, denominator):
    n, d = np.broadcast_arrays(numerator, denominator)
    return np.divide(n, d, out=np.zeros_like(n, dtype=float), where=d > 0)


def neighborhood(a):
    """Fixed periodic 3x3 binomial stencil; works on trailing spatial axes."""
    out = np.zeros_like(a, dtype=float)
    for dy, wy in ((-1, 1.), (0, 2.), (1, 1.)):
        for dx, wx in ((-1, 1.), (0, 2.), (1, 1.)):
            out += (wy*wx/16)*np.roll(a, (dy, dx), axis=(-2, -1))
    return out


def template(survivor, concentration):
    support = neighborhood(survivor)
    return divide(neighborhood(survivor*concentration), support), support


def admit(request, survivor, precursor, fuel, support, c=Conversion()):
    """Ordered birth allocation on given numbers, without advancing a state.

    Donor support licenses information copying only; no donor material is copied.
    All admitted material comes from P and all endowment/fees come from F.
    """
    arrays = np.broadcast_arrays(request, survivor, precursor, fuel, support)
    if not all(np.isfinite(a).all() and (a >= 0).all() for a in arrays):
        raise ValueError('admission arguments must be finite and nonnegative')
    request, survivor, precursor, fuel, support = arrays
    capacity = np.minimum(request, np.maximum(c.density_capacity-survivor, 0))
    licensed = np.where(support > 0, capacity, 0.)
    material = np.minimum(licensed, precursor)
    affordable = fuel/c.fuel_price
    affordable = np.where(affordable*c.fuel_price > fuel,
                          np.nextafter(affordable, 0.), affordable)
    born = np.minimum(material, affordable)
    return dict(positive_request=request, births=born,
                rejected_capacity=request-capacity,
                rejected_template=capacity-licensed,
                rejected_precursor=licensed-material,
                rejected_fuel=material-born,
                precursor_spent=born, birth_fuel=c.fuel_price*born,
                newborn_energy=c.endowment*born,
                synthesis_heat=c.synthesis_heat*born,
                template_heat=c.template_heat*born)
