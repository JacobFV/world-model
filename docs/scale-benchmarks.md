# Scale benchmarks

Measured on this machine: NVIDIA GB10, Linux aarch64, 20 cores, 121 GB RAM,
Python 3.12, numpy 2.5.3 (optional `fast` extra). All kernels are single-process;
numpy work is effectively single-threaded.

## Method

`benchmarks/scale/bench_<kernel>.py` generates a synthetic scenario for three sizes
and runs every (size, backend) pair in a fresh child process
(`benchmarks/scale/harness.py`). A watchdog kills any run above 20 GiB RSS or 10
minutes. Each run reports:

* kernel seconds, excluding synthetic scenario generation and loading, which is
  reported separately as setup;
* peak RSS of the child, from `getrusage` and the watchdog's `/proc` samples;
* throughput in each kernel's natural work unit.

```bash
python3 benchmarks/scale/run_all.py                 # all kernels, writes results/*.json + summary.md
python3 benchmarks/scale/bench_fields.py --sizes small --backends numpy,python
```

Where a pure-Python size would exceed the time budget, it is skipped (`SKIP` in the
script). National-horizon figures, such as 3,650 coupled-economy steps, are
extrapolated from measured per-step time, because the benchmarks deliberately stay
under 10 minutes.

## Results

<!-- RESULTS -->

### coupled_economy

| Size | Backend | Parameters | Kernel s | Setup s | Peak RSS GiB | Throughput |
|---|---|---|---:|---:|---:|---|
| small | numpy | firms=1,000, households=10,000, steps=30 | 0.09 | 0.05 | 0.05 | 3.61M actor-steps/s; 3 ms/step, 3,650 steps ≈ 0.00 h |
| small | python | firms=1,000, households=10,000, steps=30 | 4.07 | 0.04 | 0.06 | 81.1k actor-steps/s; 136 ms/step, 3,650 steps ≈ 0.14 h |
| medium | numpy | firms=10,000, households=100,000, steps=30 | 1.11 | 0.73 | 0.19 | 2.98M actor-steps/s; 37 ms/step, 3,650 steps ≈ 0.04 h |
| medium | python | firms=10,000, households=100,000, steps=30 | 68.12 | 0.50 | 0.25 | 48.4k actor-steps/s; 2271 ms/step, 3,650 steps ≈ 2.30 h |
| large | numpy | firms=100,000, households=1,000,000, steps=60 | 22.60 | 9.18 | 1.57 | 2.92M actor-steps/s; 377 ms/step, 3,650 steps ≈ 0.38 h |

### fields

| Size | Backend | Parameters | Kernel s | Setup s | Peak RSS GiB | Throughput |
|---|---|---|---:|---:|---:|---|
| small | numpy | cells=100,000, edges=400,000 | 0.12 | 0.09 | 0.09 | 67.23M component updates/s |
| small | python | cells=100,000, edges=400,000 | 2.10 | 0.14 | 0.17 | 3.81M component updates/s |
| medium | numpy | cells=1,000,000, edges=4,000,000 | 1.94 | 0.28 | 0.60 | 41.32M component updates/s |
| medium | python | cells=1,000,000, edges=4,000,000 | 63.07 | 0.83 | 1.44 | 1.27M component updates/s |
| large | numpy | cells=10,000,000, edges=40,000,000 | 34.39 | 1.54 | 5.48 | 23.26M component updates/s |

### spatial_store

| Size | Backend | Parameters | Kernel s | Setup s | Peak RSS GiB | Throughput |
|---|---|---|---:|---:|---:|---|
| small | numpy | cells=100,000 | 4.14 | 0.30 | 0.23 | 24.2k cells (import+load+write)/s |
| medium | numpy | cells=1,000,000 | 51.53 | 2.76 | 1.57 | 19.4k cells (import+load+write)/s |
| large | numpy | cells=5,000,000 | 359.75 | 15.47 | 7.69 | 13.9k cells (import+load+write)/s |

### exposure

| Size | Backend | Parameters | Kernel s | Setup s | Peak RSS GiB | Throughput |
|---|---|---|---:|---:|---:|---|
| small | python | entities=10,000, obligations=100,000 | 0.23 | 0.04 | 0.09 | 11.42M clearing-updates/s |
| small | numpy | entities=10,000, obligations=100,000 | 0.03 | 0.05 | 0.05 | 78.02M clearing-updates/s |
| medium | python | entities=100,000, obligations=1,000,000 | 3.54 | 0.18 | 0.57 | 9.32M clearing-updates/s |
| medium | numpy | entities=100,000, obligations=1,000,000 | 0.29 | 0.10 | 0.19 | 113.60M clearing-updates/s |
| large | python | entities=1,000,000, obligations=10,000,000 | 129.25 | 1.25 | 5.40 | 2.72M clearing-updates/s |
| large | numpy | entities=1,000,000, obligations=10,000,000 | 5.71 | 0.42 | 1.62 | 61.67M clearing-updates/s |

### transport

| Size | Backend | Parameters | Kernel s | Setup s | Peak RSS GiB | Throughput |
|---|---|---|---:|---:|---:|---|
| small | python | side=160, queries=4 | 4.47 | 0.36 | 0.16 | 187.2k label-expansions/s |
| medium | python | side=500, queries=2 | 26.00 | 4.34 | 1.38 | 160.8k label-expansions/s |
| large | python | side=1,120, queries=1 | 122.14 | 22.35 | 7.21 | 115.1k label-expansions/s |

### checkpoints

| Size | Backend | Parameters | Kernel s | Setup s | Peak RSS GiB | Throughput |
|---|---|---|---:|---:|---:|---|
| small | chunked_json | json_mb=10, array_mb=100 | 0.54 | 0.03 | 0.09 | 36.18M bytes/s |
| small | array | json_mb=10, array_mb=100 | 0.18 | 0.05 | 0.28 | 1.14B bytes/s |
| medium | chunked_json | json_mb=100, array_mb=1,000 | 5.77 | 0.32 | 0.71 | 34.15M bytes/s |
| medium | array | json_mb=100, array_mb=1,000 | 2.14 | 0.21 | 2.48 | 978.39M bytes/s |
| large | chunked_json | json_mb=1,000, array_mb=4,000 | 67.94 | 4.98 | 6.92 | 29.00M bytes/s |
| large | array | json_mb=1,000, array_mb=4,000 | 10.65 | 0.96 | 9.80 | 787.59M bytes/s |

## Reading the numbers

* **Coupled economy.** The numpy backend runs 100k firms and 1M households with
  ~2.9M purchase entries per day at about 0.38 s per step and 1.6 GiB RSS, so 3,650
  daily steps take about 23 minutes. Every measured step ran vectorized, with no
  reference fallbacks and final states exactly equal to the reference. The
  pure-Python reference is 35–60× slower and is kept for correctness and small
  runs.
* **Fields.** 10M cells, 40M edges, 3 fields (including a 2-component vector) and
  4 substeps take 34 s at 5.5 GiB through `evolve_field_arrays`. Numpy output is
  bit-identical to the Python reference, which is 20–30× slower.
* **Spatial store.** SQLite import, array load and array write-back are
  I/O-bound: about 14–24k cells/s end to end, with a 5M-cell grid at 7.7 GiB.
  National grids should be built once and then updated through
  `put_value_arrays`.
* **Exposure.** 1M entities and 10M obligations clear (baseline plus stress) in
  about 6 s with numpy at 1.6 GiB, against about 130 s in pure Python.
  Results are bit-identical.
* **Routing.** Search is pure Python over compiled networks: about 115–190k label
  expansions/s. A 5M-edge graph compiles in about 22 s at 7.2 GiB, and a 10M-edge
  graph needs about 15 GiB.
* **Checkpoints.** Binary array checkpoints write and read back verified at about
  0.8–1.1 GB/s. Chunked canonical-JSON checkpoints run at about 29–36 MB/s, with a
  ~1 GB document taking 68 s round trip.

Runs were sequential, but other agents were active on the machine, so treat the
timings as ±20%.
