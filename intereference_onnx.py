import argparse
import json
from typing import Dict, Optional

import cv2
import numpy as np
import onnxruntime as ort
import paho.mqtt.client as mqtt


IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def preprocess_el_image(image_path: str, image_size: int = 224) -> np.ndarray:
    gray = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise FileNotFoundError(f"Unable to read image: {image_path}")

    denoised = cv2.medianBlur(gray, 3)

    rgb = np.stack([denoised, denoised, denoised], axis=-1)
    # Match torchvision Resize default behavior used in eval transforms (bilinear-style interpolation).
    resized = cv2.resize(rgb, (image_size, image_size), interpolation=cv2.INTER_LINEAR)

    normalized = resized.astype(np.float32) / 255.0
    normalized = (normalized - IMAGENET_MEAN) / IMAGENET_STD

    chw = np.transpose(normalized, (2, 0, 1))
    batched = np.expand_dims(chw, axis=0).astype(np.float32)
    return batched


def infer_severity_score(
    onnx_model_path: str,
    image_path: str,
    image_size: int = 224,
    session: Optional[ort.InferenceSession] = None,
) -> float:
    if session is None:
        providers = ["CPUExecutionProvider"]
        session = ort.InferenceSession(onnx_model_path, providers=providers)

    input_name = session.get_inputs()[0].name
    model_input = preprocess_el_image(image_path=image_path, image_size=image_size)

    outputs = session.run(None, {input_name: model_input})
    severity_score = float(outputs[0][0, 0])
    return severity_score


def build_payload(
    panel_id: str,
    pad_id: str,
    robot_id: str,
    model_version: str,
    severity_score: float,
    critical_threshold: float = 0.80,
    image_path: Optional[str] = None,
) -> Dict[str, object]:
    status = "CRITICAL" if severity_score > critical_threshold else "OK"
    return {
        "panel_id": panel_id,
        "pad_id": pad_id,
        "robot_id": robot_id,
        "model_version": model_version,
        "severity_score": round(severity_score, 4),
        "status": status,
        "image_path": image_path,
    }


def publish_mqtt(payload: Dict[str, object], broker_host: str, broker_port: int, topic: str) -> None:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect(broker_host, broker_port, keepalive=30)
    client.publish(topic, json.dumps(payload), qos=0, retain=False)
    client.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description="ONNX Runtime EL inference with MQTT mock payload.")
    parser.add_argument("--onnx_model", type=str, default="best_model.onnx")
    parser.add_argument("--image_path", type=str, required=True)
    parser.add_argument("--panel_id", type=str, default="panel_A")
    parser.add_argument("--pad_id", type=str, default="simulated_pad_01")
    parser.add_argument("--robot_id", type=str, default="robot_01")
    parser.add_argument("--model_version", type=str, default="onnx_v1")
    parser.add_argument("--critical_threshold", type=float, default=0.80)
    parser.add_argument("--image_size", type=int, default=224)

    parser.add_argument("--mqtt_enable", action="store_true")
    parser.add_argument("--mqtt_broker", type=str, default="localhost")
    parser.add_argument("--mqtt_port", type=int, default=1883)
    parser.add_argument("--mqtt_topic", type=str, default="pv/inspection/severity")

    args = parser.parse_args()

    providers = ["CPUExecutionProvider"]
    session = ort.InferenceSession(args.onnx_model, providers=providers)

    score = infer_severity_score(
        onnx_model_path=args.onnx_model,
        image_path=args.image_path,
        image_size=args.image_size,
        session=session,
    )

    payload = build_payload(
        panel_id=args.panel_id,
        pad_id=args.pad_id,
        robot_id=args.robot_id,
        model_version=args.model_version,
        severity_score=score,
        critical_threshold=args.critical_threshold,
        image_path=args.image_path,
    )

    print(json.dumps(payload, indent=2))

    if args.mqtt_enable:
        try:
            publish_mqtt(
                payload=payload,
                broker_host=args.mqtt_broker,
                broker_port=args.mqtt_port,
                topic=args.mqtt_topic,
            )
            print("MQTT publish: success")
        except Exception as exc:
            print(f"MQTT publish failed (non-blocking): {exc}")


if __name__ == "__main__":
    main()
