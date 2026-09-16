"""Run every bench_*.py sequentially and print a Markdown summary table.

    python3 benchmarks/scale/run_all.py [--only fields,exposure]

Results are saved per kernel in benchmarks/scale/results/*.json; the Markdown
table is written to benchmarks/scale/results/summary.md.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent


def table():
    rows = ['| Kernel | Size | Backend | Parameters | Kernel s | Setup s | Peak RSS GiB | Throughput |',
            '|---|---|---|---|---:|---:|---:|---|']
    for path in sorted((HERE / 'results').glob('*.json')):
        data = json.loads(path.read_text())
        for r in data['results']:
            params = ', '.join(f'{k}={v:,}' if isinstance(v, int) else f'{k}={v}' for k, v in r.get('params', {}).items())
            if r.get('status') != 'ok':
                rows.append(f"| {r.get('kernel', path.stem)} | {r['size']} | {r['backend']} | {params} | {r['status']} | | {r['peak_rss_bytes'] / 1024 ** 3:.2f} | |")
                continue
            rows.append(f"| {r['kernel']} | {r['size']} | {r['backend']} | {params} | {r['kernel_seconds']:.3f} | {r['setup_seconds']:.3f} | "
                        f"{r['peak_rss_bytes'] / 1024 ** 3:.2f} | {r['throughput_per_second']:,.0f} {r['unit']}/s |")
    return '\n'.join(rows) + '\n'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--only', help='Comma-separated kernel script suffixes, e.g. fields,exposure')
    parser.add_argument('--summary-only', action='store_true')
    args, extra = parser.parse_known_args()
    if not args.summary_only:
        only = set(args.only.split(',')) if args.only else None
        for script in sorted(HERE.glob('bench_*.py')):
            if only and script.stem[len('bench_'):] not in only:
                continue
            print('==>', script.name, flush=True)
            subprocess.run([sys.executable, str(script), *extra], check=False)
    summary = table()
    (HERE / 'results').mkdir(exist_ok=True)
    (HERE / 'results' / 'summary.md').write_text(summary)
    print(summary)


if __name__ == '__main__':
    main()
