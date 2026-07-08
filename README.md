# Opsifin Bot — GoodDay ↔ Telegram

Bot dua arah GoodDay untuk tim Opsifin:

- **Reminder pagi** ke DM tiap anggota tim (task yang mulai/deadline hari ini),
  otomatis via cron.
- **Digest tim** ke group/topic Telegram (ringkasan hari ini + telat, semua orang).
- **Update task langsung dari Telegram** — ganti status, kirim comment, ubah
  custom field, report time — lewat tombol ✏️ di `/tasks`.
- **Label kesiapan** 🟢 SIAP / 🚧 BELUM SIAP di tiap task urgent, lengkap dengan
  alasannya.
- **Privasi ketat**: tiap orang hanya bisa lihat & ubah task miliknya sendiri;
  pendaftaran pakai ID GoodDay sendiri + approval admin.

Cukup 1 script Python + 1 file SQLite. **Tanpa web server, tanpa port kebuka,
tanpa publish apa pun** — semua koneksi keluar (outbound HTTPS).

---

## Isi paket

| File | Fungsi |
| ------ | -------- |
| `goodday_reminder.py` | Script utama (bot, cron `run`/`digest`, tooling admin) |
| `try_local.py` | Demo offline — coba tanpa token/tanpa internet |
| `.env.example` | Template konfigurasi (copy jadi `.env`) |
| `requirements.txt` | Dependency (`requests`) |
| `crontab.example` | Contoh baris cron |
| `goodday-bot.service.example` | Contoh unit systemd untuk mode `bot` di server |
| `PRD.md` / `TODO.md` / `HANDOFF.md` | Dokumen desain & catatan pengembangan |

---

## Prasyarat

- **Python 3.9+** (butuh modul `zoneinfo` bawaan). Cek: `python3 --version`
- **pip**
- Token admin GoodDay (Settings → Integrations → API)
- Bot Telegram + token dari [@BotFather](https://t.me/BotFather)

---

## 1. Install

```bash
pip install -r requirements.txt
```

---

## 2. Coba OFFLINE dulu (tanpa token — aman)

```bash
python try_local.py
```

Ini pakai data palsu dan cuma nge-print ke layar (ga ada koneksi keluar).
Demo-nya menjalankan alur lengkap: daftar mandiri by ID → approval admin →
reminder dengan label 🟢/🚧. Kalau formatnya udah oke, lanjut ke setup asli.

---

## 3. Konfigurasi

```bash
cp .env.example .env && chmod 600 .env
```

Variabel penting di `.env`:

```ini
# wajib
GOODDAY_API_TOKEN=token_admin_kamu
TELEGRAM_BOT_TOKEN=token_bot_dari_botfather

# approval pendaftaran (chat_id Telegram admin, pisah koma)
# ⚠️ kosong = pendaftaran auto-approve tanpa admin!
ADMIN_CHAT_IDS=123456789

# digest tim ke group/topic (opsional)
DIGEST_CHAT_ID=-100xxxxxxxxxx
DIGEST_TOPIC_ID=12345
DIGEST_GD_USERS=id1,id2,id3

# syarat label 🟢 SIAP (default sudah sesuai workspace Opsifin)
READY_STREAM_FIELD=gLu7pt
READY_STREAM_VALUE=8
READY_DELIVERY_FIELD=ZJVsCT
READY_NEED_SP_TAG=PU7NBR   # tag 'need-story-point' (cek SP via GET /tag/{id}/tasks)

# custom field yang boleh diubah dari Telegram (whitelist)
EDIT_CUSTOM_FIELDS=gLu7pt,ZJVsCT

# AI layer (opsional, masih scaffold; provider-agnostic)
AI_PROVIDER=
AI_API_KEY=
AI_MODEL=
AI_BASE_URL=
```

> ⚠️ **Jangan commit `.env` ke git** (sudah di-`.gitignore`). Isinya kredensial.

---

## 4. Tes koneksi & bikin database

```bash
python goodday_reminder.py init-db      # bikin reminders.db (migrasi otomatis)
python goodday_reminder.py gd-users     # <- nge-tes token GoodDay
```

Kalau `gd-users` nampilin daftar user tim + `GOODDAY USER ID`-nya, token valid.

---

## 5. Jalankan bot & daftarin penerima

```bash
python -u goodday_reminder.py bot
```

Alur pendaftaran (mandiri + approval admin):

1. Anggota tim kirim `/start` lalu `/daftar` ke bot (DM).
2. Bot minta dia **ketik ID GoodDay-nya sendiri** (lihat daftar via `/gdusers`).
3. Admin (semua `ADMIN_CHAT_IDS`) dapat DM berisi siapa mendaftar sebagai siapa,
   dengan tombol **✅ Approve / ❌ Tolak**.
4. Di-approve → user aktif & langsung dikirimi task-nya. Ditolak → di-reset,
   boleh daftar ulang.

Catatan keamanan pendaftaran:

- Satu ID GoodDay hanya bisa ter-link ke satu akun Telegram.
- Sebelum di-approve, semua fitur task terkunci.
- Ganti ID = daftar ulang = butuh approval lagi.

Bot harus tetap jalan supaya tombol & perintah responsif — jalankan di
`tmux`/`screen` atau systemd (lihat bagian Deploy). Reminder pagi tetap urusan
cron (`run`), terpisah dari bot.

> ⚠️ getUpdates cuma boleh 1 konsumen: jangan jalankan `bot` dobel
> (laptop vs server), dan `register` jangan barengan `bot`.

---

## 6. Fitur bot

### Perintah (DM)

| Perintah | Fungsi |
| ---------- | -------- |
| `/daftar` | Daftar/ganti ID GoodDay (approval admin) |
| `/tasks` | Task aktif kamu + tombol ✏️ untuk update |
| `/digest` | Ringkasan tim terbaru |
| `/gdusers` | Daftar user GoodDay + id |
| `/status` | Status pendaftaran kamu |
| `/batal` | Batalkan input yang sedang ditunggu |
| `/berhenti` | Stop reminder pagi (`/start` untuk aktif lagi) |
| `/terdaftar` | (khusus admin) daftar penerima yang sudah terdaftar di bot |
| `/ai on\|off\|status` | (khusus admin) kontrol AI layer |

Admin juga bisa tanya bahasa bebas ke AI (kalau AI aktif), mis. "siapa saja
yang sudah daftar?" — AI memanggil tool `list_recipients` (admin-only; non-admin
otomatis ditolak, data penerima tidak dibocorkan).

Di group/topic: `/digest`, `/tasks`, `/status`, `/gdusers`, `/help` — bot
membalas di topic yang sama. `/daftar` & `/berhenti` khusus DM.

### Update task via tombol ✏️ (DM)

`/tasks` menampilkan tombol `✏️ #<shortId>` untuk task hari-ini + telat
(maks. 8) plus `✏️ Task lain…` (ketik shortId task kamu yang lain). Aksinya:

- 🔄 **Status** — pilih status baru dari daftar status organisasi
- 💬 **Comment** — ketik teks, terkirim sebagai comment di task
- 🧾 **Custom field** — hanya field whitelist (`EDIT_CUSTOM_FIELDS`);
  dropdown & checkbox pakai tombol, field teks diketik
- ⏱ **Report time** — format `90m` / `1h30m` / `1.5h catatan opsional`

Input yang ditunggu (comment/durasi/shortId) kadaluarsa 10 menit; batalkan
kapan pun dengan `/batal`. Semua aksi diverifikasi kepemilikan task-nya.

### Label kesiapan 🟢/🚧

Task di section 🔥 HARI INI, ⚠️ TELAT, dan 📌 AKAN JALAN diberi label:

- **🟢 SIAP DIKERJAKAN** — memenuhi SEMUA: sudah di-story-point (task TIDAK
  lagi bertag `need-story-point`), start & end date terisi, Product Stream =
  Opsifin, Delivery Task dicentang.
- **🚧 BELUM SIAP** — ada yang kurang; alasannya ditulis
  (mis. `🚧 BELUM SIAP — story point kosong, Delivery Task belum dicentang`).

### AI layer (scaffold)

`/ai on|off|status` (admin) menyiapkan mode AI: nantinya teks bebas
("gantiin status task 45567 jadi in progress dong") diterjemahkan LLM ke aksi
yang sama dengan tombol. Provider-agnostic via `.env` (`AI_PROVIDER`:
anthropic/openai/gemini/ollama/custom + `AI_BASE_URL` untuk endpoint
OpenAI-compatible). **Panggilan LLM belum diimplementasi** — saat off/gagal/
belum ada key, bot berperilaku persis seperti biasa.

---

## 7. Tes kirim & pasang cron

```bash
python goodday_reminder.py run --dry-run     # pratinjau reminder personal
python goodday_reminder.py run               # kirim beneran → cek Telegram
python goodday_reminder.py digest --dry-run  # pratinjau digest tim
```

Lalu `crontab -e` (lihat `crontab.example`):

```cron
0 7 * * * cd /path/ke/folder && /usr/bin/python3 goodday_reminder.py run >> reminder.log 2>&1
5 7 * * 1-5 cd /path/ke/folder && /usr/bin/python3 goodday_reminder.py digest >> reminder.log 2>&1
```

> **Timezone:** filter "hari ini" dikunci ke **Asia/Jakarta (WIB)** di dalam
> script, tapi *jam cron* ikut timezone server. Server UTC + mau 07:00 WIB =
> pasang `0 0 * * *`. Cek: `timedatectl` atau `date`.

---

## Referensi perintah CLI

| Perintah | Fungsi |
| ---------- | -------- |
| `init-db` | Bikin/migrasi database SQLite (idempotent) |
| `bot` | Bot interaktif long-polling (daftar, tombol ✏️, approval) |
| `gd-users` | List user GoodDay + `user_id` |
| `register` | Simpan `chat_id` dari yang udah `/start` (cara manual lama) |
| `users` | List penerima di database (termasuk status approval) |
| `link <chat_id> <gd_user_id> [--name X]` | Petakan chat ↔ user GoodDay (manual, tanpa approval) |
| `enable <chat_id>` / `disable <chat_id>` | Nyalain/matiin penerima |
| `run [--dry-run] [--skip-empty]` | Reminder personal ke semua penerima aktif+approved |
| `digest [--dry-run] [--chat-id X] [--topic-id Y] [--no-buttons]` | Ringkasan tim ke group/topic |

---

## Digest tim ke group/topic

`digest` mengirim SATU pesan ringkas berisi status beberapa orang — task
**hari ini** + **lewat deadline** saja, dengan label 🟢/🚧. Tombol
**📄 Detail** per orang hanya bisa dibuka **pemiliknya** (orang lain dapat
alert); butuh mode `bot` sedang jalan.

Setup: invite bot ke group → isi `DIGEST_CHAT_ID` / `DIGEST_TOPIC_ID` /
`DIGEST_GD_USERS` di `.env` → tes `digest --dry-run` → cron-kan.

---

## Deploy ke server Linux

```bash
# di server
sudo mkdir -p /opt/gd-reminder && sudo chown $USER /opt/gd-reminder
# salin isi folder ini ke /opt/gd-reminder (scp/rsync — JANGAN ikutkan .env lama
# kalau server pakai token beda), lalu:
cd /opt/gd-reminder
pip install -r requirements.txt   # atau: apt install python3-requests
cp .env.example .env && nano .env && chmod 600 .env
python3 goodday_reminder.py init-db && chmod 600 reminders.db
```

Dua proses yang perlu jalan:

1. **Bot interaktif** (long-polling, selalu hidup) — systemd:
   `goodday-bot.service.example` (auto-start saat boot, auto-restart).
2. **Cron** untuk `run` (reminder personal) dan `digest` (ringkasan group) —
   `crontab.example`.

> ⚠️ Saat bot sudah jalan di server, **matikan** bot di laptop/mesin lain —
> getUpdates cuma boleh 1 konsumen (kalau dobel: error 409 / rebutan update).

Bawa juga `reminders.db` dari mesin lama kalau mau mempertahankan pendaftaran
(scp file-nya sebelum menyalakan bot di server).

---

## Isi pesan reminder

Reminder cuma menampilkan task yang **relevan sekarang** — task yang jalan hari
ini + yang bakal jalan seminggu ke depan. Task di luar jendela itu sengaja
disembunyikan (bisa dicari via `/cari`). Dikelompokkan:

1. 🔥 **HARUS JALAN HARI INI** — `startDate`/`deadline` == hari ini (WIB) +
   label 🟢/🚧, ⏰ *deadline HARI INI* / 🚀 *mulai HARI INI*.
2. ⚠️ **LEWAT DEADLINE** — telat, maksimal 7 hari ke belakang
   (`OVERDUE_WINDOW_DAYS`), lengkap dengan berapa hari telatnya dan 🟢/🚧.
3. 📌 **AKAN JALAN 7 HARI KE DEPAN** — `startDate` ATAU `deadline` jatuh dalam
   7 hari ke depan (`UPCOMING_WINDOW_DAYS`), urut yang paling dekat (maks. 20).

Tiap task berupa **link yang bisa diklik** ke GoodDay (pakai id task, format
`goodday.work/t/<id>`), plus nomor `#shortId`, project, status, prioritas
(🔴 Emergency / 🟠 Blocker / 🟡 High), dan tanggal mulai → deadline. Pesan
panjang otomatis dipecah biar ga kena limit 4096 karakter Telegram.

---

## Troubleshooting

**`401` saat `gd-users`** → token GoodDay salah/kadaluarsa. Cek `.env`.

**Telegram balas 404/Unauthorized** → token bot rusak/terpotong — cek panjang &
prefix `TELEGRAM_BOT_TOKEN` (riwayat: pernah rusak karena terpotong saat edit
manual).

**Error 409 Conflict di bot** → ada dua proses `bot` jalan barengan (laptop vs
server). Matikan salah satu.

**`run` bilang "ga ada penerima aktif"** → cek `users`; pastikan `STATUS = siap`
(ke-link + enabled + **approved**).

**Pendaftar tidak dapat balasan approval** → cek `ADMIN_CHAT_IDS` terisi dan
admin pernah `/start` ke bot (bot tak bisa DM orang yang belum pernah chat).

**Update status/field gagal (HTTP 4xx)** → id status/field berubah di GoodDay?
Cek `READY_*` / `EDIT_CUSTOM_FIELDS`, dan tes token via `gd-users`.

---

## Keamanan

- `.env` dan `reminders.db` sensitif — `chmod 600`, jangan pernah commit
  (`.gitignore` sudah memblokir).
- Token admin GoodDay bisa akses & UBAH task semua user — makanya semua jalur
  bot diguard `require_self()`: user hanya bisa menyentuh task miliknya.
- Pendaftaran atas nama orang lain dicegah dua lapis: ID yang sudah ter-link
  ditolak otomatis, sisanya disaring approval admin (bandingkan identitas
  Telegram vs nama GoodDay yang diklaim sebelum approve).
- Kalau token bocor: revoke & generate ulang di GoodDay/BotFather.
