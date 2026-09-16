"""Bind estimated parameters to simulator configuration paths, with provenance.

Estimates (``Estimate`` objects or their verified dicts), calibration records
(``worldmodel.calibration/1``) and ``ProcessRegistry.calibrated_parameters``
mappings all carry ``process_parameters`` keyed by the ``maps_to`` paths declared
in ``requirements.json`` (for example ``mechanisms.interest.pass_through`` or
``edges[*].conductance``). :func:`parameter_bindings` normalizes any mix of these
into ``{path: binding}`` where each binding records the value and its source
(component, estimate_id, record_id, report_id, validated). Simulators apply the
bindings with :func:`apply_bindings` and report every parameter as ``estimated``
(with its record id) or ``assumed`` via :func:`parameter_provenance`.

Only verified content is bound: estimate and record digests are recomputed.
"""
from copy import deepcopy
import math

SCHEMA = 'worldmodel.parameter_bindings/1'

# Estimated parameters without a declared maps_to path. They are bound only when the
# estimate actually fitted them (a standard error is reported), so a closed-model fit
# does not claim an estimated zero decay/source term.
UNMAPPED_PATHS = {'field_diffusion_transport': {'decay_rate': 'fields.*.decay_rate', 'source_rate': 'fields.*.source_rate'},
                  'interest_pass_through': {'impact_pass_through': 'mechanisms.interest.impact_pass_through'},
                  'deposit_rate_pass_through': {'impact_pass_through': 'mechanisms.deposit_interest.impact_pass_through'}}


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _binding(value, component, estimate_id, record_id=None, report_id=None, validated=None, standard_error=None, kind='estimate'):
    return {'value': value, 'component': component, 'estimate_id': estimate_id, 'record_id': record_id,
            'report_id': report_id, 'validated': validated, 'standard_error': standard_error, 'source_kind': kind}


def _from_parameters(out, component, process_parameters, parameters, errors, **source):
    for path, value in sorted((process_parameters or {}).items()):
        if not _finite(value):
            continue
        name = next((n for n in (parameters or {}) if path.endswith('.' + n) or path == n), None)
        out.append((path, _binding(value, component, standard_error=(errors or {}).get(name), **source)))
    for name, path in UNMAPPED_PATHS.get(component, {}).items():
        value = (parameters or {}).get(name)
        if _finite(value) and (errors or {}).get(name) is not None:
            out.append((path, _binding(value, component, standard_error=errors[name], **source)))


def _collect(source, out):
    from .spec import Estimate
    from ..util import digest
    if source is None:
        return
    if isinstance(source, (list, tuple)):
        for item in source:
            _collect(item, out)
        return
    if isinstance(source, Estimate):
        body = source.to_dict()
        _from_parameters(out, body['component'], body['process_parameters'], body['parameters'], body['standard_errors'],
                         estimate_id=body['estimate_id'])
        return
    if not isinstance(source, dict):
        raise ValueError('Calibration source must be an Estimate, estimate dict, calibration record or calibrated_parameters mapping')
    schema = source.get('schema')
    if schema == 'worldmodel.estimate/1':
        Estimate.from_dict(source)  # Verifies estimate_id against content.
        if source.get('estimate_id') is None:
            raise ValueError('Estimate dict requires estimate_id')
        _from_parameters(out, source['component'], source.get('process_parameters'), source.get('parameters'),
                         source.get('standard_errors'), estimate_id=source['estimate_id'])
    elif schema == 'worldmodel.calibration/1':
        if source.get('record_id') != digest({k: v for k, v in source.items() if k != 'record_id'}):
            raise ValueError('Calibration record content does not match record_id')
        _from_parameters(out, source['component'], source.get('process_parameters'), source.get('parameters'),
                         source.get('standard_errors'), estimate_id=source.get('estimate_id'), record_id=source['record_id'],
                         report_id=source.get('report_id'), validated=source.get('validated'), kind='calibration_record')
    elif schema == SCHEMA:
        for path, binding in source.get('bindings', {}).items():
            out.append((path, dict(binding)))
    elif all(isinstance(v, dict) and 'value' in v for v in source.values()):
        for path, item in sorted(source.items()):  # ProcessRegistry.calibrated_parameters output.
            if _finite(item['value']):
                out.append((path, _binding(item['value'], item.get('component'), item.get('estimate_id'), kind='registry_calibrated_parameters')))
    else:
        raise ValueError('Unrecognized calibration source')


def parameter_bindings(*sources):
    """Merge sources into ``{'schema', 'bindings': {path: binding}}``; conflicting values raise."""
    pairs = []
    for source in sources:
        _collect(source, pairs)
    bindings = {}
    for path, binding in pairs:
        if path in bindings and bindings[path]['value'] != binding['value']:
            raise ValueError(f'Conflicting calibrated values for {path}')
        bindings.setdefault(path, binding)
    return {'schema': SCHEMA, 'bindings': dict(sorted(bindings.items()))}


def _tokens(path):
    tokens = []
    for part in path.split('.'):
        if part.endswith('[*]'):
            tokens.extend([part[:-3], '*'])
        else:
            tokens.append(part)
    return tokens


def _assign(node, tokens, value, create):
    """Set value at tokens; return the number of leaves assigned. Missing parents are skipped unless create."""
    if not tokens:
        return 0
    head, rest = tokens[0], tokens[1:]
    if head == '*':
        children = list(node.values()) if isinstance(node, dict) else node if isinstance(node, list) else []
        return sum(_assign(child, rest, value, create) for child in children if isinstance(child, (dict, list)))
    if not isinstance(node, dict):
        return 0
    if not rest:
        node[head] = value
        return 1
    if head not in node:
        if not create:
            return 0
        node[head] = {}
    return _assign(node[head], rest, value, create)


def apply_bindings(config, bindings, *, prefix='', rename=None, create=False, only=None):
    """Return ``(new_config, applied)``: bindings whose path starts with prefix set into a copy of config.

    ``rename`` maps a source path prefix to a config path prefix (for example
    ``{'mechanisms.interest.': 'interest.'}``). Paths whose parent object is absent
    are skipped (reported ``unbound``) unless ``create``. ``only`` restricts to paths
    for which the predicate returns true.
    """
    if isinstance(bindings, dict) and bindings.get('schema') != SCHEMA:
        bindings = parameter_bindings(bindings)
    elif not isinstance(bindings, dict):
        bindings = parameter_bindings(bindings)
    out = deepcopy(config)
    applied, unbound = {}, []
    for path, binding in bindings['bindings'].items():
        target = path
        for old, new in (rename or {}).items():
            if target.startswith(old):
                target = new + target[len(old):]
                break
        if not target.startswith(prefix) or (only is not None and not only(target)):
            continue
        count = _assign(out, _tokens(target[len(prefix):]), binding['value'], create)
        if count:
            applied[target] = dict(binding, source_path=path, assignments=count)
        else:
            unbound.append(path)
    return out, {'schema': SCHEMA, 'bindings': applied, 'unbound': sorted(unbound)}


def parameter_provenance(parameters, applied):
    """Per-parameter status: ``estimated`` (value, component, estimate_id, record_id) or ``assumed`` (value).

    ``parameters`` maps config paths to their current values; ``applied`` is the
    bindings record returned by :func:`apply_bindings` (or None).
    """
    bound = (applied or {}).get('bindings', {})
    report = {}
    for path, value in sorted(parameters.items()):
        binding = bound.get(path)
        if binding is None:
            wildcard = next((b for p, b in bound.items() if '*' in p and _matches(p, path)), None)
            binding = wildcard
        if binding is not None and binding['value'] == value:
            report[path] = {'status': 'estimated', 'value': value, 'component': binding.get('component'),
                            'estimate_id': binding.get('estimate_id'), 'record_id': binding.get('record_id'),
                            'validated': binding.get('validated')}
        else:
            report[path] = {'status': 'assumed', 'value': value}
    return report


def _matches(pattern, path):
    left, right = _tokens(pattern), path.replace('[', '.').replace(']', '').split('.')
    right = [p for p in right if p != '']
    return len(left) == len(right) and all(a == '*' or a == b for a, b in zip(left, right))


def numeric_leaves(node, prefix='', exclude=()):
    """Flatten numeric leaves of nested dicts into dotted paths (lists of dicts use their 'id' or index)."""
    out = {}
    if isinstance(node, dict):
        for key, value in node.items():
            path = f'{prefix}.{key}' if prefix else str(key)
            if key in exclude:
                continue
            out.update(numeric_leaves(value, path, exclude))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            label = value.get('id', index) if isinstance(value, dict) else index
            out.update(numeric_leaves(value, f'{prefix}[{label}]', exclude))
    elif _finite(node):
        out[prefix] = node
    return out
