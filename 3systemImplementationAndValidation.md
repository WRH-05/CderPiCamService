Semantic workspace search is not currently available

### 3. System Implementation and Validation

#### 3.1 System Architecture
The proposed proof-of-concept system implements an end-to-end pipeline for automated monitoring of photovoltaic (PV) panel degradation at pad-level granularity. The architecture consists of four main components:

1. **Edge acquisition and inference layer (Raspberry Pi)**  
A Raspberry Pi listens for serial trigger messages from a microcontroller (ESP32), captures electroluminescence (EL) images from a mounted camera, and performs ONNX-based inference to estimate degradation severity.

2. **Messaging layer (MQTT)**  
Inference outputs are published as structured JSON messages over MQTT on the topic `pv/inspection/severity`.

3. **Orchestration and visualization layer (Node-RED)**  
Node-RED subscribes to the MQTT topic, updates a live dashboard (severity gauge, trend chart, metadata fields), and forwards validated observations to persistent storage.

4. **Persistence and alert analytics layer (Supabase/PostgreSQL)**  
Supabase stores time-series inspection records and computes alert states using SQL functions and trigger-based logic. Alert status is then reflected on the dashboard.

This architecture separates edge computation from storage and analytics, enabling lightweight on-device operation while preserving longitudinal degradation history.

#### 3.2 Edge Runtime and Inference Flow
Upon receiving a trigger event, the Pi executes the following sequence:

1. Acquire EL frame from camera.
2. Apply preprocessing and ONNX inference to produce a scalar severity score in the normalized range $[0,1]$.
3. Build an MQTT payload containing both inference result and contextual metadata:
   - `panel_id`
   - `pad_id`
   - `robot_id`
   - `model_version`
   - `severity_score`
   - `status`
   - `image_path`
4. Publish payload to MQTT broker.
5. Append local CSV row as a lightweight on-device fallback log.

This edge process supports low-latency inspection while remaining compatible with periodic robotic scanning workflows.

#### 3.3 Data Model and Alert Logic
The Supabase schema includes:

1. **`inspections`**  
Stores timestamped inference events per panel/pad.

2. **`thresholds`**  
Stores warning, critical, recovery, and slope thresholds (global or pad-specific).

3. **`alerts`**  
Stores alert lifecycle state (`OPEN`, `ACKNOWLEDGED`, `RESOLVED`) and supporting metrics.

4. **`maintenance_events`**  
Stores replacement/maintenance actions for traceability.

5. **Derived views**  
Examples include latest pad status, weekly trends, and currently open alerts.

Alert evaluation is implemented through SQL functions and an insert trigger on `inspections`. For each new inspection, the system evaluates current severity and recent trend slope over a rolling window and updates alert state accordingly. This enables both threshold-based and progression-based flagging.

#### 3.4 Dashboard Implementation
A full Node-RED flow was implemented and validated with:

1. MQTT ingestion on `pv/inspection/severity`.
2. Real-time widgets:
   - severity gauge,
   - trend chart,
   - panel/pad and model metadata fields.
3. Supabase insertion pipeline for each valid message.
4. RPC-based alert evaluation and dashboard alert display.
5. Open-alert count polling from database views.

The dashboard therefore provides both instantaneous condition awareness and database-backed alert context.

#### 3.5 Validation Procedure
Functional validation was executed as follows:

1. **Inference validation using EL sample image**  
A test image (cell0001.png) was processed by the ONNX model with mock IDs. The model produced a high severity score and `CRITICAL` status.

2. **Messaging validation**  
The generated payload was successfully published over MQTT and consumed by the Node-RED flow.

3. **Persistence validation**  
A corresponding inspection record was confirmed in Supabase with matching metadata and severity value.

4. **Alert opening validation**  
For a critical sample, the alert function returned `OPEN` with reason `CRITICAL_THRESHOLD`, and the open-alert view reflected the active alert.

5. **Alert resolution validation**  
A subsequent low-severity recovery sample for the same panel/pad returned `RESOLVED` with reason `RECOVERY_THRESHOLD`, and open-alert count returned to zero.

These tests confirm the correctness of the full data path:
\[
\text{EL image} \rightarrow \text{Edge inference} \rightarrow \text{MQTT} \rightarrow \text{Node-RED} \rightarrow \text{Supabase} \rightarrow \text{Alert state}
\]

#### 3.6 Outcome
The proof-of-concept demonstrates that the proposed architecture can:

1. Acquire and score EL images automatically,
2. Track pad-level degradation observations over time,
3. Persist inspection history for longitudinal analysis,
4. Trigger actionable alerts when degradation exceeds defined conditions.

As a research prototype, the system satisfies the core objective of demonstrating integrated, real-time condition monitoring with historical degradation tracking and automated alert generation.