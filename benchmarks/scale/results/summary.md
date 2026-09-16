| Kernel | Size | Backend | Parameters | Kernel s | Setup s | Peak RSS GiB | Throughput |
|---|---|---|---|---:|---:|---:|---|
| checkpoints | small | chunked_json | json_mb=10, array_mb=100 | 0.544 | 0.027 | 0.09 | 36,183,533 bytes/s |
| checkpoints | small | array | json_mb=10, array_mb=100 | 0.184 | 0.052 | 0.28 | 1,140,383,987 bytes/s |
| checkpoints | medium | chunked_json | json_mb=100, array_mb=1,000 | 5.769 | 0.317 | 0.71 | 34,153,175 bytes/s |
| checkpoints | medium | array | json_mb=100, array_mb=1,000 | 2.143 | 0.206 | 2.48 | 978,387,261 bytes/s |
| checkpoints | large | chunked_json | json_mb=1,000, array_mb=4,000 | 67.938 | 4.983 | 6.92 | 29,003,301 bytes/s |
| checkpoints | large | array | json_mb=1,000, array_mb=4,000 | 10.651 | 0.960 | 9.80 | 787,592,222 bytes/s |
| coupled_economy | small | numpy | firms=1,000, households=10,000, steps=30 | 0.091 | 0.054 | 0.05 | 3,608,456 actor-steps/s |
| coupled_economy | small | python | firms=1,000, households=10,000, steps=30 | 4.068 | 0.038 | 0.06 | 81,131 actor-steps/s |
| coupled_economy | medium | numpy | firms=10,000, households=100,000, steps=30 | 1.109 | 0.729 | 0.19 | 2,976,355 actor-steps/s |
| coupled_economy | medium | python | firms=10,000, households=100,000, steps=30 | 68.122 | 0.496 | 0.25 | 48,442 actor-steps/s |
| coupled_economy | large | numpy | firms=100,000, households=1,000,000, steps=60 | 22.601 | 9.180 | 1.57 | 2,920,164 actor-steps/s |
| exposure | small | python | entities=10,000, obligations=100,000 | 0.231 | 0.044 | 0.09 | 11,415,149 clearing-updates/s |
| exposure | small | numpy | entities=10,000, obligations=100,000 | 0.034 | 0.052 | 0.05 | 78,018,922 clearing-updates/s |
| exposure | medium | python | entities=100,000, obligations=1,000,000 | 3.540 | 0.180 | 0.57 | 9,321,125 clearing-updates/s |
| exposure | medium | numpy | entities=100,000, obligations=1,000,000 | 0.290 | 0.096 | 0.19 | 113,603,122 clearing-updates/s |
| exposure | large | python | entities=1,000,000, obligations=10,000,000 | 129.247 | 1.248 | 5.40 | 2,723,466 clearing-updates/s |
| exposure | large | numpy | entities=1,000,000, obligations=10,000,000 | 5.708 | 0.421 | 1.62 | 61,667,018 clearing-updates/s |
| fields | small | numpy | cells=100,000, edges=400,000 | 0.119 | 0.087 | 0.09 | 67,233,871 component updates/s |
| fields | small | python | cells=100,000, edges=400,000 | 2.101 | 0.139 | 0.17 | 3,807,742 component updates/s |
| fields | medium | numpy | cells=1,000,000, edges=4,000,000 | 1.936 | 0.284 | 0.60 | 41,315,060 component updates/s |
| fields | medium | python | cells=1,000,000, edges=4,000,000 | 63.074 | 0.832 | 1.44 | 1,268,357 component updates/s |
| fields | large | numpy | cells=10,000,000, edges=40,000,000 | 34.387 | 1.537 | 5.48 | 23,264,518 component updates/s |
| spatial_store | small | numpy | cells=100,000 | 4.137 | 0.298 | 0.23 | 24,171 cells (import+load+write)/s |
| spatial_store | medium | numpy | cells=1,000,000 | 51.530 | 2.762 | 1.57 | 19,406 cells (import+load+write)/s |
| spatial_store | large | numpy | cells=5,000,000 | 359.752 | 15.473 | 7.69 | 13,898 cells (import+load+write)/s |
| transport | small | python | side=160, queries=4 | 4.474 | 0.361 | 0.16 | 187,199 label-expansions/s |
| transport | medium | python | side=500, queries=2 | 25.996 | 4.340 | 1.38 | 160,759 label-expansions/s |
| transport | large | python | side=1,120, queries=1 | 122.145 | 22.352 | 7.21 | 115,077 label-expansions/s |
