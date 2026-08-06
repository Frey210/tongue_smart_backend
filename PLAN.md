# Backend Plan

## Tujuan

Menyediakan API dan ingest yang tidak mengganggu prinsip offline-first firmware, dengan jejak audit dan format data riset yang dapat diekspor.

## Stack awal

- Python 3.12+
- FastAPI, Pydantic v2, Uvicorn
- PostgreSQL + SQLAlchemy/Alembic saat milestone database dimulai
- TimescaleDB hanya untuk raw time-series setelah volume dan retention terukur
- MQTT/EMQX ditunda; tahap awal memakai REST melalui HTTP/HTTPS

## Boundary

```text
API (/api/v1) → application services → repositories → PostgreSQL/TimescaleDB
                ↘ live-session broker → WebSocket clients
```

Firmware tidak memanggil logic dashboard. Frontend tidak mengakses database langsung.

## Domain minimum

- User, Role, RefreshSession
- Subject, ConsentRecord
- Device, DevicePairing, credential unik ter-hash, DeviceCapability, Calibration
- ExaminationSession, ProtocolStage
- EMG stage menyimpan `electrode_site` terstruktur dan `electrode_site_note` untuk pilihan lokasi lain.
- SampleBatch, MeasurementSummary, EventMarker, OperatorNote
- SyncReceipt, ExportJob, AuditEvent

## Milestone

### B0 — Foundation

- Package Python, config environment, health/readiness endpoints.
- API versioning dan structured error response.
- Compile/test check lokal.

### B1 — Identity and metadata

- Authentication dan role enforcement.
- Subjects/consent, devices/capabilities, protocols.
- PostgreSQL migrations dan audit events.

### B2 — Session ingest

- Create/finalize examination session.
- Validasi posisi elektroda sebagai metadata wajib untuk setiap stage EMG; metadata stage tidak dapat diubah setelah sampel pertama diterima.
- Idempotent batch ingest dengan `message_id` + checksum unique constraint.
- Receipt/ACK hanya setelah commit.
- Batas ukuran payload dan backpressure.

### B3 — Live monitoring

- WebSocket per session dengan authorization.
- Server meneruskan sample ringkas; raw persistence tidak bergantung pada subscriber.
- Reconnect menggunakan sequence/cursor, bukan asumsi koneksi kontinu.

### B4 — Query and export

- Pagination/filter session.
- Summary dan downsampled chart endpoints.
- Background export CSV; XLSX opsional.
- Retention dan backup tervalidasi.

## API minimum

```text
GET  /health
GET  /ready
POST /api/v1/auth/login
GET  /api/v1/subjects
POST /api/v1/subjects
GET  /api/v1/devices
POST /api/v1/device/pairings
GET  /api/v1/device/pairings/{token}
POST /api/v1/devices/claim
POST /api/v1/sessions
POST /api/v1/sessions/{id}/batches
POST /api/v1/sessions/{id}/finalize
GET  /api/v1/sessions/{id}
GET  /api/v1/sessions/{id}/stream
POST /api/v1/exports
```

## Data integrity and security

- Unique ingest key: `(device_id, message_id)`.
- UTC timestamps plus receive timestamp.
- PHI/identitas langsung tidak masuk raw sensor payload.
- Secret dibuat per perangkat dan hanya hash-nya yang disimpan backend; environment key hanya jalur kompatibilitas legacy.
- Credential menentukan `device_id`; nilai identitas pada query/payload tidak boleh mengganti identitas credential.
- Maksimum satu sesi aktif per perangkat.
- Audit log append-only untuk consent, calibration, export, dan perubahan akses.
- Motor tidak pernah dikendalikan oleh endpoint cloud pada MVP.

## Definition of done

- Unit/integration check untuk ingest retry, authorization, dan transaction rollback.
- OpenAPI menjadi kontrak frontend.
- Migration dapat naik/turun pada database test.
- Backup/restore sample dataset terbukti.
