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


def build_sweep_plan(
    images: list[Path],
    panels: int,
    pads_per_panel: int,
    images_per_pad: int,
) -> list[dict[str, object]]:
    if images_per_pad < 1:
        raise ValueError("images_per_pad must be at least 1")
    if panels < 1 or pads_per_panel < 1:
        raise ValueError("panels and pads_per_panel must be at least 1")
    if len(images) < images_per_pad:
        raise ValueError(
            f"Need at least {images_per_pad} distinct images, but only found {len(images)}"
        )

    sweep: list[dict[str, object]] = []
    image_count = len(images)

    for panel_index in range(1, panels + 1):
        for pad_index in range(1, pads_per_panel + 1):
            base_index = ((panel_index - 1) * pads_per_panel + (pad_index - 1)) * images_per_pad
            chosen_images = [images[(base_index + offset) % image_count] for offset in range(images_per_pad)]
            sweep.append(
                {
                    "panel_id": f"panel_{panel_index}",
                    "pad_id": f"pad_{pad_index:02d}",
                    "images": chosen_images,
                }
            )

    return sweep


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run dashboard MQTT test over multiple images at a fixed interval."
    )
    parser.add_argument("--onnx_model", default="best_model_v3_1_goldilocks.onnx")
    parser.add_argument("--captures_dir", default="captures")
    parser.add_argument("--panels", type=int, default=9)
    parser.add_argument("--pads_per_panel", type=int, default=24)
    parser.add_argument("--images_per_pad", type=int, default=4)
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

    images = sorted(
        [p for p in captures_dir.iterdir() if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg"}]
    )
    if len(images) < args.images_per_pad:
        raise ValueError(
            f"Requested {args.images_per_pad} distinct images per pad but only found {len(images)} in {captures_dir}"
        )

    sweep_plan = build_sweep_plan(
        images=images,
        panels=args.panels,
        pads_per_panel=args.pads_per_panel,
        images_per_pad=args.images_per_pad,
    )
    session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])

    total_messages = len(sweep_plan) * args.images_per_pad
    print(
        f"Publishing {total_messages} payloads across {args.panels} panels x {args.pads_per_panel} pads x {args.images_per_pad} images to {args.mqtt_topic} @ {args.mqtt_broker}:{args.mqtt_port}"
    )

    message_index = 0
    for pad_index, entry in enumerate(sweep_plan, start=1):
        panel_id = entry["panel_id"]
        pad_id = entry["pad_id"]
        selected_images = entry["images"]

        for image_offset, image_path in enumerate(selected_images, start=1):
            message_index += 1
            score = infer_severity_score(
                onnx_model_path=str(model_path),
                image_path=str(image_path),
                image_size=args.image_size,
                session=session,
            )

            payload = build_payload(
                panel_id=str(panel_id),
                pad_id=str(pad_id),
                robot_id=f"{args.robot_prefix}_{((message_index - 1) % 3) + 1}",
                model_version=args.model_version,
                severity_score=score,
                critical_threshold=args.critical_threshold,
                image_path=str(image_path),
            )

            payload["test_sequence"] = message_index
            payload["test_total"] = total_messages
            payload["test_panel_index"] = ((pad_index - 1) // args.pads_per_panel) + 1
            payload["test_pad_index"] = ((pad_index - 1) % args.pads_per_panel) + 1
            payload["test_image_index"] = image_offset
            payload["test_images_per_pad"] = args.images_per_pad

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
                        f"[{message_index:04d}/{total_messages}] publish retry {attempt}/{args.publish_retries} failed: {exc}",
                        flush=True,
                    )
                    if attempt < max(1, args.publish_retries):
                        time.sleep(max(0.0, args.retry_wait_seconds))

            if not published:
                raise RuntimeError(f"Failed to publish payload {message_index} after retries: {last_error}")

            print(f"[{message_index:04d}/{total_messages}] published: {json.dumps(payload)}", flush=True)

            if message_index < total_messages:
                time.sleep(max(0.0, args.interval_seconds))

    print("Test complete.", flush=True)


if __name__ == "__main__":
    main()
