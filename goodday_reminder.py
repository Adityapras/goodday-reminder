#!/usr/bin/env python3
"""
GoodDay Daily Task Reminder (Multi-User) -> Telegram
=====================================================

Satu token admin GoodDay, banyak user. Cron jalanin script ini tiap pagi;
tiap anggota tim dapet ringkasan task-nya sendiri di Telegram masing-masing.

Ga perlu web server, ga perlu port kebuka, ga perlu publish. Cukup script +
1 file SQLite (daftar penerima) + cron.

------------------------------------------------------------------------------
DUA CARA DAFTARIN PENERIMA
------------------------------------------------------------------------------
  A. MANDIRI (disarankan) — jalankan bot interaktif:
       python goodday_reminder.py bot
     Tiap orang /start ke bot -> /daftar -> ketik ID GoodDay-nya sendiri
     (lihat /gdusers) -> admin approve via tombol di DM. Selesai.

  B. MANUAL (admin) — alur lama:
       1. python goodday_reminder.py init-db
       2. Suruh tiap orang kirim /start ke bot.
       3. python goodday_reminder.py register
       4. python goodday_reminder.py gd-users
       5. python goodday_reminder.py link <chat_id> <gd_user_id> [--name "Nama"]
       6. python goodday_reminder.py users

  Tes kirim:
     python goodday_reminder.py run --dry-run   # cek dulu, ga dikirim
     python goodday_reminder.py run             # kirim beneran

  CATATAN: `bot` dan `register` sama-sama pakai getUpdates — jangan jalan
  barengan. Kalau bot udah jalan, `register` ga perlu lagi.

------------------------------------------------------------------------------
CRON (contoh: tiap hari 07:00 WIB)
------------------------------------------------------------------------------
  0 7 * * * cd /path/ke/folder && /usr/bin/python3 goodday_reminder.py run >> reminder.log 2>&1

------------------------------------------------------------------------------
CONFIG (.env di folder yang sama, atau environment variable)
------------------------------------------------------------------------------
  GOODDAY_API_TOKEN   : token admin (Settings > Integrations > API)
  TELEGRAM_BOT_TOKEN  : token bot dari @BotFather
  DB_PATH             : opsional, default 'reminders.db' di folder script

Dependency: cuma `requests`  ->  pip install requests
"""

import os
import re
import sys
import json
import html
import time
import sqlite3
import argparse
from datetime import datetime, timezone, date
from pathlib import Path

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except ImportError:
    print("Butuh Python 3.9+ (modul zoneinfo).", file=sys.stderr)
    sys.exit(1)

try:
    import requests
except ImportError:
    print("Modul 'requests' belum ada. Jalankan: pip install requests", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

GOODDAY_BASE_URL = "https://api.goodday.work/2.0"
# pakai id task string (mis. ehFPUf), BUKAN shortId numerik — /t/<shortId>
# itu 404 (diverifikasi dari URL asli web app 2026-07-07)
GOODDAY_TASK_URL = "https://www.goodday.work/t/{task_id}"
TIMEZONE = "Asia/Jakarta"  # WIB
HTTP_TIMEOUT = 30
POLL_TIMEOUT = 50          # long-polling getUpdates (detik)
TG_MSG_LIMIT = 3900        # limit Telegram 4096; sisain ruang aman
MAX_OTHERS = 20            # maksimal task "lainnya" yang ditampilkan
CACHE_TTL = 600            # cache user/project GoodDay di mode bot (detik)
SCRIPT_DIR = Path(__file__).resolve().parent


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def get_config():
    load_dotenv(SCRIPT_DIR / ".env")
    return {
        "api_token": os.environ.get("GOODDAY_API_TOKEN", "").strip(),
        "bot_token": os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(),
        "db_path": os.environ.get("DB_PATH", str(SCRIPT_DIR / "reminders.db")).strip(),
        # digest tim ke group/topic (opsional)
        "digest_chat_id": os.environ.get("DIGEST_CHAT_ID", "").strip(),
        "digest_topic_id": os.environ.get("DIGEST_TOPIC_ID", "").strip(),
        "digest_gd_users": os.environ.get("DIGEST_GD_USERS", "").strip(),
        # token bot LAIN yang udah ada di group (kosong = pakai TELEGRAM_BOT_TOKEN)
        "digest_bot_token": os.environ.get("DIGEST_BOT_TOKEN", "").strip(),
        # kill-switch digest — fitur DIMATIKAN dulu (keputusan user 2026-07-07);
        # nyalain lagi: set DIGEST_ENABLED=1 di .env, tanpa ubah kode
        "digest_enabled": os.environ.get("DIGEST_ENABLED", "0").strip() == "1",
        # admin yang meng-approve pendaftaran (chat_id Telegram, pisah koma)
        "admin_chat_ids": [x.strip() for x in
                           os.environ.get("ADMIN_CHAT_IDS", "").split(",") if x.strip()],
        # syarat label 🟢 SIAP DIKERJAKAN (lihat PRD §6.2)
        "ready_stream_field": os.environ.get("READY_STREAM_FIELD", "gLu7pt").strip(),
        "ready_stream_value": os.environ.get("READY_STREAM_VALUE", "8").strip(),
        "ready_delivery_field": os.environ.get("READY_DELIVERY_FIELD", "ZJVsCT").strip(),
        # sumber story point buat ready-check. ⚠️ terverifikasi 2026-07-07:
        # API GoodDay TIDAK pernah mengembalikan "storyPoints" (bahkan setelah
        # di-set via PUT /update) -> default & produksi pakai "estimate"
        "ready_storypoint_source": os.environ.get("READY_STORYPOINT_SOURCE",
                                                  "estimate").strip(),
        # custom field yang boleh diubah dari Telegram (whitelist id, pisah koma)
        "edit_custom_fields": [x.strip() for x in
                               os.environ.get("EDIT_CUSTOM_FIELDS", "gLu7pt,ZJVsCT")
                               .split(",") if x.strip()],
        # AI layer (scaffold, PROVIDER-AGNOSTIC — keputusan user 2026-07-07):
        # ganti provider cukup ganti .env, bukan ganti kode.
        # AI_PROVIDER: anthropic | openai | gemini | ollama | custom
        "ai_provider": os.environ.get("AI_PROVIDER", "").strip().lower(),
        # AI_API_KEY generik; ANTHROPIC_API_KEY lama tetap dibaca sbg fallback
        "ai_api_key": (os.environ.get("AI_API_KEY", "").strip()
                       or os.environ.get("ANTHROPIC_API_KEY", "").strip()),
        "ai_model": os.environ.get("AI_MODEL", "").strip(),
        # endpoint custom/self-hosted yang kompatibel OpenAI (ollama dkk.)
        "ai_base_url": os.environ.get("AI_BASE_URL", "").strip(),
    }


# ---------------------------------------------------------------------------
# Database (SQLite)
# ---------------------------------------------------------------------------

def ensure_schema(conn: sqlite3.Connection) -> None:
    """Bikin/migrasi schema. Idempotent & murah — dipanggil tiap connect,
    jadi DB produksi lama otomatis ke-upgrade di jalur mana pun (run/digest/bot)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS recipients (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_chat_id  TEXT    NOT NULL UNIQUE,
            telegram_name     TEXT,
            gd_user_id        TEXT,
            gd_name           TEXT,
            enabled           INTEGER NOT NULL DEFAULT 1,
            created_at        TEXT    NOT NULL
        )
    """)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(recipients)")}
    if "approved" not in cols:
        # baris lama dianggap sudah di-approve (mereka terdaftar sebelum fitur ini)
        conn.execute("ALTER TABLE recipients "
                     "ADD COLUMN approved INTEGER NOT NULL DEFAULT 1")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pending_actions (
            chat_id    TEXT PRIMARY KEY,
            action     TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()


def db_connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")  # aman kalau ada baca-tulis barengan
    ensure_schema(conn)
    return conn


def init_db(db_path: str) -> None:
    db_connect(db_path).close()
    print(f"Database siap: {db_path}")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def upsert_recipient(conn: sqlite3.Connection, chat_id: str, name: str) -> None:
    conn.execute(
        "INSERT INTO recipients (telegram_chat_id, telegram_name, created_at) "
        "VALUES (?, ?, ?) "
        "ON CONFLICT(telegram_chat_id) DO UPDATE SET telegram_name = excluded.telegram_name",
        (chat_id, name, now_iso()),
    )
    conn.commit()


PENDING_TTL = 600  # detik; pending input kadaluarsa 10 menit (PRD §7)


def set_pending(conn, chat_id: str, action: str) -> None:
    conn.execute(
        "INSERT INTO pending_actions (chat_id, action, created_at) VALUES (?, ?, ?) "
        "ON CONFLICT(chat_id) DO UPDATE SET action = excluded.action, "
        "created_at = excluded.created_at",
        (chat_id, action, now_iso()),
    )
    conn.commit()


def get_pending(conn, chat_id: str):
    """Ambil pending action; kalau kadaluarsa langsung dihapus dan return None."""
    row = conn.execute("SELECT * FROM pending_actions WHERE chat_id = ?",
                       (chat_id,)).fetchone()
    if not row:
        return None
    try:
        age = (datetime.now(timezone.utc)
               - datetime.fromisoformat(row["created_at"])).total_seconds()
    except ValueError:
        age = PENDING_TTL + 1
    if age > PENDING_TTL:
        clear_pending(conn, chat_id)
        return None
    return row["action"]


def clear_pending(conn, chat_id: str) -> None:
    conn.execute("DELETE FROM pending_actions WHERE chat_id = ?", (chat_id,))
    conn.commit()


def get_setting(conn, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def tg_api(bot_token: str, method: str, payload: dict | None = None,
           timeout: int = HTTP_TIMEOUT):
    url = f"https://api.telegram.org/bot{bot_token}/{method}"
    resp = requests.post(url, json=payload or {}, timeout=timeout)
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if not resp.ok or not data.get("ok"):
        raise RuntimeError(f"Telegram {method} gagal: HTTP {resp.status_code}: {resp.text[:300]}")
    return data.get("result")


def tg_get_updates(bot_token: str, offset: int | None = None, timeout_s: int = 0):
    payload: dict = {"timeout": timeout_s}
    if offset is not None:
        payload["offset"] = offset
    return tg_api(bot_token, "getUpdates", payload, timeout=HTTP_TIMEOUT + timeout_s)


def chunk_message(text: str, limit: int = TG_MSG_LIMIT) -> list:
    """Pecah pesan panjang di batas baris, biar ga kena limit 4096 Telegram."""
    if len(text) <= limit:
        return [text]
    chunks, cur = [], ""
    for line in text.split("\n"):
        if cur and len(cur) + len(line) + 1 > limit:
            chunks.append(cur.rstrip())
            cur = ""
        cur += line + "\n"
    if cur.strip():
        chunks.append(cur.rstrip())
    return chunks


def tg_send(bot_token: str, chat_id: str, text: str, reply_markup: dict | None = None,
            thread_id=None) -> None:
    chunks = chunk_message(text)
    for i, chunk in enumerate(chunks):
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if thread_id:  # kirim ke topic tertentu di group forum
            payload["message_thread_id"] = int(thread_id)
        # keyboard (kalau ada) nempel di pesan terakhir
        if reply_markup and i == len(chunks) - 1:
            payload["reply_markup"] = reply_markup
        tg_api(bot_token, "sendMessage", payload)


# ---------------------------------------------------------------------------
# GoodDay API
# ---------------------------------------------------------------------------

def gd_get(path: str, api_token: str, params: dict | None = None):
    url = f"{GOODDAY_BASE_URL}/{path.lstrip('/')}"
    resp = requests.get(
        url,
        headers={"gd-api-token": api_token, "Content-Type": "application/json"},
        params=params,
        timeout=HTTP_TIMEOUT,
    )
    if resp.status_code == 401:
        raise SystemExit("GoodDay nolak token (401). Cek GOODDAY_API_TOKEN.")
    resp.raise_for_status()
    return resp.json()


def gd_write(method: str, path: str, api_token: str, payload: dict):
    """POST/PUT ke GoodDay. Error dilempar sebagai RuntimeError berisi respons
    (dipakai buat lapor balik ke user via Telegram)."""
    url = f"{GOODDAY_BASE_URL}/{path.lstrip('/')}"
    resp = requests.request(
        method, url,
        headers={"gd-api-token": api_token, "Content-Type": "application/json"},
        json=payload,
        timeout=HTTP_TIMEOUT,
    )
    if not resp.ok:
        raise RuntimeError(f"GoodDay {method} {path} gagal: "
                           f"HTTP {resp.status_code}: {resp.text[:300]}")
    try:
        return resp.json()
    except ValueError:
        return None


def gd_update_status(api_token: str, task_id: str, user_id: str, status_id: str,
                     message: str | None = None):
    # ⚠️ terverifikasi 2026-07-07: endpoint ini PUT (POST balas 405),
    # beda dari dugaan awal PRD §4
    payload = {"userId": user_id, "statusId": status_id}
    if message:
        payload["message"] = message
    return gd_write("PUT", f"task/{task_id}/status", api_token, payload)


def gd_add_comment(api_token: str, task_id: str, user_id: str, message: str):
    return gd_write("POST", f"task/{task_id}/comment", api_token,
                    {"userId": user_id, "message": message})


def gd_update_custom_fields(api_token: str, task_id: str, fields: list):
    """fields: [{"id": <fieldId>, "value": <nilai>}]"""
    return gd_write("PUT", f"task/{task_id}/custom-fields", api_token,
                    {"customFields": fields})


def gd_report_time(api_token: str, task_id: str, user_id: str, minutes: int,
                   date_str: str | None = None, message: str | None = None):
    payload = {"userId": user_id, "reportedMinutes": minutes}
    if date_str:
        payload["date"] = date_str
    if message:
        payload["message"] = message
    return gd_write("POST", f"task/{task_id}/time-report", api_token, payload)


def gd_update_task(api_token: str, task_id: str, user_id: str, **fields):
    """PUT /task/{id}/update — field task umum (storyPoints, startDate, dst.).
    ⚠️ storyPoints: tersimpan & tampil di UI (diverifikasi user 2026-07-07),
    tapi TIDAK PERNAH dikembalikan API mana pun — write-only."""
    return gd_write("PUT", f"task/{task_id}/update", api_token,
                    {"userId": user_id, **fields})


def gd_task_detail(api_token: str, task_id: str):
    return gd_get(f"task/{task_id}", api_token)


def gd_statuses(api_token: str):
    return gd_get("statuses", api_token)


def gd_list_users(api_token: str):
    return gd_get("users", api_token)


def gd_list_projects(api_token: str):
    return gd_get("projects", api_token)


def gd_assigned_tasks(api_token: str, user_id: str):
    # tanpa param `closed` -> cuma task yang masih open (yang closed ga relevan
    # buat reminder)
    return gd_get(f"user/{user_id}/assigned-tasks", api_token)


def gd_project_map(api_token: str) -> dict:
    """Map projectId -> nama project. Gagal fetch = map kosong (nama ga tampil)."""
    try:
        return {str(p.get("id")): (p.get("name") or "") for p in gd_list_projects(api_token)}
    except Exception:
        return {}


def gd_user_label(u: dict) -> str:
    return (f"{u.get('firstName', '')} {u.get('lastName', '')}".strip()
            or u.get("name", "") or str(u.get("id", "")))


# ---------------------------------------------------------------------------
# Filter & format (dipakai per user)
# ---------------------------------------------------------------------------

HARI = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
BULAN = ["Januari", "Februari", "Maret", "April", "Mei", "Juni",
         "Juli", "Agustus", "September", "Oktober", "November", "Desember"]


def today_str() -> str:
    return datetime.now(ZoneInfo(TIMEZONE)).date().isoformat()


def human_today() -> str:
    d = datetime.now(ZoneInfo(TIMEZONE))
    return f"{HARI[d.weekday()]}, {d.day} {BULAN[d.month - 1]} {d.year}"


def human_date(value) -> str:
    """'2026-07-06...' -> '6 Jul'. Kalau formatnya aneh, tampilkan apa adanya."""
    try:
        d = date.fromisoformat(str(value)[:10])
        return f"{d.day} {BULAN[d.month - 1][:3]}"
    except ValueError:
        return str(value)[:10]


def date_only(value):
    return str(value)[:10] if value else None


def split_tasks(tasks: list, today: str):
    """Bagi task jadi 3 kelompok: hari ini, lewat deadline, dan lainnya."""
    todays, overdue, others = [], [], []
    for t in tasks:
        starts = date_only(t.get("startDate")) == today
        dl = date_only(t.get("deadline"))
        due = dl == today
        t["_starts"], t["_due"] = starts, due
        if starts or due:
            todays.append(t)
        elif dl and dl < today:
            overdue.append(t)
        else:
            others.append(t)
    todays.sort(key=lambda x: (not x["_due"], -(x.get("priority") or 0)))
    overdue.sort(key=lambda x: (date_only(x.get("deadline")) or "", -(x.get("priority") or 0)))
    others.sort(key=lambda x: (date_only(x.get("deadline")) or "9999-12-31",
                               -(x.get("priority") or 0)))
    return todays, overdue, others


def priority_label(p):
    if p is None:
        return ""
    if p >= 100:
        return "🔴 Emergency"
    if p >= 50:
        return "🟠 Blocker"
    if p >= 8:
        return "🟡 High"
    return ""


def _num_eq(a, b) -> bool:
    """8 == 8.0 == '8' (customFieldsData balikin float, config-nya string)."""
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return str(a) == str(b)


def task_ready(cfg, detail: dict):
    """Cek 4 syarat 🟢 SIAP DIKERJAKAN (PRD §6.2) dari detail GET /task/{id}.
    Return (bool, [alasan yang kurang])."""
    why = []
    sp = detail.get(cfg["ready_storypoint_source"])
    if sp in (None, "", 0):
        why.append("story point kosong")
    if not detail.get("startDate"):
        why.append("start date kosong")
    if not detail.get("endDate"):
        why.append("end date kosong")
    cf = detail.get("customFieldsData") or {}
    stream = cf.get(cfg["ready_stream_field"])
    if stream is None or not _num_eq(stream, cfg["ready_stream_value"]):
        why.append("Product Stream bukan Opsifin")
    if not cf.get(cfg["ready_delivery_field"]):
        why.append("Delivery Task belum dicentang")
    return (not why), why


def mark_ready(cfg, tasks: list, today: str, cache: dict) -> None:
    """Tempelkan t['_ready'] & t['_why'] ke task hari-ini + telat (yang lain
    dibiarkan — label cuma buat yang urgent). Detail di-cache biar /tasks
    berulang ga nembak API terus. Gagal fetch = tanpa label (bukan error)."""
    store = cache.setdefault("ready", {})
    for t in tasks:
        sd, dl = date_only(t.get("startDate")), date_only(t.get("deadline"))
        if not (sd == today or (dl and dl <= today)):
            continue
        tid = t.get("id")
        if not tid:
            continue
        hit = store.get(tid)
        if hit and time.time() - hit[0] <= CACHE_TTL:
            t["_ready"], t["_why"] = hit[1], hit[2]
            continue
        try:
            ready, why = task_ready(cfg, gd_task_detail(cfg["api_token"], tid))
        except Exception as e:
            print(f"  ! ready-check {tid} gagal: {e}", file=sys.stderr)
            continue
        store[tid] = (time.time(), ready, why)
        t["_ready"], t["_why"] = ready, why


DURASI_RE = re.compile(
    r"^(?:(\d+(?:[.,]\d+)?)\s*h)?\s*(?:(\d+)\s*m)?$", re.IGNORECASE)


def parse_duration(text: str):
    """'90m' / '1h30m' / '1.5h' / '2h' [catatan] -> (menit, catatan) atau None."""
    parts = text.strip().split(maxsplit=1)
    if not parts:
        return None
    m = DURASI_RE.match(parts[0])
    if not m or (m.group(1) is None and m.group(2) is None):
        return None
    minutes = 0.0
    if m.group(1):
        minutes += float(m.group(1).replace(",", ".")) * 60
    if m.group(2):
        minutes += int(m.group(2))
    minutes = int(round(minutes))
    if minutes <= 0:
        return None
    return minutes, (parts[1].strip() if len(parts) > 1 else "")


def format_task(t: dict, idx: int, projects: dict, today: str) -> str:
    name = html.escape(t.get("name") or "(tanpa judul)")
    sid = str(t.get("shortId") or "")
    tid = str(t.get("id") or "")
    sid_tag = f" <code>#{sid}</code>" if sid else ""
    if tid:
        url = GOODDAY_TASK_URL.format(task_id=tid)
        title = f'<a href="{url}">{name}</a>{sid_tag}'
    else:
        title = f"<b>{name}</b>{sid_tag}"

    tags = []
    if t.get("_due"):
        tags.append("⏰ <b>deadline HARI INI</b>")
    if t.get("_starts"):
        tags.append("🚀 <b>mulai HARI INI</b>")
    dl = date_only(t.get("deadline"))
    if dl and dl < today:
        try:
            telat = (date.fromisoformat(today) - date.fromisoformat(dl)).days
            tags.append(f"⚠️ telat {telat} hari")
        except ValueError:
            tags.append("⚠️ telat")
    prio = priority_label(t.get("priority"))
    if prio:
        tags.append(prio)

    info = []
    proj = projects.get(str(t.get("projectId") or ""))
    if proj:
        info.append(f"📁 {html.escape(proj)}")
    status = (t.get("status") or {}).get("name")
    if status:
        info.append(f"🏷 {html.escape(status)}")
    dates = []
    sd = date_only(t.get("startDate"))
    if sd:
        dates.append(f"mulai {human_date(sd)}")
    if dl:
        dates.append(f"deadline {human_date(dl)}")
    if dates:
        info.append("🗓 " + " → ".join(dates))

    ready_mark = ""
    if "_ready" in t:
        ready_mark = "🟢 " if t["_ready"] else "🚧 "
    out = [f"{idx}. {ready_mark}{title}"]
    if tags:
        out.append("    " + " · ".join(tags))
    if "_ready" in t:
        if t["_ready"]:
            out.append("    🟢 <b>SIAP DIKERJAKAN</b>")
        else:
            out.append("    🚧 <b>BELUM SIAP</b> — "
                       + html.escape(", ".join(t.get("_why") or [])))
    if info:
        out.append("    " + " · ".join(info))
    return "\n".join(out)


def build_message(tasks: list, today: str, greeting_name: str = "",
                  projects: dict | None = None) -> str:
    projects = projects or {}
    hi = f" {html.escape(greeting_name)}" if greeting_name else ""
    if not tasks:
        return (f"✅ <b>GoodDay — {human_today()}</b>\n\n"
                f"Hai{hi}, ga ada task aktif yang di-assign ke kamu. Santai. 🎉")

    todays, overdue, others = split_tasks(tasks, today)
    lines = [f"📋 <b>GoodDay — {human_today()}</b>",
             f"Hai{hi}, ini daftar task kamu:"]
    idx = 1

    def section(title: str, items: list, limit: int | None = None):
        nonlocal idx
        shown = items[:limit] if limit else items
        lines.append("")
        lines.append(title)
        lines.append("─────────────────────────")
        for t in shown:
            lines.append(format_task(t, idx, projects, today))
            lines.append("")
            idx += 1
        hidden = len(items) - len(shown)
        if hidden > 0:
            lines.append(f"… dan <b>{hidden}</b> task lainnya — buka GoodDay buat lihat semua.")
            lines.append("")

    if todays:
        section(f"🔥 <b>HARUS JALAN HARI INI ({len(todays)})</b>", todays)
    else:
        lines += ["", "😌 Ga ada task yang mulai / deadline hari ini."]
    if overdue:
        section(f"⚠️ <b>LEWAT DEADLINE ({len(overdue)})</b>", overdue)
    if others:
        section(f"📌 <b>TASK AKTIF LAINNYA ({len(others)})</b>", others, MAX_OTHERS)

    lines.append(f"Total <b>{len(tasks)}</b> task aktif — "
                 f"🔥 {len(todays)} hari ini · ⚠️ {len(overdue)} telat · "
                 f"📌 {len(others)} lainnya")
    return "\n".join(lines)


def digest_task_line(t: dict, today: str) -> str:
    """Satu baris ringkas per task buat digest group."""
    name = html.escape(t.get("name") or "(tanpa judul)")
    tid = str(t.get("id") or "")
    if tid:
        title = f'<a href="{GOODDAY_TASK_URL.format(task_id=tid)}">{name}</a>'
    else:
        title = f"<b>{name}</b>"
    if "_ready" in t:
        title = ("🟢 " if t["_ready"] else "🚧 ") + title
    marks = []
    if t.get("_due"):
        marks.append("⏰ hari ini")
    if t.get("_starts"):
        marks.append("🚀 mulai")
    dl = date_only(t.get("deadline"))
    if dl and dl < today:
        try:
            telat = (date.fromisoformat(today) - date.fromisoformat(dl)).days
            marks.append(f"⚠️ telat {telat} hr")
        except ValueError:
            marks.append("⚠️ telat")
    prio = priority_label(t.get("priority"))
    if prio:
        marks.append(prio.split()[0])  # emoji-nya aja biar ringkas
    status = (t.get("status") or {}).get("name")
    if status:
        marks.append(f"🏷 {html.escape(status)}")
    return f"  • {title}" + (f" — {' · '.join(marks)}" if marks else "")


def build_digest(entries: list, today: str) -> str:
    """entries: [{name, gd_user_id, todays, overdue, error?}] -> pesan group."""
    lines = [f"🌅 <b>GoodDay Reminder Task — {human_today()}</b>",
             "Yang harus jalan hari ini &amp; yang lewat deadline:", ""]
    for e in entries:
        if e.get("error"):
            lines.append(f"❌ <b>{html.escape(e['name'])}</b> — gagal ambil data")
        elif not e["todays"] and not e["overdue"]:
            lines.append(f"✅ <b>{html.escape(e['name'])}</b> — bersih, ga ada yang urgent 🎉")
        else:
            lines.append(f"👤 <b>{html.escape(e['name'])}</b> — "
                         f"🔥 {len(e['todays'])} hari ini · ⚠️ {len(e['overdue'])} telat")
            for t in e["todays"] + e["overdue"]:
                lines.append(digest_task_line(t, today))
        lines.append("")
    lines.append("Tekan tombol buat detail lengkap per orang 👇")
    return "\n".join(lines)


def digest_kb(entries: list, deep_link_user: str | None = None) -> dict | None:
    """Tombol detail per orang. deep_link_user = username bot reminder; dipakai
    kalau digest dikirim bot LAIN (callback cuma bisa dilayani bot pengirim,
    jadi tombolnya dialihkan ke DM bot reminder via deep-link /start)."""
    btns = []
    for e in entries:
        if e.get("error"):
            continue
        label = f"📄 {e['name'].split()[0]}"
        if deep_link_user:
            btns.append({"text": label,
                         "url": f"https://t.me/{deep_link_user}?start=dtl_{e['gd_user_id']}"})
        else:
            btns.append({"text": label, "callback_data": f"dtl:{e['gd_user_id']}"})
    rows = [btns[i:i + 3] for i in range(0, len(btns), 3)]
    return {"inline_keyboard": rows} if rows else None


LINK_RE = re.compile(r'<a href="([^"]+)">(.*?)</a>')
TAG_RE = re.compile(r"<[^>]+>")


def strip_html(text: str) -> str:
    """Buat tampilan terminal (dry-run/demo): link jadi 'judul → url', tag dibuang."""
    text = LINK_RE.sub(r"\2 → \1", text)
    return TAG_RE.sub("", text)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_init_db(cfg, args):
    init_db(cfg["db_path"])


def cmd_gd_users(cfg, args):
    if not cfg["api_token"]:
        raise SystemExit("GOODDAY_API_TOKEN belum di-set.")
    users = gd_list_users(cfg["api_token"])
    if not users:
        print("Ga ada user kebaca.")
        return
    print(f"{'GOODDAY USER ID':<40}  {'NAMA':<26}  EMAIL")
    print("-" * 88)
    for u in users:
        uid = str(u.get("id", ""))
        print(f"{uid:<40}  {gd_user_label(u):<26}  {u.get('email', '')}")


def cmd_register(cfg, args):
    """Baca getUpdates, simpen chat_id + nama Telegram yang belum ada di DB."""
    if not cfg["bot_token"]:
        raise SystemExit("TELEGRAM_BOT_TOKEN belum di-set.")
    updates = tg_get_updates(cfg["bot_token"])
    if not updates:
        print("Ga ada update. Pastikan tiap orang udah kirim /start ke bot,")
        print("dan webhook bot ga aktif (getUpdates & webhook ga bisa barengan).")
        return

    conn = db_connect(cfg["db_path"])
    seen, added = {}, 0
    for up in updates:
        msg = up.get("message") or up.get("edited_message") or {}
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None or chat_id in seen:
            continue
        name = (f"{chat.get('first_name', '')} {chat.get('last_name', '')}".strip()
                or chat.get("username", "") or str(chat_id))
        seen[chat_id] = name
        try:
            conn.execute(
                "INSERT INTO recipients (telegram_chat_id, telegram_name, created_at) "
                "VALUES (?, ?, ?)",
                (str(chat_id), name, now_iso()),
            )
            added += 1
            print(f"  + {chat_id}  ({name})")
        except sqlite3.IntegrityError:
            pass  # udah terdaftar
    conn.commit()
    conn.close()
    print(f"\n{added} chat baru terdaftar. Total chat unik terlihat: {len(seen)}.")
    print("Langkah berikut: `link <chat_id> <gd_user_id>` buat tiap orang,")
    print("atau jalankan `bot` biar orang bisa daftar sendiri.")


def cmd_link(cfg, args):
    conn = db_connect(cfg["db_path"])
    cur = conn.execute(
        "UPDATE recipients SET gd_user_id = ?, gd_name = COALESCE(?, gd_name) "
        "WHERE telegram_chat_id = ?",
        (args.gd_user_id, args.name, args.chat_id),
    )
    conn.commit()
    if cur.rowcount == 0:
        print(f"Chat_id {args.chat_id} ga ketemu. Jalanin `register` dulu?")
    else:
        print(f"Ke-link: chat {args.chat_id} -> GoodDay user {args.gd_user_id}")
    conn.close()


def cmd_users(cfg, args):
    conn = db_connect(cfg["db_path"])
    rows = conn.execute("SELECT * FROM recipients ORDER BY id").fetchall()
    conn.close()
    if not rows:
        print("Belum ada penerima. Jalanin `register` atau `bot`.")
        return
    print(f"{'CHAT_ID':<14} {'TG NAME':<18} {'GD USER ID':<38} {'ON':<3} {'APR':<3} STATUS")
    print("-" * 90)
    for r in rows:
        linked = "✓" if r["gd_user_id"] else "—"
        on = "on" if r["enabled"] else "off"
        apr = "ya" if r["approved"] else "—"
        if r["gd_user_id"] and not r["approved"]:
            status = "nunggu approval"
        elif r["gd_user_id"] and r["enabled"]:
            status = "siap"
        else:
            status = "belum lengkap"
        print(f"{r['telegram_chat_id']:<14} {(r['telegram_name'] or ''):<18} "
              f"{(r['gd_user_id'] or '(' + linked + ')'):<38} {on:<3} {apr:<3} {status}")


def cmd_toggle(cfg, args, enabled: int):
    conn = db_connect(cfg["db_path"])
    cur = conn.execute("UPDATE recipients SET enabled = ? WHERE telegram_chat_id = ?",
                       (enabled, args.chat_id))
    conn.commit()
    conn.close()
    verb = "diaktifkan" if enabled else "dinonaktifkan"
    print(f"Chat {args.chat_id} {verb}." if cur.rowcount else "Chat_id ga ketemu.")


def cmd_run(cfg, args):
    if not cfg["api_token"]:
        raise SystemExit("GOODDAY_API_TOKEN belum di-set.")
    if not args.dry_run and not cfg["bot_token"]:
        raise SystemExit("TELEGRAM_BOT_TOKEN belum di-set.")

    conn = db_connect(cfg["db_path"])
    recipients = conn.execute(
        "SELECT * FROM recipients WHERE enabled = 1 AND approved = 1 "
        "AND gd_user_id IS NOT NULL AND gd_user_id != '' ORDER BY id"
    ).fetchall()
    conn.close()

    if not recipients:
        print("Ga ada penerima aktif yang udah ke-link. Cek `users`.")
        return

    today = today_str()
    projects = gd_project_map(cfg["api_token"])  # sekali aja buat semua user
    ready_cache: dict = {}
    ok, failed, skipped = 0, 0, 0

    for r in recipients:
        chat_id = r["telegram_chat_id"]
        gd_user_id = r["gd_user_id"]
        name = r["gd_name"] or r["telegram_name"] or ""
        label = f"{name or chat_id} (gd:{gd_user_id})"
        try:
            tasks = gd_assigned_tasks(cfg["api_token"], gd_user_id)
            mark_ready(cfg, tasks, today, ready_cache)
            todays, overdue, _ = split_tasks(tasks, today)
            # --skip-empty: lewati kalau ga ada yang urgent (hari ini / telat)
            if args.skip_empty and not todays and not overdue:
                skipped += 1
                print(f"  ~ {label}: ga ada task urgent, di-skip")
                continue
            msg = build_message(tasks, today,
                                greeting_name=(name.split()[0] if name else ""),
                                projects=projects)
            if args.dry_run:
                print(f"\n===== {label} — {len(todays)} hari ini / "
                      f"{len(overdue)} telat / {len(tasks)} total =====")
                print(strip_html(msg))
            else:
                tg_send(cfg["bot_token"], chat_id, msg)
                print(f"  ✓ {label}: terkirim ({len(todays)} hari ini / "
                      f"{len(tasks)} total)")
            ok += 1
        except Exception as e:
            failed += 1
            print(f"  ✗ {label}: GAGAL — {e}", file=sys.stderr)
            # penting: 1 user gagal ga nge-stop yang lain

    mode = "DRY RUN" if args.dry_run else "SELESAI"
    print(f"\n[{mode}] sukses={ok} gagal={failed} skip={skipped} "
          f"dari {len(recipients)} penerima, tanggal {today}")


def digest_targets(cfg) -> list:
    """[(gd_user_id, nama)] dari DIGEST_GD_USERS, atau semua penerima ke-link."""
    if cfg["digest_gd_users"]:
        wanted = [x.strip() for x in cfg["digest_gd_users"].split(",") if x.strip()]
        try:
            by_id = {str(u.get("id")): gd_user_label(u)
                     for u in gd_list_users(cfg["api_token"])}
        except Exception:
            by_id = {}
        return [(uid, by_id.get(uid, uid)) for uid in wanted]
    conn = db_connect(cfg["db_path"])
    rows = conn.execute(
        "SELECT * FROM recipients WHERE enabled = 1 AND approved = 1 "
        "AND gd_user_id IS NOT NULL AND gd_user_id != '' ORDER BY id"
    ).fetchall()
    conn.close()
    return [(r["gd_user_id"],
             r["gd_name"] or r["telegram_name"] or r["gd_user_id"])
            for r in rows]


def collect_digest_entries(cfg, targets: list, today: str,
                           cache: dict | None = None) -> list:
    cache = cache if cache is not None else {}
    entries = []
    for uid, name in targets:
        try:
            tasks = gd_assigned_tasks(cfg["api_token"], uid)
            mark_ready(cfg, tasks, today, cache)
            todays, overdue, _ = split_tasks(tasks, today)
            entries.append({"gd_user_id": uid, "name": name,
                            "todays": todays, "overdue": overdue})
            print(f"  · {name}: {len(todays)} hari ini / {len(overdue)} telat")
        except Exception as e:
            entries.append({"gd_user_id": uid, "name": name,
                            "todays": [], "overdue": [], "error": str(e)})
            print(f"  ✗ {name}: GAGAL — {e}", file=sys.stderr)
    return entries


def cmd_digest(cfg, args):
    """Kirim ringkasan tim (hari ini + telat aja) ke group/topic Telegram."""
    if not cfg["digest_enabled"]:
        # exit 0 biar cron ga spam error selama fitur dimatikan
        print("⏸ Digest lagi dinonaktifkan (DIGEST_ENABLED=1 di .env buat "
              "menyalakan lagi). Ga ada yang dikirim.")
        return
    if not cfg["api_token"]:
        raise SystemExit("GOODDAY_API_TOKEN belum di-set.")
    chat_id = args.chat_id or cfg["digest_chat_id"]
    topic_id = args.topic_id or cfg["digest_topic_id"] or None
    if not args.dry_run:
        if not cfg["bot_token"]:
            raise SystemExit("TELEGRAM_BOT_TOKEN belum di-set.")
        if not chat_id:
            raise SystemExit("Tujuan digest belum di-set: isi DIGEST_CHAT_ID di .env "
                             "atau pakai --chat-id.")

    targets = digest_targets(cfg)
    if not targets:
        print("Ga ada target digest. Isi DIGEST_GD_USERS di .env, atau link penerima dulu.")
        return

    today = today_str()
    entries = collect_digest_entries(cfg, targets, today)
    text = build_digest(entries, today)
    if args.dry_run:
        print("\n" + strip_html(text))
        print("\n[DRY RUN] ga dikirim.")
    else:
        send_token = cfg["digest_bot_token"] or cfg["bot_token"]
        if args.no_buttons:
            kb = None
        elif send_token != cfg["bot_token"]:
            # dikirim bot lain -> tombol detail deep-link ke bot reminder;
            # kalau username bot reminder ga kebaca, kirim tanpa tombol
            try:
                deep_link_user = (tg_api(cfg["bot_token"], "getMe") or {}).get("username")
            except Exception:
                deep_link_user = None
            kb = digest_kb(entries, deep_link_user) if deep_link_user else None
        else:
            kb = digest_kb(entries)
        tg_send(send_token, chat_id, text, reply_markup=kb, thread_id=topic_id)
        tujuan = f"chat {chat_id}" + (f", topic {topic_id}" if topic_id else "")
        print(f"\nDigest terkirim ke {tujuan}.")


# ---------------------------------------------------------------------------
# Mode BOT — pendaftaran mandiri via Telegram (long-polling)
# ---------------------------------------------------------------------------

WELCOME = (
    "👋 Halo! Aku bot reminder task <b>GoodDay</b>.\n\n"
    "Tiap pagi aku kirim daftar task kamu — yang harus jalan hari ini "
    "aku tandai 🔥 biar jelas.\n\n"
    "Perintah:\n"
    "/daftar — daftar pakai ID GoodDay kamu (perlu approval admin)\n"
    "/tasks — lihat task kamu + tombol ✏️ buat update langsung dari sini\n"
    "/cari — cari task kamu by nomor (#shortId) buat lihat detail & update\n"
    # "/digest — ringkasan tim terbaru (bisa juga diketik di topic group)\n"
    "/gdusers — daftar user GoodDay + id-nya\n"
    "/status — cek status pendaftaran\n"
    "/batal — batalkan input yang lagi ditunggu\n"
    "/berhenti — stop terima reminder"
)

HELP = (
    "❓ <b>Bantuan — GoodDay Reminder Bot</b>\n\n"
    "<b>Buat kamu (chat pribadi):</b>\n"
    "/daftar — ketik ID GoodDay kamu (lihat /gdusers), lalu tunggu approval admin\n"
    "/tasks — semua task aktif kamu, yang urgent ditandai 🔥\n"
    "/cari <code>&lt;nomor&gt;</code> — cari task kamu by #shortId/id, "
    "langsung dapat detail + tombol aksi\n"
    "/status — cek kamu ke-link ke user GoodDay yang mana\n"
    "\n<b>Update task dari Telegram (via tombol ✏️ di /tasks):</b>\n"
    "🔄 ganti status · 💬 kirim comment · 🧾 ubah custom field\n"
    "⏱ report time (format: <code>90m</code> / <code>1h30m</code> / "
    "<code>1.5h catatan</code>)\n"
    "🎯 set story points — pilih 1/3/5/8/13 (nilainya cuma kelihatan di GoodDay UI)\n"
    "📅 set start &amp; end date (format <code>10/7 12/7</code> atau "
    "<code>2026-07-10 2026-07-12</code>)\n"
    "✏️ Task lain… — update task di luar daftar urgent (ketik shortId)\n"
    "/batal — batalkan input yang lagi ditunggu\n"
    "/berhenti — stop reminder pagi (nyalain lagi: /start)\n\n"
    # "<b>Info tim:</b>\n"
    # "/digest — ringkasan tim: task hari ini + yang telat\n"
    # "/gdusers — daftar user GoodDay + id-nya\n\n"
    # "<b>Di topic group</b> bisa langsung ketik: /digest, /tasks (task kamu\n"
    # "sendiri), /status, /gdusers, /help — bot bales di topic yang sama.\n"
    # "Khusus /daftar &amp; /berhenti lewat DM.\n\n"
    "<b>Yang jalan otomatis:</b>\n"
    "• Tiap pagi: reminder task pribadi via DM (kalau udah /daftar).\n"
    # "• Tiap pagi: digest tim ke topic group.\n"
    "• Data selalu diambil live dari GoodDay — ga perlu sync manual;\n"
    "  task baru langsung kebawa tiap /tasks.\n\n"
    "<b>Arti label:</b>\n"
    "⏰ deadline hari ini · 🚀 mulai hari ini · ⚠️ lewat deadline\n"
    "🔴 Emergency · 🟠 Blocker · 🟡 High\n"
    "🟢 SIAP DIKERJAKAN — story point, start+end date, Product Stream=Opsifin,\n"
    "dan Delivery Task semua terpenuhi · 🚧 BELUM SIAP — ada yang kurang\n"
    "(alasannya ditulis di tiap task)\n"
    "Judul task bisa diklik — langsung kebuka di GoodDay."
)

MENU_KB = {"inline_keyboard": [
    [{"text": "📝 Daftar / ganti user GoodDay", "callback_data": "menu:daftar"}],
    [{"text": "📋 Task saya hari ini", "callback_data": "menu:tasks"}],
    [{"text": "ℹ️ Status saya", "callback_data": "menu:status"},
     {"text": "🔕 Berhenti", "callback_data": "menu:stop"},
     {"text": "❓ Bantuan", "callback_data": "menu:help"}],
]}

def cached_gd_users(cfg, cache) -> list:
    if not cache.get("users") or time.time() - cache.get("users_ts", 0) > CACHE_TTL:
        cache["users"] = sorted(gd_list_users(cfg["api_token"]),
                                key=lambda u: gd_user_label(u).lower())
        cache["users_ts"] = time.time()
    return cache["users"]


def cached_projects(cfg, cache) -> dict:
    if not cache.get("projects") or time.time() - cache.get("projects_ts", 0) > CACHE_TTL:
        cache["projects"] = gd_project_map(cfg["api_token"])
        cache["projects_ts"] = time.time()
    return cache["projects"]


def cached_statuses(cfg, cache) -> list:
    """Status org unik by nama (dari 35 status banyak nama dobel antar board;
    id org-wide mana pun diterima API — terverifikasi di task nyata)."""
    if not cache.get("statuses") or time.time() - cache.get("statuses_ts", 0) > CACHE_TTL:
        seen, uniq = set(), []
        for s in gd_statuses(cfg["api_token"]):
            nm = (s.get("name") or "").strip().lower()
            if not nm or nm in seen:
                continue
            seen.add(nm)
            uniq.append(s)
        uniq.sort(key=lambda s: (s.get("systemStatus") or 0,
                                 (s.get("name") or "").lower()))
        cache["statuses"] = uniq
        cache["statuses_ts"] = time.time()
    return cache["statuses"]


def cached_custom_fields(cfg, cache) -> dict:
    """Map fieldId -> definisi field, HANYA whitelist EDIT_CUSTOM_FIELDS.
    Definisi (nama, type, listItems) dari GET /custom-fields."""
    if not cache.get("cfields") or time.time() - cache.get("cfields_ts", 0) > CACHE_TTL:
        try:
            allf = gd_get("custom-fields", cfg["api_token"])
        except Exception as e:
            print(f"  ! GET /custom-fields gagal: {e}", file=sys.stderr)
            allf = []
        cache["cfields"] = {str(f.get("id")): f for f in allf
                            if str(f.get("id")) in cfg["edit_custom_fields"]}
        cache["cfields_ts"] = time.time()
    return cache["cfields"]


def user_owns_task(cfg, gd_user_id: str, task_ref: str):
    """Cari task by id ATAU shortId di assigned-tasks MILIK user itu saja —
    sekaligus guard kepemilikan: task orang lain otomatis 'ga ketemu'."""
    try:
        tasks = gd_assigned_tasks(cfg["api_token"], gd_user_id)
    except Exception:
        return None
    ref = str(task_ref).strip().lstrip("#")
    for t in tasks:
        if str(t.get("id")) == ref or str(t.get("shortId")) == ref:
            return t
    return None


def task_label(task: dict) -> str:
    name = html.escape((task.get("name") or "(tanpa judul)")[:60])
    return f"<b>#{task.get('shortId')}</b> {name}"


# --- Fungsi aksi update task -------------------------------------------------
# Dipisah dari handler tombol/pesan: AI layer (PRD §6.4) nanti memanggil
# fungsi-fungsi ini juga. Return: teks HTML siap kirim ke user.

def act_update_status(cfg, row, task, status_id: str, status_name: str) -> str:
    try:
        gd_update_status(cfg["api_token"], task["id"], row["gd_user_id"], status_id)
        return (f"✅ Status {task_label(task)} → "
                f"<b>{html.escape(status_name)}</b>.")
    except Exception as e:
        return f"❌ Gagal ganti status: {html.escape(str(e)[:200])}"


def act_comment(cfg, row, task, text: str) -> str:
    try:
        gd_add_comment(cfg["api_token"], task["id"], row["gd_user_id"], text)
        return f"✅ Comment terkirim ke {task_label(task)}."
    except Exception as e:
        return f"❌ Gagal kirim comment: {html.escape(str(e)[:200])}"


def act_set_custom_field(cfg, row, task, field: dict, value, value_label: str) -> str:
    fname = html.escape(field.get("name") or str(field.get("id")))
    try:
        gd_update_custom_fields(cfg["api_token"], task["id"],
                                [{"id": field.get("id"), "value": value}])
        return (f"✅ <b>{fname}</b> di {task_label(task)} → "
                f"<b>{html.escape(value_label)}</b>.")
    except Exception as e:
        return f"❌ Gagal ubah {fname}: {html.escape(str(e)[:200])}"


def act_set_story_points(cfg, row, task, points) -> str:
    """points: int (harus di STORY_POINT_CHOICES) atau None (hapus).
    Write-only — API ga bisa baca balik."""
    if points is not None and points not in STORY_POINT_CHOICES:
        pilihan = "/".join(str(p) for p in STORY_POINT_CHOICES)
        return f"❌ Story points harus salah satu dari: {pilihan}."
    try:
        gd_update_task(cfg["api_token"], task["id"], row["gd_user_id"],
                       storyPoints=points)
        if points is None:
            return f"✅ Story points {task_label(task)} dihapus."
        return (f"✅ Story points {task_label(task)} → <b>{points}</b>.\n"
                f"ℹ️ API GoodDay ga bisa nampilin balik nilai ini — "
                f"cek di GoodDay UI kalau mau mastiin.")
    except Exception as e:
        return f"❌ Gagal set story points: {html.escape(str(e)[:200])}"


DATE_RE = re.compile(r"^(?:(\d{4})-(\d{1,2})-(\d{1,2})|(\d{1,2})/(\d{1,2})(?:/(\d{4}))?)$")


def parse_date_token(tok: str):
    """'2026-07-10' / '10/7' / '10/7/2026' -> 'YYYY-MM-DD' atau None."""
    m = DATE_RE.match(tok.strip())
    if not m:
        return None
    try:
        if m.group(1):  # YYYY-MM-DD
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        else:           # DD/MM[/YYYY]
            yr = int(m.group(6)) if m.group(6) else datetime.now(ZoneInfo(TIMEZONE)).year
            d = date(yr, int(m.group(5)), int(m.group(4)))
        return d.isoformat()
    except ValueError:
        return None


def parse_date_range(text: str):
    """'10/7 12/7' / '2026-07-10' (1 tanggal = start=end) -> (sd, ed) atau None."""
    toks = [t for t in re.split(r"[\s]+|s/d|—|–", text.strip()) if t and t != "-"]
    if not toks or len(toks) > 2:
        return None
    sd = parse_date_token(toks[0])
    ed = parse_date_token(toks[1]) if len(toks) > 1 else sd
    if not sd or not ed or ed < sd:
        return None
    return sd, ed


def act_set_dates(cfg, row, task, sd: str, ed: str) -> str:
    try:
        gd_update_task(cfg["api_token"], task["id"], row["gd_user_id"],
                       startDate=sd, endDate=ed)
        return (f"✅ Jadwal {task_label(task)} → "
                f"<b>{human_date(sd)} → {human_date(ed)}</b>.")
    except Exception as e:
        return f"❌ Gagal set tanggal: {html.escape(str(e)[:200])}"


def act_report_time(cfg, row, task, minutes: int, note: str) -> str:
    try:
        gd_report_time(cfg["api_token"], task["id"], row["gd_user_id"], minutes,
                       message=note or None)
        h, m = divmod(minutes, 60)
        dur = (f"{h}j {m}m" if h and m else f"{h} jam" if h else f"{m} menit")
        return f"✅ Time report <b>{dur}</b> tercatat di {task_label(task)}."
    except Exception as e:
        return f"❌ Gagal report time: {html.escape(str(e)[:200])}"


# --- AI layer (SCAFFOLD — PRD §6.4) -----------------------------------------

def ai_enabled(cfg, conn) -> bool:
    """AI aktif hanya jika admin set `/ai on` DAN API key terisi."""
    return get_setting(conn, "ai_enabled", "0") == "1" and bool(cfg["ai_api_key"])


def _ai_endpoint(cfg) -> str | None:
    """URL /chat/completions dari base_url (OpenAI-compatible; 9router, ollama,
    openai, dst.). None kalau endpoint ga bisa ditentukan."""
    base = cfg["ai_base_url"].rstrip("/")
    if not base:
        if cfg["ai_provider"] in ("openai", ""):
            base = "https://api.openai.com/v1"
        else:
            # provider self-hosted / anthropic tanpa base_url: ga didukung jalur ini.
            # (anthropic murni butuh SDK resmi + skill claude-api — belum dipakai.)
            return None
    return base + "/chat/completions"


def _ai_chat(cfg, messages, tools):
    """Satu ronde panggilan LLM (OpenAI-compatible chat/completions).
    Return message dict dari choices[0].message. Boleh raise (dibungkus caller)."""
    endpoint = _ai_endpoint(cfg)
    if not endpoint:
        raise RuntimeError("AI_BASE_URL kosong / provider ga didukung jalur ini")
    payload = {
        "model": cfg["ai_model"] or "gpt-4o-mini",
        "messages": messages,
        "temperature": 0.2,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    resp = requests.post(
        endpoint,
        headers={"Authorization": f"Bearer {cfg['ai_api_key']}",
                 "Content-Type": "application/json"},
        json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]


def _ai_tools_spec():
    """Definisi tool (function-calling) — memetakan niat LLM ke fungsi act_*."""
    def tool(name, desc, props, required):
        return {"type": "function", "function": {
            "name": name, "description": desc,
            "parameters": {"type": "object", "properties": props,
                           "required": required}}}
    ref = {"type": "string",
           "description": "Nomor #shortId atau id task (harus milik user ini)"}
    return [
        tool("update_status", "Ganti status sebuah task.",
             {"task_ref": ref, "status_name": {"type": "string",
              "description": "Nama status, persis salah satu dari daftar status valid"}},
             ["task_ref", "status_name"]),
        tool("add_comment", "Kirim comment ke sebuah task.",
             {"task_ref": ref, "message": {"type": "string"}},
             ["task_ref", "message"]),
        tool("report_time", "Catat waktu kerja (menit) ke sebuah task.",
             {"task_ref": ref,
              "minutes": {"type": "integer", "description": "Durasi dalam menit"},
              "note": {"type": "string", "description": "Catatan opsional"}},
             ["task_ref", "minutes"]),
        tool("set_story_points", "Set story points task (1/3/5/8/13, 0 = hapus).",
             {"task_ref": ref,
              "points": {"type": "integer", "enum": [0, 1, 3, 5, 8, 13]}},
             ["task_ref", "points"]),
        tool("set_dates", "Set start & end date task (format YYYY-MM-DD).",
             {"task_ref": ref, "start_date": {"type": "string"},
              "end_date": {"type": "string"}},
             ["task_ref", "start_date", "end_date"]),
        tool("set_custom_field", "Ubah custom field task (yang ada di daftar).",
             {"task_ref": ref, "field_name": {"type": "string"},
              "value": {"type": "string"}},
             ["task_ref", "field_name", "value"]),
    ]


def _ai_system_prompt(today, tasks, statuses, cfields) -> str:
    lines = []
    for t in tasks[:60]:
        st = (t.get("status") or {}).get("name") or "?"
        extra = []
        sd = date_only(t.get("startDate"))
        dl = date_only(t.get("deadline"))
        if sd:
            extra.append(f"mulai {sd}")
        if dl:
            extra.append(f"deadline {dl}")
        lines.append(f"- #{t.get('shortId')} (id {t.get('id')}): "
                     f"{t.get('name') or '(tanpa judul)'} [status: {st}]"
                     + (" · " + ", ".join(extra) if extra else ""))
    task_block = "\n".join(lines) or "(kamu lagi ga punya task aktif)"
    status_names = ", ".join(s.get("name") for s in statuses
                             if s.get("name")) or "(ga ada)"
    cf_lines = []
    for f in cfields.values():
        nm = f.get("name") or f.get("id")
        if f.get("type") == 23:
            cf_lines.append(f"- {nm} (checkbox: ya/tidak)")
        else:
            items = (f.get("params") or {}).get("listItems") or []
            if items:
                cf_lines.append(f"- {nm} (pilihan: "
                                + ", ".join(str(i.get('value')) for i in items) + ")")
            else:
                cf_lines.append(f"- {nm} (teks)")
    cf_block = "\n".join(cf_lines) or "(ga ada)"
    return (
        "Kamu asisten di dalam bot Telegram Opsifin, bantu user ngurus task "
        "GoodDay mereka. Balas Bahasa Indonesia santai & singkat. Boleh HTML "
        "Telegram sederhana (<b>) seperlunya, JANGAN markdown.\n\n"
        "ATURAN:\n"
        "- Kamu HANYA boleh menyentuh task milik user ini yang ada di daftar "
        "bawah. Jangan mengarang task/angka.\n"
        "- Untuk MELAKUKAN aksi (status, comment, report time, story point, "
        "tanggal, custom field) WAJIB panggil tool yang sesuai. JANGAN ngaku "
        "sudah melakukan sesuatu tanpa memanggil tool.\n"
        "- Kalau user cuma nanya/ngobrol, jawab langsung tanpa tool.\n"
        "- Kalau ambigu (task mana / nilai apa), tanya balik dulu, jangan nebak.\n"
        "- Rujuk task pakai nomor #shortId.\n\n"
        f"Hari ini: {today} (WIB).\n\n"
        f"TASK AKTIF USER:\n{task_block}\n\n"
        f"STATUS valid: {status_names}\n"
        "STORY POINTS valid: 1, 3, 5, 8, 13 (0 = hapus)\n"
        f"CUSTOM FIELD yang bisa diubah:\n{cf_block}\n")


def _ai_run_tool(cfg, row, cache, name, args) -> str:
    """Eksekusi satu tool-call -> string hasil (HTML). Task di-resolve lewat
    user_owns_task -> guard kepemilikan otomatis (task orang lain = ga ketemu)."""
    ref = str(args.get("task_ref", "")).strip().lstrip("#")
    task = user_owns_task(cfg, row["gd_user_id"], ref) if ref else None
    if not task:
        return f"❌ Task '{html.escape(ref[:20])}' ga ketemu di daftar task kamu."

    if name == "update_status":
        want = (args.get("status_name") or "").strip().lower()
        opts = cached_statuses(cfg, cache)
        s = next((x for x in opts
                  if (x.get("name") or "").strip().lower() == want), None)
        if not s:
            s = next((x for x in opts
                      if want and want in (x.get("name") or "").strip().lower()), None)
        if not s:
            names = ", ".join(x.get("name") for x in opts if x.get("name"))
            return f"❌ Status '{args.get('status_name')}' ga dikenal. Pilihan: {names}"
        return act_update_status(cfg, row, task, str(s.get("id")), s.get("name"))

    if name == "add_comment":
        msg = (args.get("message") or "").strip()
        if not msg:
            return "❌ Isi comment kosong."
        return act_comment(cfg, row, task, msg)

    if name == "report_time":
        try:
            minutes = int(args.get("minutes"))
        except (TypeError, ValueError):
            return "❌ minutes harus angka (menit)."
        if minutes <= 0:
            return "❌ minutes harus > 0."
        return act_report_time(cfg, row, task, minutes,
                               (args.get("note") or "").strip())

    if name == "set_story_points":
        raw = args.get("points")
        points = None if raw in (0, "0", "-", None, "") else int(raw)
        return act_set_story_points(cfg, row, task, points)

    if name == "set_dates":
        sd = parse_date_token(str(args.get("start_date", "")))
        ed = parse_date_token(str(args.get("end_date", "")
                                  or args.get("start_date", "")))
        if not sd or not ed:
            return "❌ Format tanggal harus YYYY-MM-DD."
        if ed < sd:
            return "❌ end_date ga boleh sebelum start_date."
        return act_set_dates(cfg, row, task, sd, ed)

    if name == "set_custom_field":
        fields = cached_custom_fields(cfg, cache)
        fname = (args.get("field_name") or "").strip().lower()
        field = next((f for f in fields.values()
                      if (f.get("name") or "").strip().lower() == fname), None)
        if not field:
            names = ", ".join(f.get("name") for f in fields.values() if f.get("name"))
            return (f"❌ Custom field '{args.get('field_name')}' ga ada / di luar "
                    f"whitelist. Pilihan: {names or '(ga ada)'}")
        raw = args.get("value")
        if field.get("type") == 23:  # checkbox -> boolean asli
            on = str(raw).strip().lower() in ("1", "true", "ya", "yes", "centang",
                                              "on", "✓")
            return act_set_custom_field(cfg, row, task, field, on,
                                        "✓ dicentang" if on else "✗ tanpa centang")
        items = (field.get("params") or {}).get("listItems") or []
        if items:  # dropdown -> value = id item
            it = next((x for x in items
                       if str(x.get("value")).strip().lower()
                       == str(raw).strip().lower()), None)
            if not it:
                opts = ", ".join(str(x.get("value")) for x in items)
                return f"❌ Nilai '{raw}' ga ada buat {field.get('name')}. Pilihan: {opts}"
            return act_set_custom_field(cfg, row, task, field,
                                        int(it.get("id")), str(it.get("value")))
        return act_set_custom_field(cfg, row, task, field, raw, str(raw))

    return f"❌ Tool '{name}' ga dikenal."


def ai_interpret(cfg, row, text: str, cache=None):
    """Hook AI: teks bebas user -> LLM (via 9router/OpenAI-compatible) -> tool-call
    -> fungsi act_* YANG SAMA dengan tombol -> return teks balasan HTML.
    Return None = AI tidak paham -> pemanggil fallback perilaku default.
    KONTRAK: tidak boleh raise; tidak boleh menyentuh task selain milik `row`
    (dijamin lewat user_owns_task di _ai_run_tool)."""
    if cache is None:
        cache = {}
    if not row or not row["gd_user_id"]:
        return None
    try:
        today = today_str()
        tasks = gd_assigned_tasks(cfg["api_token"], row["gd_user_id"])
        statuses = cached_statuses(cfg, cache)
        cfields = cached_custom_fields(cfg, cache)
        messages = [
            {"role": "system",
             "content": _ai_system_prompt(today, tasks, statuses, cfields)},
            {"role": "user", "content": text},
        ]
        tools = _ai_tools_spec()
        act_results = []
        for _ in range(5):  # batas ronde tool-call
            msg = _ai_chat(cfg, messages, tools)
            calls = msg.get("tool_calls") or []
            if not calls:
                final = (msg.get("content") or "").strip()
                if act_results:
                    return "\n".join(act_results) + (("\n\n" + final) if final else "")
                return final or None
            messages.append({"role": "assistant",
                             "content": msg.get("content"), "tool_calls": calls})
            for c in calls:
                fn = c.get("function") or {}
                try:
                    a = json.loads(fn.get("arguments") or "{}")
                except (ValueError, TypeError):
                    a = {}
                result = _ai_run_tool(cfg, row, cache, fn.get("name"), a)
                act_results.append(result)
                messages.append({"role": "tool", "tool_call_id": c.get("id"),
                                 "content": result})
        return "\n".join(act_results) if act_results else None
    except Exception as e:  # kontrak: AI gagal ga boleh mecahin bot
        print(f"  ! ai_interpret LLM error: {e}", file=sys.stderr)
        return f"⚠️ AI lagi ga bisa dipanggil: {html.escape(str(e)[:150])}"


# skala story point yang dipakai tim (fibonacci) — keputusan user 2026-07-07
STORY_POINT_CHOICES = (1, 3, 5, 8, 13)


def action_kb(task_id) -> dict:
    return {"inline_keyboard": [
        [{"text": "🔄 Status", "callback_data": f"st:{task_id}"},
         {"text": "💬 Comment", "callback_data": f"cm:{task_id}"}],
        [{"text": "🧾 Custom field", "callback_data": f"cf:{task_id}"},
         {"text": "⏱ Report time", "callback_data": f"tm:{task_id}"}],
        [{"text": "🎯 Story points", "callback_data": f"sp:{task_id}"},
         {"text": "📅 Start/End", "callback_data": f"dt:{task_id}"}],
    ]}


def send_action_menu(cfg, chat_id: str, task: dict) -> None:
    tg_send(cfg["bot_token"], chat_id,
            f"✏️ {task_label(task)}\nMau ngapain?",
            reply_markup=action_kb(task.get("id")))


def send_task_card(cfg, cache, chat_id: str, row, ref: str) -> bool:
    """/cari & '✏️ Task lain…': cari task by shortId/id di assigned-tasks MILIK
    user -> kirim kartu detail (label 🟢/🚧 + info) + tombol aksi.
    Return False kalau ga ketemu (pemanggil bisa biarkan pending buat retry)."""
    ref = str(ref).strip().lstrip("#")
    task = user_owns_task(cfg, row["gd_user_id"], ref)
    if not task:
        tg_send(cfg["bot_token"], chat_id,
                f"❌ Task <code>{html.escape(ref[:20])}</code> ga ketemu di "
                f"daftar task KAMU. Cek nomornya (angka #shortId di /tasks), "
                f"coba lagi, atau /batal.")
        return False
    today = today_str()
    task["_starts"] = date_only(task.get("startDate")) == today
    task["_due"] = date_only(task.get("deadline")) == today
    try:
        ready, why = task_ready(cfg, gd_task_detail(cfg["api_token"], task["id"]))
        task["_ready"], task["_why"] = ready, why
    except Exception as e:
        print(f"  ! ready-check {task.get('id')} gagal: {e}", file=sys.stderr)
    body = format_task(task, "🔎", cached_projects(cfg, cache), today)
    tg_send(cfg["bot_token"], chat_id, body + "\n\nMau ngapain? 👇",
            reply_markup=action_kb(task.get("id")))
    print(f"  cari {chat_id}: {task.get('shortId')} ({task.get('id')})")
    return True


def get_recipient(conn, chat_id: str):
    return conn.execute("SELECT * FROM recipients WHERE telegram_chat_id = ?",
                        (chat_id,)).fetchone()


def require_self(cfg, user_key: str):
    """Guard semua fitur task: hanya user ke-link DAN sudah di-approve admin.
    Return (row, None) kalau lolos, (None, pesan_penolakan) kalau tidak.
    SEMUA view/update data GoodDay wajib lewat sini — jangan pernah pakai
    gd_user_id selain milik pengirim (PRD §6.1)."""
    conn = db_connect(cfg["db_path"])
    row = get_recipient(conn, user_key)
    conn.close()
    if not row or not row["gd_user_id"]:
        return None, ("Kamu belum ke-link ke user GoodDay. "
                      "DM aku lalu ketik /daftar ya.")
    if not row["approved"]:
        return None, ("⏳ Pendaftaran kamu masih nunggu approval admin. "
                      "Sabar ya, nanti aku kabari begitu disetujui.")
    return row, None


def notify_admins(cfg, text: str, kb: dict | None = None) -> None:
    for admin_id in cfg["admin_chat_ids"]:
        try:
            tg_send(cfg["bot_token"], admin_id, text, reply_markup=kb)
        except Exception as e:
            print(f"  ! gagal DM admin {admin_id}: {e}", file=sys.stderr)


def start_daftar(cfg, chat_id: str) -> None:
    """Mulai alur pendaftaran: tunggu user ketik ID GoodDay-nya sendiri."""
    conn = db_connect(cfg["db_path"])
    row = get_recipient(conn, chat_id)
    if row and row["gd_user_id"] and not row["approved"]:
        conn.close()
        tg_send(cfg["bot_token"], chat_id,
                "⏳ Pendaftaran kamu masih nunggu approval admin. "
                "Kalau mau ganti ID, tunggu diproses dulu ya.")
        return
    set_pending(conn, chat_id, "daftar")
    conn.close()
    extra = ""
    if row and row["gd_user_id"]:
        extra = (f"\n\nSaat ini kamu ke-link ke "
                 f"<b>{html.escape(row['gd_name'] or row['gd_user_id'])}</b> — "
                 f"ganti ID juga butuh approval admin lagi.")
    tg_send(cfg["bot_token"], chat_id,
            "📝 Ketik <b>ID GoodDay kamu</b> (contoh: <code>yt5Qk4</code>).\n"
            "Belum tahu ID-mu? Lihat daftarnya via /gdusers.\n"
            "Batalkan dengan /batal." + extra)


def handle_daftar_input(cfg, cache, chat_id: str, text: str) -> None:
    """User ngetik ID GoodDay setelah /daftar. Validasi → simpan approved=0 →
    notif admin dengan tombol approve/reject."""
    gd_id = text.strip().split()[0] if text.strip() else ""
    users = cached_gd_users(cfg, cache)
    u = next((x for x in users if str(x.get("id")) == gd_id), None)
    if not u:
        tg_send(cfg["bot_token"], chat_id,
                f"❌ ID <code>{html.escape(gd_id[:40])}</code> ga ketemu di GoodDay.\n"
                "Cek lagi via /gdusers, lalu ketik ulang ID-nya. Atau /batal.")
        return  # pending dibiarkan — user bisa langsung coba lagi
    gd_name = gd_user_label(u)
    conn = db_connect(cfg["db_path"])
    # satu ID GoodDay = satu akun Telegram; mencegah daftar pakai ID orang
    # yang sudah ter-link (termasuk yang masih nunggu approval)
    taken = conn.execute(
        "SELECT telegram_chat_id FROM recipients "
        "WHERE gd_user_id = ? AND telegram_chat_id != ?",
        (gd_id, chat_id)).fetchone()
    if taken:
        conn.close()
        tg_send(cfg["bot_token"], chat_id,
                f"❌ ID <code>{gd_id}</code> (<b>{html.escape(gd_name)}</b>) "
                f"sudah terdaftar di akun Telegram lain.\n"
                f"Kalau itu memang ID kamu, hubungi admin ya. "
                f"Atau ketik ID lain / /batal.")
        print(f"  reg  {chat_id} DITOLAK: {gd_id} sudah dipakai chat "
              f"{taken['telegram_chat_id']}")
        return  # pending dibiarkan
    row = get_recipient(conn, chat_id)
    tg_name = (row["telegram_name"] if row else None) or chat_id
    auto_ok = not cfg["admin_chat_ids"]  # tanpa admin ke-set: langsung approve
    conn.execute(
        "UPDATE recipients SET gd_user_id = ?, gd_name = ?, enabled = 1, "
        "approved = ? WHERE telegram_chat_id = ?",
        (gd_id, gd_name, 1 if auto_ok else 0, chat_id),
    )
    conn.commit()
    clear_pending(conn, chat_id)
    conn.close()
    print(f"  reg  {chat_id} ({tg_name}) -> {gd_id} ({gd_name})"
          f"{' [auto-approve: ADMIN_CHAT_IDS kosong]' if auto_ok else ' [nunggu approval]'}")
    if auto_ok:
        tg_send(cfg["bot_token"], chat_id,
                f"✅ Kamu ke-link ke GoodDay user <b>{html.escape(gd_name)}</b>.\n"
                f"Ini task kamu saat ini 👇")
        send_tasks_now(cfg, cache, chat_id)
        return
    tg_send(cfg["bot_token"], chat_id,
            f"📨 Pendaftaran sebagai <b>{html.escape(gd_name)}</b> "
            f"(<code>{gd_id}</code>) dikirim ke admin.\n"
            f"Aku kabari begitu di-approve ya. ⏳")
    kb = {"inline_keyboard": [[
        {"text": "✅ Approve", "callback_data": f"apr:{chat_id}"},
        {"text": "❌ Tolak", "callback_data": f"rej:{chat_id}"},
    ]]}
    notify_admins(cfg,
                  f"🔔 <b>Pendaftaran baru</b>\n"
                  f"Telegram: <b>{html.escape(str(tg_name))}</b> "
                  f"(<code>{chat_id}</code>)\n"
                  f"Daftar sebagai: <b>{html.escape(gd_name)}</b> "
                  f"(<code>{gd_id}</code>)", kb)


def tasks_kb(tasks, today: str) -> dict:
    """Keyboard ✏️ per task buat view daftar task (DM saja — callback aksi
    memang khusus chat privat). Semua task yang TAMPIL di pesan dapat tombol,
    urut sama dengan nomor di daftar; Telegram max 100 tombol — sisain satu
    baris buat "Task lain…"."""
    todays, overdue, others = split_tasks(list(tasks), today)
    shown = todays + overdue + others[:MAX_OTHERS]
    btns = [{"text": f"✏️ #{t.get('shortId')}",
             "callback_data": f"act:{t.get('id')}"}
            for t in shown[:96] if t.get("id")]
    rows = [btns[i:i + 4] for i in range(0, len(btns), 4)]
    rows.append([{"text": "✏️ Task lain…", "callback_data": "act:other"}])
    return {"inline_keyboard": rows}


def send_tasks_now(cfg, cache, user_key: str, dest_chat_id: str | None = None,
                   thread_id=None) -> None:
    """Kirim task milik <user_key> (chat_id privat = id user Telegram).
    dest_chat_id/thread_id diisi kalau balasannya ke group/topic."""
    dest = dest_chat_id or user_key
    row, err = require_self(cfg, user_key)
    if not row:
        tg_send(cfg["bot_token"], dest, err, thread_id=thread_id)
        return
    today = today_str()
    tasks = gd_assigned_tasks(cfg["api_token"], row["gd_user_id"])
    mark_ready(cfg, tasks, today, cache)
    name = row["gd_name"] or row["telegram_name"] or ""
    msg = build_message(tasks, today,
                        greeting_name=(name.split()[0] if name else ""),
                        projects=cached_projects(cfg, cache))
    # tombol ✏️ cuma di DM — callback aksi memang khusus chat privat
    kb = tasks_kb(tasks, today) if dest == user_key else None
    tg_send(cfg["bot_token"], dest, msg, reply_markup=kb, thread_id=thread_id)


def gdusers_text(cfg, cache) -> str:
    users = cached_gd_users(cfg, cache)
    lines = [f"👥 <b>User GoodDay ({len(users)})</b> — id buat "
             f"DIGEST_GD_USERS / link:", ""]
    for u in users:
        lines.append(f"<code>{u.get('id')}</code> — {html.escape(gd_user_label(u))}")
    return "\n".join(lines)


def send_detail(cfg, cache, chat_id: str, uid: str, thread_id=None) -> None:
    """Kirim daftar lengkap task milik GoodDay user <uid> ke chat/topic."""
    conn = db_connect(cfg["db_path"])
    row = conn.execute("SELECT * FROM recipients WHERE gd_user_id = ?",
                       (uid,)).fetchone()
    conn.close()
    name = (row["gd_name"] if row else None) or ""
    if not name:
        u = next((x for x in cached_gd_users(cfg, cache)
                  if str(x.get("id")) == uid), None)
        name = gd_user_label(u) if u else uid
    tasks = gd_assigned_tasks(cfg["api_token"], uid)
    today = today_str()
    mark_ready(cfg, tasks, today, cache)
    detail = build_message(tasks, today,
                           greeting_name=(name.split()[0] if name else ""),
                           projects=cached_projects(cfg, cache))
    # tombol ✏️ hanya kalau detail dikirim ke DM pemiliknya sendiri;
    # dari group/topic tetap polos (callback aksi khusus chat privat)
    kb = None
    if thread_id is None and row and str(row["telegram_chat_id"]) == str(chat_id):
        kb = tasks_kb(tasks, today)
    tg_send(cfg["bot_token"], chat_id, detail, reply_markup=kb,
            thread_id=thread_id)
    print(f"  dtl  {chat_id}: detail {uid} ({name})")


def send_digest_now(cfg, cache, chat_id: str, thread_id=None) -> None:
    """Bikin & kirim digest terbaru (dipicu /digest dari group/topic atau DM)."""
    if not cfg["digest_enabled"]:
        tg_send(cfg["bot_token"], chat_id,
                "⏸ Fitur digest lagi dinonaktifkan sementara.",
                thread_id=thread_id)
        return
    targets = digest_targets(cfg)
    if not targets:
        tg_send(cfg["bot_token"], chat_id,
                "Belum ada target digest: isi DIGEST_GD_USERS di .env atau "
                "daftar dulu via /daftar.", thread_id=thread_id)
        return
    today = today_str()
    entries = collect_digest_entries(cfg, targets, today, cache)
    tg_send(cfg["bot_token"], chat_id, build_digest(entries, today),
            reply_markup=digest_kb(entries), thread_id=thread_id)


def status_text(conn, chat_id: str) -> str:
    row = get_recipient(conn, chat_id)
    if not row or not row["gd_user_id"]:
        return ("ℹ️ Kamu belum daftar. DM aku lalu ketik /daftar, "
                "nanti tinggal ketik ID GoodDay kamu.")
    if not row["approved"]:
        return (f"⏳ Pendaftaran kamu sebagai "
                f"<b>{html.escape(row['gd_name'] or row['gd_user_id'])}</b> "
                f"(<code>{row['gd_user_id']}</code>) masih nunggu approval admin.")
    aktif = "aktif ✅" if row["enabled"] else "nonaktif 🔕 (ketik /start buat aktif lagi)"
    return (f"ℹ️ Kamu ke-link ke GoodDay user "
            f"<b>{html.escape(row['gd_name'] or row['gd_user_id'])}</b> "
            f"(<code>{row['gd_user_id']}</code>) — reminder {aktif}.")


def handle_pending_input(cfg, cache, conn, chat_id: str, text: str) -> None:
    """Teks bebas saat ada pending action. Format action:
    daftar | other | cari | comment:<taskId> | time:<taskId> |
    dates:<taskId> | cfval:<taskId>:<fieldId>"""
    action = get_pending(conn, chat_id)
    if action == "daftar":
        handle_daftar_input(cfg, cache, chat_id, text)
        return
    row, err = require_self(cfg, chat_id)
    if not row:
        clear_pending(conn, chat_id)
        tg_send(cfg["bot_token"], chat_id, err)
        return

    if action in ("other", "cari"):
        if send_task_card(cfg, cache, chat_id, row, text):
            clear_pending(conn, chat_id)
        # ga ketemu -> pending dibiarkan biar bisa langsung coba lagi
        return

    kind, _, rest = action.partition(":")

    if kind == "time":
        parsed = parse_duration(text)
        if not parsed:
            tg_send(cfg["bot_token"], chat_id,
                    "❌ Format durasi ga kebaca. Contoh: <code>90m</code>, "
                    "<code>1h30m</code>, <code>1.5h benerin bug</code>. Atau /batal.")
            return  # pending dibiarkan
        task = user_owns_task(cfg, row["gd_user_id"], rest)
        clear_pending(conn, chat_id)
        if not task:
            tg_send(cfg["bot_token"], chat_id, "❌ Task-nya udah ga ketemu. Coba /tasks lagi.")
            return
        minutes, note = parsed
        tg_send(cfg["bot_token"], chat_id, act_report_time(cfg, row, task, minutes, note))
        print(f"  time {chat_id}: {task.get('shortId')} {minutes}m")

    elif kind == "comment":
        task = user_owns_task(cfg, row["gd_user_id"], rest)
        clear_pending(conn, chat_id)
        if not task:
            tg_send(cfg["bot_token"], chat_id, "❌ Task-nya udah ga ketemu. Coba /tasks lagi.")
            return
        tg_send(cfg["bot_token"], chat_id, act_comment(cfg, row, task, text))
        print(f"  cmnt {chat_id}: {task.get('shortId')}")

    elif kind == "dates":
        parsed = parse_date_range(text)
        if not parsed:
            tg_send(cfg["bot_token"], chat_id,
                    "❌ Format tanggal ga kebaca. Contoh: <code>10/7 12/7</code> "
                    "atau <code>2026-07-10 2026-07-12</code> (end ≥ start). "
                    "Coba lagi, atau /batal.")
            return  # pending dibiarkan
        task = user_owns_task(cfg, row["gd_user_id"], rest)
        clear_pending(conn, chat_id)
        if not task:
            tg_send(cfg["bot_token"], chat_id, "❌ Task-nya udah ga ketemu. Coba /tasks lagi.")
            return
        sd, ed = parsed
        tg_send(cfg["bot_token"], chat_id, act_set_dates(cfg, row, task, sd, ed))
        print(f"  date {chat_id}: {task.get('shortId')} {sd}..{ed}")

    elif kind == "cfval":
        task_id, _, field_id = rest.partition(":")
        task = user_owns_task(cfg, row["gd_user_id"], task_id)
        field = cached_custom_fields(cfg, cache).get(field_id)
        clear_pending(conn, chat_id)
        if not task or not field:
            tg_send(cfg["bot_token"], chat_id, "❌ Task/field-nya udah ga ketemu. Coba /tasks lagi.")
            return
        val = text.strip()
        tg_send(cfg["bot_token"], chat_id,
                act_set_custom_field(cfg, row, task, field, val, val))
        print(f"  cfld {chat_id}: {task.get('shortId')} {field_id}")

    else:
        clear_pending(conn, chat_id)
        tg_send(cfg["bot_token"], chat_id,
                "Aku ga ngerti perintah itu. Coba /help, atau pakai tombol di bawah 👇",
                reply_markup=MENU_KB)


def handle_message(cfg, cache, msg: dict) -> None:
    chat = msg.get("chat") or {}
    chat_id = str(chat.get("id", ""))
    text = (msg.get("text") or "").strip()
    # "/digest@NamaBot" -> "/digest" (Telegram nambahin @bot di group)
    cmd = text.split()[0].split("@")[0].lower() if text else ""

    if chat.get("type") != "private":
        # di group/topic: identitas diambil dari PENGIRIM pesan (from.id =
        # chat_id DM orang itu). /daftar & /berhenti tetap khusus DM.
        thread_id = msg.get("message_thread_id")
        user_key = str((msg.get("from") or {}).get("id", ""))
        if cmd == "/digest":
            print(f"  dgst group {chat_id}: /digest")
            send_digest_now(cfg, cache, chat_id, thread_id=thread_id)
        elif cmd in ("/tasks", "/task"):
            send_tasks_now(cfg, cache, user_key,
                           dest_chat_id=chat_id, thread_id=thread_id)
        elif cmd == "/status":
            conn = db_connect(cfg["db_path"])
            tg_send(cfg["bot_token"], chat_id, status_text(conn, user_key),
                    thread_id=thread_id)
            conn.close()
        elif cmd == "/gdusers":
            tg_send(cfg["bot_token"], chat_id, gdusers_text(cfg, cache),
                    thread_id=thread_id)
        elif cmd == "/help":
            tg_send(cfg["bot_token"], chat_id, HELP, thread_id=thread_id)
        return

    name = (f"{chat.get('first_name', '')} {chat.get('last_name', '')}".strip()
            or chat.get("username", "") or chat_id)

    conn = db_connect(cfg["db_path"])
    upsert_recipient(conn, chat_id, name)

    if cmd == "/help":
        tg_send(cfg["bot_token"], chat_id, HELP)
    elif cmd in ("/start", "/menu"):
        conn.execute("UPDATE recipients SET enabled = 1 WHERE telegram_chat_id = ?",
                     (chat_id,))
        conn.commit()
        # deep-link dari tombol digest: "/start dtl_<gd_user_id>" -> kirim detail
        parts = text.split(maxsplit=1)
        payload = parts[1].strip() if len(parts) > 1 else ""
        if payload.startswith("dtl_"):
            uid = payload[4:]
            row, err = require_self(cfg, chat_id)
            if not row:
                tg_send(cfg["bot_token"], chat_id, err)
            elif row["gd_user_id"] != uid:
                tg_send(cfg["bot_token"], chat_id,
                        "🔒 Detail itu punya orang lain — kamu cuma bisa "
                        "lihat task milikmu sendiri. Coba /tasks.")
            else:
                send_detail(cfg, cache, chat_id, uid)
        else:
            tg_send(cfg["bot_token"], chat_id, WELCOME, reply_markup=MENU_KB)
    elif cmd == "/daftar":
        start_daftar(cfg, chat_id)
    elif cmd == "/batal":
        pending = get_pending(conn, chat_id)
        clear_pending(conn, chat_id)
        tg_send(cfg["bot_token"], chat_id,
                "✅ Dibatalkan." if pending else "Ga ada yang lagi ditunggu kok. 👍")
    elif cmd in ("/tasks", "/task"):
        send_tasks_now(cfg, cache, chat_id)
    elif cmd in ("/cari", "/find"):
        row, err = require_self(cfg, chat_id)
        if not row:
            tg_send(cfg["bot_token"], chat_id, err)
        else:
            parts = text.split(maxsplit=1)
            if len(parts) > 1 and parts[1].strip():
                send_task_card(cfg, cache, chat_id, row, parts[1])
            else:
                set_pending(conn, chat_id, "cari")
                tg_send(cfg["bot_token"], chat_id,
                        "🔎 Ketik <b>nomor task</b> kamu (#shortId, contoh: "
                        "<code>45567</code>) atau id-nya (<code>ehFPUf</code>).\n"
                        "Batalkan dengan /batal.")
    elif cmd == "/digest":
        send_digest_now(cfg, cache, chat_id)
    elif cmd == "/gdusers":
        tg_send(cfg["bot_token"], chat_id, gdusers_text(cfg, cache))
    elif cmd == "/status":
        tg_send(cfg["bot_token"], chat_id, status_text(conn, chat_id))
    elif cmd in ("/berhenti", "/stop"):
        conn.execute("UPDATE recipients SET enabled = 0 WHERE telegram_chat_id = ?",
                     (chat_id,))
        conn.commit()
        tg_send(cfg["bot_token"], chat_id,
                "🔕 Oke, reminder dimatikan. Ketik /start kalau mau aktif lagi.")
    elif cmd == "/ai":
        # kontrol AI layer — khusus admin (PRD §6.4)
        if chat_id not in cfg["admin_chat_ids"]:
            tg_send(cfg["bot_token"], chat_id, "🔒 /ai khusus admin.")
        else:
            parts = text.split(maxsplit=1)
            arg = parts[1].strip().lower() if len(parts) > 1 else "status"
            if arg in ("on", "off"):
                set_setting(conn, "ai_enabled", "1" if arg == "on" else "0")
            elif arg != "status":
                tg_send(cfg["bot_token"], chat_id, "Pakai: /ai on | off | status")
                arg = None
            if arg:
                on = get_setting(conn, "ai_enabled", "0") == "1"
                info = (f"🤖 AI layer: <b>{'ON' if on else 'OFF'}</b>\n"
                        f"Provider: <code>{cfg['ai_provider'] or '-'}</code> · "
                        f"Model: <code>{cfg['ai_model'] or '-'}</code> · "
                        f"API key: {'terisi ✅' if cfg['ai_api_key'] else 'kosong ❌'}")
                if on and not cfg["ai_api_key"]:
                    info += ("\n⚠️ AI ga akan jalan sampai AI_API_KEY diisi di .env "
                             "(fallback perilaku default tetap aman).")
                if on and cfg["ai_api_key"]:
                    info += ("\nℹ️ Panggilan LLM masih scaffold — semua teks bebas "
                             "tetap fallback ke perilaku default.")
                tg_send(cfg["bot_token"], chat_id, info)
    elif not cmd.startswith("/") and get_pending(conn, chat_id):
        # ada pending action -> teks bebas ini input-nya (ID GD, comment, dll.)
        handle_pending_input(cfg, cache, conn, chat_id, text)
    else:
        # teks bebas tanpa pending: kasih kesempatan AI dulu (kalau on),
        # gagal/mati -> fallback persis perilaku lama (PRD §6.4)
        reply = None
        if not cmd.startswith("/") and ai_enabled(cfg, conn):
            try:
                reply = ai_interpret(cfg, get_recipient(conn, chat_id), text, cache)
            except Exception as e:  # kontrak: AI gagal ga boleh mecahin bot
                print(f"  ! ai_interpret error: {e}", file=sys.stderr)
                reply = None
        if reply:
            tg_send(cfg["bot_token"], chat_id, reply)
        else:
            tg_send(cfg["bot_token"], chat_id,
                    "Aku ga ngerti perintah itu. Coba /help, atau pakai tombol di bawah 👇",
                    reply_markup=MENU_KB)
    conn.close()
    print(f"  msg  {chat_id} ({name}): {text[:60]}")


def handle_callback(cfg, cache, cq: dict) -> None:
    data = cq.get("data") or ""
    msg = cq.get("message") or {}
    chat = msg.get("chat") or {}
    chat_id = str(chat.get("id", ""))
    message_id = msg.get("message_id")
    thread_id = msg.get("message_thread_id")  # topic (kalau group forum)

    # identitas PENGIRIM klik (bukan chat tempat tombolnya) — dipakai buat guard
    from_id = str((cq.get("from") or {}).get("id", ""))

    def answer(text: str | None = None, alert: bool = False):
        payload = {"callback_query_id": cq["id"]}
        if text:
            payload["text"] = text
        if alert:
            payload["show_alert"] = True
        tg_api(cfg["bot_token"], "answerCallbackQuery", payload)

    def edit(text: str, kb: dict | None = None):
        payload = {"chat_id": chat_id, "message_id": message_id, "text": text,
                   "parse_mode": "HTML", "disable_web_page_preview": True}
        if kb:
            payload["reply_markup"] = kb
        try:
            tg_api(cfg["bot_token"], "editMessageText", payload)
        except RuntimeError as e:
            # tombol sama dipencet 2x -> konten ga berubah, aman diabaikan
            if "message is not modified" not in str(e):
                raise

    # "dtl:<gd_user_id>" dari tombol digest (group/topic/DM) — HANYA pemiliknya
    if data.startswith("dtl:"):
        uid = data[4:]
        row, err = require_self(cfg, from_id)
        if not row:
            answer(err, alert=True)
            return
        if row["gd_user_id"] != uid:
            answer("🔒 Detail ini cuma bisa dibuka pemiliknya.", alert=True)
            return
        answer()
        send_detail(cfg, cache, chat_id, uid, thread_id=thread_id)
        return

    # approve/reject pendaftaran — HANYA admin (tombol dikirim ke DM admin)
    if data.startswith(("apr:", "rej:")):
        if from_id not in cfg["admin_chat_ids"]:
            answer("🔒 Khusus admin.", alert=True)
            return
        target = data[4:]
        conn = db_connect(cfg["db_path"])
        row = get_recipient(conn, target)
        if not row or not row["gd_user_id"]:
            conn.close()
            answer("Pendaftaran ini udah ga ada (mungkin dibatalkan).", alert=True)
            edit("⚠️ Pendaftaran udah ga ada / dibatalkan.")
            return
        gd_label = html.escape(row["gd_name"] or row["gd_user_id"])
        tg_label = html.escape(row["telegram_name"] or target)
        if row["approved"]:
            conn.close()
            answer("Udah di-approve sebelumnya.")
            edit(f"✅ <b>{tg_label}</b> → {gd_label} (udah di-approve).")
            return
        if data.startswith("apr:"):
            conn.execute("UPDATE recipients SET approved = 1 "
                         "WHERE telegram_chat_id = ?", (target,))
            conn.commit()
            conn.close()
            answer("✅ Di-approve")
            edit(f"✅ <b>{tg_label}</b> di-approve sebagai <b>{gd_label}</b>.")
            tg_send(cfg["bot_token"], target,
                    f"🎉 Pendaftaran kamu sebagai <b>{gd_label}</b> di-approve!\n"
                    f"Ini task kamu saat ini 👇")
            send_tasks_now(cfg, cache, target)
            print(f"  apr  admin {from_id} approve {target} ({row['gd_user_id']})")
        else:
            conn.execute("UPDATE recipients SET gd_user_id = NULL, gd_name = NULL, "
                         "approved = 1 WHERE telegram_chat_id = ?", (target,))
            conn.commit()
            conn.close()
            answer("❌ Ditolak")
            edit(f"❌ Pendaftaran <b>{tg_label}</b> sebagai <b>{gd_label}</b> ditolak.")
            tg_send(cfg["bot_token"], target,
                    "❌ Pendaftaran kamu ditolak admin. Kalau merasa ini keliru, "
                    "hubungi admin, atau coba /daftar lagi dengan ID yang benar.")
            print(f"  rej  admin {from_id} tolak {target}")
        return

    # sisanya cuma buat chat privat (menu pendaftaran)
    if chat.get("type") != "private":
        answer()
        return

    if data == "noop":
        answer()

    elif data == "menu:daftar":
        answer()
        # pastikan barisnya ada (tombol bisa dipencet sebelum pernah kirim pesan)
        tg_name = (f"{chat.get('first_name', '')} {chat.get('last_name', '')}".strip()
                   or chat.get("username", "") or chat_id)
        conn = db_connect(cfg["db_path"])
        upsert_recipient(conn, chat_id, tg_name)
        conn.close()
        start_daftar(cfg, chat_id)

    elif data == "menu:tasks":
        answer()
        send_tasks_now(cfg, cache, chat_id)

    elif data == "menu:status":
        answer()
        conn = db_connect(cfg["db_path"])
        tg_send(cfg["bot_token"], chat_id, status_text(conn, chat_id))
        conn.close()

    elif data == "menu:help":
        answer()
        tg_send(cfg["bot_token"], chat_id, HELP)

    elif data == "menu:stop":
        conn = db_connect(cfg["db_path"])
        conn.execute("UPDATE recipients SET enabled = 0 WHERE telegram_chat_id = ?",
                     (chat_id,))
        conn.commit()
        conn.close()
        answer("🔕 Reminder dimatikan")
        tg_send(cfg["bot_token"], chat_id,
                "🔕 Oke, reminder dimatikan. Ketik /start kalau mau aktif lagi.")

    elif data.split(":")[0] in ("act", "st", "sts", "cm", "cf", "cff", "cfv",
                                "tm", "sp", "spv", "dt"):
        # alur update task (PRD §6.3). Semua branch: guard require_self +
        # kepemilikan task (user_owns_task cuma cari di assigned-tasks dia)
        row, err = require_self(cfg, from_id)
        if not row:
            answer(err, alert=True)
            return
        if data == "act:other":
            conn = db_connect(cfg["db_path"])
            set_pending(conn, chat_id, "other")
            conn.close()
            answer()
            tg_send(cfg["bot_token"], chat_id,
                    "🔎 Ketik <b>shortId</b> task kamu (angka di judul, contoh: "
                    "<code>45567</code>). Batalkan dengan /batal.")
            return
        prefix, rest = data.split(":", 1)
        parts = rest.split(":")
        task = user_owns_task(cfg, row["gd_user_id"], parts[0])
        if not task:
            answer("🔒 Task itu bukan milikmu (atau udah ga ada).", alert=True)
            return
        tid = task.get("id")

        if prefix == "act":
            answer()
            send_action_menu(cfg, chat_id, task)

        elif prefix == "st":
            btns = [{"text": s.get("name") or "?",
                     "callback_data": f"sts:{tid}:{s.get('id')}"}
                    for s in cached_statuses(cfg, cache)]
            rows_kb = [btns[i:i + 2] for i in range(0, len(btns), 2)]
            answer()
            edit(f"🔄 Pilih status baru buat {task_label(task)}:",
                 {"inline_keyboard": rows_kb})

        elif prefix == "sts":
            sid = parts[1]
            s = next((x for x in cached_statuses(cfg, cache)
                      if str(x.get("id")) == sid), None)
            answer()
            edit(act_update_status(cfg, row, task, sid,
                                   (s.get("name") if s else sid)))

        elif prefix == "cm":
            conn = db_connect(cfg["db_path"])
            set_pending(conn, chat_id, f"comment:{tid}")
            conn.close()
            answer()
            tg_send(cfg["bot_token"], chat_id,
                    f"💬 Ketik comment buat {task_label(task)}. "
                    f"Batalkan dengan /batal.")

        elif prefix == "tm":
            conn = db_connect(cfg["db_path"])
            set_pending(conn, chat_id, f"time:{tid}")
            conn.close()
            answer()
            tg_send(cfg["bot_token"], chat_id,
                    f"⏱ Ketik durasi kerja buat {task_label(task)} "
                    f"(+ catatan opsional).\nContoh: <code>90m</code> · "
                    f"<code>1h30m</code> · <code>1.5h benerin bug</code>\n"
                    f"Batalkan dengan /batal.")

        elif prefix == "sp":
            btns = [{"text": str(p), "callback_data": f"spv:{tid}:{p}"}
                    for p in STORY_POINT_CHOICES]
            kb = {"inline_keyboard": [btns,
                  [{"text": "✖️ Hapus", "callback_data": f"spv:{tid}:-"}]]}
            answer()
            edit(f"🎯 Pilih <b>story points</b> buat {task_label(task)}:", kb)

        elif prefix == "spv":
            raw = parts[1]
            points = None if raw == "-" else int(raw)
            answer()
            edit(act_set_story_points(cfg, row, task, points))
            print(f"  sp   {chat_id}: {task.get('shortId')} = {points}")

        elif prefix == "dt":
            conn = db_connect(cfg["db_path"])
            set_pending(conn, chat_id, f"dates:{tid}")
            conn.close()
            answer()
            tg_send(cfg["bot_token"], chat_id,
                    f"📅 Ketik <b>start &amp; end date</b> buat {task_label(task)}.\n"
                    f"Contoh: <code>10/7 12/7</code> · "
                    f"<code>2026-07-10 2026-07-12</code> · "
                    f"<code>10/7</code> (satu tanggal = start=end).\n"
                    f"Batalkan dengan /batal.")

        elif prefix == "cf":
            fields = cached_custom_fields(cfg, cache)
            if not fields:
                answer("Ga ada custom field yang bisa diubah.", alert=True)
                return
            btns = [{"text": f.get("name") or fid,
                     "callback_data": f"cff:{tid}:{fid}"}
                    for fid, f in fields.items()]
            rows_kb = [btns[i:i + 2] for i in range(0, len(btns), 2)]
            answer()
            edit(f"🧾 Pilih field yang mau diubah di {task_label(task)}:",
                 {"inline_keyboard": rows_kb})

        elif prefix == "cff":
            fid = parts[1]
            field = cached_custom_fields(cfg, cache).get(fid)
            if not field:
                answer("Field ga ketemu / di luar whitelist.", alert=True)
                return
            fname = html.escape(field.get("name") or fid)
            items = (field.get("params") or {}).get("listItems") or []
            if field.get("type") == 23:  # checkbox — API butuh boolean asli
                kb = {"inline_keyboard": [[
                    {"text": "✅ Centang", "callback_data": f"cfv:{tid}:{fid}:1"},
                    {"text": "⬜️ Hapus centang", "callback_data": f"cfv:{tid}:{fid}:0"},
                ]]}
                answer()
                edit(f"🧾 <b>{fname}</b> di {task_label(task)}:", kb)
            elif items:  # dropdown — value = id item (angka)
                btns = [{"text": str(it.get("value")),
                         "callback_data": f"cfv:{tid}:{fid}:{it.get('id')}"}
                        for it in items]
                rows_kb = [btns[i:i + 2] for i in range(0, len(btns), 2)]
                rows_kb.append([{"text": "✖️ Kosongkan",
                                 "callback_data": f"cfv:{tid}:{fid}:-"}])
                answer()
                edit(f"🧾 Pilih nilai <b>{fname}</b> buat {task_label(task)}:",
                     {"inline_keyboard": rows_kb})
            else:  # teks/angka/tanggal — minta diketik
                conn = db_connect(cfg["db_path"])
                set_pending(conn, chat_id, f"cfval:{tid}:{fid}")
                conn.close()
                answer()
                tg_send(cfg["bot_token"], chat_id,
                        f"🧾 Ketik nilai baru buat <b>{fname}</b> di "
                        f"{task_label(task)}. Batalkan dengan /batal.")

        elif prefix == "cfv":
            fid, raw = parts[1], parts[2]
            field = cached_custom_fields(cfg, cache).get(fid)
            if not field:
                answer("Field ga ketemu / di luar whitelist.", alert=True)
                return
            if field.get("type") == 23:
                value = bool(int(raw))
                vlabel = "✓ dicentang" if value else "✗ tanpa centang"
            elif raw == "-":
                value, vlabel = None, "(kosong)"
            else:
                value = int(raw) if raw.isdigit() else raw
                items = (field.get("params") or {}).get("listItems") or []
                it = next((x for x in items if str(x.get("id")) == raw), None)
                vlabel = str(it.get("value")) if it else raw
            answer()
            edit(act_set_custom_field(cfg, row, task, field, value, vlabel))

    else:
        answer()


def cmd_bot(cfg, args):
    """Long-polling bot: /start -> menu -> user pilih namanya sendiri."""
    if not cfg["bot_token"]:
        raise SystemExit("TELEGRAM_BOT_TOKEN belum di-set.")
    if not cfg["api_token"]:
        raise SystemExit("GOODDAY_API_TOKEN belum di-set.")
    init_db(cfg["db_path"])  # idempotent, biar bot bisa jalan duluan

    me = tg_api(cfg["bot_token"], "getMe")
    print(f"Bot @{me.get('username')} jalan (long-polling, Ctrl+C buat stop).")
    print("Suruh orang-orang /start ke bot itu buat daftar sendiri.\n")

    # daftar perintah biar muncul di menu "/" Telegram
    try:
        dm_cmds = [
            {"command": "daftar", "description": "Daftar pakai ID GoodDay kamu (approval admin)"},
            {"command": "tasks", "description": "Task aktif kamu (🔥 = urgent)"},
            {"command": "cari", "description": "Cari task kamu by nomor (#shortId)"},
            {"command": "digest", "description": "Ringkasan tim: hari ini + telat"},
            {"command": "gdusers", "description": "Daftar user GoodDay + id"},
            {"command": "status", "description": "Status pendaftaran kamu"},
            {"command": "batal", "description": "Batalkan input yang lagi ditunggu"},
            {"command": "berhenti", "description": "Stop reminder pagi"},
            {"command": "help", "description": "Bantuan & daftar perintah"},
        ]
        grp_cmds = [
            {"command": "digest", "description": "Ringkasan tim: hari ini + telat"},
            {"command": "tasks", "description": "Task aktif kamu (🔥 = urgent)"},
            {"command": "status", "description": "Status pendaftaran kamu"},
            {"command": "gdusers", "description": "Daftar user GoodDay + id"},
            {"command": "help", "description": "Bantuan & daftar perintah"},
        ]
        if not cfg["digest_enabled"]:  # sembunyiin dari menu selama dimatikan
            dm_cmds = [c for c in dm_cmds if c["command"] != "digest"]
            grp_cmds = [c for c in grp_cmds if c["command"] != "digest"]
        tg_api(cfg["bot_token"], "setMyCommands", {
            "commands": dm_cmds, "scope": {"type": "all_private_chats"}})
        tg_api(cfg["bot_token"], "setMyCommands", {
            "commands": grp_cmds, "scope": {"type": "all_group_chats"}})
    except Exception as e:
        print(f"  ! setMyCommands gagal (lanjut aja): {e}", file=sys.stderr)

    cache: dict = {}
    offset = None
    while True:
        try:
            updates = tg_get_updates(cfg["bot_token"], offset, POLL_TIMEOUT)
        except KeyboardInterrupt:
            print("\nBot berhenti.")
            return
        except Exception as e:
            print(f"  ! polling error: {e} — retry 5 detik", file=sys.stderr)
            time.sleep(5)
            continue
        for up in updates:
            offset = up["update_id"] + 1
            try:
                if "message" in up:
                    handle_message(cfg, cache, up["message"])
                elif "callback_query" in up:
                    handle_callback(cfg, cache, up["callback_query"])
            except Exception as e:
                print(f"  ! error proses update: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(description="GoodDay daily reminder -> Telegram (multi-user)")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="Bikin/inisialisasi database SQLite")
    sub.add_parser("gd-users", help="List user GoodDay + user_id-nya")
    sub.add_parser("register", help="Simpen chat_id dari yang udah /start ke bot")
    sub.add_parser("users", help="List penerima di database")
    sub.add_parser("bot", help="Jalankan bot interaktif — user daftar sendiri via menu")

    pl = sub.add_parser("link", help="Petakan chat_id ke GoodDay user_id (manual)")
    pl.add_argument("chat_id")
    pl.add_argument("gd_user_id")
    pl.add_argument("--name", default=None, help="Nama tampilan (opsional)")

    pe = sub.add_parser("enable", help="Aktifkan penerima")
    pe.add_argument("chat_id")
    pd = sub.add_parser("disable", help="Nonaktifkan penerima")
    pd.add_argument("chat_id")

    pr = sub.add_parser("run", help="Kirim reminder ke semua penerima aktif")
    pr.add_argument("--dry-run", action="store_true", help="Tampilkan aja, ga kirim")
    pr.add_argument("--skip-empty", action="store_true",
                    help="Jangan kirim ke user tanpa task hari ini / telat")

    pg = sub.add_parser("digest",
                        help="Kirim ringkasan tim (hari ini + telat) ke group/topic")
    pg.add_argument("--dry-run", action="store_true", help="Tampilkan aja, ga kirim")
    pg.add_argument("--chat-id", default=None,
                    help="Chat id group tujuan (default: DIGEST_CHAT_ID di .env)")
    pg.add_argument("--topic-id", default=None,
                    help="Id topic / message_thread_id (default: DIGEST_TOPIC_ID di .env)")
    pg.add_argument("--no-buttons", action="store_true",
                    help="Tanpa tombol Detail (pakai ini kalau bot pengirim ga "
                         "menjalankan mode `bot`)")
    return p


def main():
    args = build_parser().parse_args()
    cfg = get_config()

    dispatch = {
        "init-db": cmd_init_db,
        "gd-users": cmd_gd_users,
        "register": cmd_register,
        "users": cmd_users,
        "link": cmd_link,
        "run": cmd_run,
        "digest": cmd_digest,
        "bot": cmd_bot,
    }
    if args.command == "enable":
        return cmd_toggle(cfg, args, 1)
    if args.command == "disable":
        return cmd_toggle(cfg, args, 0)
    dispatch[args.command](cfg, args)


if __name__ == "__main__":
    main()
