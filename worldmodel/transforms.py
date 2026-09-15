"""Streaming adapters and example computations. Transform functions yield evidence records."""
import csv
import json
from .util import digest
from .model import instant


def raw_rows(context):
    """Yield (raw index, locator, row). JSON arrays have a deliberate memory cap."""
    format_name = context.parameters.get('format', 'jsonl')
    for index, _ in enumerate(context.raw_inputs):
        path = context.raw_path(index)
        with path.open(encoding=context.parameters.get('encoding', 'utf-8-sig'), newline='') as stream:
            if format_name == 'csv':
                reader = csv.DictReader(stream, delimiter=context.parameters.get('delimiter', ','))
                if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
                    raise ValueError('CSV requires distinct column headers')
                for number, row in enumerate(reader, 1):
                    if None in row or any(value is None for value in row.values()):
                        raise ValueError(f'Malformed CSV row {number}')
                    yield index, f'row:{number}', row
            elif format_name == 'jsonl':
                for number, line in enumerate(stream, 1):
                    if line.strip():
                        row = json.loads(line)
                        if not isinstance(row, dict):
                            raise ValueError('JSONL rows must be objects')
                        yield index, f'line:{number}', row
            elif format_name in ('json', 'census_json'):
                if path.stat().st_size > context.parameters.get('max_json_bytes', 64 * 1024 * 1024):
                    raise ValueError('JSON response exceeds in-memory limit; partition or stream upstream')
                payload = json.load(stream)
                if not isinstance(payload, list):
                    raise ValueError('JSON adapter expects an array')
                if format_name == 'census_json':
                    if not payload or not isinstance(payload[0], list):
                        raise ValueError('Census response requires header row')
                    header, payload = payload[0], payload[1:]
                    if len(header) != len(set(header)) or not all(isinstance(x, str) for x in header):
                        raise ValueError('Invalid Census headers')
                    for number, values in enumerate(payload, 1):
                        if not isinstance(values, list) or len(values) != len(header):
                            raise ValueError('Census row does not match header')
                        yield index, f'/{number}', dict(zip(header, values))
                else:
                    for number, row in enumerate(payload):
                        if not isinstance(row, dict):
                            raise ValueError('JSON rows must be objects')
                        yield index, f'/{number}', row
            else:
                raise ValueError(f'Unsupported format: {format_name}')


def normalize(context):
    """Explicit field mapping from tabular rows into the shared ontology.

    parameters: format, constants, columns (output field -> source column),
    dimensions (dimension -> source column), numeric_fields, null_values,
    id_columns. Source row order is a fallback identifier, never an entity identity.
    """
    p = context.parameters
    for index, locator, row in raw_rows(context):
        record = dict(p.get('constants', {}))
        for field, column in p.get('columns', {}).items():
            record[field] = row[column]
        if 'dimensions' in p:
            record['dimensions'] = {dimension: row[column] for dimension, column in p['dimensions'].items()}
        for field in p.get('numeric_fields', []):
            if record[field] in p.get('null_values', []):
                record[field] = None
                record['missing_reason'] = p.get('missing_reason', 'source_missing_or_suppressed')
            else:
                record[field] = float(record[field])
        identity = [row[column] for column in p.get('id_columns', [])] or [locator]
        record.setdefault('id', 'record:' + digest([context.definition['id'], context.raw_inputs[index], identity]))
        record['evidence'] = context.raw_evidence(locator, index)
        yield record


def evidence_jsonl(context):
    """Import already normalized records, attaching the actual raw-file reference."""
    for index, locator, row in raw_rows(context):
        if row.get('evidence'):
            row['upstream_evidence'] = row['evidence']
        row['evidence'] = context.raw_evidence(locator, index)
        yield row


def countries(context):
    """Fictional fixture adapter; two statistical observations per country."""
    for index, locator, row in raw_rows(context):
        for metric, unit in [('income', 'fictional_currency_per_person'),
                             ('life_satisfaction', 'points_0_10')]:
            yield {'kind': 'observation', 'id': f'fixture:{row["country"]}:{metric}',
                   'metric': metric, 'value': float(row[metric]), 'unit': unit,
                   'dimensions': {'country': row['country']},
                   'valid_from': '2024-01-01', 'valid_to': '2025-01-01',
                   'observed_at': '2025-01-01T00:00:00Z',
                   'methodology': 'Fictional fixture; not measured world data',
                   'evidence': context.raw_evidence(locator, index)}


def happiness(context):
    """Illustrative formula only, with exact record-level input provenance.

    100 * (w * min(income / scale, 1) + (1-w) * satisfaction / 10).
    This small demo groups in memory; production transforms should partition joins.
    """
    weight = context.parameters.get('income_weight', 0.5)
    scale = context.parameters.get('income_scale', 100000)
    if not isinstance(weight, (int, float)) or not 0 <= weight <= 1 or not scale > 0:
        raise ValueError('income_weight must be in [0,1] and income_scale positive')
    source = context.parameters.get('input_dataset', 'demo_countries')
    groups = {}
    for record in context.records(source):
        country = record['dimensions']['country']
        metrics = groups.setdefault(country, {})
        if record['metric'] in metrics:
            raise ValueError('Duplicate country/metric; reconcile explicitly before computation')
        metrics[record['metric']] = record
    for country, metrics in sorted(groups.items()):
        income, satisfaction = metrics['income'], metrics['life_satisfaction']
        if income['unit'] != 'fictional_currency_per_person' or satisfaction['unit'] != 'points_0_10':
            raise ValueError('Unexpected input units')
        if income['value'] < 0 or not 0 <= satisfaction['value'] <= 10:
            raise ValueError('Input outside expected domain')
        if (income['valid_from'], income['valid_to']) != (satisfaction['valid_from'], satisfaction['valid_to']):
            raise ValueError('Cannot combine different valid periods')
        value = 100 * (weight * min(income['value'] / scale, 1) + (1 - weight) * satisfaction['value'] / 10)
        yield {'kind': 'observation', 'id': f'happiness:{country}',
               'metric': 'rando_joes_happiness_index', 'value': round(value, 6), 'unit': 'index_points',
               'dimensions': {'country': country}, 'valid_from': income['valid_from'],
               'valid_to': income['valid_to'],
               'observed_at': max(income['observed_at'], satisfaction['observed_at'], key=instant),
               'methodology': 'Illustrative synthetic index; not a validated measure of happiness',
               'evidence': [{'input': context.input_ref(source), 'record_id': r['id']}
                            for r in (income, satisfaction)]}


def union(context):
    """Evidence-preserving materialization across input datasets; no truth selection."""
    for ref in context.inputs:
        for source in context.store.records(ref):
            record = dict(source)
            # Record identity is distinct from the entity the evidence describes.
            if source['kind'] == 'entity':
                record['entity_id'] = source.get('entity_id', source['id'])
            record['id'] = 'view:' + digest([ref, source['id']])
            record['evidence'] = [{'input': ref, 'record_id': source['id']}]
            yield record
