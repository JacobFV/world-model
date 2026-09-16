"""Parameter declarations, the Estimate record and the Estimator protocol."""
from dataclasses import dataclass, field, asdict
import math
import random
from typing import Protocol, runtime_checkable
from ..util import canonical, digest
from .linalg import backend

PRIORS = ('normal', 'uniform', 'lognormal', 'beta', 'half_normal')


@dataclass(frozen=True)
class ParameterSpec:
    """A declared parameter: unit, admissible bounds, prior and the process parameter it feeds."""
    name: str
    unit: str
    description: str = ''
    lower: float = None
    upper: float = None
    prior: dict = None
    maps_to: str = None
    source: str = None  # e.g. fit / external / assumed / data (model family declarations)

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name or not isinstance(self.unit, str):
            raise ValueError('Parameter requires a name and an explicit unit string')
        if self.lower is not None and self.upper is not None and self.lower >= self.upper:
            raise ValueError(f'{self.name}: lower bound must be below upper bound')
        if self.prior is not None:
            if not isinstance(self.prior, dict) or self.prior.get('distribution') not in PRIORS:
                raise ValueError(f'{self.name}: prior distribution must be one of {PRIORS}')

    @property
    def bounds(self):
        return (self.lower, self.upper)

    def within_bounds(self, value):
        return (value is not None and math.isfinite(value)
                and (self.lower is None or value >= self.lower) and (self.upper is None or value <= self.upper))

    def sample_prior(self, rng):
        prior = self.prior or {}
        kind = prior.get('distribution')
        for _ in range(10000):
            if kind == 'normal':
                value = rng.gauss(prior['mean'], prior['sd'])
            elif kind == 'half_normal':
                value = abs(rng.gauss(0, prior['sd']))
            elif kind == 'lognormal':
                value = math.exp(rng.gauss(prior['mu'], prior['sigma']))
            elif kind == 'beta':
                value = rng.betavariate(prior['alpha'], prior['beta'])
            elif kind == 'uniform' or kind is None:
                low = prior.get('lower', self.lower)
                high = prior.get('upper', self.upper)
                if low is None or high is None:
                    raise ValueError(f'{self.name}: sampling needs a proper prior or finite bounds')
                value = rng.uniform(low, high)
            if self.within_bounds(value):
                return value
        raise ValueError(f'{self.name}: prior mass outside bounds')

    def to_dict(self):
        return {k: v for k, v in asdict(self).items() if v is not None and v != ''}

    @classmethod
    def from_dict(cls, value):
        return cls(**value)


@dataclass
class Estimate:
    """Immutable-by-convention result of one estimator fit."""
    process_id: str
    component: str
    method: str
    parameters: dict
    standard_errors: dict
    cutoff: str
    vintage_policy: str
    sample: dict
    data_audit: dict
    parameter_specs: list
    covariance: dict = None
    posterior: dict = None
    diagnostics: dict = field(default_factory=dict)
    process_parameters: dict = field(default_factory=dict)
    limitations: list = field(default_factory=list)
    options: dict = field(default_factory=dict)

    def to_dict(self):
        body = {'schema': 'worldmodel.estimate/1', 'process_id': self.process_id, 'component': self.component,
                'method': self.method, 'parameters': self.parameters, 'standard_errors': self.standard_errors,
                'covariance': self.covariance, 'posterior': self.posterior, 'diagnostics': self.diagnostics,
                'sample': self.sample, 'cutoff': self.cutoff, 'vintage_policy': self.vintage_policy,
                'data_audit': self.data_audit, 'parameter_specs': self.parameter_specs,
                'process_parameters': self.process_parameters, 'limitations': self.limitations,
                'options': self.options, 'numeric_backend': backend(), 'causally_identified': False,
                'bounds_check': {spec['name']: ParameterSpec.from_dict(spec).within_bounds(self.parameters.get(spec['name']))
                                 for spec in self.parameter_specs}}
        canonical(body)
        return {**body, 'estimate_id': digest(body)}

    @classmethod
    def from_dict(cls, value):
        body = {k: v for k, v in value.items() if k != 'estimate_id'}
        expected = digest({k: v for k, v in body.items()})
        if value.get('estimate_id') is not None and value['estimate_id'] != expected:
            raise ValueError('Estimate content does not match estimate_id')
        keep = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in body.items() if k in keep})


@runtime_checkable
class Estimator(Protocol):
    process_id: str
    component: str
    frequency: str
    parameters: tuple
    requirements: tuple

    def fit(self, data, *, cutoff, vintage_policy='strict', **options):
        """Return an :class:`Estimate` using only information available by ``cutoff``."""

    def predict(self, estimate, frame, index):
        """Return ``{'mean', 'sd'}`` for ``frame`` row ``index`` from rows before it (and declared conditional inputs)."""


def seeded_rng(seed):
    if type(seed) is not int:
        raise ValueError('Seed must be an integer')
    return random.Random(seed)
