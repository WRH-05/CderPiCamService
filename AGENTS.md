# AGENTS.md - pi_camera_service

## Scope
This folder is a standalone Raspberry Pi runtime for:
- USB serial trigger intake from ESP32
- USB webcam image capture
- ONNX inference
- Optional MQTT publish
- CSV result logging

No dependency on the ESP32 firmware folder is required.

## Runtime Entry Points
- Main listener: `pi_capture_listener.py`
- Inference helpers: `intereference_onnx.py`
- Service unit: `pi_camera_listener.service`

## Required Files For Deployment
- `pi_capture_listener.py`
- `intereference_onnx.py`
- `requirements.txt`
- `best_model.onnx`

Optional model companion file:
- `best_model.onnx.data` (only if your ONNX export uses external tensor data)

## Expected Host Path
Default service config assumes:
- Folder: `/home/chrome/pi_camera_service`
- Python venv: `/home/chrome/pi_camera_service/.venv`
- User: `chrome`

If different, update `pi_camera_listener.service`.

## Standard Deployment Flow
1. Copy this folder to Pi as `/home/chrome/pi_camera_service`.
2. Place model file(s) in that folder.
3. Create venv and install dependencies.
4. Run manually once to validate serial/camera/model.
5. Install and enable systemd service.

## Validation Commands
Manual run:
`python pi_capture_listener.py --serial_port /dev/ttyUSB0 --camera_backend webcam --webcam_index 0 --capture_delay_seconds 15`

Check service status:
`sudo systemctl status pi_camera_listener.service`

Tail logs:
`journalctl -u pi_camera_listener.service -f`

## Notes
- Default trigger token is `[CAM] HIGH`.
- Captures are stored under `captures/`.
- Inference rows are appended to `inference_log.csv`.
