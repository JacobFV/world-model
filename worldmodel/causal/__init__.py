"""Natural-experiment identification: staggered difference-in-differences and exposure tests.

Standard library only. Typical use::

    from worldmodel.causal import Panel, event_study, stacked_did, twfe_static
    panel = Panel(outcomes, cohorts, strata=None, clusters=state_of_county)
    result = event_study(panel, e_min=-5, e_max=5, control_group='not_yet_treated', bootstrap=999, seed=1)
    result['overall'], result['pre_trend']['wald']

Designs are registered first (``registration``), then run with ``study.run_did_design``, which
returns a result record carrying the identification label, assumptions, pre-trend and placebo
outcomes and a plain verdict. See docs/natural-experiments.md.
"""
from .panel import Panel
from .did import att_gt, aggregate_event_time, event_study, stacked_did, twfe_static, wald_test, multiplier_bootstrap
from .placebo import placebo_date_test, placebo_unit_test
from .registration import load_registration, registration_status, validate_registration
from .results import (DID_ASSUMPTIONS, FAILED_DIAGNOSTICS, IDENTIFIED_DID, LABELS, NOT_ESTIMABLE, PREDICTIVE,
                      build_result, did_verdict, evaluate_acceptance)
from .ranking import compare_rankings, paired_sign_flip, score_event, top_k_capture, top_k_lift
from .study import run_did_design

__all__ = ['Panel', 'att_gt', 'aggregate_event_time', 'event_study', 'stacked_did', 'twfe_static', 'wald_test',
           'multiplier_bootstrap', 'placebo_date_test', 'placebo_unit_test', 'load_registration',
           'registration_status', 'validate_registration', 'DID_ASSUMPTIONS', 'FAILED_DIAGNOSTICS', 'IDENTIFIED_DID',
           'LABELS', 'NOT_ESTIMABLE', 'PREDICTIVE', 'build_result', 'did_verdict', 'evaluate_acceptance',
           'compare_rankings', 'paired_sign_flip', 'score_event', 'top_k_capture', 'top_k_lift', 'run_did_design']
