# PRD — Opsifin Bot: GoodDay Interaktif via Telegram

Status: **DISETUJUI KONSEP — belum diimplementasi**
Tanggal: 2026-07-06 · Owner: Aditya Prasetyo · Draft oleh: Claude

---

## 1. Latar belakang

`gd-reminder` saat ini read-only: reminder pagi personal (`run`), digest tim ke
topic group (`digest`), dan bot pendaftaran berbasis picker nama. Rombakan ini
mengubahnya jadi **bot dua arah**: user mengelola task GoodDay-nya langsung dari
Telegram, dengan batasan ketat hanya bisa melihat & mengubah **task miliknya
sendiri**.

## 2. Goals

1. User daftar mandiri pakai **ID GoodDay miliknya** + **approval admin**;
   setelah terdaftar hanya bisa akses data GD dirinya sendiri.
2. User terdaftar bisa:
   1. Lihat task ready hari ini + lewat deadline (format view sama seperti sekarang)
   2. Update status task
   3. Isi/ubah custom field (whitelist)
   4. Kirim comment/message ke task
   5. Report time
   6. Lihat direktori user GD (`/gdusers` — nama+id saja, bukan task orang)
3. View task ditambah **tombol aksi** per task (konsep di §6).
4. **AI manipulation di balik layar** — opsional, hanya admin yang bisa set;
   kalau off/gagal → fallback perilaku default. (Rangka dulu, panggilan API menyusul.)
5. **Label kesiapan task**: 🟢 SIAP DIKERJAKAN hanya jika SEMUA terpenuhi:
   1. Story Points terisi (board Agile)
   2. Start date & end date terisi
   3. Product Stream = `Opsifin`
   4. Custom field Delivery Task tercentang
   Kalau tidak → 🚧 BELUM SIAP + daftar alasan yang kurang.

## 3. Non-goals

- Tidak memecah arsitektur jadi package (tetap 1 file `goodday_reminder.py`).
- Tidak mengubah alur cron `run` & `digest` yang sudah jalan.
- Implementasi panggilan AI (Claude API) belum di fase ini — hanya scaffold.

## 4. Fakta teknis (SUDAH DIVERIFIKASI via probing API 2026-07-06)

Endpoint GoodDay (base `https://api.goodday.work/2.0`, header `gd-api-token`):

| Aksi | Endpoint | Body |
|---|---|---|
| Update status | `POST /task/{id}/status` | `{userId, statusId, message?}` |
| Comment | `POST /task/{id}/comment` | `{userId, message}` |
| Custom field | `PUT /task/{id}/custom-fields` | `{customFields: [{id, value}]}` |
| Report time | `POST /task/{id}/time-report` | `{userId, reportedMinutes, date?, message?}` |
| Update field task | `PUT /task/{id}/update` | `{userId, storyPoints, startDate, endDate, ...}` |
| Daftar status org | `GET /statuses` | — (id, name, systemStatus) |
| Detail task | `GET /task/{id}` | — |

Custom field workspace ini:

- **Product Stream** = id `gLu7pt`, tipe 7 (dropdown). List item `8` = **"Opsifin"**.
- **Delivery Task** = id `ZJVsCT`, tipe 23 (checkbox).
- `customFieldsData` di task = map `fieldId -> nilai numerik` (dropdown pakai id item).

⚠️ **Risiko storyPoints**: field `storyPoints` tidak muncul di JSON
`assigned-tasks` maupun `GET /task/{id}` saat belum di-set. Ready-check baca
`t.get("storyPoints")` dari `GET /task/{id}` (fetch detail hanya utk task
hari-ini+telat, di-cache per run). Kalau API tidak pernah mengembalikannya,
fallback ke `estimate` via config `READY_STORYPOINT_SOURCE`.

## 5. Keputusan produk (sudah dikonfirmasi user)

| Keputusan | Pilihan |
|---|---|
| Definisi "Story point" | **Story Points board Agile** (bukan estimate/Effort) |
| Registrasi | **Approval admin** (notif DM + tombol ✅/❌) |
| AI layer | **Rangka saja dulu** (key belum ada) |
| Custom field editable | **Whitelist** via .env (default: Product Stream, Delivery Task) |

## 6. Desain fitur

### 6.1 Registrasi mandiri + approval

- `/daftar` (DM) → bot minta user ketik **ID GoodDay-nya** (petunjuk `/gdusers`).
- Validasi ke `gd_list_users` → simpan `gd_user_id`, `approved=0` → DM semua
  `ADMIN_CHAT_IDS`: "X mendaftar sebagai <nama GD>" + tombol `apr:<chat_id>` / `rej:<chat_id>`.
- Approve → `approved=1` + user dikirimi `/tasks`-nya. Reject → reset baris.
- **Picker nama lama DIHAPUS** (melanggar privasi antar user).
- Guard `require_self()`: semua view/update hanya pakai gd_user_id pengirim;
  user `approved=0` ditolak semua fitur task; tombol 📄 Detail digest hanya bisa
  dibuka pemiliknya (orang lain dapat alert).

### 6.2 Label kesiapan (goal 5)

- `task_ready(detail) -> (bool, [alasan])`, 4 syarat di §2.5.
- Tampil sebagai prefix 🟢/🚧 di section 🔥 HARI INI & ⚠️ TELAT (personal + digest);
  di detail ditulis alasannya: `🚧 BELUM SIAP — story point kosong, Delivery Task belum dicentang`.

### 6.3 Update task (goal 2&3)

- `/tasks`: format sama + tombol `✏️ #<shortId>` utk task hari-ini+telat (≤8)
  + `✏️ Task lain…` (ketik shortId, dicari HANYA di assigned-tasks miliknya).
- `✏️` → menu aksi (callback `act:<taskId>:<aksi>`):
  - 🔄 **Status** → tombol dari `GET /statuses` (cache 10 mnt) → `st:<taskId>:<statusId>`
  - 💬 **Comment** → pending input teks → POST comment
  - 🧾 **Custom field** → tombol per field whitelist; dropdown → tombol listItems,
    checkbox → ✓/✗, teks/angka/tanggal → pending input
  - ⏱ **Report time** → pending input `90m` / `1h30m` / `1.5h [catatan]` → menit
- `/batal` batalkan pending action. Semua callback verifikasi kepemilikan task.
- Fungsi aksi dipisah dari handler tombol → AI layer nanti panggil fungsi yang sama.

### 6.4 AI scaffold (goal 4)

- `settings.ai_enabled` (default off); `/ai on|off|status` khusus `ADMIN_CHAT_IDS`.
- Teks bebas (bukan command, tanpa pending action) → `ai_interpret(cfg, user_ctx, text)`:
  ai_enabled & `ANTHROPIC_API_KEY` terisi → (fase ini: return None) → fallback default.
- **Saat implementasi AI nanti: WAJIB baca skill `claude-api` dulu.**

## 7. Perubahan data & config

DB (migrasi idempotent di `init_db`):
- `recipients` + kolom `approved INTEGER NOT NULL DEFAULT 1` (baris lama = approved).
- Tabel `settings(key TEXT PRIMARY KEY, value TEXT)`.
- Tabel `pending_actions(chat_id TEXT PRIMARY KEY, action TEXT, created_at TEXT)`
  — kadaluarsa 10 menit.

`.env` baru:
```
ADMIN_CHAT_IDS=123456789
READY_STREAM_FIELD=gLu7pt
READY_STREAM_VALUE=8
READY_DELIVERY_FIELD=ZJVsCT
READY_STORYPOINT_SOURCE=storyPoints   # atau "estimate"
EDIT_CUSTOM_FIELDS=gLu7pt,ZJVsCT
ANTHROPIC_API_KEY=
```

## 8. Acceptance criteria

1. User baru bisa daftar via ID GD sendiri; fitur task terkunci sampai admin approve.
2. User TIDAK bisa melihat task/detail user lain dari jalur mana pun (DM, group, tombol).
3. Dari `/tasks`, user bisa: ganti status, comment, isi Product Stream/Delivery Task,
   report time — dan perubahan terverifikasi muncul di GoodDay UI.
4. Task hari-ini/telat berlabel 🟢/🚧 dengan alasan yang akurat sesuai 4 syarat.
5. `/ai on|off` hanya bisa admin; teks bebas saat off berperilaku persis seperti sekarang.
6. Alur lama tetap jalan: cron `run`, `digest` (+ `/digest` di topic), `/help`, `/gdusers`.
7. `try_local.py` lulus offline; `run --dry-run` & `digest --dry-run` benar.

## 9. Verifikasi (rencana uji)

1. Syntax + `python3 try_local.py`.
2. `run --dry-run` & `digest --dry-run` — cek label & format.
3. Live: `/daftar` (ID sendiri) → approval → `/tasks` → uji update NYATA di 1 task
   milik Aditya (status bolak-balik, comment tes, Delivery Task, time 5m) →
   verifikasi di GoodDay UI → bersihkan jejak tes.
4. Uji guard: buka detail orang lain (harus ditolak), user belum approved (ditolak).
