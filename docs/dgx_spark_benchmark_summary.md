# DGX Spark Benchmark Summary

This benchmark compares CPU/Pandas and GPU/RAPIDS cuDF pipelines for Cowrie-style honeypot telemetry analytics on NVIDIA DGX Spark.

## Benchmark Workload

- Dataset type: Synthetic Cowrie-style SSH login telemetry
- Largest dataset: 10,000,000 records
- File size at 10M: 2,052.94 MB
- Analytics: event counts, time buckets, username/password frequency, credential-pair frequency, source-IP aggregates, and suspicious high-volume source analysis

## Results

| Dataset | Method | Parse/Load | Analysis | Total | Throughput |
|---:|---|---:|---:|---:|---:|
| 10K | CPU/Pandas | 42.11 ms | 5.29 ms | 47.40 ms | 210,968 records/sec |
| 10K | GPU/cuDF | 2,620.54 ms | 52.48 ms | 2,673.02 ms | 3,741 records/sec |
| 1M | CPU/Pandas | 3,660.39 ms | 195.58 ms | 3,855.97 ms | 259,338 records/sec |
| 1M | GPU/cuDF | 1,097.84 ms | 177.54 ms | 1,275.38 ms | 784,079 records/sec |
| 10M | CPU/Pandas | 35,018.04 ms | 2,081.18 ms | 37,099.22 ms | 269,547 records/sec |
| 10M | GPU/cuDF | 3,544.57 ms | 1,405.16 ms | 4,949.73 ms | 2,020,311 records/sec |

## Key Findings

At small scale, CPU/Pandas outperformed GPU/cuDF because GPU initialization, JSON parsing, and dataframe setup overhead dominated the workload.

At 1M records, GPU/cuDF achieved approximately 3.0x higher end-to-end throughput than CPU/Pandas.

At 10M records, GPU/cuDF achieved approximately 7.5x higher end-to-end throughput than CPU/Pandas, processing roughly 2.02M records/sec.

The strongest acceleration occurred in parse/load and dataframe construction, while analytics aggregation also improved but by a smaller margin. This suggests that GPU acceleration becomes increasingly valuable as telemetry volume grows, but pipeline design must still account for ingestion and normalization bottlenecks.
