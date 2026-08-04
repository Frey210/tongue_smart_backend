from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
import os
from typing import Annotated, Literal
from uuid import uuid4

import jwt
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .database import Base, SessionLocal, engine, get_db
from .models import DeviceEventRecord, RefreshSessionRecord, RegistrationRequestRecord, UserRecord
from .security import (
    create_access_token,
    decode_access_token,
    digest_token,
    hash_password,
    new_refresh_token,
    verify_password,
)

Role = Literal["admin", "operator", "researcher"]
bearer = HTTPBearer(auto_error=False)


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    bootstrap_email = os.getenv("TONGUE_SMART_BOOTSTRAP_ADMIN_EMAIL")
    bootstrap_password = os.getenv("TONGUE_SMART_BOOTSTRAP_ADMIN_PASSWORD")
    if bootstrap_email and bootstrap_password:
        with SessionLocal() as db:
            email = bootstrap_email.strip().lower()
            if not db.scalar(select(UserRecord).where(UserRecord.email == email)):
                db.add(UserRecord(
                    id=str(uuid4()), email=email, full_name="System Administrator", role="admin",
                    password_hash=hash_password(bootstrap_password), is_active=True,
                    created_at=datetime.now(UTC),
                ))
                db.commit()
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
    allow_headers=["Content-Type", "Authorization"],
)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=10, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=32, max_length=256)


class UserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    full_name: str = Field(min_length=2, max_length=120)
    role: Role
    password: str = Field(min_length=10, max_length=128)


class RegistrationRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    full_name: str = Field(min_length=2, max_length=120)
    institution: str = Field(min_length=2, max_length=160)
    password: str = Field(min_length=10, max_length=128)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=10, max_length=128)
    new_password: str = Field(min_length=10, max_length=128)


class DeviceEvent(BaseModel):
    schema_version: int = Field(default=1, ge=1)
    message_id: str = Field(min_length=1, max_length=96)
    device_id: str = Field(min_length=1, max_length=64)
    event: Literal["boot", "online", "offline", "measurement_saved"]
    firmware_version: str = Field(min_length=1, max_length=32)
    occurred_at: datetime | None = None
    uptime_ms: int | None = Field(default=None, ge=0)


def user_view(user: UserRecord) -> dict[str, object]:
    return {
        "id": user.id, "email": user.email, "full_name": user.full_name,
        "role": user.role, "is_active": user.is_active,
    }


def registration_view(request: RegistrationRequestRecord) -> dict[str, object]:
    return {
        "id": request.id, "email": request.email, "full_name": request.full_name,
        "institution": request.institution, "status": request.status,
        "created_at": request.created_at,
    }


def issue_session(user: UserRecord, db: Session) -> dict[str, object]:
    access_token, expires_in = create_access_token(user.id, user.role)
    refresh_token, token_hash, refresh_expires = new_refresh_token()
    db.add(RefreshSessionRecord(
        id=str(uuid4()), token_hash=token_hash, user_id=user.id,
        expires_at=refresh_expires, revoked_at=None, created_at=datetime.now(UTC),
    ))
    db.commit()
    return {
        "access_token": access_token, "refresh_token": refresh_token,
        "token_type": "bearer", "expires_in": expires_in, "user": user_view(user),
    }


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    db: Annotated[Session, Depends(get_db)],
) -> UserRecord:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Session expired or invalid") from exc
    user = db.get(UserRecord, str(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="User is inactive or unavailable")
    return user


def require_roles(*roles: Role):
    def dependency(user: Annotated[UserRecord, Depends(get_current_user)]) -> UserRecord:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permission")
        return user
    return dependency


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


@app.post("/api/v1/auth/login", tags=["auth"])
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> dict[str, object]:
    user = db.scalar(select(UserRecord).where(UserRecord.email == payload.email.strip().lower()))
    if user is None or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Email atau password tidak valid")
    return issue_session(user, db)


@app.post("/api/v1/auth/register", status_code=202, tags=["auth"])
def register(payload: RegistrationRequest, db: Session = Depends(get_db)) -> dict[str, object]:
    email = payload.email.strip().lower()
    if db.scalar(select(UserRecord).where(UserRecord.email == email)):
        raise HTTPException(status_code=409, detail="Email sudah terdaftar")
    existing = db.scalar(select(RegistrationRequestRecord).where(
        RegistrationRequestRecord.email == email
    ))
    if existing:
        raise HTTPException(status_code=409, detail="Permintaan pendaftaran sudah dikirim")
    request = RegistrationRequestRecord(
        id=str(uuid4()), email=email, full_name=payload.full_name.strip(),
        institution=payload.institution.strip(), password_hash=hash_password(payload.password),
        status="pending", created_at=datetime.now(UTC), reviewed_at=None, reviewed_by=None,
    )
    db.add(request)
    db.commit()
    return {"accepted": True, "status": "pending_approval"}


@app.post("/api/v1/auth/refresh", tags=["auth"])
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)) -> dict[str, object]:
    session = db.scalar(select(RefreshSessionRecord).where(
        RefreshSessionRecord.token_hash == digest_token(payload.refresh_token)
    ))
    now = datetime.now(UTC)
    expires_at = session.expires_at if session else None
    if isinstance(expires_at, datetime) and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if session is None or session.revoked_at is not None or expires_at is None or expires_at <= now:
        raise HTTPException(status_code=401, detail="Refresh session tidak valid")
    user = db.get(UserRecord, session.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="User is inactive or unavailable")
    session.revoked_at = now
    db.commit()
    return issue_session(user, db)


@app.post("/api/v1/auth/logout", status_code=204, tags=["auth"])
def logout(payload: RefreshRequest, db: Session = Depends(get_db)) -> None:
    session = db.scalar(select(RefreshSessionRecord).where(
        RefreshSessionRecord.token_hash == digest_token(payload.refresh_token)
    ))
    if session and session.revoked_at is None:
        session.revoked_at = datetime.now(UTC)
        db.commit()


@app.get("/api/v1/auth/me", tags=["auth"])
def me(user: UserRecord = Depends(get_current_user)) -> dict[str, object]:
    return user_view(user)


@app.post("/api/v1/auth/change-password", status_code=204, tags=["auth"])
def change_password(
    payload: ChangePasswordRequest, user: UserRecord = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Password saat ini tidak valid")
    user.password_hash = hash_password(payload.new_password)
    db.query(RefreshSessionRecord).filter(
        RefreshSessionRecord.user_id == user.id,
        RefreshSessionRecord.revoked_at.is_(None),
    ).update({"revoked_at": datetime.now(UTC)})
    db.commit()


@app.get("/api/v1/users", tags=["users"])
def list_users(
    _: UserRecord = Depends(require_roles("admin")), db: Session = Depends(get_db)
) -> list[dict[str, object]]:
    return [user_view(user) for user in db.scalars(select(UserRecord).order_by(UserRecord.email))]


@app.post("/api/v1/users", status_code=201, tags=["users"])
def create_user(
    payload: UserCreate, _: UserRecord = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    email = payload.email.strip().lower()
    user = UserRecord(
        id=str(uuid4()), email=email, full_name=payload.full_name.strip(), role=payload.role,
        password_hash=hash_password(payload.password), is_active=True, created_at=datetime.now(UTC),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Email sudah terdaftar") from exc
    return user_view(user)


@app.get("/api/v1/registration-requests", tags=["users"])
def list_registration_requests(
    _: UserRecord = Depends(require_roles("admin")), db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    requests = db.scalars(select(RegistrationRequestRecord).where(
        RegistrationRequestRecord.status == "pending"
    ).order_by(RegistrationRequestRecord.created_at)).all()
    return [registration_view(request) for request in requests]


@app.post("/api/v1/registration-requests/{request_id}/approve", tags=["users"])
def approve_registration(
    request_id: str, admin: UserRecord = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    request = db.get(RegistrationRequestRecord, request_id)
    if request is None or request.status != "pending":
        raise HTTPException(status_code=404, detail="Permintaan tidak ditemukan")
    if db.scalar(select(UserRecord).where(UserRecord.email == request.email)):
        raise HTTPException(status_code=409, detail="Email sudah terdaftar")
    user = UserRecord(
        id=str(uuid4()), email=request.email, full_name=request.full_name, role="operator",
        password_hash=request.password_hash, is_active=True, created_at=datetime.now(UTC),
    )
    request.status = "approved"
    request.reviewed_at = datetime.now(UTC)
    request.reviewed_by = admin.id
    db.add(user)
    db.commit()
    return user_view(user)

@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready", tags=["system"])
def ready() -> dict[str, str]:
    return {"status": "ready"}


@app.get("/api/v1/dashboard/summary", tags=["dashboard"])
def dashboard_summary(
    _: UserRecord = Depends(require_roles("admin", "operator", "researcher")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    device = device_snapshot(db)
    return {
        "device_status": device["connection"],
        "pending_sync": 0,
        "completed_sessions": 0,
        "last_calibration": None,
        "generated_at": datetime.now(UTC),
    }


@app.get("/api/v1/devices/current", tags=["devices"])
def current_device(
    _: UserRecord = Depends(require_roles("admin", "operator", "researcher")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    return device_snapshot(db)


def device_snapshot(db: Session) -> dict[str, object]:
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
def sessions(_: UserRecord = Depends(require_roles("admin", "operator", "researcher"))) -> list[object]:
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
