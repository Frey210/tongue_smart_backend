from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class DeviceEventRecord(Base):
    __tablename__ = "device_events"
    __table_args__ = (UniqueConstraint("device_id", "message_id", name="uq_device_message"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer)
    message_id: Mapped[str] = mapped_column(String(96))
    device_id: Mapped[str] = mapped_column(String(64), index=True)
    event: Mapped[str] = mapped_column(String(32), index=True)
    firmware_version: Mapped[str] = mapped_column(String(32))
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    uptime_ms: Mapped[int | None] = mapped_column(nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class UserRecord(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(24), index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RefreshSessionRecord(Base):
    __tablename__ = "refresh_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RegistrationRequestRecord(Base):
    __tablename__ = "registration_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120))
    institution: Mapped[str] = mapped_column(String(160))
    password_hash: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class SubjectRecord(Base):
    __tablename__ = "subjects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    subject_code: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    initials: Mapped[str] = mapped_column(String(12))
    research_group: Mapped[str] = mapped_column(String(80), index=True)
    year_of_birth: Mapped[int | None] = mapped_column(Integer, nullable=True)
    consent_status: Mapped[str] = mapped_column(String(24), index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)


class AuditEventRecord(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    entity_id: Mapped[str] = mapped_column(String(36), index=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ExaminationSessionRecord(Base):
    __tablename__ = "examination_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_code: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    subject_id: Mapped[str] = mapped_column(ForeignKey("subjects.id"), index=True)
    operator_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    device_id: Mapped[str] = mapped_column(String(64), index=True)
    modules: Mapped[list[str]] = mapped_column(JSON)
    protocol_stages: Mapped[list[str]] = mapped_column(JSON)
    electrode_site: Mapped[str | None] = mapped_column(String(48), nullable=True)
    electrode_site_note: Mapped[str | None] = mapped_column(String(240), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="prepared", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SampleBatchRecord(Base):
    __tablename__ = "sample_batches"
    __table_args__ = (
        UniqueConstraint("device_id", "message_id", name="uq_sample_batch_device_message"),
        UniqueConstraint("session_id", "sequence", name="uq_sample_batch_session_sequence"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("examination_sessions.id"), index=True)
    device_id: Mapped[str] = mapped_column(String(64), index=True)
    message_id: Mapped[str] = mapped_column(String(96))
    sequence: Mapped[int] = mapped_column(Integer)
    checksum: Mapped[str] = mapped_column(String(64))
    samples: Mapped[list[dict[str, object]]] = mapped_column(JSON)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class EventMarkerRecord(Base):
    __tablename__ = "event_markers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("examination_sessions.id"), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    protocol_stage: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(120))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OperatorNoteRecord(Base):
    __tablename__ = "operator_notes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("examination_sessions.id"), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    note: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ExportJobRecord(Base):
    __tablename__ = "export_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    requested_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    session_ids: Mapped[list[str]] = mapped_column(JSON)
    data_mode: Mapped[str] = mapped_column(String(24))
    include_metadata: Mapped[bool] = mapped_column(Boolean, default=True)
    include_markers: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    row_count: Mapped[int] = mapped_column(Integer)
    checksum: Mapped[str] = mapped_column(String(64))
    filename: Mapped[str] = mapped_column(String(160))
    csv_content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class SessionControlRecord(Base):
    __tablename__ = "session_controls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("examination_sessions.id"), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    measurement: Mapped[str] = mapped_column(String(32), index=True)
    phase: Mapped[str] = mapped_column(String(24), index=True)
    protocol_stage: Mapped[str] = mapped_column(String(64))
    fsr_point: Mapped[str | None] = mapped_column(String(48), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
