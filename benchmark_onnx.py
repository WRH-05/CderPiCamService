#!/usr/bin/env python3
import argparse
import csv
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

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


def pick_test_image(image_path: Optional[Path], captures_dir: Path) -> Path:
    if image_path is not None:
        return image_path

    candidates = discover_images(captures_dir)
    if not candidates:
        raise FileNotFoundError(
            f"No images found in {captures_dir}. Provide --image_path or add image files to the captures directory."
        )
    return candidates[0]


def percentile(values: Iterable[float], fraction: float) -> float:
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


def write_csv_rows(csv_path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def format_markdown_summary(summary: dict[str, object]) -> str:
    rows = [
        ("Model", str(summary["model_path"])),
        ("Image", str(summary["image_path"])),
        ("Measured runs", str(summary["measured_runs"])),
        ("Warmup runs", str(summary["warmup_runs"])),
        ("Average latency (ms)", f'{summary["mean_latency_ms"]:.2f}'),
        ("Median latency (ms)", f'{summary["median_latency_ms"]:.2f}'),
        ("P90 latency (ms)", f'{summary["p90_latency_ms"]:.2f}'),
        ("P95 latency (ms)", f'{summary["p95_latency_ms"]:.2f}'),
        ("Min latency (ms)", f'{summary["min_latency_ms"]:.2f}'),
        ("Max latency (ms)", f'{summary["max_latency_ms"]:.2f}'),
        ("Throughput (FPS)", f'{summary["throughput_fps"]:.2f}'),
        ("RSS before session (MB)", fmt_mb(summary["rss_before_session_mb"])),
        ("RSS after session (MB)", fmt_mb(summary["rss_after_session_mb"])),
        ("RSS after warmup (MB)", fmt_mb(summary["rss_after_warmup_mb"])),
        ("Session load delta (MB)", fmt_mb(summary["session_load_delta_mb"])),
        ("Warmup delta (MB)", fmt_mb(summary["warmup_delta_mb"])),
        ("Peak RSS (MB)", fmt_mb(summary["peak_rss_mb"])),
    ]

    lines = ["# Raspberry Pi 4 ONNX Benchmark Summary", "", "| Metric | Value |", "| --- | ---: |"]
    for label, value in rows:
        lines.append(f"| {label} | {value} |")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark ONNX inference latency, throughput, and RAM footprint on a Raspberry Pi."
    )
    parser.add_argument("--onnx_model", type=Path, default=Path(DEFAULT_MODEL_NAME))
    parser.add_argument("--image_path", type=Path, default=None)
    parser.add_argument("--captures_dir", type=Path, default=Path("captures"))
    parser.add_argument("--image_size", type=int, default=224)
    parser.add_argument("--warmup_runs", type=int, default=10)
    parser.add_argument("--measured_runs", type=int, default=100)
    parser.add_argument("--critical_threshold", type=float, default=0.65)
    parser.add_argument("--output_csv", type=Path, default=Path("benchmark_runs.csv"))
    parser.add_argument("--summary_csv", type=Path, default=Path("benchmark_summary.csv"))
    parser.add_argument("--summary_md", type=Path, default=Path("benchmark_summary.md"))

    args = parser.parse_args()

    service_dir = Path(__file__).resolve().parent
    onnx_model = (service_dir / args.onnx_model).resolve()
    captures_dir = (service_dir / args.captures_dir).resolve()
    image_path = pick_test_image((service_dir / args.image_path).resolve() if args.image_path else None, captures_dir)
    output_csv = (service_dir / args.output_csv).resolve()
    summary_csv = (service_dir / args.summary_csv).resolve()
    summary_md = (service_dir / args.summary_md).resolve()

    if not onnx_model.exists():
        raise FileNotFoundError(f"ONNX model not found: {onnx_model}")
    if not image_path.exists():
        raise FileNotFoundError(f"Test image not found: {image_path}")

    providers = ["CPUExecutionProvider"]
    rss_before_session_mb = collect_process_rss_mb()
    session = ort.InferenceSession(str(onnx_model), providers=providers)
    rss_after_session_mb = collect_process_rss_mb()

    for _ in range(max(0, args.warmup_runs)):
        infer_severity_score(
            onnx_model_path=str(onnx_model),
            image_path=str(image_path),
            image_size=args.image_size,
            session=session,
        )

    rss_after_warmup_mb = collect_process_rss_mb()

    measured_rows: list[dict[str, object]] = []
    measured_latencies_ms: list[float] = []
    peak_rss_mb = rss_after_warmup_mb

    for run_index in range(1, max(0, args.measured_runs) + 1):
        rss_before_run_mb = collect_process_rss_mb()
        start = time.perf_counter()
        score = infer_severity_score(
            onnx_model_path=str(onnx_model),
            image_path=str(image_path),
            image_size=args.image_size,
            session=session,
        )
        latency_ms = (time.perf_counter() - start) * 1000.0
        rss_after_run_mb = collect_process_rss_mb()

        if rss_after_run_mb is not None:
            peak_rss_mb = rss_after_run_mb if peak_rss_mb is None else max(peak_rss_mb, rss_after_run_mb)

        measured_latencies_ms.append(latency_ms)
        measured_rows.append(
            {
                "timestamp_utc": utc_now(),
                "run_index": run_index,
                "image_path": str(image_path),
                "severity_score": round(score, 6),
                "status": "CRITICAL" if score > args.critical_threshold else "OK",
                "latency_ms": round(latency_ms, 4),
                "rss_before_run_mb": None if rss_before_run_mb is None else round(rss_before_run_mb, 4),
                "rss_after_run_mb": None if rss_after_run_mb is None else round(rss_after_run_mb, 4),
                "rss_delta_mb": None
                if (rss_before_run_mb is None or rss_after_run_mb is None)
                else round(rss_after_run_mb - rss_before_run_mb, 4),
            }
        )

    if not measured_latencies_ms:
        raise RuntimeError("No measured runs were executed")

    mean_latency_ms = statistics.fmean(measured_latencies_ms)
    median_latency_ms = statistics.median(measured_latencies_ms)
    p90_latency_ms = percentile(measured_latencies_ms, 0.90)
    p95_latency_ms = percentile(measured_latencies_ms, 0.95)
    min_latency_ms = min(measured_latencies_ms)
    max_latency_ms = max(measured_latencies_ms)
    total_measured_seconds = sum(measured_latencies_ms) / 1000.0
    throughput_fps = len(measured_latencies_ms) / total_measured_seconds if total_measured_seconds > 0 else 0.0

    session_load_delta_mb = None
    if rss_before_session_mb is not None and rss_after_session_mb is not None:
        session_load_delta_mb = rss_after_session_mb - rss_before_session_mb

    warmup_delta_mb = None
    if rss_after_session_mb is not None and rss_after_warmup_mb is not None:
        warmup_delta_mb = rss_after_warmup_mb - rss_after_session_mb

    if peak_rss_mb is None:
        peak_rss_mb = rss_after_warmup_mb if rss_after_warmup_mb is not None else 0.0

    summary = {
        "generated_at_utc": utc_now(),
        "model_path": str(onnx_model),
        "image_path": str(image_path),
        "warmup_runs": max(0, args.warmup_runs),
        "measured_runs": len(measured_latencies_ms),
        "mean_latency_ms": mean_latency_ms,
        "median_latency_ms": median_latency_ms,
        "p90_latency_ms": p90_latency_ms,
        "p95_latency_ms": p95_latency_ms,
        "min_latency_ms": min_latency_ms,
        "max_latency_ms": max_latency_ms,
        "throughput_fps": throughput_fps,
        "rss_before_session_mb": rss_before_session_mb,
        "rss_after_session_mb": rss_after_session_mb,
        "rss_after_warmup_mb": rss_after_warmup_mb,
        "session_load_delta_mb": session_load_delta_mb,
        "warmup_delta_mb": warmup_delta_mb,
        "peak_rss_mb": peak_rss_mb,
    }

    write_csv_rows(
        output_csv,
        [
            "timestamp_utc",
            "run_index",
            "image_path",
            "severity_score",
            "status",
            "latency_ms",
            "rss_before_run_mb",
            "rss_after_run_mb",
            "rss_delta_mb",
        ],
        measured_rows,
    )

    summary_csv.parent.mkdir(parents=True, exist_ok=True)
    with summary_csv.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=list(summary.keys()))
        writer.writeheader()
        writer.writerow(summary)

    summary_md.parent.mkdir(parents=True, exist_ok=True)
    summary_md.write_text(format_markdown_summary(summary), encoding="utf-8")

    print(summary_md.read_text(encoding="utf-8"))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Stopped by user", flush=True)
        sys.exit(0)