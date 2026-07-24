We are preparing our Edge Deployment codebase for a public, academic repository submission Stanford/IEEE standards. 

Inspect the current workspace, then refactor, clean, and organize our ONNX inference and edge benchmarking scripts into the exact structure defined below. Do not just print the code—create and write the refactored files directly to the filesystem.

---

### Directory & File Requirements

Create the directory `src/inference/` and implement the following files:

1. `inference_mqtt.py` (Main Edge Worker):
   - Load the ONNX model using `onnxruntime`.
   - Preprocess input images (Median blur, resize, normalize).
   - Implement the $\ge 0.65$ CRITICAL safety threshold evaluation logic.
   - Construct a structured JSON payload and publish it using `paho-mqtt`.

2. `benchmark_edge.py` (Hardware Stress-Test):
   - Benchmark script that processes a target image directory for 1,588 continuous inferences.
   - Calculate and display **Mean Latency $\pm$ Std Dev** (excluding disk I/O latency).
   - Track and report **Peak RSS memory usage** using `psutil`.

3. `requirements_edge.txt` (Lightweight Dependencies):
   - Include ONLY minimal runtime packages needed for the Raspberry Pi (e.g., `onnxruntime`, `opencv-python`, `paho-mqtt`, `psutil`, `numpy`).
   - **STRICT REQUIREMENT**: Do NOT include heavy packages like `torch`, `torchvision`, `matplotlib`, or `scipy`.

4. `src/inference/README.md`:
   - Include a concise setup guide detailing how to install `requirements_edge.txt` and execute both scripts via CLI.

---

### Mandatory Engineering Constraints

1. **NO HARDCODED PATHS**: Strip out all local user paths (e.g., `/home/chrome/CderPiCamService/...`). Use `argparse` CLI arguments with sensible relative defaults (e.g., `--model_path ../../models/policy_1.5x_low_error/sahl_1.5x.onnx`).
2. **NO HARDCODED CREDENTIALS OR IPs**: Load MQTT Broker parameters (`MQTT_BROKER`, `MQTT_PORT`, `MQTT_TOPIC`) via environment variables using `os.getenv()`, falling back to `argparse` CLI flags. Never leave hardcoded local IP addresses in the code.
3. **PEP 8 Compliance & Typing**: Add type hints, clean logging (`logging` module instead of arbitrary prints), and comprehensive docstrings.

---

### Execution & Verification Workflow

1. **Scan**: Identify existing ONNX inference scripts, MQTT configuration, and hardware benchmarking routines in the project.
2. **Refactor**: Create `src/inference/` and write `inference_mqtt.py`, `benchmark_edge.py`, `requirements_edge.txt`, and `README.md`.
3. **Verify**: 
   - Run `python -m py_compile` on all `.py` files.
   - Check CLI argument parsing with `python script.py --help`.
   - Verify `requirements_edge.txt` contains zero references to `torch` or `matplotlib`.
   - make sure the code and math matches the claims in `main.pdf` meaning the latest tests and code is used.