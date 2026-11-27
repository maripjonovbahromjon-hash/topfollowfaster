import os
import json
import sqlite3
import time
import urllib.parse
import urllib.request
from functools import wraps
from flask import Flask, request, session, redirect, url_for, render_template_string, flash
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "topfollowfaster_super_secret_key_2026")

# ================= SOZLAMALAR =================
CONFIG = {
    "SITE_NAME": "TopFollowFaster",
    "ADVERTISER_PRICE": 70,              # Reklamachi 1 obunachi uchun to'laydi (so'm)
    "WORKER_REWARD": 50,                 # Bajaruvchi 1 obuna uchun oladi (so'm)
    "REFERRAL_BONUS": 500,               # Referal ilk vazifa bajarganda bonus (so'm)
    "MIN_DEPOSIT": 1000,                 # Minimal deposit (so'm)
    "MIN_WITHDRAW": 20000,               # Minimal pul chiqarish (so'm)
    "CARD_NUMBER": "9860 1701 1301 9658",  # Deposit karta raqami
    "CARD_HOLDER": "ADMIN KARTASI",        # Karta egasi
    "ADMIN_USER": "admin",               # Admin logini (oddiy kirish uchun)
    "ADMIN_PASS": "admin123",            # Admin paroli
    "ADMIN_EMAIL": "maripjonovbahromjon@gmail.com",  # Faqat shu Google email admin panelni ko'radi
    "GOOGLE_CLIENT_ID": os.environ.get("GOOGLE_CLIENT_ID", ""),
    "GOOGLE_CLIENT_SECRET": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
    "DB_NAME": "topfollowfaster.db"
}

PLATFORMS = [
    ("instagram", "Instagram (Obuna)"),
    ("youtube", "YouTube (Obuna)"),
    ("telegram_channel", "Telegram Kanal (Obuna)"),
    ("telegram_group", "Telegram Guruh (Qo'shilish)"),
]
PLATFORM_DICT = dict(PLATFORMS)

# ================= MA'LUMOTLAR BAZASI =================
def get_db():
    conn = sqlite3.connect(CONFIG["DB_NAME"])
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE,
            google_id TEXT DEFAULT '',
            password_hash TEXT NOT NULL,
            referrer_id INTEGER DEFAULT NULL,
            ref_bonus_paid INTEGER DEFAULT 0,
            ig_nick TEXT DEFAULT '',
            yt_nick TEXT DEFAULT '',
            tg_nick TEXT DEFAULT '',
            work_balance INTEGER DEFAULT 0,
            ad_balance INTEGER DEFAULT 0,
            is_admin INTEGER DEFAULT 0,
            is_banned INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL,
            platform TEXT NOT NULL,
            title TEXT NOT NULL,
            link TEXT NOT NULL,
            target_count INTEGER NOT NULL,
            completed_count INTEGER DEFAULT 0,
            price_per_action INTEGER NOT NULL,
            reward_per_action INTEGER NOT NULL,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS completions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            task_id INTEGER NOT NULL,
            reward INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, task_id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            tx_type TEXT NOT NULL,
            amount INTEGER NOT NULL,
            details TEXT NOT NULL,
            admin_receipt TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    user_cols = [row[1] for row in c.execute("PRAGMA table_info(users)").fetchall()]
    for col, ddl in [
        ("email", "ALTER TABLE users ADD COLUMN email TEXT DEFAULT ''"),
        ("google_id", "ALTER TABLE users ADD COLUMN google_id TEXT DEFAULT ''"),
        ("referrer_id", "ALTER TABLE users ADD COLUMN referrer_id INTEGER DEFAULT NULL"),
        ("ref_bonus_paid", "ALTER TABLE users ADD COLUMN ref_bonus_paid INTEGER DEFAULT 0"),
    ]:
        if col not in user_cols:
            c.execute(ddl)

    tx_cols = [row[1] for row in c.execute("PRAGMA table_info(transactions)").fetchall()]
    if "admin_receipt" not in tx_cols:
        c.execute("ALTER TABLE transactions ADD COLUMN admin_receipt TEXT DEFAULT ''")

    admin = c.execute("SELECT id FROM users WHERE username = ?", (CONFIG["ADMIN_USER"],)).fetchone()
    if not admin:
        c.execute("""
            INSERT INTO users (username, email, password_hash, ad_balance, is_admin)
            VALUES (?, ?, ?, ?, 1)
        """, (CONFIG["ADMIN_USER"], CONFIG["ADMIN_EMAIL"], generate_password_hash(CONFIG["ADMIN_PASS"]), 10000))
    else:
        c.execute("""
            UPDATE users SET email = ?, is_admin = 1 WHERE username = ?
        """, (CONFIG["ADMIN_EMAIL"], CONFIG["ADMIN_USER"]))

    conn.commit()
    conn.close()

def is_user_admin(user):
    if not user:
        return False
    email = (user["email"] or "").strip().lower()
    if CONFIG["ADMIN_EMAIL"] and email == CONFIG["ADMIN_EMAIL"].lower():
        return True
    return bool(user["is_admin"]) and user["username"] == CONFIG["ADMIN_USER"]

# ================= YORDAMCHI FUNKSIYALAR =================
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()
        conn.close()
        if not user or user["is_banned"]:
            session.clear()
            flash("Akkaunt topilmadi yoki bloklangan!", "danger")
            return redirect(url_for("login"))
        return f(user, *args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()
        conn.close()
        if not is_user_admin(user):
            return redirect(url_for("dashboard"))
        return f(user, *args, **kwargs)
    return decorated

# ================= HTML SHABLON =================
BASE_HTML = """
<!DOCTYPE html>
<html lang="uz">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ config.SITE_NAME }}</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Segoe UI', Tahoma, sans-serif; }
        body { background: #0f172a; color: #f8fafc; line-height: 1.5; padding-bottom: 40px; }
        .navbar { background: #1e293b; padding: 14px 20px; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #334155; flex-wrap: wrap; gap: 10px; }
        .brand { font-size: 20px; font-weight: 800; color: #38bdf8; text-decoration: none; }
        .nav-links { display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }
        .nav-links a { color: #cbd5e1; text-decoration: none; font-size: 14px; font-weight: 600; padding: 6px 10px; border-radius: 6px; }
        .nav-links a:hover { background: #334155; color: #fff; }
        .container { max-width: 960px; margin: 20px auto; padding: 0 15px; }
        .card { background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 18px; margin-bottom: 16px; }
        .grid-2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 15px; }
        .stat-box { background: #0f172a; border: 1px solid #334155; padding: 15px; border-radius: 10px; }
        .stat-title { font-size: 13px; color: #94a3b8; }
        .stat-val { font-size: 22px; font-weight: 700; color: #38bdf8; margin-top: 4px; }
        .stat-val.green { color: #4ade80; }
        input, select, textarea { width: 100%; padding: 10px 12px; margin-top: 6px; margin-bottom: 14px; background: #0f172a; border: 1px solid #475569; border-radius: 8px; color: #fff; font-size: 14px; }
        .sm-input { width: auto; min-width: 170px; padding: 6px 10px; margin: 0; font-size: 13px; }
        label { font-size: 13px; color: #cbd5e1; font-weight: 600; }
        .btn { display: inline-block; padding: 10px 16px; border: none; border-radius: 8px; font-weight: 700; font-size: 14px; cursor: pointer; text-decoration: none; text-align: center; }
        .btn-primary { background: #0284c7; color: #fff; }
        .btn-primary:hover { background: #0369a1; }
        .btn-success { background: #16a34a; color: #fff; }
        .btn-success:hover { background: #15803d; }
        .btn-danger { background: #dc2626; color: #fff; }
        .btn-warning { background: #d97706; color: #fff; }
        .btn-google { background: #ffffff; color: #0f172a; border: 1px solid #cbd5e1; }
        .btn-google:hover { background: #e2e8f0; }
        .btn-sm { padding: 6px 12px; font-size: 13px; }
        .alert { padding: 12px 15px; border-radius: 8px; margin-bottom: 15px; font-size: 14px; font-weight: 600; }
        .alert-success { background: #14532d; color: #bbf7d0; border: 1px solid #16a34a; }
        .alert-danger { background: #7f1d1d; color: #fecaca; border: 1px solid #dc2626; }
        .alert-info { background: #0c4a6e; color: #bae6fd; border: 1px solid #0284c7; }
        .task-item { display: flex; justify-content: space-between; align-items: center; gap: 15px; flex-wrap: wrap; border-bottom: 1px solid #334155; padding: 14px 0; }
        .task-item:last-child { border-bottom: none; }
        .badge { display: inline-block; padding: 3px 8px; border-radius: 6px; font-size: 12px; font-weight: 700; background: #334155; color: #38bdf8; }
        table { width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 10px; }
        th, td { padding: 10px; text-align: left; border-bottom: 1px solid #334155; vertical-align: middle; }
        th { color: #94a3b8; }
        .table-wrap { overflow-x: auto; }
        .ref-box { background: #0f172a; border: 1px dashed #38bdf8; padding: 10px 12px; border-radius: 8px; font-size: 13px; word-break: break-all; margin-top: 8px; }
    </style>
</head>
<body>
    <div class="navbar">
        <a href="/" class="brand">⚡ {{ config.SITE_NAME }}</a>
        <div class="nav-links">
            {% if user %}
                <a href="{{ url_for('dashboard') }}">Asosiy</a>
                <a href="{{ url_for('earn') }}">Pul ishlash (+{{ config.WORKER_REWARD }} so'm)</a>
                <a href="{{ url_for('advertise') }}">Buyurtma ({{ config.ADVERTISER_PRICE }} so'm)</a>
                <a href="{{ url_for('wallet') }}">Hamyon</a>
                <a href="{{ url_for('profile') }}">Profil</a>
                {% if show_admin %}
                    <a href="{{ url_for('admin_panel') }}" style="background:#7c2d12;color:#fed7aa;">Admin</a>
                {% endif %}
                <a href="{{ url_for('logout') }}" style="color:#f87171;">Chiqish</a>
            {% else %}
                <a href="{{ url_for('google_login') }}" class="btn btn-google btn-sm">Google orqali kirish</a>
                <a href="{{ url_for('login') }}">Google email bilan kirish</a>
                <a href="{{ url_for('register') }}" class="btn btn-primary btn-sm">Ro'yxatdan o'tish</a>
            {% endif %}
        </div>
    </div>

    <div class="container">
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, msg in messages %}
                    <div class="alert alert-{{ category }}">{{ msg }}</div>
                {% endfor %}
            {% endif %}
        {% endwith %}

        {{ content | safe }}
    </div>
</body>
</html>
"""

def render_page(content_html, user=None, **kwargs):
    show_admin = is_user_admin(user)
    inner = render_template_string(
        content_html,
        user=user,
        show_admin=show_admin,
        config=CONFIG,
        platforms=PLATFORMS,
        platform_dict=PLATFORM_DICT,
        **kwargs
    )
    return render_template_string(BASE_HTML, content=inner, user=user, show_admin=show_admin, config=CONFIG)

# ================= GOOGLE OAUTH =================
def get_google_redirect_uri():
    return url_for("google_callback", _external=True)

@app.route("/auth/google")
def google_login():
    if not CONFIG["GOOGLE_CLIENT_ID"] or not CONFIG["GOOGLE_CLIENT_SECRET"]:
        flash("Google OAuth kalitlari hali kiritilmagan — pastdagi Google email (Gmail) formasi orqali kiring yoki ro'yxatdan o'ting.", "info")
        return redirect(url_for("login"))

    state = os.urandom(16).hex()
    session["google_oauth_state"] = state
    params = urllib.parse.urlencode({
        "client_id": CONFIG["GOOGLE_CLIENT_ID"],
        "redirect_uri": get_google_redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    })
    return redirect(f"https://accounts.google.com/o/oauth2/v2/auth?{params}")

@app.route("/auth/google/callback")
def google_callback():
    error = request.args.get("error")
    if error:
        flash("Google orqali kirish bekor qilindi.", "danger")
        return redirect(url_for("login"))

    state = request.args.get("state")
    if not state or state != session.pop("google_oauth_state", None):
        flash("Xavfsizlik tekshiruvi (state) xato!", "danger")
        return redirect(url_for("login"))

    code = request.args.get("code")
    if not code:
        flash("Google tasdiq kodi kelmadi!", "danger")
        return redirect(url_for("login"))

    token_data = urllib.parse.urlencode({
        "code": code,
        "client_id": CONFIG["GOOGLE_CLIENT_ID"],
        "client_secret": CONFIG["GOOGLE_CLIENT_SECRET"],
        "redirect_uri": get_google_redirect_uri(),
        "grant_type": "authorization_code",
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=token_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            token_json = json.loads(resp.read().decode("utf-8"))
        access_token = token_json.get("access_token")
        if not access_token:
            flash("Google token olinmadi!", "danger")
            return redirect(url_for("login"))

        info_req = urllib.request.Request(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        with urllib.request.urlopen(info_req, timeout=15) as info_resp:
            g_user = json.loads(info_resp.read().decode("utf-8"))
    except Exception:
        flash("Google serveri bilan bog'lanishda xatolik yuz berdi!", "danger")
        return redirect(url_for("login"))

    email = (g_user.get("email") or "").strip().lower()
    google_id = str(g_user.get("id") or "")
    base_name = (g_user.get("given_name") or (email.split("@")[0] if email else "user")).lower()
    clean_base = "".join(ch for ch in base_name if ch.isalnum() or ch == "_")[:16] or "user"

    if not email and not google_id:
        flash("Google akkaunt ma'lumotlari olinmadi!", "danger")
        return redirect(url_for("login"))

    conn = get_db()
    user = None
    if google_id:
        user = conn.execute("SELECT * FROM users WHERE google_id = ?", (google_id,)).fetchone()
    if not user and email:
        user = conn.execute("SELECT * FROM users WHERE LOWER(email) = ?", (email,)).fetchone()

    should_be_admin = 1 if (email and email == CONFIG["ADMIN_EMAIL"].lower()) else 0

    if user:
        if user["is_banned"]:
            conn.close()
            flash("Sizning akkauntingiz bloklangan!", "danger")
            return redirect(url_for("login"))
        conn.execute(
            "UPDATE users SET email = COALESCE(NULLIF(email, ''), ?), google_id = COALESCE(NULLIF(google_id, ''), ?), is_admin = CASE WHEN ? = 1 THEN 1 ELSE is_admin END WHERE id = ?",
            (email, google_id, should_be_admin, user["id"])
        )
        conn.commit()
        user_id = user["id"]
    else:
        username = clean_base
        idx = 1
        while conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone():
            username = f"{clean_base}_{idx}"
            idx += 1

        referrer_id = None
        ref_code = session.pop("ref", None)
        if ref_code:
            ref_user = conn.execute("SELECT id FROM users WHERE username = ?", (ref_code,)).fetchone()
            if ref_user:
                referrer_id = ref_user["id"]

        cur = conn.execute("""
            INSERT INTO users (username, email, google_id, password_hash, referrer_id, is_admin)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (username, email, google_id, generate_password_hash(os.urandom(16).hex()), referrer_id, should_be_admin))
        conn.commit()
        user_id = cur.lastrowid

    conn.close()
    session["user_id"] = user_id
    flash("Google email orqali kabinetga kirdingiz!", "success")
    return redirect(url_for("dashboard"))

# ================= YO'NALISHLAR (ROUTES) =================

@app.route("/")
def index():
    ref = request.args.get("ref")
    if ref:
        session["ref"] = ref
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    conn = get_db()
    users_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    tasks_done = conn.execute("SELECT COUNT(*) FROM completions").fetchone()[0]
    conn.close()

    html = """
    <div class="card" style="text-align:center; padding: 35px 20px;">
        <h1 style="font-size:28px; color:#38bdf8; margin-bottom:10px;">{{ config.SITE_NAME }} — Tezkor SMM Birja</h1>
        <p style="color:#cbd5e1; max-width:620px; margin:0 auto 20px;">
            Google email orqali kabinetga kiring, Instagram, YouTube, Telegram kanal va guruhlarga obuna bo'lib har bir harakat uchun <b>{{ config.WORKER_REWARD }} so'm</b> ishlang, do'stlarni chaqirib <b>+{{ config.REFERRAL_BONUS }} so'm</b> bonus oling yoki o'z sahifangizga atigi <b>{{ config.ADVERTISER_PRICE }} so'm</b>dan obunachilar yig'ing!
        </p>
        <div style="display:flex; gap:12px; justify-content:center; flex-wrap:wrap;">
            <a href="{{ url_for('google_login') }}" class="btn btn-google">Google orqali kirish</a>
            <a href="{{ url_for('register') }}" class="btn btn-primary">Google email bilan ro'yxatdan o'tish</a>
            <a href="{{ url_for('login') }}" class="btn btn-success">Kirish</a>
        </div>
    </div>
    <div class="grid-2">
        <div class="card" style="text-align:center;">
            <div class="stat-title">Jami foydalanuvchilar</div>
            <div class="stat-val">{{ users_count }} ta</div>
        </div>
        <div class="card" style="text-align:center;">
            <div class="stat-title">Bajarilgan vazifalar</div>
            <div class="stat-val green">{{ tasks_done }} ta</div>
        </div>
    </div>
    """
    return render_page(html, users_count=users_count, tasks_done=tasks_done)

@app.route("/register", methods=["GET", "POST"])
def register():
    ref_param = request.args.get("ref")
    if ref_param:
        session["ref"] = ref_param

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        ig_nick = request.form.get("ig_nick", "").strip()
        yt_nick = request.form.get("yt_nick", "").strip()
        tg_nick = request.form.get("tg_nick", "").strip()

        if not email or "@" not in email:
            flash("Iltimos, haqiqiy Google email (Gmail) manzilingizni kiriting!", "danger")
            return redirect(url_for("register"))

        if not username:
            base = "".join(ch for ch in email.split("@")[0] if ch.isalnum() or ch == "_")[:16] or "user"
            username = base

        if len(username) < 3 or len(password) < 4:
            flash("Login kamida 3 ta, parol kamida 4 ta belgidan iborat bo'lsin!", "danger")
            return redirect(url_for("register"))

        referrer_id = None
        ref_code = session.get("ref")
        conn = get_db()
        if ref_code:
            ref_user = conn.execute("SELECT id FROM users WHERE username = ?", (ref_code,)).fetchone()
            if ref_user:
                referrer_id = ref_user["id"]

        existing_email = conn.execute("SELECT id FROM users WHERE LOWER(email) = ?", (email,)).fetchone()
        if existing_email:
            conn.close()
            flash("Bu Google email allaqachon ro'yxatdan o'tgan! Kirish bo'limidan kiring.", "danger")
            return redirect(url_for("login"))

        # Username band bo'lsa unikal qilib moslashtiramiz
        base_username = username
        idx = 1
        while conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone():
            username = f"{base_username}_{idx}"
            idx += 1

        is_adm = 1 if email == CONFIG["ADMIN_EMAIL"].lower() else 0

        try:
            conn.execute("""
                INSERT INTO users (username, email, password_hash, referrer_id, ig_nick, yt_nick, tg_nick, is_admin)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (username, email, generate_password_hash(password), referrer_id, ig_nick, yt_nick, tg_nick, is_adm))
            conn.commit()
            session.pop("ref", None)
            flash("Muvaffaqiyatli ro'yxatdan o'tdingiz! Endi Google emailingiz bilan kiring.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("Ro'yxatdan o'tishda xatolik yuz berdi. Qayta urinib ko'ring.", "danger")
        finally:
            conn.close()

    html = """
    <div class="card" style="max-width:480px; margin:0 auto;">
        <h2 style="margin-bottom:12px;">Google email orqali ro'yxatdan o'tish</h2>
        <a href="{{ url_for('google_login') }}" class="btn btn-google" style="width:100%; margin-bottom:14px;">Google OAuth orqali 1 bosishda kirish</a>
        <form method="POST">
            <label>Google email (Gmail) *</label>
            <input type="email" name="email" required placeholder="sizning.email@gmail.com">

            <label>Kabinet logini (ixtiyoriy, bo'sh qoldirsa emaildan olinadi)</label>
            <input type="text" name="username" placeholder="Masalan: bahrom_01">

            <label>Parol *</label>
            <input type="password" name="password" required placeholder="Kamida 4 ta belgi">

            <hr style="border-color:#334155; margin:15px 0;">
            <p style="font-size:12px; color:#94a3b8; margin-bottom:10px;">Vazifa bajarish uchun ijtimoiy tarmoq niklaringizni kiriting:</p>

            <label>Instagram username</label>
            <input type="text" name="ig_nick" placeholder="@username">

            <label>YouTube kanal/akkaunt nomi</label>
            <input type="text" name="yt_nick" placeholder="@channel">

            <label>Telegram username</label>
            <input type="text" name="tg_nick" placeholder="@username">

            <button type="submit" class="btn btn-primary" style="width:100%;">Ro'yxatdan o'tish</button>
        </form>
    </div>
    """
    return render_page(html)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        identifier = request.form.get("identifier", "").strip().lower()
        password = request.form.get("password", "").strip()
        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE LOWER(email) = ? OR LOWER(username) = ?",
            (identifier, identifier)
        ).fetchone()
        conn.close()
        if user and check_password_hash(user["password_hash"], password):
            if user["is_banned"]:
                flash("Sizning akkauntingiz bloklangan!", "danger")
                return redirect(url_for("login"))
            session["user_id"] = user["id"]
            return redirect(url_for("dashboard"))
        flash("Google email (yoki login) yoki parol noto'g'ri!", "danger")

    html = """
    <div class="card" style="max-width:420px; margin:0 auto;">
        <h2 style="margin-bottom:12px;">Google email orqali kabinetga kirish</h2>
        <a href="{{ url_for('google_login') }}" class="btn btn-google" style="width:100%; margin-bottom:14px;">Google OAuth orqali kirish</a>
        <form method="POST">
            <label>Google email (yoki login)</label>
            <input type="text" name="identifier" required placeholder="example@gmail.com">
            <label>Parol</label>
            <input type="password" name="password" required>
            <button type="submit" class="btn btn-success" style="width:100%;">Kabinetga kirish</button>
        </form>
    </div>
    """
    return render_page(html)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

@app.route("/dashboard")
@login_required
def dashboard(user):
    conn = get_db()
    my_done = conn.execute("SELECT COUNT(*) FROM completions WHERE user_id = ?", (user["id"],)).fetchone()[0]
    my_tasks = conn.execute("SELECT COUNT(*) FROM tasks WHERE owner_id = ?", (user["id"],)).fetchone()[0]
    refs_count = conn.execute("SELECT COUNT(*) FROM users WHERE referrer_id = ?", (user["id"],)).fetchone()[0]
    conn.close()

    ref_link = request.url_root.rstrip("/") + url_for("register", ref=user["username"])

    html = """
    <h2 style="margin-bottom:15px;">Salom, {{ user.username }}! 👋</h2>
    <div class="grid-2">
        <div class="stat-box">
            <div class="stat-title">Ishlangan balans (Min. yechish: {{ config.MIN_WITHDRAW }} so'm)</div>
            <div class="stat-val green">{{ user.work_balance }} so'm</div>
            <a href="{{ url_for('earn') }}" class="btn btn-success btn-sm" style="margin-top:10px;">Pul ishlash</a>
        </div>
        <div class="stat-box">
            <div class="stat-title">Reklama balansi (Deposit)</div>
            <div class="stat-val">{{ user.ad_balance }} so'm</div>
            <a href="{{ url_for('wallet') }}" class="btn btn-primary btn-sm" style="margin-top:10px;">Hisob to'ldirish</a>
        </div>
    </div>

    <div class="card" style="margin-top:15px;">
        <h3>🎁 Referal tizimi (Taklif qilganingizga +{{ config.REFERRAL_BONUS }} so'm)</h3>
        <p style="font-size:13px; color:#cbd5e1; margin-top:6px;">
            Do'stingiz havolangiz orqali kirib ilk vazifani bajarganda balansingizga <b>+{{ config.REFERRAL_BONUS }} so'm</b> qo'shiladi. Chaqirilgan do'stlar: <b>{{ refs_count }} ta</b>
        </p>
        <div class="ref-box">{{ ref_link }}</div>
    </div>

    <div class="card">
        <h3>Sizning ko'rsatkichlaringiz</h3>
        <p style="color:#cbd5e1; margin-top:8px;">✅ Siz bajargan vazifalar: <b>{{ my_done }} ta</b></p>
        <p style="color:#cbd5e1;">📢 Siz yaratgan buyurtmalar: <b>{{ my_tasks }} ta</b></p>
    </div>
    """
    return render_page(html, user=user, my_done=my_done, my_tasks=my_tasks, refs_count=refs_count, ref_link=ref_link)

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile(user):
    if request.method == "POST":
        ig_nick = request.form.get("ig_nick", "").strip()
        yt_nick = request.form.get("yt_nick", "").strip()
        tg_nick = request.form.get("tg_nick", "").strip()
        conn = get_db()
        conn.execute("UPDATE users SET ig_nick=?, yt_nick=?, tg_nick=? WHERE id=?", (ig_nick, yt_nick, tg_nick, user["id"]))
        conn.commit()
        conn.close()
        flash("Akkaunt ma'lumotlari saqlandi!", "success")
        return redirect(url_for("profile"))

    html = """
    <div class="card" style="max-width:550px;">
        <h2>Profil va ijtimoiy tarmoq akkauntlarim</h2>
        <p style="font-size:13px; color:#94a3b8; margin-bottom:15px;">
            Google email: <b>{{ user.email or 'Kiritilmagan' }}</b>
        </p>
        <form method="POST">
            <label>Instagram username</label>
            <input type="text" name="ig_nick" value="{{ user.ig_nick }}" placeholder="@instagram_nik">

            <label>YouTube kanal / akkaunt</label>
            <input type="text" name="yt_nick" value="{{ user.yt_nick }}" placeholder="@youtube_nik">

            <label>Telegram username</label>
            <input type="text" name="tg_nick" value="{{ user.tg_nick }}" placeholder="@telegram_nik">

            <button type="submit" class="btn btn-primary">Saqlash</button>
        </form>
    </div>
    """
    return render_page(html, user=user)

@app.route("/earn")
@login_required
def earn(user):
    platform_filter = request.args.get("platform", "all")
    conn = get_db()

    query = """
        SELECT t.* FROM tasks t
        WHERE t.status = 'active'
          AND t.owner_id != ?
          AND t.completed_count < t.target_count
          AND t.id NOT IN (SELECT task_id FROM completions WHERE user_id = ?)
    """
    params = [user["id"], user["id"]]
    if platform_filter != "all":
        query += " AND t.platform = ?"
        params.append(platform_filter)
    query += " ORDER BY t.id DESC"

    tasks = conn.execute(query, params).fetchall()
    conn.close()

    html = """
    <div class="card">
        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
            <h2>Vazifalar ro'yxati</h2>
            <div>
                <a href="{{ url_for('earn', platform='all') }}" class="btn btn-sm btn-primary">Barchasi</a>
                {% for key, name in platforms %}
                    <a href="{{ url_for('earn', platform=key) }}" class="btn btn-sm" style="background:#334155;color:#fff;">{{ name.split(' ')[0] }}</a>
                {% endfor %}
            </div>
        </div>
        <p style="font-size:13px; color:#94a3b8; margin-top:8px;">
            1. <b>"Ochish"</b> tugmasini bosing va obuna bo'ling.<br>
            2. Saytga qaytib <b>"Tekshirish (+{{ config.WORKER_REWARD }} so'm)"</b> tugmasini bosing.
        </p>
    </div>

    <div class="card">
        {% if tasks %}
            {% for t in tasks %}
                <div class="task-item">
                    <div>
                        <span class="badge">{{ platform_dict[t.platform] }}</span>
                        <h4 style="margin-top:6px;">{{ t.title }}</h4>
                        <small style="color:#94a3b8;">Bajarildi: {{ t.completed_count }} / {{ t.target_count }}</small>
                    </div>
                    <div style="display:flex; gap:8px; align-items:center;">
                        <a href="{{ t.link }}" target="_blank" onclick="markOpened({{ t.id }})" class="btn btn-primary btn-sm">1. Ochish ↗</a>
                        <form method="POST" action="{{ url_for('verify_task', task_id=t.id) }}" style="margin:0;">
                            <button type="submit" class="btn btn-success btn-sm">2. Tekshirish (+{{ t.reward_per_action }} so'm)</button>
                        </form>
                    </div>
                </div>
            {% endfor %}
        {% else %}
            <p style="color:#94a3b8; text-align:center; padding:20px;">Hozircha yangi vazifalar yo'q. Keyinroq qayta tekshiring!</p>
        {% endif %}
    </div>
    <script>
        function markOpened(id) {
            fetch('/start_task/' + id, {method: 'POST'});
        }
    </script>
    """
    return render_page(html, user=user, tasks=tasks)

@app.route("/start_task/<int:task_id>", methods=["POST"])
@login_required
def start_task(user, task_id):
    started = session.get("started_tasks", {})
    started[str(task_id)] = time.time()
    session["started_tasks"] = started
    return {"ok": True}

@app.route("/verify_task/<int:task_id>", methods=["POST"])
@login_required
def verify_task(user, task_id):
    started = session.get("started_tasks", {})
    start_time = started.get(str(task_id))

    if not start_time:
        flash("Avval '1. Ochish' tugmasini bosib sahifaga o'ting va obuna bo'ling!", "danger")
        return redirect(url_for("earn"))
    if time.time() - start_time < 4:
        flash("Juda tez bosdingiz! Kamida 4-5 soniya ichida haqiqiy obuna bo'ling.", "danger")
        return redirect(url_for("earn"))

    conn = get_db()
    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not task or task["status"] != "active" or task["completed_count"] >= task["target_count"]:
        conn.close()
        flash("Bu vazifa yakunlangan!", "danger")
        return redirect(url_for("earn"))

    if task["platform"] == "instagram" and not user["ig_nick"]:
        conn.close()
        flash("Avval Profil bo'limidan Instagram nikingizni kiriting!", "danger")
        return redirect(url_for("profile"))
    if task["platform"] == "youtube" and not user["yt_nick"]:
        conn.close()
        flash("Avval Profil bo'limidan YouTube nikingizni kiriting!", "danger")
        return redirect(url_for("profile"))
    if task["platform"].startswith("telegram") and not user["tg_nick"]:
        conn.close()
        flash("Avval Profil bo'limidan Telegram nikingizni kiriting!", "danger")
        return redirect(url_for("profile"))

    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO completions (user_id, task_id, reward) VALUES (?, ?, ?)",
            (user["id"], task_id, task["reward_per_action"])
        )
        new_count = task["completed_count"] + 1
        new_status = "completed" if new_count >= task["target_count"] else "active"
        conn.execute(
            "UPDATE tasks SET completed_count = ?, status = ? WHERE id = ?",
            (new_count, new_status, task_id)
        )
        conn.execute(
            "UPDATE users SET work_balance = work_balance + ? WHERE id = ?",
            (task["reward_per_action"], user["id"])
        )

        bonus_msg = ""
        if user["referrer_id"] and not user["ref_bonus_paid"] and CONFIG["REFERRAL_BONUS"] > 0:
            conn.execute(
                "UPDATE users SET work_balance = work_balance + ? WHERE id = ?",
                (CONFIG["REFERRAL_BONUS"], user["referrer_id"])
            )
            conn.execute(
                "UPDATE users SET ref_bonus_paid = 1 WHERE id = ?",
                (user["id"],)
            )
            bonus_msg = f" Taklif qilgan foydalanuvchiga +{CONFIG['REFERRAL_BONUS']} so'm bonus berildi!"

        conn.commit()
        started.pop(str(task_id), None)
        session["started_tasks"] = started
        flash(f"Tabriklaymiz! Hisobingizga +{task['reward_per_action']} so'm qo'shildi!{bonus_msg}", "success")
    except sqlite3.IntegrityError:
        conn.rollback()
        flash("Siz bu vazifani allaqachon bajargansiz!", "danger")
    finally:
        conn.close()

    return redirect(url_for("earn"))

@app.route("/advertise", methods=["GET", "POST"])
@login_required
def advertise(user):
    price = CONFIG["ADVERTISER_PRICE"]
    max_possible = user["ad_balance"] // price

    if request.method == "POST":
        platform = request.form.get("platform")
        title = request.form.get("title", "").strip()
        link = request.form.get("link", "").strip()
        try:
            count = int(request.form.get("count", 0))
        except ValueError:
            count = 0

        total_cost = count * price
        if platform not in PLATFORM_DICT or not title or not link.startswith("http"):
            flash("Barcha maydonlarni to'g'ri to'ldiring (havola http/https bilan boshlansin)!", "danger")
            return redirect(url_for("advertise"))
        if count < 1:
            flash("Kamida 1 ta obunachi kiriting!", "danger")
            return redirect(url_for("advertise"))
        if total_cost > user["ad_balance"]:
            flash(f"Reklama balansingiz yetarli emas! Kerak: {total_cost} so'm.", "danger")
            return redirect(url_for("advertise"))

        conn = get_db()
        conn.execute("UPDATE users SET ad_balance = ad_balance - ? WHERE id = ?", (total_cost, user["id"]))
        conn.execute("""
            INSERT INTO tasks (owner_id, platform, title, link, target_count, price_per_action, reward_per_action)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user["id"], platform, title, link, count, price, CONFIG["WORKER_REWARD"]))
        conn.commit()
        conn.close()
        flash(f"Buyurtma joylandi! {count} ta obunachi uchun {total_cost} so'm yechildi.", "success")
        return redirect(url_for("advertise"))

    conn = get_db()
    my_tasks = conn.execute("SELECT * FROM tasks WHERE owner_id = ? ORDER BY id DESC", (user["id"],)).fetchall()
    conn.close()

    html = """
    <div class="card">
        <h2>Yangi buyurtma berish</h2>
        <p style="font-size:13px; color:#94a3b8; margin-bottom:12px;">
            Reklama balansingiz: <b style="color:#38bdf8;">{{ user.ad_balance }} so'm</b> |
            1 ta obunachi narxi: <b>{{ config.ADVERTISER_PRICE }} so'm</b> |
            Siz hozir maksimal <b>{{ max_possible }} ta</b> obunachi buyurtma qila olasiz.
        </p>
        <form method="POST">
            <label>Tarmoqni tanlang</label>
            <select name="platform">
                {% for key, name in platforms %}
                    <option value="{{ key }}">{{ name }}</option>
                {% endfor %}
            </select>

            <label>Vazifa sarlavhasi (Masalan: Kanalimga obuna bo'ling)</label>
            <input type="text" name="title" required placeholder="Kanal yoki profil nomi">

            <label>Havola (Link)</label>
            <input type="url" name="link" required placeholder="https://t.me/... yoki https://instagram.com/...">

            <label>Nechta obunachi kerak? (1 tasi = {{ config.ADVERTISER_PRICE }} so'm)</label>
            <input type="number" name="count" min="1" max="{{ max_possible if max_possible > 0 else 1 }}" required placeholder="Masalan: 50">

            <button type="submit" class="btn btn-primary">Buyurtmani ishga tushirish</button>
        </form>
    </div>

    <div class="card">
        <h3>Mening buyurtmalarim</h3>
        {% if my_tasks %}
            <div class="table-wrap">
                <table>
                    <tr>
                        <th>Tarmoq</th>
                        <th>Sarlavha</th>
                        <th>Holat</th>
                        <th>Amal</th>
                    </tr>
                    {% for t in my_tasks %}
                    <tr>
                        <td>{{ platform_dict[t.platform] }}</td>
                        <td>{{ t.title }}</td>
                        <td>{{ t.completed_count }} / {{ t.target_count }} ({{ t.status }})</td>
                        <td>
                            {% if t.status == 'active' %}
                                <form method="POST" action="{{ url_for('cancel_task', task_id=t.id) }}" style="margin:0;">
                                    <button type="submit" class="btn btn-danger btn-sm">To'xtatish</button>
                                </form>
                            {% else %}
                                -
                            {% endif %}
                        </td>
                    </tr>
                    {% endfor %}
                </table>
            </div>
        {% else %}
            <p style="color:#94a3b8; margin-top:10px;">Hali buyurtma bermagansiz.</p>
        {% endif %}
    </div>
    """
    return render_page(html, user=user, max_possible=max_possible, my_tasks=my_tasks)

@app.route("/cancel_task/<int:task_id>", methods=["POST"])
@login_required
def cancel_task(user, task_id):
    conn = get_db()
    task = conn.execute("SELECT * FROM tasks WHERE id = ? AND owner_id = ?", (task_id, user["id"])).fetchone()
    if task and task["status"] == "active":
        remaining = task["target_count"] - task["completed_count"]
        refund = remaining * task["price_per_action"]
        conn.execute("UPDATE tasks SET status = 'cancelled' WHERE id = ?", (task_id,))
        conn.execute("UPDATE users SET ad_balance = ad_balance + ? WHERE id = ?", (refund, user["id"]))
        conn.commit()
        flash(f"Buyurtma to'xtatildi. Bajarilmagan {remaining} ta obuna uchun {refund} so'm qaytarildi!", "info")
    conn.close()
    return redirect(url_for("advertise"))

@app.route("/wallet", methods=["GET", "POST"])
@login_required
def wallet(user):
    if request.method == "POST":
        action = request.form.get("action")
        try:
            amount = int(request.form.get("amount", 0))
        except ValueError:
            amount = 0

        conn = get_db()
        if action == "deposit":
            details = request.form.get("details", "").strip()
            if amount < CONFIG["MIN_DEPOSIT"] or not details:
                flash(f"Minimal deposit {CONFIG['MIN_DEPOSIT']} so'm va chek ma'lumoti kiritilishi shart!", "danger")
            else:
                conn.execute("INSERT INTO transactions (user_id, tx_type, amount, details) VALUES (?, 'deposit', ?, ?)",
                             (user["id"], amount, details))
                conn.commit()
                flash("Deposit cheki yuborildi! Admin tasdiqlagach Reklama balansingizga tushadi.", "success")

        elif action == "withdraw":
            card = request.form.get("card", "").strip()
            if amount < CONFIG["MIN_WITHDRAW"]:
                flash(f"Minimal pul chiqarish {CONFIG['MIN_WITHDRAW']} so'm!", "danger")
            elif amount > user["work_balance"]:
                flash("Ishlangan balansingizda mablag' yetarli emas!", "danger")
            elif len(card.replace(" ", "")) < 16:
                flash("Karta raqamini to'liq kiriting!", "danger")
            else:
                conn.execute("UPDATE users SET work_balance = work_balance - ? WHERE id = ?", (amount, user["id"]))
                conn.execute("INSERT INTO transactions (user_id, tx_type, amount, details) VALUES (?, 'withdraw', ?, ?)",
                             (user["id"], amount, card))
                conn.commit()
                flash("Pul chiqarish so'rovingiz Admin panelga yuborildi!", "success")

        elif action == "exchange":
            if amount < 70 or amount > user["work_balance"]:
                flash("O'tkazish uchun mablag' yetarli emas (kamida 70 so'm)!", "danger")
            else:
                conn.execute("UPDATE users SET work_balance = work_balance - ?, ad_balance = ad_balance + ? WHERE id = ?",
                             (amount, amount, user["id"]))
                conn.commit()
                flash(f"{amount} so'm Reklama balansiga o'tkazildi!", "success")

        conn.close()
        return redirect(url_for("wallet"))

    conn = get_db()
    txs = conn.execute("SELECT * FROM transactions WHERE user_id = ? ORDER BY id DESC LIMIT 20", (user["id"],)).fetchall()
    conn.close()

    html = """
    <div class="grid-2">
        <div class="card">
            <h3>💳 Hisobni to'ldirish (Deposit)</h3>
            <div class="alert alert-info" style="margin-top:10px;">
                Deposit karta: <b>{{ config.CARD_NUMBER }}</b><br>
                Egasi: <b>{{ config.CARD_HOLDER }}</b><br>
                Minimal: <b>{{ config.MIN_DEPOSIT }} so'm</b><br>
                <small style="color:#e0f2fe;">Kartaga pul tashlang va pastda chek ma'lumotini yuboring. Admin tasdiqlagach balansga tushadi.</small>
            </div>
            <form method="POST">
                <input type="hidden" name="action" value="deposit">
                <label>To'langan summa (so'm)</label>
                <input type="number" name="amount" min="{{ config.MIN_DEPOSIT }}" required placeholder="Masalan: 7000">
                <label>Chek ma'lumoti (vaqt, chek ID yoki izoh)</label>
                <input type="text" name="details" required placeholder="Masalan: 20:15 da tashladim, chek #4821">
                <button type="submit" class="btn btn-primary" style="width:100%;">Chekni yuborish</button>
            </form>
        </div>

        <div class="card">
            <h3>💸 Pul chiqarish (Ishlangan balans: {{ user.work_balance }} so'm)</h3>
            <form method="POST" style="margin-top:10px;">
                <input type="hidden" name="action" value="withdraw">
                <label>Summa (Min: {{ config.MIN_WITHDRAW }} so'm)</label>
                <input type="number" name="amount" min="{{ config.MIN_WITHDRAW }}" required placeholder="Kamida 20000">
                <label>Karta raqamingiz</label>
                <input type="text" name="card" required placeholder="8600 / 9860 **** **** ****">
                <button type="submit" class="btn btn-success" style="width:100%;">Pul chiqarish so'rovi yuborish</button>
            </form>

            <hr style="border-color:#334155; margin:18px 0;">
            <h4>🔄 Ishlangan pulni Reklama balansiga o'tkazish</h4>
            <form method="POST" style="margin-top:8px;">
                <input type="hidden" name="action" value="exchange">
                <input type="number" name="amount" min="70" max="{{ user.work_balance }}" placeholder="Summa (so'm)" required>
                <button type="submit" class="btn btn-warning btn-sm" style="width:100%;">Reklama balansiga o'tkazish</button>
            </form>
        </div>
    </div>

    <div class="card">
        <h3>To'lovlar tarixi</h3>
        <div class="table-wrap">
            <table>
                <tr><th>Turi</th><th>Summa</th><th>Karta / Chek</th><th>Admin cheki/izohi</th><th>Holat</th></tr>
                {% for tx in txs %}
                <tr>
                    <td>{{ 'Deposit' if tx.tx_type == 'deposit' else 'Chiqarish' }}</td>
                    <td>{{ tx.amount }} so'm</td>
                    <td>{{ tx.details }}</td>
                    <td>{{ tx.admin_receipt or '-' }}</td>
                    <td>{{ tx.status }}</td>
                </tr>
                {% endfor %}
            </table>
        </div>
    </div>
    """
    return render_page(html, user=user, txs=txs)

# ================= ADMIN PANEL (FAQAT ADMIN UCHUN) =================
@app.route("/admin", methods=["GET", "POST"])
@admin_required
def admin_panel(user):
    conn = get_db()
    if request.method == "POST":
        tx_id = request.form.get("tx_id")
        decision = request.form.get("decision")
        receipt_note = request.form.get("receipt_note", "").strip()
        tx = conn.execute("SELECT * FROM transactions WHERE id = ? AND status = 'pending'", (tx_id,)).fetchone()
        if tx:
            if decision == "approve":
                conn.execute("UPDATE transactions SET status = 'approved', admin_receipt = ? WHERE id = ?",
                             (receipt_note or "Tasdiqlandi", tx_id))
                if tx["tx_type"] == "deposit":
                    conn.execute("UPDATE users SET ad_balance = ad_balance + ? WHERE id = ?", (tx["amount"], tx["user_id"]))
                flash("So'rov tasdiqlandi va chek/izoh saqlandi!", "success")
            elif decision == "reject":
                conn.execute("UPDATE transactions SET status = 'rejected', admin_receipt = ? WHERE id = ?",
                             (receipt_note or "Rad etildi", tx_id))
                if tx["tx_type"] == "withdraw":
                    conn.execute("UPDATE users SET work_balance = work_balance + ? WHERE id = ?", (tx["amount"], tx["user_id"]))
                flash("So'rov rad etildi!", "danger")
            conn.commit()
        return redirect(url_for("admin_panel"))

    pending_txs = conn.execute("""
        SELECT t.*, u.username FROM transactions t
        JOIN users u ON t.user_id = u.id
        WHERE t.status = 'pending' ORDER BY t.id ASC
    """).fetchall()

    stats = {
        "users": conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "tasks": conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0],
        "completions": conn.execute("SELECT COUNT(*) FROM completions").fetchone()[0],
    }
    stats["profit"] = stats["completions"] * (CONFIG["ADVERTISER_PRICE"] - CONFIG["WORKER_REWARD"])
    conn.close()

    html = """
    <div class="grid-2" style="grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));">
        <div class="stat-box"><div class="stat-title">Foydalanuvchilar</div><div class="stat-val">{{ stats.users }}</div></div>
        <div class="stat-box"><div class="stat-title">Jami vazifalar</div><div class="stat-val">{{ stats.tasks }}</div></div>
        <div class="stat-box"><div class="stat-title">Bajarilgan obunalar</div><div class="stat-val">{{ stats.completions }}</div></div>
        <div class="stat-box"><div class="stat-title">Sayt sof foydasi (20 so'mdan)</div><div class="stat-val green">{{ stats.profit }} so'm</div></div>
    </div>

    <div class="card" style="margin-top:15px;">
        <h2>Kutilayotgan Deposit va Pul chiqarish so'rovlari</h2>
        {% if pending_txs %}
            <div class="table-wrap">
                <table>
                    <tr>
                        <th>User</th>
                        <th>Turi</th>
                        <th>Summa</th>
                        <th>Karta / Foydalanuvchi cheki</th>
                        <th>Admin cheki / Izoh</th>
                        <th>Amal</th>
                    </tr>
                    {% for tx in pending_txs %}
                    <tr>
                        <td>{{ tx.username }}</td>
                        <td>{{ '📥 DEPOSIT' if tx.tx_type == 'deposit' else '📤 CHIQARISH' }}</td>
                        <td><b>{{ tx.amount }} so'm</b></td>
                        <td><b>{{ tx.details }}</b></td>
                        <td colspan="2">
                            <form method="POST" style="display:flex; gap:6px; flex-wrap:wrap; align-items:center; margin:0;">
                                <input type="hidden" name="tx_id" value="{{ tx.id }}">
                                <input type="text" name="receipt_note" class="sm-input" placeholder="Chek ID yoki izoh...">
                                <button name="decision" value="approve" class="btn btn-success btn-sm">Tasdiqlash</button>
                                <button name="decision" value="reject" class="btn btn-danger btn-sm">Rad etish</button>
                            </form>
                        </td>
                    </tr>
                    {% endfor %}
                </table>
            </div>
        {% else %}
            <p style="color:#94a3b8; margin-top:10px;">Hozircha kutilayotgan so'rovlar yo'q.</p>
        {% endif %}
    </div>
    """
    return render_page(html, user=user, pending_txs=pending_txs, stats=stats)

init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
