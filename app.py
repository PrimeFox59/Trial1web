import streamlit as st
import sqlite3
import pandas as pd
import hashlib
from datetime import datetime, timedelta, date
import json
from dateutil.relativedelta import relativedelta
import altair as alt
import io
import math
import time
import os
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload

# Google Drive Config
SCOPES = ["https://www.googleapis.com/auth/drive"]
FOLDER_ID_DEFAULT = "19pvCnUBhriYQdx8zBvY_3_BXvsjrK6eD"

DB_PATH = "project_charter.db"

# ---------------------------------
# Configuration Flags
# ---------------------------------
# Dapat diubah jika ingin menonaktifkan pengaruh timeline terhadap skor agregasi
ENABLE_TIMELINE_WEIGHTING = True
st.set_page_config(layout="wide", page_icon="icon.png", page_title="Project Charter")

# -------------------------
# Utility: DB initialization
# -------------------------
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    # users
    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        email TEXT UNIQUE,
        password_hash TEXT,
        role TEXT DEFAULT 'user', -- admin / user
        department TEXT,
        approved INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    # departments
    c.execute("""
    CREATE TABLE IF NOT EXISTS departments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE
    )""")
    # app_settings (key-value config)
    c.execute("""
    CREATE TABLE IF NOT EXISTS app_settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )""")
    conn.commit()

    # Seed default settings (idempotent)
    try:
        c.execute("INSERT OR IGNORE INTO app_settings (key, value) VALUES ('auto_restore_enabled','true')")
        # Could add future defaults here
        conn.commit()
    except Exception:
        pass

    # ensure at least one admin exists (seed)
    c.execute("SELECT COUNT(*) as cnt FROM users")
    row = c.fetchone()
    if row['cnt'] == 0:
        # Create default users
        users_to_seed = [
            {"name": "Admin", "email": "admin", "password": "admin123", "role": "admin", "department": "Management", "approved": 1},
            {"name": "Rendy", "email": "rendy", "password": "pass123", "role": "user", "department": "IT", "approved": 1},
            {"name": "Ammar", "email": "ammar", "password": "pass123", "role": "user", "department": "IT", "approved": 1},
            {"name": "Budi", "email": "budi", "password": "pass123", "role": "user", "department": "Humas", "approved": 1},
            {"name": "Dita", "email": "dita", "password": "pass123", "role": "user", "department": "Finance", "approved": 1},
        ]
        
        for user in users_to_seed:
            try:
                hashed_pw = hash_password(user['password'])
                c.execute("INSERT INTO users (name, email, password_hash, role, department, approved) VALUES (?,?,?,?,?,?)",
                          (user['name'], user['email'], hashed_pw, user['role'], user['department'], user['approved']))
            except sqlite3.IntegrityError:
                # User might already exist, skip.
                pass
        
        # seed some departments
        for d in ["Humas", "IT", "Operations", "Finance", "Management"]:
            try:
                c.execute("INSERT INTO departments (name) VALUES (?)", (d,))
            except sqlite3.IntegrityError:
                pass
        
        conn.commit()
    conn.close()

# -------------------------
# Helper functions
# -------------------------
def hash_password(pw: str):
    return hashlib.sha256(pw.encode()).hexdigest()

def verify_password(pw: str, h: str):
    return hash_password(pw) == h

def current_user():
    return st.session_state.get("user")

def login_user(user_row):
    st.session_state["user"] = dict(user_row)

def logout_user():
    if "user" in st.session_state:
        del st.session_state["user"]
    st.session_state.page = "Authentication" 

def fetchall(query, params=()):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(query, params)
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def fetchone(query, params=()):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(query, params)
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None

def execute(query, params=()):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(query, params)
    conn.commit()
    last = cur.lastrowid
    conn.close()
    return last

def get_setting(key, default=None):
    row = fetchone("SELECT value FROM app_settings WHERE key=?", (key,))
    if not row:
        return default
    return row.get('value')

def set_setting(key, value):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    conn.commit()
    conn.close()

# -------------------------
# Backup helpers
# -------------------------
def perform_backup(service, folder_id=FOLDER_ID_DEFAULT):
    """Create a timestamped backup of the SQLite DB to Google Drive and record in backup_log.

    Returns (success: bool, info_message: str)
    """
    if not os.path.exists(DB_PATH):
        return False, f"Database '{DB_PATH}' tidak ditemukan." 
    ts = time.strftime('%Y%m%d_%H%M%S')
    backup_name = f"auto_backup_{ts}.sqlite"
    try:
        with open(DB_PATH, 'rb') as f:
            data = f.read()
        fid = upload_bytes(service, folder_id, backup_name, data, mimetype='application/x-sqlite3')
        if fid:
            execute("INSERT INTO backup_log (file_name, drive_file_id, status, message) VALUES (?,?,?,?)",
                    (backup_name, fid, 'SUCCESS', ''))
            return True, f"Backup sukses: {backup_name} (ID: {fid})"
        else:
            execute("INSERT INTO backup_log (file_name, drive_file_id, status, message) VALUES (?,?,?,?)",
                    (backup_name, None, 'FAILED', 'Upload gagal'))
            return False, "Upload Drive gagal." 
    except Exception as e:
        execute("INSERT INTO backup_log (file_name, drive_file_id, status, message) VALUES (?,?,?,?)",
                (backup_name, None, 'FAILED', str(e)))
        return False, f"Gagal backup: {e}" 

def auto_daily_backup(service, folder_id=FOLDER_ID_DEFAULT):
    """Run once per session start (post-login). If last SUCCESS backup is not today -> perform one."""
    # Cek backup sukses terakhir
    row = fetchone("SELECT backup_time FROM backup_log WHERE status='SUCCESS' ORDER BY id DESC LIMIT 1")
    today_str = date.today().isoformat()
    if row:
        try:
            last_date = row['backup_time'][:10]
            if last_date == today_str:
                return False, "Backup harian sudah ada hari ini." 
        except Exception:
            pass
    # Jalankan backup
    ok, msg = perform_backup(service, folder_id)
    return ok, msg


DEFAULT_SCHEDULE_SLOTS = [
    {"start": 6,  "end": 12, "name": "slot_morning"},
    {"start": 12, "end": 18, "name": "slot_afternoon"},
    {"start": 18, "end": 23, "name": "slot_evening"},
    {"start": 23, "end": 6,  "name": "slot_night"},  # wrap
]

def _validate_slot_struct(slots):
    if not isinstance(slots, list) or not slots:
        return False
    names = set()
    for s in slots:
        if not isinstance(s, dict):
            return False
        if 'start' not in s or 'end' not in s or 'name' not in s:
            return False
        try:
            st_h = int(s['start']); en_h = int(s['end'])
        except Exception:
            return False
        if not (0 <= st_h <= 23 and 0 <= en_h <= 23):
            return False
        if st_h == en_h:  # zero-length not allowed
            return False
        nm = str(s['name']).strip()
        if not nm or nm in names:
            return False
        names.add(nm)
    return True

def get_schedule_slots():
    raw = get_setting('scheduled_backup_slots_json')
    if raw:
        try:
            slots = json.loads(raw)
            if _validate_slot_struct(slots):
                # Normalize shape (int casting & strip)
                norm = []
                for s in slots:
                    norm.append({
                        'start': int(s['start']),
                        'end': int(s['end']),
                        'name': str(s['name']).strip()
                    })
                return norm
        except Exception:
            pass
    return DEFAULT_SCHEDULE_SLOTS

def determine_slot(now_local):
    h = now_local.hour
    for s in get_schedule_slots():
        st_h = s['start']; en_h = s['end']
        if st_h < en_h:
            if st_h <= h < en_h:
                return s['name']
        else:  # wrap
            if h >= st_h or h < en_h:
                return s['name']
    return 'slot_unknown'

def check_scheduled_backup(service, folder_id=FOLDER_ID_DEFAULT):
    """If scheduling enabled, ensure one backup per defined slot. Overwrite single file name each time.
    Settings keys used:
      scheduled_backup_enabled: 'true'/'false'
      scheduled_backup_filename: base file name (default 'scheduled_backup.sqlite')
      scheduled_backup_last_slot: last slot string done
    """
    enabled = get_setting('scheduled_backup_enabled', 'false') == 'true'
    if not enabled:
        return False, 'Scheduled backup disabled'
    base_name = get_setting('scheduled_backup_filename', 'scheduled_backup.sqlite') or 'scheduled_backup.sqlite'
    # Determine local time (assume server already GMT+7 or adjust here if needed)
    now_local = datetime.now()  # If server timezone != GMT+7 -> adjust with timedelta(hours=offset)
    slot = determine_slot(now_local)
    if slot == 'slot_unknown':
        return False, 'Outside defined slots'
    last_slot_done = get_setting('scheduled_backup_last_slot')
    today_tag = date.today().isoformat()
    last_slot_date = get_setting('scheduled_backup_last_date')
    composite_last = f"{last_slot_date}:{last_slot_done}" if last_slot_done and last_slot_date else None
    composite_now = f"{today_tag}:{slot}"
    if composite_last == composite_now:
        return False, 'Slot already backed up'
    # Do backup overwrite single file
    if not os.path.exists(DB_PATH):
        return False, 'DB missing'
    try:
        with open(DB_PATH,'rb') as f:
            data = f.read()
        fid = upload_or_replace(service, folder_id, base_name, data, mimetype='application/x-sqlite3')
        if fid:
            set_setting('scheduled_backup_last_slot', slot)
            set_setting('scheduled_backup_last_date', today_tag)
            execute("INSERT INTO backup_log (file_name, drive_file_id, status, message) VALUES (?,?,?,?)",
                    (base_name, fid, 'SUCCESS', f'scheduled {slot}'))
            return True, f'Scheduled backup OK ({slot}) -> {base_name}'
        else:
            execute("INSERT INTO backup_log (file_name, drive_file_id, status, message) VALUES (?,?,?,?)",
                    (base_name, None, 'FAILED', f'scheduled {slot} upload error'))
            return False, 'Upload failed'
    except Exception as e:
        execute("INSERT INTO backup_log (file_name, drive_file_id, status, message) VALUES (?,?,?,?)",
                (base_name, None, 'FAILED', f'scheduled {slot} {e}'))
        return False, f'Error {e}'

# -------------------------
# Auto-restore after autosleep reset detection
# -------------------------
def _is_probably_fresh_seed_db():
    """Heuristik: anggap DB masih fresh bila belum ada project, user <=5 (seed), dan backup_log kosong."""
    try:
        proj_cnt = fetchone("SELECT COUNT(*) c FROM projects")['c']
        if proj_cnt > 0:
            return False
        user_cnt = fetchone("SELECT COUNT(*) c FROM users")['c']
        if user_cnt > 5:
            return False
        bkup_cnt = fetchone("SELECT COUNT(*) c FROM backup_log")['c']
        if bkup_cnt > 0:
            return False
        return True
    except Exception:
        return False

def _pick_latest_drive_backup_file(service, folder_id):
    try:
        files = list_files_in_folder(service, folder_id)
    except Exception:
        return None
    if not files:
        return None
    candidates = [f for f in files if f.get('name','').endswith('.sqlite') or f.get('name','').endswith('.db')]
    if not candidates:
        return None
    try:
        candidates.sort(key=lambda x: x.get('modifiedTime',''), reverse=True)
    except Exception:
        pass
    return candidates[0]

def attempt_auto_restore_if_seed(service, folder_id=FOLDER_ID_DEFAULT):
    """Jika diaktifkan & terdeteksi DB fresh, restore otomatis dari backup Drive terbaru sekali per sesi."""
    if get_setting('auto_restore_enabled', 'true') != 'true':
        return False, 'Auto-restore disabled'
    if st.session_state.get('auto_restore_attempted'):
        return False, 'Already attempted'
    st.session_state['auto_restore_attempted'] = True
    if not _is_probably_fresh_seed_db():
        return False, 'DB not fresh'
    latest = _pick_latest_drive_backup_file(service, folder_id)
    if not latest:
        return False, 'No backup found'
    fid = latest.get('id'); fname = latest.get('name')
    try:
        data = download_file_bytes(service, fid)
        if not data.startswith(b'SQLite format 3\x00'):
            return False, 'Invalid sqlite header'
        with open(DB_PATH, 'wb') as f:
            f.write(data)
        set_setting('auto_restore_last_file', fname)
        set_setting('auto_restore_last_time', datetime.utcnow().isoformat())
        return True, f'Restored from {fname}'
    except Exception as e:
        return False, f'Restore failed: {e}'

# -------------------------
# Google Drive Helper Functions
# -------------------------
def build_drive_service():
    """Load credentials from Streamlit secrets and build Drive service."""
    try:
        creds_dict = st.secrets["service_account"]
    except Exception:
        st.error("Secrets 'service_account' tidak ditemukan. Tambahkan di Streamlit Cloud.")
        st.stop()
    creds = service_account.Credentials.from_service_account_info(dict(creds_dict), scopes=SCOPES)
    service = build("drive", "v3", credentials=creds)
    return service, creds.service_account_email

def list_files_in_folder(service, folder_id):
    results = []
    page_token = None
    query = f"'{folder_id}' in parents and trashed = false"
    while True:
        resp = service.files().list(
            q=query,
            spaces="drive",
            fields="nextPageToken, files(id, name, mimeType, createdTime, modifiedTime, size)",
            pageToken=page_token,
            pageSize=200,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        results.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return results

def upload_bytes(service, folder_id, name, data_bytes, mimetype="application/octet-stream"):
    media = MediaIoBaseUpload(io.BytesIO(data_bytes), mimetype=mimetype, resumable=True)
    file_metadata = {"name": name, "parents": [folder_id]}
    try:
        created = service.files().create(body=file_metadata, media_body=media, fields="id", supportsAllDrives=True).execute()
        return created.get("id")
    except Exception as e:
        err_text = str(e)
        if 'File not found' in err_text:
            st.error("Folder tidak ditemukan atau akses ditolak. Pastikan Folder ID benar dan folder telah dishare ke service account.")
        elif 'storageQuotaExceeded' in err_text:
            st.error("Kuota penyimpanan Google Drive penuh untuk service account ini.")
        else:
            st.error(f"Gagal upload: {err_text}")
        return None

def upload_or_replace(service, folder_id, name, data_bytes, mimetype="application/octet-stream"):
    """Find a file with same name in folder; if exists update, else create. Return file id or None."""
    try:
        query = f"name='{name}' and '{folder_id}' in parents and trashed=false"
        resp = service.files().list(q=query, spaces='drive', fields='files(id, name)', supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
        existing = resp.get('files', [])
        media = MediaIoBaseUpload(io.BytesIO(data_bytes), mimetype=mimetype, resumable=True)
        if existing:
            fid = existing[0]['id']
            service.files().update(fileId=fid, media_body=media, supportsAllDrives=True).execute()
            return fid
        else:
            file_metadata = {"name": name, "parents": [folder_id]}
            created = service.files().create(body=file_metadata, media_body=media, fields='id', supportsAllDrives=True).execute()
            return created.get('id')
    except Exception:
        return None

def download_file_bytes(service, file_id):
    request = service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    fh.seek(0)
    return fh.read()

def get_folder_metadata(service, folder_id):
    """Return (metadata, error_message)."""
    try:
        meta = service.files().get(fileId=folder_id, fields="id, name, mimeType, owners", supportsAllDrives=True).execute()
        if meta.get('mimeType') != 'application/vnd.google-apps.folder':
            return None, "ID tersebut bukan folder."
        return meta, None
    except Exception as e:
        if 'File not found' in str(e):
            return None, "Folder tidak ditemukan atau belum dibagikan ke service account."
        return None, f"Gagal memeriksa folder: {e}"

def delete_file(service, file_id):
    try:
        service.files().delete(fileId=file_id, supportsAllDrives=True).execute()
    except Exception as e:
        if hasattr(e, 'status_code') and e.status_code == 404:
            st.error(f"File tidak ditemukan (ID: {file_id})")
        else:
            st.error(f"Gagal menghapus file: {e}")


# -------------------------
# Role checks
# -------------------------
def require_login():
    if not current_user():
        st.warning("Silakan login terlebih dahulu.")
        st.session_state.page = "Authentication"
        st.rerun()

def require_admin():
    u = current_user()
    if not u or u.get("role") != "admin":
        st.warning("Akses admin diperlukan.")
        # Optional: redirect non-admin users to dashboard/login
        if not u:
            st.session_state.page = "Authentication"
        else:
            st.session_state.page = "Dashboard"
        st.rerun()


def get_pending_users_count():
    return fetchone("SELECT COUNT(*) AS count FROM users WHERE approved=0")['count']



# ... (page_auth, page_dashboard, page_resume, page_reporting, page_admin_panel, page_user_guide and main function remain the same) ...
def page_auth():
    # Set layout non-wide khusus halaman login
    try:
        st.set_page_config(layout="centered")
    except Exception:
        pass  # Sudah pernah dipanggil di awal, abaikan error
    # Always use non-wide mode on login/register page
    # Sembunyikan sidebar dengan CSS hack
    st.markdown("""
        <style>
        [data-testid="stSidebar"] {display: none !important;}
        </style>
    """, unsafe_allow_html=True)
    # Tampilkan logo sebagai header
    st.image("logo.png", width=180)
    st.title("Authentication")
    st.markdown("---")
    tab = st.tabs(["Login", "Register"])
    
    if "login_status_message" not in st.session_state:
        st.session_state.login_status_message = {"type": None, "text": ""}

    with tab[0]:
        st.subheader("Login")
        email = st.text_input("Email", key="login_email")
        pw = st.text_input("Password", type="password", key="login_pw")
        
        if st.button("Login", use_container_width=True):
            st.session_state.login_status_message = {"type": None, "text": ""}
            
            row = fetchone("SELECT * FROM users WHERE email=?", (email,))
            if not row:
                st.session_state.login_status_message = {"type": "error", "text": "User tidak ditemukan."}
            else:
                if not row['approved']:
                    st.session_state.login_status_message = {"type": "error", "text": "Akun belum disetujui oleh Admin."}
                elif verify_password(pw, row['password_hash']):
                    login_user(row)
                    # Catat audit trail login
                    execute("INSERT INTO audit_logs (user_id, action, details) VALUES (?,?,?)", (row['id'], "LOGIN", f"User {row['email']} login."))
                    st.session_state.login_status_message = {"type": "success", "text": "Login berhasil. Mengalihkan..."}
                    st.session_state.page = "Dashboard" 
                    st.rerun() 
                else:
                    st.session_state.login_status_message = {"type": "error", "text": "Password salah."}

        if st.session_state.login_status_message["type"] == "error":
            st.error(st.session_state.login_status_message["text"])
        elif st.session_state.login_status_message["type"] == "success":
            st.success(st.session_state.login_status_message["text"])

    with tab[1]:
        st.subheader("Register")
        name = st.text_input("Nama", key="reg_name")
        email_r = st.text_input("Email", key="reg_email")
        deps = [d['name'] for d in fetchall("SELECT * FROM departments")]
        dept = st.selectbox("Departemen", deps + ["Other"], key="reg_dept")
        if dept == "Other":
            dept = st.text_input("Nama Departemen baru", key="reg_dept_new")
        pw1 = st.text_input("Password", type="password", key="reg_pw1")
        pw2 = st.text_input("Confirm Password", type="password", key="reg_pw2")
        if st.button("Register", use_container_width=True):
            if not name or not email_r or not pw1:
                st.error("Isi semua data.")
            elif pw1 != pw2:
                st.error("Password dan konfirmasi tidak cocok.")
            else:
                try:
                    execute("INSERT INTO users (name,email,password_hash,role,department,approved) VALUES (?,?,?,?,?,?)",
                            (name, email_r, hash_password(pw1), "user", dept, 0))
                    st.success("Registrasi berhasil. Tunggu approval Admin.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Gagal register: {e}")

def page_gdrive():
    require_login()
    st.header("📂 Google Drive Files")
    try:
        service, _sa_email = build_drive_service()
    except Exception:
        return
    # Hardcoded folder ID per permintaan user
    folder_id = FOLDER_ID_DEFAULT
    meta, meta_err = get_folder_metadata(service, folder_id)
    if meta_err:
        st.error(meta_err)
        st.info("Pastikan folder dengan ID di-hardcode sudah dishare ke service account sebagai Editor.")
        return
    st.markdown(f"Aktif Folder: **{meta.get('name')}** (`{folder_id}`)")

    tabs = st.tabs(["List", "Upload file", "Download", "Delete", "Sync DB"])

    # List Tab
    with tabs[0]:
        st.subheader("Daftar File")
        # Manual trigger backup (admin only)
        u = current_user()
        if u and u.get('role') == 'admin':
            if st.button('🚀 Trigger Auto Backup Sekarang'):
                ok, msg = perform_backup(service, folder_id)
                if ok:
                    st.success(msg)
                else:
                    st.error(msg)
            # Show last 5 backup logs
            logs = fetchall("SELECT * FROM backup_log ORDER BY id DESC LIMIT 5")
            if logs:
                st.markdown("**Riwayat Backup Terbaru:**")
                for lg in logs:
                    st.markdown(f"- {lg['backup_time']} | {lg['file_name']} | {lg['status']}")

            st.markdown("---")
            st.markdown("### ⚙️ Pengaturan Scheduled Backup")
            enabled_flag = get_setting('scheduled_backup_enabled', 'false') == 'true'
            col_sb1, col_sb2 = st.columns([1,2])
            with col_sb1:
                enable_toggle = st.checkbox("Aktifkan Jadwal", value=enabled_flag, key='sched_enable')
            default_name = get_setting('scheduled_backup_filename', 'scheduled_backup.sqlite') or 'scheduled_backup.sqlite'
            with col_sb2:
                new_name = st.text_input("Nama File Backup (overwrite)", value=default_name, key='sched_filename')
            if st.button("Simpan Pengaturan Jadwal"):
                set_setting('scheduled_backup_enabled', 'true' if enable_toggle else 'false')
                set_setting('scheduled_backup_filename', new_name.strip() or 'scheduled_backup.sqlite')
                st.success("Pengaturan jadwal disimpan.")
            st.markdown("### ♻️ Auto-Restore Saat Wake (Autosleep)")
            ar_enabled = get_setting('auto_restore_enabled','true') == 'true'
            col_ar1, col_ar2 = st.columns([1,2])
            with col_ar1:
                ar_toggle = st.checkbox('Aktifkan Auto-Restore', value=ar_enabled, key='auto_restore_toggle')
            last_ar_file = get_setting('auto_restore_last_file','-')
            last_ar_time = get_setting('auto_restore_last_time','-')
            with col_ar2:
                st.caption(f"Terakhir restore: {last_ar_file} pada {last_ar_time}")
            if st.button('Simpan Auto-Restore'):
                set_setting('auto_restore_enabled', 'true' if ar_toggle else 'false')
                st.success('Pengaturan auto-restore disimpan.')
            st.caption('Auto-restore akan mencoba mendeteksi DB fresh (reset) dan mengganti otomatis dengan backup Drive terbaru sekali per sesi admin pertama yang login.')
            # --- Dynamic Slot Editor ---
            with st.expander("🕒 Edit Slot Jadwal (Advanced)", expanded=False):
                st.markdown("""
                Atur slot jadwal backup tanpa perlu menulis JSON. Setiap slot menentukan rentang jam lokal (0-23).\
                Jika Start > End maka dianggap melewati tengah malam (wrap). Contoh: 23 -> 6.\
                Tidak boleh ada dua slot yang saling tumpang tindih pada jam yang sama.\
                """)
                hours = list(range(24))
                # Ambil slot saat ini dari setting / default
                if 'slot_editor_state' not in st.session_state:
                    st.session_state.slot_editor_state = get_schedule_slots()
                slots_state = st.session_state.slot_editor_state

                # Tampilkan form per slot
                to_remove_indexes = []
                for idx, slot_obj in enumerate(slots_state):
                    with st.container():
                        c1,c2,c3,c4 = st.columns([1,1,2,0.6])
                        with c1:
                            slots_state[idx]['start'] = c1.selectbox(
                                'Start', hours, index=hours.index(int(slot_obj['start'])), key=f'slot_start_{idx}')
                        with c2:
                            slots_state[idx]['end'] = c2.selectbox(
                                'End', hours, index=hours.index(int(slot_obj['end'])), key=f'slot_end_{idx}')
                        with c3:
                            slots_state[idx]['name'] = c3.text_input('Nama Slot', value=slot_obj['name'], key=f'slot_name_{idx}')
                        with c4:
                            if st.button('🗑️', key=f'del_slot_{idx}'):
                                to_remove_indexes.append(idx)
                    st.markdown("")
                # Hapus slot yang diminta
                if to_remove_indexes:
                    for ridx in sorted(to_remove_indexes, reverse=True):
                        if 0 <= ridx < len(slots_state):
                            slots_state.pop(ridx)
                    st.rerun()

                st.markdown("**Tambah Slot Baru**")
                col_new1, col_new2, col_new3, col_new4 = st.columns([1,1,2,0.8])
                new_start = col_new1.selectbox('Start', hours, key='new_slot_start')
                new_end = col_new2.selectbox('End', hours, index=hours.index((new_start+1) % 24), key='new_slot_end')
                new_name = col_new3.text_input('Nama Slot', key='new_slot_name', placeholder='misal: slot_dawn')
                if col_new4.button('➕ Tambah'):
                    if new_name.strip() == '':
                        st.error('Nama slot tidak boleh kosong.')
                    elif any(s['name'] == new_name.strip() for s in slots_state):
                        st.error('Nama slot harus unik.')
                    elif new_start == new_end:
                        st.error('Start dan End tidak boleh sama (durasi 0).')
                    else:
                        slots_state.append({'start': int(new_start), 'end': int(new_end), 'name': new_name.strip()})
                        st.success('Slot ditambahkan.')
                        st.rerun()

                # Validasi overlap & struktur sebelum simpan
                def _hours_covered(slot):
                    st_h = int(slot['start']); en_h = int(slot['end'])
                    if st_h < en_h:
                        return list(range(st_h, en_h))
                    else:  # wrap
                        return list(range(st_h,24)) + list(range(0,en_h))

                def _check_overlaps(slots):
                    hour_map = {}  # hour -> slot names
                    for s in slots:
                        for h in _hours_covered(s):
                            hour_map.setdefault(h, set()).add(s['name'])
                    conflicts = {h:n for h,n in hour_map.items() if len(n) > 1}
                    return conflicts

                save_col, reset_col, export_col = st.columns([1,1,1])
                with save_col:
                    if st.button('💾 Simpan Slot Jadwal', key='save_slots_btn'):
                        # Basic structure validation
                        if not _validate_slot_struct(slots_state):
                            st.error('Struktur slot tidak valid (nama unik, rentang jam 0-23, start != end).')
                        else:
                            conflicts = _check_overlaps(slots_state)
                            if conflicts:
                                conflict_msgs = []
                                for h, names in sorted(conflicts.items()):
                                    conflict_msgs.append(f"Jam {h}: {' , '.join(sorted(names))}")
                                st.error('Terdapat tumpang tindih slot:\n' + '\n'.join(conflict_msgs))
                            else:
                                set_setting('scheduled_backup_slots_json', json.dumps(slots_state))
                                st.success('Slot jadwal tersimpan ke konfigurasi.')
                with reset_col:
                    if st.button('♻️ Reset Default', key='reset_slots_btn'):
                        st.session_state.slot_editor_state = DEFAULT_SCHEDULE_SLOTS.copy()
                        set_setting('scheduled_backup_slots_json', json.dumps(DEFAULT_SCHEDULE_SLOTS))
                        st.info('Slot dikembalikan ke default.')
                        st.rerun()
                with export_col:
                    if st.button('📄 Lihat JSON', key='export_slots_btn'):
                        st.code(json.dumps(slots_state, indent=2))

                # Preview ringkas
                if slots_state:
                    st.markdown("**Preview Slot Aktif**")
                    prev_df = pd.DataFrame(slots_state)
                    # Durasi jam (approx) hanya untuk info
                    def _dur(srow):
                        st_h=int(srow['start']); en_h=int(srow['end'])
                        return (en_h-st_h) if st_h < en_h else ((24-st_h)+en_h)
                    prev_df['duration_h'] = prev_df.apply(_dur, axis=1)
                    st.dataframe(prev_df[['name','start','end','duration_h']], use_container_width=True, hide_index=True)
                st.caption("Catatan: Backup akan dijalankan sekali per slot saat ada interaksi admin (page refresh / navigasi).")
            last_slot = get_setting('scheduled_backup_last_slot', '-')
            last_date = get_setting('scheduled_backup_last_date', '-')
            st.caption(f"Slot terakhir: {last_slot} pada {last_date}")
            if st.button("Paksa Backup Slot Saat Ini"):
                try:
                    okf, msgf = check_scheduled_backup(service, folder_id)
                    if okf:
                        st.success(msgf)
                    else:
                        st.info(msgf)
                except Exception as e:
                    st.error(f"Gagal paksa backup: {e}")
        try:
            files = list_files_in_folder(service, folder_id)
        except Exception as e:
            st.error(f"Gagal mengambil daftar file: {e}")
            return
        if not files:
            st.info("Folder kosong.")
        else:
            df = pd.DataFrame(files)
            if 'size' in df.columns:
                def nice_size(s):
                    try:
                        s = int(s)
                    except Exception:
                        return '-'
                    for unit in ['B','KB','MB','GB']:
                        if s < 1024:
                            return f"{s}{unit}"
                        s //= 1024
                    return f"{s}TB"
                df['size'] = df['size'].apply(nice_size)
            st.dataframe(df[['name','id','mimeType','createdTime','modifiedTime'] + ([ 'size'] if 'size' in df.columns else [])], use_container_width=True, hide_index=True)

        st.markdown('---')
        st.subheader('Backup Database ke Drive')
        if st.button('📤 Export Database ke Drive'):
            if os.path.exists(DB_PATH):
                try:
                    with open(DB_PATH,'rb') as f:
                        data = f.read()
                    backup_name = f"backup_db_{time.strftime('%Y%m%d_%H%M%S')}.sqlite"
                    fid = upload_bytes(service, folder_id, backup_name, data, mimetype='application/x-sqlite3')
                    if fid:
                        st.success(f"Database berhasil diupload sebagai {backup_name} (ID: {fid})")
                    else:
                        st.error("Gagal mengupload database.")
                except Exception as e:
                    st.error(f"Error saat membaca / upload DB: {e}")
            else:
                st.error(f"File database '{DB_PATH}' tidak ditemukan.")

    # Upload Tab
    with tabs[1]:
        st.subheader('Upload File Baru')
        uploaded = st.file_uploader('Pilih file')
        if uploaded and st.button('Upload ke Drive'):
            data = uploaded.read()
            fid = upload_bytes(service, folder_id, uploaded.name, data, mimetype=uploaded.type or 'application/octet-stream')
            if fid:
                st.success(f"File '{uploaded.name}' terupload (ID: {fid})")

    # Download Tab
    with tabs[2]:
        st.subheader('Download File')
        files_all = list_files_in_folder(service, folder_id)
        if not files_all:
            st.info('Folder kosong.')
        else:
            name_to_id = {f['name']: f['id'] for f in files_all}
            sel_name = st.selectbox('Pilih file', list(name_to_id.keys()))
            if st.button('Download file'):
                try:
                    data = download_file_bytes(service, name_to_id[sel_name])
                    st.download_button('Klik untuk download', data=data, file_name=sel_name)
                except Exception as e:
                    st.error(f"Gagal download: {e}")

    # Delete Tab
    with tabs[3]:
        st.subheader('Hapus File')
        files_all = list_files_in_folder(service, folder_id)
        if not files_all:
            st.info('Folder kosong.')
        else:
            name_to_id = {f['name']: f['id'] for f in files_all}
            sel_name = st.selectbox('Pilih file untuk dihapus', list(name_to_id.keys()))
            if st.button('Hapus file'):
                try:
                    delete_file(service, name_to_id[sel_name])
                    st.success(f"File '{sel_name}' dihapus.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Gagal hapus: {e}")

    # Sync DB Tab
    with tabs[4]:
        st.subheader('🔄 Sinkronisasi Database')
        st.markdown("Gunakan fitur ini untuk: 1) Mengunggah file database (.sqlite) baru dan menggantikan database lokal. 2) Merestore database lokal dari salinan yang ada di Google Drive.")
        st.warning("Pastikan Anda benar-benar paham dampaknya. Selalu lakukan backup sebelum replace.")

        col_upload, col_restore = st.columns(2)

        # --- Upload & Replace Local DB ---
        with col_upload:
            st.markdown("### ⬆️ Upload & Ganti DB Lokal")
            up_db = st.file_uploader("Pilih file .sqlite", type=["sqlite","db"], key="sync_upload_sqlite")
            auto_push = st.checkbox("Juga upload file ini ke Drive setelah replace", value=True, key="sync_auto_push")
            if up_db and st.button("Replace Database Lokal", type="primary"):
                try:
                    data = up_db.read()
                    # Validasi header sqlite
                    if not data.startswith(b"SQLite format 3\x00"):
                        st.error("File bukan database SQLite yang valid.")
                    else:
                        ts = time.strftime('%Y%m%d_%H%M%S')
                        # Backup lokal lama jika ada
                        if os.path.exists(DB_PATH):
                            backup_local = f"local_backup_before_replace_{ts}.sqlite"
                            try:
                                with open(DB_PATH,'rb') as oldf, open(backup_local,'wb') as newf:
                                    newf.write(oldf.read())
                                st.info(f"Backup lokal lama tersimpan: {backup_local}")
                            except Exception as e:
                                st.error(f"Gagal membuat backup lokal: {e}")
                        # Tulis DB baru
                        with open(DB_PATH,'wb') as fnew:
                            fnew.write(data)
                        st.success("Database lokal berhasil diganti dengan file yang diupload.")
                        # Optional push ke Drive
                        if auto_push:
                            fname_drive = f"uploaded_db_{ts}.sqlite"
                            fid = upload_bytes(service, folder_id, fname_drive, data, mimetype='application/x-sqlite3')
                            if fid:
                                st.success(f"Salinan diupload ke Drive sebagai {fname_drive} (ID: {fid})")
                            else:
                                st.error("Gagal mengupload salinan ke Drive.")
                        st.info("Silakan refresh halaman atau navigasi ulang untuk memastikan app memakai DB baru.")
                except Exception as e:
                    st.error(f"Gagal mengganti database: {e}")

        # --- Restore From Drive ---
        with col_restore:
            st.markdown("### ⬇️ Restore dari Drive")
            try:
                drive_files = list_files_in_folder(service, folder_id)
            except Exception as e:
                drive_files = []
                st.error(f"Tidak bisa mengambil daftar file Drive: {e}")
            # Filter file sqlite/db setelah mencoba mengambil daftar file
            sqlite_files = [
                f for f in drive_files
                if f.get('name','').endswith('.sqlite') or f.get('name','').endswith('.db')
            ]
            if not sqlite_files:
                st.info("Tidak ada file .sqlite / .db di folder Drive.")
            else:
                # Urutkan terbaru berdasarkan modifiedTime
                try:
                    sqlite_files.sort(key=lambda x: x.get('modifiedTime',''), reverse=True)
                except Exception:
                    pass
                name_to_id_restore = {f["name"]: f["id"] for f in sqlite_files}
                sel_restore = st.selectbox("Pilih file DB di Drive", list(name_to_id_restore.keys()), key="restore_sel_db")
                if st.button("Restore Database Lokal dari Drive", type="primary"):
                    try:
                        fid = name_to_id_restore[sel_restore]
                        data = download_file_bytes(service, fid)
                        if not data.startswith(b"SQLite format 3\x00"):
                            st.error("File di Drive bukan database SQLite valid.")
                        else:
                            ts = time.strftime('%Y%m%d_%H%M%S')
                            if os.path.exists(DB_PATH):
                                backup_local = f"local_backup_before_restore_{ts}.sqlite"
                                try:
                                    with open(DB_PATH,'rb') as oldf, open(backup_local,'wb') as newf:
                                        newf.write(oldf.read())
                                    st.info(f"Backup lokal lama tersimpan: {backup_local}")
                                except Exception as e:
                                    st.error(f"Gagal membuat backup lokal: {e}")
                            with open(DB_PATH,'wb') as fnew:
                                fnew.write(data)
                            st.success(f"Database lokal berhasil direstore dari '{sel_restore}'.")
                            st.info("Reload halaman untuk memakai DB baru.")
                    except Exception as e:
                        st.error(f"Gagal restore: {e}")
    
def main():
    init_db()

    if "page" not in st.session_state:
        st.session_state.page = "Authentication"
    if "user" not in st.session_state:
        st.session_state.user = None


    user = current_user()

    # Sidebar minimal: hanya autentikasi & G Drive
    st.sidebar.image("logo.png", use_container_width=True)
    st.sidebar.title("Navigasi")

    if not user:
        if st.sidebar.button("🔐 Login / Register", use_container_width=True):
            st.session_state.page = "Authentication"
    else:
        # Info singkat user
        st.sidebar.markdown(f"**👤 {user['name']}**")
        st.sidebar.markdown(f"✉️ {user['email']}")
        st.sidebar.markdown(f"🏢 {user.get('department','-')}")
        st.sidebar.markdown(f"**Role:** {user['role'].capitalize()}")
        st.sidebar.markdown("---")

        # Jalankan auto-restore & auto/dijadwalkan backup tetap dipertahankan (opsional bagian dari fitur G Drive)
        if user.get('role') == 'admin' and 'auto_restore_done' not in st.session_state:
            try:
                service_ar, _ = build_drive_service()
                ok_ar, msg_ar = attempt_auto_restore_if_seed(service_ar, FOLDER_ID_DEFAULT)
                if ok_ar:
                    st.sidebar.success(f"Auto-Restore: {msg_ar}")
                    st.session_state['auto_restore_done'] = True
                    st.rerun()
                else:
                    st.sidebar.caption(f"Auto-Restore: {msg_ar}")
                    st.session_state['auto_restore_done'] = True
            except Exception as e:
                st.sidebar.caption(f"Auto-Restore Err: {e}")
                st.session_state['auto_restore_done'] = True

        if user.get('role') == 'admin' and 'auto_backup_checked' not in st.session_state:
            try:
                if _is_probably_fresh_seed_db():
                    st.sidebar.caption("Auto Backup: dilewati (DB masih fresh/seed)")
                else:
                    service_ab, _ = build_drive_service()
                    ok_ab, msg_ab = auto_daily_backup(service_ab, FOLDER_ID_DEFAULT)
                    if ok_ab:
                        st.sidebar.success(f"Auto Backup: {msg_ab}")
                    else:
                        st.sidebar.caption(f"Auto Backup: {msg_ab}")
            except Exception as e:
                st.sidebar.caption(f"Auto Backup Err: {e}")
            st.session_state['auto_backup_checked'] = True

        if user.get('role') == 'admin':
            if 'scheduled_backup_last_check' not in st.session_state or (datetime.utcnow().timestamp() - st.session_state['scheduled_backup_last_check'] > 60):
                try:
                    service_sched, _ = build_drive_service()
                    ok_sched, msg_sched = check_scheduled_backup(service_sched, FOLDER_ID_DEFAULT)
                    if ok_sched:
                        st.sidebar.success(msg_sched)
                    else:
                        if 'disabled' in (msg_sched or '').lower():
                            pass
                        else:
                            st.sidebar.caption(f"Scheduled: {msg_sched}")
                except Exception as e:
                    st.sidebar.caption(f"Scheduled Err: {e}")
                st.session_state['scheduled_backup_last_check'] = datetime.utcnow().timestamp()

        # Tombol hanya untuk G Drive & Logout
        if st.sidebar.button("📂 G Drive", use_container_width=True, type="primary"):
            st.session_state.page = "G Drive"
        st.sidebar.button("🚪 Logout", on_click=logout_user, use_container_width=True)
        st.sidebar.markdown("---")
    
    if not user:
        page_auth()
        return

    if st.session_state.page == "Authentication":
        st.session_state.page = "G Drive"
        st.rerun()
    # Paksa halaman selain Authentication menjadi G Drive
    st.session_state.page = "G Drive"
    page_gdrive()

if __name__ == '__main__':
    main()