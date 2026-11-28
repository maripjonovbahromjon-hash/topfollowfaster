import os
import json
import random
import secrets
import smtplib
import sqlite3
import time
import urllib.parse
import urllib.request
from datetime import timedelta
from email.mime.text import MIMEText
from functools import wraps
from flask import Flask, request, session, redirect, url_for, render_template_string, flash, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "topfollowfaster_super_secret_key_2026")
app.permanent_session_lifetime = timedelta(days=10)

@app.before_request
def make_session_permanent():
    session.permanent = True

RECEIPT_UPLOAD_DIR = os.path.join("static", "receipts")
ALLOWED_RECEIPT_EXTS = {"png", "jpg", "jpeg", "webp", "pdf"}
os.makedirs(RECEIPT_UPLOAD_DIR, exist_ok=True)

CONFIG = {
    "SITE_NAME": "TopFollowFaster",
    "ADVERTISER_PRICE": 70,
    "WORKER_REWARD": 50,
    "REFERRAL_BONUS": 500,
    "MIN_DEPOSIT": 1000,
    "MIN_WITHDRAW": 20000,
    "CARD_NUMBER": "9860 1701 1301 9658",
    "CARD_HOLDER": "ADMIN KARTASI",
    "ADMIN_USER": "admin",
    "ADMIN_PASS": "admin123",
    "ADMIN_EMAIL": "maripjonovbahromjon@gmail.com",
    "SMTP_EMAIL": os.environ.get("SMTP_EMAIL", "maripjonovbahromjon@gmail.com"),
    "SMTP_APP_PASSWORD": os.environ.get("SMTP_APP_PASSWORD", ""),
    "BREVO_API_KEY": os.environ.get("BREVO_API_KEY", ""),
    "RESEND_API_KEY": os.environ.get("RESEND_API_KEY", ""),
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

def get_db():
    conn = sqlite3.connect(CONFIG["DB_NAME"])
    conn.row_factory = sqlite3.Row
    return conn

def generate_quick_token():
    return secrets.token_urlsafe(12)

def seed_initial_tasks(c, admin_id):
    existing = c.execute("SELECT COUNT(*) FROM tasks WHERE status = 'active'").fetchone()[0]
    if existing > 0:
        return
    sample_tasks = [
        (admin_id, "instagram", "Instagram rasmiy sahifaga obuna bo'ling", "https://www.instagram.com/instagram/", 500),
        (admin_id, "instagram", "Bahromjon Instagram sahifasiga obuna bo'ling", "https://www.instagram.com/bahromjonmaripjonov/", 500),
        (admin_id, "youtube", "YouTube kanalga obuna bo'ling", "https://www.youtube.com/", 500),
        (admin_id, "telegram_channel", "Telegram yangiliklar kanaliga a'zo bo'ling", "https://t.me/telegram", 500),
        (admin_id, "telegram_group", "Telegram muhokama guruhiga qo'shiling", "https://t.me/telegram", 500),
    ]
    for owner_id, platform, title, link, target_count in sample_tasks:
        c.execute("""
            INSERT INTO tasks (owner_id, platform, title, link, target_count, price_per_action, reward_per_action, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'active')
        """, (owner_id, platform, title, link, target_count, CONFIG["ADVERTISER_PRICE"], CONFIG["WORKER_REWARD"]))

def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE,
            google_id TEXT DEFAULT '',
            quick_token TEXT DEFAULT '',
            password_hash TEXT NOT NULL,
            referrer_id INTEGER DEFAULT NULL,
            ref_bonus_paid INTEGER DEFAULT 0,
            ig_nick TEXT DEFAULT '',
            ig_link TEXT DEFAULT '',
            yt_nick TEXT DEFAULT '',
            tg_nick TEXT DEFAULT '',
            reset_code TEXT DEFAULT '',
            reset_expires INTEGER DEFAULT 0,
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
            receipt_file TEXT DEFAULT '',
            admin_receipt TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message TEXT NOT NULL,
            task_id INTEGER DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    admin = c.execute("SELECT id FROM users WHERE username = ?", (CONFIG["ADMIN_USER"],)).fetchone()
    if not admin:
        cur = c.execute("""
            INSERT INTO users (username, email, password_hash, quick_token, ad_balance, is_admin)
            VALUES (?, ?, ?, ?, ?, 1)
        """, (CONFIG["ADMIN_USER"], CONFIG["ADMIN_EMAIL"], generate_password_hash(CONFIG["ADMIN_PASS"]), generate_quick_token(), 10000))
        admin_id = cur.lastrowid
    else:
        admin_id = admin["id"]
        c.execute("UPDATE users SET email = ?, is_admin = 1 WHERE username = ?", (CONFIG["ADMIN_EMAIL"], CONFIG["ADMIN_USER"]))

    seed_initial_tasks(c, admin_id)
    conn.commit()
    conn.close()

def is_user_admin(user):
    if not user:
        return False
    email = (user["email"] or "").strip().lower()
    if CONFIG["ADMIN_EMAIL"] and email == CONFIG["ADMIN_EMAIL"].lower():
        return True
    return bool(user["is_admin"]) and user["username"] == CONFIG["ADMIN_USER"]

def allowed_receipt(filename):
    if not filename or "." not in filename:
        return False
    return filename.rsplit(".", 1)[1].lower() in ALLOWED_RECEIPT_EXTS

def parse_ig_link_or_nick(raw_value):
    val = (raw_value or "").strip()
    if not val:
        return "", ""
    if val.startswith("http://") or val.startswith("https://"):
        parsed = urllib.parse.urlparse(val)
        parts = [p for p in parsed.path.split("/") if p]
        if parts and parts[0].lower() not in ("p", "reel", "reels", "stories", "explore"):
            nick = parts[0].lstrip("@").strip().lower()
            return nick, f"https://www.instagram.com/{nick}/"
        return "", val
    nick = val.lstrip("@").strip().lower()
    return nick, f"https://www.instagram.com/{nick}/"

def to_web_url(raw_url):
    u = (raw_url or "").strip()
    if not u:
        return "#"
    if u.startswith("http://") or u.startswith("https://"):
        return u
    return "https://" + u.lstrip("/")

def verify_account_exists(platform, account_handle):
    clean = (account_handle or "").lstrip("@").strip().lower()
    if not clean:
        return False
    if platform == "instagram":
        try:
            url = f"https://www.instagram.com/{urllib.parse.quote(clean)}/"
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/126.0 Mobile Safari/537.36"}
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                return resp.status == 200
        except Exception:
            return True
    return True

def send_reset_email(to_email, code):
    subject = f"{CONFIG['SITE_NAME']} — Parolni tiklash kodi: {code}"
    body_text = f"Tasdiqlash kodingiz: {code} (10 daqiqa amal qiladi)."

    brevo_key = (os.environ.get("BREVO_API_KEY") or CONFIG["BREVO_API_KEY"] or "").strip()
    sender_email = (os.environ.get("SMTP_EMAIL") or CONFIG["SMTP_EMAIL"] or "no-reply@example.com").strip()
    if brevo_key:
        try:
            payload = json.dumps({
                "sender": {"name": CONFIG["SITE_NAME"], "email": sender_email},
                "to": [{"email": to_email}],
                "subject": subject,
                "textContent": body_text,
            }).encode("utf-8")
            req = urllib.request.Request(
                "https://api.brevo.com/v3/smtp/email",
                data=payload,
                headers={"accept": "application/json", "api-key": brevo_key, "content-type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                if 200 <= resp.status < 300:
                    return True, "Kod emailga yuborildi!"
        except Exception as e:
            return False, f"Brevo API xatosi: {e}"

    smtp_email = (os.environ.get("SMTP_EMAIL") or CONFIG["SMTP_EMAIL"] or "").strip()
    smtp_pass = (os.environ.get("SMTP_APP_PASSWORD") or CONFIG["SMTP_APP_PASSWORD"] or "").replace(" ", "").strip()
    if not smtp_email or not smtp_pass:
        return False, "BREVO_API_KEY yoki SMTP_APP_PASSWORD sozlanmagan."

    msg = MIMEText(body_text, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = smtp_email
    msg["To"] = to_email
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=7) as server:
            server.starttls()
            server.login(smtp_email, smtp_pass)
            server.sendmail(smtp_email, [to_email], msg.as_string())
        return True, "Kod emailga yuborildi!"
    except Exception as e:
        return False, f"SMTP xatosi: {e}"

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

BASE_HTML = """
<!DOCTYPE html>
<html lang="uz">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ config.SITE_NAME }}</title>
    <style>
        body { background:#070b14; color:#f8fafc; font-family:sans-serif; margin:0; padding-bottom:40px; }
        .navbar { background:#0b1120; padding:14px 20px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px; border-bottom:1px solid rgba(148,163,184,0.2); }
        .brand { color:#38bdf8; font-weight:800; font-size:20px; text-decoration:none; }
        .nav-links { display:flex; gap:10px; flex-wrap:wrap; align-items:center; }
        .nav-links a { color:#cbd5e1; text-decoration:none; font-size:13.5px; font-weight:600; }
        .container { max-width:960px; margin:22px auto; padding:0 15px; }
        .card { background:#111928; border:1px solid rgba(148,163,184,0.2); border-radius:14px; padding:18px; margin-bottom:16px; }
        input, select { width:100%; padding:10px 12px; margin:6px 0 14px; background:#0b1120; border:1px solid rgba(148,163,184,0.3); border-radius:10px; color:#fff; box-sizing:border-box; }
        .btn { display:inline-block; padding:10px 16px; border:none; border-radius:10px; font-weight:700; cursor:pointer; text-decoration:none; font-size:13.5px; }
        .btn-primary { background:#2563eb; color:#fff; }
        .btn-success { background:#059669; color:#fff; }
        .btn-danger { background:#dc2626; color:#fff; }
        .btn-sm { padding:7px 12px; font-size:12.5px; }
        .alert { padding:12px 14px; border-radius:10px; margin-bottom:14px; font-weight:600; }
        .alert-success { background:rgba(16,185,129,0.2); color:#6ee7b7; }
        .alert-danger { background:rgba(239,68,68,0.2); color:#fca5a5; }
        .alert-info { background:rgba(56,189,248,0.2); color:#7dd3fc; }
        .task-item { display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap; padding:12px; border:1px solid rgba(148,163,184,0.2); border-radius:12px; margin-bottom:10px; }
        .notif-item { padding:10px 12px; border-radius:10px; background:rgba(56,189,248,0.12); margin-bottom:8px; font-size:13.5px; }
    </style>
</head>
<body>
    <div class="navbar">
        <a href="/" class="brand">⚡ {{ config.SITE_NAME }}</a>
        <div class="nav-links">
            {% if user %}
                <a href="{{ url_for('dashboard') }}">Asosiy</a>
                <a href="{{ url_for('earn') }}">Pul ishlash (+{{ config.WORKER_REWARD }})</a>
                <a href="{{ url_for('advertise') }}">Buyurtma ({{ config.ADVERTISER_PRICE }})</a>
                <a href="{{ url_for('wallet') }}">Hamyon</a>
                <a href="{{ url_for('profile') }}">Akkaunt</a>
                {% if show_admin %}<a href="{{ url_for('admin_panel') }}">Admin</a>{% endif %}
                <a href="{{ url_for('logout') }}" style="color:#f87171;">Chiqish</a>
            {% else %}
                <a href="{{ url_for('login') }}">Kirish</a>
                <a href="{{ url_for('register') }}">Ro'yxatdan o'tish</a>
            {% endif %}
        </div>
    </div>
    <div class="container">
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for cat, m in messages %}
                    <div class="alert alert-{{ cat }}">{{ m }}</div>
                {% endfor %}
            {% endif %}
        {% endwith %}
        {{ content | safe }}
    </div>
</body>
</html>
"""

def render_page(html_body, user=None, **kwargs):
    show_admin = is_user_admin(user)
    inner = render_template_string(
        html_body,
        user=user,
        show_admin=show_admin,
        config=CONFIG,
        platforms=PLATFORMS,
        platform_dict=PLATFORM_DICT,
        to_web_url=to_web_url,
        **kwargs
    )
    return render_template_string(BASE_HTML, content=inner, user=user, show_admin=show_admin, config=CONFIG)

@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return render_page("""
        <div class="card" style="text-align:center; padding:36px;">
            <h1>{{ config.SITE_NAME }}</h1>
            <p style="color:#94a3b8;">Vazifalarni sayt orqali bajaring va balansga pul ishlang.</p>
            <a href="{{ url_for('login') }}" class="btn btn-primary">Kirish</a>
            <a href="{{ url_for('register') }}" class="btn btn-success">Ro'yxatdan o'tish</a>
        </div>
    """)

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        if not email or "@" not in email or len(password) < 4:
            flash("To'g'ri email va kamida 4 belgili parol kiriting!", "danger")
            return redirect(url_for("register"))
        conn = get_db()
        if conn.execute("SELECT id FROM users WHERE LOWER(email) = ?", (email,)).fetchone():
            conn.close()
            flash("Bu email allaqachon mavjud!", "info")
            return redirect(url_for("login"))
        base = "".join(ch for ch in email.split("@")[0] if ch.isalnum() or ch == "_")[:16] or "user"
        username = base
        idx = 1
        while conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone():
            username = f"{base}_{idx}"
            idx += 1
        is_adm = 1 if email == CONFIG["ADMIN_EMAIL"].lower() else 0
        cur = conn.execute(
            "INSERT INTO users (username, email, quick_token, password_hash, is_admin) VALUES (?, ?, ?, ?, ?)",
            (username, email, generate_quick_token(), generate_password_hash(password), is_adm)
        )
        conn.commit()
        conn.close()
        session["user_id"] = cur.lastrowid
        return redirect(url_for("dashboard"))
    return render_page("""
        <div class="card" style="max-width:420px; margin:0 auto;">
            <h2>Ro'yxatdan o'tish</h2>
            <form method="POST">
                <label>Email</label><input type="email" name="email" required>
                <label>Parol</label><input type="password" name="password" required>
                <button class="btn btn-primary" style="width:100%;">Ro'yxatdan o'tish</button>
            </form>
        </div>
    """)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        identifier = request.form.get("identifier", "").strip().lower()
        password = request.form.get("password", "").strip()
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE LOWER(email)=? OR LOWER(username)=?", (identifier, identifier)).fetchone()
        conn.close()
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            return redirect(url_for("dashboard"))
        flash("Email yoki parol noto'g'ri!", "danger")
    return render_page("""
        <div class="card" style="max-width:420px; margin:0 auto;">
            <h2>Kirish</h2>
            <form method="POST">
                <label>Email yoki username</label><input type="text" name="identifier" required>
                <label>Parol</label><input type="password" name="password" required>
                <button class="btn btn-success" style="width:100%;">Kirish</button>
            </form>
            <div style="margin-top:12px;"><a href="{{ url_for('forgot_password') }}" style="color:#fbbf24;">Parolni unutdingizmi?</a></div>
        </div>
    """)

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE LOWER(email) = ?", (email,)).fetchone()
        if not user:
            conn.close()
            flash("Email topilmadi!", "danger")
            return redirect(url_for("forgot_password"))
        code = f"{random.randint(100000, 999999)}"
        conn.execute("UPDATE users SET reset_code=?, reset_expires=? WHERE id=?", (code, int(time.time()) + 600, user["id"]))
        conn.commit()
        conn.close()
        ok, msg = send_reset_email(email, code)
        session["reset_email"] = email
        flash(msg, "success" if ok else "danger")
        return redirect(url_for("reset_password"))
    return render_page("""
        <div class="card" style="max-width:420px; margin:0 auto;">
            <h2>Parolni tiklash</h2>
            <form method="POST">
                <label>Email</label><input type="email" name="email" required>
                <button class="btn btn-primary" style="width:100%;">Kod yuborish</button>
            </form>
        </div>
    """)

@app.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    saved_email = session.get("reset_email", "")
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        code = request.form.get("code", "").strip()
        new_password = request.form.get("new_password", "").strip()
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE LOWER(email) = ?", (email,)).fetchone()
        if not user or code != user["reset_code"] or int(time.time()) > int(user["reset_expires"] or 0):
            conn.close()
            flash("Kod noto'g'ri yoki eskirgan!", "danger")
            return redirect(url_for("reset_password"))
        conn.execute("UPDATE users SET password_hash=?, reset_code='', reset_expires=0 WHERE id=?", (generate_password_hash(new_password), user["id"]))
        conn.commit()
        conn.close()
        flash("Parol yangilandi!", "success")
        return redirect(url_for("login"))
    return render_page("""
        <div class="card" style="max-width:420px; margin:0 auto;">
            <h2>Yangi parol</h2>
            <form method="POST">
                <label>Email</label><input type="email" name="email" value="{{ saved_email }}" required>
                <label>6 xonali kod</label><input type="text" name="code" required>
                <label>Yangi parol</label><input type="password" name="new_password" required>
                <button class="btn btn-success" style="width:100%;">Saqlash</button>
            </form>
        </div>
    """, saved_email=saved_email)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

@app.route("/dashboard")
@login_required
def dashboard(user):
    conn = get_db()
    notifs = conn.execute("SELECT * FROM notifications ORDER BY id DESC LIMIT 5").fetchall()
    conn.close()
    return render_page("""
        <div class="card">
            <h2>Salom, {{ user.username }}!</h2>
            <p>Ishlangan balans: <b>{{ user.work_balance }} so'm</b> | Reklama balansi: <b>{{ user.ad_balance }} so'm</b></p>
            <a href="{{ url_for('earn') }}" class="btn btn-success">Pul ishlash</a>
            <a href="{{ url_for('advertise') }}" class="btn btn-primary">Buyurtma berish</a>
        </div>
        {% if notifs %}
        <div class="card">
            <h3>Bildirishnomalar (Zayavkalar)</h3>
            {% for n in notifs %}<div class="notif-item">{{ n.message }}</div>{% endfor %}
        </div>
        {% endif %}
    """, user=user, notifs=notifs)

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile(user):
    if request.method == "POST":
        raw_ig = request.form.get("ig_link", "").strip()
        nick, full_link = parse_ig_link_or_nick(raw_ig)
        conn = get_db()
        conn.execute(
            "UPDATE users SET ig_link=?, ig_nick=? WHERE id=?",
            (full_link, ("@" + nick) if nick else "", user["id"])
        )
        conn.commit()
        conn.close()
        flash("Instagram akkaunt ma'lumoti saqlandi!", "success")
        return redirect(url_for("profile"))

    return render_page("""
        <div class="card">
            <h2>Instagram Akkaunt</h2>
            <p style="color:#94a3b8;">Vazifalarni tekshirish uchun foydalanadigan Instagram username yoki havolangizni kiriting.</p>
            <form method="POST">
                <label>Instagram username yoki havola</label>
                <input type="text" name="ig_link" value="{{ user.ig_link or user.ig_nick }}" placeholder="https://www.instagram.com/username/">
                <button class="btn btn-success">Saqlash</button>
            </form>
        </div>
    """, user=user)

@app.route("/earn")
@login_required
def earn(user):
    conn = get_db()
    tasks = conn.execute("""
        SELECT * FROM tasks
        WHERE status='active' AND completed_count < target_count
          AND id NOT IN (SELECT task_id FROM completions WHERE user_id=?)
        ORDER BY id DESC
    """, (user["id"],)).fetchall()
    notifs = conn.execute("SELECT * FROM notifications ORDER BY id DESC LIMIT 4").fetchall()
    conn.close()
    return render_page("""
        {% if notifs %}
        <div class="card">
            <h3>Yangi reklama zayavkalari</h3>
            {% for n in notifs %}<div class="notif-item">{{ n.message }}</div>{% endfor %}
        </div>
        {% endif %}
        <div class="card">
            <h2>Vazifalar</h2>
            {% for t in tasks %}
            <div class="task-item">
                <div>
                    <b>{{ t.title }}</b> <small style="color:#94a3b8;">({{ t.completed_count }}/{{ t.target_count }})</small>
                </div>
                <div style="display:flex; gap:8px; flex-wrap:wrap;">
                    <a href="{{ to_web_url(t.link) }}" target="_blank" rel="noopener noreferrer" onclick="fetch('/start_task/{{ t.id }}',{method:'POST'})" class="btn btn-primary btn-sm">1. Saytga o'tish</a>
                    <a href="{{ url_for('consent_verify', task_id=t.id) }}" class="btn btn-success btn-sm">2. Tasdiqlash (+{{ t.reward_per_action }})</a>
                </div>
            </div>
            {% else %}
            <p style="color:#94a3b8;">Hozircha vazifalar yo'q.</p>
            {% endfor %}
        </div>
    """, user=user, tasks=tasks, notifs=notifs)

@app.route("/start_task/<int:task_id>", methods=["POST"])
@login_required
def start_task(user, task_id):
    started = session.get("started_tasks", {})
    started[str(task_id)] = time.time()
    session["started_tasks"] = started
    return {"ok": True}

@app.route("/verify_task/<int:task_id>", methods=["GET", "POST"])
@login_required
def consent_verify(user, task_id):
    conn = get_db()
    task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not task or task["status"] != "active" or task["completed_count"] >= task["target_count"]:
        conn.close()
        flash("Vazifa tugagan!", "danger")
        return redirect(url_for("earn"))

    if request.method == "POST":
        raw_ig = request.form.get("ig_link", "").strip() or (user["ig_link"] or user["ig_nick"] or "")
        nick, full_link = parse_ig_link_or_nick(raw_ig)

        if not nick:
            conn.close()
            flash("Tasdiqlash uchun Instagram username yoki havolangizni kiriting!", "danger")
            return redirect(url_for("consent_verify", task_id=task_id))

        conn.execute(
            "UPDATE users SET ig_link=?, ig_nick=? WHERE id=?",
            (full_link, "@" + nick, user["id"])
        )
        conn.commit()

        if not verify_account_exists(task["platform"], nick):
            conn.close()
            flash("Akkaunt tekshiruvdan o'tmadi!", "danger")
            return redirect(url_for("earn"))

        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO completions (user_id, task_id, reward) VALUES (?, ?, ?)", (user["id"], task_id, task["reward_per_action"]))
            new_cnt = task["completed_count"] + 1
            conn.execute("UPDATE tasks SET completed_count=?, status=? WHERE id=?", (new_cnt, "completed" if new_cnt >= task["target_count"] else "active", task_id))
            conn.execute("UPDATE users SET work_balance = work_balance + ? WHERE id=?", (task["reward_per_action"], user["id"]))
            conn.commit()
            flash(f"+{task['reward_per_action']} so'm balansga qo'shildi!", "success")
        except sqlite3.IntegrityError:
            conn.rollback()
            flash("Bu vazifa allaqachon bajarilgan!", "danger")
        finally:
            conn.close()
        return redirect(url_for("earn"))

    conn.close()
    return render_page("""
        <div class="card" style="max-width:480px; margin:0 auto;">
            <h2>Akkaunt Orqali Tasdiqlash</h2>
            <p style="color:#94a3b8;">
                Vazifa: <b>{{ task.title }}</b><br>
                Sahifaga obuna bo‘lgan akkauntingiz username yoki havolasini ko‘rsating.
            </p>
            <form method="POST">
                <label>Sizning Instagram username yoki havolangiz</label>
                <input type="text" name="ig_link" value="{{ user.ig_link or user.ig_nick }}" required placeholder="https://www.instagram.com/username/">
                <button class="btn btn-success" style="width:100%;">Tasdiqlash</button>
            </form>
        </div>
    """, user=user, task=task)

@app.route("/advertise", methods=["GET", "POST"])
@login_required
def advertise(user):
    if request.method == "POST":
        platform = request.form.get("platform")
        title = request.form.get("title", "").strip()
        link = request.form.get("link", "").strip()
        try:
            count = int(request.form.get("count", 0))
        except ValueError:
            count = 0
        total_cost = count * CONFIG["ADVERTISER_PRICE"]
        if count < 1 or total_cost > user["ad_balance"] or not link.startswith("http"):
            flash("Ma'lumot yoki balansni tekshiring!", "danger")
            return redirect(url_for("advertise"))
        conn = get_db()
        conn.execute("UPDATE users SET ad_balance = ad_balance - ? WHERE id=?", (total_cost, user["id"]))
        cur = conn.execute(
            "INSERT INTO tasks (owner_id, platform, title, link, target_count, price_per_action, reward_per_action) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user["id"], platform, title, link, count, CONFIG["ADVERTISER_PRICE"], CONFIG["WORKER_REWARD"])
        )
        conn.execute("INSERT INTO notifications (message, task_id) VALUES (?, ?)", (f"Yangi zayafka: '{title}' ({count} ta)", cur.lastrowid))
        conn.commit()
        conn.close()
        flash("Reklama qo'shildi va bildirishnoma yuborildi!", "success")
        return redirect(url_for("advertise"))

    return render_page("""
        <div class="card">
            <h2>Buyurtma berish (Balans: {{ user.ad_balance }} so'm)</h2>
            <form method="POST">
                <label>Tarmoq</label>
                <select name="platform">{% for k, n in platforms %}<option value="{{ k }}">{{ n }}</option>{% endfor %}</select>
                <label>Sarlavha</label><input type="text" name="title" required>
                <label>Havola</label><input type="url" name="link" required placeholder="https://...">
                <label>Soni (1 tasi {{ config.ADVERTISER_PRICE }} so'm)</label><input type="number" name="count" min="1" required>
                <button class="btn btn-primary">Buyurtma berish</button>
            </form>
        </div>
    """, user=user)

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
        if action == "deposit" and amount >= CONFIG["MIN_DEPOSIT"]:
            receipt = request.files.get("receipt")
            saved = ""
            if receipt and receipt.filename and allowed_receipt(receipt.filename):
                ext = receipt.filename.rsplit(".", 1)[1].lower()
                saved = secure_filename(f"u{user['id']}_{int(time.time())}.{ext}")
                receipt.save(os.path.join(RECEIPT_UPLOAD_DIR, saved))
            conn.execute("INSERT INTO transactions (user_id, tx_type, amount, details, receipt_file) VALUES (?, 'deposit', ?, 'Chek', ?)", (user["id"], amount, saved))
            conn.commit()
            flash("Deposit so'rovi yuborildi!", "success")
        elif action == "withdraw" and amount >= CONFIG["MIN_WITHDRAW"] and amount <= user["work_balance"]:
            card = request.form.get("card", "").strip()
            conn.execute("UPDATE users SET work_balance = work_balance - ? WHERE id=?", (amount, user["id"]))
            conn.execute("INSERT INTO transactions (user_id, tx_type, amount, details) VALUES (?, 'withdraw', ?, ?)", (user["id"], amount, card))
            conn.commit()
            flash("Chiqarish so'rovi yuborildi!", "success")
        else:
            flash("Summa yoki balans noto'g'ri!", "danger")
        conn.close()
        return redirect(url_for("wallet"))

    return render_page("""
        <div class="card">
            <h3>Hisobni to'ldirish (Karta: {{ config.CARD_NUMBER }})</h3>
            <form method="POST" enctype="multipart/form-data">
                <input type="hidden" name="action" value="deposit">
                <label>Summa</label><input type="number" name="amount" min="{{ config.MIN_DEPOSIT }}" required>
                <label>Chek fayl</label><input type="file" name="receipt">
                <button class="btn btn-primary">Yuborish</button>
            </form>
        </div>
        <div class="card">
            <h3>Pul chiqarish (Balans: {{ user.work_balance }} so'm)</h3>
            <form method="POST">
                <input type="hidden" name="action" value="withdraw">
                <label>Summa (Min {{ config.MIN_WITHDRAW }})</label><input type="number" name="amount" min="{{ config.MIN_WITHDRAW }}" required>
                <label>Karta</label><input type="text" name="card" required>
                <button class="btn btn-success">Chiqarish</button>
            </form>
        </div>
    """, user=user)

@app.route("/uploads/receipts/<path:filename>")
@login_required
def get_receipt_file(user, filename):
    return send_from_directory(RECEIPT_UPLOAD_DIR, os.path.basename(filename))

@app.route("/admin", methods=["GET", "POST"])
@admin_required
def admin_panel(user):
    conn = get_db()
    if request.method == "POST":
        tx_id = request.form.get("tx_id")
        decision = request.form.get("decision")
        tx = conn.execute("SELECT * FROM transactions WHERE id=? AND status='pending'", (tx_id,)).fetchone()
        if tx:
            if decision == "approve":
                conn.execute("UPDATE transactions SET status='approved' WHERE id=?", (tx_id,))
                if tx["tx_type"] == "deposit":
                    conn.execute("UPDATE users SET ad_balance = ad_balance + ? WHERE id=?", (tx["amount"], tx["user_id"]))
            elif decision == "reject":
                conn.execute("UPDATE transactions SET status='rejected' WHERE id=?", (tx_id,))
                if tx["tx_type"] == "withdraw":
                    conn.execute("UPDATE users SET work_balance = work_balance + ? WHERE id=?", (tx["amount"], tx["user_id"]))
            conn.commit()
        return redirect(url_for("admin_panel"))

    pending = conn.execute("SELECT t.*, u.username FROM transactions t JOIN users u ON t.user_id=u.id WHERE t.status='pending'").fetchall()
    conn.close()
    return render_page("""
        <div class="card">
            <h2>Admin panel</h2>
            {% for tx in pending %}
            <div class="task-item">
                <span>{{ tx.username }} — {{ tx.tx_type }} — {{ tx.amount }} so'm</span>
                <form method="POST" style="display:flex; gap:6px; margin:0;">
                    <input type="hidden" name="tx_id" value="{{ tx.id }}">
                    <button name="decision" value="approve" class="btn btn-success btn-sm">Tasdiqlash</button>
                    <button name="decision" value="reject" class="btn btn-danger btn-sm">Rad etish</button>
                </form>
            </div>
            {% else %}<p style="color:#94a3b8;">So'rovlar yo'q.</p>{% endfor %}
        </div>
    """, user=user, pending=pending)

init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
