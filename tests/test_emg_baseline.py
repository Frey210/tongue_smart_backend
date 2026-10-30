from datetime import UTC, datetime
from uuid import uuid4
import hashlib
import json

from fastapi.testclient import TestClient
from tongue_smart.database import SessionLocal
from tongue_smart.main import app
from tongue_smart.models import UserRecord
from tongue_smart.security import hash_password


METRICS = {"samples": 1000, "duration_ms": 10000, "mean_mv": 1480.0,
           "noise_mv": 5.0, "span_mv": 20.0, "drift_mv": 2.0, "clipped_frames": 0}


def prepare(client):
    identity = uuid4().hex
    email = f"baseline-{identity}@example.test"
    with SessionLocal() as db:
        db.add(UserRecord(id=str(uuid4()), email=email, full_name="Baseline test", role="operator",
                          password_hash=hash_password("test-only-password"), is_active=True, created_at=datetime.now(UTC)))
        db.commit()
    login = client.post("/api/v1/auth/login", json={"email": email, "password": "test-only-password"}).json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    device_id, secret = "BASE-" + identity[:12], identity * 2
    pairing = client.post("/api/v1/device/pairings", json={"device_id": device_id, "hardware_uid": "TEST:" + identity,
        "device_secret": secret, "firmware_version": "0.7.0", "capabilities": {"emg_channels": 1}}).json()
    assert client.post("/api/v1/devices/claim", headers=headers, json={"pairing_code": pairing["pairing_code"], "display_name": "Baseline simulator"}).status_code == 201
    subject = client.post("/api/v1/subjects", headers=headers, json={"subject_code": "S-" + identity[:8], "initials": "BT", "research_group": "Test", "consent_status": "granted"}).json()
    session = client.post("/api/v1/sessions", headers=headers, json={"subject_id": subject["id"], "device_id": device_id,
        "session_name": "Baseline " + identity[:8], "modules": ["emg"], "protocol_stages": [], "electrode_site": "masseter_right"}).json()
    assert client.post(f"/api/v1/sessions/{session['id']}/start", headers=headers).status_code == 200
    return headers, {"X-Device-ID": device_id, "X-Device-Key": secret}, session["id"], device_id


def test_emg_baseline_confirmation_requires_device_ack_and_handles_stop():
    with TestClient(app) as client:
        headers, device, sid, device_id = prepare(client)
        control = {"measurement": "emg", "protocol_stage": "emg_masseter_right", "phase": "recording"}
        assert client.post(f"/api/v1/sessions/{sid}/control", headers=headers, json=control).status_code == 409
        request = client.post(f"/api/v1/sessions/{sid}/control", headers=headers, json={**control, "phase": "baseline"}).json()
        cid = request["id"]
        endpoint = f"/api/v1/device/sessions/{sid}/emg-baseline/{cid}"
        confirm = f"/api/v1/sessions/{sid}/emg-baseline/confirm?control_id={cid}"
        assert client.post(confirm, headers=headers).status_code == 409
        assert client.post(endpoint, headers=device, json={"state": "ready"}).status_code == 409
        assert client.post(endpoint, headers=device, json={"state": "capturing"}).status_code == 200
        assert client.post(endpoint, headers=device, json={"state": "review", "metrics": {**METRICS, "clipped_frames": 1}}).status_code == 422
        assert client.post(endpoint, headers=device, json={"state": "review", "metrics": METRICS}).json()["state"] == "review"
        assert client.post(confirm, headers=headers).json()["state"] == "accepted"
        assert client.post(f"/api/v1/sessions/{sid}/control", headers=headers, json=control).status_code == 409
        assert client.post(endpoint, headers=device, json={"state": "ready"}).json()["state"] == "ready"
        assert client.post(endpoint, headers=device, json={"state": "ready"}).status_code == 200
        assert client.post(f"/api/v1/sessions/{sid}/control", headers=headers, json=control).status_code == 201
        active = client.get(f"/api/v1/device/sessions/active?device_id={device_id}", headers=device).json()[0]
        assert active["emg_baseline"]["control_id"] == cid
        assert active["emg_baseline"]["gain_verified"] is False
        samples = [{"timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"), "protocol_stage": "emg_masseter_right",
                    "sensor_channel": "emg_1", "measurement_unit": "uV", "raw_value": 1800.0,
                    "calibrated_value": 15.0, "signal_quality": "good"}]
        batch = {"device_id": device_id, "message_id": "baseline-batch-1", "sequence": 0, "samples": samples,
                 "checksum": hashlib.sha256(json.dumps(samples, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
        assert client.post(f"/api/v1/sessions/{sid}/batches", headers=device, json=batch).status_code == 202
        assert client.post(endpoint, headers=device, json={"state": "failed", "reason": "Device restarted"}).status_code == 200
        result = client.get(f"/api/v1/sessions/{sid}/results", headers=headers).json()
        assert result["control"]["phase"] == "paused" and result["emg_baseline"]["state"] == "failed"
        assert result["sample_count"] == 1
        assert client.post(f"/api/v1/sessions/{sid}/batches", headers=device, json={**batch, "message_id": "baseline-batch-2", "sequence": 1}).status_code == 409


def test_baseline_supersession_auth_and_invalid_metrics():
    with TestClient(app) as client:
        headers, device, sid, _ = prepare(client)
        payload = {"measurement": "emg", "protocol_stage": "emg_masseter_right", "phase": "baseline"}
        cid = client.post(f"/api/v1/sessions/{sid}/control", headers=headers, json=payload).json()["id"]
        endpoint = f"/api/v1/device/sessions/{sid}/emg-baseline/{cid}"
        assert client.post(endpoint, headers=headers, json={"state": "capturing"}).status_code in (401, 503)
        _, wrong_device, _, _ = prepare(client)
        assert client.post(endpoint, headers=wrong_device, json={"state": "capturing"}).status_code == 403
        for metrics in ({**METRICS, "noise_mv": 100}, {**METRICS, "samples": 50}, {**METRICS, "drift_mv": 31}):
            assert client.post(endpoint, headers=device, json={"state": "review", "metrics": metrics}).status_code == 422
        assert client.post(endpoint, headers=device, json={"state": "review", "metrics": METRICS}).status_code == 200
        client.post(f"/api/v1/sessions/{sid}/control", headers=headers, json={**payload, "phase": "paused"})
        assert client.post(f"/api/v1/sessions/{sid}/emg-baseline/confirm?control_id={cid}", headers=headers).status_code == 409
        assert client.post(endpoint, headers=device, json={"state": "ready"}).status_code == 409
        new = client.post(f"/api/v1/sessions/{sid}/control", headers=headers, json=payload).json()
        assert new["id"] != cid
        assert client.post(endpoint, headers=device, json={"state": "review", "metrics": METRICS}).status_code == 409
