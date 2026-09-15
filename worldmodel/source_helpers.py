"""Reusable record readers, format validators and local-pipeline compatibility.

Dataset interpretation belongs to data/<id>/pipeline.py. These helpers contain
no dataset dispatch branches; shared identifier/date/series vocabulary and
format-preserving union/import operations remain reusable infrastructure.
"""
import csv
from datetime import date, timedelta
import importlib.util
import json
import re
import sys
from .util import digest, slug

STATE_FIPS = dict(zip('AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY AS GU MP PR VI'.split(),
 '01 02 04 05 06 08 09 10 11 12 13 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 44 45 46 47 48 49 50 51 53 54 55 56 60 66 69 72 78'.split()))

SERIES={'DFF':('policy_rate','percent'),'DGS2':('treasury_yield_2y','percent'),
 'DGS10':('treasury_yield_10y','percent'),'T10YIE':('breakeven_inflation_10y','percent'),
 'DCOILWTICO':('oil_price','USD/barrel'),'CPIAUCSL':('consumer_price_index','index_1982_1984_100')}

def next_day(value):return (date.fromisoformat(value)+timedelta(days=1)).isoformat()

def month_end(value):
    d=date.fromisoformat(value)
    return date(d.year+int(d.month==12),1 if d.month==12 else d.month+1,1).isoformat()

def _iso_date(value):
    if not value: return None
    value = str(value)
    if len(value) == 8 and value.isdigit():
        return date(int(value[:4]), int(value[4:6]), int(value[6:])).isoformat()
    date.fromisoformat(value[:10])
    return value

def _code(value, pattern, name):
    value = str(value or '').strip().upper()
    if not re.fullmatch(pattern, value): raise ValueError('Invalid published '+name)
    return value

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

def run_local_pipeline(context, dataset=None, function='run'):
    """Compatibility entrypoint for callers of the former central adapters.

    New stage execution loads its catalog-local entrypoint directly. This path
    resolves packaged built-in adapters for old Python imports and tests only.
    """
    from .resources import resource_roots
    dataset = slug(dataset or context.definition['id'])
    path = resource_roots()['catalog'] / dataset / 'pipeline.py'
    if not path.is_file():
        raise ValueError('No dataset-local pipeline: ' + dataset)
    source = path.read_bytes()
    name = '_worldmodel_compat_' + dataset + '_' + digest([str(path), source.decode('utf-8')])
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        # Compile the captured bytes directly; timestamp-based pyc caches must
        # not substitute a stale dataset implementation.
        exec(compile(source, str(path), 'exec'), module.__dict__)
        sys.modules[name] = module
    entrypoint = getattr(module, function, None)
    if not callable(entrypoint):
        raise ValueError('Dataset pipeline has no callable ' + function)
    return entrypoint(context)
