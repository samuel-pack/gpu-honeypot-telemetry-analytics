#!/usr/bin/env python3
"""
GPU baseline analytics for synthetic Cowrie telemetry.

This script is for benchmarking only. It reads synthetic Cowrie-style JSONL,
runs the brute-force analytics with RAPIDS/cuDF on the GPU, and writes timing
results for comparison with the CPU/Pandas baseline.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from dataclasses import dataclass
from pathlib import Path
try:
    import cudf  # type: ignore
except Exception:  # pragma: no cover - import failure is handled at runtime
    cudf = None  # type: ignore[assignment]


@dataclass
class BenchmarkTiming:
    parse_ms: float
    analysis_ms: float
    total_ms: float
    records_per_second: float


def positive_int(value: str) -> int:
    try:
        parsed = int(value.replace("_", ""))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc

    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")

    return parsed


def optional_path(value: str | None) -> Path | None:
    if value is None or value == "":
        return None
    return Path(value)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run RAPIDS/cuDF baseline analytics on Cowrie JSONL telemetry.")
    parser.add_argument("--input", required=True, help="Cowrie-style JSONL input file.")
    parser.add_argument("--bucket-minutes", type=positive_int, default=5, help="Time bucket size in minutes.")
    parser.add_argument("--min-attempts", type=positive_int, default=100, help="Minimum attempts for suspicious IPs.")
    parser.add_argument(
        "--output",
        default=None,
        help="Optional markdown summary output path. If omitted, no markdown file is written.",
    )
    parser.add_argument(
        "--benchmark-csv",
        default="results/benchmark_results.csv",
        help="Benchmark CSV path. Default: results/benchmark_results.csv.",
    )
    parser.add_argument(
        "--top-n",
        type=positive_int,
        default=20,
        help="Number of rows to show for top-value summaries. Default: 20.",
    )
    return parser


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def sync_gpu() -> None:
    try:
        import cupy as cp  # type: ignore
    except Exception:
        return

    try:
        cp.cuda.Stream.null.synchronize()
    except Exception:
        pass


def as_pandas_series(series):
    if hasattr(series, "to_pandas"):
        return series.to_pandas()
    return series


def as_pandas_frame(frame):
    if hasattr(frame, "to_pandas"):
        return frame.to_pandas()
    return frame


def ensure_string_column(df, column: str):
    if column not in df.columns:
        df[column] = ""
    return df[column].fillna("").astype("str")


def normalize_frame(df, bucket_minutes: int):
    if "timestamp" not in df.columns:
        raise ValueError("Input file does not contain a timestamp column")

    df["timestamp"] = cudf.to_datetime(df["timestamp"])
    df = df.dropna(subset=["timestamp"])

    df["username"] = ensure_string_column(df, "username")
    df["password"] = ensure_string_column(df, "password")
    df["src_ip"] = ensure_string_column(df, "src_ip")
    df["eventid"] = ensure_string_column(df, "eventid")
    df["protocol"] = ensure_string_column(df, "protocol")
    bucket_ns = bucket_minutes * 60 * 1_000_000_000
    timestamp_ns = df["timestamp"].astype("int64")
    df["time_bucket"] = ((timestamp_ns // bucket_ns) * bucket_ns).astype("datetime64[ns]")
    df["credential_pair"] = df["username"] + ":" + df["password"]

    sync_gpu()
    return df


def read_and_normalize(input_path: Path, bucket_minutes: int):
    started = time.perf_counter()
    df = cudf.read_json(str(input_path), lines=True)
    df = normalize_frame(df, bucket_minutes)
    sync_gpu()
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return df, elapsed_ms


def build_summary(df, min_attempts: int, top_n: int):
    event_counts = df["eventid"].value_counts()
    top_usernames = df["username"].value_counts().head(top_n)
    top_passwords = df["password"].value_counts().head(top_n)
    top_src_ips = df["src_ip"].value_counts().head(top_n)
    top_pairs = df["credential_pair"].value_counts().head(top_n)
    bucket_counts = df["time_bucket"].value_counts().sort_index()

    attempts_by_ip = df["src_ip"].value_counts().to_frame("attempts").reset_index()
    attempts_by_ip.columns = ["src_ip", "attempts"]

    unique_usernames = df.groupby("src_ip")["username"].nunique().reset_index()
    unique_usernames.columns = ["src_ip", "unique_usernames"]

    unique_passwords = df.groupby("src_ip")["password"].nunique().reset_index()
    unique_passwords.columns = ["src_ip", "unique_passwords"]

    per_ip = attempts_by_ip.merge(unique_usernames, on="src_ip", how="left")
    per_ip = per_ip.merge(unique_passwords, on="src_ip", how="left")
    per_ip = per_ip.sort_values(["attempts", "unique_usernames", "unique_passwords"], ascending=[False, False, False])

    suspicious = per_ip[per_ip["attempts"] >= min_attempts]

    return {
        "event_counts": event_counts,
        "top_usernames": top_usernames,
        "top_passwords": top_passwords,
        "top_src_ips": top_src_ips,
        "top_pairs": top_pairs,
        "bucket_counts": bucket_counts,
        "per_ip": per_ip,
        "suspicious": suspicious,
    }


def format_series_table(series, title: str, top_n: int) -> list[str]:
    lines = [f"### {title}"]
    series_pd = as_pandas_series(series)
    if len(series_pd) == 0:
        lines.append("")
        lines.append("_No data._")
        return lines

    lines.append("")
    lines.append("| Value | Count |")
    lines.append("| --- | ---: |")
    for value, count in series_pd.head(top_n).items():
        lines.append(f"| `{value}` | {int(count):,} |")
    return lines


def format_time_bucket_table(series) -> list[str]:
    lines = ["## Attempts Per Time Bucket", ""]
    series_pd = as_pandas_series(series)
    if len(series_pd) == 0:
        lines.append("_No data._")
        return lines

    lines.append("| Time Bucket | Attempts |")
    lines.append("| --- | ---: |")
    for ts, count in series_pd.items():
        lines.append(f"| `{ts}` | {int(count):,} |")
    return lines


def format_ip_table(frame, title: str, top_n: int) -> list[str]:
    lines = [f"## {title}", ""]
    frame_pd = as_pandas_frame(frame)
    if len(frame_pd) == 0:
        lines.append("_No data._")
        return lines

    lines.append("| Source IP | Attempts | Unique Usernames | Unique Passwords |")
    lines.append("| --- | ---: | ---: | ---: |")
    for _, row in frame_pd.head(top_n).iterrows():
        lines.append(
            f"| `{row['src_ip']}` | {int(row['attempts']):,} | {int(row['unique_usernames']):,} | {int(row['unique_passwords']):,} |"
        )
    return lines


def print_console_summary(df, summary: dict[str, object], top_n: int, min_attempts: int, bucket_minutes: int) -> None:
    event_counts = summary["event_counts"]
    top_usernames = summary["top_usernames"]
    top_passwords = summary["top_passwords"]
    top_src_ips = summary["top_src_ips"]
    top_pairs = summary["top_pairs"]
    bucket_counts = summary["bucket_counts"]
    per_ip = summary["per_ip"]
    suspicious = summary["suspicious"]

    event_counts_pd = as_pandas_series(event_counts)
    bucket_counts_pd = as_pandas_series(bucket_counts)
    top_usernames_pd = as_pandas_series(top_usernames)
    top_passwords_pd = as_pandas_series(top_passwords)
    top_src_ips_pd = as_pandas_series(top_src_ips)
    top_pairs_pd = as_pandas_series(top_pairs)
    per_ip_pd = as_pandas_frame(per_ip)
    suspicious_pd = as_pandas_frame(suspicious)

    print("GPU cuDF baseline summary")
    print(f"records: {len(df):,}")
    print("event IDs:")
    for key, value in event_counts_pd.items():
        print(f"  {key}: {int(value):,}")
    if len(bucket_counts_pd) > 0:
        print(f"time buckets ({bucket_counts_pd.index.min()} -> {bucket_counts_pd.index.max()})")
        print(bucket_counts_pd.head(top_n).to_string())
    else:
        print(f"time buckets (bucket size {bucket_minutes} minutes)")
        print("  none")
    print("top usernames:")
    print(top_usernames_pd.to_string())
    print("top passwords:")
    print(top_passwords_pd.to_string())
    print("top source IPs:")
    print(top_src_ips_pd.to_string())
    print("top credential pairs:")
    print(top_pairs_pd.to_string())
    print("suspicious high-volume source IPs:")
    if len(suspicious_pd) == 0:
        print(f"  none found at threshold >= {min_attempts}")
    else:
        print(suspicious_pd.head(top_n).to_string(index=False))
    print("per-source-IP aggregates (top 10):")
    print(per_ip_pd.head(10).to_string(index=False))


def build_markdown_report(
    input_path: Path,
    df,
    summary: dict[str, object],
    timing: BenchmarkTiming,
    bucket_minutes: int,
    min_attempts: int,
    benchmark_row: dict[str, object],
    top_n: int,
) -> str:
    event_counts = summary["event_counts"]
    top_usernames = summary["top_usernames"]
    top_passwords = summary["top_passwords"]
    top_src_ips = summary["top_src_ips"]
    top_pairs = summary["top_pairs"]
    bucket_counts = summary["bucket_counts"]
    per_ip = summary["per_ip"]
    suspicious = summary["suspicious"]

    event_counts_pd = as_pandas_series(event_counts)
    bucket_counts_pd = as_pandas_series(bucket_counts)
    suspicious_pd = as_pandas_frame(suspicious)

    lines: list[str] = []
    lines.append("# GPU cuDF Baseline Summary")
    lines.append("")
    lines.append(f"- Input: `{input_path}`")
    lines.append(f"- Records: `{len(df):,}`")
    lines.append(f"- Bucket minutes: `{bucket_minutes}`")
    lines.append(f"- Minimum attempts: `{min_attempts}`")
    lines.append(f"- Parse/load time: `{timing.parse_ms:.2f} ms`")
    lines.append(f"- Analysis time: `{timing.analysis_ms:.2f} ms`")
    lines.append(f"- Total time: `{timing.total_ms:.2f} ms`")
    lines.append(f"- Records per second: `{timing.records_per_second:,.2f}`")
    lines.append("")
    lines.append("## Event IDs")
    lines.append("")
    lines.append("| Event ID | Count |")
    lines.append("| --- | ---: |")
    for key, value in event_counts_pd.items():
        lines.append(f"| `{key}` | {int(value):,} |")
    lines.append("")
    lines.extend(format_series_table(top_usernames, "Top Usernames", top_n))
    lines.append("")
    lines.extend(format_series_table(top_passwords, "Top Passwords", top_n))
    lines.append("")
    lines.extend(format_series_table(top_src_ips, "Top Source IPs", top_n))
    lines.append("")
    lines.extend(format_series_table(top_pairs, "Top Credential Pairs", top_n))
    lines.append("")
    lines.extend(format_time_bucket_table(bucket_counts_pd))
    lines.append("")
    lines.append("## Per-Source-IP Aggregates")
    lines.append("")
    lines.append("| Source IP | Attempts | Unique Usernames | Unique Passwords |")
    lines.append("| --- | ---: | ---: | ---: |")
    per_ip_pd = as_pandas_frame(per_ip)
    for _, row in per_ip_pd.head(20).iterrows():
        lines.append(
            f"| `{row['src_ip']}` | {int(row['attempts']):,} | {int(row['unique_usernames']):,} | {int(row['unique_passwords']):,} |"
        )
    lines.append("")
    lines.append("## Suspicious High-Volume Source IPs")
    lines.append("")
    lines.append(f"Threshold: `{min_attempts}` attempts")
    lines.append("")
    if len(suspicious_pd) == 0:
        lines.append("_None found._")
    else:
        lines.append("| Source IP | Attempts | Unique Usernames | Unique Passwords |")
        lines.append("| --- | ---: | ---: | ---: |")
        for _, row in suspicious_pd.head(20).iterrows():
            lines.append(
                f"| `{row['src_ip']}` | {int(row['attempts']):,} | {int(row['unique_usernames']):,} | {int(row['unique_passwords']):,} |"
            )

    lines.append("")
    lines.append("## Benchmark Row")
    lines.append("")
    lines.append("| records | method | input_file | file_size_mb | parse_ms | analysis_ms | total_ms | records_per_second | notes |")
    lines.append("| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |")
    lines.append(
        f"| {benchmark_row['records']:,} | {benchmark_row['method']} | `{benchmark_row['input_file']}` | "
        f"{benchmark_row['file_size_mb']:.2f} | {benchmark_row['parse_ms']:.2f} | {benchmark_row['analysis_ms']:.2f} | "
        f"{benchmark_row['total_ms']:.2f} | {benchmark_row['records_per_second']:,.2f} | {benchmark_row['notes']} |"
    )
    return "\n".join(lines) + "\n"


def append_benchmark_csv(csv_path: Path, row: dict[str, object]) -> None:
    ensure_parent_dir(csv_path)
    fieldnames = [
        "records",
        "method",
        "input_file",
        "file_size_mb",
        "parse_ms",
        "analysis_ms",
        "total_ms",
        "records_per_second",
        "notes",
    ]
    file_exists = csv_path.exists() and csv_path.stat().st_size > 0
    with csv_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow({name: row[name] for name in fieldnames})


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if cudf is None:
        print("RAPIDS/cuDF is not available in this Python environment.", file=sys.stderr)
        return 1

    input_path = Path(args.input)
    if not input_path.exists():
        parser.error(f"input file not found: {input_path}")

    output_path = optional_path(args.output)
    benchmark_csv = Path(args.benchmark_csv)
    file_size_mb = input_path.stat().st_size / (1024 * 1024)

    total_started = time.perf_counter()
    parse_started = time.perf_counter()
    try:
        df, parse_ms = read_and_normalize(input_path, args.bucket_minutes)
    except Exception as exc:
        parser.exit(2, f"run_gpu_cudf: {exc}\n")

    analysis_started = time.perf_counter()
    summary = build_summary(df, args.min_attempts, args.top_n)
    sync_gpu()
    analysis_ms = (time.perf_counter() - analysis_started) * 1000.0
    total_ms = (time.perf_counter() - total_started) * 1000.0
    records_per_second = len(df) / (total_ms / 1000.0) if total_ms > 0 else 0.0
    timing = BenchmarkTiming(
        parse_ms=parse_ms,
        analysis_ms=analysis_ms,
        total_ms=total_ms,
        records_per_second=records_per_second,
    )

    benchmark_row = {
        "records": int(len(df)),
        "method": "gpu_cudf",
        "input_file": str(input_path),
        "file_size_mb": float(file_size_mb),
        "parse_ms": float(parse_ms),
        "analysis_ms": float(analysis_ms),
        "total_ms": float(total_ms),
        "records_per_second": float(records_per_second),
        "notes": (
            f"bucket_minutes={args.bucket_minutes}; min_attempts={args.min_attempts}; "
            "parse/load includes cuDF JSON ingestion overhead"
        ),
    }

    print_console_summary(df, summary, args.top_n, args.min_attempts, args.bucket_minutes)
    print("")
    print("Benchmark timing")
    print(f"  parse/load: {parse_ms:.2f} ms")
    print(f"  analysis: {analysis_ms:.2f} ms")
    print(f"  total: {total_ms:.2f} ms")
    print(f"  file size: {file_size_mb:.2f} MB")
    print(f"  records/sec: {records_per_second:,.2f}")

    append_benchmark_csv(benchmark_csv, benchmark_row)

    if output_path is not None:
        ensure_parent_dir(output_path)
        markdown = build_markdown_report(
            input_path=input_path,
            df=df,
            summary=summary,
            timing=timing,
            bucket_minutes=args.bucket_minutes,
            min_attempts=args.min_attempts,
            benchmark_row=benchmark_row,
            top_n=args.top_n,
        )
        output_path.write_text(markdown, encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
