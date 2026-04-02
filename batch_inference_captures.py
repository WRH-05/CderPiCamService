#!/usr/bin/env python3
import argparse
import csv
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import psutil
except ImportError:  # pragma: no cover - optional dependency for Pi deployments
    psutil = None

import onnxruntime as ort

from intereference_onnx import infer_severity_score


DEFAULT_MODEL_NAME = "best_model_v3_1_goldilocks.onnx"
DEFAULT_CANDIDATE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".webp")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fmt_mb(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def collect_process_rss_mb() -> Optional[float]:
    if psutil is None:
        return None

    process = psutil.Process()
    return process.memory_info().rss / (1024.0 * 1024.0)


def discover_images(captures_dir: Path) -> list[Path]:
    candidates: list[Path] = []
    for extension in DEFAULT_CANDIDATE_EXTENSIONS:
        candidates.extend(captures_dir.glob(f"*{extension}"))
    return sorted(path for path in candidates if path.is_file())


def write_csv_rows(csv_path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("Cannot compute percentile of an empty series")

    if len(ordered) == 1:
        return ordered[0]

    if fraction <= 0:
        return ordered[0]
    if fraction >= 1:
        return ordered[-1]

    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run ONNX inference across all images in captures and write a per-image CSV log."
    )
    parser.add_argument("--onnx_model", type=Path, default=Path(DEFAULT_MODEL_NAME))
    parser.add_argument("--captures_dir", type=Path, default=Path("captures"))
    parser.add_argument("--image_size", type=int, default=224)
    parser.add_argument("--critical_threshold", type=float, default=0.65)
    parser.add_argument("--output_csv", type=Path, default=Path("batch_inference_results.csv"))
    parser.add_argument("--summary_csv", type=Path, default=Path("batch_inference_summary.csv"))
    parser.add_argument("--summary_md", type=Path, default=Path("batch_inference_summary.md"))

    args = parser.parse_args()

    service_dir = Path(__file__).resolve().parent
    onnx_model = (service_dir / args.onnx_model).resolve()
    captures_dir = (service_dir / args.captures_dir).resolve()
    output_csv = (service_dir / args.output_csv).resolve()
    summary_csv = (service_dir / args.summary_csv).resolve()
    summary_md = (service_dir / args.summary_md).resolve()

    if not onnx_model.exists():
        raise FileNotFoundError(f"ONNX model not found: {onnx_model}")
    if not captures_dir.exists():
        raise FileNotFoundError(f"Captures directory not found: {captures_dir}")

    image_paths = discover_images(captures_dir)
    if not image_paths:
        raise FileNotFoundError(f"No supported images found in {captures_dir}")

    providers = ["CPUExecutionProvider"]
    rss_before_session_mb = collect_process_rss_mb()
    session = ort.InferenceSession(str(onnx_model), providers=providers)
    rss_after_session_mb = collect_process_rss_mb()

    rows: list[dict[str, object]] = []
    successful_latencies_ms: list[float] = []
    peak_rss_mb = rss_after_session_mb
    failed_count = 0

    for index, image_path in enumerate(image_paths, start=1):
        rss_before_run_mb = collect_process_rss_mb()
        start = time.perf_counter()
        try:
            score = infer_severity_score(
                onnx_model_path=str(onnx_model),
                image_path=str(image_path),
                image_size=args.image_size,
                session=session,
            )
            latency_ms = (time.perf_counter() - start) * 1000.0
            status = "CRITICAL" if score > args.critical_threshold else "OK"
            error = ""
            successful_latencies_ms.append(latency_ms)
        except Exception as exc:
            latency_ms = (time.perf_counter() - start) * 1000.0
            score = None
            status = "ERROR"
            error = str(exc)
            failed_count += 1

        rss_after_run_mb = collect_process_rss_mb()
        if rss_after_run_mb is not None:
            peak_rss_mb = rss_after_run_mb if peak_rss_mb is None else max(peak_rss_mb, rss_after_run_mb)

        rows.append(
            {
                "timestamp_utc": utc_now(),
                "image_index": index,
                "image_path": str(image_path),
                "severity_score": None if score is None else round(score, 6),
                "status": status,
                "latency_ms": round(latency_ms, 4),
                "rss_before_run_mb": None if rss_before_run_mb is None else round(rss_before_run_mb, 4),
                "rss_after_run_mb": None if rss_after_run_mb is None else round(rss_after_run_mb, 4),
                "rss_delta_mb": None
                if (rss_before_run_mb is None or rss_after_run_mb is None)
                else round(rss_after_run_mb - rss_before_run_mb, 4),
                "error": error,
            }
        )

    processed_count = len(rows)
    success_count = processed_count - failed_count
    total_success_seconds = sum(successful_latencies_ms) / 1000.0
    throughput_fps = success_count / total_success_seconds if total_success_seconds > 0 else 0.0

    summary = {
        "generated_at_utc": utc_now(),
        "model_path": str(onnx_model),
        "captures_dir": str(captures_dir),
        "processed_count": processed_count,
        "success_count": success_count,
        "failed_count": failed_count,
        "mean_latency_ms": statistics.fmean(successful_latencies_ms) if successful_latencies_ms else 0.0,
        "median_latency_ms": statistics.median(successful_latencies_ms) if successful_latencies_ms else 0.0,
        "p90_latency_ms": percentile(successful_latencies_ms, 0.90) if successful_latencies_ms else 0.0,
        "p95_latency_ms": percentile(successful_latencies_ms, 0.95) if successful_latencies_ms else 0.0,
        "min_latency_ms": min(successful_latencies_ms) if successful_latencies_ms else 0.0,
        "max_latency_ms": max(successful_latencies_ms) if successful_latencies_ms else 0.0,
        "throughput_fps": throughput_fps,
        "rss_before_session_mb": rss_before_session_mb,
        "rss_after_session_mb": rss_after_session_mb,
        "peak_rss_mb": peak_rss_mb,
    }

    write_csv_rows(
        output_csv,
        [
            "timestamp_utc",
            "image_index",
            "image_path",
            "severity_score",
            "status",
            "latency_ms",
            "rss_before_run_mb",
            "rss_after_run_mb",
            "rss_delta_mb",
            "error",
        ],
        rows,
    )

    summary_csv.parent.mkdir(parents=True, exist_ok=True)
    with summary_csv.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=list(summary.keys()))
        writer.writeheader()
        writer.writerow(summary)

    summary_md.parent.mkdir(parents=True, exist_ok=True)
    summary_md.write_text(
        "\n".join(
            [
                "# Raspberry Pi 4 Batch Inference Summary",
                "",
                "| Metric | Value |",
                "| --- | ---: |",
                f"| Model | {summary['model_path']} |",
                f"| Captures directory | {summary['captures_dir']} |",
                f"| Processed images | {summary['processed_count']} |",
                f"| Successful images | {summary['success_count']} |",
                f"| Failed images | {summary['failed_count']} |",
                f"| Average latency (ms) | {summary['mean_latency_ms']:.2f} |",
                f"| Median latency (ms) | {summary['median_latency_ms']:.2f} |",
                f"| P90 latency (ms) | {summary['p90_latency_ms']:.2f} |",
                f"| P95 latency (ms) | {summary['p95_latency_ms']:.2f} |",
                f"| Min latency (ms) | {summary['min_latency_ms']:.2f} |",
                f"| Max latency (ms) | {summary['max_latency_ms']:.2f} |",
                f"| Throughput (FPS) | {summary['throughput_fps']:.2f} |",
                f"| RSS before session (MB) | {fmt_mb(summary['rss_before_session_mb'])} |",
                f"| RSS after session (MB) | {fmt_mb(summary['rss_after_session_mb'])} |",
                f"| Peak RSS (MB) | {fmt_mb(summary['peak_rss_mb'])} |",
                "",
            ]
        ),
        encoding="utf-8",
    )

    print(summary_md.read_text(encoding="utf-8"))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Stopped by user", flush=True)
        sys.exit(0)