import streamlit as st
import pandas as pd
import sqlite3
import os
from datetime import datetime, date
import altair as alt
import pytz
import json

# Timezone GMT+7 (WIB)
WIB = pytz.timezone('Asia/Jakarta')

# Helper functions untuk format tanggal
def parse_date(date_str):
    """Parse tanggal dari berbagai format ke datetime object"""
    if pd.isna(date_str) or date_str == '' or date_str is None:
        return None
    
    date_str = str(date_str).strip()
    
    # Try dd-mm-yyyy first (format baru kita)
    for fmt in ['%d-%m-%Y', '%Y-%m-%d', '%d/%m/%Y', '%Y/%m/%d']:
        try:
            return datetime.strptime(date_str, fmt)
        except:
            continue
    
    # Try pandas parsing as fallback
    try:
        return pd.to_datetime(date_str)
    except:
        return None

def format_date(date_obj):
    """Format datetime object ke string dd-mm-yyyy"""
    if pd.isna(date_obj) or date_obj is None:
        return ''
    if hasattr(date_obj, 'strftime'):
        return date_obj.strftime('%d-%m-%Y')
    return str(date_obj)

DB_NAME = "car_wash.db"

# Paket Cucian (akan diload dari database)
PAKET_CUCIAN = {
    "Cuci Reguler": 50000,
    "Cuci Premium": 75000,
    "Cuci + Wax": 100000,
    "Full Detailing": 200000,
    "Interior Only": 60000,
    "Exterior Only": 40000
}

# Default checklist items (akan diload dari database)
DEFAULT_CHECKLIST_DATANG = [
    "Ban lengkap dan baik",
    "Wiper berfungsi", 
    "Kaca tidak retak",
    "Body tidak penyok",
    "Lampu lengkap",
    "Spion lengkap"
]

DEFAULT_CHECKLIST_SELESAI = [
    "Interior bersih",
    "Exterior bersih",
    "Kaca bersih",
    "Ban hitam mengkilap",
    "Dashboard bersih",
    "Tidak ada noda"
]

# --- Database Setup ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # Tabel customers - database pelanggan
    c.execute('''
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nopol TEXT UNIQUE NOT NULL,
            nama_customer TEXT NOT NULL,
            no_telp TEXT,
            alamat TEXT,
            created_at TEXT NOT NULL
        )
    ''')
    
    # Tabel wash_transactions - transaksi cuci mobil
    c.execute('''
        CREATE TABLE IF NOT EXISTS wash_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nopol TEXT NOT NULL,
            nama_customer TEXT NOT NULL,
            tanggal TEXT NOT NULL,
            waktu_masuk TEXT NOT NULL,
            waktu_selesai TEXT,
            paket_cuci TEXT NOT NULL,
            harga INTEGER NOT NULL,
            checklist_datang TEXT,
            checklist_selesai TEXT,
            qc_barang TEXT,
            catatan TEXT,
            status TEXT DEFAULT 'Dalam Proses',
            created_by TEXT,
            FOREIGN KEY (nopol) REFERENCES customers(nopol)
        )
    ''')
    
    # Tabel audit trail
    c.execute('''
        CREATE TABLE IF NOT EXISTS audit_trail (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            user TEXT NOT NULL,
            action TEXT NOT NULL,
            detail TEXT
        )
    ''')
    
    # Tabel settings - untuk konfigurasi toko
    c.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            setting_key TEXT UNIQUE NOT NULL,
            setting_value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    ''')
    
    # Insert default settings jika belum ada
    c.execute("SELECT COUNT(*) FROM settings")
    if c.fetchone()[0] == 0:
        now = datetime.now(WIB).strftime("%d-%m-%Y %H:%M:%S")
        
        # Default paket cucian
        c.execute("INSERT INTO settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)",
                 ("paket_cucian", json.dumps(PAKET_CUCIAN), now))
        
        # Default checklist
        c.execute("INSERT INTO settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)",
                 ("checklist_datang", json.dumps(DEFAULT_CHECKLIST_DATANG), now))
        c.execute("INSERT INTO settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)",
                 ("checklist_selesai", json.dumps(DEFAULT_CHECKLIST_SELESAI), now))
        
        # Info toko
        toko_info = {
            "nama": "CUCI MOBIL BERSIH",
            "alamat": "Jl. Contoh No. 123",
            "telp": "08123456789",
            "email": "info@cucimobil.com"
        }
        c.execute("INSERT INTO settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)",
                 ("toko_info", json.dumps(toko_info), now))
    
    conn.commit()
    conn.close()


# --- Simpan & Load Customer ---
def save_customer(nopol, nama, telp, alamat):
    """Simpan data customer baru"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    now_wib = datetime.now(WIB)
    try:
        c.execute("""
            INSERT INTO customers (nopol, nama_customer, no_telp, alamat, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (nopol.upper(), nama, telp, alamat, now_wib.strftime("%d-%m-%Y %H:%M:%S")))
        conn.commit()
        return True, "Customer berhasil ditambahkan"
    except sqlite3.IntegrityError:
        return False, "Nopol sudah terdaftar"
    except Exception as e:
        return False, f"Error: {str(e)}"
    finally:
        conn.close()

def get_customer_by_nopol(nopol):
    """Ambil data customer berdasarkan nopol"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT * FROM customers WHERE nopol = ?", (nopol.upper(),))
    result = c.fetchone()
    conn.close()
    if result:
        return {
            'id': result[0],
            'nopol': result[1],
            'nama_customer': result[2],
            'no_telp': result[3],
            'alamat': result[4],
            'created_at': result[5]
        }
    return None

def get_all_customers():
    """Ambil semua data customer"""
    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql("SELECT * FROM customers ORDER BY created_at DESC", conn)
    conn.close()
    return df

# --- Simpan & Load Transaksi ---
def save_transaction(data):
    """Simpan transaksi cuci mobil"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    try:
        c.execute("""
            INSERT INTO wash_transactions 
            (nopol, nama_customer, tanggal, waktu_masuk, waktu_selesai, paket_cuci, harga, 
             checklist_datang, checklist_selesai, qc_barang, catatan, status, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data['nopol'].upper(),
            data['nama_customer'],
            data['tanggal'],
            data['waktu_masuk'],
            data.get('waktu_selesai', ''),
            data['paket_cuci'],
            data['harga'],
            data.get('checklist_datang', ''),
            data.get('checklist_selesai', ''),
            data.get('qc_barang', ''),
            data.get('catatan', ''),
            data.get('status', 'Dalam Proses'),
            data.get('created_by', '')
        ))
        conn.commit()
        return True, "Transaksi berhasil disimpan"
    except Exception as e:
        return False, f"Error: {str(e)}"
    finally:
        conn.close()

def update_transaction_finish(trans_id, waktu_selesai, checklist_selesai, qc_barang, catatan):
    """Update transaksi saat selesai cuci"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    try:
        # Pastikan trans_id adalah integer
        trans_id = int(trans_id)
        
        # Cek status dulu dengan logging
        c.execute("SELECT id, status, nopol FROM wash_transactions WHERE id = ?", (trans_id,))
        result = c.fetchone()
        
        if not result:
            # Debug: cek semua ID yang ada
            c.execute("SELECT id, nopol, status FROM wash_transactions")
            all_trans = c.fetchall()
            print(f"DEBUG: Mencari ID {trans_id} (tipe: {type(trans_id)})")
            print(f"DEBUG: IDs yang ada di database: {[row[0] for row in all_trans]}")
            return False, f"Transaksi ID {trans_id} tidak ditemukan di database"
        
        current_status = result[1].strip()
        print(f"DEBUG: Transaksi ditemukan - ID: {result[0]}, Status: '{current_status}', Nopol: {result[2]}")
        
        if current_status != 'Dalam Proses':
            return False, f"Transaksi berstatus '{current_status}', tidak bisa diselesaikan"
        
        # Update status menjadi 'Selesai'
        c.execute("""
            UPDATE wash_transactions 
            SET waktu_selesai = ?, checklist_selesai = ?, qc_barang = ?, 
                catatan = ?, status = 'Selesai'
            WHERE id = ?
        """, (waktu_selesai, checklist_selesai, qc_barang, catatan, trans_id))
        
        conn.commit()
        print(f"DEBUG: Update berhasil untuk ID {trans_id}")
        return True, "Transaksi berhasil diselesaikan"
        
    except Exception as e:
        print(f"DEBUG ERROR: {str(e)}")
        return False, f"Error: {str(e)}"
    finally:
        conn.close()

def get_all_transactions():
    """Ambil semua transaksi"""
    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql("SELECT * FROM wash_transactions ORDER BY tanggal DESC, waktu_masuk DESC", conn)
    conn.close()
    return df

def get_transactions_by_date_range(start_date, end_date):
    """Ambil transaksi dalam rentang tanggal"""
    conn = sqlite3.connect(DB_NAME)
    query = """
        SELECT * FROM wash_transactions 
        WHERE tanggal BETWEEN ? AND ?
        ORDER BY tanggal DESC, waktu_masuk DESC
    """
    df = pd.read_sql(query, conn, params=(start_date, end_date))
    conn.close()
    return df

# --- Settings Functions ---
def get_setting(key):
    """Ambil setting berdasarkan key"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT setting_value FROM settings WHERE setting_key = ?", (key,))
    result = c.fetchone()
    conn.close()
    if result:
        try:
            return json.loads(result[0])
        except:
            return result[0]
    return None

def update_setting(key, value):
    """Update setting"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    now = datetime.now(WIB).strftime("%d-%m-%Y %H:%M:%S")
    try:
        value_str = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
        c.execute("""
            INSERT OR REPLACE INTO settings (setting_key, setting_value, updated_at)
            VALUES (?, ?, ?)
        """, (key, value_str, now))
        conn.commit()
        return True, "Setting berhasil diupdate"
    except Exception as e:
        return False, f"Error: {str(e)}"
    finally:
        conn.close()

def get_paket_cucian():
    """Ambil daftar paket cucian dari database"""
    paket = get_setting("paket_cucian")
    return paket if paket else PAKET_CUCIAN

def get_checklist_datang():
    """Ambil checklist datang dari database"""
    checklist = get_setting("checklist_datang")
    return checklist if checklist else DEFAULT_CHECKLIST_DATANG

def get_checklist_selesai():
    """Ambil checklist selesai dari database"""
    checklist = get_setting("checklist_selesai")
    return checklist if checklist else DEFAULT_CHECKLIST_SELESAI



USERS = {
    "admin": {"password": "admin123", "role": "Admin"},
    "kasir": {"password": "kasir123", "role": "Kasir"},
    "supervisor": {"password": "super123", "role": "Supervisor"},
}

# --- Audit Trail Helper ---
def add_audit(action, detail=None):
    """Simpan audit trail ke database SQLite agar persisten dan bisa dilihat semua user"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    # Gunakan timezone WIB (GMT+7)
    now_wib = datetime.now(WIB)
    c.execute("""
        INSERT INTO audit_trail (timestamp, user, action, detail)
        VALUES (?, ?, ?, ?)
    """, (
        now_wib.strftime("%d-%m-%Y %H:%M:%S"),
        st.session_state.get("login_user", "-"),
        action,
        detail or ""
    ))
    conn.commit()
    conn.close()

def load_audit_trail(user=None):
    """Load audit trail dari database. Jika user specified, filter by user."""
    conn = sqlite3.connect(DB_NAME)
    if user:
        query = "SELECT * FROM audit_trail WHERE user = ? ORDER BY timestamp DESC"
        df = pd.read_sql(query, conn, params=(user,))
    else:
        query = "SELECT * FROM audit_trail ORDER BY timestamp DESC"
        df = pd.read_sql(query, conn)
    conn.close()
    return df

def login_page():
    st.set_page_config(page_title="Login Cuci Mobil", layout="centered")
    
    st.markdown("""
    <style>
    .login-container {
        max-width: 400px;
        margin: 0 auto;
        padding: 2rem;
        background: white;
        border-radius: 15px;
        box-shadow: 0 4px 20px rgba(0,0,0,0.1);
    }
    </style>
    """, unsafe_allow_html=True)
    
    st.title("🚗 Sistem Manajemen Cuci Mobil")
    st.markdown("---")
    
    username = st.text_input("👤 Username", key="login_username")
    password = st.text_input("🔒 Password", type="password", key="login_password")
    
    col1, col2, col3 = st.columns([1,2,1])
    with col2:
        login_btn = st.button("🔐 Login", key="login_btn", use_container_width=True)
    
    if login_btn:
        uname = username.strip().lower()
        if uname in USERS and password == USERS[uname]["password"]:
            st.session_state["is_logged_in"] = True
            st.session_state["login_user"] = uname
            st.session_state["login_role"] = USERS[uname]["role"]
            add_audit("login", f"Login sebagai {USERS[uname]['role']}")
            st.success(f"✅ Login berhasil sebagai {USERS[uname]['role']}")
            st.rerun()
        else:
            st.error("❌ Username atau password salah.")
    
    st.markdown("---")
    st.info("💡 **Demo Account:**\n- admin / admin123\n- kasir / kasir123\n- supervisor / super123")


def dashboard_page(role):
    st.markdown("""
    <style>
    .dashboard-header {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 2rem;
        border-radius: 15px;
        color: white;
        margin-bottom: 2rem;
        box-shadow: 0 4px 20px rgba(102, 126, 234, 0.4);
    }
    .dashboard-header h1 {
        margin: 0;
        font-size: 2.2rem;
        font-weight: 800;
    }
    .dashboard-header p {
        margin: 0.5rem 0 0 0;
        opacity: 0.9;
    }
    .card-container {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
        gap: 1.2rem;
        margin-bottom: 2rem;
    }
    .card {
        background: white;
        border-radius: 16px;
        padding: 1.5rem;
        box-shadow: 0 2px 10px rgba(0,0,0,0.08);
        border-left: 4px solid;
        transition: all 0.3s ease;
    }
    .card:hover {
        transform: translateY(-5px);
        box-shadow: 0 8px 25px rgba(0,0,0,0.15);
    }
    .card.card-1 { border-left-color: #667eea; }
    .card.card-2 { border-left-color: #f093fb; }
    .card.card-3 { border-left-color: #4facfe; }
    .card.card-4 { border-left-color: #43e97b; }
    .card.card-5 { border-left-color: #fa709a; }
    .card-icon {
        font-size: 2.5rem;
        margin-bottom: 0.5rem;
    }
    .card-title {
        color: #636e72;
        font-size: 0.85rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        margin-bottom: 0.5rem;
    }
    .card-value {
        font-size: 2.2rem;
        font-weight: 800;
        color: #2d3436;
        margin-bottom: 0.3rem;
    }
    .card-desc {
        color: #b2bec3;
        font-size: 0.8rem;
    }
    .chart-box {
        background: white;
        padding: 1.5rem;
        border-radius: 15px;
        box-shadow: 0 2px 10px rgba(0,0,0,0.08);
        margin-bottom: 1.5rem;
    }
    .chart-title {
        color: #2d3436;
        font-size: 1.2rem;
        font-weight: 700;
        margin-bottom: 1rem;
        padding-bottom: 0.8rem;
        border-bottom: 3px solid #f0f0f0;
    }
    .filter-bar {
        background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
        padding: 1.5rem;
        border-radius: 12px;
        margin-bottom: 2rem;
        box-shadow: 0 2px 8px rgba(0,0,0,0.05);
    }
    </style>
    """, unsafe_allow_html=True)

    # Header
    now = datetime.now(WIB)
    st.markdown(f'''
    <div class="dashboard-header">
        <h1>📊 Dashboard Cuci Mobil</h1>
        <p>📅 {now.strftime("%A, %d %B %Y")} • ⏰ {now.strftime("%H:%M:%S")} WIB</p>
    </div>
    ''', unsafe_allow_html=True)
    
    # Load data transaksi
    df_trans = get_all_transactions()
    df_cust = get_all_customers()
    
    # Filter tanggal - default hari ini
    col1, col2 = st.columns([2, 2])
    with col1:
        today = datetime.now(WIB).date()
        date_filter = st.date_input("� Filter Tanggal", value=(today, today))
    
    # Apply filter
    if isinstance(date_filter, (list, tuple)) and len(date_filter) == 2:
        start_date = date_filter[0].strftime('%d-%m-%Y')
        end_date = date_filter[1].strftime('%d-%m-%Y')
        df_filtered = get_transactions_by_date_range(start_date, end_date)
    else:
        df_filtered = df_trans
    
    # Hitung statistik
    total_transaksi = len(df_filtered)
    total_pendapatan = df_filtered['harga'].sum() if not df_filtered.empty else 0
    transaksi_selesai = len(df_filtered[df_filtered['status'] == 'Selesai'])
    transaksi_proses = len(df_filtered[df_filtered['status'] == 'Dalam Proses'])
    total_customer = len(df_cust)
    
    # Cards
    st.markdown(f'''
    <div class="card-container">
        <div class="card card-1">
            <div class="card-icon">�</div>
            <div class="card-title">Total Pendapatan</div>
            <div class="card-value">Rp {total_pendapatan:,.0f}</div>
            <div class="card-desc">Periode yang dipilih</div>
        </div>
        <div class="card card-2">
            <div class="card-icon">🚗</div>
            <div class="card-title">Total Transaksi</div>
            <div class="card-value">{total_transaksi}</div>
            <div class="card-desc">Transaksi dalam periode</div>
        </div>
        <div class="card card-3">
            <div class="card-icon">✅</div>
            <div class="card-title">Selesai</div>
            <div class="card-value">{transaksi_selesai}</div>
            <div class="card-desc">Sudah dikerjakan</div>
        </div>
        <div class="card card-4">
            <div class="card-icon">⏳</div>
            <div class="card-title">Dalam Proses</div>
            <div class="card-value">{transaksi_proses}</div>
            <div class="card-desc">Sedang dikerjakan</div>
        </div>
        <div class="card card-5">
            <div class="card-icon">👥</div>
            <div class="card-title">Total Customer</div>
            <div class="card-value">{total_customer}</div>
            <div class="card-desc">Customer terdaftar</div>
        </div>
    </div>
    ''', unsafe_allow_html=True)
    
    # Grafik
    if not df_filtered.empty:
        col1, col2 = st.columns(2)
        
        with col1:
            st.subheader("📊 Pendapatan per Paket")
            paket_income = df_filtered.groupby('paket_cuci')['harga'].sum().reset_index()
            paket_income.columns = ['Paket', 'Total']
            
            chart = alt.Chart(paket_income).mark_bar(cornerRadiusEnd=8).encode(
                x=alt.X('Total:Q', title='Total Pendapatan (Rp)'),
                y=alt.Y('Paket:N', sort='-x', title='Paket Cuci'),
                color=alt.Color('Total:Q', scale=alt.Scale(scheme='viridis'), legend=None),
                tooltip=['Paket', alt.Tooltip('Total:Q', format=',.0f', title='Rp')]
            ).properties(height=300)
            st.altair_chart(chart, use_container_width=True)
        
        with col2:
            st.subheader("📈 Status Transaksi")
            status_count = df_filtered['status'].value_counts().reset_index()
            status_count.columns = ['Status', 'Jumlah']
            
            pie = alt.Chart(status_count).mark_arc(innerRadius=60, outerRadius=120).encode(
                theta='Jumlah:Q',
                color=alt.Color('Status:N', 
                    scale=alt.Scale(domain=['Selesai', 'Dalam Proses'], range=['#43e97b', '#f5576c']),
                    legend=alt.Legend(orient='bottom')
                ),
                tooltip=['Status', 'Jumlah']
            ).properties(height=300)
            st.altair_chart(pie, use_container_width=True)
        
        # Tabel transaksi terbaru
        st.subheader("� Transaksi Terbaru")
        df_display = df_filtered[['tanggal', 'nopol', 'nama_customer', 'paket_cuci', 'harga', 'status']].head(10)
        st.dataframe(df_display, use_container_width=True)
    else:
        st.info("📭 Belum ada transaksi untuk periode ini")


def transaksi_page(role):
    st.markdown("""
    <style>
    .trans-header {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 1.5rem;
        border-radius: 15px;
        color: white;
        margin-bottom: 2rem;
        box-shadow: 0 4px 15px rgba(102, 126, 234, 0.3);
    }
    .trans-header h2 {
        margin: 0;
        font-size: 1.8rem;
        font-weight: 700;
    }
    .form-section {
        background: white;
        padding: 1.5rem;
        border-radius: 12px;
        box-shadow: 0 2px 10px rgba(0,0,0,0.08);
        margin-bottom: 1.5rem;
    }
    .section-title {
        color: #667eea;
        font-size: 1.1rem;
        font-weight: 600;
        margin-bottom: 1rem;
        padding-bottom: 0.5rem;
        border-bottom: 2px solid #f0f0f0;
    }
    .stTextInput > label, .stSelectbox > label {
        font-weight: 500;
        color: #2d3436;
    }
    </style>
    """, unsafe_allow_html=True)
    
    st.markdown('<div class="trans-header"><h2>🚗 Input Transaksi Cuci Mobil</h2></div>', unsafe_allow_html=True)
    
    # Hitung jumlah transaksi dalam proses untuk badge
    df_check = get_all_transactions()
    jumlah_proses = len(df_check[df_check['status'] == 'Dalam Proses'])
    jumlah_selesai = len(df_check[df_check['status'] == 'Selesai'])
    
    tab1, tab2, tab3 = st.tabs([
        "📝 Transaksi Baru", 
        f"✅ Selesaikan Transaksi ({jumlah_proses})",
        f"📚 History Customer ({jumlah_selesai})"
    ])
    
    with tab1:
        # Load paket dan checklist dari database
        paket_cucian = get_paket_cucian()
        checklist_items = get_checklist_datang()
        
        col1, col2 = st.columns([2, 1])
        
        with col1:
            st.markdown('<div class="form-section">', unsafe_allow_html=True)
            st.markdown('<p class="section-title">🚘 Data Kendaraan</p>', unsafe_allow_html=True)
            
            nopol_input = st.text_input("🔖 Nomor Polisi *", placeholder="Contoh: B1234XYZ", 
                                       key="trans_nopol", help="Masukkan nomor polisi kendaraan").upper()
            
            # Auto-fill dari database
            customer_data = None
            if nopol_input:
                customer_data = get_customer_by_nopol(nopol_input)
            
            if customer_data:
                st.success(f"✅ Customer ditemukan: **{customer_data['nama_customer']}**")
                nama_cust = customer_data['nama_customer']
                telp_cust = customer_data['no_telp']
                alamat_cust = customer_data['alamat']
                
                with st.expander("📋 Lihat Detail Customer", expanded=False):
                    col_a, col_b = st.columns(2)
                    with col_a:
                        st.write(f"**📌 Nama:** {nama_cust}")
                        st.write(f"**📞 Telp:** {telp_cust}")
                    with col_b:
                        st.write(f"**📍 Alamat:** {alamat_cust}")
            else:
                if nopol_input:
                    st.info("ℹ️ Customer baru - silakan isi data")
                nama_cust = st.text_input("👤 Nama Customer *", key="trans_nama", 
                                         placeholder="Nama lengkap customer")
                col_tel, col_addr = st.columns(2)
                with col_tel:
                    telp_cust = st.text_input("📞 No. Telepon", key="trans_telp", 
                                             placeholder="08xxxxxxxxxx")
                with col_addr:
                    alamat_cust = st.text_input("📍 Alamat", key="trans_alamat", 
                                               placeholder="Alamat customer")
            st.markdown('</div>', unsafe_allow_html=True)
        
        with col2:
            st.markdown('<div class="form-section">', unsafe_allow_html=True)
            st.markdown('<p class="section-title">🕐 Waktu</p>', unsafe_allow_html=True)
            now_wib = datetime.now(WIB)
            tanggal_trans = st.date_input("📅 Tanggal", value=now_wib.date(), key="trans_date")
            waktu_masuk = st.time_input("⏰ Waktu Masuk", value=now_wib.time(), key="trans_time")
            st.markdown('</div>', unsafe_allow_html=True)
        
        # Paket cuci
        st.markdown('<div class="form-section">', unsafe_allow_html=True)
        st.markdown('<p class="section-title">📦 Paket Cuci & Harga</p>', unsafe_allow_html=True)
        
        paket = st.selectbox("🧼 Pilih Paket Cuci *", options=list(paket_cucian.keys()), key="trans_paket")
        harga = paket_cucian[paket]
        st.success(f"💰 Harga: **Rp {harga:,.0f}**")
        
        st.markdown('</div>', unsafe_allow_html=True)
        
        # Checklist saat datang
        st.markdown('<div class="form-section">', unsafe_allow_html=True)
        st.markdown('<p class="section-title">✅ Checklist Kondisi Mobil Saat Datang</p>', unsafe_allow_html=True)
        
        selected_checks = []
        cols = st.columns(3)
        for idx, item in enumerate(checklist_items):
            with cols[idx % 3]:
                if st.checkbox(item, key=f"check_{idx}", value=True):
                    selected_checks.append(item)
        st.markdown('</div>', unsafe_allow_html=True)
        
        # QC Barang dalam mobil
        st.markdown('<div class="form-section">', unsafe_allow_html=True)
        st.markdown('<p class="section-title">📋 QC Barang dalam Mobil</p>', unsafe_allow_html=True)
        qc_barang = st.text_area("📝 Catat barang-barang di dalam mobil", 
                                 placeholder="Contoh:\n• Dompet di dashboard\n• HP di tempat HP\n• Karpet di bagasi\n• Payung di pintu",
                                 key="trans_qc_barang", height=120)
        
        # Catatan tambahan
        catatan = st.text_area("� Catatan Tambahan", placeholder="Catatan khusus untuk pengerjaan...", 
                              key="trans_catatan", height=80)
        st.markdown('</div>', unsafe_allow_html=True)
        
        # Submit button
        col1, col2, col3 = st.columns([2, 1, 2])
        with col2:
            submit_btn = st.button("💾 Simpan Transaksi", type="primary", use_container_width=True)
        
        if submit_btn:
            if not nopol_input or not nama_cust or not paket:
                st.error("❌ Mohon isi semua field yang wajib (*)")
            else:
                # Simpan customer baru jika belum ada
                if not customer_data:
                    success, msg = save_customer(nopol_input, nama_cust, telp_cust or "", alamat_cust or "")
                    if not success and "sudah terdaftar" not in msg.lower():
                        st.error(f"❌ Gagal menyimpan customer: {msg}")
                        st.stop()
                
                # Simpan transaksi
                trans_data = {
                    'nopol': nopol_input,
                    'nama_customer': nama_cust,
                    'tanggal': tanggal_trans.strftime('%d-%m-%Y'),
                    'waktu_masuk': waktu_masuk.strftime('%H:%M:%S'),
                    'waktu_selesai': '',
                    'paket_cuci': paket,
                    'harga': harga,
                    'checklist_datang': json.dumps(selected_checks),
                    'checklist_selesai': '',
                    'qc_barang': qc_barang,
                    'catatan': catatan,
                    'status': 'Dalam Proses',
                    'created_by': st.session_state.get('login_user', '')
                }
                
                success, msg = save_transaction(trans_data)
                if success:
                    add_audit("transaksi_baru", f"Nopol: {nopol_input}, Paket: {paket}, Harga: Rp {harga:,.0f}")
                    st.success(f"✅ {msg}")
                    st.balloons()
                    st.rerun()
                else:
                    st.error(f"❌ {msg}")
    
    with tab2:
        st.markdown('<div class="form-section">', unsafe_allow_html=True)
        st.markdown('<p class="section-title">✅ Selesaikan Transaksi</p>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)
        
        checklist_selesai_items = get_checklist_selesai()
        
        # Load transaksi yang masih dalam proses - HANYA yang berstatus "Dalam Proses"
        df_trans = get_all_transactions()
        
        # PENTING: Filter KETAT hanya status "Dalam Proses" - EXACT MATCH
        df_proses = df_trans[df_trans['status'].str.strip() == 'Dalam Proses'].copy()
        
        # Reset index untuk menghindari masalah indexing
        df_proses = df_proses.reset_index(drop=True)
        
        # Debug info untuk Admin
        if st.session_state.get('role') == 'Admin':
            with st.expander("🔧 Debug Info (Admin Only)"):
                st.write(f"Total transaksi di database: {len(df_trans)}")
                st.write(f"Transaksi 'Dalam Proses': {len(df_proses)}")
                st.write(f"Transaksi 'Selesai': {len(df_trans[df_trans['status'] == 'Selesai'])}")
                if not df_trans.empty:
                    st.write("Status terakhir 5 transaksi:")
                    st.dataframe(df_trans[['id', 'nopol', 'tanggal', 'status']].head(5))
                
                # Tampilkan detail df_proses
                if not df_proses.empty:
                    st.write("Detail transaksi 'Dalam Proses':")
                    st.dataframe(df_proses[['id', 'nopol', 'tanggal', 'status', 'waktu_masuk']])
        
        if df_proses.empty:
            st.info("📭 Tidak ada transaksi yang sedang dalam proses")
            st.success("✨ Semua transaksi sudah selesai dikerjakan!")
        else:
            st.success(f"📋 **{len(df_proses)} transaksi** sedang dalam proses")
            
            # Pilih transaksi - HANYA dari df_proses yang sudah difilter
            trans_display = df_proses[['id', 'tanggal', 'waktu_masuk', 'nopol', 'nama_customer', 'paket_cuci', 'status']].copy()
            
            # Validasi sekali lagi bahwa semua status adalah "Dalam Proses"
            trans_display = trans_display[trans_display['status'].str.strip() == 'Dalam Proses'].copy()
            
            if trans_display.empty:
                st.error("❌ Error: Data transaksi tidak valid. Silakan refresh halaman.")
                st.stop()
            
            trans_display['display'] = trans_display.apply(
                lambda x: f"🚗 {x['tanggal']} {x['waktu_masuk']} - {x['nopol']} - {x['nama_customer']} ({x['paket_cuci']})", axis=1
            )
            
            # Dropdown hanya berisi transaksi "Dalam Proses"
            selected_display = st.selectbox("🔍 Pilih Transaksi yang Akan Diselesaikan", 
                                          options=trans_display['display'].tolist(), 
                                          key="finish_trans",
                                          help="Hanya menampilkan transaksi dengan status 'Dalam Proses'")
            
            # Pastikan ID dalam format integer Python
            selected_id = int(trans_display[trans_display['display'] == selected_display]['id'].iloc[0])
            selected_trans = df_proses[df_proses['id'] == selected_id].iloc[0]
            
            # Debug info - tampilkan ID dan status
            st.info(f"🔍 ID Transaksi: **{selected_id}** | Status: **'{selected_trans['status']}'** | Tipe: {type(selected_id).__name__}")
            
            # Double check status
            if selected_trans['status'].strip() != 'Dalam Proses':
                st.error(f"❌ Error: Transaksi ini berstatus '{selected_trans['status']}', bukan 'Dalam Proses'")
                st.warning("🔄 Halaman akan di-refresh otomatis...")
                import time
                time.sleep(2)
                st.rerun()
                st.stop()
            
            # Tampilkan detail
            st.markdown('<div class="form-section">', unsafe_allow_html=True)
            with st.expander("📄 Detail Transaksi", expanded=True):
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.markdown(f"**🔖 Nopol:** `{selected_trans['nopol']}`")
                    st.markdown(f"**👤 Customer:** {selected_trans['nama_customer']}")
                with col2:
                    st.markdown(f"**📅 Tanggal:** {selected_trans['tanggal']}")
                    st.markdown(f"**⏰ Waktu Masuk:** {selected_trans['waktu_masuk']}")
                with col3:
                    st.markdown(f"**📦 Paket:** {selected_trans['paket_cuci']}")
                    st.markdown(f"**💰 Harga:** Rp {selected_trans['harga']:,.0f}")
                
                st.markdown("---")
                
                # Checklist saat datang
                try:
                    checks_datang = json.loads(selected_trans['checklist_datang'])
                    if checks_datang:
                        st.markdown("**✅ Checklist Kondisi Saat Datang:**")
                        cols = st.columns(3)
                        for idx, check in enumerate(checks_datang):
                            with cols[idx % 3]:
                                st.markdown(f"✓ {check}")
                except:
                    pass
                
                if selected_trans['qc_barang']:
                    st.markdown("**📋 Barang dalam Mobil:**")
                    st.info(selected_trans['qc_barang'])
                
                if selected_trans['catatan']:
                    st.markdown("**💬 Catatan:**")
                    st.warning(selected_trans['catatan'])
            st.markdown('</div>', unsafe_allow_html=True)
            
            # Input waktu selesai
            st.markdown('<div class="form-section">', unsafe_allow_html=True)
            st.markdown('<p class="section-title">⏰ Waktu Penyelesaian</p>', unsafe_allow_html=True)
            now_wib = datetime.now(WIB)
            waktu_selesai = st.time_input("🕐 Waktu Selesai", value=now_wib.time(), key="finish_time")
            st.markdown('</div>', unsafe_allow_html=True)
            
            # Checklist selesai
            st.markdown('<div class="form-section">', unsafe_allow_html=True)
            st.markdown('<p class="section-title">✅ Checklist QC Selesai Cuci</p>', unsafe_allow_html=True)
            
            selected_checks_selesai = []
            cols = st.columns(3)
            for idx, item in enumerate(checklist_selesai_items):
                with cols[idx % 3]:
                    if st.checkbox(item, key=f"check_done_{idx}", value=True):
                        selected_checks_selesai.append(item)
            st.markdown('</div>', unsafe_allow_html=True)
            
            # QC final barang
            st.markdown('<div class="form-section">', unsafe_allow_html=True)
            st.markdown('<p class="section-title">📋 Konfirmasi Final</p>', unsafe_allow_html=True)
            qc_final = st.text_area("✓ Konfirmasi Barang Customer Kembali Lengkap", 
                                   value=selected_trans['qc_barang'],
                                   placeholder="Pastikan semua barang customer kembali lengkap",
                                   key="finish_qc", height=100)
            
            catatan_final = st.text_area("� Catatan Penyelesaian", 
                                        placeholder="Hasil pengerjaan, kondisi akhir, dll...", 
                                        key="finish_catatan", height=80)
            st.markdown('</div>', unsafe_allow_html=True)
            
            # Finish button
            col1, col2, col3 = st.columns([2, 1, 2])
            with col2:
                finish_btn = st.button("✅ Selesaikan Transaksi", type="primary", use_container_width=True, key="btn_finish_trans")
            
            if finish_btn:
                # Validasi checklist minimal harus ada
                if not selected_checks_selesai:
                    st.error("❌ Mohon pilih minimal 1 checklist QC selesai!")
                elif not qc_final or qc_final.strip() == "":
                    st.error("❌ Mohon isi konfirmasi barang customer!")
                else:
                    # Debug: tampilkan ID yang akan diupdate
                    st.warning(f"🔍 Akan mengupdate transaksi ID: **{selected_id}** (Tipe: {type(selected_id).__name__})")
                    
                    # Cek ulang status sebelum update (double check)
                    df_recheck = get_all_transactions()
                    matching_trans = df_recheck[df_recheck['id'] == selected_id]
                    
                    if len(matching_trans) == 0:
                        st.error(f"❌ Transaksi ID {selected_id} tidak ditemukan saat recheck!")
                        st.write("IDs yang ada:", df_recheck['id'].tolist()[:10])
                        st.stop()
                    
                    current_status = matching_trans['status'].iloc[0].strip()
                    
                    if current_status != 'Dalam Proses':
                        st.error(f"❌ Transaksi ini sudah berstatus '{current_status}'. Halaman akan di-refresh.")
                        import time
                        time.sleep(2)
                        st.rerun()
                        st.stop()
                    
                    # Pastikan ID adalah integer
                    trans_id_to_update = int(selected_id)
                    
                    success, msg = update_transaction_finish(
                        trans_id_to_update,
                        waktu_selesai.strftime('%H:%M:%S'),
                        json.dumps(selected_checks_selesai),
                        qc_final,
                        catatan_final
                    )
                    
                    if success:
                        add_audit("transaksi_selesai", f"ID: {selected_id}, Nopol: {selected_trans['nopol']}")
                        
                        # Clear any session state cache
                        if 'finish_trans' in st.session_state:
                            del st.session_state['finish_trans']
                        
                        st.success(f"✅ {msg} - Transaksi telah dipindahkan ke status Selesai")
                        st.balloons()
                        import time
                        time.sleep(1)  # Delay untuk memastikan database ter-commit
                        st.rerun()
                    else:
                        st.error(f"❌ {msg}")
    
    with tab3:
        st.markdown('<div class="form-section">', unsafe_allow_html=True)
        st.markdown('<p class="section-title">📚 History Customer - Transaksi Selesai</p>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)
        
        # Load transaksi yang sudah selesai
        df_trans = get_all_transactions()
        df_selesai = df_trans[df_trans['status'] == 'Selesai'].copy()
        
        if df_selesai.empty:
            st.info("📭 Belum ada transaksi yang selesai")
        else:
            st.success(f"📋 **{len(df_selesai)} transaksi** telah selesai dikerjakan")
            
            # Filter pencarian
            col1, col2, col3 = st.columns([2, 2, 1])
            with col1:
                search_nopol = st.text_input("🔍 Cari Nopol", key="search_history_nopol")
            with col2:
                search_customer = st.text_input("🔍 Cari Nama Customer", key="search_history_customer")
            
            # Apply filter
            if search_nopol:
                df_selesai = df_selesai[df_selesai['nopol'].str.contains(search_nopol, case=False, na=False)]
            if search_customer:
                df_selesai = df_selesai[df_selesai['nama_customer'].str.contains(search_customer, case=False, na=False)]
            
            # Tampilkan tabel history
            if not df_selesai.empty:
                st.markdown("---")
                
                # Pilih transaksi untuk lihat detail
                with st.expander("👁️ Lihat Detail Transaksi"):
                    trans_display = df_selesai[['id', 'tanggal', 'waktu_masuk', 'waktu_selesai', 'nopol', 'nama_customer', 'paket_cuci', 'harga']].copy()
                    trans_display['display'] = trans_display.apply(
                        lambda x: f"✅ {x['tanggal']} | {x['waktu_masuk']}-{x['waktu_selesai']} | {x['nopol']} - {x['nama_customer']} | {x['paket_cuci']} (Rp {x['harga']:,.0f})", axis=1
                    )
                    
                    selected_history = st.selectbox("Pilih Transaksi", 
                                                   options=trans_display['display'].tolist(), 
                                                   key="select_history")
                    selected_hist_id = trans_display[trans_display['display'] == selected_history]['id'].iloc[0]
                    selected_hist = df_selesai[df_selesai['id'] == selected_hist_id].iloc[0]
                    
                    # Detail lengkap
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.markdown("**📋 Info Dasar**")
                        st.write(f"🔖 Nopol: `{selected_hist['nopol']}`")
                        st.write(f"👤 Customer: {selected_hist['nama_customer']}")
                        # Ambil telp dari tabel customer jika ada
                        cust_data = get_customer_by_nopol(selected_hist['nopol'])
                        telp_display = cust_data['no_telp'] if cust_data and cust_data.get('no_telp') else '-'
                        st.write(f"📞 Telp: {telp_display}")
                        st.write(f"📦 Paket: {selected_hist['paket_cuci']}")
                        st.write(f"💰 Harga: Rp {selected_hist['harga']:,.0f}")
                    
                    with col2:
                        st.markdown("**⏰ Waktu**")
                        st.write(f"📅 Tanggal: {selected_hist['tanggal']}")
                        st.write(f"🕐 Masuk: {selected_hist['waktu_masuk']}")
                        st.write(f"🕐 Selesai: {selected_hist['waktu_selesai']}")
                        st.write(f"👤 Oleh: {selected_hist['created_by']}")
                    
                    with col3:
                        st.markdown("**✅ Checklist & QC**")
                        try:
                            checks = json.loads(selected_hist['checklist_datang'])
                            st.write("Saat Datang:")
                            for check in checks[:3]:
                                st.write(f"✓ {check}")
                        except:
                            pass
                        
                        try:
                            checks_done = json.loads(selected_hist['checklist_selesai'])
                            st.write("Saat Selesai:")
                            for check in checks_done[:3]:
                                st.write(f"✓ {check}")
                        except:
                            pass
                    
                    if selected_hist['catatan']:
                        st.markdown("**💬 Catatan:**")
                        st.info(selected_hist['catatan'])
                
                # Tabel ringkas
                st.markdown("### 📊 Daftar Transaksi Selesai")
                df_display = df_selesai[['tanggal', 'waktu_masuk', 'waktu_selesai', 'nopol', 'nama_customer', 'paket_cuci', 'harga']].copy()
                df_display.columns = ['📅 Tanggal', '⏰ Masuk', '⏰ Selesai', '🔖 Nopol', '👤 Customer', '📦 Paket', '💰 Harga']
                df_display['💰 Harga'] = df_display['💰 Harga'].apply(lambda x: f"Rp {x:,.0f}")
                
                st.dataframe(df_display, use_container_width=True, hide_index=True)
            else:
                st.warning("⚠️ Tidak ada transaksi yang sesuai dengan pencarian")

def customer_page(role):
    st.markdown("""
    <style>
    .cust-header {
        background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%);
        padding: 1.5rem;
        border-radius: 15px;
        color: white;
        margin-bottom: 2rem;
        box-shadow: 0 4px 15px rgba(79, 172, 254, 0.3);
    }
    .cust-header h2 {
        margin: 0;
        font-size: 1.8rem;
        font-weight: 700;
    }
    .customer-card {
        background: white;
        padding: 1.5rem;
        border-radius: 12px;
        box-shadow: 0 2px 10px rgba(0,0,0,0.08);
        margin-bottom: 1rem;
        transition: transform 0.2s;
    }
    .customer-card:hover {
        transform: translateY(-3px);
        box-shadow: 0 4px 20px rgba(0,0,0,0.12);
    }
    .search-box {
        background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
        padding: 1rem;
        border-radius: 12px;
        margin-bottom: 1.5rem;
    }
    </style>
    """, unsafe_allow_html=True)
    
    st.markdown('<div class="cust-header"><h2>👥 Manajemen Customer</h2></div>', unsafe_allow_html=True)
    
    tab1, tab2 = st.tabs(["📋 Daftar Customer", "➕ Tambah Customer Baru"])
    
    with tab1:
        df_cust = get_all_customers()
        
        if df_cust.empty:
            st.info("📭 Belum ada customer terdaftar. Silakan tambah customer baru di tab sebelah →")
        else:
            # Search dengan UI lebih baik
            st.markdown('<div class="search-box">', unsafe_allow_html=True)
            col1, col2 = st.columns([3, 1])
            with col1:
                search = st.text_input("🔍 Cari customer", key="cust_search", 
                                      placeholder="Ketik nopol atau nama customer...",
                                      label_visibility="collapsed")
            with col2:
                st.metric("📊 Total Customer", len(df_cust))
            st.markdown('</div>', unsafe_allow_html=True)
            
            if search:
                mask = df_cust['nopol'].str.contains(search, case=False, na=False) | \
                       df_cust['nama_customer'].str.contains(search, case=False, na=False)
                df_display = df_cust[mask]
                if not df_display.empty:
                    st.success(f"✅ Ditemukan {len(df_display)} customer")
                else:
                    st.warning("⚠️ Tidak ada customer yang cocok dengan pencarian")
            else:
                df_display = df_cust
            
            if not df_display.empty:
                # Display dengan styling lebih baik
                df_show = df_display[['nopol', 'nama_customer', 'no_telp', 'alamat', 'created_at']].copy()
                df_show.columns = ['🔖 Nopol', '👤 Nama', '📞 Telepon', '📍 Alamat', '📅 Terdaftar']
                
                st.dataframe(
                    df_show,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "🔖 Nopol": st.column_config.TextColumn(width="small"),
                        "👤 Nama": st.column_config.TextColumn(width="medium"),
                        "📞 Telepon": st.column_config.TextColumn(width="small"),
                        "📍 Alamat": st.column_config.TextColumn(width="large"),
                        "📅 Terdaftar": st.column_config.TextColumn(width="small")
                    }
                )
                
                # Download CSV
                col1, col2, col3 = st.columns([2, 1, 2])
                with col2:
                    csv = df_show.to_csv(index=False).encode('utf-8')
                    st.download_button(
                        "📥 Download CSV", 
                        data=csv, 
                        file_name=f"customer_list_{datetime.now(WIB).strftime('%d%m%Y')}.csv", 
                        mime="text/csv",
                        use_container_width=True
                    )
    
    with tab2:
        st.markdown('<div class="customer-card">', unsafe_allow_html=True)
        st.subheader("📝 Form Customer Baru")
        
        with st.form("add_customer_form"):
            st.info("💡 Isi data customer dengan lengkap. Field dengan tanda * wajib diisi")
            
            col1, col2 = st.columns(2)
            with col1:
                nopol = st.text_input("🔖 Nomor Polisi *", placeholder="Contoh: B1234XYZ", 
                                     help="Format: huruf+angka+huruf").upper()
                nama = st.text_input("👤 Nama Customer *", placeholder="Nama lengkap customer")
            with col2:
                telp = st.text_input("📞 No. Telepon", placeholder="08xxxxxxxxxx",
                                    help="Format: 08xxx atau +62xxx")
                alamat = st.text_area("📍 Alamat", placeholder="Alamat lengkap customer", height=100)
            
            submitted = st.form_submit_button("💾 Simpan Customer", type="primary", use_container_width=True)
            
            if submitted:
                if not nopol or not nama:
                    st.error("❌ Nopol dan Nama wajib diisi")
                else:
                    success, msg = save_customer(nopol, nama, telp, alamat)
                    if success:
                        add_audit("customer_baru", f"Nopol: {nopol}, Nama: {nama}")
                        st.success(f"✅ {msg}")
                        st.balloons()
                        st.rerun()
                    else:
                        st.error(f"❌ {msg}")
        
        st.markdown('</div>', unsafe_allow_html=True)

def laporan_page(role):
    st.markdown("""
    <style>
    .laporan-header {
        background: linear-gradient(135deg, #fa709a 0%, #fee140 100%);
        padding: 2rem;
        border-radius: 15px;
        color: white;
        margin-bottom: 2rem;
        box-shadow: 0 4px 20px rgba(250, 112, 154, 0.4);
    }
    .laporan-header h1 {
        margin: 0;
        font-size: 2.2rem;
        font-weight: 800;
    }
    .filter-section {
        background: white;
        padding: 1.5rem;
        border-radius: 15px;
        box-shadow: 0 2px 10px rgba(0,0,0,0.08);
        margin-bottom: 2rem;
    }
    .metric-card {
        background: white;
        border-radius: 12px;
        padding: 1.2rem;
        box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        border-left: 4px solid;
        transition: transform 0.2s;
    }
    .metric-card:hover {
        transform: translateY(-3px);
    }
    .metric-card-1 { border-left-color: #43e97b; }
    .metric-card-2 { border-left-color: #4facfe; }
    .metric-card-3 { border-left-color: #f093fb; }
    .report-box {
        background: white;
        padding: 1.5rem;
        border-radius: 15px;
        box-shadow: 0 2px 10px rgba(0,0,0,0.08);
        margin-bottom: 1.5rem;
    }
    .report-title {
        color: #2d3436;
        font-size: 1.3rem;
        font-weight: 700;
        margin-bottom: 1rem;
        padding-bottom: 0.8rem;
        border-bottom: 3px solid #f0f0f0;
    }
    </style>
    """, unsafe_allow_html=True)
    
    st.markdown('''
    <div class="laporan-header">
        <h1>📊 Laporan Pendapatan</h1>
    </div>
    ''', unsafe_allow_html=True)
    
    df_trans = get_all_transactions()
    
    if df_trans.empty:
        st.info("📭 Belum ada data transaksi")
        return
    
    # Filter bulan dan tahun
    st.markdown('<div class="filter-section">', unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1, 1, 2])
    
    # Parse tanggal
    df_trans['tanggal_dt'] = pd.to_datetime(df_trans['tanggal'], format='%d-%m-%Y', errors='coerce')
    df_trans['bulan'] = df_trans['tanggal_dt'].dt.month
    df_trans['tahun'] = df_trans['tanggal_dt'].dt.year
    df_trans['bulan_tahun'] = df_trans['tanggal_dt'].dt.strftime('%m-%Y')
    
    with col1:
        years = sorted(df_trans['tahun'].dropna().unique(), reverse=True)
        selected_year = st.selectbox("📅 Tahun", options=years, key="lap_year")
    
    with col2:
        months = ['All'] + [f"{i:02d}" for i in range(1, 13)]
        month_names = ['Semua', 'Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni',
                      'Juli', 'Agustus', 'September', 'Oktober', 'November', 'Desember']
        selected_month = st.selectbox("📆 Bulan", options=range(len(months)), 
                                     format_func=lambda x: month_names[x], key="lap_month")
    st.markdown('</div>', unsafe_allow_html=True)
    
    # Filter data
    df_filtered = df_trans[df_trans['tahun'] == selected_year].copy()
    
    if selected_month != 0:  # Not "All"
        df_filtered = df_filtered[df_filtered['bulan'] == selected_month]
    
    if df_filtered.empty:
        st.warning("⚠️ Tidak ada data untuk periode ini")
        return
    
    # Statistik
    total_pendapatan = df_filtered['harga'].sum()
    total_transaksi = len(df_filtered)
    avg_transaksi = total_pendapatan / total_transaksi if total_transaksi > 0 else 0
    
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("💰 Total Pendapatan", f"Rp {total_pendapatan:,.0f}")
    with col2:
        st.metric("🚗 Total Transaksi", total_transaksi)
    with col3:
        st.metric("📊 Rata-rata per Transaksi", f"Rp {avg_transaksi:,.0f}")
    
    st.markdown("---")
    
    # Tabel per paket
    st.markdown('<div class="report-box">', unsafe_allow_html=True)
    st.markdown('<p class="report-title">📦 Pendapatan per Paket Cuci</p>', unsafe_allow_html=True)
    
    paket_summary = df_filtered.groupby('paket_cuci').agg(
        Jumlah=('id', 'count'),
        Total_Pendapatan=('harga', 'sum'),
        Rata_rata=('harga', 'mean')
    ).reset_index()
    paket_summary.columns = ['Paket Cuci', 'Jumlah', 'Total Pendapatan', 'Rata-rata']
    paket_summary = paket_summary.sort_values('Total Pendapatan', ascending=False)
    
    # Format currency
    paket_summary['Total Pendapatan'] = paket_summary['Total Pendapatan'].apply(lambda x: f"Rp {x:,.0f}")
    paket_summary['Rata-rata'] = paket_summary['Rata-rata'].apply(lambda x: f"Rp {x:,.0f}")
    
    st.dataframe(paket_summary, use_container_width=True, hide_index=True)
    st.markdown('</div>', unsafe_allow_html=True)
    
    # Grafik
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown('<div class="report-box">', unsafe_allow_html=True)
        st.markdown('<p class="report-title">📊 Jumlah Transaksi per Paket</p>', unsafe_allow_html=True)
        paket_count = df_filtered.groupby('paket_cuci').size().reset_index(name='count')
        paket_count = paket_count.sort_values('count', ascending=False)
        chart = alt.Chart(paket_count).mark_bar(cornerRadiusEnd=8).encode(
            x=alt.X('count:Q', title='Jumlah'),
            y=alt.Y('paket_cuci:N', sort='-x', title=''),
            color=alt.Color('count:Q', scale=alt.Scale(scheme='purples'), legend=None),
            tooltip=[
                alt.Tooltip('paket_cuci:N', title='Paket'),
                alt.Tooltip('count:Q', title='Jumlah')
            ]
        ).properties(height=280)
        st.altair_chart(chart, use_container_width=True)
        st.markdown('</div>', unsafe_allow_html=True)
    
    with col2:
        st.markdown('<div class="report-box">', unsafe_allow_html=True)
        st.markdown('<p class="report-title">💰 Pendapatan per Paket</p>', unsafe_allow_html=True)
        paket_income = df_filtered.groupby('paket_cuci')['harga'].sum().reset_index()
        paket_income.columns = ['paket', 'total']
        pie = alt.Chart(paket_income).mark_arc(innerRadius=60).encode(
            theta='total:Q',
            color=alt.Color('paket:N', legend=alt.Legend(orient='bottom', title=None)),
            tooltip=[
                alt.Tooltip('paket:N', title='Paket'),
                alt.Tooltip('total:Q', format=',.0f', title='Pendapatan (Rp)')
            ]
        ).properties(height=280)
        st.altair_chart(pie, use_container_width=True)
        st.markdown('</div>', unsafe_allow_html=True)
    
    # Tren harian
    if selected_month != 0:
        st.markdown('<div class="report-box">', unsafe_allow_html=True)
        st.markdown('<p class="report-title">📈 Tren Pendapatan Harian</p>', unsafe_allow_html=True)
        daily_income = df_filtered.groupby('tanggal').agg(
            total=('harga', 'sum'),
            count=('id', 'count')
        ).reset_index().sort_values('tanggal')
        
        # Convert tanggal untuk chart
        daily_income['tanggal_dt'] = pd.to_datetime(daily_income['tanggal'], format='%d-%m-%Y')
        
        line = alt.Chart(daily_income).mark_line(point=True, strokeWidth=3, color='#fa709a').encode(
            x=alt.X('tanggal_dt:T', title='Tanggal', axis=alt.Axis(format='%d-%m')),
            y=alt.Y('total:Q', title='Pendapatan (Rp)'),
            tooltip=[
                alt.Tooltip('tanggal:N', title='Tanggal'),
                alt.Tooltip('total:Q', format=',.0f', title='Rp'),
                alt.Tooltip('count:Q', title='Transaksi')
            ]
        ).properties(height=250)
        
        st.altair_chart(line, use_container_width=True)
        st.markdown('</div>', unsafe_allow_html=True)

def setting_toko_page(role):
    st.markdown("""
    <style>
    .setting-header {
        background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
        padding: 1.5rem;
        border-radius: 15px;
        color: white;
        margin-bottom: 2rem;
        box-shadow: 0 4px 15px rgba(245, 87, 108, 0.3);
    }
    .setting-header h2 {
        margin: 0;
        font-size: 1.8rem;
        font-weight: 700;
    }
    .setting-section {
        background: white;
        padding: 1.5rem;
        border-radius: 12px;
        box-shadow: 0 2px 10px rgba(0,0,0,0.08);
        margin-bottom: 1.5rem;
    }
    </style>
    """, unsafe_allow_html=True)
    
    st.markdown('<div class="setting-header"><h2>⚙️ Setting Toko</h2></div>', unsafe_allow_html=True)
    
    # Check role
    if role not in ["Admin", "Supervisor"]:
        st.warning("⚠️ Hanya Admin dan Supervisor yang dapat mengakses halaman ini")
        return
    
    tab1, tab2, tab3, tab4 = st.tabs(["📦 Paket Cuci", "✅ Checklist Datang", "✓ Checklist Selesai", "🏪 Info Toko"])
    
    with tab1:
        st.markdown('<div class="setting-section">', unsafe_allow_html=True)
        st.subheader("📦 Kelola Paket Cucian")
        
        # Load paket cucian
        paket_cucian = get_paket_cucian()
        
        st.info("ℹ️ Tambah, edit, atau hapus paket cucian yang tersedia")
        
        # Tampilkan paket yang ada
        st.markdown("##### Paket Cucian Saat Ini:")
        for idx, (nama, harga) in enumerate(paket_cucian.items()):
            col1, col2, col3 = st.columns([3, 2, 1])
            with col1:
                new_nama = st.text_input("Nama Paket", value=nama, key=f"paket_nama_{idx}", label_visibility="collapsed")
            with col2:
                new_harga = st.number_input("Harga", value=harga, min_value=0, step=5000, key=f"paket_harga_{idx}", label_visibility="collapsed")
            with col3:
                if st.button("🗑️", key=f"del_paket_{idx}", help="Hapus paket"):
                    del paket_cucian[nama]
                    update_setting("paket_cucian", paket_cucian)
                    add_audit("setting_toko", f"Hapus paket: {nama}")
                    st.rerun()
            
            # Update jika berubah
            if new_nama != nama or new_harga != harga:
                if new_nama and new_nama != nama:
                    paket_cucian[new_nama] = paket_cucian.pop(nama)
                paket_cucian[new_nama if new_nama else nama] = new_harga
        
        st.markdown("---")
        
        # Tambah paket baru
        st.markdown("##### Tambah Paket Baru:")
        col1, col2, col3 = st.columns([3, 2, 1])
        with col1:
            nama_baru = st.text_input("Nama Paket Baru", key="new_paket_nama", placeholder="Contoh: Cuci Express")
        with col2:
            harga_baru = st.number_input("Harga", value=50000, min_value=0, step=5000, key="new_paket_harga")
        with col3:
            if st.button("➕ Tambah", key="add_paket"):
                if nama_baru:
                    paket_cucian[nama_baru] = harga_baru
                    update_setting("paket_cucian", paket_cucian)
                    add_audit("setting_toko", f"Tambah paket: {nama_baru} - Rp {harga_baru:,.0f}")
                    st.success(f"✅ Paket '{nama_baru}' berhasil ditambahkan")
                    st.rerun()
        
        if st.button("💾 Simpan Semua Perubahan Paket", type="primary", use_container_width=True):
            success, msg = update_setting("paket_cucian", paket_cucian)
            if success:
                add_audit("setting_toko", "Update paket cucian")
                st.success("✅ Paket cucian berhasil diupdate")
                st.rerun()
            else:
                st.error(f"❌ {msg}")
        
        st.markdown('</div>', unsafe_allow_html=True)
    
    with tab2:
        st.markdown('<div class="setting-section">', unsafe_allow_html=True)
        st.subheader("✅ Kelola Checklist Mobil Datang")
        
        checklist_datang = get_checklist_datang()
        
        st.info("ℹ️ Checklist untuk memeriksa kondisi mobil saat pertama datang")
        
        # Tampilkan checklist yang ada
        new_checklist = []
        for idx, item in enumerate(checklist_datang):
            col1, col2 = st.columns([5, 1])
            with col1:
                new_item = st.text_input(f"Item {idx+1}", value=item, key=f"check_datang_{idx}", label_visibility="collapsed")
                if new_item:
                    new_checklist.append(new_item)
            with col2:
                if st.button("🗑️", key=f"del_check_datang_{idx}", help="Hapus item"):
                    checklist_datang.pop(idx)
                    update_setting("checklist_datang", checklist_datang)
                    add_audit("setting_toko", f"Hapus checklist datang: {item}")
                    st.rerun()
        
        st.markdown("---")
        
        # Tambah item baru
        st.markdown("##### Tambah Item Baru:")
        col1, col2 = st.columns([5, 1])
        with col1:
            item_baru = st.text_input("Item Checklist Baru", key="new_check_datang", placeholder="Contoh: Kondisi interior bersih")
        with col2:
            if st.button("➕", key="add_check_datang"):
                if item_baru:
                    checklist_datang.append(item_baru)
                    update_setting("checklist_datang", checklist_datang)
                    add_audit("setting_toko", f"Tambah checklist datang: {item_baru}")
                    st.success(f"✅ Item berhasil ditambahkan")
                    st.rerun()
        
        if st.button("💾 Simpan Perubahan Checklist", type="primary", use_container_width=True, key="save_checklist_datang"):
            success, msg = update_setting("checklist_datang", new_checklist if new_checklist else checklist_datang)
            if success:
                add_audit("setting_toko", "Update checklist datang")
                st.success("✅ Checklist berhasil diupdate")
                st.rerun()
            else:
                st.error(f"❌ {msg}")
        
        st.markdown('</div>', unsafe_allow_html=True)
    
    with tab3:
        st.markdown('<div class="setting-section">', unsafe_allow_html=True)
        st.subheader("✓ Kelola Checklist QC Selesai")
        
        checklist_selesai = get_checklist_selesai()
        
        st.info("ℹ️ Checklist untuk quality control setelah selesai cuci")
        
        # Tampilkan checklist yang ada
        new_checklist_selesai = []
        for idx, item in enumerate(checklist_selesai):
            col1, col2 = st.columns([5, 1])
            with col1:
                new_item = st.text_input(f"Item {idx+1}", value=item, key=f"check_selesai_{idx}", label_visibility="collapsed")
                if new_item:
                    new_checklist_selesai.append(new_item)
            with col2:
                if st.button("🗑️", key=f"del_check_selesai_{idx}", help="Hapus item"):
                    checklist_selesai.pop(idx)
                    update_setting("checklist_selesai", checklist_selesai)
                    add_audit("setting_toko", f"Hapus checklist selesai: {item}")
                    st.rerun()
        
        st.markdown("---")
        
        # Tambah item baru
        st.markdown("##### Tambah Item Baru:")
        col1, col2 = st.columns([5, 1])
        with col1:
            item_baru_selesai = st.text_input("Item Checklist Baru", key="new_check_selesai", placeholder="Contoh: Velg mengkilap")
        with col2:
            if st.button("➕", key="add_check_selesai"):
                if item_baru_selesai:
                    checklist_selesai.append(item_baru_selesai)
                    update_setting("checklist_selesai", checklist_selesai)
                    add_audit("setting_toko", f"Tambah checklist selesai: {item_baru_selesai}")
                    st.success(f"✅ Item berhasil ditambahkan")
                    st.rerun()
        
        if st.button("💾 Simpan Perubahan Checklist", type="primary", use_container_width=True, key="save_checklist_selesai"):
            success, msg = update_setting("checklist_selesai", new_checklist_selesai if new_checklist_selesai else checklist_selesai)
            if success:
                add_audit("setting_toko", "Update checklist selesai")
                st.success("✅ Checklist berhasil diupdate")
                st.rerun()
            else:
                st.error(f"❌ {msg}")
        
        st.markdown('</div>', unsafe_allow_html=True)
    
    with tab4:
        st.markdown('<div class="setting-section">', unsafe_allow_html=True)
        st.subheader("🏪 Informasi Toko")
        
        toko_info = get_setting("toko_info")
        if not toko_info:
            toko_info = {
                "nama": "CUCI MOBIL BERSIH",
                "alamat": "Jl. Contoh No. 123",
                "telp": "08123456789",
                "email": "info@cucimobil.com"
            }
        
        with st.form("toko_info_form"):
            st.info("ℹ️ Informasi ini akan muncul di laporan dan dokumen")
            
            nama_toko = st.text_input("🏪 Nama Toko", value=toko_info.get("nama", ""))
            alamat_toko = st.text_area("📍 Alamat", value=toko_info.get("alamat", ""))
            col1, col2 = st.columns(2)
            with col1:
                telp_toko = st.text_input("📞 Telepon", value=toko_info.get("telp", ""))
            with col2:
                email_toko = st.text_input("📧 Email", value=toko_info.get("email", ""))
            
            submitted = st.form_submit_button("💾 Simpan Info Toko", type="primary", use_container_width=True)
            
            if submitted:
                new_toko_info = {
                    "nama": nama_toko,
                    "alamat": alamat_toko,
                    "telp": telp_toko,
                    "email": email_toko
                }
                success, msg = update_setting("toko_info", new_toko_info)
                if success:
                    add_audit("setting_toko", "Update info toko")
                    st.success("✅ Info toko berhasil diupdate")
                    st.rerun()
                else:
                    st.error(f"❌ {msg}")
        
        st.markdown('</div>', unsafe_allow_html=True)

def audit_trail_page():
    st.header("Audit Trail")
    role = st.session_state.get("login_role", "-")
    uname = st.session_state.get("login_user", "-")
    
    if role == "Supervisor":
        st.info("Sebagai Supervisor, Anda dapat melihat semua aktivitas dari semua user.")
    else:
        st.info("Anda hanya dapat melihat aktivitas Anda sendiri.")
    
    # Load audit trail dari database
    df_audit = load_audit_trail()

    # Filters
    c1, c2, c3 = st.columns([1,1,1.2])
    all_users = sorted(df_audit['user'].dropna().unique().tolist()) if not df_audit.empty else []
    with c1:
        if role == "Supervisor":
            user_filter = st.multiselect("Filter User", options=all_users, default=all_users)
        else:
            user_filter = [uname]
            st.multiselect("Filter User", options=[uname], default=[uname], disabled=True)
    with c2:
        search = st.text_input("Cari kata kunci", placeholder="action/detail...")
    with c3:
        if not df_audit.empty:
            # Parse timestamps dengan format fleksibel (support format lama dan baru)
            df_audit['timestamp_dt'] = pd.to_datetime(df_audit['timestamp'], format='mixed', dayfirst=True, errors='coerce')
            date_min = df_audit['timestamp_dt'].min().date()
            date_max = df_audit['timestamp_dt'].max().date()
        else:
            date_min = date_max = datetime.now().date()
        date_range = st.date_input("Rentang tanggal", value=(date_min, date_max))

    # Apply filters
    if not df_audit.empty:
        # timestamp_dt sudah di-parse di atas
        
        # Filter by user
        if user_filter:
            df_audit = df_audit[df_audit['user'].isin(user_filter)]
        
        # Filter by search keyword
        if search:
            mask = df_audit['action'].str.contains(search, case=False, na=False) | df_audit['detail'].str.contains(search, case=False, na=False)
            df_audit = df_audit[mask]
        
        # Filter by date range
        if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
            start_d, end_d = pd.to_datetime(date_range[0]), pd.to_datetime(date_range[1])
            df_audit = df_audit[(df_audit['timestamp_dt'] >= start_d) & (df_audit['timestamp_dt'] <= end_d + pd.Timedelta(days=1))]
        
        # Display results
        df_display = df_audit.drop(columns=['timestamp_dt', 'id']).sort_values('timestamp', ascending=False)
        st.dataframe(df_display, use_container_width=True)
        
        # Statistics
        st.markdown("---")
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Total Records", len(df_display))
        with col2:
            st.metric("Unique Users", df_display['user'].nunique())
        with col3:
            st.metric("Unique Actions", df_display['action'].nunique())
    else:
        st.info("Belum ada data audit trail.")

def user_setting_page():
    st.header("⚙️ User Setting")
    st.info("Fitur ganti password dan nama user.")
    uname = st.session_state.get("login_user", "-")
    role = st.session_state.get("login_role", "-")
    st.write(f"**Username:** {uname}")
    st.write(f"**Role:** {role}")
    
    st.markdown("---")
    
    with st.form("user_setting_form"):
        st.subheader("Ubah Informasi")
        new_name = st.text_input("Ganti Nama Tampilan", value=uname)
        new_pass = st.text_input("Ganti Password", type="password", placeholder="Kosongkan jika tidak ingin mengubah")
        confirm_pass = st.text_input("Konfirmasi Password Baru", type="password")
        
        submitted = st.form_submit_button("💾 Simpan Perubahan", type="primary", use_container_width=True)
        
        if submitted:
            changes = []
            
            if new_name and new_name != uname:
                st.session_state["login_user"] = new_name
                changes.append(f"username: {uname} → {new_name}")
            
            if new_pass:
                if new_pass != confirm_pass:
                    st.error("❌ Konfirmasi password tidak cocok!")
                    st.stop()
                elif len(new_pass) < 6:
                    st.error("❌ Password minimal 6 karakter!")
                    st.stop()
                else:
                    USERS[uname]["password"] = new_pass
                    changes.append("password diubah")
            
            if changes:
                add_audit("update_user_setting", f"User setting: {', '.join(changes)}")
                st.success("✅ Perubahan user disimpan (hanya berlaku sesi ini)")
                st.balloons()
                st.rerun()
            else:
                st.info("ℹ️ Tidak ada perubahan.")

def main():
    st.set_page_config(page_title="Cuci Mobil Apps", layout="wide", page_icon="🚗")
    
    # Initialize database di awal sebelum login
    init_db()
    
    if "is_logged_in" not in st.session_state or not st.session_state["is_logged_in"]:
        login_page()
        return
    
    role = st.session_state.get("login_role", "Kasir")
    
    # Initialize menu state
    if "menu" not in st.session_state:
        st.session_state["menu"] = "Dashboard"
    
    # Custom sidebar styling
    st.sidebar.markdown("""
    <style>
    [data-testid="stSidebar"] {
        background-color: #f8f9fa;
    }
    .sidebar-user-info {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        padding: 1.2rem;
        border-radius: 12px;
        margin-bottom: 1.5rem;
        box-shadow: 0 4px 15px rgba(102, 126, 234, 0.4);
    }
    .sidebar-user-info h3 {
        margin: 0 0 0.5rem 0;
        font-size: 1.1rem;
        font-weight: 600;
    }
    .sidebar-user-info p {
        margin: 0.25rem 0;
        font-size: 0.9rem;
        opacity: 0.95;
    }
    .menu-title {
        color: #2d3436;
        font-size: 0.85rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 1px;
        margin: 1.5rem 0 0.8rem 0;
        padding-left: 0.3rem;
    }
    .stButton > button {
        width: 100%;
        padding: 0.85rem 1rem;
        margin-bottom: 0.5rem;
        background: #ffffff;
        border: 2px solid #e8e8e8;
        border-radius: 10px;
        color: #2d3436;
        font-size: 0.95rem;
        font-weight: 500;
        transition: all 0.3s ease;
        text-align: left;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
    }
    .stButton > button:hover {
        background: #f8f9fa;
        border-color: #667eea;
        transform: translateX(4px);
        box-shadow: 0 4px 12px rgba(102, 126, 234, 0.15);
    }
    .stButton > button[kind="secondary"] {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%) !important;
        border-color: #667eea !important;
        color: white !important;
        font-weight: 600 !important;
        box-shadow: 0 4px 15px rgba(102, 126, 234, 0.3) !important;
    }
    .logout-btn > button {
        background: #ff6b6b !important;
        border: none !important;
        color: white !important;
        font-weight: 600 !important;
        margin-top: 1.5rem !important;
        box-shadow: 0 4px 12px rgba(255, 107, 107, 0.3) !important;
    }
    .logout-btn > button:hover {
        background: #ff5252 !important;
        transform: translateY(-2px) !important;
        box-shadow: 0 6px 20px rgba(255, 107, 107, 0.4) !important;
    }
    </style>
    """, unsafe_allow_html=True)
    
    # Sidebar
    st.sidebar.markdown(f"""
    <div class="sidebar-user-info">
        <h3>👤 {st.session_state.get('login_user', '-').upper()}</h3>
        <p>🎯 Role: {role}</p>
    </div>
    """, unsafe_allow_html=True)
    
    st.sidebar.markdown('<p class="menu-title">🚗 MENU CUCI MOBIL</p>', unsafe_allow_html=True)
    
    # Menu items
    menu_items = [
        ("Dashboard", "📊"),
        ("Transaksi", "🚗"),
        ("Customer", "👥"),
        ("Laporan", "📊"),
        ("Setting Toko", "⚙️"),
        ("Audit Trail", "📜"),
        ("User Setting", "👤")
    ]
    
    for menu_name, icon in menu_items:
        button_type = "secondary" if st.session_state["menu"] == menu_name else "primary"
        if st.sidebar.button(f"{icon}  {menu_name}", key=f"menu_{menu_name}", use_container_width=True, type=button_type):
            st.session_state["menu"] = menu_name
            st.rerun()
    
    # Logout button
    st.sidebar.markdown("<br>", unsafe_allow_html=True)
    st.sidebar.markdown('<div class="logout-btn">', unsafe_allow_html=True)
    if st.sidebar.button("🚪  Logout", key="logout_btn", use_container_width=True):
        add_audit("logout", f"Logout user {st.session_state.get('login_user','-')}")
        st.session_state.clear()
        st.rerun()
    st.sidebar.markdown('</div>', unsafe_allow_html=True)
    
    # Page title
    st.title("🚗 Sistem Manajemen Cuci Mobil")
    
    menu = st.session_state["menu"]

    # Route to pages
    if menu == "Dashboard":
        dashboard_page(role)
    elif menu == "Transaksi":
        transaksi_page(role)
    elif menu == "Customer":
        customer_page(role)
    elif menu == "Laporan":
        laporan_page(role)
    elif menu == "Setting Toko":
        setting_toko_page(role)
    elif menu == "User Setting":
        user_setting_page()
    elif menu == "Audit Trail":
        audit_trail_page()

if __name__ == "__main__":
    main()
