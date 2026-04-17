# Edge Sustained Benchmark Summary

## Run configuration

| Metric | Value |
| --- | ---: |
| Model | /home/chrome/CderPiCamService/model/best_sahl_1.5x_final.onnx |
| Captures directory | /home/chrome/CderPiCamService/captures |
| Image count | 397 |
| Loops | 4 |
| Warmup runs | 10 |
| Total measured inferences | 1588 |

## Primary latency metric (ONNX-only)

| Metric | Value |
| --- | ---: |
| Mean ± StdDev (ms) | 272.33 +/- 112.28 |
| Median (ms) | 228.73 |
| P95 (ms) | 491.75 |
| Min (ms) | 163.85 |
| Max (ms) | 585.35 |
| Throughput (FPS) | 3.67 |

## Secondary latency metric (End-to-end)

| Metric | Value |
| --- | ---: |
| Mean ± StdDev (ms) | 290.18 +/- 120.32 |
| Median (ms) | 243.84 |
| P95 (ms) | 518.24 |
| Min (ms) | 175.71 |
| Max (ms) | 1613.34 |
| Throughput (FPS) | 3.45 |

## Memory stability (RSS)

| Metric | Value |
| --- | ---: |
| RSS start (MB) | 60.98 |
| RSS after session load (MB) | 68.78 |
| RSS peak (MB) | 128.36 |
| RSS end (MB) | 127.18 |
| Session load delta (MB) | 7.80 |
| Start to end delta (MB) | 66.21 |
