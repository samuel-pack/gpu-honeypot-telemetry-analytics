# Custom CUDA Projection

This project currently compares CPU/Pandas and GPU/RAPIDS cuDF pipelines for Cowrie-style honeypot telemetry analytics.

A custom CUDA/C++ kernel is a possible future optimization path, but the current benchmark suggests it should not be the first optimization target unless the input path is also changed.

## Current 10M GPU/cuDF Timing

| Stage | Time |
|---|---:|
| Parse/load | 3,544.57 ms |
| Analysis | 1,405.16 ms |
| Total | 4,949.73 ms |

A custom CUDA kernel that only improves aggregation can only reduce the analysis portion of the workload. Even if the analysis stage were reduced to zero, total runtime would fall from about 4.95 seconds to about 3.54 seconds.

That is a theoretical maximum end-to-end improvement of roughly 28%.

## Implication

A custom CUDA kernel alone is unlikely to produce a greater than 50% end-to-end improvement unless the project also reduces ingestion, normalization, or dataframe construction overhead.

The more promising optimization path is:

```text
Raw JSONL -> normalized Parquet or compact binary columns -> cuDF/custom CUDA aggregation
```

This would separate raw log parsing from repeated analytics and provide a cleaner basis for comparing RAPIDS/cuDF against a narrower custom CUDA implementation.

## Future Work

Potential future benchmark stages:

1. JSONL -> CPU/Pandas
2. JSONL -> RAPIDS/cuDF
3. Parquet -> RAPIDS/cuDF
4. Compact binary columns -> custom CUDA/C++
5. Compare end-to-end runtime and aggregation-only runtime separately
