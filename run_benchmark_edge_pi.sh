#!/usr/bin/env bash
set -euo pipefail

# One-command sustained edge benchmark protocol for Raspberry Pi.
#
# Defaults are chosen to match the manuscript run (397 images x 4 loops).
# Override any variable inline, for example:
#   LOOPS=5 WARMUP_RUNS=20 ./run_benchmark_edge_pi.sh

REPO_DIR="${REPO_DIR:-$(pwd)}"
VENV_ACTIVATE="${VENV_ACTIVATE:-$REPO_DIR/.venv/bin/activate}"
MODEL_PATH="${MODEL_PATH:-$REPO_DIR/model/best_sahl_1.5x_final.onnx}"
CAPTURES_DIR="${CAPTURES_DIR:-$REPO_DIR/captures}"

LOOPS="${LOOPS:-4}"
WARMUP_RUNS="${WARMUP_RUNS:-10}"

RUNS_CSV="${RUNS_CSV:-$REPO_DIR/benchmark_edge_runs.csv}"
SUMMARY_CSV="${SUMMARY_CSV:-$REPO_DIR/benchmark_edge_summary.csv}"
SUMMARY_MD="${SUMMARY_MD:-$REPO_DIR/benchmark_edge_summary.md}"

# If not provided, infer expected count from captures/cell*.png.
EXPECTED_IMAGE_COUNT="${EXPECTED_IMAGE_COUNT:-}"

# Services to quiet during benchmark.
STOP_SERVICES=(
  "pi_camera_listener.service"
  "mosquitto"
  "apt-daily.service"
  "apt-daily-upgrade.service"
)

declare -A ORIGINAL_GOVERNOR

restore_system() {
  echo "[cleanup] Restoring CPU governors and background services"

  for governor_file in "${!ORIGINAL_GOVERNOR[@]}"; do
    echo "${ORIGINAL_GOVERNOR[$governor_file]}" | sudo tee "$governor_file" >/dev/null || true
  done

  sudo swapon -a >/dev/null 2>&1 || true

  for service in "${STOP_SERVICES[@]}"; do
    sudo systemctl start "$service" >/dev/null 2>&1 || true
  done
}

trap restore_system EXIT

if [[ ! -f "$VENV_ACTIVATE" ]]; then
  echo "[error] Virtual environment activation script not found: $VENV_ACTIVATE"
  echo "[hint] From repo root: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

if [[ ! -f "$MODEL_PATH" ]]; then
  echo "[error] Model file not found: $MODEL_PATH"
  exit 1
fi

if [[ ! -d "$CAPTURES_DIR" ]]; then
  echo "[error] Captures directory not found: $CAPTURES_DIR"
  exit 1
fi

if [[ -z "$EXPECTED_IMAGE_COUNT" ]]; then
  EXPECTED_IMAGE_COUNT="$(find "$CAPTURES_DIR" -maxdepth 1 -type f -name 'cell*.png' | wc -l | tr -d ' ')"
fi

echo "[info] Repo: $REPO_DIR"
echo "[info] Model: $MODEL_PATH"
echo "[info] Captures: $CAPTURES_DIR"
echo "[info] Expected images: $EXPECTED_IMAGE_COUNT"
echo "[info] Loops: $LOOPS"
echo "[info] Warmup runs: $WARMUP_RUNS"

echo "[prep] Stopping non-essential services"
for service in "${STOP_SERVICES[@]}"; do
  sudo systemctl stop "$service" >/dev/null 2>&1 || true
done

echo "[prep] Disabling swap for cleaner RSS/latency measurements"
sudo swapoff -a >/dev/null 2>&1 || true

echo "[prep] Setting CPU governors to performance"
for governor_file in /sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_governor; do
  if [[ -f "$governor_file" ]]; then
    ORIGINAL_GOVERNOR["$governor_file"]="$(cat "$governor_file")"
    echo "performance" | sudo tee "$governor_file" >/dev/null || true
  fi
done

if [[ -f /sys/class/thermal/thermal_zone0/temp ]]; then
  echo "[prep] SoC temp (milli-C): $(cat /sys/class/thermal/thermal_zone0/temp)"
fi

cd "$REPO_DIR"
source "$VENV_ACTIVATE"

echo "[run] Starting sustained benchmark"
python benchmark_edge.py \
  --onnx_model "$MODEL_PATH" \
  --captures_dir "$CAPTURES_DIR" \
  --loops "$LOOPS" \
  --warmup_runs "$WARMUP_RUNS" \
  --expected_image_count "$EXPECTED_IMAGE_COUNT" \
  --output_csv "$RUNS_CSV" \
  --summary_csv "$SUMMARY_CSV" \
  --summary_md "$SUMMARY_MD"

echo "[done] Benchmark finished"
echo "[done] Runs CSV: $RUNS_CSV"
echo "[done] Summary CSV: $SUMMARY_CSV"
echo "[done] Summary MD: $SUMMARY_MD"
