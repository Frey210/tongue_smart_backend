# Tongue Smart Backend

## HTTP ingest perangkat

Firmware mengirim batch hanya untuk sesi berstatus `active`:

```text
POST /api/v1/sessions/{session_id}/batches
X-Device-ID: <registered device id>
X-Device-Key: <device secret>
Content-Type: application/json
```

`checksum` adalah SHA-256 lowercase dari array `samples` dalam JSON canonical (`sort_keys=true`, separator tanpa spasi). Kombinasi `device_id + message_id` dan `session_id + sequence` unik. ACK/receipt baru dikirim setelah transaksi database berhasil. Maksimum 500 sampel per batch.

Perangkat hasil pairing memiliki secret unik. Backend hanya menyimpan SHA-256 secret tersebut. `TONGUE_SMART_DEVICE_API_KEY` tetap didukung sementara untuk perangkat legacy `tongue-smart-v3`; jangan hard-code secret ke repository.

## Pairing dan kepemilikan perangkat

Device membuat secret lokal dan meminta pairing code melalui `POST /api/v1/device/pairings`. Admin/operator memasukkan kode itu pada halaman **Perangkat**, sehingga device terikat ke akun pendaftar. Kode kedaluwarsa setelah 10 menit. Sesudah diklaim, kombinasi `X-Device-ID` dan `X-Device-Key` menjadi identitas perangkat untuk setiap poll dan batch.

## Simulator perangkat Python

Simulator ESP tersedia di `tools/device_simulator.py` dan hanya memakai Python standard library. Buat sesi dari dashboard, tandai sebagai `active`, lalu jalankan:

Untuk mendaftarkan simulator baru:

```powershell
python tools/device_simulator.py --pair --device-id TS-SIM-001
```

Masukkan pairing code yang tampil ke Dashboard. Setelah diklaim, simulator mencetak `--device-key` unik yang perlu disimpan secara lokal. Untuk menjalankannya kembali:

```powershell
python tools/device_simulator.py --device-id TS-SIM-001 --device-key "<secret unik>" --duration 30 --sample-rate 10
```

Simulator mencari sesi aktif lalu menunggu kontrol tahap dari tab Monitoring. Hanya modul/titik yang sedang berstatus `baseline` atau `recording` yang disimulasikan. Program mengirim batch ber-checksum, melanjutkan sequence terakhir, dan melakukan retry eksponensial. Gunakan `--help` untuk opsi session, batch, seed, base URL, dan dry-run.

FastAPI foundation untuk sinkronisasi dan data riset. Pengukuran tidak bergantung pada backend.

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m uvicorn tongue_smart.main:app --reload
```

Rencana implementasi: [PLAN.md](PLAN.md).
