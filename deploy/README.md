# Deployment

Clone repository backend dan frontend sebagai direktori bersaudara:

```text
/opt/tongue-smart/
  backend/
  frontend/
```

Jalankan dari repository backend:

```bash
cd /opt/tongue-smart/backend/deploy
cp .env.example .env
docker compose up -d --build
```

Aplikasi tersedia pada port host `8180`. Cloudflare Tunnel diarahkan ke `http://192.168.10.100:8180`. Jangan commit `.env` atau kredensial deployment.

Secara default metadata disimpan pada volume Docker `backend_data`. Untuk PostgreSQL, isi `TONGUE_SMART_DATABASE_URL` pada `.env` setelah database dan user aplikasi tersedia.
