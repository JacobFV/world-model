"""Decision layer: contracts, evidence-labelled rollouts, fragility search and gated optimization.

* :mod:`.contract` — the declarative decision contract (``contract.schema.json``) and
  its stdlib validator.
* :mod:`.evidence` — every mechanism's evidence status, re-derived from the published
  calibration reports; claims can only be lowered.
* :mod:`.kernels` / :mod:`.compile` — a contract compiles into an ordinary
  ``worldmodel.environments.Environment``; every rollout carries the status of each
  mechanism it used and an automatic label.
* :mod:`.fragility` — adversarial scenario search that reports failure regions, not
  optima.
* :mod:`.gated_rl` — bounded tabular policy optimization that refuses to run unless
  every mechanism is validated in the store.

None of it establishes a causal response. See docs/decision-layer.md.
"""
from .contract import ContractError, load_contract, validate_contract
from .evidence import EvidenceIndex, load_attempt_data, recommendation_label, resolve_mechanisms
from .compile import CompiledDecision, Policy, compile_contract, policies_from, recommend
from .fragility import stress_test
from .gated_rl import RefusedToOptimize, optimization_gate, optimize

__all__ = ['ContractError', 'load_contract', 'validate_contract', 'EvidenceIndex', 'load_attempt_data',
           'recommendation_label', 'resolve_mechanisms', 'CompiledDecision', 'Policy', 'compile_contract', 'policies_from',
           'recommend', 'stress_test', 'RefusedToOptimize', 'optimization_gate', 'optimize']
