# Tongue Smart Backend

## HTTP ingest perangkat

Firmware mengirim batch hanya untuk sesi berstatus `active`:

```text
POST /api/v1/sessions/{session_id}/batches
X-Device-Key: <device secret>
Content-Type: application/json
```

`checksum` adalah SHA-256 lowercase dari array `samples` dalam JSON canonical (`sort_keys=true`, separator tanpa spasi). Kombinasi `device_id + message_id` dan `session_id + sequence` unik. ACK/receipt baru dikirim setelah transaksi database berhasil. Maksimum 500 sampel per batch.

Secret perangkat dibaca dari `TONGUE_SMART_DEVICE_API_KEY`; jangan hard-code secret ke repository.

FastAPI foundation untuk sinkronisasi dan data riset. Pengukuran tidak bergantung pada backend.

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m uvicorn tongue_smart.main:app --reload
```

Rencana implementasi: [PLAN.md](PLAN.md).
