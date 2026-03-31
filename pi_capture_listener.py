#!/usr/bin/env python3
import argparse
import csv
import importlib.util
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import cv2
import serial
from serial.tools import list_ports


DEFAULT_TRIGGER_TEXT = "[CAM] HIGH"


def load_inference_module(script_path: Path) -> Any:
    if not script_path.exists():
        raise FileNotFoundError(f"Inference script not found: {script_path}")

    spec = importlib.util.spec_from_file_location("intereference_onnx", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {script_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pick_camera_command() -> str:
    # Bookworm uses rpicam-still, older images use libcamera-still.
    for command in ["rpicam-still", "libcamera-still"]:
        if shutil.which(command):
            return command
    raise RuntimeError("Neither rpicam-still nor libcamera-still is available on this Pi.")


def capture_image_libcamera(output_path: Path, shutter_us: int, width: Optional[int], height: Optional[int]) -> None:
    camera_cmd = pick_camera_command()

    cmd = [
        camera_cmd,
        "-o",
        str(output_path),
        "--shutter",
        str(shutter_us),
        "--nopreview",
    ]

    if width is not None:
        cmd.extend(["--width", str(width)])
    if height is not None:
        cmd.extend(["--height", str(height)])

    subprocess.run(cmd, check=True)


def capture_image_webcam(
    output_path: Path,
    webcam_index: int,
    width: Optional[int],
    height: Optional[int],
    settle_seconds: float,
    capture_delay_seconds: float,
) -> None:
    # Prefer V4L2 on Linux for USB webcams; fallback to default backend if needed.
    cap = cv2.VideoCapture(webcam_index, cv2.CAP_V4L2)
    if not cap.isOpened():
        cap.release()
        cap = cv2.VideoCapture(webcam_index)

    if not cap.isOpened():
        raise RuntimeError(f"Unable to open USB webcam at index {webcam_index}")

    try:
        if width is not None:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(width))
        if height is not None:
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(height))

        settle_until = time.time() + max(0.0, settle_seconds)
        while time.time() < settle_until:
            cap.read()
            time.sleep(0.03)

        delay_until = time.time() + max(0.0, capture_delay_seconds)
        while time.time() < delay_until:
            cap.read()
            time.sleep(0.03)

        frame = None
        for _ in range(6):
            ok, current = cap.read()
            if ok and current is not None:
                frame = current
            time.sleep(0.02)

        if frame is None:
            raise RuntimeError("Webcam capture failed: no frame received")

        if not cv2.imwrite(str(output_path), frame):
            raise RuntimeError(f"Failed to write image to {output_path}")
    finally:
        cap.release()


def make_capture_path(captures_dir: Path) -> Path:
    captures_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return captures_dir / f"el_{timestamp}.jpg"


def append_csv_log(csv_path: Path, row: dict[str, object]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = csv_path.exists()

    with csv_path.open("a", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(
            fp,
            fieldnames=[
                "timestamp_utc",
                "image_path",
                "pad_id",
                "severity_score",
                "status",
                "trigger_line",
                "camera_backend",
            ],
        )
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def process_capture(
    inference_module: Any,
    onnx_model: Path,
    image_path: Path,
    image_size: int,
    pad_id: str,
    critical_threshold: float,
    mqtt_enable: bool,
    mqtt_broker: str,
    mqtt_port: int,
    mqtt_topic: str,
    session: Optional[Any],
) -> Optional[Any]:
    score = inference_module.infer_severity_score(
        onnx_model_path=str(onnx_model),
        image_path=str(image_path),
        image_size=image_size,
        session=session,
    )

    payload = inference_module.build_payload(
        pad_id=pad_id,
        severity_score=score,
        critical_threshold=critical_threshold,
    )

    print(json.dumps(payload, indent=2), flush=True)

    if mqtt_enable:
        try:
            inference_module.publish_mqtt(
                payload=payload,
                broker_host=mqtt_broker,
                broker_port=mqtt_port,
                topic=mqtt_topic,
            )
            print("MQTT publish: success", flush=True)
        except Exception as exc:
            print(f"MQTT publish failed (non-blocking): {exc}", flush=True)

    return payload


def open_serial_with_retry(port: str, baudrate: int, timeout: float, retry_seconds: float) -> serial.Serial:
    while True:
        try:
            ser = serial.Serial(port=port, baudrate=baudrate, timeout=timeout)
            print(f"Connected to ESP32 on {port} @ {baudrate}", flush=True)
            return ser
        except serial.SerialException as exc:
            print(f"Waiting for serial port {port}: {exc}", flush=True)
            time.sleep(retry_seconds)


def scan_serial_ports() -> list[str]:
    ports: list[str] = []
    for p in list_ports.comports():
        desc = p.description or "Unknown"
        hwid = p.hwid or ""
        ports.append(f"{p.device} | {desc} | {hwid}")
    return ports


def probe_webcam_indexes(max_index: int) -> list[int]:
    detected: list[int] = []
    for idx in range(max_index + 1):
        cap = cv2.VideoCapture(idx, cv2.CAP_V4L2)
        if not cap.isOpened():
            cap.release()
            cap = cv2.VideoCapture(idx)

        if cap.isOpened():
            ok, frame = cap.read()
            if ok and frame is not None:
                detected.append(idx)
        cap.release()
    return detected


def print_startup_device_scan(serial_port: str, webcam_index: int, webcam_probe_max_index: int) -> None:
    print("[SCAN] Startup device scan", flush=True)
    serial_ports = scan_serial_ports()
    if serial_ports:
        print("[SCAN] Serial ports:", flush=True)
        for item in serial_ports:
            print(f"  - {item}", flush=True)
    else:
        print("[SCAN] Serial ports: none found", flush=True)

    webcams = probe_webcam_indexes(webcam_probe_max_index)
    if webcams:
        indexes = ", ".join(str(i) for i in webcams)
        print(f"[SCAN] Webcam indexes available: {indexes}", flush=True)
    else:
        print("[SCAN] Webcam indexes available: none found", flush=True)

    print(
        f"[SCAN] Configured defaults -> serial_port={serial_port}, webcam_index={webcam_index}",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Raspberry Pi USB trigger listener for camera + ONNX inference")

    parser.add_argument("--serial_port", default="/dev/ttyUSB0")
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument("--serial_timeout", type=float, default=1.0)
    parser.add_argument("--reconnect_seconds", type=float, default=2.0)
    parser.add_argument("--trigger_text", default=DEFAULT_TRIGGER_TEXT)

    parser.add_argument("--captures_dir", default="captures")
    parser.add_argument("--camera_backend", choices=["webcam", "libcamera"], default="webcam")
    parser.add_argument("--shutter_us", type=int, default=15000000)
    parser.add_argument("--camera_width", type=int, default=None)
    parser.add_argument("--camera_height", type=int, default=None)
    parser.add_argument("--webcam_index", type=int, default=0)
    parser.add_argument("--webcam_probe_max_index", type=int, default=5)
    parser.add_argument("--webcam_settle_seconds", type=float, default=1.0)
    parser.add_argument("--capture_delay_seconds", type=float, default=15.0)

    parser.add_argument("--inference_script", default="intereference_onnx.py")
    parser.add_argument("--onnx_model", default="best_model.onnx")
    parser.add_argument("--image_size", type=int, default=224)
    parser.add_argument("--pad_id", default="simulated_pad_01")
    parser.add_argument("--critical_threshold", type=float, default=0.65)

    parser.add_argument("--mqtt_enable", action="store_true")
    parser.add_argument("--mqtt_broker", default="localhost")
    parser.add_argument("--mqtt_port", type=int, default=1883)
    parser.add_argument("--mqtt_topic", default="pv/inspection/severity")
    parser.add_argument("--csv_log", default="inference_log.csv")
    parser.add_argument("--no_startup_scan", action="store_true")

    args = parser.parse_args()

    service_dir = Path(__file__).resolve().parent
    inference_script = (service_dir / args.inference_script).resolve()
    onnx_model = (service_dir / args.onnx_model).resolve()
    captures_dir = (service_dir / args.captures_dir).resolve()
    csv_log_path = (service_dir / args.csv_log).resolve()

    if not onnx_model.exists():
        raise FileNotFoundError(f"ONNX model not found: {onnx_model}")

    inference_module = load_inference_module(inference_script)

    session = None
    try:
        providers = ["CPUExecutionProvider"]
        session = inference_module.ort.InferenceSession(str(onnx_model), providers=providers)
    except Exception as exc:
        print(f"ONNX session pre-load failed, fallback to lazy init: {exc}", flush=True)

    trigger_text = args.trigger_text.strip()
    if not trigger_text:
        raise ValueError("--trigger_text must not be empty")

    if not args.no_startup_scan:
        print_startup_device_scan(
            serial_port=args.serial_port,
            webcam_index=args.webcam_index,
            webcam_probe_max_index=args.webcam_probe_max_index,
        )

    while True:
        ser = open_serial_with_retry(
            port=args.serial_port,
            baudrate=args.baudrate,
            timeout=args.serial_timeout,
            retry_seconds=args.reconnect_seconds,
        )

        try:
            while True:
                line = ser.readline().decode("utf-8", errors="ignore").strip()
                if not line:
                    continue

                print(f"Serial RX: {line}", flush=True)

                if trigger_text not in line:
                    continue

                image_path = make_capture_path(captures_dir)
                print(f"Trigger matched ({trigger_text}); capturing {image_path.name}", flush=True)

                try:
                    if args.camera_backend == "webcam":
                        capture_image_webcam(
                            output_path=image_path,
                            webcam_index=args.webcam_index,
                            width=args.camera_width,
                            height=args.camera_height,
                            settle_seconds=args.webcam_settle_seconds,
                            capture_delay_seconds=args.capture_delay_seconds,
                        )
                    else:
                        capture_image_libcamera(
                            output_path=image_path,
                            shutter_us=args.shutter_us,
                            width=args.camera_width,
                            height=args.camera_height,
                        )
                    payload = process_capture(
                        inference_module=inference_module,
                        onnx_model=onnx_model,
                        image_path=image_path,
                        image_size=args.image_size,
                        pad_id=args.pad_id,
                        critical_threshold=args.critical_threshold,
                        mqtt_enable=args.mqtt_enable,
                        mqtt_broker=args.mqtt_broker,
                        mqtt_port=args.mqtt_port,
                        mqtt_topic=args.mqtt_topic,
                        session=session,
                    )

                    if isinstance(payload, dict):
                        append_csv_log(
                            csv_path=csv_log_path,
                            row={
                                "timestamp_utc": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                                "image_path": str(image_path),
                                "pad_id": payload.get("pad_id", args.pad_id),
                                "severity_score": payload.get("severity_score", ""),
                                "status": payload.get("status", ""),
                                "trigger_line": line,
                                "camera_backend": args.camera_backend,
                            },
                        )
                except Exception as exc:
                    print(f"Capture/inference failed: {exc}", flush=True)

        except serial.SerialException as exc:
            print(f"Serial disconnected: {exc}", flush=True)
            time.sleep(args.reconnect_seconds)
        finally:
            try:
                ser.close()
            except Exception:
                pass


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Stopped by user", flush=True)
        sys.exit(0)
