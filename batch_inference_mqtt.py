import argparse
import json
import onnxruntime as ort
import time
from pathlib import Path
from typing import Any, Dict, Optional

# Import the necessary functions from intereference_onnx
from intereference_onnx import infer_severity_score, build_payload, publish_mqtt


DEFAULT_MODEL_NAME = "best_sahl_1.5x_final.onnx" # Corrected default model name based on previous context
DEFAULT_CANDIDATE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".webp")


def discover_images(captures_dir: Path) -> list[Path]:
    candidates: list[Path] = []
    for extension in DEFAULT_CANDIDATE_EXTENSIONS:
        candidates.extend(captures_dir.glob(f"*{extension}"))
    return sorted(path for path in candidates if path.is_file())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run ONNX inference across all images in captures and publish results via MQTT."
    )
    parser.add_argument("--onnx_model", type=Path, default=Path(DEFAULT_MODEL_NAME))
    parser.add_argument("--captures_dir", type=Path, default=Path("captures"))
    parser.add_argument("--image_size", type=int, default=224)
    parser.add_argument("--critical_threshold", type=float, default=0.65)

    parser.add_argument("--mqtt_enable", action="store_true", help="Enable MQTT publishing of inference results.")
    parser.add_argument("--mqtt_broker", type=str, default="localhost", help="MQTT broker host.")
    parser.add_argument("--mqtt_port", type=int, default=1883, help="MQTT broker port.")
    parser.add_argument("--mqtt_topic", type=str, default="pv/inspection/severity", help="MQTT topic for publishing.")

    args = parser.parse_args()

    service_dir = Path(__file__).resolve().parent
    onnx_model = (service_dir / args.onnx_model).resolve()
    captures_dir = (service_dir / args.captures_dir).resolve()

    if not onnx_model.exists():
        raise FileNotFoundError(f"ONNX model not found: {onnx_model}")
    if not captures_dir.exists():
        raise FileNotFoundError(f"Captures directory not found: {captures_dir}")

    image_paths = discover_images(captures_dir)
    if not image_paths:
        raise FileNotFoundError(f"No supported images found in {captures_dir}")

    providers = ["CPUExecutionProvider"]
    session = ort.InferenceSession(str(onnx_model), providers=providers)

    # Placeholder for model_version, panel_id, pad_id, robot_id
    # You might want to make these command-line arguments or read from a config
    model_version = "onnx_v1"
    panel_id = "batch_panel"
    pad_id = "batch_pad"
    robot_id = "batch_robot"

    print(f"Starting batch inference on {len(image_paths)} images...")

    for index, image_path in enumerate(image_paths, start=1):
        print(f"Processing image {index}/{len(image_paths)}: {image_path.name}")
        try:
            score = infer_severity_score(
                onnx_model_path=str(onnx_model),
                image_path=str(image_path),
                image_size=args.image_size,
                session=session,
            )

            payload = build_payload(
                panel_id=panel_id,
                pad_id=pad_id,
                robot_id=robot_id,
                model_version=model_version,
                severity_score=score,
                critical_threshold=args.critical_threshold,
                image_path=str(image_path),
            )

            print(json.dumps(payload, indent=2), flush=True)

            if args.mqtt_enable:
                try:
                    publish_mqtt(
                        payload=payload,
                        broker_host=args.mqtt_broker,
                        broker_port=args.mqtt_port,
                        topic=args.mqtt_topic,
                    )
                    print("MQTT publish: success", flush=True)
                except Exception as exc:
                    print(f"MQTT publish failed (non-blocking): {exc}", flush=True)

        except Exception as exc:
            print(f"Error processing {image_path.name}: {exc}", flush=True)
        time.sleep(2)

    print("Batch inference completed.")

if __name__ == "__main__":
    main()