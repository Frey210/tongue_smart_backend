from contextlib import asynccontextmanager
import csv
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import json
from io import StringIO
import os
from typing import Annotated, Literal
from uuid import uuid4

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .database import Base, SessionLocal, engine, get_db
from .models import AuditEventRecord, DeviceEventRecord, EventMarkerRecord, ExaminationSessionRecord, ExportJobRecord, OperatorNoteRecord, RefreshSessionRecord, RegistrationRequestRecord, SampleBatchRecord, SubjectRecord, UserRecord
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
    allow_methods=["GET", "POST", "PATCH"],
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


ConsentStatus = Literal["pending", "granted", "withdrawn"]


class SubjectCreate(BaseModel):
    subject_code: str = Field(min_length=2, max_length=48, pattern=r"^[A-Za-z0-9_-]+$")
    initials: str = Field(min_length=1, max_length=12)
    research_group: str = Field(min_length=2, max_length=80)
    year_of_birth: int | None = Field(default=None, ge=1900, le=datetime.now(UTC).year)
    consent_status: ConsentStatus
    notes: str = Field(default="", max_length=2000)


class SubjectUpdate(BaseModel):
    initials: str | None = Field(default=None, min_length=1, max_length=12)
    research_group: str | None = Field(default=None, min_length=2, max_length=80)
    year_of_birth: int | None = Field(default=None, ge=1900, le=datetime.now(UTC).year)
    consent_status: ConsentStatus | None = None
    notes: str | None = Field(default=None, max_length=2000)
    is_active: bool | None = None


MeasurementModule = Literal["emg", "tongue_pressure", "lip_force"]
ElectrodeSite = Literal["masseter_left", "masseter_right", "temporalis_left", "temporalis_right", "other"]


class ExaminationSessionCreate(BaseModel):
    subject_id: str = Field(min_length=36, max_length=36)
    device_id: str = Field(min_length=1, max_length=64)
    modules: list[MeasurementModule] = Field(min_length=1, max_length=3)
    protocol_stages: list[str] = Field(min_length=1, max_length=12)
    electrode_site: ElectrodeSite | None = None
    electrode_site_note: str | None = Field(default=None, max_length=240)


class MeasurementSample(BaseModel):
    timestamp: datetime
    protocol_stage: str = Field(min_length=1, max_length=64)
    sensor_channel: str = Field(min_length=1, max_length=64)
    raw_value: float
    calibrated_value: float | None = None
    measurement_unit: str = Field(min_length=1, max_length=16)
    signal_quality: Literal["good", "fair", "poor", "invalid"] = "good"


class SampleBatchIngest(BaseModel):
    message_id: str = Field(min_length=1, max_length=96)
    device_id: str = Field(min_length=1, max_length=64)
    sequence: int = Field(ge=0)
    checksum: str = Field(min_length=64, max_length=64, pattern=r"^[a-fA-F0-9]{64}$")
    samples: list[MeasurementSample] = Field(min_length=1, max_length=500)


class EventMarkerCreate(BaseModel):
    protocol_stage: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=120)
    occurred_at: datetime


class OperatorNoteCreate(BaseModel):
    note: str = Field(min_length=1, max_length=2000)


class ExportCreate(BaseModel):
    session_ids: list[str] = Field(min_length=1, max_length=100)
    data_mode: Literal["raw", "processed", "both"] = "both"
    include_metadata: bool = True
    include_markers: bool = True


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


def subject_view(subject: SubjectRecord) -> dict[str, object]:
    return {
        "id": subject.id, "subject_code": subject.subject_code, "initials": subject.initials,
        "research_group": subject.research_group, "year_of_birth": subject.year_of_birth,
        "consent_status": subject.consent_status, "notes": subject.notes,
        "is_active": subject.is_active, "created_at": subject.created_at, "updated_at": subject.updated_at,
    }


def examination_view(session: ExaminationSessionRecord, db: Session) -> dict[str, object]:
    subject = db.get(SubjectRecord, session.subject_id)
    operator = db.get(UserRecord, session.operator_id)
    return {
        "id": session.id, "session_code": session.session_code, "subject_id": session.subject_id,
        "subject_code": subject.subject_code if subject else None,
        "operator_id": session.operator_id, "operator_name": operator.full_name if operator else None,
        "device_id": session.device_id, "modules": session.modules, "protocol_stages": session.protocol_stages,
        "electrode_site": session.electrode_site, "electrode_site_note": session.electrode_site_note,
        "status": session.status, "created_at": session.created_at, "started_at": session.started_at,
        "completed_at": session.completed_at,
    }


def add_audit(db: Session, actor_id: str, action: str, entity_type: str, entity_id: str, detail: str = "") -> None:
    db.add(AuditEventRecord(id=str(uuid4()), actor_id=actor_id, action=action, entity_type=entity_type,
                            entity_id=entity_id, detail=detail, created_at=datetime.now(UTC)))


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


def require_device_key(x_device_key: Annotated[str | None, Header()] = None) -> None:
    expected = os.getenv("TONGUE_SMART_DEVICE_API_KEY", "")
    if not expected:
        raise HTTPException(status_code=503, detail="Device ingest is not configured")
    if not x_device_key or not hmac.compare_digest(x_device_key, expected):
        raise HTTPException(status_code=401, detail="Invalid device credential")


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


@app.get("/api/v1/subjects", tags=["subjects"])
def list_subjects(
    search: str = Query(default="", max_length=80),
    consent_status: ConsentStatus | None = None,
    _: UserRecord = Depends(require_roles("admin", "operator", "researcher")),
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    statement = select(SubjectRecord).order_by(desc(SubjectRecord.created_at))
    if search.strip():
        term = f"%{search.strip()}%"
        statement = statement.where(or_(SubjectRecord.subject_code.ilike(term), SubjectRecord.initials.ilike(term), SubjectRecord.research_group.ilike(term)))
    if consent_status:
        statement = statement.where(SubjectRecord.consent_status == consent_status)
    return [subject_view(subject) for subject in db.scalars(statement).all()]


@app.post("/api/v1/subjects", status_code=201, tags=["subjects"])
def create_subject(
    payload: SubjectCreate,
    actor: UserRecord = Depends(require_roles("admin", "operator")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    code = payload.subject_code.strip().upper()
    if db.scalar(select(SubjectRecord).where(SubjectRecord.subject_code == code)):
        raise HTTPException(status_code=409, detail="Kode subjek sudah digunakan")
    now = datetime.now(UTC)
    subject = SubjectRecord(id=str(uuid4()), subject_code=code, initials=payload.initials.strip().upper(),
                            research_group=payload.research_group.strip(), year_of_birth=payload.year_of_birth,
                            consent_status=payload.consent_status, notes=payload.notes.strip(), is_active=True,
                            created_at=now, updated_at=now, created_by=actor.id)
    db.add(subject)
    add_audit(db, actor.id, "subject.created", "subject", subject.id, f"consent={subject.consent_status}")
    db.commit()
    return subject_view(subject)


@app.patch("/api/v1/subjects/{subject_id}", tags=["subjects"])
def update_subject(
    subject_id: str,
    payload: SubjectUpdate,
    actor: UserRecord = Depends(require_roles("admin", "operator")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    subject = db.get(SubjectRecord, subject_id)
    if subject is None:
        raise HTTPException(status_code=404, detail="Subjek tidak ditemukan")
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        if isinstance(value, str): value = value.strip()
        if field == "initials" and isinstance(value, str): value = value.upper()
        setattr(subject, field, value)
    subject.updated_at = datetime.now(UTC)
    add_audit(db, actor.id, "subject.updated", "subject", subject.id, ",".join(sorted(changes)))
    db.commit()
    return subject_view(subject)


@app.get("/api/v1/dashboard/summary", tags=["dashboard"])
def dashboard_summary(
    _: UserRecord = Depends(require_roles("admin", "operator", "researcher")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    device = device_snapshot(db)
    return {
        "device_status": device["connection"],
        "pending_sync": db.scalar(select(func.count()).select_from(ExaminationSessionRecord).where(ExaminationSessionRecord.status.in_(["prepared", "active"]))) or 0,
        "completed_sessions": db.scalar(select(func.count()).select_from(ExaminationSessionRecord).where(ExaminationSessionRecord.status == "completed")) or 0,
        "subject_count": db.scalar(select(func.count()).select_from(SubjectRecord).where(SubjectRecord.is_active.is_(True))) or 0,
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
def sessions(
    _: UserRecord = Depends(require_roles("admin", "operator", "researcher")),
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    records = db.scalars(select(ExaminationSessionRecord).order_by(desc(ExaminationSessionRecord.created_at))).all()
    return [examination_view(record, db) for record in records]


@app.post("/api/v1/sessions", status_code=201, tags=["sessions"])
def create_examination_session(
    payload: ExaminationSessionCreate,
    operator: UserRecord = Depends(require_roles("admin", "operator")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    subject = db.get(SubjectRecord, payload.subject_id)
    if subject is None or not subject.is_active:
        raise HTTPException(status_code=404, detail="Subjek aktif tidak ditemukan")
    if subject.consent_status != "granted":
        raise HTTPException(status_code=409, detail="Consent subjek harus disetujui sebelum membuat sesi")
    supported = {"emg" if CAPABILITIES["emg_channels"] else "", "tongue_pressure" if CAPABILITIES["tongue_pressure_channels"] else "", "lip_force" if CAPABILITIES["lip_force"] else ""}
    unsupported = set(payload.modules) - supported
    if unsupported:
        raise HTTPException(status_code=422, detail=f"Modul tidak tersedia: {', '.join(sorted(unsupported))}")
    if "emg" in payload.modules:
        if payload.electrode_site is None:
            raise HTTPException(status_code=422, detail="Posisi elektroda wajib untuk modul EMG")
        if payload.electrode_site == "other" and not (payload.electrode_site_note or "").strip():
            raise HTTPException(status_code=422, detail="Catatan posisi elektroda wajib untuk pilihan lainnya")
    now = datetime.now(UTC)
    session = ExaminationSessionRecord(
        id=str(uuid4()), session_code=f"SESS-{now.year}-{uuid4().hex[:6].upper()}",
        subject_id=subject.id, operator_id=operator.id, device_id=payload.device_id,
        modules=list(dict.fromkeys(payload.modules)), protocol_stages=list(dict.fromkeys(payload.protocol_stages)),
        electrode_site=payload.electrode_site if "emg" in payload.modules else None,
        electrode_site_note=(payload.electrode_site_note or "").strip() or None,
        status="prepared", created_at=now, started_at=None, completed_at=None,
    )
    db.add(session)
    add_audit(db, operator.id, "session.prepared", "examination_session", session.id, f"subject={subject.subject_code}")
    db.commit()
    return examination_view(session, db)


@app.post("/api/v1/sessions/{session_id}/start", tags=["sessions"])
def start_examination_session(
    session_id: str,
    operator: UserRecord = Depends(require_roles("admin", "operator")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    session = db.get(ExaminationSessionRecord, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Sesi tidak ditemukan")
    if session.status != "prepared":
        raise HTTPException(status_code=409, detail="Hanya sesi prepared yang dapat dimulai")
    session.status = "active"
    session.started_at = datetime.now(UTC)
    add_audit(db, operator.id, "session.started", "examination_session", session.id)
    db.commit()
    return examination_view(session, db)


@app.post("/api/v1/sessions/{session_id}/finalize", tags=["sessions"])
def finalize_examination_session(
    session_id: str,
    operator: UserRecord = Depends(require_roles("admin", "operator")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    session = db.get(ExaminationSessionRecord, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Sesi tidak ditemukan")
    if session.status != "active":
        raise HTTPException(status_code=409, detail="Hanya sesi aktif yang dapat diselesaikan")
    session.status = "completed"
    session.completed_at = datetime.now(UTC)
    add_audit(db, operator.id, "session.completed", "examination_session", session.id)
    db.commit()
    return examination_view(session, db)


@app.post("/api/v1/sessions/{session_id}/batches", status_code=202, tags=["ingest"])
def ingest_sample_batch(
    session_id: str,
    payload: SampleBatchIngest,
    _: None = Depends(require_device_key),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    session = db.get(ExaminationSessionRecord, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Sesi tidak ditemukan")
    if session.status != "active":
        raise HTTPException(status_code=409, detail="Sesi belum aktif atau sudah selesai")
    if payload.device_id != session.device_id:
        raise HTTPException(status_code=409, detail="Device tidak sesuai dengan sesi")
    sample_data = [sample.model_dump(mode="json") for sample in payload.samples]
    computed = hashlib.sha256(json.dumps(sample_data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if not hmac.compare_digest(computed, payload.checksum.lower()):
        raise HTTPException(status_code=422, detail="Checksum batch tidak valid")
    existing = db.scalar(select(SampleBatchRecord).where(SampleBatchRecord.device_id == payload.device_id, SampleBatchRecord.message_id == payload.message_id))
    if existing:
        if existing.checksum != computed or existing.session_id != session_id:
            raise HTTPException(status_code=409, detail="message_id telah digunakan untuk payload berbeda")
        return {"receipt_id": existing.id, "duplicate": True, "sequence": existing.sequence, "received_at": existing.received_at}
    record = SampleBatchRecord(id=str(uuid4()), session_id=session_id, device_id=payload.device_id,
                               message_id=payload.message_id, sequence=payload.sequence, checksum=computed,
                               samples=sample_data, received_at=datetime.now(UTC))
    db.add(record)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Sequence batch sudah diterima") from exc
    return {"receipt_id": record.id, "duplicate": False, "sequence": record.sequence, "received_at": record.received_at}


@app.get("/api/v1/sessions/{session_id}/results", tags=["results"])
def session_results(
    session_id: str,
    sensor_channel: str | None = Query(default=None, max_length=64),
    max_points: int = Query(default=300, ge=10, le=1000),
    _: UserRecord = Depends(require_roles("admin", "operator", "researcher")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    session = db.get(ExaminationSessionRecord, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Sesi tidak ditemukan")
    batches = db.scalars(select(SampleBatchRecord).where(SampleBatchRecord.session_id == session_id).order_by(SampleBatchRecord.sequence)).all()
    samples = [sample for batch in batches for sample in batch.samples if not sensor_channel or sample.get("sensor_channel") == sensor_channel]
    channels = sorted({str(sample.get("sensor_channel")) for batch in batches for sample in batch.samples})
    stride = max(1, (len(samples) + max_points - 1) // max_points)
    points = samples[::stride]
    values = [float(sample["calibrated_value"] if sample.get("calibrated_value") is not None else sample["raw_value"]) for sample in samples]
    quality = {name: sum(1 for sample in samples if sample.get("signal_quality") == name) for name in ("good", "fair", "poor", "invalid")}
    markers = db.scalars(select(EventMarkerRecord).where(EventMarkerRecord.session_id == session_id).order_by(EventMarkerRecord.occurred_at)).all()
    notes = db.scalars(select(OperatorNoteRecord).where(OperatorNoteRecord.session_id == session_id).order_by(desc(OperatorNoteRecord.created_at))).all()
    return {
        "session": examination_view(session, db), "channels": channels, "selected_channel": sensor_channel,
        "sample_count": len(samples), "batch_count": len(batches), "downsample_stride": stride,
        "summary": {"minimum": min(values) if values else None, "maximum": max(values) if values else None,
                    "average": sum(values) / len(values) if values else None, "quality": quality},
        "points": [{"timestamp": sample.get("timestamp"), "protocol_stage": sample.get("protocol_stage"),
                    "sensor_channel": sample.get("sensor_channel"),
                    "value": sample.get("calibrated_value") if sample.get("calibrated_value") is not None else sample.get("raw_value"),
                    "unit": sample.get("measurement_unit"), "quality": sample.get("signal_quality")} for sample in points],
        "markers": [{"id": marker.id, "protocol_stage": marker.protocol_stage, "label": marker.label,
                     "occurred_at": marker.occurred_at} for marker in markers],
        "notes": [{"id": note.id, "actor_id": note.actor_id, "note": note.note, "created_at": note.created_at} for note in notes],
    }


@app.post("/api/v1/sessions/{session_id}/markers", status_code=201, tags=["results"])
def create_event_marker(
    session_id: str,
    payload: EventMarkerCreate,
    actor: UserRecord = Depends(require_roles("admin", "operator")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    session = db.get(ExaminationSessionRecord, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Sesi tidak ditemukan")
    marker = EventMarkerRecord(id=str(uuid4()), session_id=session_id, actor_id=actor.id,
                               protocol_stage=payload.protocol_stage, label=payload.label.strip(),
                               occurred_at=payload.occurred_at, created_at=datetime.now(UTC))
    db.add(marker)
    add_audit(db, actor.id, "marker.created", "examination_session", session_id, marker.label)
    db.commit()
    return {"id": marker.id, "protocol_stage": marker.protocol_stage, "label": marker.label, "occurred_at": marker.occurred_at}


@app.post("/api/v1/sessions/{session_id}/notes", status_code=201, tags=["results"])
def create_operator_note(
    session_id: str,
    payload: OperatorNoteCreate,
    actor: UserRecord = Depends(require_roles("admin", "operator", "researcher")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    if db.get(ExaminationSessionRecord, session_id) is None:
        raise HTTPException(status_code=404, detail="Sesi tidak ditemukan")
    note = OperatorNoteRecord(id=str(uuid4()), session_id=session_id, actor_id=actor.id,
                              note=payload.note.strip(), created_at=datetime.now(UTC))
    db.add(note)
    add_audit(db, actor.id, "note.created", "examination_session", session_id)
    db.commit()
    return {"id": note.id, "actor_id": note.actor_id, "note": note.note, "created_at": note.created_at}


def export_view(job: ExportJobRecord) -> dict[str, object]:
    return {"id": job.id, "session_ids": job.session_ids, "data_mode": job.data_mode,
            "include_metadata": job.include_metadata, "include_markers": job.include_markers,
            "status": job.status, "row_count": job.row_count, "checksum": job.checksum,
            "filename": job.filename, "created_at": job.created_at}


def spreadsheet_safe(value: object) -> object:
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def utc_timestamp_key(value: datetime | str) -> str:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


@app.get("/api/v1/exports", tags=["exports"])
def list_exports(
    _: UserRecord = Depends(require_roles("admin", "operator", "researcher")),
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    return [export_view(job) for job in db.scalars(select(ExportJobRecord).order_by(desc(ExportJobRecord.created_at))).all()]


@app.post("/api/v1/exports", status_code=201, tags=["exports"])
def create_export(
    payload: ExportCreate,
    actor: UserRecord = Depends(require_roles("admin", "operator", "researcher")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    sessions_by_id: dict[str, ExaminationSessionRecord] = {}
    for session_id in dict.fromkeys(payload.session_ids):
        session = db.get(ExaminationSessionRecord, session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"Sesi tidak ditemukan: {session_id}")
        sessions_by_id[session_id] = session
    output = StringIO(newline="")
    base_headers = ["timestamp", "subject_id", "session_id", "protocol_stage", "sensor_channel", "raw_value",
                    "calibrated_value", "measurement_unit", "signal_quality", "event_marker"]
    metadata_headers = ["device_id", "session_status", "electrode_site", "operator_id"] if payload.include_metadata else []
    writer = csv.DictWriter(output, fieldnames=base_headers + metadata_headers, lineterminator="\n")
    writer.writeheader()
    row_count = 0
    for session_id, session in sessions_by_id.items():
        subject = db.get(SubjectRecord, session.subject_id)
        markers = db.scalars(select(EventMarkerRecord).where(EventMarkerRecord.session_id == session_id)).all() if payload.include_markers else []
        marker_map: dict[str, list[str]] = {}
        for marker in markers:
            marker_map.setdefault(utc_timestamp_key(marker.occurred_at), []).append(marker.label)
        batches = db.scalars(select(SampleBatchRecord).where(SampleBatchRecord.session_id == session_id).order_by(SampleBatchRecord.sequence)).all()
        for batch in batches:
            for sample in batch.samples:
                timestamp = str(sample.get("timestamp", ""))
                raw = sample.get("raw_value") if payload.data_mode in ("raw", "both") else ""
                calibrated = sample.get("calibrated_value") if payload.data_mode in ("processed", "both") else ""
                row = {"timestamp": timestamp, "subject_id": subject.subject_code if subject else session.subject_id,
                       "session_id": session.session_code, "protocol_stage": sample.get("protocol_stage", ""),
                       "sensor_channel": sample.get("sensor_channel", ""), "raw_value": raw,
                       "calibrated_value": calibrated, "measurement_unit": sample.get("measurement_unit", ""),
                       "signal_quality": sample.get("signal_quality", ""),
                       "event_marker": " | ".join(marker_map.get(utc_timestamp_key(timestamp), []))}
                if payload.include_metadata:
                    row.update({"device_id": session.device_id, "session_status": session.status,
                                "electrode_site": session.electrode_site or "", "operator_id": session.operator_id})
                writer.writerow({key: spreadsheet_safe(value) for key, value in row.items()})
                row_count += 1
    content = output.getvalue()
    checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()
    now = datetime.now(UTC)
    job = ExportJobRecord(id=str(uuid4()), requested_by=actor.id, session_ids=list(sessions_by_id),
                          data_mode=payload.data_mode, include_metadata=payload.include_metadata,
                          include_markers=payload.include_markers, status="ready", row_count=row_count,
                          checksum=checksum, filename=f"tongue-smart-export-{now:%Y%m%d-%H%M%S}.csv",
                          csv_content=content, created_at=now)
    db.add(job)
    add_audit(db, actor.id, "export.created", "export_job", job.id, f"rows={row_count};checksum={checksum}")
    db.commit()
    return export_view(job)


@app.get("/api/v1/exports/{export_id}/download", tags=["exports"])
def download_export(
    export_id: str,
    actor: UserRecord = Depends(require_roles("admin", "operator", "researcher")),
    db: Session = Depends(get_db),
) -> Response:
    job = db.get(ExportJobRecord, export_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Export tidak ditemukan")
    add_audit(db, actor.id, "export.downloaded", "export_job", job.id, job.checksum)
    db.commit()
    return Response(content="\ufeff" + job.csv_content, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{job.filename}"', "X-Content-SHA256": job.checksum})


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
