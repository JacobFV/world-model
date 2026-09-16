"""Subprocess scale-benchmark harness: wall time, peak RSS and throughput.

Each benchmark script defines ``KERNEL``, ``SIZES`` (ordered mapping of size name to
parameters), ``BACKENDS`` and ``run(size_name, params, backend) -> dict`` returning
at least ``units`` (int), ``unit`` (str) and ``kernel_seconds`` (float, excluding
synthetic scenario generation); optional ``setup_seconds`` and ``details``.
Scripts end with ``main(globals())``. Every (size, backend) runs in a fresh child
process with a watchdog that kills it above the RSS ceiling (default 20 GiB) or
the timeout (default 600 s), so one benchmark cannot exhaust the machine.

    python3 benchmarks/scale/bench_fields.py [--sizes small,medium] [--backends numpy]
"""
import argparse
import json
import os
from pathlib import Path
import platform
import resource
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
RESULTS = Path(__file__).resolve().parent / 'results'
GiB = 1024 ** 3


def _child(namespace, size, backend):
    sys.path.insert(0, str(ROOT))
    params = namespace['SIZES'][size]
    started = time.perf_counter()
    result = namespace['run'](size, params, backend)
    wall = time.perf_counter() - started
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    kernel = result.get('kernel_seconds') or wall
    payload = {'kernel': namespace['KERNEL'], 'size': size, 'backend': backend, 'params': params,
               'wall_seconds': round(wall, 3), 'kernel_seconds': round(kernel, 3),
               'setup_seconds': round(result.get('setup_seconds', wall - kernel), 3),
               'peak_rss_bytes': rss, 'units': result['units'], 'unit': result['unit'],
               'throughput_per_second': result['units'] / kernel if kernel else None,
               'details': result.get('details', {})}
    print('BENCHMARK_RESULT ' + json.dumps(payload), flush=True)


def _rss(pid):
    try:
        with open(f'/proc/{pid}/status', encoding='ascii') as stream:
            for line in stream:
                if line.startswith('VmRSS:'):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    return 0


def run_one(script, size, backend, *, max_rss_bytes=20 * GiB, timeout=600):
    env = {**os.environ, 'PYTHONPATH': str(ROOT) + os.pathsep + os.environ.get('PYTHONPATH', '')}
    process = subprocess.Popen([sys.executable, str(script), '--child', size, backend], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, env=env, cwd=str(ROOT))
    started = time.monotonic(); peak = 0; status = None
    while process.poll() is None:
        peak = max(peak, _rss(process.pid))
        if peak > max_rss_bytes:
            process.kill(); status = 'killed_rss'
        elif time.monotonic() - started > timeout:
            process.kill(); status = 'killed_timeout'
        time.sleep(0.1)
    stdout, stderr = process.communicate()
    for line in stdout.splitlines():
        if line.startswith('BENCHMARK_RESULT '):
            result = json.loads(line[len('BENCHMARK_RESULT '):])
            result['peak_rss_bytes'] = max(result['peak_rss_bytes'], peak)
            result['status'] = 'ok'
            return result
    return {'size': size, 'backend': backend, 'status': status or 'error', 'peak_rss_bytes': peak,
            'wall_seconds': round(time.monotonic() - started, 3), 'error': stderr[-2000:]}


def machine():
    cpus = os.cpu_count()
    try:
        memory = os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')
    except (ValueError, OSError):
        memory = None
    try:
        import numpy
        numpy_version = numpy.__version__
    except ImportError:
        numpy_version = None
    return {'platform': platform.platform(), 'machine': platform.machine(), 'python': platform.python_version(),
            'cpus': cpus, 'memory_bytes': memory, 'numpy': numpy_version}


def main(namespace):
    if len(sys.argv) >= 4 and sys.argv[1] == '--child':
        return _child(namespace, sys.argv[2], sys.argv[3])
    parser = argparse.ArgumentParser(description=namespace.get('__doc__'))
    parser.add_argument('--sizes', default=','.join(namespace['SIZES']))
    parser.add_argument('--backends', default=','.join(namespace['BACKENDS']))
    parser.add_argument('--max-rss-gb', type=float, default=20)
    parser.add_argument('--timeout', type=float, default=600)
    parser.add_argument('--no-save', action='store_true')
    args = parser.parse_args()
    script = Path(namespace['__file__']).resolve()
    results = []
    for size in args.sizes.split(','):
        for backend in args.backends.split(','):
            if (size, backend) in namespace.get('SKIP', ()):
                continue
            result = run_one(script, size, backend, max_rss_bytes=int(args.max_rss_gb * GiB), timeout=args.timeout)
            result.setdefault('kernel', namespace['KERNEL'])
            results.append(result)
            if result['status'] == 'ok':
                print(f"{namespace['KERNEL']:<24} {size:<7} {backend:<7} kernel {result['kernel_seconds']:>9.3f}s "
                      f"setup {result['setup_seconds']:>8.3f}s rss {result['peak_rss_bytes'] / GiB:>6.2f} GiB "
                      f"{result['throughput_per_second']:>14,.0f} {result['unit']}/s", flush=True)
            else:
                print(f"{namespace['KERNEL']:<24} {size:<7} {backend:<7} {result['status']}: {result.get('error', '')[-400:]}", flush=True)
    if not args.no_save:
        RESULTS.mkdir(parents=True, exist_ok=True)
        (RESULTS / f"{namespace['KERNEL']}.json").write_text(json.dumps({'machine': machine(), 'results': results}, indent=2) + '\n')
    return results
