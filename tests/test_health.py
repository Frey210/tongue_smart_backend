from fastapi.testclient import TestClient
from datetime import UTC, datetime
import hashlib
import json
from uuid import uuid4

from tongue_smart.database import SessionLocal
from tongue_smart.main import app
from tongue_smart.models import UserRecord
from tongue_smart.security import hash_password


def create_test_user(role: str, email: str) -> None:
    with SessionLocal() as db:
        db.add(UserRecord(
            id=str(uuid4()), email=email, full_name="Test User", role=role,
            password_hash=hash_password("valid-password-123"), is_active=True,
            created_at=datetime.now(UTC),
        ))
        db.commit()


def test_health() -> None:
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/ready").json() == {"status": "ready"}


def test_idempotent_firmware_contract() -> None:
    event = {
        "schema_version": 1,
        "message_id": "boot-test-123",
        "device_id": "tongue-smart-v3",
        "event": "online",
        "firmware_version": "0.2.0",
        "uptime_ms": 123,
    }
    with TestClient(app) as client:
        first = client.post("/api/v1/device-events", json=event)
        duplicate = client.post("/api/v1/device-events", json=event)

    assert first.status_code == 202
    assert first.json()["duplicate"] is False
    assert duplicate.json()["duplicate"] is True


def test_login_refresh_and_role_protection() -> None:
    admin_email = f"admin-{uuid4()}@example.test"
    operator_email = f"operator-{uuid4()}@example.test"
    with TestClient(app) as client:
        create_test_user("admin", admin_email)
        create_test_user("operator", operator_email)
        login_response = client.post("/api/v1/auth/login", json={
            "email": admin_email, "password": "valid-password-123",
        })
        assert login_response.status_code == 200
        tokens = login_response.json()
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        assert client.get("/api/v1/auth/me", headers=headers).json()["role"] == "admin"
        assert client.get("/api/v1/users", headers=headers).status_code == 200
        assert client.get("/api/v1/devices/current").status_code == 401

        operator_login = client.post("/api/v1/auth/login", json={
            "email": operator_email, "password": "valid-password-123",
        }).json()
        operator_headers = {"Authorization": f"Bearer {operator_login['access_token']}"}
        assert client.get("/api/v1/users", headers=operator_headers).status_code == 403

        refreshed = client.post("/api/v1/auth/refresh", json={
            "refresh_token": tokens["refresh_token"],
        })
        assert refreshed.status_code == 200
        assert client.post("/api/v1/auth/refresh", json={
            "refresh_token": tokens["refresh_token"],
        }).status_code == 401


def test_registration_requires_admin_approval_and_password_can_change() -> None:
    admin_email = f"admin-{uuid4()}@example.test"
    applicant_email = f"applicant-{uuid4()}@example.test"
    with TestClient(app) as client:
        create_test_user("admin", admin_email)
        registration = client.post("/api/v1/auth/register", json={
            "email": applicant_email, "full_name": "New Operator",
            "institution": "FKG Research Lab", "password": "applicant-password-123",
        })
        assert registration.status_code == 202
        assert client.post("/api/v1/auth/login", json={
            "email": applicant_email, "password": "applicant-password-123",
        }).status_code == 401

        admin_login = client.post("/api/v1/auth/login", json={
            "email": admin_email, "password": "valid-password-123",
        }).json()
        headers = {"Authorization": f"Bearer {admin_login['access_token']}"}
        requests = client.get("/api/v1/registration-requests", headers=headers).json()
        request = next(item for item in requests if item["email"] == applicant_email)
        assert client.post(
            f"/api/v1/registration-requests/{request['id']}/approve", headers=headers
        ).status_code == 200
        assert client.post("/api/v1/auth/login", json={
            "email": applicant_email, "password": "applicant-password-123",
        }).status_code == 200

        changed = client.post("/api/v1/auth/change-password", headers=headers, json={
            "current_password": "valid-password-123", "new_password": "new-password-456",
        })
        assert changed.status_code == 204
        assert client.post("/api/v1/auth/login", json={
            "email": admin_email, "password": "new-password-456",
        }).status_code == 200


def test_subject_consent_workflow_and_role_permissions() -> None:
    operator_email = f"operator-{uuid4()}@example.test"
    researcher_email = f"researcher-{uuid4()}@example.test"
    with TestClient(app) as client:
        create_test_user("operator", operator_email)
        create_test_user("researcher", researcher_email)
        operator = client.post("/api/v1/auth/login", json={"email": operator_email, "password": "valid-password-123"}).json()
        researcher = client.post("/api/v1/auth/login", json={"email": researcher_email, "password": "valid-password-123"}).json()
        operator_headers = {"Authorization": f"Bearer {operator['access_token']}"}
        researcher_headers = {"Authorization": f"Bearer {researcher['access_token']}"}
        code = f"TS-{uuid4().hex[:8]}"
        created = client.post("/api/v1/subjects", headers=operator_headers, json={
            "subject_code": code, "initials": "AN", "research_group": "Control",
            "year_of_birth": 2015, "consent_status": "pending", "notes": "No direct identity",
        })
        assert created.status_code == 201
        subject_id = created.json()["id"]
        assert client.get("/api/v1/subjects?consent_status=pending", headers=researcher_headers).json()[0]["subject_code"] == code.upper()
        assert client.post("/api/v1/subjects", headers=researcher_headers, json={
            "subject_code": "DENIED", "initials": "XX", "research_group": "Control", "consent_status": "pending",
        }).status_code == 403
        updated = client.patch(f"/api/v1/subjects/{subject_id}", headers=operator_headers, json={"consent_status": "granted"})
        assert updated.status_code == 200
        assert updated.json()["consent_status"] == "granted"


def test_prepare_examination_session_validates_consent_and_emg_site() -> None:
    operator_email = f"operator-{uuid4()}@example.test"
    with TestClient(app) as client:
        create_test_user("operator", operator_email)
        login = client.post("/api/v1/auth/login", json={"email": operator_email, "password": "valid-password-123"}).json()
        headers = {"Authorization": f"Bearer {login['access_token']}"}
        subject = client.post("/api/v1/subjects", headers=headers, json={
            "subject_code": f"TS-{uuid4().hex[:8]}", "initials": "EM", "research_group": "Pilot", "consent_status": "granted",
        }).json()
        payload = {"subject_id": subject["id"], "device_id": "tongue-smart-v3", "modules": ["emg", "tongue_pressure"], "protocol_stages": ["rest", "clench"]}
        assert client.post("/api/v1/sessions", headers=headers, json=payload).status_code == 422
        payload["electrode_site"] = "masseter_left"
        created = client.post("/api/v1/sessions", headers=headers, json=payload)
        assert created.status_code == 201
        assert created.json()["status"] == "prepared"
        assert created.json()["electrode_site"] == "masseter_left"
        assert any(item["id"] == created.json()["id"] for item in client.get("/api/v1/sessions", headers=headers).json())


def test_session_lifecycle_and_idempotent_batch_receipt(monkeypatch) -> None:
    monkeypatch.setenv("TONGUE_SMART_DEVICE_API_KEY", "test-device-key")
    operator_email = f"operator-{uuid4()}@example.test"
    with TestClient(app) as client:
        create_test_user("operator", operator_email)
        login = client.post("/api/v1/auth/login", json={"email": operator_email, "password": "valid-password-123"}).json()
        headers = {"Authorization": f"Bearer {login['access_token']}"}
        subject = client.post("/api/v1/subjects", headers=headers, json={
            "subject_code": f"TS-{uuid4().hex[:8]}", "initials": "BT", "research_group": "Pilot", "consent_status": "granted",
        }).json()
        session = client.post("/api/v1/sessions", headers=headers, json={
            "subject_id": subject["id"], "device_id": "tongue-smart-v3", "modules": ["tongue_pressure"], "protocol_stages": ["tongue_press"],
        }).json()
        assert client.post(f"/api/v1/sessions/{session['id']}/start", headers=headers).json()["status"] == "active"
        samples = [{"timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"), "protocol_stage": "tongue_press", "sensor_channel": "fsr_1", "raw_value": 1042.0, "calibrated_value": 12.4, "measurement_unit": "kPa", "signal_quality": "good"}]
        checksum = hashlib.sha256(json.dumps(samples, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        batch = {"message_id": f"batch-{uuid4()}", "device_id": "tongue-smart-v3", "sequence": 0, "checksum": checksum, "samples": samples}
        device_headers = {"X-Device-Key": "test-device-key"}
        active = client.get("/api/v1/device/sessions/active?device_id=tongue-smart-v3", headers=device_headers)
        assert active.status_code == 200 and active.json()[0]["next_sequence"] == 0 and active.json()[0]["control"] is None
        assert client.post(f"/api/v1/sessions/{session['id']}/control", headers=headers, json={
            "measurement": "tongue_pressure", "phase": "recording", "protocol_stage": "tongue_press",
        }).status_code == 422
        control = client.post(f"/api/v1/sessions/{session['id']}/control", headers=headers, json={
            "measurement": "tongue_pressure", "phase": "recording", "protocol_stage": "tongue_press", "fsr_point": "median_anterior",
        })
        assert control.status_code == 201 and control.json()["fsr_point"] == "median_anterior"
        assert client.get(f"/api/v1/sessions/{session['id']}/control", headers=headers).json()["phase"] == "recording"
        first = client.post(f"/api/v1/sessions/{session['id']}/batches", headers=device_headers, json=batch)
        duplicate = client.post(f"/api/v1/sessions/{session['id']}/batches", headers=device_headers, json=batch)
        assert first.status_code == 202 and first.json()["duplicate"] is False
        assert duplicate.status_code == 202 and duplicate.json()["duplicate"] is True
        assert client.get("/api/v1/device/sessions/active?device_id=tongue-smart-v3", headers=device_headers).json()[0]["next_sequence"] == 1
        marker = client.post(f"/api/v1/sessions/{session['id']}/markers", headers=headers, json={
            "protocol_stage": "tongue_press", "label": "Tekanan puncak", "occurred_at": samples[0]["timestamp"],
        })
        note = client.post(f"/api/v1/sessions/{session['id']}/notes", headers=headers, json={"note": "Sinyal stabil."})
        assert marker.status_code == 201 and note.status_code == 201
        results = client.get(f"/api/v1/sessions/{session['id']}/results?sensor_channel=fsr_1", headers=headers).json()
        assert results["sample_count"] == 1
        assert results["summary"]["maximum"] == 12.4
        assert results["markers"][0]["label"] == "Tekanan puncak"
        assert results["notes"][0]["note"] == "Sinyal stabil."
        export = client.post("/api/v1/exports", headers=headers, json={
            "session_ids": [session["id"]], "data_mode": "both", "include_metadata": True, "include_markers": True,
        })
        assert export.status_code == 201 and export.json()["row_count"] == 1
        download = client.get(f"/api/v1/exports/{export.json()['id']}/download", headers=headers)
        assert download.status_code == 200
        assert "text/csv" in download.headers["content-type"]
        assert download.headers["x-content-sha256"] == export.json()["checksum"]
        assert "Tekanan puncak" in download.text and "fsr_1" in download.text
        assert client.post(f"/api/v1/sessions/{session['id']}/finalize", headers=headers).json()["status"] == "completed"


def test_pair_claim_and_device_credentials_are_isolated() -> None:
    operator_email = f"operator-{uuid4()}@example.test"
    first_id = f"TS-SIM-{uuid4().hex[:6]}"
    second_id = f"TS-SIM-{uuid4().hex[:6]}"
    with TestClient(app) as client:
        create_test_user("operator", operator_email)
        login = client.post("/api/v1/auth/login", json={
            "email": operator_email, "password": "valid-password-123",
        }).json()
        user_headers = {"Authorization": f"Bearer {login['access_token']}"}

        def pair_and_claim(device_id: str, secret: str) -> dict:
            pairing = client.post("/api/v1/device/pairings", json={
                "device_id": device_id,
                "hardware_uid": f"SIM:{device_id}",
                "device_secret": secret,
                "firmware_version": "sim-1.0",
                "capabilities": {"emg_channels": 1, "tongue_pressure_channels": 1, "lip_force": True},
            })
            assert pairing.status_code == 201
            pairing_data = pairing.json()
            assert client.get(f"/api/v1/device/pairings/{pairing_data['pairing_token']}").json()["status"] == "pending"
            claimed = client.post("/api/v1/devices/claim", headers=user_headers, json={
                "pairing_code": pairing_data["pairing_code"], "display_name": f"Simulator {device_id}",
            })
            assert claimed.status_code == 201
            assert client.get(f"/api/v1/device/pairings/{pairing_data['pairing_token']}").json()["status"] == "claimed"
            return claimed.json()

        first_secret = "first-device-secret-1234567890abcdef"
        second_secret = "second-device-secret-1234567890abcdef"
        first = pair_and_claim(first_id, first_secret)
        pair_and_claim(second_id, second_secret)
        assert first["owner_id"] == login["user"]["id"]
        assert first["credential_hint"] == first_secret[-6:]
        listed = client.get("/api/v1/devices", headers=user_headers).json()
        assert {first_id, second_id}.issubset({item["device_id"] for item in listed})

        subject = client.post("/api/v1/subjects", headers=user_headers, json={
            "subject_code": f"TS-{uuid4().hex[:8]}", "initials": "MD",
            "research_group": "Multi device", "consent_status": "granted",
        }).json()
        session = client.post("/api/v1/sessions", headers=user_headers, json={
            "subject_id": subject["id"], "device_id": first_id,
            "modules": ["tongue_pressure"], "protocol_stages": ["tongue_press"],
        }).json()
        client.post(f"/api/v1/sessions/{session['id']}/start", headers=user_headers)
        first_headers = {"X-Device-ID": first_id, "X-Device-Key": first_secret}
        second_headers = {"X-Device-ID": second_id, "X-Device-Key": second_secret}
        assert client.get(f"/api/v1/device/sessions/active?device_id={first_id}", headers=first_headers).status_code == 200
        assert client.get(f"/api/v1/device/sessions/active?device_id={first_id}", headers=second_headers).status_code == 403
