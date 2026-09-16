"""Estimation and validation layer: process parameters from point-in-time data.

Pure Python reference implementations (NumPy used for large inversions only when
importable). Typical use::

    from worldmodel.estimation import ObservationSet, estimator_for, validate_process, attach_calibration
    data = ObservationSet.from_store(store, refs)
    estimator = estimator_for('interest_pass_through')
    estimate = estimator.fit(data, cutoff='2024-12-31', vintage_policy='strict')
    report = validate_process(estimator, data, train_end='2015-12-31', validation_end='2019-12-31', cutoff='2024-12-31')
    record = attach_calibration(registry, report)   # registry process 'validated' only if all criteria pass

See docs/estimation-and-validation.md.
"""
from .data import ObservationSet, SeriesRequirement, Frame, Series, LeakageError, VINTAGE_POLICIES
from .spec import ParameterSpec, Estimate, Estimator
from .acceptance import DEFAULT_CRITERIA, evaluate_criteria, default_criteria
from .validation import (rolling_origin_backtest, validate_process, diebold_mariano, baseline_forecast, score_forecasts,
                         publish_validation_report, publish_estimate, crps_gaussian, pinball_loss, brier_score,
                         calibration_curve, verify_report_id)
from .families import ESTIMATORS, estimator_for, components_for, fit_component, load_requirements, requirements_summary
from .registry import calibration_record, attach_calibration, load_calibrations, required_components
from .model_families import ModelFamilyEstimator, family_components
from .linalg import backend

__all__ = ['ObservationSet', 'SeriesRequirement', 'Frame', 'Series', 'LeakageError', 'VINTAGE_POLICIES',
           'ParameterSpec', 'Estimate', 'Estimator', 'DEFAULT_CRITERIA', 'evaluate_criteria', 'default_criteria',
           'rolling_origin_backtest', 'validate_process', 'diebold_mariano', 'baseline_forecast', 'score_forecasts',
           'publish_validation_report', 'publish_estimate', 'crps_gaussian', 'pinball_loss', 'brier_score',
           'calibration_curve', 'verify_report_id', 'ESTIMATORS', 'estimator_for', 'components_for', 'fit_component',
           'load_requirements', 'requirements_summary', 'calibration_record', 'attach_calibration', 'load_calibrations',
           'required_components', 'backend', 'ModelFamilyEstimator', 'family_components']
