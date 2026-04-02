import argparse
import json
import time
from pathlib import Path

import onnxruntime as ort

from intereference_onnx import build_payload, infer_severity_score, publish_mqtt


def pick_images(captures_dir: Path, count: int) -> list[Path]:
    images = sorted(
        [p for p in captures_dir.iterdir() if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg"}]
    )
    if len(images) < count:
        raise ValueError(f"Requested {count} images but only found {len(images)} in {captures_dir}")
    return images[:count]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run dashboard MQTT test over multiple images at a fixed interval."
    )
    parser.add_argument("--onnx_model", default="best_model_v3_1_goldilocks.onnx")
    parser.add_argument("--captures_dir", default="captures")
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--interval_seconds", type=float, default=5.0)
    parser.add_argument("--image_size", type=int, default=224)
    parser.add_argument("--critical_threshold", type=float, default=0.65)

    parser.add_argument("--mqtt_broker", default="test.mosquitto.org")
    parser.add_argument("--mqtt_port", type=int, default=1883)
    parser.add_argument("--mqtt_topic", default="pv/inspection/severity")
    parser.add_argument("--publish_retries", type=int, default=5)
    parser.add_argument("--retry_wait_seconds", type=float, default=1.0)

    parser.add_argument("--panel_prefix", default="panel")
    parser.add_argument("--pad_prefix", default="pad")
    parser.add_argument("--robot_prefix", default="robot")
    parser.add_argument("--model_version", default="onnx_v1")

    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    model_path = (root / args.onnx_model).resolve()
    captures_dir = (root / args.captures_dir).resolve()

    if not model_path.exists():
        raise FileNotFoundError(f"Model not found: {model_path}")
    if not captures_dir.exists():
        raise FileNotFoundError(f"Capture directory not found: {captures_dir}")

    images = pick_images(captures_dir, args.count)
    session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])

    print(f"Publishing {len(images)} payloads to {args.mqtt_topic} @ {args.mqtt_broker}:{args.mqtt_port}")

    for i, image_path in enumerate(images, start=1):
        score = infer_severity_score(
            onnx_model_path=str(model_path),
            image_path=str(image_path),
            image_size=args.image_size,
            session=session,
        )

        payload = build_payload(
            panel_id=f"{args.panel_prefix}_{((i - 1) % 4) + 1}",
            pad_id=f"{args.pad_prefix}_{i:02d}",
            robot_id=f"{args.robot_prefix}_{((i - 1) % 3) + 1}",
            model_version=args.model_version,
            severity_score=score,
            critical_threshold=args.critical_threshold,
            image_path=str(image_path),
        )

        payload["test_sequence"] = i
        payload["test_total"] = len(images)

        published = False
        last_error = None
        for attempt in range(1, max(1, args.publish_retries) + 1):
            try:
                publish_mqtt(
                    payload=payload,
                    broker_host=args.mqtt_broker,
                    broker_port=args.mqtt_port,
                    topic=args.mqtt_topic,
                )
                published = True
                break
            except Exception as exc:
                last_error = exc
                print(
                    f"[{i:02d}/{len(images)}] publish retry {attempt}/{args.publish_retries} failed: {exc}",
                    flush=True,
                )
                if attempt < max(1, args.publish_retries):
                    time.sleep(max(0.0, args.retry_wait_seconds))

        if not published:
            raise RuntimeError(f"Failed to publish payload {i} after retries: {last_error}")

        print(f"[{i:02d}/{len(images)}] published: {json.dumps(payload)}", flush=True)

        if i < len(images):
            time.sleep(max(0.0, args.interval_seconds))

    print("Test complete.", flush=True)


if __name__ == "__main__":
    main()
