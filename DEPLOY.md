# Runbook Deploy ke Server Linux

Target: Ubuntu/Debian dengan systemd. Semua perintah tinggal copy-paste,
urut dari atas. Ganti nilai yang ditandai `<...>`.

Sumber kode: repo GitHub — server tinggal `git clone`, update tinggal
`git pull`. `.env` dan `reminders.db` TIDAK ada di repo (di-gitignore),
jadi aman dari tertimpa saat update.

---

## 1. Setup dasar di server

```bash
ssh <user>@<host>

# prasyarat
python3 --version          # butuh 3.9+
sudo apt update && sudo apt install -y git python3-requests   # atau: pip3 install requests

# clone aplikasi
sudo mkdir -p /opt/gd-reminder && sudo chown $USER /opt/gd-reminder
git clone https://github.com/Adityapras/goodday-reminder.git /opt/gd-reminder
cd /opt/gd-reminder
```

## 2. Konfigurasi

```bash
cp .env.example .env && chmod 600 .env
nano .env
```

Isi minimal (nilai token JANGAN di-copy-paste lewat chat/log — ketik/pindahkan
lewat jalur aman; hati-hati token kepotong, riwayatnya sudah 2x kejadian):

```ini
GOODDAY_API_TOKEN=...
TELEGRAM_BOT_TOKEN=...
ADMIN_CHAT_IDS=<chat_id_telegram_admin>
DIGEST_CHAT_ID=<chat_id_group>          # group besar diawali -100
DIGEST_TOPIC_ID=<topic_id>              # kosongkan kalau tanpa Topics
DIGEST_GD_USERS=<id1>,<id2>,<id3>       # id GoodDay, lihat: gd-users
READY_STREAM_FIELD=gLu7pt
READY_STREAM_VALUE=8
READY_DELIVERY_FIELD=ZJVsCT
READY_STORYPOINT_SOURCE=estimate
EDIT_CUSTOM_FIELDS=gLu7pt,ZJVsCT
```

Lalu:

```bash
python3 goodday_reminder.py init-db && chmod 600 reminders.db
python3 goodday_reminder.py gd-users        # tes token GoodDay (harus muncul daftar user)
python3 goodday_reminder.py run --dry-run   # tes format reminder (belum kirim)
```

## 3. ⚠️ CUTOVER — matikan bot di mesin lama DULU

getUpdates cuma boleh 1 konsumen. Sebelum menyalakan bot di server,
di mesin lama (Mac):

```bash
pkill -f "goodday_reminder.py bot"
```

## 4. Pasang bot sebagai service systemd

```bash
sudo cp goodday-bot.service.example /etc/systemd/system/goodday-bot.service
sudo nano /etc/systemd/system/goodday-bot.service   # GANTI User= dan path kalau bukan /opt/gd-reminder
sudo systemctl daemon-reload
sudo systemctl enable --now goodday-bot

# verifikasi
systemctl status goodday-bot          # harus: active (running)
journalctl -u goodday-bot -f          # harus muncul: "Bot @<username_bot> jalan"
```

Tes dari HP: kirim `/status` ke bot — harus dibalas.

## 5. Pasang cron (reminder pagi + digest)

```bash
timedatectl        # CEK TIMEZONE! jam cron ikut server
crontab -e
```

Kalau server **WIB**:

```cron
0 7 * * *   cd /opt/gd-reminder && /usr/bin/python3 goodday_reminder.py run    >> reminder.log 2>&1
5 7 * * 1-5 cd /opt/gd-reminder && /usr/bin/python3 goodday_reminder.py digest >> reminder.log 2>&1
```

Kalau server **UTC** (07:00 & 07:05 WIB = 00:00 & 00:05 UTC):

```cron
0 0 * * *   cd /opt/gd-reminder && /usr/bin/python3 goodday_reminder.py run    >> reminder.log 2>&1
5 0 * * 0-4 cd /opt/gd-reminder && /usr/bin/python3 goodday_reminder.py digest >> reminder.log 2>&1
```

> Catatan UTC: Senin–Jumat WIB pagi = Minggu–Kamis malam UTC, makanya `0-4`.

Tes kirim beneran sekali (opsional):

```bash
cd /opt/gd-reminder && python3 goodday_reminder.py digest   # cek muncul di topic group
```

## 6. Onboarding tim (DB kosong)

1. Kamu (admin) `/start` + `/daftar` → ketik ID GoodDay kamu → auto-nunggu
   approval dari... kamu sendiri — approve via tombol di DM.
2. Minta anggota tim lain `/daftar` (ID masing-masing lihat `gd-users`) →
   approve dari DM kamu.

## 7. Verifikasi akhir

- [ ] `systemctl status goodday-bot` → active (running)
- [ ] `/status`, `/tasks`, `/cari` dibalas dari HP
- [ ] Bot di mesin lama sudah MATI (`pgrep -f goodday_reminder` = kosong)
- [ ] Besok pagi: reminder DM + digest masuk ke topic (cek `reminder.log` kalau tidak)

## Update versi berikutnya

```bash
cd /opt/gd-reminder
git pull                        # .env & reminders.db aman (di-gitignore)
sudo systemctl restart goodday-bot
```

## Rollback / troubleshooting cepat

- Bot error terus: `journalctl -u goodday-bot -n 50`; cek token (`gd-users`).
- 409 Conflict: masih ada bot lain jalan (mesin lama?) — matikan salah satu.
- Balik ke mesin lama sementara: `sudo systemctl stop goodday-bot` di server,
  lalu jalankan `python3 -u goodday_reminder.py bot` di sana.
- Balik ke versi sebelumnya: `git log --oneline` → `git checkout <commit>`
  → restart service (kembali normal: `git checkout main && git pull`).
