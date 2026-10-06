# Backend Dashboard Multi WhatsApp (Django)

Backend untuk dashboard multi-outlet: menerima pesan dari **WhatsApp Cloud API** (webhook), menyimpan percakapan,
dan mengirim balasan atau template lewat satu System User token untuk semua nomor outlet.

**Stack:** Django 5.2+, Django REST Framework (token auth), PostgreSQL, Gunicorn + WhiteNoise, Docker.

## Menjalankan
```bash
# Lokal (SQLite, tanpa Docker)
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # isi nilainya
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver      # http://localhost:8000/admin/

# Docker (PostgreSQL)
cp .env.example .env
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
```

## Penyiapan awal
1. Buka `/admin/`, tambahkan **Outlet** satu per satu. Isi `phone_number_id` dari Meta (WhatsApp Manager / API Setup).
2. Buat user untuk agen, lalu centang outlet yang boleh mereka akses (kolom *Members* di outlet). User dengan status
   *staff* melihat semua outlet.
3. Isi `.env`: `WA_TOKEN` (token System User), `WA_APP_SECRET` (App Settings > Basic), `WA_VERIFY_TOKEN` (string bebas),
   `WA_WABA_ID` (untuk sinkron template).
4. Di Meta App > WhatsApp > Configuration, isi **Callback URL** `https://DOMAIN-ANDA/webhook/` dan Verify Token yang sama
   dengan `WA_VERIFY_TOKEN`, lalu berlangganan field **messages**. URL harus HTTPS dan publik
   (saat pengembangan pakai ngrok atau Cloudflare Tunnel).
5. Sinkronkan template: `python manage.py sync_templates` atau `POST /api/templates/sync/` (khusus staf).

## Endpoint
Semua `/api/*` memakai header `Authorization: Token <token>` kecuali login.

| Method | Path | Fungsi |
|---|---|---|
| POST | `/api/auth/login/` | `{username, password}` menghasilkan `{token}` |
| GET | `/api/me/`, `/api/stats/` | profil dan angka dashboard (24 jam) |
| GET | `/api/outlets/` | outlet yang boleh dilihat + jumlah belum dibaca |
| GET | `/api/conversations/` | filter: `outlet`, `status`, `unread=1`, `replied=1`, `assigned=me`, `q`, `updated_after` |
| POST | `/api/conversations/` | mulai percakapan `{outlet, wa_id, name}` |
| PATCH | `/api/conversations/{id}/` | ubah `assigned_to` atau `status` |
| GET | `/api/conversations/{id}/messages/` | riwayat pesan (sekaligus menandai terbaca) |
| POST | `/api/conversations/{id}/messages/` | kirim `{text}` (dalam 24 jam) atau `{template: id}` |
| POST | `/api/conversations/{id}/read/` | tandai terbaca |
| GET | `/api/messages/` | semua pesan; filter `direction`, `template=0/1`, `q` (halaman Pesan Terkirim) |
| GET/POST/DELETE | `/api/contacts/` | kontak (hapus khusus staf; ditolak bila punya percakapan) |
| GET | `/api/templates/` | template pesan |
| POST/GET | `/webhook/` | endpoint Meta (verifikasi dan penerimaan event) |

Aturan yang ditegakkan server: teks bebas hanya dalam jendela 24 jam sejak pesan terakhir pelanggan (selain itu `409`),
template hanya yang berstatus `approved`, dan agen hanya melihat outlet miliknya.

## Menyambungkan frontend Next.js
- Tambahkan `NEXT_PUBLIC_API_URL=http://localhost:8000` di frontend dan pastikan origin frontend ada di `CORS_ALLOWED_ORIGINS`.
- Login, simpan token, lalu kirim di header `Authorization`.
- Realtime sederhana: polling `GET /api/conversations/?updated_after=<waktu terakhir>` tiap beberapa detik.
  Perubahan status pesan (delivered/read) juga menaikkan `updated_at`.

## Keamanan
- Setiap POST webhook diverifikasi dengan `X-Hub-Signature-256`. Tanpa `WA_APP_SECRET`, semua permintaan ditolak.
- Jangan commit `.env`. Token dan App Secret hanya ada di server.
- Produksi: `DEBUG=0`, `SECRET_KEY` acak, `ALLOWED_HOSTS` diisi domain, pasang HTTPS (nginx/Caddy), `BEHIND_PROXY=1`.

## Tes
```bash
python manage.py test      # 12 tes: webhook, tanda tangan, idempotensi, aturan 24 jam, akses per outlet
```

## Belum ada (langkah berikutnya)
- Unduh media (gambar/dokumen) lewat `media_id`; saat ini hanya disimpan sebagai `[image]` dan `media_id`.
- Antrean (Celery/Redis) untuk memproses webhook volume tinggi; saat ini diproses langsung di request.
- Pembuatan template baru ke Meta lewat API (sekarang dibuat di WhatsApp Manager lalu disinkronkan).
- Catatan internal, WebSocket/SSE, pagination, dan pelacakan biaya pesan per outlet.
