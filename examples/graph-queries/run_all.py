"""Run every cross-dataset query against a unified index and save the outputs.

    python3 examples/graph-queries/run_all.py --save
    WORLD_MODEL_INDEX=data/world_evidence/politics.sqlite python3 examples/graph-queries/run_all.py
"""
import json
import subprocess
import sys
from pathlib import Path

import common

QUERIES = ['q1_sanctioned_to_listed_holders', 'q2_county_economy_hazard_assistance',
           'q3_commodity_production_trade_price', 'q4_legislator_bills_votes_money',
           'q5_sanctioned_vessel_to_port_network', 'q6_bank_filings_to_identity_to_group']


def main():
    options = common.parser(__doc__)
    options.add_argument('--only', action='append', default=[], choices=QUERIES)
    args = options.parse_args()
    here = Path(__file__).resolve().parent
    summary = []
    for name in (args.only or QUERIES):
        command = [sys.executable, str(here / (name + '.py')), '--index', str(args.index),
                   '--limit', str(args.limit)] + (['--save'] if args.save else [])
        print('\n=== ' + name, file=sys.stderr, flush=True)
        completed = subprocess.run(command, capture_output=True, text=True)
        if completed.returncode:
            print(completed.stderr[-4000:], file=sys.stderr)
            summary.append({'query': name, 'status': 'failed', 'error': completed.stderr.strip()[-400:]})
            continue
        payload = json.loads(completed.stdout)
        summary.append({'query': name, 'status': 'ok', 'datasets_used': payload.get('datasets_used'),
                        'counts': payload.get('counts')})
    print(json.dumps({'index': str(args.index), 'queries': summary}, indent=1, ensure_ascii=False))


if __name__ == '__main__':
    main()
