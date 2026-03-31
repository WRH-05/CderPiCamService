# Node-RED Supabase Ingest Flow

This folder contains an importable Node-RED flow that:

1. Subscribes to MQTT topic pv/inspection/severity
2. Validates and maps payload to Supabase inspections table shape
3. Inserts row into inspections
4. Calls Supabase RPC evaluate_pad_alert
5. Routes OPEN vs non-OPEN alert states for dashboard and notifications

## Files

- supabase_ingest_flow.json: Import this file into Node-RED

## Required Node-RED Environment Variables

Set these in your Node-RED runtime before deploying the flow:

- SUPABASE_URL
- SUPABASE_SERVICE_ROLE_KEY

## Import Steps

1. Open Node-RED editor
2. Menu -> Import
3. Paste contents of supabase_ingest_flow.json or select file
4. Update MQTT broker node host and auth
5. Deploy

## Test

Publish one test message to MQTT topic pv/inspection/severity:

{
  "panel_id": "panel_A",
  "pad_id": "pad_001",
  "robot_id": "robot_01",
  "model_version": "onnx_v1",
  "severity_score": 0.73,
  "status": "CRITICAL",
  "image_path": "captures/el_20260331_123456.jpg"
}

Expected result:

1. New row inserted into inspections
2. Alert evaluated and upserted by DB trigger logic
3. OPEN alert visible in Node-RED debug sidebar
