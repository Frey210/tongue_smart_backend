from datetime import UTC, datetime, timedelta
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .database import Base, engine, get_db
from .models import DeviceEventRecord


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield

app = FastAPI(
    title="Tongue Smart API",
    version="0.1.0",
    description="Synchronization and research data API; measurement remains device-local.",
    lifespan=lifespan,
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

@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready", tags=["system"])
def ready() -> dict[str, str]:
    return {"status": "ready"}


@app.get("/api/v1/dashboard/summary", tags=["dashboard"])
def dashboard_summary(db: Session = Depends(get_db)) -> dict[str, object]:
    device = current_device(db)
    return {
        "device_status": device["connection"],
        "pending_sync": 0,
        "completed_sessions": 0,
        "last_calibration": None,
        "generated_at": datetime.now(UTC),
    }


@app.get("/api/v1/devices/current", tags=["devices"])
def current_device(db: Session = Depends(get_db)) -> dict[str, object]:
    device = dict(CAPABILITIES)
    latest = db.scalar(
        select(DeviceEventRecord)
        .where(DeviceEventRecord.device_id == CAPABILITIES["device_id"])
        .order_by(desc(DeviceEventRecord.received_at))
        .limit(1)
    )
    last_seen = latest.received_at if latest else None
    if isinstance(last_seen, datetime) and last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=UTC)
    device["last_seen_at"] = last_seen
    if latest and latest.event == "online":
        device["connection"] = "online"
    if isinstance(last_seen, datetime) and datetime.now(UTC) - last_seen > timedelta(seconds=45):
        device["connection"] = "offline"
    return device


@app.get("/api/v1/sessions", tags=["sessions"])
def sessions() -> list[object]:
    return []


@app.post("/api/v1/device-events", status_code=status.HTTP_202_ACCEPTED, tags=["devices"])
def receive_device_event(event: DeviceEvent, db: Session = Depends(get_db)) -> dict[str, object]:
    received_at = datetime.now(UTC)
    record = DeviceEventRecord(**event.model_dump(), received_at=received_at)
    db.add(record)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return {"accepted": True, "duplicate": True, "message_id": event.message_id}
    return {"accepted": True, "duplicate": False, "message_id": event.message_id}
