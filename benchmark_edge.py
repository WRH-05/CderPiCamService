#!/usr/bin/env python3
import argparse
import csv
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import onnxruntime as ort

from intereference_onnx import preprocess_el_image

try:
    import psutil
except ImportError:  # pragma: no cover - optional dependency for Pi deployments
    psutil = None


DEFAULT_MODEL_NAME = Path("model") / "best_sahl_1.5x_final.onnx"
DEFAULT_CAPTURES_DIR = Path("captures")
DEFAULT_CANDIDATE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".webp")
DEFAULT_EXPECTED_IMAGE_COUNT = 397


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fmt_ms(value: float) -> str:
    return f"{value:.2f}"


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


def write_csv_rows(csv_path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def build_latency_summary(latencies_ms: list[float]) -> dict[str, float]:
    if not latencies_ms:
        raise RuntimeError("No latency samples were recorded")

    mean_ms = statistics.fmean(latencies_ms)
    stddev_ms = statistics.pstdev(latencies_ms) if len(latencies_ms) > 1 else 0.0
    median_ms = statistics.median(latencies_ms)
    p95_ms = percentile(latencies_ms, 0.95)
    min_ms = min(latencies_ms)
    max_ms = max(latencies_ms)
    total_s = sum(latencies_ms) / 1000.0
    fps = len(latencies_ms) / total_s if total_s > 0 else 0.0

    return {
        "mean_ms": mean_ms,
        "stddev_ms": stddev_ms,
        "median_ms": median_ms,
        "p95_ms": p95_ms,
        "min_ms": min_ms,
        "max_ms": max_ms,
        "throughput_fps": fps,
    }


def format_markdown_summary(summary: dict[str, object]) -> str:
    onnx = summary["onnx_primary"]
    e2e = summary["end_to_end_secondary"]

    lines = [
        "# Edge Sustained Benchmark Summary",
        "",
        "## Run configuration",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Model | {summary['model_path']} |",
        f"| Captures directory | {summary['captures_dir']} |",
        f"| Image count | {summary['image_count']} |",
        f"| Loops | {summary['loops']} |",
        f"| Warmup runs | {summary['warmup_runs']} |",
        f"| Total measured inferences | {summary['measured_inferences']} |",
        "",
        "## Primary latency metric (ONNX-only)",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Mean ± StdDev (ms) | {onnx['mean_ms']:.2f} +/- {onnx['stddev_ms']:.2f} |",
        f"| Median (ms) | {onnx['median_ms']:.2f} |",
        f"| P95 (ms) | {onnx['p95_ms']:.2f} |",
        f"| Min (ms) | {onnx['min_ms']:.2f} |",
        f"| Max (ms) | {onnx['max_ms']:.2f} |",
        f"| Throughput (FPS) | {onnx['throughput_fps']:.2f} |",
        "",
        "## Secondary latency metric (End-to-end)",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Mean ± StdDev (ms) | {e2e['mean_ms']:.2f} +/- {e2e['stddev_ms']:.2f} |",
        f"| Median (ms) | {e2e['median_ms']:.2f} |",
        f"| P95 (ms) | {e2e['p95_ms']:.2f} |",
        f"| Min (ms) | {e2e['min_ms']:.2f} |",
        f"| Max (ms) | {e2e['max_ms']:.2f} |",
        f"| Throughput (FPS) | {e2e['throughput_fps']:.2f} |",
        "",
        "## Memory stability (RSS)",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| RSS start (MB) | {fmt_mb(summary['rss_start_mb'])} |",
        f"| RSS after session load (MB) | {fmt_mb(summary['rss_after_session_mb'])} |",
        f"| RSS peak (MB) | {fmt_mb(summary['rss_peak_mb'])} |",
        f"| RSS end (MB) | {fmt_mb(summary['rss_end_mb'])} |",
        f"| Session load delta (MB) | {fmt_mb(summary['rss_session_delta_mb'])} |",
        f"| Start to end delta (MB) | {fmt_mb(summary['rss_start_to_end_delta_mb'])} |",
        "",
    ]

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run sustained ONNX deployment benchmark over capture images with latency jitter and RSS reporting."
    )
    parser.add_argument("--onnx_model", type=Path, default=DEFAULT_MODEL_NAME)
    parser.add_argument("--captures_dir", type=Path, default=DEFAULT_CAPTURES_DIR)
    parser.add_argument("--image_size", type=int, default=224)
    parser.add_argument("--critical_threshold", type=float, default=0.65)
    parser.add_argument("--loops", type=int, default=4)
    parser.add_argument("--warmup_runs", type=int, default=10)
    parser.add_argument("--expected_image_count", type=int, default=DEFAULT_EXPECTED_IMAGE_COUNT)
    parser.add_argument("--allow_non_expected_count", action="store_true")
    parser.add_argument("--output_csv", type=Path, default=Path("benchmark_edge_runs.csv"))
    parser.add_argument("--summary_csv", type=Path, default=Path("benchmark_edge_summary.csv"))
    parser.add_argument("--summary_md", type=Path, default=Path("benchmark_edge_summary.md"))
    args = parser.parse_args()

    loops = max(0, args.loops)
    warmup_runs = max(0, args.warmup_runs)

    service_dir = Path(__file__).resolve().parent
    model_path = (service_dir / args.onnx_model).resolve()
    captures_dir = (service_dir / args.captures_dir).resolve()
    output_csv = (service_dir / args.output_csv).resolve()
    summary_csv = (service_dir / args.summary_csv).resolve()
    summary_md = (service_dir / args.summary_md).resolve()

    if not model_path.exists():
        raise FileNotFoundError(f"ONNX model not found: {model_path}")
    if not captures_dir.exists():
        raise FileNotFoundError(f"Captures directory not found: {captures_dir}")

    image_paths = discover_images(captures_dir)
    if not image_paths:
        raise FileNotFoundError(f"No supported images found in {captures_dir}")

    if not args.allow_non_expected_count and len(image_paths) != args.expected_image_count:
        raise RuntimeError(
            f"Expected exactly {args.expected_image_count} images in {captures_dir}, found {len(image_paths)}. "
            "Use --allow_non_expected_count to bypass strict dataset size checks."
        )

    providers = ["CPUExecutionProvider"]
    rss_start_mb = collect_process_rss_mb()
    session = ort.InferenceSession(str(model_path), providers=providers)
    rss_after_session_mb = collect_process_rss_mb()

    input_name = session.get_inputs()[0].name

    if image_paths:
        warmup_input = preprocess_el_image(str(image_paths[0]), image_size=args.image_size)
        for _ in range(warmup_runs):
            session.run(None, {input_name: warmup_input})

    rows: list[dict[str, object]] = []
    onnx_latencies_ms: list[float] = []
    e2e_latencies_ms: list[float] = []
    failed_count = 0

    rss_peak_mb = rss_after_session_mb

    total_iterations = len(image_paths) * loops
    for loop_index in range(1, loops + 1):
        for image_index, image_path in enumerate(image_paths, start=1):
            global_index = (loop_index - 1) * len(image_paths) + image_index
            rss_before_run_mb = collect_process_rss_mb()

            score: Optional[float] = None
            status = "ERROR"
            error = ""

            e2e_start = time.perf_counter()
            try:
                e2e_input = preprocess_el_image(str(image_path), image_size=args.image_size)
                session.run(None, {input_name: e2e_input})
                e2e_latency_ms = (time.perf_counter() - e2e_start) * 1000.0

                onnx_start = time.perf_counter()
                outputs = session.run(None, {input_name: e2e_input})
                onnx_latency_ms = (time.perf_counter() - onnx_start) * 1000.0

                score = float(outputs[0][0, 0])
                status = "CRITICAL" if score > args.critical_threshold else "OK"
                onnx_latencies_ms.append(onnx_latency_ms)
                e2e_latencies_ms.append(e2e_latency_ms)
            except Exception as exc:
                e2e_latency_ms = (time.perf_counter() - e2e_start) * 1000.0
                onnx_latency_ms = 0.0
                error = str(exc)
                failed_count += 1

            rss_after_run_mb = collect_process_rss_mb()
            if rss_after_run_mb is not None:
                rss_peak_mb = rss_after_run_mb if rss_peak_mb is None else max(rss_peak_mb, rss_after_run_mb)

            rows.append(
                {
                    "timestamp_utc": utc_now(),
                    "global_index": global_index,
                    "loop_index": loop_index,
                    "image_index": image_index,
                    "image_path": str(image_path),
                    "severity_score": None if score is None else round(score, 6),
                    "status": status,
                    "onnx_latency_ms": round(onnx_latency_ms, 4),
                    "end_to_end_latency_ms": round(e2e_latency_ms, 4),
                    "rss_before_run_mb": None if rss_before_run_mb is None else round(rss_before_run_mb, 4),
                    "rss_after_run_mb": None if rss_after_run_mb is None else round(rss_after_run_mb, 4),
                    "rss_delta_mb": None
                    if (rss_before_run_mb is None or rss_after_run_mb is None)
                    else round(rss_after_run_mb - rss_before_run_mb, 4),
                    "error": error,
                }
            )

            if global_index % 100 == 0 or global_index == total_iterations:
                print(f"Progress: {global_index}/{total_iterations}", flush=True)

    if not onnx_latencies_ms:
        raise RuntimeError("No successful inferences were recorded")

    rss_end_mb = collect_process_rss_mb()

    rss_session_delta_mb = None
    if rss_start_mb is not None and rss_after_session_mb is not None:
        rss_session_delta_mb = rss_after_session_mb - rss_start_mb

    rss_start_to_end_delta_mb = None
    if rss_start_mb is not None and rss_end_mb is not None:
        rss_start_to_end_delta_mb = rss_end_mb - rss_start_mb

    onnx_summary = build_latency_summary(onnx_latencies_ms)
    e2e_summary = build_latency_summary(e2e_latencies_ms)

    summary = {
        "generated_at_utc": utc_now(),
        "model_path": str(model_path),
        "captures_dir": str(captures_dir),
        "image_count": len(image_paths),
        "loops": loops,
        "warmup_runs": warmup_runs,
        "measured_inferences": len(onnx_latencies_ms),
        "failed_inferences": failed_count,
        "onnx_mean_latency_ms": onnx_summary["mean_ms"],
        "onnx_stddev_latency_ms": onnx_summary["stddev_ms"],
        "onnx_median_latency_ms": onnx_summary["median_ms"],
        "onnx_p95_latency_ms": onnx_summary["p95_ms"],
        "onnx_min_latency_ms": onnx_summary["min_ms"],
        "onnx_max_latency_ms": onnx_summary["max_ms"],
        "onnx_throughput_fps": onnx_summary["throughput_fps"],
        "e2e_mean_latency_ms": e2e_summary["mean_ms"],
        "e2e_stddev_latency_ms": e2e_summary["stddev_ms"],
        "e2e_median_latency_ms": e2e_summary["median_ms"],
        "e2e_p95_latency_ms": e2e_summary["p95_ms"],
        "e2e_min_latency_ms": e2e_summary["min_ms"],
        "e2e_max_latency_ms": e2e_summary["max_ms"],
        "e2e_throughput_fps": e2e_summary["throughput_fps"],
        "rss_start_mb": rss_start_mb,
        "rss_after_session_mb": rss_after_session_mb,
        "rss_peak_mb": rss_peak_mb,
        "rss_end_mb": rss_end_mb,
        "rss_session_delta_mb": rss_session_delta_mb,
        "rss_start_to_end_delta_mb": rss_start_to_end_delta_mb,
        "onnx_mean_plus_minus_stddev_ms": f"{fmt_ms(onnx_summary['mean_ms'])} +/- {fmt_ms(onnx_summary['stddev_ms'])}",
        "e2e_mean_plus_minus_stddev_ms": f"{fmt_ms(e2e_summary['mean_ms'])} +/- {fmt_ms(e2e_summary['stddev_ms'])}",
    }

    write_csv_rows(
        output_csv,
        [
            "timestamp_utc",
            "global_index",
            "loop_index",
            "image_index",
            "image_path",
            "severity_score",
            "status",
            "onnx_latency_ms",
            "end_to_end_latency_ms",
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

    summary_doc = {
        "model_path": summary["model_path"],
        "captures_dir": summary["captures_dir"],
        "image_count": summary["image_count"],
        "loops": summary["loops"],
        "warmup_runs": summary["warmup_runs"],
        "measured_inferences": summary["measured_inferences"],
        "onnx_primary": {
            "mean_ms": summary["onnx_mean_latency_ms"],
            "stddev_ms": summary["onnx_stddev_latency_ms"],
            "median_ms": summary["onnx_median_latency_ms"],
            "p95_ms": summary["onnx_p95_latency_ms"],
            "min_ms": summary["onnx_min_latency_ms"],
            "max_ms": summary["onnx_max_latency_ms"],
            "throughput_fps": summary["onnx_throughput_fps"],
        },
        "end_to_end_secondary": {
            "mean_ms": summary["e2e_mean_latency_ms"],
            "stddev_ms": summary["e2e_stddev_latency_ms"],
            "median_ms": summary["e2e_median_latency_ms"],
            "p95_ms": summary["e2e_p95_latency_ms"],
            "min_ms": summary["e2e_min_latency_ms"],
            "max_ms": summary["e2e_max_latency_ms"],
            "throughput_fps": summary["e2e_throughput_fps"],
        },
        "rss_start_mb": summary["rss_start_mb"],
        "rss_after_session_mb": summary["rss_after_session_mb"],
        "rss_peak_mb": summary["rss_peak_mb"],
        "rss_end_mb": summary["rss_end_mb"],
        "rss_session_delta_mb": summary["rss_session_delta_mb"],
        "rss_start_to_end_delta_mb": summary["rss_start_to_end_delta_mb"],
    }

    summary_md.parent.mkdir(parents=True, exist_ok=True)
    summary_md.write_text(format_markdown_summary(summary_doc), encoding="utf-8")

    print(summary_md.read_text(encoding="utf-8"))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Stopped by user", flush=True)
        sys.exit(0)