def page_audit_trail():
    st.header("🕓 Audit Trail")
    st.markdown("---")
    # Ambil data audit_logs join user dan project
    logs = fetchall('''
        SELECT a.id, a.timestamp, a.action, a.details, u.name as user_name, u.email as user_email, p.name as project_name
        FROM audit_logs a
        LEFT JOIN users u ON a.user_id = u.id
        LEFT JOIN projects p ON a.project_id = p.id
        ORDER BY a.timestamp DESC
        LIMIT 1000
    ''')
    if not logs:
        st.info("No activity has been recorded yet.")
        return
    df = pd.DataFrame(logs)
    # Kolom yang ditampilkan
    df = df.rename(columns={
        'timestamp': 'Waktu',
        'user_name': 'User',
        'user_email': 'Email',
        'action': 'Aksi',
        'details': 'Detail',
        'project_name': 'Project'
    })

    # FILTERS
    with st.expander("🔎 Audit Trail Filter", expanded=True):
        col1, col2, col3 = st.columns(3)
        # Time (date range)
        min_date = pd.to_datetime(df['Waktu']).min().date() if not df.empty else date.today()
        max_date = pd.to_datetime(df['Waktu']).max().date() if not df.empty else date.today()
        d1 = col1.date_input("From Date", min_value=min_date, max_value=max_date, value=min_date, key="audit_from")
        d2 = col1.date_input("To Date", min_value=min_date, max_value=max_date, value=max_date, key="audit_to")
        # User
        user_opt = ["(All)"] + sorted(df['User'].dropna().unique().tolist())
        user_sel = col2.selectbox("User", user_opt, key="audit_user")
        # Email
        email_opt = ["(All)"] + sorted(df['Email'].dropna().unique().tolist())
        email_sel = col2.selectbox("Email", email_opt, key="audit_email")
        # Action
        aksi_opt = ["(All)"] + sorted(df['Aksi'].dropna().unique().tolist())
        aksi_sel = col3.selectbox("Action", aksi_opt, key="audit_aksi")
        # Project
        proj_opt = ["(All)"] + sorted(df['Project'].dropna().unique().tolist())
        proj_sel = col3.selectbox("Project", proj_opt, key="audit_proj")

    # Apply filters
    mask = (
        (pd.to_datetime(df['Waktu']).dt.date >= d1) &
        (pd.to_datetime(df['Waktu']).dt.date <= d2)
    )
    if user_sel != "(All)":
        mask &= (df['User'] == user_sel)
    if email_sel != "(All)":
        mask &= (df['Email'] == email_sel)
    if aksi_sel != "(All)":
        mask &= (df['Aksi'] == aksi_sel)
    if proj_sel != "(All)":
        mask &= (df['Project'] == proj_sel)

    df_filtered = df[mask]
    # Rename columns to English for display
    df_filtered = df_filtered.rename(columns={
        "Waktu": "Time",
        "User": "User",
        "Email": "Email",
        "Aksi": "Action",
        "Detail": "Detail",
        "Project": "Project"
    })
    st.dataframe(df_filtered[["Time", "User", "Email", "Action", "Detail", "Project"]], use_container_width=True, hide_index=True)
import streamlit as st
st.set_page_config(layout="wide", page_icon="icon.png", page_title="Project Charter")
import sqlite3
import pandas as pd
import hashlib
from datetime import datetime, timedelta, date
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

# ---------------------------------
# Validation & Helper Utilities (bobot, UOM, child projects)
# ---------------------------------
def get_project_groups(project_id):
    return fetchall("SELECT * FROM groups WHERE project_id=?", (project_id,))

def get_group_items(group_id):
    return fetchall("SELECT * FROM items WHERE group_id=?", (group_id,))

def validate_and_warn_project_weights(project_id):
    groups = get_project_groups(project_id)
    if not groups:
        st.info("Project belum memiliki group.")
        return
    total_group_weight = sum([(g.get('group_weight') or 0) for g in groups])
    if abs(total_group_weight - 100) > 1e-6:
        st.warning(f"Total Group Weight saat ini = {total_group_weight:.2f} (HARUS 100).")
    else:
        st.success("Total Group Weight = 100 ✅")
    # Per group item weight
    for g in groups:
        items = get_group_items(g['id'])
        if not items:
            st.info(f"Group {g['group_type']} (ID {g['id']}) belum memiliki item.")
            continue
        tot_item = sum([(it.get('item_weight') or 0) for it in items])
        if abs(tot_item - 100) > 1e-6:
            st.warning(f"Total Item Weight pada Group {g['group_type']} (ID {g['id']}) = {tot_item:.2f} (HARUS 100).")
        else:
            st.success(f"Group {g['group_type']} (ID {g['id']}) Item Weight = 100 ✅")

def warn_activity_uom(project_id):
    rows = fetchall("""
        SELECT i.id, i.uom, g.group_type, i.name
        FROM items i JOIN groups g ON i.group_id=g.id
        WHERE g.project_id=? AND g.group_type='ACTIVITY'
    """, (project_id,))
    bad = [r for r in rows if (r.get('uom') or '').strip() != '%']
    if bad:
        st.error("Ada Key Activity dengan UOM bukan %. Harap sesuaikan:")
        for b in bad:
            st.write(f"- Item ID {b['id']}: {b['name']} (UOM sekarang: {b['uom']})")

def page_child_projects():
    require_login()
    st.header("🌿 Turunan Project Charter")
    st.markdown("---")
    child_projects = fetchall("SELECT * FROM projects WHERE parent_project_id IS NOT NULL ORDER BY id DESC")
    if not child_projects:
        st.info("Belum ada Turunan Project.")
        return
    # Mapping parent item & parent project
    data_rows = []
    for p in child_projects:
        parent_item = fetchone("SELECT id, name, group_id FROM items WHERE id=?", (p['parent_project_id'],))
        parent_project = None
        if parent_item:
            parent_group = fetchone("SELECT project_id FROM groups WHERE id=?", (parent_item['group_id'],))
            if parent_group:
                parent_project = fetchone("SELECT id, name FROM projects WHERE id=?", (parent_group['project_id'],))
        score_month = compute_project_score(p['id'])
        data_rows.append({
            'Child Project ID': p['id'],
            'Child Name': p['name'],
            'Department': p['department'],
            'Start': p['start_date'],
            'Finish': p['finish_date'],
            'Parent Item ID': parent_item['id'] if parent_item else None,
            'Parent Project': parent_project['name'] if parent_project else None,
            'Score (0-1)': round(score_month, 3) if score_month is not None else None
        })
    import pandas as _pd
    df_cp = _pd.DataFrame(data_rows)
    st.dataframe(df_cp, use_container_width=True, hide_index=True)


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
    # projects: parent_project_id can store the ID of a Parent Project OR a Parent Item
    c.execute("""
    CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        department TEXT,
        pic_user_id INTEGER,
        start_date TEXT,
        finish_date TEXT,
        parent_project_id INTEGER, 
        created_by INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    # groups: group_type ACTIVITY or SUCCESS, group_weight is portion of 100
    c.execute("""
    CREATE TABLE IF NOT EXISTS groups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER,
        group_type TEXT,
        group_weight REAL
    )""")
    # items: key activities and key success items, each with weight within group
    c.execute("""
    CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id INTEGER,
        name TEXT,
        item_weight REAL,
        uom TEXT,
        polarisasi TEXT,
        period_type TEXT,
        rollup TEXT,
        pic_user_id INTEGER,
        start_date TEXT,
        end_date TEXT
    )""")
    # realizations: store actual value by item and date
    c.execute("""
    CREATE TABLE IF NOT EXISTS realizations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_id INTEGER,
        realized_value REAL,
        realized_date TEXT,
        recorded_by INTEGER,
        recorded_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    # periodic_targets: store target value for a specific period (e.g., month, quarter)
    c.execute("""
    CREATE TABLE IF NOT EXISTS periodic_targets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_id INTEGER,
        period_start_date TEXT,
        period_end_date TEXT,
        target_value REAL,
        FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
    )""")
    # audit logs
    c.execute("""
    CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER,
        user_id INTEGER,
        action TEXT,
        details TEXT,
        timestamp TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    # delegations (optional)
    c.execute("""
    CREATE TABLE IF NOT EXISTS delegations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER,
        item_id INTEGER,
        delegator_id INTEGER,
        delegatee_id INTEGER,
        start_date TEXT,
        end_date TEXT
    )""")
    conn.commit()

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

# --- HELPER FUNCTION: DELETE ITEM ---
def delete_item(item_id):
    """
    Menghapus item beserta semua target periodik dan realisasi terkait.
    """
    conn = get_db()
    cur = conn.cursor()
    try:
        # Check if any child project is linked to this item
        child_project = fetchone("SELECT id FROM projects WHERE parent_project_id=?", (item_id,))
        if child_project:
            raise Exception(f"Item ini adalah Parent Project Turunan (ID: {child_project['id']}). Hapus Project Turunan tersebut terlebih dahulu.")
            
        # Cascading delete
        cur.execute("DELETE FROM periodic_targets WHERE item_id=?", (item_id,))
        cur.execute("DELETE FROM realizations WHERE item_id=?", (item_id,))
        cur.execute("DELETE FROM items WHERE id=?", (item_id,))
        conn.commit()
        execute("INSERT INTO audit_logs (project_id, user_id, action, details) VALUES (?,?,?,?)",
                (None, current_user()['id'], "DELETE", f"Item ID {item_id} deleted."))
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()
# ----------------------------------------


# -------------------------
# Business logic: Thursday Rule & period generation
# -------------------------
def generate_weeks_by_thursday(start_dt: date, end_dt: date):
    """
    Return list of week buckets where each bucket is dict:
    { 'week_start': Monday-date, 'week_end': Sunday-date, 'week_thursday': date, 'month_assigned': int (1-12) }
    Using ISO week Monday-Sunday and "Thursday Rule": assign week to month of its Thursday.
    """
    # find Monday on or before start_dt
    day0 = start_dt - timedelta(days=(start_dt.weekday()))  # Monday
    weeks = []
    cur = day0
    while cur <= end_dt:
        week_start = cur
        week_end = cur + timedelta(days=6)
        week_thursday = week_start + timedelta(days=3)
        month_assigned = week_thursday.month
        year_assigned = week_thursday.year
        weeks.append({
            "week_start": week_start,
            "week_end": week_end,
            "week_thursday": week_thursday,
            "month_assigned": month_assigned,
            "year_assigned": year_assigned
        })
        cur = cur + timedelta(days=7)
    return weeks

def generate_periods(start_date: date, end_date: date, period_type: str):
    periods = []
    current_date = start_date
    if period_type == 'weekly':
        weeks = generate_weeks_by_thursday(start_date, end_date)
        for w in weeks:
            periods.append({
                "period_name": f"Week of {w['week_start'].strftime('%Y-%m-%d')}",
                "start": w['week_start'],
                "end": w['week_end']
            })
    elif period_type == 'monthly':
        current_month = date(start_date.year, start_date.month, 1)
        while current_month <= end_date:
            period_end = (current_month + relativedelta(months=1)) - timedelta(days=1)
            periods.append({
                "period_name": current_month.strftime("%B %Y"),
                "start": current_month,
                "end": period_end
            })
            current_month += relativedelta(months=1)
    elif period_type == 'quarterly':
        current_quarter_start = date(start_date.year, (math.ceil(start_date.month / 3) - 1) * 3 + 1, 1)
        while current_quarter_start <= end_date:
            period_end = (current_quarter_start + relativedelta(months=3)) - timedelta(days=1)
            periods.append({
                "period_name": f"Q{math.ceil(current_quarter_start.month / 3)} {current_quarter_start.year}",
                "start": current_quarter_start,
                "end": period_end
            })
            current_quarter_start += relativedelta(months=3)
    elif period_type == 'semester':
        current_semester_start = date(start_date.year, 1 if start_date.month <= 6 else 7, 1)
        while current_semester_start <= end_date:
            period_end = (current_semester_start + relativedelta(months=6)) - timedelta(days=1)
            periods.append({
                "period_name": f"Semester {1 if current_semester_start.month <=6 else 2} {current_semester_start.year}",
                "start": current_semester_start,
                "end": period_end
            })
            current_semester_start += relativedelta(months=6)
    elif period_type == 'yearly':
        current_year_start = date(start_date.year, 1, 1)
        while current_year_start <= end_date:
            period_end = date(current_year_start.year, 12, 31)
            periods.append({
                "period_name": f"Year {current_year_start.year}",
                "start": current_year_start,
                "end": period_end
            })
            current_year_start += relativedelta(years=1)
    
    # Filter periods to be within the start_date and end_date of the project/item
    final_periods = [
        p for p in periods 
        if p['start'] <= end_date and p['end'] >= start_date
    ]
    return final_periods

def get_valid_months_in_range(start_date: date, end_date: date):
    """
    Returns a list of date objects representing the first day of each month
    within the given start and end date range.
    """
    months = []
    # Start from the first day of the start_date's month
    current_month = date(start_date.year, start_date.month, 1)
    # Stop when the current month passes the end_date's month
    # We must include the month of end_date if it is not the first day of the next month
    
    # Get the last day of the month for end_date
    last_day_of_end_month = date(end_date.year, end_date.month, 1) + relativedelta(months=1) - timedelta(days=1)

    while current_month <= last_day_of_end_month:
        months.append(current_month)
        current_month += relativedelta(months=1)
    return months


# -------------------------
# Score normalization (Polarisasi)
# -------------------------
def normalize_score(realized, target, polarisasi, normal=None, cap=True, tolerance_ratio=0.2):
    """Normalisasi skor berbasis Polarisasi.

    Polarisasi:
    - MAX: lebih besar lebih baik (dibatasi 1 kalau cap True)
    - MIN: lebih kecil lebih baik (ratio target / realized)
    - STABLE: semakin dekat ke 'normal' semakin baik dengan toleransi tolerance_ratio * normal
    """
    if realized is None:
        return None
    try:
        realized = float(realized)
    except Exception:
        return None

    if polarisasi == "MAX":
        if not target or target == 0:
            return 1.0 if realized > 0 else 0.0
        val = realized / target
        return min(val, 1.0) if cap else val
    if polarisasi == "MIN":
        if realized == 0:
            return 1.0
        if not target or target == 0:
            return 0.0
        val = target / realized
        return min(val, 1.0)
    if polarisasi == "STABLE":
        if normal is None:
            return 0.0
        tol = normal * tolerance_ratio
        if tol == 0:
            return 1.0 if abs(realized - normal) < 1e-9 else 0.0
        score = max(0.0, 1 - abs(realized - normal) / tol)
        return max(0.0, min(score, 1.0))
    return None

# -------------------------
# Helper functions for rollup & timeline weighting
# -------------------------
def _last_day_of_month(d: date) -> date:
    return (d.replace(day=1) + relativedelta(months=1)) - timedelta(days=1)

def _is_full_month_period(sp: date, ep: date) -> bool:
    try:
        return sp.day == 1 and ep == _last_day_of_month(sp)
    except Exception:
        return False

def _time_factor(start_date_str, end_date_str, ref_end):
    """Timeline weighting: proporsi waktu yang telah berlalu (0..1).

    - Jika tanggal tidak lengkap -> 1.0 (anggap penuh)
    - Jika ref_end sebelum start -> 0.0
    - ref_end menggunakan end_period jika tersedia, else today
    """
    if not start_date_str or not end_date_str:
        return 1.0
    try:
        s = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        e = datetime.strptime(end_date_str, "%Y-%m-%d").date()
    except Exception:
        return 1.0
    if e < s:
        return 1.0
    ref = ref_end or date.today()
    if ref < s:
        return 0.0
    effective = min(ref, e)
    total_days = (e - s).days + 1
    elapsed_days = (effective - s).days + 1
    if total_days <= 0:
        return 1.0
    return max(0.0, min(1.0, elapsed_days / total_days))

# -------------------------
# Rollup functions
# -------------------------
def compute_item_score(item_id, start_period=None, end_period=None):
    """
    Compute item score across a period based on rollup method.
    If the item is a parent of a child project (parent_project_id in projects table == item_id),
    the score is taken from the child project's overall score.
    """
    conn = get_db()
    cur = conn.cursor()
    
    # --- LOGIC FOR PROJECT TURUNAN (CHILD PROJECT) ---
    # Check if this item is a parent of another project (by matching projects.parent_project_id to item_id)
    child_project = fetchone("SELECT id FROM projects WHERE parent_project_id=?", (item_id,))
    if child_project:
        conn.close()
        # If a child project exists, get its overall score
        # This is the core logic: Item score = Child Project Score
        child_score = compute_project_score(child_project['id'], start_period, end_period)
        return child_score
    
    # --- STANDARD ITEM SCORE CALCULATION ---
    # If no child project, continue with normal item score calculation
    cur.execute("SELECT i.*, g.project_id FROM items i JOIN groups g ON i.group_id=g.id WHERE i.id=?",
                (item_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return None
    item = dict(row)
    pol = item.get('polarisasi')
    rollup = item.get('rollup')
    
    # Perbaikan: Menangani kasus di mana start_date atau end_date item adalah None
    item_start_date_str = item['start_date']
    item_end_date_str = item['end_date']
    
    item_start_date = datetime.strptime(item_start_date_str, "%Y-%m-%d").date() if item_start_date_str else date.today()
    item_end_date = datetime.strptime(item_end_date_str, "%Y-%m-%d").date() if item_end_date_str else date.today()
    
    start_period_iso = start_period.isoformat() if start_period else item_start_date.isoformat()
    end_period_iso = end_period.isoformat() if end_period else item_end_date.isoformat()
    
    # UPDATED: Get periodic target
    q_target = "SELECT SUM(target_value) as target_sum FROM periodic_targets WHERE item_id=? AND date(period_start_date)<=? AND date(period_end_date)>=?"
    params_target = [item_id, end_period_iso, start_period_iso]
    cur.execute(q_target, params_target)
    
    target_row = cur.fetchone()
    # Use sum of targets for the period as the overall target
    target = target_row['target_sum'] if target_row and target_row['target_sum'] is not None else 0.0
    normal = target # For STABLE, normal value is the target
    
    # fetch realizations in period
    q = "SELECT realized_value, realized_date, recorded_at FROM realizations WHERE item_id=?"
    params = [item_id]
    
    q += " AND date(realized_date) >= date(?)"
    params.append(start_period_iso)
    q += " AND date(realized_date) <= date(?)"
    params.append(end_period_iso)
    
    q += " ORDER BY date(realized_date) ASC, recorded_at ASC"
    cur.execute(q, params)
    rows = cur.fetchall()
    conn.close()
    # Deduplicate per date to use the latest recorded entry
    recognized_map = {}
    for r in rows:
        rd = r['realized_date']
        ra = r['recorded_at'] or ""
        if rd not in recognized_map or recognized_map[rd]['recorded_at'] < ra:
            recognized_map[rd] = {'realized_value': r['realized_value'], 'recorded_at': ra}
    recognized = sorted([(d, v['realized_value'], v['recorded_at']) for d, v in recognized_map.items()], key=lambda x: x[0])
    if not recognized:
        # If there are targets but no realizations, score is 0
        if target > 0 and pol == "MAX":
            return 0.0 
        return None
    vals = [v for _, v, _ in recognized]
    
    if rollup == "SUMMARY":
        total_val = sum([v for v in vals if v is not None])
        return normalize_score(total_val, target, pol, normal)
    elif rollup == "AVERAGE":
        # Target rata-rata per titik (fallback target total jika tidak bisa dibagi)
        if target and len(recognized) > 0:
            avg_target = target / len(recognized)
        else:
            avg_target = target if target else 0.0
        norm_list = []
        for v in vals:
            sc = normalize_score(v, avg_target, pol, normal=avg_target)
            if sc is not None:
                norm_list.append(sc)
        return (sum(norm_list) / len(norm_list)) if norm_list else None
    elif rollup == "LATEST":
        latest_val = None
        if start_period and end_period and isinstance(start_period, date) and isinstance(end_period, date):
            # Jika periode adalah satu bulan penuh gunakan nilai terakhir dalam bulan tsb
            if _is_full_month_period(start_period, end_period):
                month_vals = [r for r in recognized if start_period <= date.fromisoformat(r[0]) <= end_period]
                if month_vals:
                    latest_val = month_vals[-1][1]
        if latest_val is None:
            latest_val = recognized[-1][1]
        return normalize_score(latest_val, target, pol, normal)
    return None

# -------------------------
# Aggregation: group score and project score
# -------------------------
def compute_group_score(group_id, start_period=None, end_period=None):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM groups WHERE id=?", (group_id,))
    g = cur.fetchone()
    if not g:
        conn.close()
        return None, None
    g = dict(g)
    cur.execute("SELECT * FROM items WHERE group_id=?", (group_id,))
    items = [dict(r) for r in cur.fetchall()]
    conn.close()
    weights = []
    item_scores = []
    for it in items:
        sc = compute_item_score(it['id'], start_period, end_period)
        if sc is None:
            sc = 0.0  # treat missing as 0 for aggregation
        if ENABLE_TIMELINE_WEIGHTING:
            tf = _time_factor(it.get('start_date'), it.get('end_date'), end_period)
            sc *= tf
        item_scores.append((sc, it['item_weight'] or 0.0))
    # Penting: total_item_weight digunakan untuk normalisasi pembobotan item. 
    total_item_weight = sum([w for _,w in item_scores]) or 1.0
    group_score = sum([s*(w/total_item_weight) for s,w in item_scores])
    return group_score, g['group_weight'] or 0.0

def compute_group_type_score(project_id, group_type, start_period=None, end_period=None):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM groups WHERE project_id=? AND group_type=?", (project_id, group_type))
    groups = [dict(r) for r in cur.fetchall()]
    conn.close()
    if not groups:
        return None
    # Assuming one group per group_type per project
    g = groups[0]
    gscore, _ = compute_group_score(g['id'], start_period, end_period)
    return gscore
    

def compute_project_score(project_id, start_period=None, end_period=None):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM groups WHERE project_id=?", (project_id,))
    groups = [dict(r) for r in cur.fetchall()]
    conn.close()
    if not groups:
        return 0.0
    total = 0.0
    total_group_weight = sum([g['group_weight'] for g in groups]) or 1.0
    for g in groups:
        gscore, gw = compute_group_score(g['id'], start_period, end_period)
        if gscore is None:
            gscore = 0.0
        # Normalisasi menggunakan total_group_weight (yang seharusnya 100)
        total += gscore * (gw/total_group_weight) 
    # cap 0..1
    return max(0.0, min(total, 1.0))

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

def calc_status(project_row, ref_date=None):
    if not ref_date:
        ref_date = date.today()
    s = "Not Started"
    start = datetime.strptime(project_row['start_date'], "%Y-%m-%d").date()
    finish = datetime.strptime(project_row['finish_date'], "%Y-%m-%d").date()
    score = compute_project_score(project_row['id'])
    if ref_date < start:
        s = "Not Started"
    elif start <= ref_date <= finish:
        s = "Progress"
    else:
        # after finish
        if score is not None and score >= 1.0:
            s = "Done"
        else:
            s = "Overdue"
    return s

def get_pending_users_count():
    return fetchone("SELECT COUNT(*) AS count FROM users WHERE approved=0")['count']

# ... (other utility functions like get_pending_projects_count, etc. remain the same) ...

# -------------------------
# UI Rendering Functions (Tabs Content)
# -------------------------

def render_periodic_target_form(project_id, project_start_date_str, project_end_date_str):
    st.subheader("🎯 Set Periodic Target for Item")

    # Fetch items for the project
    items = fetchall("""
        SELECT i.id, i.name, i.period_type, i.uom, i.start_date, i.end_date, g.group_type
        FROM items i JOIN groups g ON i.group_id=g.id
        WHERE g.project_id=?
    """, (project_id,))

    if not items:
        st.info("No items in this project. Please add items in the 'Groups & Items' tab first.")
        return

    item_map = {f"({i['id']}) {i['name']} ({i['group_type']} - {i['uom']})": i for i in items}
    item_key = st.selectbox("Select Item", options=list(item_map.keys()), key=f"target_item_sel_{project_id}")
    
    selected_item = item_map.get(item_key)

    if selected_item:
        item_id = selected_item['id']
        
        # --- NEW CHECK: IS THIS ITEM A PARENT OF ANOTHER PROJECT? ---
        child_project = fetchone("SELECT id, name FROM projects WHERE parent_project_id=?", (item_id,))
        if child_project:
            st.warning(f"""
                ⚠️ **Item ini telah menjadi Parent (Induk) dari Project Turunan:** **{child_project['name']}** (ID: {child_project['id']}).
                
                Target periodik Item ini **TIDAK PERLU** diisi di sini. Perhitungan capaian akan diambil dari **Progress Project Turunan** tersebut.
            """)
            return
        # -------------------------------------------------------------
        
        period_type = selected_item['period_type']
        
        # Use item's specific dates, falling back to project dates if item dates are missing/outside project range
        p_start = datetime.strptime(project_start_date_str, "%Y-%m-%d").date()
        p_end = datetime.strptime(project_end_date_str, "%Y-%m-%d").date()

        item_start_str = selected_item['start_date']
        item_end_str = selected_item['end_date']
        
        # Fallback to project dates (p_start, p_end) if item date is None
        item_start = datetime.strptime(item_start_str, "%Y-%m-%d").date() if item_start_str else p_start
        item_end = datetime.strptime(item_end_str, "%Y-%m-%d").date() if item_end_str else p_end

    st.markdown(f"**Periodization:** {period_type.capitalize()} | **Item Range:** {item_start} to {item_end}")
        
        # Generate periods
    periods = generate_periods(item_start, item_end, period_type)
        
        # Fetch existing targets for the item
    existing_targets = {
            (datetime.strptime(t['period_start_date'], "%Y-%m-%d").date(), datetime.strptime(t['period_end_date'], "%Y-%m-%d").date()): t['target_value']
            for t in fetchall("SELECT * FROM periodic_targets WHERE item_id=?", (item_id,))
        }

        # Form to set targets
    with st.form(f"set_targets_form_{item_id}"):
            st.markdown("Fill in the **Target Value** column for each period. Leave 0 if there is no target.")
            
            targets_to_save = []
            
            for p_period in periods:
                period_str = f"Target {p_period['period_name']} ({p_period['start']} - {p_period['end']})"
                default_val = existing_targets.get((p_period['start'], p_period['end']), 0.0)
                
                target_value = st.number_input(
                    period_str, 
                    min_value=0.0, 
                    value=float(default_val), 
                    step=1.0, 
                    key=f"target_{item_id}_{p_period['start'].isoformat()}"
                )
                targets_to_save.append({
                    "start": p_period['start'].isoformat(),
                    "end": p_period['end'].isoformat(),
                    "value": target_value
                })

            if st.form_submit_button(f"Save {len(periods)} Targets", type="primary"):
                conn = get_db()
                cur = conn.cursor()
                try:
                    # Clear existing targets for this item to perform a clean update
                    cur.execute("DELETE FROM periodic_targets WHERE item_id=?", (item_id,))
                    
                    # Insert new targets (only if value is not None)
                    for t in targets_to_save:
                        if t['value'] is not None:
                            cur.execute("""
                                INSERT INTO periodic_targets (item_id, period_start_date, period_end_date, target_value)
                                VALUES (?, ?, ?, ?)
                            """, (item_id, t['start'], t['end'], t['value']))
                    
                    conn.commit()
                    execute("INSERT INTO audit_logs (project_id, user_id, action, details) VALUES (?,?,?,?)",
                            (project_id, current_user()['id'], "UPDATE_TARGET", f"Updated targets for Item ID {item_id}"))
                    
                    st.success("Periodic targets updated successfully. Target input form has been reset.")
                    st.rerun()
                except Exception as e:
                    conn.rollback()
                    st.error(f"Failed to save targets: {e}")
                finally:
                    conn.close()

def render_delegation_form(project_id):
    # ... (No change in this function) ...
    st.subheader("🤝 Manage Project Item Delegation")
    
    # Fetch data relevant to the project
    items_for_project = fetchall("SELECT i.id, i.name, g.group_type FROM items i JOIN groups g ON i.group_id=g.id WHERE g.project_id=?", (project_id,))
    item_map = {f"({i['id']}) {i['name']} - {i['group_type']}": i['id'] for i in items_for_project}
    item_options = ["(Optional) Project Level Delegation"] + list(item_map.keys())

    users = fetchall("SELECT id, name FROM users WHERE approved=1")
    users_map = {u['id']: u['name'] for u in users}
    user_options = list(users_map.keys())
    
    if not user_options:
        st.warning("No approved users. Admin must approve users first.")
        return

    with st.form(f"create_delegation_form_{project_id}"):
        item_selection_name = st.selectbox("Select Item", options=item_options, key=f"del_item_sel_{project_id}")
        item_id = item_map.get(item_selection_name)

        # Set default for delegator to current user
        default_delegator_index = user_options.index(current_user().get('id')) if current_user().get('id') in user_options else 0
        delegator_id = st.selectbox("Delegator (Original PIC)", options=user_options, format_func=lambda x: users_map[x], key=f"delegator_sel_{project_id}", index=default_delegator_index)

        delegatee_id = st.selectbox("Delegatee", options=user_options, format_func=lambda x: users_map[x], key=f"delegatee_sel_{project_id}")

        col_d_start, col_d_end = st.columns(2)
        sd = col_d_start.date_input("Start Date", value=date.today(), key=f"del_start_date_{project_id}")
        ed = col_d_end.date_input("End Date (optional)", value=None, key=f"del_end_date_{project_id}")

        if st.form_submit_button("Create Delegation", type="primary"):
            # Basic validation
            if not delegator_id or not delegatee_id:
                st.error("Delegator and Delegatee must be selected.")
            elif delegator_id == delegatee_id:
                st.error("Delegator and Delegatee cannot be the same.")
            else:
                execute("INSERT INTO delegations (project_id,item_id,delegator_id,delegatee_id,start_date,end_date) VALUES (?,?,?,?,?,?)", 
                        (project_id, item_id, delegator_id, delegatee_id, sd.isoformat(), ed.isoformat() if ed else None))
                st.success("Delegation created successfully.")
                execute("INSERT INTO audit_logs (project_id, user_id, action, details) VALUES (?,?,?,?)",
                        (project_id, current_user()['id'], "CREATE_DELEGATION", f"Delegated item {item_id} to user {delegatee_id}"))
                st.rerun()

    st.markdown("---")
    st.subheader("Active Delegations for This Project")
    dels = fetchall("SELECT * FROM delegations WHERE project_id=? ORDER BY end_date DESC", (project_id,))
    if dels:
        df_del = pd.DataFrame(dels)
        users = fetchall("SELECT id, name FROM users WHERE approved=1")
        users_map = {u['id']: u['name'] for u in users}
        df_del['Delegator'] = df_del['delegator_id'].map(users_map)
        df_del['Delegatee'] = df_del['delegatee_id'].map(users_map)
        st.dataframe(df_del[[
            'item_id', 'Delegator', 'Delegatee', 'start_date', 'end_date'
        ]].rename(columns={'item_id': 'Item ID (Null=Project)'}), use_container_width=True, hide_index=True)
    else:
        st.info("No delegations for this project.")

def render_realization_input_form(project_id):
    st.subheader("📈 Input Realisasi Nilai Item")

    # Fetch items for the project
    items = fetchall("""
        SELECT i.id, i.name, i.uom, i.start_date, i.end_date, g.group_type
        FROM items i JOIN groups g ON i.group_id=g.id
        WHERE g.project_id=?
    """, (project_id,))

    if not items:
        st.info("Tidak ada Item di proyek ini. Harap tambahkan Item di tab 'Groups & Items' terlebih dahulu.")
        return

    item_map = {f"({i['id']}) {i['name']} ({i['group_type']} - {i['uom']})": i for i in items}
    item_key = st.selectbox("Pilih Item", options=list(item_map.keys()), key=f"real_item_sel_{project_id}")
    
    selected_item = item_map.get(item_key)

    if selected_item:
        item_id = selected_item['id']
        
        # --- NEW CHECK: IS THIS ITEM A PARENT OF ANOTHER PROJECT? ---
        child_project = fetchone("SELECT id, name FROM projects WHERE parent_project_id=?", (item_id,))
        if child_project:
            st.warning(f"""
                ⚠️ **Item ini telah menjadi Parent (Induk) dari Project Turunan:** **{child_project['name']}** (ID: {child_project['id']}).
                
                Nilai realisasi Item ini **TIDAK PERLU** diisi di sini. Perhitungan capaian akan diambil dari **Progress Project Turunan** tersebut.
            """)
            return
        # -------------------------------------------------------------
        
        # 1. Get Project start/end dates
        project_row = fetchone("SELECT start_date, finish_date FROM projects WHERE id=?", (project_id,))
        p_start = datetime.strptime(project_row['start_date'], "%Y-%m-%d").date()
        p_end = datetime.strptime(project_row['finish_date'], "%Y-%m-%d").date()
        
        # 2. Month Selection
        valid_months = get_valid_months_in_range(p_start, p_end)
        if not valid_months:
            st.error("Rentang proyek tidak valid untuk input realisasi.")
            return

        month_options = {m.strftime("%B %Y"): m for m in valid_months}
        
        # Set default to the current month if available, otherwise the first month
        default_month_key = date.today().strftime("%B %Y")
        if default_month_key not in month_options:
            default_month_key = list(month_options.keys())[0]

        selected_month_key = st.selectbox(
            "Pilih Bulan", 
            options=list(month_options.keys()), 
            index=list(month_options.keys()).index(default_month_key),
            key=f"real_month_sel_{project_id}"
        )
        selected_month_date = month_options[selected_month_key]

        # Calculate month start and end dates for filtering weeks
        month_start_date = selected_month_date
        # month_end_date = (selected_month_date + relativedelta(months=1)) - timedelta(days=1)
        
        # Generate weeks spanning around the selected month
        weeks_around_month = generate_weeks_by_thursday(
            month_start_date - timedelta(days=7), 
            month_start_date + relativedelta(months=1) + timedelta(days=6) # ensure we cover the next month's start week
        )
        
        # Filter only weeks where the Thursday belongs to the selected month (Thursday Rule)
        weeks_in_month = [
            w for w in weeks_around_month 
            if w['month_assigned'] == month_start_date.month and w['year_assigned'] == month_start_date.year
        ]

        if not weeks_in_month:
            st.error(f"Tidak ada minggu yang valid (Kamis di bulan {selected_month_key}).")
            return

        # Create week options (e.g., "Week 1 (01 Jan - 07 Jan)")
        week_options_map = {}
        for i, w in enumerate(weeks_in_month):
            week_label = (
                f"Week {i+1} ({w['week_start'].strftime('%d %b')} - {w['week_end'].strftime('%d %b')}) "
                f"- Tanggal Realisasi: {w['week_thursday'].strftime('%d %b %Y')}"
            )
            # The actual date used for realization is the Thursday
            week_options_map[week_label] = w['week_thursday']

        # 3. Week Selection
        selected_week_key = st.selectbox(
            "Pilih Minggu (Tanggal Realisasi = Hari Kamis)",
            options=list(week_options_map.keys()),
            key=f"real_week_sel_{project_id}"
        )
        
        realization_date = week_options_map[selected_week_key]
        
        st.info(f"Tanggal Realisasi yang akan dicatat: **{realization_date.isoformat()}**")
        # --- END NEW DATE SELECTION LOGIC ---

        # Form to input realization
        with st.form(f"input_realization_form_{item_id}"):
            st.markdown(f"**Item:** {selected_item['name']} | **UoM:** {selected_item['uom']}")
            
            # Display the derived date, no need for date_input anymore
            st.markdown(f"Tanggal Realisasi: **{realization_date.isoformat()}**")
            
            realized_value = st.number_input(f"Nilai Realisasi ({selected_item['uom']})", value=0.0, step=0.1, key=f"real_value_{item_id}")

            if st.form_submit_button("Simpan Realisasi", type="primary"):
                if realized_value is None or realization_date is None:
                    st.error("Semua field harus diisi.")
                else:
                    # Check if the realization date is within the Item's start/end dates
                    item_start = datetime.strptime(selected_item['start_date'], "%Y-%m-%d").date() if selected_item['start_date'] else p_start
                    item_end = datetime.strptime(selected_item['end_date'], "%Y-%m-%d").date() if selected_item['end_date'] else p_end

                    if realization_date < item_start or realization_date > item_end:
                        st.error(f"Tanggal realisasi ({realization_date}) berada di luar rentang tanggal Item ({item_start} s/d {item_end}).")
                    else:
                        execute("INSERT INTO realizations (item_id, realized_value, realized_date, recorded_by) VALUES (?,?,?,?)",
                                (item_id, realized_value, realization_date.isoformat(), current_user()['id']))
                        
                        # --- SUCCESS NOTIFICATION & CLEAR FORM (via rerun) ---
                        st.success(f"Realisasi {realized_value} berhasil dicatat untuk tanggal {realization_date.isoformat()}. Form direset.")
                        execute("INSERT INTO audit_logs (project_id, user_id, action, details) VALUES (?,?,?,?)",
                                (project_id, current_user()['id'], "INPUT_REALIZATION", f"Input realization for Item ID {item_id}: {realized_value}"))
                        st.rerun()
                        # -----------------------------------------------------

        st.markdown("---")
        col_hist, col_tbl = st.columns(2)
        with col_hist:
            st.subheader("Riwayat Realisasi Terbaru")
            
            realizations = fetchall("SELECT * FROM realizations WHERE item_id=? ORDER BY realized_date DESC LIMIT 10", (item_id,))
            
            if realizations:
                real_df = pd.DataFrame(realizations)
                st.dataframe(real_df[[
                    'realized_date', 'realized_value', 'recorded_at'
                ]].rename(columns={'realized_date': 'Tanggal', 'realized_value': 'Nilai', 'recorded_at': 'Dicatat Pada'}), use_container_width=True, hide_index=True)
            else:
                st.info("Belum ada realisasi yang dicatat untuk item ini.")
        with col_tbl:
            st.subheader("Tabel Realisasi (Per Minggu)")
            all_real = fetchall("SELECT realized_date, realized_value, recorded_at FROM realizations WHERE item_id=? ORDER BY date(realized_date) ASC, recorded_at ASC", (item_id,))
            if all_real:
                # Gunakan nilai terakhir (revisi terbaru) per tanggal realisasi
                rec_map = {}
                for r in all_real:
                    rd = r['realized_date']
                    ra = r['recorded_at'] or ""
                    if rd not in rec_map or rec_map[rd]['recorded_at'] < ra:
                        rec_map[rd] = {'realized_value': r['realized_value'], 'recorded_at': ra}
                recognized = sorted([(d, v['realized_value']) for d, v in rec_map.items()], key=lambda x: x[0])
                rows_week = []
                for rd, val in recognized:
                    d = datetime.strptime(rd, "%Y-%m-%d").date()
                    week_start = d - timedelta(days=d.weekday())
                    week_end = week_start + timedelta(days=6)
                    week_label = f"Week ({week_start.strftime('%d %b')} - {week_end.strftime('%d %b')}) - Thu {d.strftime('%d %b %Y')}"
                    rows_week.append({"Date (Week)": week_label, "Realisasi": val})
                df_week = pd.DataFrame(rows_week)
                st.dataframe(df_week, use_container_width=True, hide_index=True)
            else:
                st.info("Belum ada data realisasi untuk ditampilkan.")

# -------------------------
# UI pages
# -------------------------

def page_project_charter():
    if not st.session_state.get('user'):
        st.error("Silakan login untuk mengakses halaman ini.")
        return

    st.header("Project Charter")
    st.markdown("---")
    tab_proj = st.tabs(["List Projects", "Create Project"])

    # List Projects
    with tab_proj[0]:
        st.subheader("Daftar Proyek")
        st.markdown("---")
        
        # Initialize state variables
        if "selected_project_id" not in st.session_state:
            st.session_state.selected_project_id = None

        # UI for selecting project detail
        col_list_proj, col_detail_proj = st.columns([3,1])
        
        with col_list_proj:
            search_query = st.text_input("Cari proyek berdasarkan nama")
            projects_df = pd.DataFrame(fetchall("SELECT p.*, u.name as pic_name FROM projects p LEFT JOIN users u ON p.pic_user_id=u.id"))
            
            if not projects_df.empty:
                projects_df['Status'] = projects_df.apply(calc_status, axis=1)
                
                if search_query:
                    projects_df = projects_df[projects_df['name'].str.contains(search_query, case=False, na=False)]
                
                # Check for empty after filtering
                if projects_df.empty:
                    st.info("Proyek tidak ditemukan.")
                else:
                    st.dataframe(projects_df[[
                        "id", "name", "department", "pic_name", "start_date", "finish_date", "Status"
                    ]].set_index("id"), use_container_width=True)
            else:
                st.info("Belum ada project.")
        
        with col_detail_proj:
            st.subheader("Lihat Detail")
            # Dropdown project selector
            all_projects = fetchall("SELECT id, name FROM projects ORDER BY id DESC")
            if all_projects:
                project_options = {f"[{p['id']}] {p['name']}": p['id'] for p in all_projects}
                selected_proj_label = st.selectbox("Pilih Proyek", options=list(project_options.keys()), key="selected_proj_dropdown")
                selected_id_input = project_options[selected_proj_label]
                if st.button("Lihat Detail", use_container_width=True, type="primary"):
                    st.session_state.selected_project_id = selected_id_input
                    st.rerun()
            else:
                st.info("Belum ada project.")

        # --- Project Detail View (starts here when a project is selected) ---
        if st.session_state.selected_project_id:
            selected_id = st.session_state.selected_project_id
            p = fetchone("SELECT p.*, u.name as pic_name FROM projects p LEFT JOIN users u ON p.pic_user_id=u.id WHERE p.id=?", (selected_id,))

            if not p:
                st.error("Project tidak ditemukan.")
                st.session_state.selected_project_id = None
            else:
                st.markdown("---")
                st.subheader(f"Detail Project: {p['name']}")
                
                # Check if this project is a child project (has a parent item ID)
                parent_item_id = p.get('parent_project_id')
                parent_info = None
                if parent_item_id:
                    parent_item = fetchone("""
                        SELECT i.name as item_name, p_parent.name as project_name
                        FROM items i
                        JOIN groups g ON i.group_id = g.id
                        JOIN projects p_parent ON g.project_id = p_parent.id
                        WHERE i.id=?
                    """, (parent_item_id,))
                    if parent_item:
                        parent_info = f"Turunan dari Item **{parent_item['item_name']}** di Proyek **{parent_item['project_name']}**"

                st.markdown(f"**ID:** {p['id']} | **Dept:** {p['department']} | **PIC:** {p.get('pic_name') or 'N/A'}")
                st.markdown(f"**Periode:** {p['start_date']} s/d {p['finish_date']}")
                if parent_info:
                    st.markdown(f"**Status:** {parent_info}")
                
                # --- Get current group weights for pre-filling EDIT form ---
                current_groups = fetchall("SELECT group_type, group_weight FROM groups WHERE project_id=?", (p['id'],))
                current_weights = {g['group_type']: g['group_weight'] for g in current_groups}
                default_activity_weight = current_weights.get('ACTIVITY', 50.0)
                # -----------------------------------------------------------
                
                tab_item, tab_target, tab_delegation, tab_realization, tab_manage = st.tabs([
                    "📋 Groups & Items", 
                    "🎯 Set Target Periodik", 
                    "🤝 Kelola Delegasi",
                    "📈 Input Realisasi",
                    "⚙️ Kelola Proyek" 
                ])

                # 1. Tab: Groups & Items
                with tab_item:
                    st.subheader("Daftar Groups & Items")
                    
                    project_start = datetime.strptime(p['start_date'], "%Y-%m-%d").date()
                    project_end = datetime.strptime(p['finish_date'], "%Y-%m-%d").date()
                    
                    # Fetch common data for forms
                    users = fetchall("SELECT id, name FROM users WHERE approved=1")
                    user_map = {u['name']: u['id'] for u in users}
                    user_names = list(user_map.keys())
                    groups_for_select = fetchall("SELECT id, group_type FROM groups WHERE project_id=?", (p['id'],))
                    group_map = {f"{g['group_type']} (ID: {g['id']})": g['id'] for g in groups_for_select}


                    # --- FORM: ADD ITEM ---
                    if group_map:
                        with st.expander("➕ Tambah Item ke Group", expanded=False):
                            with st.form("add_item_form"):
                                
                                i_group_name = st.selectbox("Pilih Group", options=list(group_map.keys()), key=f"i_group_{p['id']}")
                                i_group_id = group_map[i_group_name]
                                i_name = st.text_input("Nama Item (Contoh: Jumlah User, Tingkat Kehadiran)", key=f"i_name_{p['id']}")
                                
                                col_i1, col_i2, col_i3 = st.columns(3)
                                i_weight = col_i1.number_input("Bobot Item (%)", min_value=0.0, max_value=100.0, step=0.1, key=f"i_weight_{p['id']}")
                                i_uom = col_i2.text_input("UoM (Unit of Measure)", value="Unit", key=f"i_uom_{p['id']}")
                                i_pol = col_i3.selectbox("Polarisasi", options=["MAX", "MIN", "STABLE"])

                                col_i4, col_i5, col_i6 = st.columns(3)
                                i_period = col_i4.selectbox("Tipe Periode Target", options=['weekly', 'monthly', 'quarterly', 'semester', 'yearly'])
                                i_rollup = col_i5.selectbox("Metode Rollup", options=['SUMMARY', 'AVERAGE', 'LATEST'])

                                i_pic_name = col_i6.selectbox("PIC Item", options=user_names)
                                i_pic_id = user_map.get(i_pic_name)

                                col_i_date1, col_i_date2 = st.columns(2)
                                
                                i_start = col_i_date1.date_input("Tanggal Mulai Item", value=project_start)
                                i_end = col_i_date2.date_input("Tanggal Selesai Item", value=project_end)

                                if st.form_submit_button("Simpan Item", type="secondary"):
                                    if not i_name or i_weight is None or not i_pic_id:
                                        st.error("Nama Item, Bobot, dan PIC harus diisi.")
                                    elif i_weight <= 0:
                                        st.error("Bobot harus lebih besar dari 0.")
                                    else:
                                        execute("INSERT INTO items (group_id, name, item_weight, uom, polarisasi, period_type, rollup, pic_user_id, start_date, end_date) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                                (i_group_id, i_name, i_weight, i_uom, i_pol, i_period, i_rollup, i_pic_id, i_start.isoformat(), i_end.isoformat()))
                                        
                                        st.success(f"Item **{i_name}** berhasil ditambahkan. Form direset.")
                                        st.rerun()
                    else:
                         st.warning("⚠️ **Tambahkan minimal satu Group (Activity/Success) terlebih dahulu** untuk dapat menambah Item. (Atur di tab 'Kelola Proyek')")

                    st.markdown("---") # Separator
                    # Tata fitur utama horizontal
                    col_turunan, col_edit, col_delete = st.columns(3)

                    with col_turunan:
                        st.subheader("🔗 Buat Project Turunan dari Key Activity")
                        # --- NEW FEATURE: CREATE CHILD PROJECT FROM ITEM ---
                        items_for_child = fetchall("""
                            SELECT i.id, i.name, g.group_type, i.start_date, i.end_date
                            FROM items i JOIN groups g ON i.group_id=g.id
                            WHERE g.project_id=? AND g.group_type='ACTIVITY'
                            ORDER BY i.id
                        """, (p['id'],))
                        if not items_for_child:
                            st.info("Tidak ada Key Activity di proyek ini untuk dijadikan project turunan.")
                        else:
                            item_child_map = {f"({i['id']}) {i['name']}": i for i in items_for_child}
                            selected_item_key_child = st.selectbox("Pilih Key Activity untuk dijadikan Turunan Project", options=list(item_child_map.keys()), key=f"item_to_child_sel_{p['id']}h")
                            item_to_child = item_child_map.get(selected_item_key_child)
                            item_id_to_child = item_to_child['id'] if item_to_child else None
                            if item_id_to_child:
                                existing_child = fetchone("SELECT id, name FROM projects WHERE parent_project_id=?", (item_id_to_child,))
                                if existing_child:
                                    st.warning(f"Project Turunan sudah ada: **{existing_child['name']}** (ID: {existing_child['id']}).")
                                else:
                                    with st.form(f"create_child_project_form_{item_id_to_child}"):
                                        default_child_name = f"Child - {item_to_child['name']}"
                                        new_child_name = st.text_input("Nama Project Turunan", value=default_child_name, key=f"child_name_{item_id_to_child}")
                                        p_start_date = datetime.strptime(p['start_date'], "%Y-%m-%d").date()
                                        p_finish_date = datetime.strptime(p['finish_date'], "%Y-%m-%d").date()
                                        col_c_s, col_c_f = st.columns(2)
                                        child_start = col_c_s.date_input("Tanggal Mulai Turunan", value=p_start_date, key=f"child_start_{item_id_to_child}")
                                        child_finish = col_c_f.date_input("Tanggal Selesai Turunan", value=p_finish_date, key=f"child_finish_{item_id_to_child}")
                                        parent_pic_row = fetchone("SELECT name FROM users WHERE id=?", (p['pic_user_id'],))
                                        parent_pic_name = parent_pic_row['name'] if parent_pic_row else list(user_map.keys())[0] if user_map else "None"
                                        child_pic_name = st.selectbox("PIC Project Turunan", options=list(user_map.keys()), index=list(user_map.keys()).index(parent_pic_name) if parent_pic_name in user_map else 0, key=f"child_pic_{item_id_to_child}")
                                        child_pic_id = user_map.get(child_pic_name)
                                        if st.form_submit_button(f"Buat Project Turunan dari Item ID {item_id_to_child}", type="primary"):
                                            new_id = execute("INSERT INTO projects (name, department, pic_user_id, start_date, finish_date, parent_project_id, created_by) VALUES (?,?,?,?,?,?,?)", (new_child_name, p['department'], child_pic_id, child_start.isoformat(), child_finish.isoformat(), item_id_to_child, current_user()['id']))
                                            execute("INSERT INTO groups (project_id, group_type, group_weight) VALUES (?,?,?)", (new_id, "ACTIVITY", 50.0))
                                            execute("INSERT INTO groups (project_id, group_type, group_weight) VALUES (?,?,?)", (new_id, "SUCCESS", 50.0))
                                            execute("INSERT INTO audit_logs (project_id, user_id, action, details) VALUES (?,?,?,?)", (new_id, current_user()['id'], "CREATE_CHILD", f"Created child project ID {new_id} from Item ID {item_id_to_child}."))
                                            st.success(f"Project Turunan **{new_child_name}** berhasil dibuat dengan ID: **{new_id}** (Parent Item ID: {item_id_to_child}).")
                                            st.rerun()

                    with col_edit:
                        st.subheader("✏️ Edit Item")
                        items_for_edit = fetchall("""
                            SELECT i.id, i.name, g.group_type, i.group_id
                            FROM items i JOIN groups g ON i.group_id=g.id
                            WHERE g.project_id=?
                            ORDER BY i.id
                        """, (p['id'],))
                        if not items_for_edit:
                            st.info("Tidak ada Item untuk diedit.")
                        else:
                            item_edit_map = {f"({i['id']}) {i['name']} - {i['group_type']}": i['id'] for i in items_for_edit}
                            selected_item_key = st.selectbox("Pilih Item yang akan diedit", options=list(item_edit_map.keys()), key=f"edit_item_sel_{p['id']}h")
                            item_id_to_edit = item_edit_map.get(selected_item_key)
                            if item_id_to_edit:
                                current_item_data = fetchone("SELECT * FROM items WHERE id=?", (item_id_to_edit,))
                                item_is_parent = fetchone("SELECT id FROM projects WHERE parent_project_id=?", (item_id_to_edit,))
                                if current_item_data:
                                    group_map_rev = {g['id']: f"{g['group_type']} (ID: {g['id']})" for g in groups_for_select}
                                    current_group_label = group_map_rev.get(current_item_data['group_id'])
                                    group_names = list(group_map_rev.values())
                                    group_map_for_submit = {v: k for k, v in group_map_rev.items()}
                                    current_pic_name_row = fetchone("SELECT name FROM users WHERE id=?", (current_item_data['pic_user_id'],))
                                    current_pic_name = current_pic_name_row['name'] if current_pic_name_row else user_names[0] if user_names else "None"
                                    item_start_date_e = (datetime.strptime(current_item_data['start_date'], "%Y-%m-%d").date() if current_item_data['start_date'] else project_start)
                                    item_end_date_e = (datetime.strptime(current_item_data['end_date'], "%Y-%m-%d").date() if current_item_data['end_date'] else project_end)
                                    with st.form(f"edit_item_form_{item_id_to_edit}"):
                                        st.markdown(f"**Mengedit Item ID: {item_id_to_edit}**")
                                        if item_is_parent:
                                            st.info("Item ini adalah Parent Project Turunan. Beberapa field seperti **Polarisasi**, **Tipe Periode Target**, dan **Metode Rollup** **tidak lagi relevan** dan dapat diabaikan, karena skornya diambil dari Proyek Turunan.")
                                        i_group_name_e = st.selectbox("Pindah Group", options=group_names, index=group_names.index(current_group_label) if current_group_label in group_names else 0, key=f"i_group_e_{item_id_to_edit}")
                                        i_group_id_e = group_map_for_submit.get(i_group_name_e)
                                        i_name_e = st.text_input("Nama Item", value=current_item_data['name'], key=f"i_name_e_{item_id_to_edit}")
                                        col_i1_e, col_i2_e, col_i3_e = st.columns(3)
                                        i_weight_e = col_i1_e.number_input("Bobot Item (%)", min_value=0.0, max_value=100.0, step=0.1, value=float(current_item_data['item_weight']), key=f"i_weight_e_{item_id_to_edit}")
                                        i_uom_e = col_i2_e.text_input("UoM (Unit of Measure)", value=current_item_data['uom'], key=f"i_uom_e_{item_id_to_edit}")
                                        pol_options = ["MAX", "MIN", "STABLE"]
                                        i_pol_e = col_i3_e.selectbox("Polarisasi", options=pol_options, index=pol_options.index(current_item_data['polarisasi']), key=f"i_pol_e_{item_id_to_edit}")
                                        col_i4_e, col_i5_e, col_i6_e = st.columns(3)
                                        period_options = ['weekly', 'monthly', 'quarterly', 'semester', 'yearly']
                                        i_period_e = col_i4_e.selectbox("Tipe Periode Target", options=period_options, index=period_options.index(current_item_data['period_type']), key=f"i_period_e_{item_id_to_edit}")
                                        rollup_options = ['SUMMARY', 'AVERAGE', 'LATEST']
                                        i_rollup_e = col_i5_e.selectbox("Metode Rollup", options=rollup_options, index=rollup_options.index(current_item_data['rollup']), key=f"i_rollup_e_{item_id_to_edit}")
                                        i_pic_name_e = col_i6_e.selectbox("PIC Item", options=user_names, index=user_names.index(current_pic_name) if current_pic_name in user_names else 0, key=f"i_pic_name_e_{item_id_to_edit}")
                                        i_pic_id_e = user_map.get(i_pic_name_e)
                                        col_i_date1_e, col_i_date2_e = st.columns(2)
                                        i_start_e = col_i_date1_e.date_input("Tanggal Mulai Item", value=item_start_date_e, key=f"i_start_e_{item_id_to_edit}")
                                        i_end_e = col_i_date2_e.date_input("Tanggal Selesai Item", value=item_end_date_e, key=f"i_end_e_{item_id_to_edit}")
                                        if st.form_submit_button("Update Item", type="primary"):
                                            if not i_name_e or i_weight_e is None or not i_pic_id_e:
                                                st.error("Nama Item, Bobot, dan PIC harus diisi.")
                                            elif i_weight_e <= 0:
                                                st.error("Bobot harus lebih besar dari 0.")
                                            else:
                                                execute("""
                                                    UPDATE items 
                                                    SET group_id=?, name=?, item_weight=?, uom=?, polarisasi=?, period_type=?, rollup=?, pic_user_id=?, start_date=?, end_date=? 
                                                    WHERE id=?
                                                """,
                                                (i_group_id_e, i_name_e, i_weight_e, i_uom_e, i_pol_e, i_period_e, i_rollup_e, i_pic_id_e, i_start_e.isoformat(), i_end_e.isoformat(), item_id_to_edit))
                                                execute("INSERT INTO audit_logs (project_id, user_id, action, details) VALUES (?,?,?,?)", (p['id'], current_user()['id'], "UPDATE_ITEM", f"Updated Item ID {item_id_to_edit}: {i_name_e}"))
                                                st.success(f"Item **{i_name_e}** (ID: {item_id_to_edit}) berhasil diperbarui.")
                                                st.rerun()

                    with col_delete:
                        st.subheader("🗑️ Hapus Item")
                        # Dropdown daftar item milik project
                        items_for_delete = fetchall("SELECT i.id, i.name FROM items i JOIN groups g ON i.group_id=g.id WHERE g.project_id=? ORDER BY i.id", (p['id'],))
                        if not items_for_delete:
                            st.info("Tidak ada item untuk dihapus.")
                        else:
                            item_delete_map = {f"({i['id']}) {i['name']}": i['id'] for i in items_for_delete}
                            selected_item_delete_label = st.selectbox("Pilih Item yang akan dihapus", options=list(item_delete_map.keys()), key=f"item_id_to_delete_{p['id']}h_delete")
                            item_id_to_delete = item_delete_map[selected_item_delete_label]
                            if st.button(f"Hapus Item ID {item_id_to_delete}", key=f"delete_item_btn_{p['id']}h_delete", type="secondary"):
                                item_check = fetchone("SELECT i.id FROM items i JOIN groups g ON i.group_id=g.id WHERE i.id=? AND g.project_id=?", (item_id_to_delete, p['id']))
                                if item_check:
                                    try:
                                        delete_item(item_id_to_delete)
                                        st.success(f"Item ID **{item_id_to_delete}** berhasil dihapus beserta semua realisasi dan targetnya.")
                                        st.session_state.selected_project_id = None
                                        st.rerun()
                                    except Exception as e:
                                        st.error(f"Gagal menghapus item: {e}")
                                else:
                                    st.error(f"Item ID {item_id_to_delete} tidak ditemukan di proyek ini atau tidak valid.")
                    
                    # --- ITEM LIST DISPLAY (Existing Code) ---
                    conn = get_db()
                    cur = conn.cursor()
                    cur.execute("SELECT * FROM groups WHERE project_id=?", (p['id'],))
                    groups = [dict(r) for r in cur.fetchall()]
                    conn.close()

                    if not groups:
                        st.info("Belum ada Group (Activity/Success) untuk project ini.")
                    
                    for g in groups:
                        with st.expander(f"**{g['group_type']}** (Bobot: {g['group_weight']}%)", expanded=True):
                            items = fetchall("SELECT i.*, u.name as pic_name FROM items i LEFT JOIN users u ON i.pic_user_id=u.id WHERE group_id=?", (g['id'],))
                            
                            if items:
                                items_df = pd.DataFrame(items)
                                st.dataframe(items_df[[
                                    'id', 'name', 'item_weight', 'uom', 'polarisasi', 'period_type', 'rollup', 'pic_name', 'start_date', 'end_date'
                                ]].rename(columns={'id': 'ID Item'}), use_container_width=True, hide_index=True)
                            else:
                                st.info(f"Tidak ada item di grup {g['group_type']} ini.")
                    


                # 2. Tab: Set Target Periodik
                with tab_target:
                    render_periodic_target_form(p['id'], p['start_date'], p['finish_date'])

                # 3. Tab: Kelola Delegasi
                with tab_delegation:
                    render_delegation_form(p['id'])
                
                # 4. Tab: Input Realisasi
                with tab_realization:
                    render_realization_input_form(p['id'])

                # 5. Tab: Kelola Proyek (Edit/Delete)
                with tab_manage:
                    
                    # --- PROJECT EDIT FORM (Now includes Group Weight) ---
                    st.subheader("✏️ Edit Detail Proyek")
                    with st.form("edit_project_form"):
                        # Fetch necessary data (departments, users)
                        deps = [d['name'] for d in fetchall("SELECT * FROM departments")]
                        users = fetchall("SELECT id, name FROM users WHERE approved=1")
                        user_map = {u['name']: u['id'] for u in users}
                        
                        col_name_e, col_dept_e = st.columns(2)
                        p_name_e = col_name_e.text_input("Nama Project", value=p['name'], key=f"edit_name_{p['id']}")
                        p_dept_e = col_dept_e.selectbox("Departemen", options=deps, index=deps.index(p['department']) if p['department'] in deps else 0, key=f"edit_dept_{p['id']}")
                        
                        col_pic_e, col_parent_e = st.columns(2)
                        # Ensure current PIC name is handled gracefully
                        current_pic_name_row = fetchone("SELECT name FROM users WHERE id=?", (p['pic_user_id'],))
                        current_pic_name = current_pic_name_row['name'] if current_pic_name_row else list(user_map.keys())[0] if user_map else "None"
                        
                        pic_name_e = col_pic_e.selectbox("PIC Project", options=list(user_map.keys()), index=list(user_map.keys()).index(current_pic_name) if current_pic_name in user_map else 0, key=f"edit_pic_name_{p['id']}")
                        pic_user_id_e = user_map.get(pic_name_e)
                        
                        parent_projects = fetchall("SELECT id, name FROM projects WHERE id != ?", (p['id'],)) # Exclude current project
                        parent_map = {p_proj['name']: p_proj['id'] for p_proj in parent_projects}
                        
                        # --- MODIFIED PARENT SELECTION: Project Parent ONLY ---
                        parent_options = ["None"] + list(parent_map.keys())
                        
                        # Check if parent_project_id refers to a project (ID > max item ID, simple heuristic)
                        # Since we use parent_project_id for both Project Parent ID and Item Parent ID,
                        # this simple list view cannot reliably display the Project Parent name if it's an Item Parent.
                        # We will only allow changing to another Project Parent here.
                        
                        # Assuming the project is currently a child of an ITEM if parent_project_id is set.
                        # If it is an Item child, setting a new Project Parent will clear the Item Parent link (parent_project_id).
                        # For simplicity, we only allow linking to *another project* here.
                        
                        parent_name_e = col_parent_e.selectbox("Parent Project (opsional)", 
                                                            options=parent_options, 
                                                            key=f"edit_parent_name_{p['id']}")
                        
                        new_parent_id_value = parent_map.get(parent_name_e) # This will be either a Project ID or None

                        st.info("Catatan: Jika proyek ini adalah Turunan dari Item Key Activity, mengubah Parent Project di sini akan memutuskan hubungan Parent Item.")
                        
                        col_date_s_e, col_date_f_e = st.columns(2)
                        p_start_e = col_date_s_e.date_input("Tanggal Mulai", value=datetime.strptime(p['start_date'], "%Y-%m-%d").date(), key=f"edit_start_date_{p['id']}")
                        p_finish_e = col_date_f_e.date_input("Tanggal Selesai", value=datetime.strptime(p['finish_date'], "%Y-%m-%d").date(), key=f"edit_finish_date_{p['id']}")
                        
                        # --- BOBOT GROUP INDUK ---
                        st.markdown("---")
                        st.subheader("Bobot Induk Proyek (Total Wajib 100%)")
                        col_weight_act_e, col_weight_suc_e = st.columns(2)
                        
                        p_activity_weight_e = col_weight_act_e.number_input(
                            "Bobot Key ACTIVITY (%)", 
                            min_value=0.0, max_value=100.0, step=1.0, 
                            value=float(default_activity_weight), 
                            key=f"p_activity_weight_edit_{p['id']}"
                        )
                        p_success_weight_e = 100.0 - p_activity_weight_e
                        col_weight_suc_e.info(f"Bobot Key SUCCESS (%): **{p_success_weight_e:.1f}**")
                        # -------------------------
                        
                        if st.form_submit_button("Update Proyek", type="primary"):
                            conn = get_db()
                            cur = conn.cursor()
                            
                            try:
                                # 1. Update Project Details
                                # Note: If parent_name_e is not "None", new_parent_id_value will be a Project ID.
                                # This operation will override the existing parent_project_id (which might be an Item ID),
                                # effectively clearing the Item-to-Project Turunan link if a Project-to-Project link is set.
                                cur.execute("UPDATE projects SET name=?, department=?, pic_user_id=?, start_date=?, finish_date=?, parent_project_id=? WHERE id=?",
                                            (p_name_e, p_dept_e, pic_user_id_e, p_start_e.isoformat(), p_finish_e.isoformat(), new_parent_id_value, p['id']))

                                # 2. Update/Insert Groups (Activity and Success)
                                cur.execute("SELECT id FROM groups WHERE project_id=? AND group_type='ACTIVITY'", (p['id'],))
                                existing_act = cur.fetchone()
                                if existing_act:
                                    cur.execute("UPDATE groups SET group_weight=? WHERE id=?", (p_activity_weight_e, existing_act['id']))
                                else:
                                    cur.execute("INSERT INTO groups (project_id, group_type, group_weight) VALUES (?,?,?)", (p['id'], 'ACTIVITY', p_activity_weight_e))

                                cur.execute("SELECT id FROM groups WHERE project_id=? AND group_type='SUCCESS'", (p['id'],))
                                existing_suc = cur.fetchone()
                                if existing_suc:
                                    cur.execute("UPDATE groups SET group_weight=? WHERE id=?", (p_success_weight_e, existing_suc['id']))
                                else:
                                    cur.execute("INSERT INTO groups (project_id, group_type, group_weight) VALUES (?,?,?)", (p['id'], 'SUCCESS', p_success_weight_e))
                                
                                conn.commit()
                                
                                execute("INSERT INTO audit_logs (project_id, user_id, action, details) VALUES (?,?,?,?)",
                                        (p['id'], current_user()['id'], "UPDATE", f"Updated project details and group weights: {p_name_e}"))
                                
                                st.success("Detail proyek dan bobot grup berhasil diperbarui.")
                                st.session_state.selected_project_id = p['id'] 
                                st.rerun()
                                
                            except Exception as e:
                                conn.rollback()
                                st.error(f"Gagal mengupdate proyek: {e}")
                            finally:
                                conn.close()
                    
                    # --- PROJECT DELETE FEATURE ---
                    st.markdown("---")
                    st.subheader("❌ Hapus Proyek")
                    st.warning("⚠️ **PERHATIAN:** Menghapus proyek akan menghapus SEMUA data terkait. Tindakan ini **TIDAK** dapat dibatalkan.")
                    
                    if st.button(f"Hapus Proyek '{p['name']}' ({p['id']}) secara Permanen", type="secondary"):
                        # Cascading Delete manually
                        conn = get_db()
                        cur = conn.cursor()
                        
                        try:
                            # 1. Delete realizations
                            cur.execute("DELETE FROM realizations WHERE item_id IN (SELECT id FROM items WHERE group_id IN (SELECT id FROM groups WHERE project_id=?))", (p['id'],))
                            # 2. Delete periodic_targets
                            cur.execute("DELETE FROM periodic_targets WHERE item_id IN (SELECT id FROM items WHERE group_id IN (SELECT id FROM groups WHERE project_id=?))", (p['id'],))
                            # 3. Delete delegations
                            cur.execute("DELETE FROM delegations WHERE project_id=?", (p['id'],))
                            # 4. Delete items
                            cur.execute("DELETE FROM items WHERE group_id IN (SELECT id FROM groups WHERE project_id=?)", (p['id'],))
                            # 5. Delete groups
                            cur.execute("DELETE FROM groups WHERE project_id=?", (p['id'],))
                            # 6. Delete audit logs related to project
                            cur.execute("DELETE FROM audit_logs WHERE project_id=?", (p['id'],))
                            # 7. Finally, delete the project itself
                            cur.execute("DELETE FROM projects WHERE id=?", (p['id'],))
                            
                            conn.commit()
                            
                            st.session_state.selected_project_id = None
                            st.success(f"Proyek '{p['name']}' berhasil dihapus secara permanen.")
                            st.rerun()
                            
                        except Exception as e:
                            conn.rollback()
                            st.error(f"Gagal menghapus proyek: {e}")
                        finally:
                            conn.close()


    # Create Project Tab (tab_proj[1])
    # ... (No change in this function) ...
    with tab_proj[1]:
        st.subheader("Buat Project Charter Baru")
        with st.form("create_project_form"):
            col_name, col_dept = st.columns(2)
            # Menggunakan key agar form input direset
            p_name = col_name.text_input("Nama Project", key="p_name_create") 
            deps = [d['name'] for d in fetchall("SELECT * FROM departments")]
            p_dept = col_dept.selectbox("Departemen", options=deps, key="p_dept_create")
            
            col_pic, col_parent = st.columns(2)
            users = fetchall("SELECT id, name, department FROM users WHERE approved=1")
            user_map = {u['name']: u['id'] for u in users}
            pic_name = col_pic.selectbox("PIC Project", options=list(user_map.keys()), key="pic_name_create")
            pic_user_id = user_map.get(pic_name)
            
            parent_projects = fetchall("SELECT id, name FROM projects")
            parent_map = {p_proj['name']: p_proj['id'] for p_proj in parent_projects}
            parent_name = col_parent.selectbox("Parent Project (opsional)", options=["None"] + list(parent_map.keys()), key="parent_name_create")
            parent_id = parent_map.get(parent_name)
            
            col_date_s, col_date_f = st.columns(2)
            p_start = col_date_s.date_input("Tanggal Mulai", value=date.today(), key="p_start_create")
            p_finish = col_date_f.date_input("Tanggal Selesai", value=date.today() + relativedelta(months=6), key="p_finish_create")
            
            st.markdown("---")
            st.subheader("Bobot Induk Proyek (Total Wajib 100%)")
            col_weight_act, col_weight_suc = st.columns(2)
            
            p_activity_weight = col_weight_act.number_input("Bobot Key ACTIVITY (%)", min_value=0.0, max_value=100.0, step=1.0, value=50.0, key="p_activity_weight_create")
            p_success_weight = 100.0 - p_activity_weight
            col_weight_suc.info(f"Bobot Key SUCCESS (%): **{p_success_weight:.1f}**")
            
            if st.form_submit_button("Buat Project", type="primary"):
                if not p_name or not pic_user_id:
                    st.error("Nama Project dan PIC harus diisi.")
                else:
                    # 1. Insert Project
                    new_id = execute("INSERT INTO projects (name, department, pic_user_id, start_date, finish_date, parent_project_id, created_by) VALUES (?,?,?,?,?,?,?)",
                                    (p_name, p_dept, pic_user_id, p_start.isoformat(), p_finish.isoformat(), parent_id, current_user()['id']))
                    
                    # 2. Insert Groups (ACTIVITY and SUCCESS) with calculated weights
                    execute("INSERT INTO groups (project_id, group_type, group_weight) VALUES (?,?,?)",
                            (new_id, "ACTIVITY", p_activity_weight))
                    execute("INSERT INTO groups (project_id, group_type, group_weight) VALUES (?,?,?)",
                            (new_id, "SUCCESS", p_success_weight))
                            
                    # 3. Log
                    execute("INSERT INTO audit_logs (project_id, user_id, action, details) VALUES (?,?,?,?)",
                            (new_id, current_user()['id'], "CREATE", f"Created new project: {p_name}"))
                            
                    st.success(f"Project **{p_name}** berhasil dibuat dengan ID: **{new_id}**. Form direset.")
                    st.session_state.selected_project_id = new_id
                    st.rerun()

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

def page_dashboard():
    # Switch to wide mode for dashboard
    # ...existing code...
    require_login()
    st.header("📊 Dasbor Aplikasi")
    st.markdown("---")
    
    # Placeholder utility functions (must be defined in the main script scope if not provided here)
    def get_pending_projects_count():
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(DISTINCT T1.id) AS count FROM projects T1 LEFT JOIN groups T2 ON T1.id = T2.project_id LEFT JOIN items T3 ON T2.id = T3.group_id LEFT JOIN realizations T4 ON T3.id = T4.item_id WHERE T4.id IS NULL")
        count = cur.fetchone()['count']
        conn.close()
        return count

    def get_completed_projects_count():
        completed_count = 0
        projects = fetchall("SELECT id FROM projects")
        for p in projects:
            score = compute_project_score(p['id'])
            if score is not None and score >= 1.0:
                completed_count += 1
        return completed_count

    def get_total_users_count():
        return fetchone("SELECT COUNT(*) AS count FROM users")['count']

    def get_users_active_today():
        today_iso = date.today().isoformat()
        return fetchone("SELECT COUNT(DISTINCT user_id) AS count FROM audit_logs WHERE date(timestamp)=date(?)", (today_iso,))['count']

    def get_monthly_project_stats():
        today = date.today()
        this_month_start = today.replace(day=1).isoformat()
        last_month_start = (today.replace(day=1) - relativedelta(months=1)).isoformat()
        this_month_count = fetchone("SELECT COUNT(*) AS count FROM projects WHERE date(created_at) >= date(?)", (this_month_start,))['count']
        last_month_count = fetchone("SELECT COUNT(*) AS count FROM projects WHERE date(created_at) >= date(?) AND date(created_at) < date(?)", (last_month_start, this_month_start))['count']
        monthly_change = 0
        if last_month_count > 0:
            monthly_change = ((this_month_count - last_month_count) / last_month_count) * 100
        return this_month_count, monthly_change

    def get_upcoming_project_deadlines():
        today = date.today().isoformat()
        in_7_days = (date.today() + timedelta(days=7)).isoformat()
        return fetchall("SELECT * FROM projects WHERE date(finish_date) BETWEEN date(?) AND date(?) ORDER BY finish_date ASC", (today, in_7_days))

    def get_project_status_counts():
        projects = fetchall("SELECT id, start_date, finish_date FROM projects")
        status_counts = {"Not Started": 0, "Progress": 0, "Done": 0, "Overdue": 0}
        for p in projects:
            status = calc_status(p)
            status_counts[status] += 1
        return status_counts
    
    pending_projects_count = get_pending_projects_count()
    completed_projects_count = get_completed_projects_count()
    total_users_count = get_total_users_count()
    active_users_today = get_users_active_today()
    monthly_projects_count, monthly_change_percent = get_monthly_project_stats()
    
    st.markdown("""
    <style>
    .stat-card {
        background: #fff;
        border-radius: 16px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.07);
        padding: 1.5rem;
        transition: box-shadow 0.2s;
        margin-bottom: 0.5rem;
    }
    .stat-card:hover {
        box-shadow: 0 6px 18px rgba(0,0,0,0.13);
    }
    .stat-flex {
        display: flex;
        align-items: center;
        justify-content: space-between;
    }
    .stat-label {
        font-size: 1rem;
        color: #666;
        font-weight: 500;
        margin-bottom: 0.2rem;
    }
    .stat-value {
        font-size: 2.2rem;
        font-weight: bold;
        margin-bottom: 0.1rem;
    }
    .stat-delta {
        font-size: 1rem;
        color: #888;
    }
    .stat-iconbox {
        width: 48px; height: 48px;
        border-radius: 12px;
        display: flex; align-items: center; justify-content: center;
    }
    .stat-iconbox.orange { background: #fff7ed; }
    .stat-iconbox.green { background: #e6f9ed; }
    .stat-iconbox.blue { background: #e6f0fa; }
    .stat-iconbox.purple { background: #f3e8ff; }
    .stat-icon {
        width: 28px; height: 28px;
        stroke-width: 2.2;
    }
    .stat-icon.orange { color: #fb923c; }
    .stat-icon.green { color: #22c55e; }
    .stat-icon.blue { color: #2563eb; }
    .stat-icon.purple { color: #a21caf; }
    .stat-value.orange { color: #fb923c; }
    .stat-value.green { color: #22c55e; }
    .stat-value.blue { color: #2563eb; }
    .stat-value.purple { color: #a21caf; }
    </style>
    """, unsafe_allow_html=True)
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown(f"""
        <div class="stat-card">
          <div class="stat-flex">
            <div>
              <div class="stat-label">Proyek Pending</div>
              <div class="stat-value orange">{pending_projects_count}</div>
              <div class="stat-delta">Belum ada realisasi</div>
            </div>
            <div class="stat-iconbox orange">
              <svg class="stat-icon orange" viewBox="0 0 24 24" fill="none" stroke="currentColor">
                <circle cx="12" cy="12" r="10"/>
                <polyline points="12,6 12,12 16,14"/>
              </svg>
            </div>
          </div>
        </div>
        """, unsafe_allow_html=True)
    with col2:
        st.markdown(f"""
        <div class="stat-card">
          <div class="stat-flex">
            <div>
              <div class="stat-label">Proyek Selesai</div>
              <div class="stat-value green">{completed_projects_count}</div>
              <div class="stat-delta">Skor 100%</div>
            </div>
            <div class="stat-iconbox green">
              <svg class="stat-icon green" viewBox="0 0 24 24" fill="none" stroke="currentColor">
                <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/>
                <polyline points="22,4 12,14.01 9,11.01"/>
              </svg>
            </div>
          </div>
        </div>
        """, unsafe_allow_html=True)
    with col3:
        st.markdown(f"""
        <div class="stat-card">
          <div class="stat-flex">
            <div>
              <div class="stat-label">Total User</div>
              <div class="stat-value blue">{total_users_count}</div>
              <div class="stat-delta">{active_users_today} aktif hari ini</div>
            </div>
            <div class="stat-iconbox blue">
              <svg class="stat-icon blue" viewBox="0 0 24 24" fill="none" stroke="currentColor">
                <path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/>
                <circle cx="8.5" cy="7" r="4"/>
                <path d="M20 8v6"/>
                <path d="M23 11h-6"/>
              </svg>
            </div>
          </div>
        </div>
        """, unsafe_allow_html=True)
    with col4:
        delta_text = f"{monthly_change_percent:.0f}% dari bulan lalu"
        delta_color_class = "stat-delta" if monthly_change_percent >= 0 else "stat-delta"
        st.markdown(f"""
        <div class="stat-card">
          <div class="stat-flex">
            <div>
              <div class="stat-label">Proyek Bulan Ini</div>
              <div class="stat-value purple">{monthly_projects_count}</div>
              <div class="{delta_color_class}">{delta_text}</div>
            </div>
            <div class="stat-iconbox purple">
              <svg class="stat-icon purple" viewBox="0 0 24 24" fill="none" stroke="currentColor">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                <polyline points="14,2 14,8 20,8"/>
                <line x1="16" y1="13" x2="8" y2="13"/>
                <line x1="16" y1="17" x2="8" y2="17"/>
              </svg>
            </div>
          </div>
        </div>
        """, unsafe_allow_html=True)
        
    if current_user().get('role') == 'admin':
        pending_count = get_pending_users_count()
        st.markdown(f"""
        <div class="metric-card" style="grid-column: span 4; text-align: left; padding: 15px 20px; margin-top: 20px;">
            <h3>Persetujuan Pengguna Tertunda</h3>
            <p style="font-size: 2rem; display: inline-block; margin-right: 15px;">{pending_count}</p>
            <span style="font-size: 1rem; color: #6c757d;">Jumlah akun baru yang terdaftar menunggu persetujuan admin.</span>
        </div>
        """, unsafe_allow_html=True)

    # Project Status Chart
    st.markdown("---")
    st.subheader("Project Status Distribution")
    status_counts = get_project_status_counts()
    status_df = pd.DataFrame(status_counts.items(), columns=['Status', 'Count'])
    
    chart_pie = alt.Chart(status_df).mark_arc(outerRadius=120).encode(
        theta=alt.Theta("Count", stack=True),
        color=alt.Color("Status", scale=alt.Scale(
            domain=["Not Started", "Progress", "Done", "Overdue"],
            range=["#94a3b8", "#fb923c", "#22c55e", "#ef4444"]
        )),
        tooltip=["Status", "Count"]
    ).properties(title="Project Status Ratio")
    
    st.altair_chart(chart_pie, use_container_width=True)
        
    st.markdown("---")
    col_logs, col_deadlines = st.columns(2)
    
    with col_logs:
        st.subheader("Latest Activity Logs 📜")
        all_logs = fetchall("SELECT * FROM audit_logs ORDER BY timestamp DESC LIMIT 10")
        logs_df = pd.DataFrame(all_logs)
        if not logs_df.empty:
            logs_df['timestamp'] = pd.to_datetime(logs_df['timestamp']).dt.strftime('%d-%b-%Y %H:%M:%S')
            st.dataframe(logs_df[['timestamp', 'action', 'details']], use_container_width=True, hide_index=True)
        else:
            st.info("No activity logs recorded.")
    
    with col_deadlines:
        st.subheader("Upcoming Project Deadlines ⏰")
        deadline_projects = get_upcoming_project_deadlines()
        
        if deadline_projects:
            deadline_df = pd.DataFrame(deadline_projects)
            st.dataframe(deadline_df[[
                "id", "name", "finish_date", "department"
            ]].sort_values(by="finish_date"),
            column_config={
                "id": "Project ID",
                "name": "Project Name",
                "finish_date": st.column_config.DatetimeColumn("Deadline", format="DD-MM-YYYY"),
                "department": "Department"
            },
            hide_index=True, use_container_width=True)
        else:
            st.info("No projects with upcoming deadlines.")

def page_resume():
    require_login()
    st.title("Resume - Project Charter List")
    st.markdown("---")
    deps = [d['name'] for d in fetchall("SELECT * FROM departments")]
    deps.insert(0, "ALL")
    dept_filter = st.selectbox("Department Filter", deps)
    month_filter = st.date_input("Month Filter (select any day in the desired month)", value=date.today())
    # normalize month to first day
    mf = date(month_filter.year, month_filter.month, 1)
    # fetch projects
    projects = fetchall("SELECT * FROM projects")
    
    # Filter out projects that are children of an item, as they should only be viewed as part of the parent project.
    # However, to display the monthly progress of the child project for comparison/review, we should keep them in the list.
    # We will adjust the display of 'Monthly Progress' and 'Up-to-Month (%)' for the resume page.

    if dept_filter != "ALL":
        projects = [p for p in projects if p['department']==dept_filter]
    
    # Function to determine status based on ratio
    def get_status(ratio):
        if ratio is None:
            return "No Data"
        if ratio > 1.20:
            return "Outstanding"
        elif ratio >= 1.10:
            return "Above Target"
        elif ratio >= 0.90:
            return "On Target"
        elif ratio >= 0.70:
            return "Below Target"
        elif ratio > 0.50:
            return "Underperforming"
        else:
            return "Critical"
    
    # build table entries
    rows = []
    for p in projects:
        start = datetime.strptime(p['start_date'], "%Y-%m-%d").date()
        finish = datetime.strptime(p['finish_date'], "%Y-%m-%d").date()
        
        # monthly progress is compute_project_score for that month
        start_period = mf
        end_period = (mf + relativedelta(months=1)) - timedelta(days=1)
        monthly_score = compute_project_score(p['id'], start_period, end_period)
        
        # up-to-month (cumulative from start to selected month)
        up_to_end = end_period
        up_score = compute_project_score(p['id'], datetime.strptime(p['start_date'], "%Y-%m-%d").date(), up_to_end)
        
        # Get Key Success target goal
        key_success_score = compute_group_type_score(p['id'], "SUCCESS", start_period, end_period)
        
        # Calculate ratios with safe fallback to avoid N/A when target unavailable
        if key_success_score is None or key_success_score <= 0:
            monthly_ratio = monthly_score if monthly_score is not None else 0.0
            up_to_month_ratio = up_score if up_score is not None else 0.0
            key_success_target_pct = 100.0
        else:
            monthly_ratio = (monthly_score if monthly_score is not None else 0.0) / key_success_score
            up_to_month_ratio = (up_score if up_score is not None else 0.0) / key_success_score
            key_success_target_pct = key_success_score * 100
        
        # Check if project is a child of an item
        is_item_child = fetchone("SELECT id FROM items WHERE id=?", (p['parent_project_id'],))
        
        project_name = p['name']
        if is_item_child:
             project_name = f"Child: {p['name']}" # Mark as child project
             
        rows.append({
            "Project": project_name,
            "Dept": p['department'],
            "PIC": p['pic_user_id'],
            "Start": p['start_date'],
            "Finish": p['finish_date'],
            "Key Success Target": round(key_success_target_pct, 2),
            "Monthly Progress": round(monthly_score*100, 2) if monthly_score is not None else None,
            "Monthly Status": get_status(monthly_ratio),
            "Monthly Achievement": f"{round(monthly_ratio*100, 2)}%",
            "Up To Month Progress": round(up_score*100, 2) if up_score is not None else None,
            "Up To Month Status": get_status(up_to_month_ratio),
            "Up To Month Achievement": f"{round(up_to_month_ratio*100, 2)}%"
        })
    
    
    # Display the table with better formatting
    import pandas as pd
    df = pd.DataFrame(rows)
    if not df.empty:
        st.dataframe(
            df,
            column_config={
                "Project": "Project",
                "Dept": "Department",
                "PIC": "PIC",
                "Start": "Start",
                "Finish": "Finish",
                "Key Success Target": st.column_config.NumberColumn(
                    "Key Success Target (%)",
                    format="%.2f %%"
                ),
                "Monthly Progress": st.column_config.NumberColumn(
                    "Monthly Progress (%)",
                    format="%.2f %%"
                ),
                "Monthly Status": "Monthly Status",
                "Monthly Achievement": "Monthly Achievement",
                "Up To Month Progress": st.column_config.NumberColumn(
                    "Up To Month Progress (%)",
                    format="%.2f %%"
                ),
                "Up To Month Status": "Up To Month Status",
                "Up To Month Achievement": "Up To Month Achievement"
            },
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("No projects for this filter.")

    # Visualisasi Fleksibel & Kurva S berdampingan
    st.markdown("---")
    col_vis, col_kurva = st.columns(2)

    with col_vis:
        st.subheader("Flexible Visualization")
        # Additional filters
        view_mode = st.selectbox("Visualization Level", ["Per Item", "Per Group", "Per Department"], key="resume_view_mode")
        group_type_opt = st.selectbox("Group Type Filter", ["ALL", "ACTIVITY", "SUCCESS"], key="resume_group_type")

        # Filter proyek berdasarkan departemen yang sudah diterapkan sebelumnya
        proj_name_to_row = {p['name']: p for p in projects}
        proj_names_filtered = [p['name'] for p in projects]
    selected_proj_names = st.multiselect("Select Projects", options=proj_names_filtered, default=proj_names_filtered, key="resume_projects_sel")

        # Filter PIC opsional
    users_pic = fetchall("SELECT id, name FROM users WHERE approved=1")
    user_map_pic = {u['name']: u['id'] for u in users_pic}
    pic_filter_opt = st.selectbox("PIC Filter (optional)", ["ALL"] + list(user_map_pic.keys()), index=0, key="resume_pic_filter")

        # Apply selected project and PIC filter
    selected_projects = [proj_name_to_row[n] for n in selected_proj_names if n in proj_name_to_row]
    if pic_filter_opt != "ALL":
        selected_projects = [p for p in selected_projects if p.get('pic_user_id') == user_map_pic.get(pic_filter_opt)]

        # Period (use selected month: mf)
        start_period = mf

    with col_kurva:
        # S-curve for filtered projects/items
        st.subheader("S Curve (Key Activities vs Key Success)")
        if not selected_projects:
            st.info("No projects for this filter.")
        else:
            # Gather items by selected projects and group type filter
            items_pool = []  # list of dicts: {id, name, project, group_type}
            for p in selected_projects:
                q = "SELECT i.id, i.name, g.group_type FROM items i JOIN groups g ON i.group_id=g.id WHERE g.project_id=?"
                its = fetchall(q, (p['id'],))
                if group_type_opt != "ALL":
                    its = [it for it in its if it['group_type'] == group_type_opt]
                for it in its:
                    items_pool.append({
                        'id': it['id'], 'name': it['name'], 'project': p['name'], 'group_type': it['group_type']
                    })

            # Multiselect item filter for S Curve (optional)
            item_label_map = {f"[{d['project']}] ({d['id']}) {d['name']} - {d['group_type']}": d for d in items_pool}
            item_labels = list(item_label_map.keys())
            sel_item_labels = st.multiselect("Item Filter for S Curve (optional)", options=item_labels, default=[], key="s_curve_items_sel")
            sel_item_ids = set([item_label_map[l]['id'] for l in sel_item_labels])

            # Determine items to use for calculation
            if sel_item_ids:
                scoped_items = [d for d in items_pool if d['id'] in sel_item_ids]
            else:
                scoped_items = items_pool

            if not scoped_items:
                st.info("No items matching filter for S Curve.")
            else:
                # Determine combined month range from selected projects
                min_start = min([datetime.strptime(p['start_date'], "%Y-%m-%d").date() for p in selected_projects])
                max_finish = max([datetime.strptime(p['finish_date'], "%Y-%m-%d").date() for p in selected_projects])

                cur_month = date(min_start.year, min_start.month, 1)
                series_rows = []
                while cur_month <= max_finish:
                    sp = cur_month
                    ep = (sp + relativedelta(months=1)) - timedelta(days=1)
                    act_scores = []
                    suc_scores = []
                    for it in scoped_items:
                        sc = compute_item_score(it['id'], sp, ep)
                        if sc is None:
                            continue
                        if it['group_type'] == 'ACTIVITY':
                            act_scores.append(sc)
                        elif it['group_type'] == 'SUCCESS':
                            suc_scores.append(sc)
                    # Average per type if data exists
                    if act_scores:
                        series_rows.append({"month": sp, "type": "Key Activities", "score": sum(act_scores)/len(act_scores)})
                    if suc_scores:
                        series_rows.append({"month": sp, "type": "Key Success", "score": sum(suc_scores)/len(suc_scores)})
                    cur_month = cur_month + relativedelta(months=1)

                dfsc_combined = pd.DataFrame(series_rows)

                if not dfsc_combined.empty:
                    chart = alt.Chart(dfsc_combined).mark_line(point=True).encode(
                        x=alt.X('month:T', title='Month'),
                        y=alt.Y('score:Q', title="Average Score"),
                        color=alt.Color('type:N', title="Group Type")
                    ).properties(width=400, height=350)
                    st.altair_chart(chart)
    end_period = (mf + relativedelta(months=1)) - timedelta(days=1)

    # Helper lokal: Target & Realisasi per item untuk periode
    def _item_target_real(item_id, sp, ep):
        trows = fetchall(
            "SELECT SUM(target_value) AS t FROM periodic_targets WHERE item_id=? AND date(period_start_date)<=date(?) AND date(period_end_date)>=date(?)",
            (item_id, ep.isoformat(), sp.isoformat())
        )
        tsum = trows[0]['t'] if trows and trows[0]['t'] is not None else 0.0
        rrows = fetchall(
            "SELECT realized_date, realized_value, recorded_at FROM realizations WHERE item_id=? AND date(realized_date)>=date(?) AND date(realized_date)<=date(?) ORDER BY date(realized_date) ASC, recorded_at ASC",
            (item_id, sp.isoformat(), ep.isoformat())
        )
        rec_map = {}
        for r in rrows:
            rd = r['realized_date']
            ra = r.get('recorded_at') or ""
            if rd not in rec_map or rec_map[rd]['recorded_at'] < ra:
                rec_map[rd] = {'realized_value': r['realized_value'], 'recorded_at': ra}
        rsum = sum([v['realized_value'] for v in rec_map.values()]) if rec_map else 0.0
        return float(tsum or 0.0), float(rsum or 0.0)

    # Render sesuai level
    if not selected_projects:
        st.info("No projects matching filter.")
    else:
        if view_mode == "Per Item":
            rows_item = []
            for p in selected_projects:
                # Filter groups by type
                groups = fetchall("SELECT * FROM groups WHERE project_id=?", (p['id'],))
                if group_type_opt != "ALL":
                    groups = [g for g in groups if g['group_type'] == group_type_opt]
                for g in groups:
                    items = fetchall("SELECT * FROM items WHERE group_id=?", (g['id'],))
                    for it in items:
                        # Bulan ini
                        t_bln, r_bln = _item_target_real(it['id'], start_period, end_period)
                        s_bln = compute_item_score(it['id'], start_period, end_period)
                        # Up to month
                        it_start = datetime.strptime(it['start_date'], "%Y-%m-%d").date() if it['start_date'] else start_period
                        t_up, r_up = _item_target_real(it['id'], it_start, end_period)
                        s_up = compute_item_score(it['id'], it_start, end_period)
                        rows_item.append({
                            "Dept": p['department'],
                            "Project": p['name'],
                            "Group": g['group_type'],
                            "Item": it['name'],
                            "UoM": it['uom'],
                            "Target Bulan": round(t_bln, 2),
                            "Realisasi Bulan": round(r_bln, 2),
                            "Skor Bulan (%)": round((s_bln or 0.0)*100, 2) if s_bln is not None else 0.0,
                            "Target Up To": round(t_up, 2),
                            "Realisasi Up To": round(r_up, 2),
                            "Skor Up To (%)": round((s_up or 0.0)*100, 2) if s_up is not None else 0.0,
                        })
            df_item = pd.DataFrame(rows_item)
            if df_item.empty:
                st.info("No items for this filter.")
            else:
                st.dataframe(df_item, use_container_width=True, hide_index=True)

        elif view_mode == "Per Group":
            rows_group = []
            for p in selected_projects:
                groups = fetchall("SELECT * FROM groups WHERE project_id=?", (p['id'],))
                if group_type_opt != "ALL":
                    groups = [g for g in groups if g['group_type'] == group_type_opt]
                for g in groups:
                    items = fetchall("SELECT * FROM items WHERE group_id=?", (g['id'],))
                    # Aggregasi target & realisasi dari item
                    t_bln_sum = 0.0
                    r_bln_sum = 0.0
                    t_up_sum = 0.0
                    r_up_sum = 0.0
                    for it in items:
                        t_b, r_b = _item_target_real(it['id'], start_period, end_period)
                        t_bln_sum += t_b
                        r_bln_sum += r_b
                        it_start = datetime.strptime(it['start_date'], "%Y-%m-%d").date() if it['start_date'] else start_period
                        t_u, r_u = _item_target_real(it['id'], it_start, end_period)
                        t_up_sum += t_u
                        r_up_sum += r_u
                    s_bln, _gw = compute_group_score(g['id'], start_period, end_period)
                    p_start = datetime.strptime(p['start_date'], "%Y-%m-%d").date()
                    s_up, _gw2 = compute_group_score(g['id'], p_start, end_period)
                    rows_group.append({
                        "Dept": p['department'],
                        "Project": p['name'],
                        "Group": g['group_type'],
                        "Weight (%)": round((g.get('group_weight') or 0.0), 2),
                        "Target Bulan": round(t_bln_sum, 2),
                        "Realisasi Bulan": round(r_bln_sum, 2),
                        "Skor Bulan (%)": round((s_bln or 0.0)*100, 2) if s_bln is not None else 0.0,
                        "Target Up To": round(t_up_sum, 2),
                        "Realisasi Up To": round(r_up_sum, 2),
                        "Skor Up To (%)": round((s_up or 0.0)*100, 2) if s_up is not None else 0.0,
                    })
            df_group = pd.DataFrame(rows_group)
            if df_group.empty:
                st.info("No groups for this filter.")
            else:
                st.dataframe(df_group, use_container_width=True, hide_index=True)

        else:  # Per Departemen
            # Kumpulkan dept unik dari proyek terpilih
            depts = sorted(list({p['department'] for p in selected_projects}))
            rows_dept = []
            for dname in depts:
                p_in_dept = [p for p in selected_projects if p['department'] == dname]
                # Aggregasi target & realisasi dari item (sesuai group filter)
                t_bln_sum = 0.0
                r_bln_sum = 0.0
                t_up_sum = 0.0
                r_up_sum = 0.0
                s_list_month = []
                s_list_up = []
                for p in p_in_dept:
                    groups = fetchall("SELECT * FROM groups WHERE project_id=?", (p['id'],))
                    if group_type_opt != "ALL":
                        groups = [g for g in groups if g['group_type'] == group_type_opt]
                    for g in groups:
                        items = fetchall("SELECT * FROM items WHERE group_id=?", (g['id'],))
                        for it in items:
                            t_b, r_b = _item_target_real(it['id'], start_period, end_period)
                            t_bln_sum += t_b
                            r_bln_sum += r_b
                            it_start = datetime.strptime(it['start_date'], "%Y-%m-%d").date() if it['start_date'] else start_period
                            t_u, r_u = _item_target_real(it['id'], it_start, end_period)
                            t_up_sum += t_u
                            r_up_sum += r_u
                    # skor: jika ALL, pakai skor project; jika spesifik, pakai skor group type
                    if group_type_opt == "ALL":
                        s_m = compute_project_score(p['id'], start_period, end_period)
                        s_u = compute_project_score(p['id'], datetime.strptime(p['start_date'], "%Y-%m-%d").date(), end_period)
                    else:
                        s_m = compute_group_type_score(p['id'], group_type_opt, start_period, end_period)
                        s_u = compute_group_type_score(p['id'], group_type_opt, datetime.strptime(p['start_date'], "%Y-%m-%d").date(), end_period)
                    if s_m is not None:
                        s_list_month.append(s_m)
                    if s_u is not None:
                        s_list_up.append(s_u)
                # Rata-rata skor departemen
                dept_s_m = sum(s_list_month)/len(s_list_month) if s_list_month else 0.0
                dept_s_u = sum(s_list_up)/len(s_list_up) if s_list_up else 0.0
                rows_dept.append({
                    "Departemen": dname,
                    "Target Bulan": round(t_bln_sum, 2),
                    "Realisasi Bulan": round(r_bln_sum, 2),
                    "Skor Bulan (%)": round(dept_s_m*100, 2),
                    "Target Up To": round(t_up_sum, 2),
                    "Realisasi Up To": round(r_up_sum, 2),
                    "Skor Up To (%)": round(dept_s_u*100, 2),
                })
            df_dept = pd.DataFrame(rows_dept)
            if df_dept.empty:
                st.info("No department data for this filter.")
            else:
                st.dataframe(df_dept, use_container_width=True, hide_index=True)


    # Export
    if not df.empty:
        buf = io.BytesIO()
        df.to_csv(buf, index=False)
        st.download_button("Export CSV", data=buf.getvalue(), file_name="resume.csv", mime="text/csv")
        

def page_admin_panel():
    require_admin()
    st.header("🔐 Admin Panel")
    st.markdown("---")
    
    tab_users, tab_deps = st.tabs(["Kelola User", "Kelola Departemen"])
    
    with tab_users:
        st.subheader("Daftar Pengguna")
        users_df = pd.DataFrame(fetchall("SELECT id, name, email, role, department, approved FROM users"))
        
        if not users_df.empty:
            st.dataframe(users_df.set_index('id'))
            
            st.markdown("---")
            st.subheader("Set Approval & Role")
            with st.form("user_management_form"):
                user_ids = users_df['id'].tolist()
                user_to_edit = st.selectbox("Pilih ID User", options=user_ids, key="admin_user_id")
                
                if user_to_edit:
                    current_user_data = users_df[users_df['id'] == user_to_edit].iloc[0]
                    new_role = st.selectbox("Role", options=["user", "admin"], index=["user", "admin"].index(current_user_data['role']), key="admin_user_role")
                    new_approved = st.checkbox("Approved", value=current_user_data['approved'], key="admin_user_approved")
                    
                    if st.form_submit_button("Update User"):
                        execute("UPDATE users SET role=?, approved=? WHERE id=?", (new_role, 1 if new_approved else 0, user_to_edit))
                        st.success(f"User ID {user_to_edit} berhasil diupdate.")
                        st.rerun()
                        
    with tab_deps:
        st.subheader("Daftar Departemen")
        deps_df = pd.DataFrame(fetchall("SELECT * FROM departments"))
        st.dataframe(deps_df, use_container_width=True, hide_index=True)
        
        st.markdown("---")
        st.subheader("Tambah Departemen Baru")
        with st.form("add_department_form"):
            new_dept_name = st.text_input("Nama Departemen", key="new_dept_name")
            if st.form_submit_button("Tambah"):
                try:
                    execute("INSERT INTO departments (name) VALUES (?)", (new_dept_name,))
                    st.success(f"Departemen '{new_dept_name}' berhasil ditambahkan. Form direset.")
                    st.rerun()
                except sqlite3.IntegrityError:
                    st.error("Departemen dengan nama ini sudah ada.")

def page_user_guide():
    st.header("📖 Panduan Pengguna")
    st.markdown("---")
    st.markdown("""
## Selamat datang di **Project Charter Application**!

Aplikasi ini membantu Anda membuat, mengelola, dan memantau kemajuan Project Charter dengan metode Key Activities & Key Success.

---
### **1. Login & Registrasi**
- Klik **🔐 Login / Register** di sidebar.
- Untuk pengguna baru, isi form Register dan tunggu persetujuan admin.
- Untuk login, masukkan email & password yang sudah terdaftar.

---
### **2. Dashboard**
- Setelah login, Anda akan masuk ke **Dashboard**.
- Lihat statistik proyek, user aktif, status distribusi proyek, dan tenggat waktu terdekat.
- Admin dapat melihat jumlah user yang menunggu persetujuan.

---
### **3. Project Charter**
- Pilih menu **📝 Project Charter**.
- Tab **List Projects**: Lihat daftar proyek, klik untuk detail, edit, atau hapus.
- Tab **Create Project**: Buat proyek baru, isi nama, departemen, PIC, tanggal mulai & selesai.

#### **Groups & Items**
- Tambahkan Group (Key Activity/SUCCESS) dan Item di dalamnya.
- Set bobot group & item (total group = 100, total item per group = 100).
- Untuk Key Activity, Anda bisa membuat Project Turunan dari item.

#### **Set Target Periodik**
- Pilih item, tentukan target nilai per periode (mingguan/bulanan/dll).
- Jika item punya Project Turunan, target/realisasi dinonaktifkan.

#### **Input Realisasi**
- Pilih item, pilih bulan & minggu (mengikuti Thursday Rule), lalu input nilai realisasi.
- Lihat histori realisasi dan tabel data.

---
### **4. Resume & Kurva S**
- Pilih menu **📄 Resume & Kurva S**.
- Filter proyek berdasarkan departemen, bulan, PIC, dan group type.
- Lihat tabel progres bulanan, up-to-month, status capaian, dan target key success.
- Visualisasi: S-Curve per item, group, atau departemen.
- Download data resume ke CSV.

---
### **5. Admin Panel (Khusus Admin)**
- Pilih menu **⚙️ Admin Panel**.
- Tab **Kelola User**: Lihat, setujui, dan ubah role user.
- Tab **Kelola Departemen**: Tambah atau edit daftar departemen.

---
### **6. User Setting**
- Pilih menu **⚙️ User Setting**.
- Ganti password: Masukkan password lama & baru.
- Ganti email: Masukkan email baru & password.
- Ganti departemen: Pilih departemen baru & masukkan password.

---
### **Tips & Catatan**
- Gunakan browser modern (Chrome/Edge/Firefox) untuk pengalaman terbaik.
- Jika ada error, refresh halaman atau login ulang.
- Hubungi admin jika butuh bantuan lebih lanjut.

---
**Selamat menggunakan aplikasi!**
""")

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

    tabs = st.tabs(["List", "Upload file", "Download", "Delete"])

    # List Tab
    with tabs[0]:
        st.subheader("Daftar File")
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
    
def main():
    init_db()

    if "page" not in st.session_state:
        st.session_state.page = "Authentication"
    if "user" not in st.session_state:
        st.session_state.user = None


    user = current_user()

    # Logo di sidebar atas
    st.sidebar.image("logo.png", use_container_width=True)
    st.sidebar.title("Navigasi")

    if not user:
        if st.sidebar.button("🔐 Login / Register", use_container_width=True):
            st.session_state.page = "Authentication"

    else:
        # Info user
        st.sidebar.markdown(f"**👤 {user['name']}**")
        st.sidebar.markdown(f"✉️ {user['email']}")
        st.sidebar.markdown(f"🏢 {user.get('department','-')}")
        st.sidebar.markdown(f"**Role:** {user['role'].capitalize()}")
        st.sidebar.markdown("---")

        if st.sidebar.button("📊 Dashboard", use_container_width=True, type="secondary"):
            st.session_state.page = "Dashboard"
        if st.sidebar.button("📄 Resume & Kurva S", use_container_width=True, type="secondary"):
            st.session_state.page = "Resume"
        if st.sidebar.button("📝 Project Charter", use_container_width=True, type="primary"):
            st.session_state.page = "Project Charter"
        if st.sidebar.button("📂 G Drive", use_container_width=True, type="secondary"):
            st.session_state.page = "G Drive"
        if user.get('role') == 'admin':
            if st.sidebar.button("⚙️ Admin Panel", use_container_width=True, type="secondary"):
                st.session_state.page = "Admin Panel"

        if st.sidebar.button("🕓 Audit Trail", use_container_width=True, type="secondary"):
            st.session_state.page = "Audit Trail"
        if st.sidebar.button("🌿 Turunan Project", use_container_width=True, type="secondary"):
            st.session_state.page = "Child Projects"
        if st.sidebar.button("⚙️ User Setting", use_container_width=True, type="secondary"):
            st.session_state.page = "User Setting"
        if st.sidebar.button("📖 Panduan", use_container_width=True, type="secondary"):
            st.session_state.page = "Panduan Pengguna"

        # Logout button di bawah Panduan
        st.sidebar.button("🚪 Logout", on_click=logout_user, use_container_width=True)
        st.sidebar.markdown("---")
    
    if not user:
        page_auth()
        return

    if st.session_state.page == "Authentication":
        st.session_state.page = "Dashboard"
        st.rerun()

    if st.session_state.page == "Dashboard":
        page_dashboard()
    elif st.session_state.page == "Resume":
        page_resume()
    elif st.session_state.page == "Project Charter":
        page_project_charter()
    elif st.session_state.page == "Admin Panel":
        page_admin_panel()
    elif st.session_state.page == "User Setting":
        page_user_setting()
    elif st.session_state.page == "Panduan Pengguna":
        page_user_guide()
    elif st.session_state.page == "Audit Trail":
        page_audit_trail()
    elif st.session_state.page == "Child Projects":
        page_child_projects()
    elif st.session_state.page == "G Drive":
        page_gdrive()
def page_user_setting():
    user = current_user()
    st.markdown("---")
    st.subheader(f"Ganti Departemen untuk {user['name']}")
    # Get department list
    departments = [d['name'] for d in fetchall("SELECT * FROM departments")]
    current_dept = user.get('department') or ""
    with st.form("change_dept_form"):
        new_dept = st.selectbox("Pilih Departemen Baru", options=departments, index=departments.index(current_dept) if current_dept in departments else 0)
        pw_for_dept = st.text_input("Password", type="password", key="pw_for_dept")
        submit_dept = st.form_submit_button("Ganti Departemen", type="primary")
        if submit_dept:
            if not pw_for_dept:
                st.error("Password harus diisi.")
            elif not verify_password(pw_for_dept, user['password_hash']):
                st.error("Password salah.")
            elif new_dept == current_dept:
                st.info("Departemen baru sama dengan departemen lama.")
            else:
                execute("UPDATE users SET department=? WHERE id=?", (new_dept, user['id']))
                st.success("Departemen berhasil diganti.")
    require_login()
    user = current_user()
    st.header("⚙️ User Setting")
    st.markdown("---")
    st.subheader(f"Ganti Password untuk {user['name']}")
    with st.form("change_pw_form"):
        old_pw = st.text_input("Password Lama", type="password")
        new_pw1 = st.text_input("Password Baru", type="password")
        new_pw2 = st.text_input("Konfirmasi Password Baru", type="password")
        submit = st.form_submit_button("Ganti Password", type="primary")
        if submit:
            if not old_pw or not new_pw1 or not new_pw2:
                st.error("Semua kolom harus diisi.")
            elif not verify_password(old_pw, user['password_hash']):
                st.error("Password lama salah.")
            elif new_pw1 != new_pw2:
                st.error("Password baru dan konfirmasi tidak cocok.")
            elif len(new_pw1) < 6:
                st.error("Password baru minimal 6 karakter.")
            else:
                execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(new_pw1), user['id']))
                st.success("Password berhasil diganti.")

    st.markdown("---")
    st.subheader(f"Ganti Email untuk {user['name']}")
    with st.form("change_email_form"):
        new_email = st.text_input("Email Baru", value=user['email'])
        pw_for_email = st.text_input("Password", type="password")
        submit_email = st.form_submit_button("Ganti Email", type="primary")
        if submit_email:
            if not new_email or not pw_for_email:
                st.error("Semua kolom harus diisi.")
            elif not verify_password(pw_for_email, user['password_hash']):
                st.error("Password salah.")
            elif new_email == user['email']:
                st.info("Email baru sama dengan email lama.")
            elif '@' not in new_email or '.' not in new_email:
                st.error("Format email tidak valid.")
            else:
                # Check if email already exists
                existing = fetchone("SELECT id FROM users WHERE email=?", (new_email,))
                if existing and existing['id'] != user['id']:
                    st.error("Email sudah digunakan user lain.")
                else:
                    execute("UPDATE users SET email=? WHERE id=?", (new_email, user['id']))
                    st.success("Email berhasil diganti.")

if __name__ == '__main__':
    main()