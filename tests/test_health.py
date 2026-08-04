from fastapi.testclient import TestClient
from datetime import UTC, datetime
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
