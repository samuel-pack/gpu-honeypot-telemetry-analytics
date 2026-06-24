# GPU-Accelerated Honeypot Telemetry Analytics

_A benchmark prototype for accelerating Cowrie-style SSH honeypot telemetry analysis with NVIDIA RAPIDS/cuDF and DGX Spark._

## Overview

This project evaluates whether NVIDIA accelerated computing can improve the processing of high-volume honeypot telemetry. The workload focuses on SSH brute-force login activity collected by Cowrie and similar data submitted to the SANS Internet Storm Center DShield project.

The prototype compares CPU/Pandas and GPU/RAPIDS cuDF analytics pipelines on synthetic Cowrie-style datasets up to 10 million records. It measures ingestion, normalization, aggregation, and end-to-end throughput for common telemetry questions:

- What usernames are being targeted?
- What passwords are being attempted?
- Which credential pairs appear most often?
- Which source actors generate the most activity?
- How does activity change across time buckets?
- Where does GPU acceleration help, and where do parsing or normalization costs still dominate?

This project is not an official DShield component and does not claim to represent DShield’s production workload. It is a possible complementary processing path for Cowrie/DShield-style telemetry: keep raw logs local, generate safe benchmark data, and evaluate whether NVIDIA hardware and software can make large-scale analysis faster.

## What This Demonstrates

This project demonstrates an end-to-end accelerated-computing evaluation:

- define a realistic telemetry workload
- build CPU and GPU implementations
- benchmark scaling behavior across 10K, 1M, and 10M records
- separate ingestion time from analytics runtime
- identify where GPU acceleration improves throughput
- document the limits of the prototype honestly

The goal is not simply to show that a GPU is faster. The goal is to measure when GPU acceleration becomes useful and which parts of the pipeline still constrain performance.

## Processing Pipeline

```text
Cowrie JSONL logs
      |
      v
Sanitized local sample
      |
      v
Synthetic benchmark generation
      |
      v
+-------------------------+      +----------------------------+
| CPU/Pandas baseline     |      | GPU/RAPIDS cuDF baseline   |
| JSONL -> Pandas -> agg  |      | JSONL -> cuDF -> agg       |
+-------------------------+      +----------------------------+
      |                                      |
      v                                      v
Benchmark summaries, throughput comparison, bottleneck analysis
```

## Why This Might Help

Cowrie logs are easy to collect, but repeated analysis becomes expensive as telemetry grows. A local DShield or Cowrie operator may want to summarize large JSONL log files without uploading raw data elsewhere.

This project treats Cowrie/DShield-style telemetry as a customer workload: a local operator has high-volume honeypot logs and wants faster summarization while keeping raw telemetry under local control.

The benchmark separates:

- JSON parse/load time
- field normalization
- aggregation runtime
- end-to-end throughput

That distinction matters because a GPU can only accelerate the parts of the pipeline that are actually moved into GPU-friendly processing.

## Data Model and Privacy

The project uses a sanitized local Cowrie sample to generate synthetic benchmark datasets. Real public source IPs are not preserved in synthetic output. Synthetic source actors are generated from private address ranges so the data can model source skew and repeated activity without attributing behavior to real Internet hosts.

The generated records preserve benchmark-relevant fields such as:

- timestamp
- event ID
- username
- password
- source actor
- destination port
- protocol
- credential pair

The synthetic datasets are intended for performance testing, not threat attribution.

## Synthetic Data Generation

Synthetic datasets are generated with:

```bash
python scripts/generate_synthetic_cowrie.py \
  --input data/real/cowrie_sanitized.jsonl \
  --output data/synthetic/synthetic_cowrie_10m.jsonl \
  --records 10000000 \
  --seed 20260623 \
  --profile failed-heavy \
  --days 7 \
  --start 2026-06-22T11:50:00Z \
  --src-ip-mode private-diverse \
  --exclude-values claude,openclaw,nvidia,grok,cursor
```

Large generated datasets are not committed to the repository.

## CPU Baseline

`scripts/run_cpu_pandas.py` implements the CPU/Pandas baseline. It reads JSONL telemetry, normalizes fields, and computes:

- event ID counts
- top usernames
- top passwords
- top source IPs
- top credential pairs
- time-bucket counts
- per-source-IP aggregates
- suspicious high-volume source actors

## GPU Baseline

`scripts/run_gpu_cudf.py` implements the RAPIDS/cuDF GPU baseline. It mirrors the CPU analytics path as closely as practical while using GPU dataframe operations for ingestion, normalization, and aggregation.

The GPU path currently targets NVIDIA DGX Spark with CUDA 13 and RAPIDS/cuDF 26.06.

## Reproducing the DGX Spark Benchmark

Create the CPU/Pandas environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Create the RAPIDS/cuDF environment:

```bash
conda env create -f environment-rapids-cuda13.yml
conda activate rapids-cuda13
```

Generate the 10M synthetic dataset:

```bash
python scripts/generate_synthetic_cowrie.py \
  --input data/real/cowrie_sanitized.jsonl \
  --output data/synthetic/synthetic_cowrie_10m.jsonl \
  --records 10000000 \
  --seed 20260623 \
  --profile failed-heavy \
  --days 7 \
  --start 2026-06-22T11:50:00Z \
  --src-ip-mode private-diverse \
  --exclude-values claude,openclaw,nvidia,grok,cursor
```

Run the CPU baseline:

```bash
source .venv/bin/activate

python scripts/run_cpu_pandas.py \
  --input data/synthetic/synthetic_cowrie_10m.jsonl \
  --bucket-minutes 5 \
  --min-attempts 1000 \
  --output results/dgx_cpu_pandas_10m_summary.md \
  --benchmark-csv results/benchmark_results.csv
```

Run the GPU baseline:

```bash
conda activate rapids-cuda13

python scripts/run_gpu_cudf.py \
  --input data/synthetic/synthetic_cowrie_10m.jsonl \
  --bucket-minutes 5 \
  --min-attempts 1000 \
  --output results/dgx_gpu_cudf_10m_summary.md \
  --benchmark-csv results/benchmark_results.csv
```

## Benchmark Results

Benchmark platform: NVIDIA DGX Spark  
Largest dataset: 10,000,000 synthetic Cowrie-style SSH login records  
Largest file size: 2,052.94 MB

| Dataset | Method | Parse/Load | Analysis | Total | Throughput |
|---:|---|---:|---:|---:|---:|
| 10K | CPU/Pandas | 42.11 ms | 5.29 ms | 47.40 ms | 210,968 records/sec |
| 10K | GPU/cuDF | 2,620.54 ms | 52.48 ms | 2,673.02 ms | 3,741 records/sec |
| 1M | CPU/Pandas | 3,660.39 ms | 195.58 ms | 3,855.97 ms | 259,338 records/sec |
| 1M | GPU/cuDF | 1,097.84 ms | 177.54 ms | 1,275.38 ms | 784,079 records/sec |
| 10M | CPU/Pandas | 35,018.04 ms | 2,081.18 ms | 37,099.22 ms | 269,547 records/sec |
| 10M | GPU/cuDF | 3,544.57 ms | 1,405.16 ms | 4,949.73 ms | 2,020,311 records/sec |

## Findings

At 10K records, CPU/Pandas is faster because GPU initialization, JSON parsing, and dataframe setup overhead dominate the workload.

At 1M records, GPU/cuDF provides a clear throughput advantage.

At 10M records, GPU/cuDF processes roughly 2.02 million records per second and improves end-to-end throughput by about 7.5x over the CPU/Pandas baseline.

The largest improvement occurs in parse/load and dataframe construction. Aggregation also improves, but by a smaller margin. This suggests that GPU acceleration becomes more useful as telemetry volume grows, while pipeline design still needs to account for ingestion and normalization bottlenecks.

## Repository Layout

```text
scripts/
  generate_synthetic_cowrie.py   Synthetic Cowrie-style data generator
  run_cpu_pandas.py              CPU/Pandas benchmark pipeline
  run_gpu_cudf.py                GPU/RAPIDS cuDF benchmark pipeline

docs/
  methodology.md
  data_fidelity_and_sanitization.md
  limitations.md
  dgx_spark_benchmark_summary.md

data/
  real/                          Local sanitized input; not committed
  synthetic/                     Generated benchmark data; not committed

results/
  Benchmark summaries and CSV outputs
```

## Limitations

This project is not an official DShield component, does not use unpublished DShield infrastructure, and does not claim to reproduce DShield’s full production workload.

The benchmark intentionally focuses on SSH credential-attempt analytics. It does not cover:

- full session reconstruction
- command analysis
- malware capture
- HTTP honeypot telemetry
- firewall log processing
- public-IP threat attribution
- DShield production ingestion or reporting workflows

The synthetic data is useful for repeatable performance testing, but it should not be treated as real threat intelligence.

## Roadmap

- Add a normalized intermediate representation for GPU-friendly processing.
- Implement a custom CUDA/C++ frequency-analysis kernel.
- Compare CPU/Pandas, RAPIDS/cuDF, and custom CUDA paths.
- Package a local analyzer that Cowrie/DShield users could run against their own logs.
- Expand benchmark documentation with hardware, software, and scaling notes.
