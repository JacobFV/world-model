'Two fictional countries; pipeline fixture, not real measurements.'
from worldmodel.source_helpers import raw_rows


def parse(context):
    """Materialize finite parsed rows with exact raw-file locators."""
    for index, locator, row in raw_rows(context):
        yield {'raw_index': index, 'locator': locator, 'row': row}


def _normalize_rows(context, rows):
    """Fictional fixture adapter; two statistical observations per country."""
    for index, locator, row in rows:
        for metric, unit in [('income', 'fictional_currency_per_person'), ('life_satisfaction', 'points_0_10')]:
            yield {'kind': 'observation', 'id': f"fixture:{row['country']}:{metric}", 'metric': metric, 'value': float(row[metric]), 'unit': unit, 'dimensions': {'country': row['country']}, 'valid_from': '2024-01-01', 'valid_to': '2025-01-01', 'observed_at': '2025-01-01T00:00:00Z', 'methodology': 'Fictional fixture; not measured world data', 'evidence': context.raw_evidence(locator, index)}


def run(context):
    """Normalize the declared parsed stage; do not reread original source bytes."""
    rows = ((item['raw_index'], item['locator'], item['row'])
            for item in context.stage_records('parsed'))
    yield from _normalize_rows(context, rows)


def countries(context):
    """Compatibility for direct Python callers predating stage execution."""
    yield from _normalize_rows(context, raw_rows(context))
