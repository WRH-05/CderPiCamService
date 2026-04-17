We are finalizing our Edge Hardware stress tests for the manuscript. To ensure strict methodological consistency and avoid cross-dataset contamination, we have removed all external images. The `captures` directory now contains exactly the **397 held-out test images from the ZAE Bayern dataset**. 

I have also provided the final, mathematically optimal model: `best_sahl_1.5x_final.onnx`.

**Please update and run the `benchmark_edge.py` script to perform a sustained deployment stress-test:**

**1. Sustained Latency & Jitter Benchmark:**
- Loop through these 397 images **4 times consecutively** to simulate a continuous run of 1,588 inferences.
- Record the inference time for every single iteration (excluding image load time from the disk if possible, measuring pure ONNX execution).
- Output the **Mean Latency $\pm$ Standard Deviation** (e.g., $185.4 \pm 2.1$ ms) so we can report processor jitter in the paper. Also include Median, P95, Min, and Max latency, as well as overall Throughput (FPS).

**2. Memory Leak (RSS) Verification:**
- Continuously monitor the Resident Set Size (RSS) memory during the 1,588 iterations.
- Report the Starting RSS, Peak RSS, and Ending RSS to prove the memory footprint remains constrained (around ~125 MB) with zero memory leaks.

Please give me the most optimal command to run inside the pi to execute the benchmark and provide the final table, and i'm assuming running mqtt messaging whilst collecting this data is probably ot optimal for performance metrics, so maybe also guide me on how to minimize processses running on the pi ubuntu 22.04 headless.