import streamlit as st
import pandas as pd
import sqlite3
import os
from datetime import datetime, timedelta
import altair as alt
import pytz
import io
import hashlib

# Timezone GMT+7 (WIB)
WIB = pytz.timezone('Asia/Jakarta')

# Database Name
DB_NAME = "ipcc_system.db"

# Kategori WBS Standard untuk setiap project
WBS_CATEGORIES = [
    "MATERIAL BUILDING BUDGET",
    "BUDGET SEWA",
    "LABOUR BUDGET",
    "OPERASIONAL BUDGET",
    "BIAYA ADMIN"
]

# ==================== HELPER FUNCTIONS ====================

def hash_password(password):
    """Hash password untuk keamanan"""
    return hashlib.sha256(password.encode()).hexdigest()

def parse_date(date_str):
    """Parse tanggal dari berbagai format ke datetime object"""
    if pd.isna(date_str) or date_str == '' or date_str is None:
        return None
    
    date_str = str(date_str).strip()
    
    for fmt in ['%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y', '%Y/%m/%d']:
        try:
            return datetime.strptime(date_str, fmt)
        except:
            continue
    
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

def format_currency(amount):
    """Format angka ke format mata uang IDR"""
    if pd.isna(amount) or amount is None:
        return "Rp 0"
    try:
        return f"Rp {amount:,.0f}".replace(",", ".")
    except:
        return "Rp 0"

def calculate_ev(planned_value, percent_complete):
    """Calculate Earned Value"""
    return (percent_complete / 100) * planned_value

def calculate_cpi(ev, ac):
    """Calculate Cost Performance Index"""
    if ac == 0:
        return 0
    return ev / ac

def calculate_spi(ev, pv):
    """Calculate Schedule Performance Index"""
    if pv == 0:
        return 0
    return ev / pv

def get_traffic_light_status(cpi, spi):
    """Determine project health status"""
    if cpi >= 0.95 and spi >= 0.95:
        return "🟢 On Track"
    elif cpi >= 0.85 and spi >= 0.85:
        return "🟡 At Risk"
    else:
        return "🔴 Critical"

# ==================== DATABASE INITIALIZATION ====================

def migrate_db():
    """Migrate database schema to ensure all columns exist"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    try:
        # Check if cost_items table exists and has correct columns
        c.execute("PRAGMA table_info(cost_items)")
        columns = [column[1] for column in c.fetchall()]
        
        if 'cost_items' in [row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]:
            # Check for missing columns and add them
            if 'category_id' not in columns:
                st.warning("Migrating database: Adding category_id column to cost_items table...")
                c.execute("ALTER TABLE cost_items ADD COLUMN category_id INTEGER")
                conn.commit()
            
            if 'is_budget_estimation' not in columns:
                st.warning("Migrating database: Adding is_budget_estimation column to cost_items table...")
                c.execute("ALTER TABLE cost_items ADD COLUMN is_budget_estimation INTEGER DEFAULT 1")
                conn.commit()
            
            if 'linked_actual_id' not in columns:
                c.execute("ALTER TABLE cost_items ADD COLUMN linked_actual_id INTEGER")
                conn.commit()
            
            if 'created_by' not in columns:
                c.execute("ALTER TABLE cost_items ADD COLUMN created_by TEXT")
                conn.commit()
            
            if 'created_at' not in columns:
                c.execute("ALTER TABLE cost_items ADD COLUMN created_at TEXT")
                conn.commit()
            
            if 'updated_at' not in columns:
                c.execute("ALTER TABLE cost_items ADD COLUMN updated_at TEXT")
                conn.commit()
        
        # Check budget_categories table
        if 'budget_categories' in [row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]:
            c.execute("PRAGMA table_info(budget_categories)")
            columns = [column[1] for column in c.fetchall()]
            
            if 'is_excluded_from_project' not in columns:
                st.warning("Migrating database: Adding is_excluded_from_project column to budget_categories table...")
                c.execute("ALTER TABLE budget_categories ADD COLUMN is_excluded_from_project INTEGER DEFAULT 0")
                conn.commit()
        
        # Check actual_spending table
        if 'actual_spending' in [row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]:
            c.execute("PRAGMA table_info(actual_spending)")
            columns = [column[1] for column in c.fetchall()]
            
            if 'category_id' not in columns:
                st.warning("Migrating database: Adding category_id column to actual_spending table...")
                c.execute("ALTER TABLE actual_spending ADD COLUMN category_id INTEGER")
                conn.commit()
            
            if 'is_planned' not in columns:
                st.warning("Migrating database: Adding is_planned column to actual_spending table...")
                c.execute("ALTER TABLE actual_spending ADD COLUMN is_planned INTEGER DEFAULT 1")
                conn.commit()
            
            if 'invoice_file_url' not in columns:
                c.execute("ALTER TABLE actual_spending ADD COLUMN invoice_file_url TEXT")
                conn.commit()
            
            if 'created_by' not in columns:
                c.execute("ALTER TABLE actual_spending ADD COLUMN created_by TEXT")
                conn.commit()
        
        conn.commit()
        
    except Exception as e:
        st.error(f"Migration error: {str(e)}")
    finally:
        conn.close()

def init_db():
    """Initialize database dengan semua tabel yang diperlukan"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # Tabel Users
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            full_name TEXT NOT NULL,
            email TEXT,
            role TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            last_login TEXT
        )
    ''')
    
    # Tabel Projects
    c.execute('''
        CREATE TABLE IF NOT EXISTS projects (
            project_id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_code TEXT UNIQUE NOT NULL,
            project_name TEXT NOT NULL,
            description TEXT,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            budget_total REAL NOT NULL,
            status TEXT DEFAULT 'Planning',
            project_manager TEXT,
            client_name TEXT,
            location TEXT,
            created_by TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT
        )
    ''')
    
    # Tabel Budget Categories (simplified WBS)
    c.execute('''
        CREATE TABLE IF NOT EXISTS budget_categories (
            category_id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            category_name TEXT NOT NULL,
            budget_amount REAL DEFAULT 0,
            actual_amount REAL DEFAULT 0,
            is_excluded_from_project INTEGER DEFAULT 0,
            notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT,
            FOREIGN KEY (project_id) REFERENCES projects(project_id)
        )
    ''')
    
    # Tabel Cost Items (detail items untuk setiap kategori)
    c.execute('''
        CREATE TABLE IF NOT EXISTS cost_items (
            item_id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            description TEXT NOT NULL,
            unit TEXT,
            budget_price REAL DEFAULT 0,
            actual_price REAL DEFAULT 0,
            is_budget_estimation INTEGER DEFAULT 1,
            linked_actual_id INTEGER,
            notes TEXT,
            created_by TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT,
            FOREIGN KEY (category_id) REFERENCES budget_categories(category_id)
        )
    ''')
    
    # Tabel Actual Spending (pengeluaran yang link ke budget estimation)
    c.execute('''
        CREATE TABLE IF NOT EXISTS actual_spending (
            actual_id INTEGER PRIMARY KEY AUTOINCREMENT,
            budget_item_id INTEGER,
            category_id INTEGER NOT NULL,
            vendor_id INTEGER,
            actual_date TEXT NOT NULL,
            description TEXT NOT NULL,
            unit TEXT,
            actual_price REAL NOT NULL,
            invoice_number TEXT,
            invoice_file_url TEXT,
            payment_status TEXT DEFAULT 'Pending',
            is_planned INTEGER DEFAULT 1,
            notes TEXT,
            created_by TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (budget_item_id) REFERENCES cost_items(item_id),
            FOREIGN KEY (category_id) REFERENCES budget_categories(category_id),
            FOREIGN KEY (vendor_id) REFERENCES vendors(vendor_id)
        )
    ''')
    
    # Tabel Progress Tracking
    c.execute('''
        CREATE TABLE IF NOT EXISTS progress_tracking (
            progress_id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER NOT NULL,
            progress_date TEXT NOT NULL,
            percent_complete REAL NOT NULL,
            evidence_file_url TEXT,
            remarks TEXT,
            reported_by TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (category_id) REFERENCES budget_categories(category_id)
        )
    ''')
    
    # Tabel Vendors
    c.execute('''
        CREATE TABLE IF NOT EXISTS vendors (
            vendor_id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor_code TEXT UNIQUE NOT NULL,
            vendor_name TEXT NOT NULL,
            contact_person TEXT,
            phone TEXT,
            email TEXT,
            address TEXT,
            vendor_type TEXT,
            rating REAL,
            is_active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL
        )
    ''')
    
    # Tabel Contracts
    c.execute('''
        CREATE TABLE IF NOT EXISTS contracts (
            contract_id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            vendor_id INTEGER NOT NULL,
            contract_number TEXT UNIQUE NOT NULL,
            contract_name TEXT NOT NULL,
            contract_value REAL NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            status TEXT DEFAULT 'Active',
            payment_terms TEXT,
            contract_file_url TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(project_id),
            FOREIGN KEY (vendor_id) REFERENCES vendors(vendor_id)
        )
    ''')
    
    # Tabel Audit Trail
    c.execute('''
        CREATE TABLE IF NOT EXISTS audit_trail (
            audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            user TEXT NOT NULL,
            action TEXT NOT NULL,
            module TEXT NOT NULL,
            detail TEXT,
            ip_address TEXT
        )
    ''')
    
    # Insert default users jika belum ada
    c.execute("SELECT COUNT(*) FROM users")
    if c.fetchone()[0] == 0:
        default_users = [
            ('admin', hash_password('admin123'), 'System Administrator', 'admin@ipcc.com', 'Owner'),
            ('pm001', hash_password('pm123'), 'John Doe', 'john@ipcc.com', 'Project Manager'),
            ('cc001', hash_password('cc123'), 'Jane Smith', 'jane@ipcc.com', 'Cost Controller'),
            ('proc001', hash_password('proc123'), 'Mike Johnson', 'mike@ipcc.com', 'Procurement'),
            ('eng001', hash_password('eng123'), 'Sarah Williams', 'sarah@ipcc.com', 'Engineer'),
        ]
        
        for username, pwd_hash, full_name, email, role in default_users:
            c.execute('''
                INSERT INTO users (username, password_hash, full_name, email, role, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (username, pwd_hash, full_name, email, role, datetime.now(WIB).strftime('%Y-%m-%d %H:%M:%S')))
    
    conn.commit()
    conn.close()

# ==================== AUDIT TRAIL ====================

def add_audit(action, module, detail=None):
    """Simpan audit trail"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    now_wib = datetime.now(WIB)
    c.execute("""
        INSERT INTO audit_trail (timestamp, user, action, module, detail, ip_address)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        now_wib.strftime("%Y-%m-%d %H:%M:%S"),
        st.session_state.get("user_info", {}).get("username", "system"),
        action,
        module,
        detail or "",
        "localhost"
    ))
    conn.commit()
    conn.close()

def load_audit_trail(limit=100):
    """Load audit trail"""
    conn = sqlite3.connect(DB_NAME)
    query = f"SELECT * FROM audit_trail ORDER BY timestamp DESC LIMIT {limit}"
    df = pd.read_sql(query, conn)
    conn.close()
    return df

# ==================== DATABASE OPERATIONS ====================

def get_user_by_username(username):
    """Get user by username"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE username = ? AND is_active = 1", (username,))
    user = c.fetchone()
    conn.close()
    
    if user:
        return {
            'user_id': user[0],
            'username': user[1],
            'password_hash': user[2],
            'full_name': user[3],
            'email': user[4],
            'role': user[5],
            'is_active': user[6],
            'created_at': user[7],
            'last_login': user[8]
        }
    return None

def update_last_login(username):
    """Update last login timestamp"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    now = datetime.now(WIB).strftime('%Y-%m-%d %H:%M:%S')
    c.execute("UPDATE users SET last_login = ? WHERE username = ?", (now, username))
    conn.commit()
    conn.close()

def get_all_projects():
    """Get all projects"""
    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql("SELECT * FROM projects ORDER BY created_at DESC", conn)
    conn.close()
    return df

def get_project_by_id(project_id):
    """Get project by ID"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT * FROM projects WHERE project_id = ?", (project_id,))
    project = c.fetchone()
    conn.close()
    return project

def create_default_budget_categories(project_id):
    """Create default 5 budget categories for a new project"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    now = datetime.now(WIB).strftime('%Y-%m-%d %H:%M:%S')
    
    for category_name in WBS_CATEGORIES:
        # BIAYA ADMIN excluded from project budget calculation
        is_excluded = 1 if category_name == "BIAYA ADMIN" else 0
        
        c.execute('''
            INSERT INTO budget_categories (
                project_id, category_name, budget_amount, actual_amount, 
                is_excluded_from_project, created_at
            ) VALUES (?, ?, 0, 0, ?, ?)
        ''', (project_id, category_name, is_excluded, now))
    
    conn.commit()
    conn.close()

def get_budget_categories(project_id):
    """Get all budget categories for a project"""
    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql(
        "SELECT * FROM budget_categories WHERE project_id = ? ORDER BY category_id", 
        conn, 
        params=(project_id,)
    )
    conn.close()
    return df

def get_cost_items_by_category(category_id):
    """Get all cost items for a category"""
    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql(
        "SELECT * FROM cost_items WHERE category_id = ? ORDER BY date DESC", 
        conn, 
        params=(category_id,)
    )
    conn.close()
    return df

def get_budget_estimation_items(category_id):
    """Get only budget estimation items (for linking)"""
    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql(
        "SELECT * FROM cost_items WHERE category_id = ? AND is_budget_estimation = 1 ORDER BY date DESC", 
        conn, 
        params=(category_id,)
    )
    conn.close()
    return df

def get_actual_spending_by_category(category_id):
    """Get all actual spending for a category"""
    conn = sqlite3.connect(DB_NAME)
    query = """
        SELECT 
            a.*,
            c.description as budget_description,
            v.vendor_name
        FROM actual_spending a
        LEFT JOIN cost_items c ON a.budget_item_id = c.item_id
        LEFT JOIN vendors v ON a.vendor_id = v.vendor_id
        WHERE a.category_id = ?
        ORDER BY a.actual_date DESC
    """
    df = pd.read_sql(query, conn, params=(category_id,))
    conn.close()
    return df

def update_category_actual_amount(category_id):
    """Update actual amount in budget_categories based on actual_spending"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # Sum all actual spending for this category
    c.execute("""
        SELECT SUM(actual_price) 
        FROM actual_spending 
        WHERE category_id = ?
    """, (category_id,))
    
    total_actual = c.fetchone()[0] or 0
    now = datetime.now(WIB).strftime('%Y-%m-%d %H:%M:%S')
    
    c.execute("""
        UPDATE budget_categories 
        SET actual_amount = ?, updated_at = ?
        WHERE category_id = ?
    """, (total_actual, now, category_id))
    
    conn.commit()
    conn.close()

def get_vendors():
    """Get all active vendors"""
    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql("SELECT * FROM vendors WHERE is_active = 1 ORDER BY vendor_name", conn)
    conn.close()
    return df

def update_category_budget_from_items(category_id):
    """Update category budget_amount based on sum of budget estimation items"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # Sum all budget estimation items for this category
    c.execute("""
        SELECT SUM(budget_price) 
        FROM cost_items 
        WHERE category_id = ? AND is_budget_estimation = 1
    """, (category_id,))
    
    total_budget = c.fetchone()[0] or 0
    now = datetime.now(WIB).strftime('%Y-%m-%d %H:%M:%S')
    
    c.execute("""
        UPDATE budget_categories 
        SET budget_amount = ?, updated_at = ?
        WHERE category_id = ?
    """, (total_budget, now, category_id))
    
    conn.commit()
    conn.close()
    
    return total_budget

def sync_all_category_budgets(project_id):
    """Sync all category budgets from their budget estimation items and update project total"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    now = datetime.now(WIB).strftime('%Y-%m-%d %H:%M:%S')
    
    # Get all categories for this project
    c.execute("SELECT category_id FROM budget_categories WHERE project_id = ?", (project_id,))
    categories = c.fetchall()
    
    for (cat_id,) in categories:
        # Update each category budget from its items
        update_category_budget_from_items(cat_id)
    
    # Update project total budget (exclude BIAYA ADMIN)
    c.execute('''
        SELECT SUM(budget_amount) 
        FROM budget_categories 
        WHERE project_id = ? AND is_excluded_from_project = 0
    ''', (project_id,))
    
    new_total_budget = c.fetchone()[0] or 0
    
    c.execute('''
        UPDATE projects 
        SET budget_total = ?, updated_at = ?
        WHERE project_id = ?
    ''', (new_total_budget, now, project_id))
    
    conn.commit()
    conn.close()
    
    return new_total_budget


# ==================== LOGIN PAGE ====================

def login_page():
    st.set_page_config(page_title="IPCC System - Login", layout="centered", page_icon="🏗️")
    
    # Custom CSS
    st.markdown("""
    <style>
    .main {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    }
    .login-container {
        background: white;
        padding: 3rem;
        border-radius: 20px;
        box-shadow: 0 10px 40px rgba(0,0,0,0.2);
        max-width: 450px;
        margin: 5rem auto;
    }
    .login-title {
        text-align: center;
        color: #2d3436;
        font-size: 2rem;
        font-weight: 800;
        margin-bottom: 0.5rem;
    }
    .login-subtitle {
        text-align: center;
        color: #636e72;
        font-size: 1rem;
        margin-bottom: 2rem;
    }
    </style>
    """, unsafe_allow_html=True)
    
    col1, col2, col3 = st.columns([1, 2, 1])
    
    with col2:
        st.markdown('<div class="login-container">', unsafe_allow_html=True)
        
        st.markdown('<h1 class="login-title">🏗️ IPCC System</h1>', unsafe_allow_html=True)
        st.markdown('<p class="login-subtitle">Integrated Project & Cost Control</p>', unsafe_allow_html=True)
        
        username = st.text_input("👤 Username", key="login_username")
        password = st.text_input("🔒 Password", type="password", key="login_password")
        
        col_btn1, col_btn2 = st.columns(2)
        with col_btn1:
            login_btn = st.button("🚀 Login", use_container_width=True, type="primary")
        with col_btn2:
            st.button("❓ Help", use_container_width=True)
        
        if login_btn:
            if username and password:
                user = get_user_by_username(username.strip().lower())
                
                if user and user['password_hash'] == hash_password(password):
                    st.session_state["is_logged_in"] = True
                    st.session_state["user_info"] = user
                    update_last_login(username.strip().lower())
                    add_audit("login", "authentication", f"User {user['full_name']} logged in successfully")
                    st.success(f"✅ Welcome, {user['full_name']}!")
                    st.rerun()
                else:
                    st.error("❌ Invalid username or password")
            else:
                st.warning("⚠️ Please enter both username and password")
        
        st.markdown("---")
        st.markdown("""
        <div style='text-align: center; color: #636e72; font-size: 0.85rem;'>
        <b>Default Login Credentials:</b><br>
        Owner: admin / admin123<br>
        PM: pm001 / pm123<br>
        Cost Controller: cc001 / cc123<br>
        Procurement: proc001 / proc123<br>
        Engineer: eng001 / eng123
        </div>
        """, unsafe_allow_html=True)
        
        st.markdown('</div>', unsafe_allow_html=True)

# ==================== MAIN DASHBOARD ====================

def dashboard_page():
    st.title("📊 Executive Dashboard")
    
    user_role = st.session_state.get("user_info", {}).get("role", "")
    
    # Load projects data
    df_projects = get_all_projects()
    
    if df_projects.empty:
        st.info("👋 Welcome to IPCC System! No projects yet. Create your first project to get started.")
        return
    
    # Summary Cards
    col1, col2, col3, col4, col5 = st.columns(5)
    
    total_projects = len(df_projects)
    active_projects = len(df_projects[df_projects['status'].isin(['Planning', 'In Progress'])])
    completed_projects = len(df_projects[df_projects['status'] == 'Completed'])
    total_budget = df_projects['budget_total'].sum()
    
    with col1:
        st.markdown(f"""
        <div style='background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                    padding: 1.5rem; border-radius: 15px; color: white; text-align: center;'>
            <div style='font-size: 0.85rem; opacity: 0.9;'>TOTAL PROJECTS</div>
            <div style='font-size: 2.5rem; font-weight: 800;'>{total_projects}</div>
        </div>
        """, unsafe_allow_html=True)
    
    with col2:
        st.markdown(f"""
        <div style='background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); 
                    padding: 1.5rem; border-radius: 15px; color: white; text-align: center;'>
            <div style='font-size: 0.85rem; opacity: 0.9;'>ACTIVE</div>
            <div style='font-size: 2.5rem; font-weight: 800;'>{active_projects}</div>
        </div>
        """, unsafe_allow_html=True)
    
    with col3:
        st.markdown(f"""
        <div style='background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%); 
                    padding: 1.5rem; border-radius: 15px; color: white; text-align: center;'>
            <div style='font-size: 0.85rem; opacity: 0.9;'>COMPLETED</div>
            <div style='font-size: 2.5rem; font-weight: 800;'>{completed_projects}</div>
        </div>
        """, unsafe_allow_html=True)
    
    with col4:
        st.markdown(f"""
        <div style='background: linear-gradient(135deg, #43e97b 0%, #38f9d7 100%); 
                    padding: 1.5rem; border-radius: 15px; color: white; text-align: center;'>
            <div style='font-size: 0.85rem; opacity: 0.9;'>TOTAL BUDGET</div>
            <div style='font-size: 1.8rem; font-weight: 800;'>{format_currency(total_budget)}</div>
        </div>
        """, unsafe_allow_html=True)
    
    with col5:
        avg_budget = total_budget / total_projects if total_projects > 0 else 0
        st.markdown(f"""
        <div style='background: linear-gradient(135deg, #fa709a 0%, #fee140 100%); 
                    padding: 1.5rem; border-radius: 15px; color: white; text-align: center;'>
            <div style='font-size: 0.85rem; opacity: 0.9;'>AVG BUDGET</div>
            <div style='font-size: 1.8rem; font-weight: 800;'>{format_currency(avg_budget)}</div>
        </div>
        """, unsafe_allow_html=True)
    
    st.markdown("---")
    
    # Project List
    col_left, col_right = st.columns([2, 1])
    
    with col_left:
        st.subheader("📋 Active Projects")
        
        # Filter active projects
        active_df = df_projects[df_projects['status'].isin(['Planning', 'In Progress'])]
        
        if not active_df.empty:
            for _, proj in active_df.iterrows():
                with st.expander(f"**{proj['project_code']}** - {proj['project_name']}", expanded=False):
                    col_a, col_b = st.columns(2)
                    with col_a:
                        st.write(f"**Status:** {proj['status']}")
                        st.write(f"**PM:** {proj['project_manager'] or 'Not assigned'}")
                        st.write(f"**Location:** {proj['location'] or '-'}")
                    with col_b:
                        st.write(f"**Budget:** {format_currency(proj['budget_total'])}")
                        st.write(f"**Start:** {format_date(parse_date(proj['start_date']))}")
                        st.write(f"**End:** {format_date(parse_date(proj['end_date']))}")
                    
                    if st.button(f"View Details", key=f"view_{proj['project_id']}"):
                        st.session_state['selected_project_id'] = proj['project_id']
                        st.session_state['menu'] = 'Project Details'
                        st.rerun()
        else:
            st.info("No active projects")
    
    with col_right:
        st.subheader("📊 Projects by Status")
        
        status_counts = df_projects['status'].value_counts().reset_index()
        status_counts.columns = ['Status', 'Count']
        
        pie_chart = alt.Chart(status_counts).mark_arc(innerRadius=50).encode(
            theta=alt.Theta('Count:Q'),
            color=alt.Color('Status:N', 
                scale=alt.Scale(
                    domain=['Planning', 'In Progress', 'On Hold', 'Completed', 'Cancelled'],
                    range=['#4facfe', '#43e97b', '#feca57', '#667eea', '#ff6b6b']
                )
            ),
            tooltip=['Status', 'Count']
        ).properties(height=300)
        
        st.altair_chart(pie_chart, use_container_width=True)

# ==================== PROJECT MANAGEMENT ====================

def project_management_page():
    st.title("🏗️ Project Management")
    
    tab1, tab2 = st.tabs(["📋 Project List", "➕ Create New Project"])
    
    with tab1:
        df_projects = get_all_projects()
        
        if not df_projects.empty:
            # Filters
            col1, col2, col3 = st.columns(3)
            
            with col1:
                status_filter = st.multiselect(
                    "Filter by Status",
                    options=df_projects['status'].unique().tolist(),
                    default=df_projects['status'].unique().tolist()
                )
            
            with col2:
                search = st.text_input("Search project", placeholder="Project name or code...")
            
            with col3:
                sort_by = st.selectbox("Sort by", ["Created Date", "Project Name", "Budget", "Start Date"])
            
            # Apply filters
            filtered_df = df_projects[df_projects['status'].isin(status_filter)]
            
            if search:
                filtered_df = filtered_df[
                    filtered_df['project_name'].str.contains(search, case=False, na=False) |
                    filtered_df['project_code'].str.contains(search, case=False, na=False)
                ]
            
            # Display projects
            for _, proj in filtered_df.iterrows():
                with st.container():
                    st.markdown(f"""
                    <div style='background: white; padding: 1.5rem; border-radius: 15px; 
                                box-shadow: 0 2px 10px rgba(0,0,0,0.08); margin-bottom: 1rem;'>
                        <h3 style='margin: 0; color: #2d3436;'>{proj['project_code']} - {proj['project_name']}</h3>
                        <p style='color: #636e72; margin: 0.5rem 0;'>{proj['description'] or 'No description'}</p>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    col_a, col_b, col_c, col_d = st.columns(4)
                    with col_a:
                        st.metric("Status", proj['status'])
                    with col_b:
                        st.metric("Budget", format_currency(proj['budget_total']))
                    with col_c:
                        st.metric("PM", proj['project_manager'] or '-')
                    with col_d:
                        if st.button("View Details", key=f"proj_{proj['project_id']}"):
                            st.session_state['selected_project_id'] = proj['project_id']
                            st.session_state['menu'] = 'Project Details'
                            st.rerun()
                    
                    st.markdown("---")
        else:
            st.info("No projects found. Create your first project!")
    
    with tab2:
        st.subheader("Create New Project")
        
        with st.form("new_project_form"):
            col1, col2 = st.columns(2)
            
            with col1:
                project_code = st.text_input("Project Code *", placeholder="e.g., PRJ-2025-001")
                project_name = st.text_input("Project Name *", placeholder="e.g., Building Construction")
                start_date = st.date_input("Start Date *")
                budget_total = st.number_input("Total Budget (IDR) *", min_value=0.0, step=1000000.0)
                project_manager = st.text_input("Project Manager", placeholder="Name")
            
            with col2:
                client_name = st.text_input("Client Name", placeholder="Client/Owner name")
                location = st.text_input("Location", placeholder="Project location")
                end_date = st.date_input("End Date *")
                status = st.selectbox("Status", ["Planning", "In Progress", "On Hold", "Completed", "Cancelled"])
                description = st.text_area("Description", placeholder="Project description...")
            
            submit = st.form_submit_button("🚀 Create Project", use_container_width=True, type="primary")
            
            if submit:
                if not project_code or not project_name or not start_date or not end_date or budget_total <= 0:
                    st.error("Please fill all required fields!")
                else:
                    try:
                        conn = sqlite3.connect(DB_NAME)
                        c = conn.cursor()
                        
                        now = datetime.now(WIB).strftime('%Y-%m-%d %H:%M:%S')
                        
                        c.execute('''
                            INSERT INTO projects (
                                project_code, project_name, description, start_date, end_date,
                                budget_total, status, project_manager, client_name, location,
                                created_by, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            project_code,
                            project_name,
                            description,
                            start_date.strftime('%Y-%m-%d'),
                            end_date.strftime('%Y-%m-%d'),
                            budget_total,
                            status,
                            project_manager,
                            client_name,
                            location,
                            st.session_state.get("user_info", {}).get("username", "system"),
                            now,
                            now
                        ))
                        
                        conn.commit()
                        project_id = c.lastrowid
                        conn.close()
                        
                        # Auto-create 5 default budget categories
                        create_default_budget_categories(project_id)
                        
                        add_audit("create", "project", f"Created project: {project_code} - {project_name}")
                        st.success(f"✅ Project '{project_name}' created successfully with 5 budget categories!")
                        st.rerun()
                        
                    except sqlite3.IntegrityError:
                        st.error("❌ Project code already exists!")
                    except Exception as e:
                        st.error(f"❌ Error: {str(e)}")

# ==================== PROJECT DETAILS ====================

def project_details_page():
    if 'selected_project_id' not in st.session_state:
        st.warning("Please select a project first")
        return
    
    project_id = st.session_state['selected_project_id']
    project = get_project_by_id(project_id)
    
    if not project:
        st.error("Project not found")
        return
    
    # Custom CSS untuk UI yang lebih menarik
    st.markdown("""
    <style>
    .project-header {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 2rem;
        border-radius: 20px;
        color: white;
        margin-bottom: 2rem;
        box-shadow: 0 10px 30px rgba(0,0,0,0.2);
    }
    .project-title {
        font-size: 2rem;
        font-weight: 800;
        margin: 0;
        text-shadow: 2px 2px 4px rgba(0,0,0,0.2);
    }
    .project-code {
        font-size: 1.2rem;
        opacity: 0.9;
        margin-top: 0.5rem;
    }
    .metric-card {
        background: white;
        padding: 1.5rem;
        border-radius: 15px;
        box-shadow: 0 4px 15px rgba(0,0,0,0.1);
        text-align: center;
        transition: transform 0.3s ease;
        height: 100%;
    }
    .metric-card:hover {
        transform: translateY(-5px);
        box-shadow: 0 8px 25px rgba(0,0,0,0.15);
    }
    .metric-label {
        font-size: 0.85rem;
        color: #636e72;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 1px;
        margin-bottom: 0.5rem;
    }
    .metric-value {
        font-size: 1.8rem;
        font-weight: 800;
        color: #2d3436;
    }
    .info-card {
        background: white;
        padding: 1.5rem;
        border-radius: 15px;
        box-shadow: 0 2px 10px rgba(0,0,0,0.08);
        margin-bottom: 1rem;
        border-left: 4px solid #667eea;
    }
    .info-row {
        display: flex;
        justify-content: space-between;
        padding: 0.8rem 0;
        border-bottom: 1px solid #f0f0f0;
    }
    .info-row:last-child {
        border-bottom: none;
    }
    .info-label {
        color: #636e72;
        font-weight: 600;
        font-size: 0.95rem;
    }
    .info-value {
        color: #2d3436;
        font-weight: 500;
    }
    .section-header {
        background: linear-gradient(90deg, #f5f7fa 0%, #ffffff 100%);
        padding: 1rem 1.5rem;
        border-radius: 10px;
        border-left: 5px solid #667eea;
        margin: 2rem 0 1rem 0;
        font-size: 1.3rem;
        font-weight: 700;
        color: #2d3436;
    }
    .budget-category-card {
        background: white;
        padding: 1.2rem;
        border-radius: 12px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        margin-bottom: 1rem;
        border-left: 4px solid #43e97b;
        transition: all 0.3s ease;
    }
    .budget-category-card:hover {
        box-shadow: 0 4px 15px rgba(0,0,0,0.12);
        transform: translateX(5px);
    }
    .budget-summary-card {
        background: linear-gradient(135deg, #43e97b 0%, #38f9d7 100%);
        padding: 1.5rem;
        border-radius: 15px;
        color: white;
        text-align: center;
        box-shadow: 0 5px 20px rgba(67, 233, 123, 0.3);
    }
    </style>
    """, unsafe_allow_html=True)
    
    # Back button dengan style lebih baik
    col_back, col_space = st.columns([1, 11])
    with col_back:
        if st.button("⬅️ Back", key="back_btn", use_container_width=True):
            st.session_state['menu'] = 'Dashboard'
            st.rerun()
    
    # Project Header yang lebih menarik
    st.markdown(f"""
    <div class="project-header">
        <div class="project-title">📁 {project[3]}</div>
        <div class="project-code">Project Code: {project[2]}</div>
    </div>
    """, unsafe_allow_html=True)
    
    # Project Metrics Cards dengan design yang lebih baik
    col1, col2, col3, col4 = st.columns(4)
    
    # Status dengan warna dinamis
    status_colors = {
        'Planning': '#4facfe',
        'In Progress': '#43e97b',
        'On Hold': '#feca57',
        'Completed': '#667eea',
        'Cancelled': '#ff6b6b'
    }
    status_icons = {
        'Planning': '🔵',
        'In Progress': '🟢',
        'On Hold': '🟡',
        'Completed': '✅',
        'Cancelled': '🔴'
    }
    status_color = status_colors.get(project[7], '#95a5a6')
    status_icon = status_icons.get(project[7], '⚪')
    
    with col1:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-label">Project Status</div>
            <div class="metric-value" style="color: {status_color};">{status_icon} {project[7]}</div>
        </div>
        """, unsafe_allow_html=True)
    
    with col2:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-label">Total Budget</div>
            <div class="metric-value" style="color: #667eea; font-size: 1.5rem;">{format_currency(project[6])}</div>
        </div>
        """, unsafe_allow_html=True)
    
    with col3:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-label">📅 Start Date</div>
            <div class="metric-value" style="color: #43e97b; font-size: 1.3rem;">{format_date(parse_date(project[4]))}</div>
        </div>
        """, unsafe_allow_html=True)
    
    with col4:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-label">🏁 End Date</div>
            <div class="metric-value" style="color: #f5576c; font-size: 1.3rem;">{format_date(parse_date(project[5]))}</div>
        </div>
        """, unsafe_allow_html=True)
    
    st.markdown("<br>", unsafe_allow_html=True)
    
    # Tabs dengan icon yang lebih jelas - HANYA 3 TABS
    tab1, tab2, tab3 = st.tabs([
        "📊 Overview & Summary",
        "📝 Cost Items & Spending",
        "📈 Progress Tracking"
    ])
    
    with tab1:
        st.markdown('<div class="section-header">📋 Project Information</div>', unsafe_allow_html=True)
        
        # AUTO-SYNC BUDGET saat buka tab Overview
        if 'overview_budget_synced' not in st.session_state:
            with st.spinner("🔄 Syncing budgets from estimation items..."):
                sync_all_category_budgets(project_id)
                st.session_state['overview_budget_synced'] = True
        
        col_a, col_b = st.columns(2)
        
        with col_a:
            st.markdown(f"""
            <div class="info-card">
                <div class="info-row">
                    <span class="info-label">🏷️ Project Code</span>
                    <span class="info-value">{project[1]}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">📁 Project Name</span>
                    <span class="info-value">{project[2]}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">📝 Description</span>
                    <span class="info-value">{project[3] or '-'}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">👤 Client</span>
                    <span class="info-value">{project[9] or '-'}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">📍 Location</span>
                    <span class="info-value">{project[10] or '-'}</span>
                </div>
            </div>
            """, unsafe_allow_html=True)
        
        with col_b:
            st.markdown(f"""
            <div class="info-card">
                <div class="info-row">
                    <span class="info-label">👨‍💼 Project Manager</span>
                    <span class="info-value">{project[8] or '-'}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">📊 Status</span>
                    <span class="info-value" style="color: {status_color}; font-weight: 700;">{status_icon} {project[7]}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">💰 Total Budget</span>
                    <span class="info-value" style="color: #667eea; font-weight: 700;">{format_currency(project[6])}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">👤 Created By</span>
                    <span class="info-value">{project[11] or '-'}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">🕐 Created At</span>
                    <span class="info-value">{project[12]}</span>
                </div>
            </div>
            """, unsafe_allow_html=True)    
        
    with tab2:
        st.markdown('<div class="section-header">📝 Budget Estimation & Actual Spending</div>', unsafe_allow_html=True)
        
        df_categories = get_budget_categories(project_id)
        
        if df_categories.empty:
            st.warning("Please set up budget categories first")
        else:
            # Select category dengan style yang lebih baik
            st.markdown("#### 📂 Select Budget Category")
            category_names = df_categories['category_name'].tolist()
            selected_category_name = st.selectbox(
                "Choose a category to manage costs",
                category_names, 
                key="cat_select_tab3",
                label_visibility="collapsed"
            )
            
            selected_category = df_categories[df_categories['category_name'] == selected_category_name].iloc[0]
            category_id = selected_category['category_id']
            
            st.markdown("<br>", unsafe_allow_html=True)
            
            # GET FRESH DATA dari database untuk summary cards
            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()
            
            # Get budget from estimation items
            c.execute("""
                SELECT SUM(budget_price) 
                FROM cost_items 
                WHERE category_id = ? AND is_budget_estimation = 1
            """, (category_id,))
            category_budget_from_items = c.fetchone()[0] or 0
            
            # Get FRESH actual amount from actual_spending table
            c.execute("""
                SELECT SUM(actual_price) 
                FROM actual_spending 
                WHERE category_id = ?
            """, (category_id,))
            category_actual_amount = c.fetchone()[0] or 0
            
            conn.close()
            
            # Summary cards dengan design yang lebih baik
            variance = category_budget_from_items - category_actual_amount
            variance_pct = (variance / category_budget_from_items * 100) if category_budget_from_items > 0 else 0
            utilization = (category_actual_amount / category_budget_from_items * 100) if category_budget_from_items > 0 else 0
            
            col_summary1, col_summary2, col_summary3, col_summary4 = st.columns(4)
            
            with col_summary1:
                st.markdown(f"""
                <div class="metric-card" style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white;">
                    <div style="font-size: 0.85rem; opacity: 0.9; margin-bottom: 0.5rem;">💰 CATEGORY BUDGET</div>
                    <div style="font-size: 1.6rem; font-weight: 800; color: white;">{format_currency(category_budget_from_items)}</div>
                </div>
                """, unsafe_allow_html=True)
            
            with col_summary2:
                st.markdown(f"""
                <div class="metric-card" style="background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); color: white;">
                    <div style="font-size: 0.85rem; opacity: 0.9; margin-bottom: 0.5rem;">💸 TOTAL ACTUAL</div>
                    <div style="font-size: 1.6rem; font-weight: 800; color: white;">{format_currency(category_actual_amount)}</div>
                </div>
                """, unsafe_allow_html=True)
            
            with col_summary3:
                variance_color = "#43e97b" if variance >= 0 else "#ff6b6b"
                variance_icon = "🟢" if variance >= 0 else "🔴"
                st.markdown(f"""
                <div class="metric-card" style="background: {variance_color}; color: white;">
                    <div style="font-size: 0.85rem; opacity: 0.9; margin-bottom: 0.5rem;">{variance_icon} REMAINING</div>
                    <div style="font-size: 1.4rem; font-weight: 800; color: white;">{format_currency(variance)}</div>
                    <div style="font-size: 0.9rem; opacity: 0.9;">{variance_pct:.1f}%</div>
                </div>
                """, unsafe_allow_html=True)
            
            with col_summary4:
                util_color = "#43e97b" if utilization <= 90 else ("#feca57" if utilization <= 100 else "#ff6b6b")
                util_icon = "🟢" if utilization <= 90 else ("🟡" if utilization <= 100 else "🔴")
                st.markdown(f"""
                <div class="metric-card" style="background: {util_color}; color: white;">
                    <div style="font-size: 0.85rem; opacity: 0.9; margin-bottom: 0.5rem;">{util_icon} UTILIZATION</div>
                    <div style="font-size: 1.8rem; font-weight: 800; color: white;">{utilization:.1f}%</div>
                </div>
                """, unsafe_allow_html=True)
            
            # Progress bar
            st.markdown(f"""
            <div style="margin: 1.5rem 0;">
                <div style="display: flex; justify-content: space-between; margin-bottom: 0.5rem;">
                    <span style="font-weight: 600; color: #2d3436;">Budget Utilization Progress</span>
                    <span style="font-weight: 700; color: {util_color};">{utilization:.1f}%</span>
                </div>
                <div style="background: #e9ecef; border-radius: 10px; height: 20px; overflow: hidden;">
                    <div style="background: linear-gradient(90deg, {util_color} 0%, {util_color} 100%); 
                                height: 100%; width: {min(utilization, 100):.1f}%; 
                                transition: width 0.5s ease;
                                display: flex; align-items: center; justify-content: flex-end; padding-right: 0.5rem;">
                        <span style="color: white; font-size: 0.75rem; font-weight: 700;">{min(utilization, 100):.1f}%</span>
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)
            
            st.markdown("<br>", unsafe_allow_html=True)
            
            # Two tabs: Budget Estimation & Actual Spending
            subtab1, subtab2 = st.tabs(["💰 Budget Estimation Planning", "💸 Actual Spending Records"])
            
            with subtab1:
                st.markdown("#### 📋 Budget Estimation Items")
                
                df_budget_items = get_budget_estimation_items(category_id)
                
                if not df_budget_items.empty:
                    # Display budget items dengan table yang lebih baik
                    st.markdown("""
                    <div style="background: white; padding: 1rem; border-radius: 12px; 
                                box-shadow: 0 2px 10px rgba(0,0,0,0.08); margin-bottom: 1rem;">
                    """, unsafe_allow_html=True)
                    
                    display_df = df_budget_items[['date', 'description', 'unit', 'budget_price', 'notes']].copy()
                    display_df['date'] = pd.to_datetime(display_df['date']).dt.strftime('%d-%m-%Y')
                    display_df['budget_price'] = display_df['budget_price'].apply(lambda x: format_currency(x))
                    display_df.columns = ['📅 Date', '📝 Description', '📦 Unit', '💰 Budget Price', '📄 Notes']
                    
                    st.dataframe(display_df, use_container_width=True, hide_index=True)
                    
                    st.markdown("</div>", unsafe_allow_html=True)
                    
                    total_budget_items = df_budget_items['budget_price'].sum()
                    st.markdown(f"""
                    <div style="background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%); 
                                padding: 1rem; border-radius: 10px; color: white; text-align: center;">
                        <strong>📊 Total Budget Estimation Items: {format_currency(total_budget_items)}</strong>
                    </div>
                    """, unsafe_allow_html=True)
                else:
                    st.info("📝 No budget estimation items yet. Add your first budget estimation below.")
                
                st.markdown("<br>", unsafe_allow_html=True)
                
                # Add budget estimation item dengan design yang lebih baik
                with st.expander("➕ Add New Budget Estimation Item", expanded=False):
                    with st.form("add_budget_item_form"):
                        st.markdown("""
                        <div style="background: #e3f2fd; padding: 1rem; border-radius: 8px; border-left: 4px solid #2196f3; margin-bottom: 1rem;">
                            <strong>💡 Tips:</strong> Budget Estimation adalah rencana biaya yang diestimasi di awal project untuk perencanaan budget yang lebih akurat.
                        </div>
                        """, unsafe_allow_html=True)
                        
                        col1, col2 = st.columns(2)
                        
                        with col1:
                            budget_date = st.date_input("📅 Planned Date *", value=datetime.now().date())
                            budget_desc = st.text_input("📝 Description *", placeholder="e.g., Semen Portland 50kg")
                            budget_unit = st.text_input("📦 Unit", placeholder="e.g., sack, m3, pcs")
                        
                        with col2:
                            budget_price = st.number_input("💰 Budget Price (IDR) *", min_value=0.0, step=100000.0, format="%.0f")
                            budget_notes = st.text_area("📄 Notes", placeholder="Additional budget estimation details...", height=100)
                        
                        col_btn1, col_btn2 = st.columns([3, 1])
                        with col_btn1:
                            submit_budget = st.form_submit_button("💾 Add Budget Estimation Item", type="primary", use_container_width=True)
                        with col_btn2:
                            st.form_submit_button("🔄 Clear", use_container_width=True)
                        
                        if submit_budget:
                            if budget_desc and budget_price > 0:
                                try:
                                    conn = sqlite3.connect(DB_NAME)
                                    c = conn.cursor()
                                    now = datetime.now(WIB).strftime('%Y-%m-%d %H:%M:%S')
                                    
                                    c.execute('''
                                        INSERT INTO cost_items (
                                            category_id, date, description, unit, budget_price,
                                            is_budget_estimation, notes, created_by, created_at
                                        ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
                                    ''', (
                                        category_id, budget_date.strftime('%Y-%m-%d'), budget_desc,
                                        budget_unit, budget_price, budget_notes,
                                        st.session_state.get("user_info", {}).get("username", "system"),
                                        now
                                    ))
                                    
                                    conn.commit()
                                    conn.close()
                                    
                                    # Auto-update category budget from items
                                    update_category_budget_from_items(category_id)
                                    
                                    # Update project total budget
                                    sync_all_category_budgets(project_id)
                                    
                                    add_audit("create", "budget_item", f"Added budget item: {budget_desc} - Budget auto-updated")
                                    st.success(f"✅ Budget estimation item added! Category budget auto-updated.")
                                    st.rerun()
                                    
                                except Exception as e:
                                    st.error(f"❌ Error: {str(e)}")
                            else:
                                st.error("Please fill required fields!")
            
            with subtab2:
                st.markdown("#### 💸 Actual Spending Records")
                
                df_actual = get_actual_spending_by_category(category_id)
                
                if not df_actual.empty:
                    # Display actual spending dengan card design yang lebih baik
                    for idx, actual in df_actual.iterrows():
                        is_planned = actual['is_planned'] == 1
                        budget_ref = actual['budget_description'] if actual['budget_description'] else "-"
                        vendor_name = actual['vendor_name'] if actual['vendor_name'] else "No Vendor"
                        
                        # Color coding untuk status
                        if is_planned:
                            border_color = "#43e97b"
                            status_badge = '<span style="background: #43e97b; color: white; padding: 0.2rem 0.6rem; border-radius: 6px; font-size: 0.75rem; font-weight: 600;">🔗 PLANNED</span>'
                        else:
                            border_color = "#feca57"
                            status_badge = '<span style="background: #feca57; color: white; padding: 0.2rem 0.6rem; border-radius: 6px; font-size: 0.75rem; font-weight: 600;">⚠️ UNPLANNED</span>'
                        
                        payment_colors = {
                            'Paid': '#43e97b',
                            'Pending': '#feca57',
                            'Partial': '#4facfe'
                        }
                        payment_color = payment_colors.get(actual['payment_status'], '#95a5a6')
                        
                        st.markdown(f"""
                        <div style="background: white; padding: 1.2rem; border-radius: 12px; 
                                    border-left: 5px solid {border_color}; margin-bottom: 1rem;
                                    box-shadow: 0 2px 8px rgba(0,0,0,0.08);">
                            <div style="display: flex; justify-content: space-between; align-items: start; margin-bottom: 0.8rem;">
                                <div>
                                    <h4 style="margin: 0; color: #2d3436;">💸 {actual['description']}</h4>
                                    {f'<p style="margin: 0.3rem 0; color: #636e72; font-size: 0.9rem;">📋 Reference: {budget_ref}</p>' if budget_ref != "-" else ''}
                                </div>
                                <div style="text-align: right;">
                                    {status_badge}
                                </div>
                            </div>
                            <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem; margin-top: 1rem;">
                                <div>
                                    <div style="font-size: 0.75rem; color: #636e72; font-weight: 600;">📅 DATE</div>
                                    <div style="font-size: 0.95rem; color: #2d3436; font-weight: 600;">{pd.to_datetime(actual['actual_date']).strftime('%d-%m-%Y')}</div>
                                </div>
                                <div>
                                    <div style="font-size: 0.75rem; color: #636e72; font-weight: 600;">💰 AMOUNT</div>
                                    <div style="font-size: 1.1rem; color: #f5576c; font-weight: 700;">{format_currency(actual['actual_price'])}</div>
                                </div>
                                <div>
                                    <div style="font-size: 0.75rem; color: #636e72; font-weight: 600;">🏢 VENDOR</div>
                                    <div style="font-size: 0.95rem; color: #2d3436; font-weight: 600;">{vendor_name}</div>
                                </div>
                                <div>
                                    <div style="font-size: 0.75rem; color: #636e72; font-weight: 600;">📊 PAYMENT</div>
                                    <div style="font-size: 0.95rem; color: {payment_color}; font-weight: 700;">{actual['payment_status']}</div>
                                </div>
                            </div>
                        </div>
                        """, unsafe_allow_html=True)
                    
                    # Summary cards
                    total_actual_spending = df_actual['actual_price'].sum()
                    planned_spending = df_actual[df_actual['is_planned'] == 1]['actual_price'].sum()
                    unplanned_spending = df_actual[df_actual['is_planned'] == 0]['actual_price'].sum()
                    
                    st.markdown("<br>", unsafe_allow_html=True)
                    col1, col2, col3 = st.columns(3)
                    
                    with col1:
                        st.markdown(f"""
                        <div class="budget-summary-card" style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);">
                            <div style="font-size: 0.9rem; opacity: 0.9; margin-bottom: 0.5rem;">💸 TOTAL ACTUAL</div>
                            <div style="font-size: 1.8rem; font-weight: 800;">{format_currency(total_actual_spending)}</div>
                        </div>
                        """, unsafe_allow_html=True)
                    
                    with col2:
                        st.markdown(f"""
                        <div class="budget-summary-card" style="background: linear-gradient(135deg, #43e97b 0%, #38f9d7 100%);">
                            <div style="font-size: 0.9rem; opacity: 0.9; margin-bottom: 0.5rem;">🔗 PLANNED</div>
                            <div style="font-size: 1.8rem; font-weight: 800;">{format_currency(planned_spending)}</div>
                        </div>
                        """, unsafe_allow_html=True)
                    
                    with col3:
                        st.markdown(f"""
                        <div class="budget-summary-card" style="background: linear-gradient(135deg, #feca57 0%, #f5576c 100%);">
                            <div style="font-size: 0.9rem; opacity: 0.9; margin-bottom: 0.5rem;">⚠️ UNPLANNED</div>
                            <div style="font-size: 1.8rem; font-weight: 800;">{format_currency(unplanned_spending)}</div>
                        </div>
                        """, unsafe_allow_html=True)
                else:
                    st.info("💸 No actual spending recorded yet. Add your first spending record below.")
                
                st.markdown("<br>", unsafe_allow_html=True)
                
                # Add actual spending dengan design yang lebih baik
                with st.expander("➕ Add New Actual Spending", expanded=False):
                    with st.form("add_actual_form"):
                        st.markdown("""
                        <div style="background: #fff3cd; padding: 1rem; border-radius: 8px; border-left: 4px solid #ffc107; margin-bottom: 1rem;">
                            <strong>💡 Tips:</strong> Input pengeluaran aktual dan link ke budget estimation jika ada. Unplanned spending akan ditandai secara khusus untuk monitoring.
                        </div>
                        """, unsafe_allow_html=True)
                        
                        col1, col2 = st.columns(2)
                        
                        with col1:
                            actual_date = st.date_input("📅 Actual Date *", value=datetime.now().date())
                            actual_desc = st.text_input("📝 Description *", placeholder="e.g., Pembelian Semen Portland")
                            actual_unit = st.text_input("📦 Unit", placeholder="e.g., sack, m3, pcs")
                            actual_price = st.number_input("💰 Actual Price (IDR) *", min_value=0.0, step=100000.0, format="%.0f")
                        
                        with col2:
                            # Link to budget estimation
                            df_budget_items = get_budget_estimation_items(category_id)
                            
                            link_options = ["⚠️ Unplanned (Not in budget estimation)"]
                            budget_item_map = {}
                            
                            if not df_budget_items.empty:
                                for _, item in df_budget_items.iterrows():
                                    option_text = f"🔗 {item['description']} ({format_currency(item['budget_price'])})"
                                    link_options.append(option_text)
                                    budget_item_map[option_text] = item['item_id']
                            
                            link_to_budget = st.selectbox(
                                "🔗 Link to Budget Estimation *",
                                options=link_options,
                                help="Pilih budget estimation item jika pengeluaran ini sudah direncanakan"
                            )
                            
                            # Vendor selection
                            df_vendors = get_vendors()
                            vendor_options = ["- No Vendor -"]
                            vendor_map = {}
                            
                            if not df_vendors.empty:
                                for _, vendor in df_vendors.iterrows():
                                    vendor_text = f"{vendor['vendor_code']} - {vendor['vendor_name']}"
                                    vendor_options.append(vendor_text)
                                    vendor_map[vendor_text] = vendor['vendor_id']
                            
                            vendor_select = st.selectbox("🏢 Vendor", options=vendor_options)
                            
                            invoice_number = st.text_input("📄 Invoice Number", placeholder="INV-2025-001")
                            payment_status = st.selectbox("💳 Payment Status", ["Pending", "Paid", "Partial"])
                            actual_notes = st.text_area("📝 Notes", placeholder="Additional information about this spending...", height=80)
                        
                        col_btn1, col_btn2 = st.columns([3, 1])
                        with col_btn1:
                            submit_actual = st.form_submit_button("💾 Add Actual Spending Record", type="primary", use_container_width=True)
                        with col_btn2:
                            st.form_submit_button("🔄 Clear", use_container_width=True)
                        
                        if submit_actual:
                            if actual_desc and actual_price > 0:
                                try:
                                    conn = sqlite3.connect(DB_NAME)
                                    c = conn.cursor()
                                    now = datetime.now(WIB).strftime('%Y-%m-%d %H:%M:%S')
                                    
                                    # Determine if planned or unplanned
                                    is_planned = 0 if link_to_budget == "⚠️ Unplanned (Not in budget estimation)" else 1
                                    budget_item_id = budget_item_map.get(link_to_budget, None) if is_planned else None
                                    vendor_id = vendor_map.get(vendor_select, None) if vendor_select != "- No Vendor -" else None
                                    
                                    c.execute('''
                                        INSERT INTO actual_spending (
                                            budget_item_id, category_id, vendor_id, actual_date, description,
                                            unit, actual_price, invoice_number, payment_status, is_planned,
                                            notes, created_by, created_at
                                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                    ''', (
                                        budget_item_id, category_id, vendor_id, actual_date.strftime('%Y-%m-%d'),
                                        actual_desc, actual_unit, actual_price, invoice_number, payment_status,
                                        is_planned, actual_notes,
                                        st.session_state.get("user_info", {}).get("username", "system"),
                                        now
                                    ))
                                    
                                    conn.commit()
                                    conn.close()
                                    
                                    # Update category actual amount
                                    update_category_actual_amount(category_id)
                                    
                                    add_audit("create", "actual_spending", f"Added actual spending: {actual_desc} - {format_currency(actual_price)}")
                                    st.success("✅ Actual spending recorded!")
                                    st.rerun()
                                    
                                except Exception as e:
                                    st.error(f"❌ Error: {str(e)}")
                            else:
                                st.error("Please fill required fields!")
    
    with tab3:
        st.markdown('<div class="section-header">📈 Progress Tracking & Monitoring</div>', unsafe_allow_html=True)
        
        st.markdown("""
        <div style="background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%); 
                    padding: 2rem; border-radius: 15px; color: white; text-align: center; margin: 2rem 0;">
            <h2 style="margin: 0;">🚧 Coming Soon!</h2>
            <p style="margin: 1rem 0 0 0; font-size: 1.1rem; opacity: 0.9;">
                Progress tracking features will be available in the next update.<br>
                Track project milestones, completion percentage, and performance metrics.
            </p>
        </div>
        """, unsafe_allow_html=True)
        
        # Preview features
        col1, col2, col3 = st.columns(3)
        
        with col1:
            st.markdown("""
            <div class="info-card">
                <h4 style="color: #667eea; margin: 0;">📊 Milestone Tracking</h4>
                <p style="color: #636e72; margin-top: 0.5rem;">Monitor project milestones and key deliverables</p>
            </div>
            """, unsafe_allow_html=True)
        
        with col2:
            st.markdown("""
            <div class="info-card">
                <h4 style="color: #43e97b; margin: 0;">📈 Performance Metrics</h4>
                <p style="color: #636e72; margin-top: 0.5rem;">Track CPI, SPI, and earned value analysis</p>
            </div>
            """, unsafe_allow_html=True)
        
        with col3:
            st.markdown("""
            <div class="info-card">
                <h4 style="color: #f5576c; margin: 0;">📸 Evidence Upload</h4>
                <p style="color: #636e72; margin-top: 0.5rem;">Upload photos and documents as progress evidence</p>
            </div>
            """, unsafe_allow_html=True)


# ==================== COST CONTROL ====================

def cost_control_page():
    st.title("💰 Cost Control")
    st.info("Advanced cost control features will be implemented here")

# ==================== VENDOR MANAGEMENT ====================

def vendor_management_page():
    st.title("🏢 Vendor Management")
    
    tab1, tab2 = st.tabs(["📋 Vendor List", "➕ Add New Vendor"])
    
    with tab1:
        df_vendors = get_vendors()
        
        if not df_vendors.empty:
            st.dataframe(
                df_vendors[['vendor_code', 'vendor_name', 'contact_person', 'phone', 'email', 'vendor_type']],
                use_container_width=True
            )
        else:
            st.info("No vendors registered yet")
    
    with tab2:
        with st.form("add_vendor_form"):
            col1, col2 = st.columns(2)
            
            with col1:
                vendor_code = st.text_input("Vendor Code *", placeholder="e.g., VND-001")
                vendor_name = st.text_input("Vendor Name *")
                contact_person = st.text_input("Contact Person")
                phone = st.text_input("Phone")
            
            with col2:
                email = st.text_input("Email")
                vendor_type = st.selectbox("Vendor Type", ["Supplier", "Contractor", "Subcontractor", "Consultant"])
                rating = st.slider("Rating", 1.0, 5.0, 3.0, 0.5)
                address = st.text_area("Address")
            
            submit = st.form_submit_button("Add Vendor", type="primary", use_container_width=True)
            
            if submit:
                if vendor_code and vendor_name:
                    try:
                        conn = sqlite3.connect(DB_NAME)
                        c = conn.cursor()
                        
                        now = datetime.now(WIB).strftime('%Y-%m-%d %H:%M:%S')
                        
                        c.execute('''
                            INSERT INTO vendors (
                                vendor_code, vendor_name, contact_person, phone, email,
                                address, vendor_type, rating, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            vendor_code, vendor_name, contact_person, phone, email,
                            address, vendor_type, rating, now
                        ))
                        
                        conn.commit()
                        conn.close()
                        
                        add_audit("create", "vendor", f"Added vendor: {vendor_code} - {vendor_name}")
                        st.success("✅ Vendor added successfully!")
                        st.rerun()
                        
                    except sqlite3.IntegrityError:
                        st.error("❌ Vendor code already exists!")
                    except Exception as e:
                        st.error(f"❌ Error: {str(e)}")
                else:
                    st.error("Please fill required fields!")

# ==================== REPORTING ====================

def reporting_page():
    st.title("📊 Reports & Analytics")
    st.info("Comprehensive reporting features coming soon...")

# ==================== SETTINGS ====================

def settings_page():
    st.title("⚙️ Settings")
    
    user_info = st.session_state.get("user_info", {})
    
    tab1, tab2 = st.tabs(["👤 Profile", "🔐 Change Password"])
    
    with tab1:
        st.subheader("User Profile")
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.write(f"**Username:** {user_info.get('username', '-')}")
            st.write(f"**Full Name:** {user_info.get('full_name', '-')}")
            st.write(f"**Email:** {user_info.get('email', '-')}")
        
        with col2:
            st.write(f"**Role:** {user_info.get('role', '-')}")
            st.write(f"**Last Login:** {user_info.get('last_login', '-')}")
            st.write(f"**Account Created:** {user_info.get('created_at', '-')}")
    
    with tab2:
        st.subheader("Change Password")
        
        with st.form("change_password_form"):
            current_password = st.text_input("Current Password", type="password")
            new_password = st.text_input("New Password", type="password")
            confirm_password = st.text_input("Confirm New Password", type="password")
            
            submit = st.form_submit_button("Change Password", type="primary")
            
            if submit:
                if new_password != confirm_password:
                    st.error("❌ New passwords don't match!")
                elif hash_password(current_password) != user_info.get('password_hash'):
                    st.error("❌ Current password is incorrect!")
                else:
                    try:
                        conn = sqlite3.connect(DB_NAME)
                        c = conn.cursor()
                        
                        c.execute(
                            "UPDATE users SET password_hash = ? WHERE username = ?",
                            (hash_password(new_password), user_info.get('username'))
                        )
                        
                        conn.commit()
                        conn.close()
                        
                        add_audit("update", "user", "Password changed")
                        st.success("✅ Password changed successfully!")
                        
                    except Exception as e:
                        st.error(f"❌ Error: {str(e)}")

# ==================== MAIN APP ====================

def main():
    # Initialize database
    init_db()
    
    # Run database migration to ensure schema is up to date
    migrate_db()
    
    # Check login
    if "is_logged_in" not in st.session_state or not st.session_state["is_logged_in"]:
        login_page()
        return
    
    # Set page config
    st.set_page_config(
        page_title="IPCC System",
        layout="wide",
        page_icon="🏗️",
        initial_sidebar_state="expanded"
    )
    
    # Custom CSS
    st.markdown("""
    <style>
    .main {
        background-color: #f5f7fa;
    }
    .stButton > button {
        border-radius: 10px;
        transition: all 0.3s ease;
    }
    .stButton > button:hover {
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(0,0,0,0.15);
    }
    </style>
    """, unsafe_allow_html=True)
    
    # Initialize menu
    if "menu" not in st.session_state:
        st.session_state["menu"] = "Dashboard"
    
    # Sidebar
    user_info = st.session_state.get("user_info", {})
    
    st.sidebar.markdown(f"""
    <div style='background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                padding: 1.5rem; border-radius: 15px; color: white; margin-bottom: 1.5rem;'>
        <h3 style='margin: 0;'>👤 {user_info.get('full_name', 'User')}</h3>
        <p style='margin: 0.5rem 0 0 0; opacity: 0.9;'>🎯 {user_info.get('role', 'Role')}</p>
    </div>
    """, unsafe_allow_html=True)
    
    st.sidebar.markdown("### 📋 Navigation")
    
    # Menu items based on role
    menu_items = {
        "Owner": [
            ("Dashboard", "📊"),
            ("Project Management", "🏗️"),
            ("Cost Control", "💰"),
            ("Vendor Management", "🏢"),
            ("Reports", "📊"),
            ("Settings", "⚙️"),
        ],
        "Project Manager": [
            ("Dashboard", "📊"),
            ("Project Management", "🏗️"),
            ("Cost Control", "💰"),
            ("Reports", "📊"),
            ("Settings", "⚙️"),
        ],
        "Cost Controller": [
            ("Dashboard", "📊"),
            ("Cost Control", "💰"),
            ("Reports", "📊"),
            ("Settings", "⚙️"),
        ],
        "Procurement": [
            ("Dashboard", "📊"),
            ("Vendor Management", "🏢"),
            ("Reports", "📊"),
            ("Settings", "⚙️"),
        ],
        "Engineer": [
            ("Dashboard", "📊"),
            ("Project Management", "🏗️"),
            ("Settings", "⚙️"),
        ],
    }
    
    role = user_info.get('role', 'Engineer')
    menus = menu_items.get(role, menu_items["Engineer"])
    
    for menu_name, icon in menus:
        if st.sidebar.button(f"{icon} {menu_name}", key=f"menu_{menu_name}", use_container_width=True):
            st.session_state["menu"] = menu_name
            st.rerun()
    
    st.sidebar.markdown("---")
    
    if st.sidebar.button("🚪 Logout", use_container_width=True, type="primary"):
        add_audit("logout", "authentication", f"User logged out")
        st.session_state.clear()
        st.rerun()
    
    # Page routing
    menu = st.session_state["menu"]
    
    if menu == "Dashboard":
        dashboard_page()
    elif menu == "Project Management":
        project_management_page()
    elif menu == "Project Details":
        project_details_page()
    elif menu == "Cost Control":
        cost_control_page()
    elif menu == "Vendor Management":
        vendor_management_page()
    elif menu == "Reports":
        reporting_page()
    elif menu == "Settings":
        settings_page()

if __name__ == "__main__":
    main()
