# AGENTS.md — aturan kerja di repo gd-reminder

Baca dulu (kalau ada — catatan lokal, tidak di-commit): `HANDOFF.md` (kondisi
terkini) → `TODO.md` (urutan kerja). Lalu `PRD.md` (spesifikasi rombakan
Opsifin Bot).

## Aturan proyek

1. **Arsitektur satu file**: semua logika di `goodday_reminder.py`. Jangan pecah
   jadi package tanpa persetujuan user. Pisahkan section pakai banner komentar.
2. **Bahasa**: komentar/pesan bot bahasa Indonesia kasual (gaya "ga", "udah",
   "biar") — ikuti gaya yang sudah ada. Identifier tetap Inggris.
3. **Kredensial**: `.env` berisi token admin GoodDay + bot Telegram. JANGAN pernah
   print token utuh ke output/log; kalau perlu verifikasi, tampilkan panjang +
   prefix saja. Jangan commit `.env` / `reminders.db`.
4. **Satu konsumen getUpdates**: hanya boleh 1 proses `bot` per token. Setelah
   mengedit handler bot, restart: `pkill -f "goodday_reminder.py bot"` lalu
   `python3 -u goodday_reminder.py bot` (background).
5. **DB produksi berisi data** — semua perubahan skema harus migrasi idempotent.
6. **Selesai edit** selalu jalankan: syntax check (`python3 -c "import ast; ..."`)
   dan `python3 try_local.py` (demo offline harus tetap lulus; patch fungsi network
   baru di try_local bila perlu).
7. **Update handoff**: sebelum mengakhiri sesi, perbarui `HANDOFF.md` + `TODO.md`.
8. Bot produksi dipakai juga sebagai notifier GitHub milik user (kirim saja).
   Jangan hapus webhook bot lain / ubah setMyCommands tanpa alasan.
