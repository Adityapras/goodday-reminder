#!/usr/bin/env python3
"""
try_local.py — Coba GoodDay reminder di LOKAL tanpa token / tanpa internet.
==========================================================================

Ini harness DEMO. Dia:
  - pakai database SQLite sementara (ga ganggu reminders.db asli)
  - mengganti panggilan GoodDay API dengan DATA PALSU
  - mengganti "kirim ke Telegram" jadi cuma nge-print ke layar

Tujuannya biar kamu lihat seluruh alur + format pesan JALAN dulu, sebelum
isi token beneran. Ga ada koneksi keluar sama sekali.

Jalankan:
    python try_local.py
"""

import os
import sys
import tempfile
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "goodday_reminder.py"

if not TARGET.exists():
    sys.exit("goodday_reminder.py ga ketemu di folder yang sama.")

# import goodday_reminder.py sebagai modul
spec = importlib.util.spec_from_file_location("gd", str(TARGET))
gd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gd)


# ---- 1. Siapkan data GoodDay PALSU -----------------------------------------
today = gd.today_str()

import datetime as _dt
_today = _dt.date.fromisoformat(today)
kemarin = (_today - _dt.timedelta(days=3)).isoformat()
besok = (_today + _dt.timedelta(days=2)).isoformat()
jauh = (_today + _dt.timedelta(days=30)).isoformat()   # di luar jendela -> disembunyikan
lama = (_today - _dt.timedelta(days=30)).isoformat()   # telat >7 hari -> disembunyikan

FAKE_TASKS = {
    "GD-USER-ADITYA": [
        {"id": "Tk412", "name": "Fix bug billing cron multi-tenant", "shortId": "412",
         "projectId": "P1", "priority": 100,
         "status": {"name": "In Progress"}, "deadline": today},
        {"id": "Tk418", "name": "Review PR modul KDS", "shortId": "418",
         "projectId": "P2", "priority": 8,
         "status": {"name": "New"}, "startDate": today},
        {"id": "Tk300", "name": "Task telat (deadline lewat 3 hari)", "shortId": "300",
         "projectId": "P1", "priority": 5,
         "status": {"name": "In Progress"}, "deadline": kemarin},
        {"id": "Tk320", "name": "Task minggu depan (akan jalan 7 hari ke depan)",
         "shortId": "320", "projectId": "P2", "priority": 5,
         "status": {"name": "New"}, "deadline": besok},
        {"id": "Tk900", "name": "Task jauh di depan (disembunyikan)", "shortId": "900",
         "projectId": "P1", "priority": 5,
         "status": {"name": "New"}, "startDate": jauh, "deadline": jauh},
        {"id": "Tk901", "name": "Task telat lama >7 hari (disembunyikan)",
         "shortId": "901", "projectId": "P1", "priority": 5,
         "status": {"name": "In Progress"}, "deadline": lama},
    ],
    "GD-USER-BUDI": [
        {"id": "Tk501", "name": "Deploy staging QR ordering", "shortId": "501",
         "projectId": "P1", "priority": 50, "status": {"name": "Not started"},
         "startDate": today, "deadline": today},
    ],
    "GD-USER-CITRA": [],  # ga ada task sama sekali
}

FAKE_PROJECTS = {"P1": "Opsifin Core", "P2": "Opsifin KDS"}

# detail per task -> buat label 🟢 SIAP / 🚧 BELUM SIAP (4 syarat PRD §6.2):
# sudah di-story-point (task TIDAK bertag 'need-story-point'), start+end date
# terisi, Product Stream=Opsifin (gLu7pt=8), Delivery Task dicentang (ZJVsCT=true)
FAKE_DETAILS = {
    "Tk412": {"id": "Tk412", "startDate": today, "endDate": today,
              "customFieldsData": {"gLu7pt": 8, "ZJVsCT": True}},   # -> 🟢
    "Tk418": {"id": "Tk418", "startDate": today, "endDate": None,
              "customFieldsData": {"gLu7pt": 2, "ZJVsCT": False}},  # -> 🚧
    "Tk300": {"id": "Tk300", "startDate": kemarin, "endDate": kemarin,
              "customFieldsData": {"gLu7pt": 8, "ZJVsCT": False}},  # -> 🚧
    "Tk501": {"id": "Tk501", "startDate": today, "endDate": today,
              "customFieldsData": {"gLu7pt": 8, "ZJVsCT": True}},   # -> 🟢
}

# task yang MASIH bertag 'need-story-point' (dari GET /tag/{id}/tasks):
# Tk418 belum di-story-point -> ikut jadi alasan 🚧
FAKE_NEED_SP = [{"id": "Tk418"}]

FAKE_GD_USERS = [
    {"id": "GD-USER-ADITYA", "firstName": "Aditya", "lastName": "P"},
    {"id": "GD-USER-BUDI", "firstName": "Budi", "lastName": "S"},
    {"id": "GD-USER-CITRA", "firstName": "Citra", "lastName": "W"},
]

# ---- 2. Ganti fungsi jaringan dengan versi PALSU ---------------------------
gd.gd_assigned_tasks = lambda api_token, user_id: FAKE_TASKS.get(user_id, [])
gd.gd_project_map = lambda api_token: FAKE_PROJECTS
gd.gd_task_detail = lambda api_token, task_id: FAKE_DETAILS[task_id]
gd.gd_tag_tasks = lambda api_token, tag_id: FAKE_NEED_SP
gd.gd_list_users = lambda api_token: FAKE_GD_USERS
gd.tg_api = lambda bot_token, method, payload=None, timeout=None: {}

def fake_send(bot_token, chat_id, text, reply_markup=None, thread_id=None):
    print("\n" + "─" * 58)
    print(f"📨 (SIMULASI) kirim ke Telegram chat_id={chat_id}")
    print("─" * 58)
    # tampilkan seperti aslinya, tag HTML dibersihin biar kebaca di terminal
    print(gd.strip_html(text))

gd.tg_send = fake_send


# ---- 3. DB sementara + argumen dummy ---------------------------------------
class NS:
    def __init__(self, **kw): self.__dict__.update(kw)

tmpdir = tempfile.mkdtemp()
cfg = {
    "api_token": "DUMMY",
    "bot_token": "DUMMY",
    "db_path": os.path.join(tmpdir, "demo.db"),
    "digest_chat_id": "", "digest_topic_id": "", "digest_gd_users": "",
    "digest_bot_token": "",
    "admin_chat_ids": ["99999999"],  # admin palsu buat demo approval
    "ready_stream_field": "gLu7pt", "ready_stream_value": "8",
    "ready_delivery_field": "ZJVsCT", "ready_need_sp_tag": "PU7NBR",
    "edit_custom_fields": ["gLu7pt", "ZJVsCT"],
    "ai_provider": "", "ai_api_key": "", "ai_model": "", "ai_base_url": "",
}


def step(title):
    print("\n" + "=" * 62)
    print(f"  {title}")
    print("=" * 62)


# ---- 4. Jalankan alur lengkap ----------------------------------------------
step("1) init-db — bikin database")
gd.cmd_init_db(cfg, NS())

cache = {}


def priv_msg(chat_id, name, text):
    """Simulasikan pesan DM ke bot."""
    gd.handle_message(cfg, cache, {
        "chat": {"id": int(chat_id), "type": "private", "first_name": name},
        "text": text,
    })


def admin_click(data):
    """Simulasikan admin memencet tombol inline (approve/tolak)."""
    gd.handle_callback(cfg, cache, {
        "id": "demo-cq", "from": {"id": 99999999}, "data": data,
        "message": {"message_id": 1,
                    "chat": {"id": 99999999, "type": "private"}},
    })


step("2) daftar mandiri — /daftar lalu ketik ID GoodDay sendiri (alur baru)")
for cid, nm, gid in [("11111111", "Aditya", "GD-USER-ADITYA"),
                     ("22222222", "Budi", "GD-USER-BUDI"),
                     ("33333333", "Citra", "GD-USER-CITRA")]:
    priv_msg(cid, nm, "/daftar")
    priv_msg(cid, nm, gid)  # notif approval otomatis "terkirim" ke admin 99999999

step("3) approval admin — pencet tombol ✅ Approve (disimulasikan)")
for cid in ("11111111", "22222222", "33333333"):
    admin_click(f"apr:{cid}")

step("4) users — cek daftar penerima")
gd.cmd_users(cfg, NS())

step("5) run --dry-run — pratinjau, ga 'dikirim'")
gd.cmd_run(cfg, NS(dry_run=True, skip_empty=False))

step("6) run — 'kirim' beneran (disimulasikan ke layar)")
gd.cmd_run(cfg, NS(dry_run=False, skip_empty=False))

step("7) run --skip-empty — Citra (tanpa task urgent) dilewati")
gd.cmd_run(cfg, NS(dry_run=False, skip_empty=True))

print("\n\n✅ Demo selesai. Semua ini pakai data palsu & tanpa internet.")
print("   Kalau outputnya udah sesuai, tinggal isi .env dengan token asli")
print("   lalu jalankan goodday_reminder.py langsung (bukan file ini).")
