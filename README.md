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

## Simulator perangkat Python

Simulator ESP tersedia di `tools/device_simulator.py` dan hanya memakai Python standard library. Buat sesi dari dashboard, tandai sebagai `active`, lalu jalankan:

```powershell
$env:TONGUE_SMART_DEVICE_API_KEY = "<lihat credentials.md lokal>"
python tools/device_simulator.py --duration 30 --sample-rate 10
```

Simulator mencari sesi aktif, menghasilkan data sesuai modul sesi, mengirim batch ber-checksum, melanjutkan sequence terakhir, dan melakukan retry eksponensial. Gunakan `--help` untuk opsi session, batch, seed, base URL, dan dry-run.

FastAPI foundation untuk sinkronisasi dan data riset. Pengukuran tidak bergantung pada backend.

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m uvicorn tongue_smart.main:app --reload
```

Rencana implementasi: [PLAN.md](PLAN.md).
