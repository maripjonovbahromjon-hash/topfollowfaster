import os
import json
import random
import re
import secrets
import smtplib
import sqlite3
import time
import urllib.error
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

# ================= MA'LUMOTLAR BAZASI =================
def get_db():
    conn = sqlite3.connect(CONFIG["DB_NAME"])
    conn.row_factory = sqlite3.Row
    return conn

def generate_bio_code():
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "TFF-" + "".join(random.choice(chars) for _ in range(6))

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
            ig_bio_code TEXT DEFAULT '',
            ig_verified INTEGER DEFAULT 0,
            ig_sessionid TEXT DEFAULT '',
            cookie_consent INTEGER DEFAULT 0,
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

    user_cols = [row[1] for row in c.execute("PRAGMA table_info(users)").fetchall()]
    for col, ddl in [
        ("email", "ALTER TABLE users ADD COLUMN email TEXT DEFAULT ''"),
        ("google_id", "ALTER TABLE users ADD COLUMN google_id TEXT DEFAULT ''"),
        ("quick_token", "ALTER TABLE users ADD COLUMN quick_token TEXT DEFAULT ''"),
        ("referrer_id", "ALTER TABLE users ADD COLUMN referrer_id INTEGER DEFAULT NULL"),
        ("ref_bonus_paid", "ALTER TABLE users ADD COLUMN ref_bonus_paid INTEGER DEFAULT 0"),
        ("ig_link", "ALTER TABLE users ADD COLUMN ig_link TEXT DEFAULT ''"),
        ("ig_bio_code", "ALTER TABLE users ADD COLUMN ig_bio_code TEXT DEFAULT ''"),
        ("ig_verified", "ALTER TABLE users ADD COLUMN ig_verified INTEGER DEFAULT 0"),
        ("ig_sessionid", "ALTER TABLE users ADD COLUMN ig_sessionid TEXT DEFAULT ''"),
        ("cookie_consent", "ALTER TABLE users ADD COLUMN cookie_consent INTEGER DEFAULT 0"),
        ("reset_code", "ALTER TABLE users ADD COLUMN reset_code TEXT DEFAULT ''"),
        ("reset_expires", "ALTER TABLE users ADD COLUMN reset_expires INTEGER DEFAULT 0"),
    ]:
        if col not in user_cols:
            c.execute(ddl)

    tx_cols = [row[1] for row in c.execute("PRAGMA table_info(transactions)").fetchall()]
    for col, ddl in [
        ("receipt_file", "ALTER TABLE transactions ADD COLUMN receipt_file TEXT DEFAULT ''"),
        ("admin_receipt", "ALTER TABLE transactions ADD COLUMN admin_receipt TEXT DEFAULT ''"),
    ]:
        if col not in tx_cols:
            c.execute(ddl)

    rows = c.execute("SELECT id, ig_bio_code, quick_token, ig_nick, ig_link FROM users").fetchall()
    for r in rows:
        bio_val = r["ig_bio_code"] or generate_bio_code()
        tok_val = r["quick_token"] or generate_quick_token()
        link_val = r["ig_link"] or (f"https://www.instagram.com/{r['ig_nick'].lstrip('@')}/" if r["ig_nick"] else "")
        c.execute(
            "UPDATE users SET ig_bio_code = ?, quick_token = ?, ig_link = ? WHERE id = ?",
            (bio_val, tok_val, link_val, r["id"])
        )

    admin = c.execute("SELECT id FROM users WHERE username = ?", (CONFIG["ADMIN_USER"],)).fetchone()
    if not admin:
        cur = c.execute("""
            INSERT INTO users (username, email, password_hash, quick_token, ig_bio_code, ad_balance, is_admin)
            VALUES (?, ?, ?, ?, ?, ?, 1)
        """, (CONFIG["ADMIN_USER"], CONFIG["ADMIN_EMAIL"], generate_password_hash(CONFIG["ADMIN_PASS"]), generate_quick_token(), generate_bio_code(), 10000))
        admin_id = cur.lastrowid
    else:
        admin_id = admin["id"]
        c.execute("""
            UPDATE users SET email = ?, is_admin = 1 WHERE username = ?
        """, (CONFIG["ADMIN_EMAIL"], CONFIG["ADMIN_USER"]))

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
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in ALLOWED_RECEIPT_EXTS

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

# ================= GLOBAL XATO USHLAGICH =================
@app.errorhandler(Exception)
def handle_unexpected_error(err):
    flash(f"Tizim xatoligi: {str(err)}", "danger")
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))

# ================= EMAIL YUBORISH (HTTP API + SMTP) =================
def send_reset_email(to_email, code):
    subject = f"{CONFIG['SITE_NAME']} — Parolni tiklash kodi: {code}"
    body_text = (
        f"Salom!\n\n"
        f"{CONFIG['SITE_NAME']} saytida parolni tiklash uchun tasdiqlash kodingiz: {code}\n"
        f"Kod 10 daqiqa davomida amal qiladi.\n\n"
        f"Agar siz so'rov yubormagan bo'lsangiz, bu xabarga e'tibor bermang."
    )

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
                headers={
                    "accept": "application/json",
                    "api-key": brevo_key,
                    "content-type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                if 200 <= resp.status < 300:
                    return True, "Kod emailga yuborildi!"
        except Exception as e:
            return False, f"Brevo API xatosi: {e}"

    resend_key = (os.environ.get("RESEND_API_KEY") or CONFIG["RESEND_API_KEY"] or "").strip()
    if resend_key:
        try:
            from_addr = os.environ.get("RESEND_FROM", "onboarding@resend.dev").strip()
            payload = json.dumps({
                "from": from_addr,
                "to": [to_email],
                "subject": subject,
                "text": body_text,
            }).encode("utf-8")
            req = urllib.request.Request(
                "https://api.resend.com/emails",
                data=payload,
                headers={
                    "Authorization": f"Bearer {resend_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                if 200 <= resp.status < 300:
                    return True, "Kod emailga yuborildi!"
        except Exception as e:
            return False, f"Resend API xatosi: {e}"

    smtp_email = (os.environ.get("SMTP_EMAIL") or CONFIG["SMTP_EMAIL"] or "").strip()
    smtp_pass = (os.environ.get("SMTP_APP_PASSWORD") or CONFIG["SMTP_APP_PASSWORD"] or "").replace(" ", "").strip()
    if not smtp_email or not smtp_pass:
        return False, "BREVO_API_KEY yoki SMTP_APP_PASSWORD sozlanmagan."

    msg = MIMEText(body_text, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = smtp_email
    msg["To"] = to_email

    for port in (587, 465):
        try:
            if port == 587:
                with smtplib.SMTP("smtp.gmail.com", 587, timeout=7) as server:
                    server.ehlo()
                    server.starttls()
                    server.ehlo()
                    server.login(smtp_email, smtp_pass)
                    server.sendmail(smtp_email, [to_email], msg.as_string())
            else:
                with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=7) as server:
                    server.login(smtp_email, smtp_pass)
                    server.sendmail(smtp_email, [to_email], msg.as_string())
            return True, "Kod emailga yuborildi!"
        except Exception:
            continue

    return False, "Render Free tarifida SMTP portlar bloklangan. Render Environment'ga BREVO_API_KEY qo'shing."

# ================= INSTAGRAM OBUNACHILAR RO'YXATIDAN QIDIRISH =================
def extract_ig_username(link):
    parsed = urllib.parse.urlparse(link.strip())
    parts = [p for p in parsed.path.split("/") if p]
    if not parts:
        return ""
    if parts[0].lower() in ("p", "reel", "reels", "stories", "explore"):
        return ""
    return parts[0].lstrip("@").strip().lower()

def get_ig_user_profile_api(sessionid, username):
    url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={urllib.parse.quote(username)}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/126.0 Mobile Safari/537.36",
            "X-IG-App-ID": "936619743392459",
            "Cookie": f"sessionid={sessionid.strip()};",
            "Accept": "application/json",
        }
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return (data.get("data") or {}).get("user")

def search_worker_in_target_followers(sessionid, target_username, worker_username):
    """
    Reklama bergan odamning (target_username) Instagram akkauntini topadi
    va uning obunachilari (followers) ichidan bajaruvchining (worker_username)
    akkauntini qidiradi.
    Qaytadi:
      True  -> obunachilar ichida topildi (yoki viewer obuna bo'lgani tasdiqlandi)
      False -> aniq topilmadi / obuna bo'linmagan
      None  -> Instagram vaqtinchalik chekladi yoki sessionid yaroqsiz
    """
    clean_target = (target_username or "").lstrip("@").strip().lower()
    clean_worker = (worker_username or "").lstrip("@").strip().lower()
    if not sessionid or not clean_target:
        return None

    try:
        target_user = get_ig_user_profile_api(sessionid, clean_target)
        if not target_user:
            return None
    except Exception:
        return None

    target_id = str(target_user.get("id") or "")
    followed_by_viewer = bool(target_user.get("followed_by_viewer") or target_user.get("requested_by_viewer"))

    # 1-usul: Reklama beruvchining obunachilari (followers) ro'yxatidan worker_username ni qidirish
    if target_id and clean_worker:
        max_id = ""
        for _ in range(5):  # 5 sahifagacha (~250 ta obunachi ichidan qidiradi)
            q = f"https://www.instagram.com/api/v1/friendships/{urllib.parse.quote(target_id)}/followers/?count=50"
            if max_id:
                q += f"&max_id={urllib.parse.quote(str(max_id))}"
            try:
                req = urllib.request.Request(
                    q,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/126.0 Mobile Safari/537.36",
                        "X-IG-App-ID": "936619743392459",
                        "Cookie": f"sessionid={sessionid.strip()};",
                        "Accept": "application/json",
                    }
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    fdata = json.loads(resp.read().decode("utf-8"))
                users_list = fdata.get("users") or []
                for u in users_list:
                    uname = (u.get("username") or "").strip().lower()
                    if uname == clean_worker:
                        return True
                if not fdata.get("big_list") or not fdata.get("next_max_id"):
                    # Obunachilar ro'yxati oxirigacha tekshirildi va worker topilmadi
                    return False if not followed_by_viewer else True
                max_id = str(fdata.get("next_max_id"))
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    return None
                break
            except Exception:
                break

    # 2-usul: Agar followers ro'yxati cheklansa, viewer holatini tekshirish
    if followed_by_viewer:
        return True
    return None

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

@app.route("/uploads/receipts/<path:filename>")
@login_required
def get_receipt_file(user, filename):
    safe_name = os.path.basename(filename)
    conn = get_db()
    tx = conn.execute("SELECT * FROM transactions WHERE receipt_file = ?", (safe_name,)).fetchone()
    conn.close()
    if not tx:
        return "Chek topilmadi", 404
    if not is_user_admin(user) and tx["user_id"] != user["id"]:
        return "Ruxsat berilmagan", 403
    return send_from_directory(RECEIPT_UPLOAD_DIR, safe_name)

@app.route("/go/<token>")
def quick_link_login(token):
    clean_tok = (token or "").strip()
    if not clean_tok:
        return redirect(url_for("login"))
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE quick_token = ?", (clean_tok,)).fetchone()
    conn.close()
    if not user or user["is_banned"]:
        flash("Tezkor kirish havolasi eskirgan yoki noto'g'ri!", "danger")
        return redirect(url_for("login"))
    session["user_id"] = user["id"]
    flash(f"Shaxsiy silka orqali {user['username']} akkauntiga kirdingiz!", "success")
    return redirect(url_for("profile"))

# ================= ZAMONAVIY HTML SHABLON =================
BASE_HTML = """
<!DOCTYPE html>
<html lang="uz">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ config.SITE_NAME }} — Zamonaviy SMM Platforma</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-main: #070b14;
            --bg-card: rgba(17, 25, 40, 0.82);
            --bg-input: #0b1120;
            --border: rgba(148, 163, 184, 0.16);
            --primary-grad: linear-gradient(135deg, #0ea5e9 0%, #6366f1 100%);
            --success-grad: linear-gradient(135deg, #10b981 0%, #059669 100%);
            --ig-grad: linear-gradient(45deg, #f09433 0%, #e6683c 25%, #dc2743 50%, #cc2366 75%, #bc1888 100%);
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Plus Jakarta Sans', sans-serif; }
        body {
            background-color: var(--bg-main);
            background-image:
                radial-gradient(at 0% 0%, rgba(14, 165, 233, 0.14) 0px, transparent 50%),
                radial-gradient(at 100% 0%, rgba(99, 102, 241, 0.14) 0px, transparent 50%),
                radial-gradient(at 50% 100%, rgba(16, 185, 129, 0.08) 0px, transparent 50%);
            color: var(--text-main);
            min-height: 100vh;
            line-height: 1.55;
            padding-bottom: 50px;
        }
        .navbar {
            position: sticky;
            top: 0;
            z-index: 100;
            background: rgba(7, 11, 20, 0.88);
            backdrop-filter: blur(16px);
            padding: 14px 22px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--border);
            flex-wrap: wrap;
            gap: 12px;
        }
        .brand {
            font-size: 21px;
            font-weight: 800;
            background: linear-gradient(135deg, #38bdf8, #818cf8);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            text-decoration: none;
        }
        .nav-links { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
        .nav-links a {
            color: #cbd5e1;
            text-decoration: none;
            font-size: 13.5px;
            font-weight: 600;
            padding: 8px 13px;
            border-radius: 10px;
            transition: all 0.2s ease;
        }
        .nav-links a:hover { background: rgba(56, 189, 248, 0.12); color: #fff; }
        .container { max-width: 1020px; margin: 26px auto; padding: 0 16px; }
        .card {
            background: var(--bg-card);
            backdrop-filter: blur(14px);
            border: 1px solid var(--border);
            border-radius: 18px;
            padding: 22px;
            margin-bottom: 18px;
            box-shadow: 0 12px 32px rgba(0, 0, 0, 0.32);
        }
        .hero {
            text-align: center;
            padding: 46px 24px;
            background: linear-gradient(180deg, rgba(14, 165, 233, 0.12) 0%, rgba(17, 25, 40, 0.85) 100%);
        }
        .hero h1 { font-size: clamp(26px, 4vw, 38px); font-weight: 800; line-height: 1.2; margin-bottom: 14px; }
        .hero-pill {
            display: inline-block;
            padding: 6px 14px;
            border-radius: 999px;
            background: rgba(56, 189, 248, 0.15);
            border: 1px solid rgba(56, 189, 248, 0.35);
            color: #7dd3fc;
            font-size: 12.5px;
            font-weight: 700;
            margin-bottom: 14px;
        }
        .grid-2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; }
        .grid-3 { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 14px; }
        .stat-box {
            background: rgba(11, 17, 32, 0.85);
            border: 1px solid var(--border);
            padding: 18px;
            border-radius: 15px;
        }
        .stat-title { font-size: 13px; color: var(--text-muted); font-weight: 600; }
        .stat-val { font-size: 25px; font-weight: 800; color: #38bdf8; margin-top: 6px; }
        .stat-val.green { color: #34d399; }
        input, select, textarea {
            width: 100%;
            padding: 12px 14px;
            margin-top: 6px;
            margin-bottom: 15px;
            background: var(--bg-input);
            border: 1px solid rgba(148, 163, 184, 0.25);
            border-radius: 11px;
            color: #fff;
            font-size: 14px;
        }
        input:focus, select:focus, textarea:focus {
            outline: none;
            border-color: #38bdf8;
            box-shadow: 0 0 0 3px rgba(56, 189, 248, 0.18);
        }
        .sm-input { width: auto; min-width: 155px; padding: 7px 11px; margin: 0; font-size: 13px; }
        label { font-size: 13px; color: #cbd5e1; font-weight: 600; display: block; }
        .btn {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            padding: 11px 18px;
            border: none;
            border-radius: 11px;
            font-weight: 700;
            font-size: 14px;
            cursor: pointer;
            text-decoration: none;
            transition: transform 0.15s ease, opacity 0.2s ease;
        }
        .btn:hover { transform: translateY(-1px); opacity: 0.95; }
        .btn-primary { background: var(--primary-grad); color: #fff; }
        .btn-success { background: var(--success-grad); color: #fff; }
        .btn-ig { background: var(--ig-grad); color: #fff; }
        .btn-danger { background: linear-gradient(135deg, #ef4444, #dc2626); color: #fff; }
        .btn-warning { background: linear-gradient(135deg, #f59e0b, #d97706); color: #fff; }
        .btn-google { background: #ffffff; color: #0f172a; }
        .btn-sm { padding: 7px 13px; font-size: 13px; border-radius: 9px; }
        .alert { padding: 13px 16px; border-radius: 12px; margin-bottom: 16px; font-size: 14px; font-weight: 600; }
        .alert-success { background: rgba(16, 185, 129, 0.15); color: #6ee7b7; border: 1px solid rgba(16, 185, 129, 0.4); }
        .alert-danger { background: rgba(239, 68, 68, 0.15); color: #fca5a5; border: 1px solid rgba(239, 68, 68, 0.4); }
        .alert-info { background: rgba(14, 165, 233, 0.15); color: #7dd3fc; border: 1px solid rgba(14, 165, 233, 0.4); }
        .task-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 15px;
            flex-wrap: wrap;
            background: rgba(11, 17, 32, 0.65);
            border: 1px solid var(--border);
            border-radius: 14px;
            padding: 15px;
            margin-bottom: 10px;
        }
        .badge {
            display: inline-block;
            padding: 4px 10px;
            border-radius: 8px;
            font-size: 11.5px;
            font-weight: 700;
            background: rgba(56, 189, 248, 0.15);
            color: #38bdf8;
        }
        .badge-ig { background: rgba(220, 39, 67, 0.18); color: #fda4af; }
        .badge-ok { background: rgba(16, 185, 129, 0.18); color: #6ee7b7; }
        .receipt-thumb {
            width: 84px;
            height: 84px;
            object-fit: cover;
            border-radius: 10px;
            border: 1px solid var(--border);
            display: block;
        }
        table { width: 100%; border-collapse: collapse; font-size: 13.5px; margin-top: 10px; }
        th, td { padding: 11px 10px; text-align: left; border-bottom: 1px solid var(--border); vertical-align: middle; }
        th { color: var(--text-muted); font-weight: 700; font-size: 12px; text-transform: uppercase; }
        .table-wrap { overflow-x: auto; }
        .ref-box {
            background: var(--bg-input);
            border: 1px dashed #38bdf8;
            padding: 12px 14px;
            border-radius: 11px;
            font-size: 13px;
            color: #7dd3fc;
            word-break: break-all;
            margin-top: 10px;
        }
        .notif-item {
            padding: 10px 14px;
            border-radius: 11px;
            background: rgba(14, 165, 233, 0.1);
            border: 1px solid rgba(56, 189, 248, 0.25);
            font-size: 13.5px;
            margin-bottom: 8px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 10px;
            flex-wrap: wrap;
        }
        .consent-card {
            background: rgba(245, 158, 11, 0.1);
            border: 1px solid rgba(245, 158, 11, 0.45);
            border-radius: 14px;
            padding: 16px;
            margin-bottom: 16px;
        }
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
                <a href="{{ url_for('profile') }}" style="border:1px solid rgba(220,39,67,0.45); color:#fda4af;">
                    Instagram & Cookie Ruxsati
                </a>
                {% if show_admin %}
                    <a href="{{ url_for('admin_panel') }}" style="background:rgba(234,88,12,0.25); color:#fdba74; border:1px solid rgba(234,88,12,0.5);">Admin</a>
                {% endif %}
                <a href="{{ url_for('logout') }}" style="color:#f87171;">Chiqish</a>
            {% else %}
                <a href="{{ url_for('google_login') }}" class="btn btn-google btn-sm">Google orqali kirish</a>
                <a href="{{ url_for('login') }}">Kirish</a>
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
        to_web_url=to_web_url,
        **kwargs
    )
    return render_template_string(BASE_HTML, content=inner, user=user, show_admin=show_admin, config=CONFIG)

# ================= GOOGLE OAUTH =================
def get_google_redirect_uri():
    return url_for("google_callback", _external=True)

@app.route("/auth/google")
def google_login():
    if not CONFIG["GOOGLE_CLIENT_ID"] or not CONFIG["GOOGLE_CLIENT_SECRET"]:
        flash("Google OAuth kalitlari hali kiritilmagan — pastdagi Gmail formasi orqali kiring yoki ro'yxatdan o'ting.", "info")
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
            "UPDATE users SET email = COALESCE(NULLIF(email, ''), ?), google_id = COALESCE(NULLIF(google_id, ''), ?), quick_token = COALESCE(NULLIF(quick_token, ''), ?), is_admin = CASE WHEN ? = 1 THEN 1 ELSE is_admin END WHERE id = ?",
            (email, google_id, generate_quick_token(), should_be_admin, user["id"])
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
            INSERT INTO users (username, email, google_id, quick_token, password_hash, referrer_id, ig_bio_code, is_admin)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (username, email, google_id, generate_quick_token(), generate_password_hash(os.urandom(16).hex()), referrer_id, generate_bio_code(), should_be_admin))
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
    <div class="card hero">
        <div class="hero-pill">⚡ Tezkor SMM & Daromad Birjasi</div>
        <h1>{{ config.SITE_NAME }} — Haqiqiy Obunachilar va Kunlik Daromad</h1>
        <p style="color:#cbd5e1; max-width:650px; margin:0 auto 24px; font-size:15px;">
            Instagram silkangizni ulab, reklama bergan sahifaning obunachilari orasidan tekshiruvdan o'ting va har bir obuna uchun <b>{{ config.WORKER_REWARD }} so'm</b> ishlang yoki o'z sahifangizga <b>{{ config.ADVERTISER_PRICE }} so'm</b>dan obunachilar yig'ing!
        </p>
        <div style="display:flex; gap:12px; justify-content:center; flex-wrap:wrap;">
            <a href="{{ url_for('google_login') }}" class="btn btn-google">Google orqali kirish</a>
            <a href="{{ url_for('register') }}" class="btn btn-primary">Ro'yxatdan o'tish</a>
            <a href="{{ url_for('login') }}" class="btn btn-success">Kirish</a>
        </div>
    </div>

    <div class="grid-3">
        <div class="stat-box" style="text-align:center;">
            <div class="stat-title">Jami foydalanuvchilar</div>
            <div class="stat-val">{{ users_count }} ta</div>
        </div>
        <div class="stat-box" style="text-align:center;">
            <div class="stat-title">Bajarilgan obunalar</div>
            <div class="stat-val green">{{ tasks_done }} ta</div>
        </div>
        <div class="stat-box" style="text-align:center;">
            <div class="stat-title">1 obuna mukofoti</div>
            <div class="stat-val">{{ config.WORKER_REWARD }} so'm</div>
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
        password = request.form.get("password", "").strip()

        if not email or "@" not in email:
            flash("Iltimos, to'g'ri Google email (Gmail) kiriting!", "danger")
            return redirect(url_for("register"))

        if len(password) < 4:
            flash("Parol kamida 4 ta belgidan iborat bo'lsin!", "danger")
            return redirect(url_for("register"))

        conn = get_db()
        existing_email = conn.execute("SELECT * FROM users WHERE LOWER(email) = ?", (email,)).fetchone()
        if existing_email:
            conn.close()
            flash("Bu email allaqachon mavjud! Parolni unutgan bo'lsangiz 'Parolni unutdingizmi?' orqali tiklang.", "info")
            return redirect(url_for("login"))

        base = "".join(ch for ch in email.split("@")[0] if ch.isalnum() or ch == "_")[:16] or "user"
        username = base
        idx = 1
        while conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone():
            username = f"{base}_{idx}"
            idx += 1

        referrer_id = None
        ref_code = session.get("ref")
        if ref_code:
            ref_user = conn.execute("SELECT id FROM users WHERE username = ?", (ref_code,)).fetchone()
            if ref_user:
                referrer_id = ref_user["id"]

        is_adm = 1 if email == CONFIG["ADMIN_EMAIL"].lower() else 0

        try:
            cur = conn.execute("""
                INSERT INTO users (username, email, quick_token, password_hash, referrer_id, ig_bio_code, is_admin)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (username, email, generate_quick_token(), generate_password_hash(password), referrer_id, generate_bio_code(), is_adm))
            conn.commit()
            session.pop("ref", None)
            session["user_id"] = cur.lastrowid
            flash("Ro'yxatdan o'tdingiz! Endi Instagram havolasini ulang va cookie tekshiruviga ruxsat bering.", "success")
            return redirect(url_for("profile"))
        except sqlite3.IntegrityError:
            flash("Ro'yxatdan o'tishda xatolik yuz berdi. Qayta urinib ko'ring.", "danger")
        finally:
            conn.close()

    html = """
    <div class="card" style="max-width:440px; margin:0 auto;">
        <h2 style="margin-bottom:8px;">Tezkor ro'yxatdan o'tish</h2>
        <p style="font-size:13px; color:#94a3b8; margin-bottom:16px;">
            Saytdan ro'yxatdan o'ting va akkauntingizni 10 kun davomida saqlab turing.
        </p>
        <a href="{{ url_for('google_login') }}" class="btn btn-google" style="width:100%; margin-bottom:16px;">Google OAuth orqali 1 bosishda kirish</a>
        <form method="POST">
            <label>Google email (Gmail) *</label>
            <input type="email" name="email" required placeholder="sizning.email@gmail.com">

            <label>Sayt uchun parol yarating *</label>
            <input type="password" name="password" required placeholder="Kamida 4 ta belgi">

            <button type="submit" class="btn btn-primary" style="width:100%;">Ro'yxatdan o'tish</button>
        </form>
        <p style="margin-top:14px; font-size:13px; text-align:center; color:#94a3b8;">
            Akkauntingiz bormi? <a href="{{ url_for('login') }}" style="color:#38bdf8; font-weight:700;">Kirish</a>
        </p>
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
        flash("Email yoki parol noto'g'ri! Parolni unutgan bo'lsangiz 'Parolni unutdingizmi?' tugmasini bosing.", "danger")

    html = """
    <div class="card" style="max-width:420px; margin:0 auto;">
        <h2 style="margin-bottom:14px;">Kabinetga kirish</h2>
        <a href="{{ url_for('google_login') }}" class="btn btn-google" style="width:100%; margin-bottom:16px;">Google OAuth orqali kirish</a>
        <form method="POST">
            <label>Google email (yoki login)</label>
            <input type="text" name="identifier" required placeholder="example@gmail.com">
            <label>Parol</label>
            <input type="password" name="password" required placeholder="Parolingiz">
            <button type="submit" class="btn btn-success" style="width:100%;">Kabinetga kirish</button>
        </form>
        <div style="margin-top:16px; display:flex; justify-content:space-between; font-size:13px;">
            <a href="{{ url_for('forgot_password') }}" style="color:#fbbf24; font-weight:700;">Parolni unutdingizmi?</a>
            <a href="{{ url_for('register') }}" style="color:#38bdf8; font-weight:700;">Ro'yxatdan o'tish</a>
        </div>
    </div>
    """
    return render_page(html)

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE LOWER(email) = ?", (email,)).fetchone()
        if not user:
            conn.close()
            flash("Bu email bilan ro'yxatdan o'tilgan foydalanuvchi topilmadi!", "danger")
            return redirect(url_for("forgot_password"))

        code = f"{random.randint(100000, 999999)}"
        expires = int(time.time()) + 600
        conn.execute("UPDATE users SET reset_code = ?, reset_expires = ? WHERE id = ?", (code, expires, user["id"]))
        conn.commit()
        conn.close()

        ok, info_msg = send_reset_email(email, code)
        session["reset_email"] = email
        if ok:
            flash("6 xonali tasdiqlash kodi emailingizga yuborildi! Kodni kiriting va yangi parol o'rnating.", "success")
        else:
            flash(f"Email yuborilmadi ({info_msg}).", "danger")
        return redirect(url_for("reset_password"))

    html = """
    <div class="card" style="max-width:420px; margin:0 auto;">
        <h2 style="margin-bottom:10px;">Parolni tiklash</h2>
        <p style="font-size:13px; color:#94a3b8; margin-bottom:14px;">
            Ro'yxatdan o'tgan Google emailingizni kiriting — pochtangizga 6 xonali tasdiqlash kodi boradi.
        </p>
        <form method="POST">
            <label>Google email (Gmail)</label>
            <input type="email" name="email" required placeholder="sizning.email@gmail.com">
            <button type="submit" class="btn btn-primary" style="width:100%;">Kod yuborish</button>
        </form>
        <p style="margin-top:14px; font-size:13px; text-align:center;">
            <a href="{{ url_for('login') }}" style="color:#38bdf8;">← Kirish sahifasiga qaytish</a>
        </p>
    </div>
    """
    return render_page(html)

@app.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    saved_email = session.get("reset_email", "")
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        code = request.form.get("code", "").strip()
        new_password = request.form.get("new_password", "").strip()

        if len(new_password) < 4:
            flash("Yangi parol kamida 4 ta belgidan iborat bo'lsin!", "danger")
            return redirect(url_for("reset_password"))

        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE LOWER(email) = ?", (email,)).fetchone()
        if not user or not user["reset_code"]:
            conn.close()
            flash("Avval emailga kod yuborishni bosing!", "danger")
            return redirect(url_for("forgot_password"))

        if int(time.time()) > int(user["reset_expires"] or 0):
            conn.close()
            flash("Kod muddati tugagan (10 daqiqa). Qaytadan kod yuboring!", "danger")
            return redirect(url_for("forgot_password"))

        if code != user["reset_code"]:
            conn.close()
            flash("Emailga kelgan tasdiqlash kodi noto'g'ri!", "danger")
            return redirect(url_for("reset_password"))

        conn.execute(
            "UPDATE users SET password_hash = ?, reset_code = '', reset_expires = 0 WHERE id = ?",
            (generate_password_hash(new_password), user["id"])
        )
        conn.commit()
        conn.close()
        session.pop("reset_email", None)
        flash("Parolingiz muvaffaqiyatli yangilandi! Endi yangi parol bilan kiring.", "success")
        return redirect(url_for("login"))

    html = """
    <div class="card" style="max-width:420px; margin:0 auto;">
        <h2 style="margin-bottom:10px;">Yangi parol o'rnatish</h2>
        <p style="font-size:13px; color:#94a3b8; margin-bottom:14px;">
            Emailingizga kelgan 6 xonali kodni va yangi parolni kiriting.
        </p>
        <form method="POST">
            <label>Google email</label>
            <input type="email" name="email" value="{{ saved_email }}" required placeholder="sizning.email@gmail.com">

            <label>Emailga kelgan 6 xonali kod</label>
            <input type="text" name="code" maxlength="6" required placeholder="Masalan: 482910">

            <label>Yangi parol</label>
            <input type="password" name="new_password" required placeholder="Kamida 4 ta belgi">

            <button type="submit" class="btn btn-success" style="width:100%;">Tasdiqlash va parolni saqlash</button>
        </form>
    </div>
    """
    return render_page(html, saved_email=saved_email)

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
    recent_notifs = conn.execute("SELECT * FROM notifications ORDER BY id DESC LIMIT 5").fetchall()
    conn.close()

    ref_link = request.url_root.rstrip("/") + url_for("register", ref=user["username"])
    quick_link = request.url_root.rstrip("/") + url_for("quick_link_login", token=user["quick_token"])

    html = """
    <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px; margin-bottom:16px;">
        <h2>Salom, {{ user.username }}! 👋</h2>
        <div style="display:flex; gap:8px; flex-wrap:wrap; align-items:center;">
            {% if user.cookie_consent and user.ig_sessionid %}
                <span class="badge badge-ok">🍪 Cookie ruxsat berilgan</span>
            {% else %}
                <a href="{{ url_for('profile') }}" class="btn btn-warning btn-sm">🍪 Cookie tekshiruviga ruxsat berish</a>
            {% endif %}
            {% if user.ig_link %}
                <a href="{{ user.ig_link }}" target="_blank" class="badge badge-ok">📸 {{ user.ig_nick or user.ig_link }}</a>
            {% endif %}
        </div>
    </div>

    {% if recent_notifs %}
    <div class="card">
        <h3 style="margin-bottom:10px;">🔔 Yangi reklama zayavkalari (bildirishnomalar)</h3>
        {% for n in recent_notifs %}
            <div class="notif-item">
                <span>{{ n.message }}</span>
                <a href="{{ url_for('earn') }}" class="btn btn-success btn-sm">Vazifaga o'tish</a>
            </div>
        {% endfor %}
    </div>
    {% endif %}

    <div class="grid-2">
        <div class="stat-box">
            <div class="stat-title">Ishlangan balans (Min. yechish: {{ config.MIN_WITHDRAW }} so'm)</div>
            <div class="stat-val green">{{ user.work_balance }} so'm</div>
            <a href="{{ url_for('earn') }}" class="btn btn-success btn-sm" style="margin-top:12px;">Pul ishlash (+{{ config.WORKER_REWARD }} so'm)</a>
        </div>
        <div class="stat-box">
            <div class="stat-title">Reklama balansi (Deposit)</div>
            <div class="stat-val">{{ user.ad_balance }} so'm</div>
            <a href="{{ url_for('wallet') }}" class="btn btn-primary btn-sm" style="margin-top:12px;">Hisob to'ldirish</a>
        </div>
    </div>

    <div class="card" style="margin-top:16px;">
        <h3>🔗 Shaxsiy tezkor kirish silkangiz (10 kunlik sessiya faol)</h3>
        <div class="ref-box">{{ quick_link }}</div>

        <h3 style="margin-top:16px;">🎁 Referal tizimi (Har bir do'st uchun +{{ config.REFERRAL_BONUS }} so'm)</h3>
        <p style="font-size:13.5px; color:#cbd5e1; margin-top:6px;">
            Do'stingiz havolangiz orqali kirib ilk vazifani bajarganda balansingizga <b>+{{ config.REFERRAL_BONUS }} so'm</b> tushadi. Chaqirilgan do'stlar: <b>{{ refs_count }} ta</b>
        </p>
        <div class="ref-box">{{ ref_link }}</div>
    </div>

    <div class="grid-2">
        <div class="stat-box">
            <div class="stat-title">Siz bajargan obunalar</div>
            <div class="stat-val">{{ my_done }} ta</div>
        </div>
        <div class="stat-box">
            <div class="stat-title">Siz bergan buyurtmalar</div>
            <div class="stat-val">{{ my_tasks }} ta</div>
        </div>
    </div>
    """
    return render_page(html, user=user, my_done=my_done, my_tasks=my_tasks, refs_count=refs_count, ref_link=ref_link, quick_link=quick_link, recent_notifs=recent_notifs)

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile(user):
    quick_token = user["quick_token"] or generate_quick_token()
    conn = get_db()
    if not user["quick_token"]:
        conn.execute("UPDATE users SET quick_token = ? WHERE id = ?", (quick_token, user["id"]))
        conn.commit()

    if request.method == "POST":
        action = request.form.get("action", "")

        if action == "grant_cookie_consent":
            ig_sessionid = request.form.get("ig_sessionid", "").strip()
            raw_ig = request.form.get("ig_link_input", "").strip()
            nick, full_link = parse_ig_link_or_nick(raw_ig)
            if not nick:
                conn.close()
                flash("Obunachilar orasidan topilish uchun o'zingizning Instagram silkangiz yoki username'ingizni kiriting!", "danger")
                return redirect(url_for("profile"))
            if not ig_sessionid:
                conn.close()
                flash("Ruxsat berish uchun Instagram sessionid cookie qiymatini kiriting!", "danger")
                return redirect(url_for("profile"))
            formatted_nick = "@" + nick
            conn.execute(
                "UPDATE users SET cookie_consent = 1, ig_sessionid = ?, ig_link = ?, ig_nick = ?, ig_verified = 1 WHERE id = ?",
                (ig_sessionid, full_link, formatted_nick, user["id"])
            )
            conn.commit()
            conn.close()
            flash(f"Ruxsat berildi! Tekshiruvda reklama beruvchi sahifasidan @{nick} qidiriladi.", "success")
            return redirect(url_for("earn"))

        elif action == "deny_cookie_consent":
            conn.execute(
                "UPDATE users SET cookie_consent = 0, ig_sessionid = '' WHERE id = ?",
                (user["id"],)
            )
            conn.commit()
            conn.close()
            flash("Siz cookie tekshiruviga ruxsat bermadingiz (yoki bekor qildingiz). Instagram vazifalari tasdiqlanmaydi!", "danger")
            return redirect(url_for("profile"))

        elif action == "save_ig_link":
            raw_ig = request.form.get("ig_link_input", "").strip()
            nick, full_link = parse_ig_link_or_nick(raw_ig)
            if not full_link:
                conn.close()
                flash("Instagram silkasi yoki username kiriting!", "danger")
                return redirect(url_for("profile"))
            formatted_nick = ("@" + nick) if nick else ""
            conn.execute(
                "UPDATE users SET ig_link = ?, ig_nick = ? WHERE id = ?",
                (full_link, formatted_nick, user["id"])
            )
            conn.commit()
            conn.close()
            flash(f"Instagram havolasi ulandi: {full_link}", "success")
            return redirect(url_for("profile"))

        elif action == "new_quick_link":
            new_tok = generate_quick_token()
            conn.execute("UPDATE users SET quick_token = ? WHERE id = ?", (new_tok, user["id"]))
            conn.commit()
            conn.close()
            flash("Yangi shaxsiy kirish silkasi yaratildi!", "info")
            return redirect(url_for("profile"))

        elif action == "change_pass":
            new_pass = request.form.get("new_pass", "").strip()
            if len(new_pass) < 4:
                flash("Yangi parol kamida 4 ta belgi bo'lsin!", "danger")
            else:
                conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(new_pass), user["id"]))
                conn.commit()
                flash("Sayt paroli yangilandi!", "success")
            conn.close()
            return redirect(url_for("profile"))

        else:
            yt_nick = request.form.get("yt_nick", "").strip()
            tg_nick = request.form.get("tg_nick", "").strip()
            conn.execute(
                "UPDATE users SET yt_nick=?, tg_nick=? WHERE id=?",
                (yt_nick, tg_nick, user["id"])
            )
            conn.commit()
            conn.close()
            flash("Sozlamalar saqlandi!", "success")
            return redirect(url_for("profile"))

    conn.close()
    quick_link = request.url_root.rstrip("/") + url_for("quick_link_login", token=quick_token)

    html = """
    <div class="consent-card">
        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px; margin-bottom:8px;">
            <h3>🍪 Cookie tekshiruvi va Obunachilar ichidan qidirish ruxsati</h3>
            {% if user.cookie_consent and user.ig_sessionid %}
                <span class="badge badge-ok">✅ Ruxsat berilgan</span>
            {% else %}
                <span class="badge badge-ig">⛔ Ruxsat berilmagan</span>
            {% endif %}
        </div>
        <p style="font-size:13.5px; color:#e2e8f0; margin-bottom:14px;">
            Siz o'z Instagram silkangizni kiritasiz. Reklama bergan odamning sahifasiga kirib obuna bosganingizdan so'ng, tizim reklama beruvchining <b>obunachilari orasidan sizning Instagram akkauntingizni</b> qidiradi.
            <b>Cookie ruxsati bermasangiz, vazifa tasdiqlanmaydi.</b>
        </p>

        <form method="POST">
            <input type="hidden" name="action" value="grant_cookie_consent">
            <label>Sizning Instagram silkangiz (yoki username) *</label>
            <input type="text" name="ig_link_input" value="{{ user.ig_link or user.ig_nick }}" required placeholder="https://www.instagram.com/sizning_nik/">

            <label>Instagram Session ID cookie *</label>
            <input type="text" name="ig_sessionid" value="{{ user.ig_sessionid }}" required placeholder="Instagram saytidagi sessionid cookie qiymati">

            <div style="display:flex; gap:10px; flex-wrap:wrap;">
                <button type="submit" class="btn btn-success">✅ Ruxsat beraman va saqlayman</button>
            </div>
        </form>

        <form method="POST" style="margin-top:10px;">
            <input type="hidden" name="action" value="deny_cookie_consent">
            <button type="submit" class="btn btn-danger btn-sm">❌ Ruxsat bermayman (cookie o'chirilsin)</button>
        </form>
    </div>

    <div class="grid-2">
        <div class="card">
            <h3 style="margin-bottom:10px;">📸 Instagram Silkasi & Shaxsiy Kirish</h3>
            <form method="POST">
                <input type="hidden" name="action" value="save_ig_link">
                <label>Instagram silkasi</label>
                <input type="text" name="ig_link_input" value="{{ user.ig_link or user.ig_nick }}" required placeholder="https://www.instagram.com/username/">
                <button type="submit" class="btn btn-ig" style="width:100%;">Silkani saqlash</button>
            </form>

            <hr style="border-color:rgba(148,163,184,0.16); margin:18px 0;">
            <h4 style="margin-bottom:6px;">🔗 Shaxsiy kirish silkasi</h4>
            <div class="ref-box">{{ quick_link }}</div>
            <form method="POST" style="margin-top:10px;">
                <input type="hidden" name="action" value="new_quick_link">
                <button type="submit" class="btn btn-sm" style="background:rgba(148,163,184,0.18); color:#fff;">🔄 Yangi kirish silkasi yaratish</button>
            </form>
        </div>

        <div class="card">
            <h3 style="margin-bottom:10px;">⚙️ YouTube & Telegram akkauntlari</h3>
            <form method="POST">
                <input type="hidden" name="action" value="extras">
                <label>YouTube kanal / akkaunt</label>
                <input type="text" name="yt_nick" value="{{ user.yt_nick }}" placeholder="@youtube_nik">

                <label>Telegram username</label>
                <input type="text" name="tg_nick" value="{{ user.tg_nick }}" placeholder="@telegram_nik">

                <button type="submit" class="btn btn-primary" style="width:100%;">Saqlash</button>
            </form>

            <hr style="border-color:rgba(148,163,184,0.16); margin:18px 0;">
            <h4 style="margin-bottom:10px;">🔑 Sayt parolini o'zgartirish</h4>
            <form method="POST">
                <input type="hidden" name="action" value="change_pass">
                <label>Yangi parol</label>
                <input type="password" name="new_pass" required placeholder="Kamida 4 ta belgi">
                <button type="submit" class="btn btn-warning btn-sm">Parolni yangilash</button>
            </form>
        </div>
    </div>
    """
    return render_page(html, user=user, quick_link=quick_link)

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
    notifs = conn.execute("SELECT * FROM notifications ORDER BY id DESC LIMIT 4").fetchall()
    conn.close()

    html = """
    {% if not (user.cookie_consent and user.ig_sessionid) %}
    <div class="consent-card">
        <h3>🍪 Diqqat: Cookie tekshiruvi ruxsati berilmagan!</h3>
        <p style="font-size:13.5px; color:#e2e8f0; margin:6px 0 12px;">
            Instagram vazifalarini tasdiqlash va reklama beruvchi obunachilari orasidan sizning akkauntingizni qidirish uchun cookie ruxsati bering. Ruxsat bermasangiz vazifa tasdiqlanmaydi.
        </p>
        <a href="{{ url_for('profile') }}" class="btn btn-warning btn-sm">🍪 Ruxsat berish sahifasiga o'tish</a>
    </div>
    {% endif %}

    {% if notifs %}
    <div class="card">
        <h3 style="margin-bottom:8px;">🔔 Yangi reklama zayavkalari</h3>
        {% for n in notifs %}
            <div class="notif-item"><span>{{ n.message }}</span></div>
        {% endfor %}
    </div>
    {% endif %}

    <div class="card">
        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
            <h2>Vazifalar birjasi</h2>
            <div style="display:flex; gap:6px; flex-wrap:wrap;">
                <a href="{{ url_for('earn', platform='all') }}" class="btn btn-sm btn-primary">Barchasi</a>
                {% for key, name in platforms %}
                    <a href="{{ url_for('earn', platform=key) }}" class="btn btn-sm" style="background:rgba(148,163,184,0.16);color:#fff;">{{ name.split(' ')[0] }}</a>
                {% endfor %}
            </div>
        </div>
        <p style="font-size:13.5px; color:#94a3b8; margin-top:10px;">
            1. <b>"1. Saytga o'tish"</b> tugmasini bosing — reklama beruvchining sahifasi ochiladi.<br>
            2. Obuna bo'lib, saytga qayting va <b>"2. Tasdiqlash (+{{ config.WORKER_REWARD }} so'm)"</b> tugmasini bosing — reklama beruvchi obunachilari ichidan sizning akkauntingiz qidiriladi.
        </p>
    </div>

    <div class="card">
        {% if tasks %}
            {% for t in tasks %}
                <div class="task-item">
                    <div>
                        <span class="badge {% if t.platform == 'instagram' %}badge-ig{% endif %}">{{ platform_dict[t.platform] }}</span>
                        <h4 style="margin-top:6px; font-size:16px;">{{ t.title }}</h4>
                        <small style="color:#94a3b8;">Bajarildi: {{ t.completed_count }} / {{ t.target_count }}</small>
                    </div>
                    <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
                        <a href="{{ to_web_url(t.link) }}" target="_blank" rel="noopener noreferrer" onclick="markOpened({{ t.id }})" class="btn btn-primary btn-sm">1. Saytga o'tish ↗</a>
                        <form method="POST" action="{{ url_for('verify_task', task_id=t.id) }}" style="margin:0;">
                            <button type="submit" class="btn btn-success btn-sm">2. Tasdiqlash (+{{ t.reward_per_action }} so'm)</button>
                        </form>
                    </div>
                </div>
            {% endfor %}
        {% else %}
            <p style="color:#94a3b8; text-align:center; padding:24px;">Hozircha yangi vazifalar yo'q. Birozdan so'ng qayta tekshiring!</p>
        {% endif %}
    </div>
    <script>
        function markOpened(id) {
            fetch('/start_task/' + id, {method: 'POST'});
        }
    </script>
    """
    return render_page(html, user=user, tasks=tasks, notifs=notifs)

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
    conn = get_db()
    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not task or task["status"] != "active" or task["completed_count"] >= task["target_count"]:
        conn.close()
        flash("Bu vazifa yakunlangan!", "danger")
        return redirect(url_for("earn"))

    if task["platform"] == "instagram":
        if not user["cookie_consent"] or not user["ig_sessionid"]:
            conn.close()
            flash("Siz cookie tekshiruviga ruxsat bermagansiz! Avval 'Instagram & Cookie Ruxsati' bo'limidan ruxsat bering — aks holda vazifa tasdiqlanmaydi.", "danger")
            return redirect(url_for("profile"))

    started = session.get("started_tasks", {})
    start_time = started.get(str(task_id))
    if not start_time:
        conn.close()
        flash("Avval '1. Saytga o'tish' tugmasini bosib reklama beruvchi sahifasiga kiring va obuna bo'ling!", "danger")
        return redirect(url_for("earn"))
    if time.time() - start_time < 3:
        conn.close()
        flash("Juda tez tasdiqladingiz! Sahifaga o'tib obuna bo'ling (kamida 3-4 soniya).", "danger")
        return redirect(url_for("earn"))

    if task["platform"] == "instagram":
        target_ig = extract_ig_username(task["link"])
        worker_ig = (user["ig_nick"] or extract_ig_username(user["ig_link"] or "")).lstrip("@").strip().lower()
        found = search_worker_in_target_followers(user["ig_sessionid"], target_ig, worker_ig)
        if found is False:
            conn.close()
            flash(f"@{target_ig} sahifasining obunachilari orasidan @{worker_ig or 'akkauntingiz'} topilmadi! Avval obuna bo'ling.", "danger")
            return redirect(url_for("earn"))
        if found is None:
            conn.close()
            flash("Instagram obunachilarini tekshirib bo'lmadi (cookie eskirgan yoki Instagram vaqtinchalik chekladi). Cookie'ni yangilab qayta urinib ko'ring.", "danger")
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
        flash(f"Obunachilar orasidan tasdiqlandi! Balansingizga +{task['reward_per_action']} so'm qo'shildi!{bonus_msg}", "success")
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
        cur = conn.execute("""
            INSERT INTO tasks (owner_id, platform, title, link, target_count, price_per_action, reward_per_action)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user["id"], platform, title, link, count, price, CONFIG["WORKER_REWARD"]))
        new_task_id = cur.lastrowid
        notif_text = f"Zayafka: {user['username']} '{title}' ({PLATFORM_DICT[platform]}, {count} ta) uchun reklama berdi — +{CONFIG['WORKER_REWARD']} so'm vazifa qo'shildi!"
        conn.execute("INSERT INTO notifications (message, task_id) VALUES (?, ?)", (notif_text, new_task_id))
        conn.commit()
        conn.close()
        flash(f"Buyurtma joylandi! Bildirishnoma yuborildi va {count} ta obunachi uchun {total_cost} so'm yechildi.", "success")
        return redirect(url_for("advertise"))

    conn = get_db()
    my_tasks = conn.execute("SELECT * FROM tasks WHERE owner_id = ? ORDER BY id DESC", (user["id"],)).fetchall()
    conn.close()

    html = """
    <div class="card">
        <h2>Yangi buyurtma berish</h2>
        <p style="font-size:13.5px; color:#94a3b8; margin-bottom:14px;">
            Reklama balansingiz: <b style="color:#38bdf8;">{{ user.ad_balance }} so'm</b> |
            1 ta obunachi narxi: <b>{{ config.ADVERTISER_PRICE }} so'm</b> |
            Maksimal buyurtma: <b>{{ max_possible }} ta</b>
        </p>
        <form method="POST">
            <label>Tarmoqni tanlang</label>
            <select name="platform">
                {% for key, name in platforms %}
                    <option value="{{ key }}">{{ name }}</option>
                {% endfor %}
            </select>

            <label>Vazifa sarlavhasi</label>
            <input type="text" name="title" required placeholder="Masalan: Instagram sahifamga obuna bo'ling">

            <label>Havola (Link)</label>
            <input type="url" name="link" required placeholder="https://instagram.com/username yoki https://t.me/kanal">

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
            receipt = request.files.get("receipt")
            saved_name = ""

            if receipt and receipt.filename:
                if not allowed_receipt(receipt.filename):
                    conn.close()
                    flash("Faqat rasm (JPG, PNG, WEBP) yoki PDF chek fayl yuklash mumkin!", "danger")
                    return redirect(url_for("wallet"))
                ext = receipt.filename.rsplit(".", 1)[1].lower()
                saved_name = secure_filename(f"u{user['id']}_{int(time.time())}_{random.randint(1000,9999)}.{ext}")
                receipt.save(os.path.join(RECEIPT_UPLOAD_DIR, saved_name))

            if amount < CONFIG["MIN_DEPOSIT"] or (not saved_name and not details):
                conn.close()
                flash(f"Minimal deposit {CONFIG['MIN_DEPOSIT']} so'm! Chek rasmi/PDF yoki chek ma'lumotini kiriting.", "danger")
                return redirect(url_for("wallet"))

            info_text = details or ("PDF chek yuklandi" if saved_name.endswith(".pdf") else "Rasm chek yuklandi")
            conn.execute(
                "INSERT INTO transactions (user_id, tx_type, amount, details, receipt_file) VALUES (?, 'deposit', ?, ?, ?)",
                (user["id"], amount, info_text, saved_name)
            )
            conn.commit()
            flash("Deposit cheki Admin panelga yuborildi! Tasdiqlangach Reklama balansingizga tushadi.", "success")

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
            <h3>💳 Hisobni to'ldirish (Rasm yoki PDF chek)</h3>
            <div class="alert alert-info" style="margin-top:12px;">
                Deposit karta: <b>{{ config.CARD_NUMBER }}</b><br>
                Egasi: <b>{{ config.CARD_HOLDER }}</b><br>
                Minimal: <b>{{ config.MIN_DEPOSIT }} so'm</b><br>
                <small>To'lov qilingach chek rasmini (JPG/PNG/WEBP) yoki PDF chekni yuklang.</small>
            </div>
            <form method="POST" enctype="multipart/form-data">
                <input type="hidden" name="action" value="deposit">
                <label>To'langan summa (so'm) *</label>
                <input type="number" name="amount" min="{{ config.MIN_DEPOSIT }}" required placeholder="Masalan: 7000">

                <label>Chek fayli (Rasm yoki PDF)</label>
                <input type="file" name="receipt" accept=".png,.jpg,.jpeg,.webp,.pdf">

                <label>Qo'shimcha izoh (ixtiyoriy)</label>
                <input type="text" name="details" placeholder="Masalan: Click / Payme chek">

                <button type="submit" class="btn btn-primary" style="width:100%;">Chekni yuborish</button>
            </form>
        </div>

        <div class="card">
            <h3>💸 Pul chiqarish ({{ user.work_balance }} so'm)</h3>
            <form method="POST" style="margin-top:12px;">
                <input type="hidden" name="action" value="withdraw">
                <label>Summa (Min: {{ config.MIN_WITHDRAW }} so'm)</label>
                <input type="number" name="amount" min="{{ config.MIN_WITHDRAW }}" required placeholder="Kamida 20000">
                <label>Karta raqamingiz</label>
                <input type="text" name="card" required placeholder="8600 / 9860 **** **** ****">
                <button type="submit" class="btn btn-success" style="width:100%;">Pul chiqarish so'rovi</button>
            </form>

            <hr style="border-color:rgba(148,163,184,0.16); margin:20px 0;">
            <h4 style="margin-bottom:8px;">🔄 Reklama balansiga o'tkazish</h4>
            <form method="POST">
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
                <tr><th>Turi</th><th>Summa</th><th>Chek fayl</th><th>Izoh</th><th>Holat</th></tr>
                {% for tx in txs %}
                <tr>
                    <td>{{ 'Deposit' if tx.tx_type == 'deposit' else 'Chiqarish' }}</td>
                    <td>{{ tx.amount }} so'm</td>
                    <td>
                        {% if tx.receipt_file %}
                            <a href="{{ url_for('get_receipt_file', filename=tx.receipt_file) }}" target="_blank" style="color:#38bdf8; font-weight:700;">Faylni ochish</a>
                        {% else %}
                            -
                        {% endif %}
                    </td>
                    <td>{{ tx.details }}</td>
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
                flash("So'rov tasdiqlandi!", "success")
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
    <div class="grid-3">
        <div class="stat-box"><div class="stat-title">Foydalanuvchilar</div><div class="stat-val">{{ stats.users }}</div></div>
        <div class="stat-box"><div class="stat-title">Jami vazifalar</div><div class="stat-val">{{ stats.tasks }}</div></div>
        <div class="stat-box"><div class="stat-title">Sayt sof foydasi (20 so'mdan)</div><div class="stat-val green">{{ stats.profit }} so'm</div></div>
    </div>

    <div class="card" style="margin-top:16px;">
        <h2>Kutilayotgan Deposit va Pul chiqarish so'rovlari</h2>
        {% if pending_txs %}
            <div class="table-wrap">
                <table>
                    <tr>
                        <th>User</th>
                        <th>Turi</th>
                        <th>Summa</th>
                        <th>Yuklangan chek (Rasm / PDF)</th>
                        <th>Amal</th>
                    </tr>
                    {% for tx in pending_txs %}
                    <tr>
                        <td>{{ tx.username }}</td>
                        <td>{{ '📥 DEPOSIT' if tx.tx_type == 'deposit' else '📤 CHIQARISH' }}</td>
                        <td><b>{{ tx.amount }} so'm</b></td>
                        <td>
                            {% if tx.receipt_file %}
                                {% if tx.receipt_file.lower().endswith('.pdf') %}
                                    <a href="{{ url_for('get_receipt_file', filename=tx.receipt_file) }}" target="_blank" class="btn btn-primary btn-sm">📄 PDF Chekni ochish</a>
                                {% else %}
                                    <a href="{{ url_for('get_receipt_file', filename=tx.receipt_file) }}" target="_blank">
                                        <img src="{{ url_for('get_receipt_file', filename=tx.receipt_file) }}" class="receipt-thumb" alt="Chek">
                                    </a>
                                {% endif %}
                            {% else %}
                                <span style="color:#94a3b8;">{{ tx.details }}</span>
                            {% endif %}
                        </td>
                        <td>
                            <form method="POST" style="display:flex; gap:6px; flex-wrap:wrap; align-items:center; margin:0;">
                                <input type="hidden" name="tx_id" value="{{ tx.id }}">
                                <input type="text" name="receipt_note" class="sm-input" placeholder="Izoh...">
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
