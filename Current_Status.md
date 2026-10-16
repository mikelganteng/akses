# PopPay Hub — Current Status

Updated: 2026-10-14

## Hub v1.1.1 — Windows toast, tab boleh ditutup

- Watcher di `hub.py` poll FAIL 20s. FAIL baru → balloon Windows. Browser tab tidak wajib.
- `Hub/start.bat` double-click. Jangan close window Python.

## Prior: Hub v1.1.0 — FAIL notif realtime (poll)

- `Hub/start.bat` double-click → buka `http://127.0.0.1:3999/`
- Poll status 15s, FAIL 20s. FAIL baru → beep + toast + browser Notification (klik **Notif** → Allow). Tab harus tetap terbuka.
- Sidebar FAIL ke atas. Retry dari table bawah. Iframe = UI asli brand.
- `index.html` tetap tanpa Python, tapi **tidak** realtime / tidak notif (CORS).

## Hub HTML — tanpa Python

- File: `Hub/index.html` — double-click / buka di Chrome.
- Sidebar 49 brand, kanan iframe UI transaksi asli (retry/BO Check tetap di engine).
- Kalau iframe diblok, pakai **Open tab baru**.

## Hub v1.0.0 Python (opsional)

- Status live + Scan FAIL + retry dari satu table: `python Hub/hub.py` port 3999.

- File: `Hub/hub.py` + `Hub/brands.json`
- Port: **3999** → `http://127.0.0.1:3999/`
- Grid 49 brand, status di-proxy dari `/status` masing-masing (cache 8s).
- Klik kartu → table `/refids` brand itu. Retry / Force / Del di-proxy.
- **Scan FAIL semua** fan-out `/refids?status=FAIL`.
- Engine tidak diubah. Hub tidak insert deposit / tidak sentuh BO.
- Edit daftar brand: `Hub/brands.json`. Restart hub.

## Run

```
cd Hub
pip install -r requirements.txt
python hub.py
```

Atau double-click `start.bat` (auto `pip install -r requirements.txt`).
