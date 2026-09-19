"""Save unpublished assay reports on a compute host, and publish them where the data lives.

Standard library only, so a checkout without the ``embed`` extra can publish.
"""
import json
from pathlib import Path

from ..util import digest

REPORTS = 'embedding_reports'


def save_reports(path, reports):
    """Write unpublished reports with what publication needs (inputs, parameters, entrypoint)."""
    Path(path).write_text(json.dumps([{'target': r['target'], 'report': r['report'], 'publication': r['publication']}
                                      for r in reports]))


def publish_saved(store, path):
    """Publish reports saved on a compute host. The report id is re-derived before anything is written."""
    from ..artifacts import publish_report
    out = []
    for item in json.loads(Path(path).read_text()):
        report, publication = item['report'], item['publication']
        if report['report_id'] != digest({k: v for k, v in report.items() if k != 'report_id'}):
            raise ValueError(f'Report for {item["target"]} does not match its report_id')
        ref = publish_report(store, REPORTS, report, publication['parameters'], inputs=publication['inputs'],
                             entrypoint=publication['entrypoint'])
        out.append({'target': item['target'], 'ref': ref, 'validated': report['validated']})
    return out
