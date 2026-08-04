from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Literal

from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

app = FastAPI(
    title="Tongue Smart API",
    version="0.1.0",
    description="Synchronization and research data API; measurement remains device-local.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


class DeviceEvent(BaseModel):
    schema_version: int = Field(default=1, ge=1)
    message_id: str = Field(min_length=1, max_length=96)
    device_id: str = Field(min_length=1, max_length=64)
    event: Literal["boot", "online", "offline", "measurement_saved"]
    firmware_version: str = Field(min_length=1, max_length=32)
    occurred_at: datetime | None = None
    uptime_ms: int | None = Field(default=None, ge=0)


CAPABILITIES = {
    "device_id": "tongue-smart-v3",
    "firmware_version": "0.2.0",
    "connection": "offline",
    "transport": ["usb_serial", "http", "https"],
    "emg_channels": 1,
    "tongue_pressure_channels": 1,
    "lip_force": True,
    "motorized_traction": True,
    "wifi_portal": True,
    "mqtt": False,
    "last_seen_at": None,
}

DEVICE_EVENTS: dict[tuple[str, str], dict[str, object]] = {}
EVENT_LOCK = Lock()


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready", tags=["system"])
def ready() -> dict[str, str]:
    return {"status": "ready"}


@app.get("/api/v1/dashboard/summary", tags=["dashboard"])
def dashboard_summary() -> dict[str, object]:
    device = current_device()
    return {
        "device_status": device["connection"],
        "pending_sync": 0,
        "completed_sessions": 0,
        "last_calibration": None,
        "generated_at": datetime.now(UTC),
    }


@app.get("/api/v1/devices/current", tags=["devices"])
def current_device() -> dict[str, object]:
    device = dict(CAPABILITIES)
    last_seen = device["last_seen_at"]
    if isinstance(last_seen, datetime) and datetime.now(UTC) - last_seen > timedelta(seconds=45):
        device["connection"] = "offline"
    return device


@app.get("/api/v1/sessions", tags=["sessions"])
def sessions() -> list[object]:
    return []


@app.post("/api/v1/device-events", status_code=status.HTTP_202_ACCEPTED, tags=["devices"])
def receive_device_event(event: DeviceEvent) -> dict[str, object]:
    key = (event.device_id, event.message_id)
    received_at = datetime.now(UTC)
    with EVENT_LOCK:
        if key in DEVICE_EVENTS:
            return {"accepted": True, "duplicate": True, "message_id": event.message_id}
        DEVICE_EVENTS[key] = {**event.model_dump(), "received_at": received_at}
        if event.device_id == CAPABILITIES["device_id"]:
            if event.event == "online":
                CAPABILITIES["connection"] = "online"
            elif event.event == "offline":
                CAPABILITIES["connection"] = "offline"
            CAPABILITIES["last_seen_at"] = received_at
    return {"accepted": True, "duplicate": False, "message_id": event.message_id}
