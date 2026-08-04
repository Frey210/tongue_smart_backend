from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint
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
