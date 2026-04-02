# Raspberry Pi Camera Service (USB Trigger + ONNX)

This folder is a Pi-side service that waits for a serial trigger from ESP32 over USB.
When trigger text is received, it:

1. Captures an image from a USB webcam.
2. Waits 15 seconds before the capture (default) to match your scan timing.
3. Runs ONNX inference using local `intereference_onnx.py`.
4. Prints payload JSON and can publish MQTT (default broker port 1883).
5. Appends each inference result to a CSV log file (`inference_log.csv`).

It also includes standalone benchmarking and batch-inference scripts so you can measure Raspberry Pi 4 performance without changing the listener.

The ESP32 firmware project can stay separate and contain no Python files.

## Expected Folder Layout on Pi

Use only this folder on the Pi:

```
pi_camera_service/
  intereference_onnx.py
  best_model.onnx
  best_model_v3_1_goldilocks.onnx
  pi_capture_listener.py
  benchmark_onnx.py
  batch_inference_captures.py
  requirements.txt
  pi_camera_listener.service
  README.md
  captures/
```

## ESP32 -> Pi Trigger Format

Default trigger text is `[CAM] HIGH` (matches your current ESP32 serial log).
If ESP32 sends a line containing this text, the Pi starts capture+inference.

## USB Connections

- Plug ESP32 into Pi USB (serial trigger channel).
- Plug USB webcam into Pi USB (image source).
- Pi uses both USB devices in parallel.

## Install

```bash
cd ~/pi_camera_service
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Detailed Deployment (Model + venv + Service)

Follow this once on a fresh Pi.

1. Copy folder to Pi

```bash
cd ~
# Example if you transferred a zip and extracted it already:
ls ~/pi_camera_service
```

2. Place model files

- Required: `best_model.onnx`
- Optional: `best_model.onnx.data` (only if your ONNX export uses external data)

```bash
cd ~/pi_camera_service
ls -lh best_model.onnx
```

If `best_model.onnx` is missing, the listener exits at startup with a clear error.

3. Create virtual environment and install Python dependencies

```bash
cd ~/pi_camera_service
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

4. Run once manually (recommended before enabling service)

```bash
cd ~/pi_camera_service
source .venv/bin/activate
python pi_capture_listener.py \
  --serial_port /dev/ttyUSB0 \
  --baudrate 115200 \
  --camera_backend webcam \
  --webcam_index 0 \
  --capture_delay_seconds 15
```

At startup you should see:

- serial ports discovered
- webcam indexes discovered
- chosen `serial_port` and `webcam_index`

5. Verify outputs after one trigger

```bash
cd ~/pi_camera_service
ls -lh captures | tail
ls -lh inference_log.csv
tail -n 5 inference_log.csv
```

6. Configure and enable systemd service

First make sure `pi_camera_listener.service` path values match your setup:

- `User=pi`
- `WorkingDirectory=/home/pi/pi_camera_service`
- `ExecStart=/home/pi/pi_camera_service/.venv/bin/python ...`

Then install:

```bash
cd ~/pi_camera_service
sudo cp pi_camera_listener.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable pi_camera_listener.service
sudo systemctl start pi_camera_listener.service
sudo systemctl status pi_camera_listener.service
```

7. Service logs and restart controls

```bash
sudo journalctl -u pi_camera_listener.service -f
sudo systemctl restart pi_camera_listener.service
sudo systemctl stop pi_camera_listener.service
```

8. Device permissions (if service cannot access serial/webcam)

```bash
sudo usermod -aG dialout,video pi
```

Reboot once after changing groups:

```bash
sudo reboot
```

Install camera tools if needed:

```bash
sudo apt update
sudo apt install -y libcamera-apps
```

Note: `libcamera-apps` is only needed if you later switch to `--camera_backend libcamera`.

## Raspberry Pi 4 Performance Benchmark

Use the benchmark script to report the three metrics reviewers usually ask for: latency, memory footprint, and throughput.

```bash
cd ~/pi_camera_service
source .venv/bin/activate
python benchmark_onnx.py \
  --onnx_model best_model_v3_1_goldilocks.onnx \
  --warmup_runs 10 \
  --measured_runs 100 \
  --output_csv benchmark_runs.csv \
  --summary_csv benchmark_summary.csv \
  --summary_md benchmark_summary.md
```

Outputs:

- `benchmark_runs.csv`: one row per measured inference run.
- `benchmark_summary.csv`: aggregate metrics for the run.
- `benchmark_summary.md`: paper-ready table with the headline numbers.

If you want to force a specific image, add `--image_path path/to/image.png`.

The most useful values to report are:

- Average latency per image in milliseconds.
- RSS-based memory footprint before and after session creation, plus after warmup.
- Throughput in frames per second, computed from the measured runs.

## Batch Inference Over Captures

To run inference across every image already captured on the Pi:

```bash
cd ~/pi_camera_service
source .venv/bin/activate
python batch_inference_captures.py \
  --onnx_model best_model_v3_1_goldilocks.onnx \
  --captures_dir captures \
  --output_csv batch_inference_results.csv \
  --summary_csv batch_inference_summary.csv \
  --summary_md batch_inference_summary.md
```

Outputs:

- `batch_inference_results.csv`: one row per image, including latency and any error string.
- `batch_inference_summary.csv`: overall counts and latency summary.
- `batch_inference_summary.md`: compact table for reporting.

If you want to keep the run comparable to the benchmark table, use the same model file and `--image_size` value in both scripts.

## Run

```bash
cd ~/pi_camera_service
source .venv/bin/activate
python pi_capture_listener.py \
  --serial_port /dev/ttyUSB0 \
  --baudrate 115200 \
  --camera_backend webcam \
  --webcam_index 0 \
  --capture_delay_seconds 15
```

On startup, the script automatically prints:

- detected serial ports
- detected webcam indexes (probe range 0..5 by default)
- currently configured `serial_port` and `webcam_index`

## Important Defaults

- Serial port: `/dev/ttyUSB0`
- Baud: `115200`
- Trigger text: `[CAM] HIGH`
- Camera backend: `webcam`
- Webcam index: `0`
- Capture delay: `15` seconds
- Critical threshold: `0.65`
- MQTT broker: `localhost`
- MQTT port: `1883`
- MQTT topic: `pv/inspection/severity`
- CSV log path: `inference_log.csv`

## Optional MQTT

Add `--mqtt_enable` to publish the payload.

Example:

```bash
python pi_capture_listener.py --mqtt_enable
```

## Common Adjustments

If your board enumerates as ACM instead of USB:

```bash
python pi_capture_listener.py --serial_port /dev/ttyACM0
```

If ESP32 sends a different token:

```bash
python pi_capture_listener.py --trigger_text TAKE_SHOT
```

If you need fixed capture resolution:

```bash
python pi_capture_listener.py --camera_width 1920 --camera_height 1080
```

If your webcam is not device index 0:

```bash
python pi_capture_listener.py --webcam_index 1
```

If you want to probe more webcam indexes at startup:

```bash
python pi_capture_listener.py --webcam_probe_max_index 10
```

If you want to disable startup scanning:

```bash
python pi_capture_listener.py --no_startup_scan
```

If you want immediate capture on trigger (no delay):

```bash
python pi_capture_listener.py --capture_delay_seconds 0
```

If you want a custom CSV path:

```bash
python pi_capture_listener.py --csv_log logs/inference_results.csv
```

## Optional Auto-Start with systemd

This folder includes `pi_camera_listener.service`.

Copy and enable it:

```bash
sudo cp pi_camera_listener.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable pi_camera_listener.service
sudo systemctl start pi_camera_listener.service
sudo systemctl status pi_camera_listener.service
```

If your paths or username differ from `/home/pi/pi_camera_service`, edit the service file first.
