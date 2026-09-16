"""Calibration records linking an Estimate and a ValidationReport to a registered process.

Records are recomputed from the report, never trusted from embedded flags:
the report digest and its final estimate digest are verified and the declared
acceptance criteria are re-evaluated before ``validated`` is set.
"""
from ..util import digest
from .acceptance import evaluate_criteria
from .spec import Estimate
from .validation import verify_report_id
from .families import load_requirements

SCHEMA = 'worldmodel.calibration/1'


def required_components(process_id):
    return list(load_requirements()['processes'].get(process_id, {}).get('required_components', []))


def calibration_record(report, *, criteria=None, estimate=None, report_ref=None, estimate_ref=None):
    verify_report_id(report)
    final = report['final_estimate']
    Estimate.from_dict(final)  # Raises when estimate content does not match its identifier.
    if estimate is not None:
        if estimate.get('estimate_id') != final['estimate_id']:
            raise ValueError('Supplied estimate is not the estimate validated by this report')
    if criteria is None:
        criteria = report.get('acceptance', {}).get('criteria')
    acceptance = evaluate_criteria(report, criteria)
    embedded = report.get('acceptance')
    if embedded and embedded.get('criteria_digest') == acceptance['criteria_digest'] and embedded.get('passed') != acceptance['passed']:
        raise ValueError('Embedded acceptance disagrees with re-evaluated criteria')
    record = {'schema': SCHEMA, 'process_id': report['process_id'], 'component': report['component'],
              'estimate_id': final['estimate_id'], 'report_id': report['report_id'],
              'estimate_ref': estimate_ref, 'report_ref': report_ref, 'cutoff': final['cutoff'],
              'vintage_policy': final['vintage_policy'], 'sample': final['sample'], 'parameters': final['parameters'],
              'standard_errors': final['standard_errors'], 'process_parameters': final['process_parameters'],
              'criteria': [dict(c) for c in criteria],
              'acceptance': {'passed': acceptance['passed'], 'results': acceptance['results'], 'criteria_digest': acceptance['criteria_digest']},
              'validated': bool(acceptance['passed'])}
    record['record_id'] = digest(record)
    return record


def attach_calibration(registry, report, *, process_id=None, criteria=None, report_ref=None, estimate_ref=None):
    record = calibration_record(report, criteria=criteria, report_ref=report_ref, estimate_ref=estimate_ref)
    target = process_id or record['process_id']
    required = required_components(target)
    if target != record['process_id'] and record['component'] not in required:
        raise ValueError(f'Component {record["component"]} is not declared for process {target}')
    registry.attach_calibration(record, process_id=target, required_components=required)
    return record


def load_calibrations(store, registry, report_refs, *, criteria=None):
    from ..artifacts import load_report
    records = []
    for ref in report_refs:
        records.append(attach_calibration(registry, load_report(store, ref), criteria=criteria, report_ref=ref))
    return records
