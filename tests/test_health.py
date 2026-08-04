from fastapi.testclient import TestClient

from tongue_smart.main import app


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
        device = client.get("/api/v1/devices/current").json()

    assert first.status_code == 202
    assert first.json()["duplicate"] is False
    assert duplicate.json()["duplicate"] is True
    assert device["connection"] == "online"
    assert device["mqtt"] is False
