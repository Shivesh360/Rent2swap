"""
Rent It Bro: An AI-Powered Rental, Selling & Exchange Marketplace
Phase 2 - Marketplace / Resource Management

Backend: Python / Flask
Database: SQLite (database/student_exchange.db)
"""

import os
import io
import re
import json
import math
import uuid
import hmac
import hashlib
import sqlite3
import argparse
import time
import select
import secrets
import socket
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timedelta
from functools import wraps

try:
    import psycopg2
    from psycopg2 import extensions as pg_extensions
    from psycopg2.extras import RealDictCursor
    from psycopg2.pool import ThreadedConnectionPool
    PSYCOPG2_AVAILABLE = True
except ImportError:
    pg_extensions = None
    PSYCOPG2_AVAILABLE = False

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    abort,
    jsonify,
    send_file,
    Response
)
from flask_wtf.csrf import CSRFProtect, CSRFError
from PIL import Image, UnidentifiedImageError
import mimetypes
import urllib.request
import urllib.error
import urllib.parse
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix

from config import Config
import gemini_service

# Initialize Flask Application
app = Flask(__name__)
app.secret_key = Config.SECRET_KEY
app.config["UPLOAD_FOLDER"] = Config.UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = Config.MAX_CONTENT_LENGTH

# Apply ProxyFix for reverse proxy (Render / Cloud Run / Nginx) HTTPS support
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# Secure session cookie & CSRF configuration
app.config["SESSION_COOKIE_NAME"] = "rent_it_bro_session"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SECURE"] = Config.SESSION_COOKIE_SECURE
app.config["SESSION_COOKIE_SAMESITE"] = Config.SESSION_COOKIE_SAMESITE
if Config.SESSION_COOKIE_SAMESITE == "None" and Config.SESSION_COOKIE_SECURE:
    app.config["SESSION_COOKIE_PARTITIONED"] = True
app.config["WTF_CSRF_TIME_LIMIT"] = 3600

csrf = CSRFProtect(app)


@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    msg = "Security verification (CSRF token) missing or expired. Please refresh the page and try again."
    if _wants_json_response() or request.path.startswith("/api/"):
        return jsonify({"success": False, "message": msg}), 400
    flash(msg, "danger")
    return redirect(request.referrer or url_for("home")), 400


# Supported Marketplace Taxonomy
CATEGORIES = [
    "Books",
    "Electronics",
    "Mobile Phones",
    "Laptops",
    "Tablets",
    "Cameras",
    "Headphones",
    "Speakers",
    "Gaming Devices",
    "Cycles",
    "Sports Equipment",
    "Musical Instruments",
    "Furniture",
    "Home Appliances",
    "Tools",
    "Bags",
    "Accessories",
    "Project/Work Equipment",
    "Vehicles",
    "Other"
]

# ==============================================================================
# SECURITY QUESTIONS & PASSWORD RECOVERY CONFIGURATION (BCA Mini-Project)
# ==============================================================================
SECURITY_QUESTIONS = [
    "What is your birthplace?",
    "What is the name of your favourite book?",
    "What is your favourite tourist place?",
    "What was the name of your first school?",
    "Who was your childhood best friend?",
    "What was your childhood nickname?",
    "In which city did you grow up?"
]

def normalize_security_answer(answer: str) -> str:
    """
    Normalize security answers consistently:
    Trim whitespace, collapse internal multiple spaces, and convert to lower case.
    """
    if not answer:
        return ""
    return " ".join(str(answer).strip().lower().split())

def _get_client_ip() -> str:
    """Return normalized client IP address from request context."""
    try:
        return (request.remote_addr or "unknown").strip()[:64]
    except Exception:
        return "unknown"


def _is_safe_redirect_url(target: str) -> bool:
    """Strictly allow relative same-origin path redirects (blocks //evil.com, /\\evil.com, http://...)."""
    if not target or not isinstance(target, str):
        return False
    cleaned = target.strip()
    if not cleaned.startswith("/") or cleaned.startswith("//") or "\\" in cleaned:
        return False
    if any(ch in cleaned for ch in ("\r", "\n", "\x00")):
        return False
    parsed = urllib.parse.urlsplit(cleaned)
    return not parsed.scheme and not parsed.netloc


def _decoy_security_question(email: str) -> str:
    """Deterministically select a plausible security question without leaking account existence or admin role."""
    digest = hmac.new(
        str(Config.SECRET_KEY).encode("utf-8"),
        (email or "").strip().lower().encode("utf-8"),
        hashlib.sha256,
    ).digest()
    idx = digest[0] % len(SECURITY_QUESTIONS)
    return SECURITY_QUESTIONS[idx]


def _rate_limit_key(scope: str, identifier: str) -> str:
    norm = (identifier or "").strip().lower()[:180]
    return f"{scope}:{norm}"


def check_auth_rate_limit(scope: str, identifier: str) -> tuple:
    """Persistent DB-backed rate limit check shared across gunicorn workers."""
    key = _rate_limit_key(scope, identifier)
    now = time.time()
    rec = execute_query_one(
        "SELECT attempts, locked_until FROM auth_rate_limits WHERE scope_key = ?",
        (key,),
    )
    if rec and float(rec.get("locked_until") or 0) > now:
        remaining_secs = int(float(rec["locked_until"]) - now)
        remaining_mins = max(1, (remaining_secs + 59) // 60)
        return False, f"Too many incorrect attempts. Please wait {remaining_mins} minute(s) before trying again."
    return True, ""


def record_failed_auth_attempt(scope: str, identifier: str, max_attempts: int = 5, lock_seconds: int = 600) -> int:
    """Increment persistent failure counter and lock after threshold. Returns remaining attempts."""
    key = _rate_limit_key(scope, identifier)
    now = time.time()
    rec = execute_query_one(
        "SELECT attempts, locked_until FROM auth_rate_limits WHERE scope_key = ?",
        (key,),
    )
    attempts = 0
    if rec:
        prev_locked = float(rec.get("locked_until") or 0)
        if prev_locked <= 0 or prev_locked > now:
            attempts = int(rec.get("attempts") or 0)

    attempts += 1
    locked_until = (now + lock_seconds) if attempts >= max_attempts else 0.0

    if rec:
        execute_query_commit(
            "UPDATE auth_rate_limits SET attempts = ?, locked_until = ?, updated_at = CURRENT_TIMESTAMP WHERE scope_key = ?",
            (attempts, locked_until, key),
        )
    else:
        ok, _ = execute_query_commit(
            "INSERT INTO auth_rate_limits (scope_key, attempts, locked_until) VALUES (?, ?, ?)",
            (key, attempts, locked_until),
        )
        if not ok:
            execute_query_commit(
                "UPDATE auth_rate_limits SET attempts = ?, locked_until = ?, updated_at = CURRENT_TIMESTAMP WHERE scope_key = ?",
                (attempts, locked_until, key),
            )
    return max(0, max_attempts - attempts)


def clear_auth_rate_limit(scope: str, identifier: str):
    """Reset persistent lockout record on success."""
    key = _rate_limit_key(scope, identifier)
    execute_query_commit("DELETE FROM auth_rate_limits WHERE scope_key = ?", (key,))


def check_recovery_rate_limit(email: str) -> tuple:
    ip_ok, ip_msg = check_auth_rate_limit("recovery_ip", _get_client_ip())
    if not ip_ok:
        return False, ip_msg
    return check_auth_rate_limit("recovery_email", email)


def record_failed_recovery_attempt(email: str) -> int:
    rem_email = record_failed_auth_attempt("recovery_email", email, max_attempts=5, lock_seconds=600)
    record_failed_auth_attempt("recovery_ip", _get_client_ip(), max_attempts=10, lock_seconds=600)
    return rem_email


def clear_recovery_rate_limit(email: str):
    clear_auth_rate_limit("recovery_email", email)


def _hash_recovery_token(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def create_recovery_token(user_id: int, email: str) -> str:
    """Generate a cryptographically random, single-use recovery token persisted as SHA-256 hash in DB."""
    token = secrets.token_urlsafe(32)
    token_hash = _hash_recovery_token(token)
    expires_at = time.time() + 600  # 10 minutes expiry
    execute_query_commit(
        "DELETE FROM password_reset_tokens WHERE user_id = ? OR expires_at < ?",
        (int(user_id), time.time()),
    )
    execute_query_commit(
        "INSERT INTO password_reset_tokens (token_hash, user_id, email, expires_at) VALUES (?, ?, ?, ?)",
        (token_hash, int(user_id), (email or "").strip().lower(), expires_at),
    )
    return token


def verify_and_consume_recovery_token(token: str) -> dict | None:
    """Atomically verify and consume a single-use hashed recovery token from DB."""
    if not token or not isinstance(token, str):
        return None
    token_hash = _hash_recovery_token(token.strip())
    now = time.time()

    pool = get_pg_pool()
    if pool:
        conn = None
        try:
            conn = _get_healthy_pg_conn(pool)
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(
                """
                DELETE FROM password_reset_tokens
                WHERE token_hash = %s AND expires_at > %s
                RETURNING user_id, email
                """,
                (token_hash, now),
            )
            row = cur.fetchone()
            conn.commit()
            cur.close()
            _return_pg_conn(pool, conn)
            return dict(row) if row else None
        except Exception as e:
            is_conn_drop = isinstance(e, (psycopg2.OperationalError, psycopg2.InterfaceError)) if PSYCOPG2_AVAILABLE else False
            print(f"[Recovery Token PG Error] {type(e).__name__}")
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
                _return_pg_conn(pool, conn, force_close=is_conn_drop)
            return None

    conn = get_sqlite_connection()
    try:
        conn.isolation_level = "IMMEDIATE"
        cur = conn.cursor()
        cur.execute(
            "SELECT user_id, email, expires_at FROM password_reset_tokens WHERE token_hash = ?",
            (token_hash,),
        )
        row = cur.fetchone()
        cur.execute("DELETE FROM password_reset_tokens WHERE token_hash = ?", (token_hash,))
        conn.commit()
        cur.close()
        conn.close()
        if not row or float(row["expires_at"]) <= now:
            return None
        return {"user_id": row["user_id"], "email": row["email"]}
    except Exception as e:
        print(f"[Recovery Token SQLite Error] {type(e).__name__}")
        try:
            conn.rollback()
        except Exception:
            pass
        conn.close()
        return None


def _is_user_admin(user) -> bool:
    """Return True only if an active database user record holds explicit administrator privileges."""
    if not user:
        return False
    status_val = str(user.get("status") or "active").strip().lower()
    if status_val in ("suspended", "disabled", "banned"):
        return False
    return bool(user.get("is_admin")) or status_val == "admin"


def _is_user_suspended(user) -> bool:
    """Return True if a user record is suspended, disabled, or banned."""
    if not user:
        return False
    return str(user.get("status") or "active").strip().lower() in ("suspended", "disabled", "banned")


def normalize_verification_status(raw_status) -> str:
    """Normalize any legacy or null verification status into canonical Rent2Swap states."""
    val = str(raw_status or "").strip().lower()
    if val == "verified":
        return "Verified"
    if val == "pending":
        return "Pending"
    if val == "rejected":
        return "Rejected"
    return "Not Submitted"


def is_student_verified(user) -> bool:
    """Return True only if a user record has Verified status in the database."""
    if not user:
        return False
    return normalize_verification_status(user.get("verification_status")) == "Verified"


def log_verification_history(
    user_id: int,
    previous_status: str | None,
    new_status: str,
    action_type: str,
    submitted_at=None,
    resubmitted_at=None,
    reviewed_at=None,
    reviewed_by=None,
    rejection_reason: str = "",
    notes: str = "",
):
    """Record a student verification lifecycle transition into verification_history."""
    try:
        execute_query_commit("""
            INSERT INTO verification_history (
                user_id, previous_status, new_status, action_type,
                submitted_at, resubmitted_at, reviewed_at, reviewed_by,
                rejection_reason, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            previous_status,
            new_status,
            action_type,
            submitted_at,
            resubmitted_at,
            reviewed_at,
            reviewed_by,
            (rejection_reason or "").strip(),
            (notes or "").strip(),
        ))
    except Exception as e:
        print(f"[Verification History Error] {e}")


def _verification_blocked_message(status: str, action_label: str) -> str:
    if status == "Pending":
        return f"Your student verification is currently under admin review. Once approved, you can {action_label}."
    if status == "Rejected":
        return f"Your previous verification was rejected. Please update your details and resubmit your College ID to {action_label}."
    return f"Student verification is required to {action_label}. Please upload both sides of your College ID to verify your account."


def _require_verified_for_marketplace_action(action_label: str = "use this feature"):
    """
    Backend guard enforcing Verified student status for listing creation,
    rental requests, and Buy/Sell/Swap transactions.
    Returns None when allowed, or a Flask 403/401 Response when blocked.
    """
    user_id = session.get("user_id")
    if not user_id:
        if _wants_json_response() or request.path.startswith("/api/"):
            return jsonify({
                "success": False,
                "message": "Please sign in again to continue.",
                "redirect": url_for("login"),
            }), 401
        flash("Please sign in to access this feature.", "warning")
        return redirect(url_for("login", next=request.url))

    user = execute_query_one(
        "SELECT user_id, name, verification_status, verification_rejection_reason, status, session_version FROM users WHERE user_id = ?",
        (user_id,)
    )
    sess_ver = session.get("session_version")
    if not user or _is_user_suspended(user) or (
        sess_ver is not None and int(user.get("session_version") or 1) != int(sess_ver)
    ):
        session.clear()
        msg = "Your account is unavailable or suspended." if (user and _is_user_suspended(user)) else "Your session has expired. Please sign in again."
        if _wants_json_response() or request.path.startswith("/api/"):
            return jsonify({"success": False, "message": msg, "redirect": url_for("login")}), 403
        flash(msg, "danger")
        return redirect(url_for("login"))

    status = normalize_verification_status(user.get("verification_status"))
    session["is_verified"] = (status == "Verified")
    if status == "Verified":
        return None

    msg = _verification_blocked_message(status, action_label)
    if _wants_json_response() or request.path.startswith("/api/"):
        return jsonify({
            "success": False,
            "verification_required": True,
            "verification_status": status,
            "message": msg,
            "redirect": url_for("verification"),
        }), 403

    flash(msg, "warning")
    return render_template(
        "verification_required.html",
        verification_status=status,
        rejection_reason=user.get("verification_rejection_reason") or "",
        action_label=action_label,
        message=msg,
    ), 403


@app.context_processor
def inject_common_variables():
    """Inject global variables into all Jinja templates."""
    user = None
    user_id = session.get("user_id")
    if user_id:
        user = execute_query_one(
            "SELECT user_id, name, email, college, course, branch, year, location, profile_image, rating, is_admin, verification_status, verification_rejection_reason, status, session_version FROM users WHERE user_id = ?",
            (user_id,)
        )
        sess_ver = session.get("session_version")
        if not user or _is_user_suspended(user) or (
            sess_ver is not None and int(user.get("session_version") or 1) != int(sess_ver)
        ):
            session.clear()
            user = None
        else:
            user["verification_status"] = normalize_verification_status(user.get("verification_status"))
            session["is_admin"] = _is_user_admin(user)
            session["is_verified"] = (user["verification_status"] == "Verified")
    unread_messages_count = 0
    if user:
        try:
            unread_row = execute_query_one("""
                SELECT COUNT(*) AS c
                FROM messages m
                JOIN conversations c ON m.conversation_id = c.conversation_id
                WHERE (c.owner_id = ? OR c.interested_user_id = ?)
                  AND m.sender_id != ?
                  AND NOT m.is_read
            """, (user["user_id"], user["user_id"], user["user_id"]))
            if unread_row:
                unread_messages_count = int(unread_row.get("c") or 0)
        except Exception:
            unread_messages_count = 0
    return {
        "SECURITY_QUESTIONS": SECURITY_QUESTIONS,
        "current_user_profile": user,
        "unread_messages_count": unread_messages_count,
    }

TRANSACTION_TYPES = ["Sell", "Rent", "Exchange"]
CONDITIONS = ["New", "Like New", "Good", "Fair", "Used"]
RENT_PERIODS = ["Hour", "Day", "Week", "Month"]
LISTING_STATUSES = ["Available", "Inactive", "Rented", "Sold", "Exchanged"]

# Category to default fallback placeholder icon mapping
CATEGORY_PLACEHOLDERS = {
    "Cameras": "placeholders/camera.svg",
    "Laptops": "placeholders/laptop.svg",
    "Electronics": "placeholders/laptop.svg",
    "Mobile Phones": "placeholders/laptop.svg",
    "Tablets": "placeholders/laptop.svg",
    "Cycles": "placeholders/cycle.svg",
    "Vehicles": "placeholders/cycle.svg",
    "Gaming Devices": "placeholders/gaming.svg",
    "Headphones": "placeholders/headphones.svg",
    "Speakers": "placeholders/headphones.svg",
    "Furniture": "placeholders/furniture.svg",
    "Tools": "placeholders/tools.svg",
    "Project/Work Equipment": "placeholders/tools.svg",
    "Musical Instruments": "placeholders/guitar.svg",
    "Books": "placeholders/book.svg",
    "Sports Equipment": "placeholders/cycle.svg",
    "Home Appliances": "placeholders/furniture.svg",
    "Other": "placeholders/default.svg"
}

SAMPLE_DEMO_EMAILS = {
    "test.student@college.edu",
    "seller@rentitbro.com",
    "swapper.a@campus.edu",
    "swapper.b@campus.edu",
    "student.e2e@college.edu",
    "flow_test@college.edu",
}


def is_sample_email(email: str) -> bool:
    """Return True when a listing belongs to a seeded demo/sample account."""
    return (email or "").strip().lower() in SAMPLE_DEMO_EMAILS


# ==============================================================================
# DATABASE LAYER (Supabase PostgreSQL with SQLite Fallback)
# ==============================================================================

PG_POOL = None
_PG_LAST_FAILED_TIME = 0
PG_RETRY_COOLDOWN = 30  # seconds
_PG_CONN_LAST_USED = {}
_PG_CONN_LOCK = threading.Lock()


def get_pg_pool():
    """Get or initialize the PostgreSQL connection pool with failure cooldown."""
    global PG_POOL, _PG_LAST_FAILED_TIME
    if not (Config.USE_POSTGRES and PSYCOPG2_AVAILABLE):
        return None
    # Skip attempting connection if recent failure occurred to avoid severe query latency
    if _PG_LAST_FAILED_TIME and (time.time() - _PG_LAST_FAILED_TIME < PG_RETRY_COOLDOWN):
        return None
    try:
        if PG_POOL is None or getattr(PG_POOL, "closed", False):
            PG_POOL = ThreadedConnectionPool(
                minconn=1,
                maxconn=10,
                host=Config.DB_HOST,
                port=Config.DB_PORT,
                user=Config.DB_USER,
                password=Config.DB_PASSWORD,
                dbname=Config.DB_NAME,
                connect_timeout=5,
                options="-c statement_timeout=15000",
                keepalives=1,
                keepalives_idle=10,
                keepalives_interval=5,
                keepalives_count=2
            )
        return PG_POOL
    except Exception as e:
        _PG_LAST_FAILED_TIME = time.time()
        print(f"[Supabase PG Pool Error] Could not connect to pool: {e}")
        return None


def _configure_pg_socket_timeout(conn):
    """Apply TCP_USER_TIMEOUT on the psycopg2 socket so silent drops fail in <=8s instead of 60s+."""
    try:
        fd = conn.fileno()
        if fd is not None and fd >= 0:
            tcp_user_timeout = getattr(socket, "TCP_USER_TIMEOUT", 18)
            for fam in (socket.AF_INET, socket.AF_INET6):
                try:
                    s = socket.fromfd(fd, fam, socket.SOCK_STREAM)
                    try:
                        s.setsockopt(socket.IPPROTO_TCP, tcp_user_timeout, 8000)
                    finally:
                        s.close()
                    break
                except Exception:
                    continue
    except Exception:
        pass


def _return_pg_conn(pool, conn, force_close=False):
    """Safely return or discard a PostgreSQL connection back to the pool."""
    if not conn:
        return
    conn_id = id(conn)
    should_close = force_close or bool(getattr(conn, "closed", 0))
    if not should_close and pg_extensions is not None:
        status = getattr(conn, "status", None)
        if status != pg_extensions.STATUS_READY:
            try:
                conn.rollback()
            except Exception:
                should_close = True
            if getattr(conn, "status", None) != pg_extensions.STATUS_READY:
                should_close = True
    with _PG_CONN_LOCK:
        if should_close:
            _PG_CONN_LAST_USED.pop(conn_id, None)
        else:
            _PG_CONN_LAST_USED[conn_id] = time.time()
    try:
        pool.putconn(conn, close=should_close)
    except Exception:
        pass


def _get_healthy_pg_conn(pool):
    """Acquire a live PostgreSQL connection, replacing stale or silently dropped sockets."""
    now = time.time()
    for _ in range(3):
        conn = pool.getconn()
        is_dirty = (
            pg_extensions is not None
            and getattr(conn, "status", None) != pg_extensions.STATUS_READY
        )
        is_eof = False
        if not getattr(conn, "closed", 0) and not is_dirty:
            try:
                fd = conn.fileno()
                if fd is not None and fd >= 0:
                    readable, _, _ = select.select([fd], [], [], 0)
                    if readable:
                        is_eof = True
            except Exception:
                is_eof = True

        if getattr(conn, "closed", 0) or is_dirty or is_eof:
            with _PG_CONN_LOCK:
                _PG_CONN_LAST_USED.pop(id(conn), None)
            try:
                pool.putconn(conn, close=True)
            except Exception:
                pass
            continue

        _configure_pg_socket_timeout(conn)

        with _PG_CONN_LOCK:
            last_used = _PG_CONN_LAST_USED.get(id(conn))
            _PG_CONN_LAST_USED[id(conn)] = now

        # If idle > 45s, ping quickly to discard silent NAT/pooler drops
        if last_used is not None and (now - last_used > 45):
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                conn.commit()
            except Exception:
                with _PG_CONN_LOCK:
                    _PG_CONN_LAST_USED.pop(id(conn), None)
                try:
                    pool.putconn(conn, close=True)
                except Exception:
                    pass
                continue
        return conn
    conn = pool.getconn()
    _configure_pg_socket_timeout(conn)
    with _PG_CONN_LOCK:
        _PG_CONN_LAST_USED[id(conn)] = now
    return conn


def get_sqlite_connection():
    """Establish and return an active SQLite database connection."""
    os.makedirs(os.path.dirname(Config.DATABASE_PATH), exist_ok=True)
    conn = sqlite3.connect(Config.DATABASE_PATH, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def get_db_connection():
    """Backwards-compatible connection getter."""
    pool = get_pg_pool()
    if pool:
        return pool.getconn()
    return get_sqlite_connection()


def _prepare_query(query: str, is_pg: bool = False) -> str:
    """Format query syntax between SQLite and PostgreSQL."""
    if not is_pg:
        return query
    # Convert LIKE ? to case-insensitive ILIKE %s for PostgreSQL
    formatted = re.sub(r'\bLIKE\s*\?', 'ILIKE %s', query, flags=re.IGNORECASE)
    # Fix boolean comparisons where integer literals are used in queries
    formatted = re.sub(r'\bis_admin\s*=\s*1\b', 'is_admin = TRUE', formatted, flags=re.IGNORECASE)
    formatted = re.sub(r'\bis_admin\s*=\s*0\b', 'is_admin = FALSE', formatted, flags=re.IGNORECASE)
    formatted = re.sub(r'\bagreement_accepted\s*=\s*1\b', 'agreement_accepted = TRUE', formatted, flags=re.IGNORECASE)
    formatted = re.sub(r'\bagreement_accepted\s*=\s*0\b', 'agreement_accepted = FALSE', formatted, flags=re.IGNORECASE)
    if "agreement_accepted" in formatted.lower() or "is_admin" in formatted.lower():
        formatted = re.sub(r',\s*1(\s*\)|\s*,)', r', TRUE\1', formatted)
        formatted = re.sub(r',\s*0(\s*\)|\s*,)', r', FALSE\1', formatted)
    # Escape any literal % not part of a format specifier before replacing ? with %s
    formatted = re.sub(r'%(?!s)', '%%', formatted)
    formatted = formatted.replace('?', '%s')
    return formatted


def _serialize_row(row_dict):
    """Normalize row values (e.g. converting datetime to string) for cross-database compatibility."""
    if not row_dict:
        return row_dict
    res = {}
    for k, v in row_dict.items():
        if isinstance(v, datetime):
            res[k] = v.strftime('%Y-%m-%d %H:%M:%S')
        else:
            res[k] = v
    return res


def execute_query_one(query, params=()):
    """
    Execute a parameterized SQL query and return a single row as a dictionary.
    Supports both Supabase PostgreSQL and SQLite.
    """
    pool = get_pg_pool()
    if pool:
        for attempt in range(2):
            conn = None
            try:
                conn = _get_healthy_pg_conn(pool)
                pg_query = _prepare_query(query, is_pg=True)
                cursor = conn.cursor(cursor_factory=RealDictCursor)
                cursor.execute(pg_query, params)
                row = cursor.fetchone()
                result = _serialize_row(dict(row)) if row else None
                cursor.close()
                _return_pg_conn(pool, conn)
                return result
            except Exception as e:
                is_conn_drop = isinstance(e, (psycopg2.OperationalError, psycopg2.InterfaceError)) if PSYCOPG2_AVAILABLE else False
                print(f"[Supabase PG Query Error] execute_query_one (attempt {attempt + 1}): {e}")
                if conn:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    _return_pg_conn(pool, conn, force_close=is_conn_drop)
                if is_conn_drop and attempt == 0:
                    continue
                break

    # SQLite fallback
    conn = get_sqlite_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(query, params)
        row = cursor.fetchone()
        result = _serialize_row(dict(row)) if row else None
        cursor.close()
        conn.close()
        return result
    except Exception as e:
        print(f"[SQLite Query Error] execute_query_one: {e}")
        conn.close()
        return None


def execute_query_all(query, params=()):
    """
    Execute a parameterized SQL query and return all rows as a list of dictionaries.
    Supports both Supabase PostgreSQL and SQLite.
    """
    pool = get_pg_pool()
    if pool:
        for attempt in range(2):
            conn = None
            try:
                conn = _get_healthy_pg_conn(pool)
                pg_query = _prepare_query(query, is_pg=True)
                cursor = conn.cursor(cursor_factory=RealDictCursor)
                cursor.execute(pg_query, params)
                rows = cursor.fetchall()
                result = [_serialize_row(dict(r)) for r in rows]
                cursor.close()
                _return_pg_conn(pool, conn)
                return result
            except Exception as e:
                is_conn_drop = isinstance(e, (psycopg2.OperationalError, psycopg2.InterfaceError)) if PSYCOPG2_AVAILABLE else False
                print(f"[Supabase PG Query Error] execute_query_all (attempt {attempt + 1}): {e}")
                if conn:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    _return_pg_conn(pool, conn, force_close=is_conn_drop)
                if is_conn_drop and attempt == 0:
                    continue
                break

    # SQLite fallback
    conn = get_sqlite_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        result = [_serialize_row(dict(r)) for r in rows]
        cursor.close()
        conn.close()
        return result
    except Exception as e:
        print(f"[SQLite Query Error] execute_query_all: {e}")
        conn.close()
        return []


def execute_query_commit(query, params=()):
    """
    Execute an INSERT, UPDATE, or DELETE parameterized query and commit.
    Returns (success: bool, last_row_id or None).
    """
    pool = get_pg_pool()
    if pool:
        for attempt in range(2):
            conn = None
            try:
                conn = _get_healthy_pg_conn(pool)
                clean_query = query.strip()
                while clean_query.endswith(";"):
                    clean_query = clean_query[:-1].strip()
                upper_query = clean_query.upper()
                returning_col = None

                if upper_query.startswith("INSERT INTO") and "RETURNING" not in upper_query:
                    if "USERS" in upper_query:
                        clean_query += " RETURNING user_id"
                        returning_col = "user_id"
                    elif "ITEMS" in upper_query and "ITEM_IMAGES" not in upper_query:
                        clean_query += " RETURNING item_id"
                        returning_col = "item_id"
                    elif "ITEM_IMAGES" in upper_query:
                        clean_query += " RETURNING image_id"
                        returning_col = "image_id"
                    elif "RENTAL_REQUESTS" in upper_query:
                        clean_query += " RETURNING request_id"
                        returning_col = "request_id"
                    elif "DAMAGE_REPORTS" in upper_query:
                        clean_query += " RETURNING report_id"
                        returning_col = "report_id"
                    elif "REVIEWS" in upper_query:
                        clean_query += " RETURNING review_id"
                        returning_col = "review_id"
                    elif "REPORTS" in upper_query:
                        clean_query += " RETURNING report_id"
                        returning_col = "report_id"
                    elif "ADMIN_ACTIONS" in upper_query:
                        clean_query += " RETURNING action_id"
                        returning_col = "action_id"
                    elif "AI_INTERACTIONS" in upper_query:
                        clean_query += " RETURNING interaction_id"
                        returning_col = "interaction_id"
                    elif "CONVERSATIONS" in upper_query:
                        clean_query += " RETURNING conversation_id"
                        returning_col = "conversation_id"
                    elif "MESSAGES" in upper_query:
                        clean_query += " RETURNING message_id"
                        returning_col = "message_id"

                pg_query = _prepare_query(clean_query, is_pg=True)
                cursor = conn.cursor(cursor_factory=RealDictCursor)
                cursor.execute(pg_query, params)

                last_id = None
                if returning_col:
                    returned = cursor.fetchone()
                    if returned:
                        last_id = returned.get(returning_col) or list(returned.values())[0]

                conn.commit()
                cursor.close()
                _return_pg_conn(pool, conn)
                return True, last_id
            except Exception as e:
                is_conn_drop = isinstance(e, (psycopg2.OperationalError, psycopg2.InterfaceError)) if PSYCOPG2_AVAILABLE else False
                print(f"[Supabase PG Query Error] execute_query_commit (attempt {attempt + 1}): {e}")
                if conn:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    _return_pg_conn(pool, conn, force_close=is_conn_drop)
                if is_conn_drop and attempt == 0:
                    continue
                # Never silently divert PostgreSQL write failures to SQLite (avoids ghost listings)
                return False, None

    # SQLite fallback (only when PostgreSQL is disabled/unavailable)
    conn = get_sqlite_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(query, params)
        last_id = cursor.lastrowid
        conn.commit()
        cursor.close()
        conn.close()
        return True, last_id
    except Exception as e:
        print(f"[SQLite Query Error] execute_query_commit: {e}")
        conn.close()
        return False, None


def count_query(query, params=()):
    """Safely execute a COUNT query and return an integer."""
    row = execute_query_one(query, params)
    if not row:
        return 0
    return int(row.get("c") or row.get("count") or 0)


_db_init_event = threading.Event()
_db_init_started = False
_db_init_lock = threading.Lock()


def init_database():
    """
    Initialize schema for Rent2Swap.
    Creates all required tables in Supabase PostgreSQL (or SQLite fallback) and applies non-destructive migrations.
    """
    os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)

    if Config.USE_POSTGRES and PSYCOPG2_AVAILABLE:
        try:
            conn = psycopg2.connect(
                host=Config.DB_HOST,
                port=Config.DB_PORT,
                user=Config.DB_USER,
                password=Config.DB_PASSWORD,
                dbname=Config.DB_NAME,
                connect_timeout=3
            )
            cursor = conn.cursor()

            # Execute all PostgreSQL schema DDL & non-destructive migrations in a single round-trip
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id SERIAL PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    email VARCHAR(255) NOT NULL UNIQUE,
                    password VARCHAR(255) NOT NULL,
                    college VARCHAR(255) NOT NULL DEFAULT 'General User',
                    course VARCHAR(255) NOT NULL DEFAULT 'General',
                    branch VARCHAR(255) DEFAULT '',
                    year VARCHAR(50) NOT NULL DEFAULT 'N/A',
                    enrollment_no VARCHAR(100) DEFAULT '',
                    phone VARCHAR(50) DEFAULT '',
                    location VARCHAR(255) NOT NULL,
                    profile_image TEXT DEFAULT NULL,
                    id_card_image TEXT DEFAULT NULL,
                    rating DOUBLE PRECISION DEFAULT 5.00,
                    is_admin BOOLEAN DEFAULT FALSE,
                    verification_status VARCHAR(50) DEFAULT 'Unverified',
                    verified_at TIMESTAMP DEFAULT NULL,
                    hostel_room TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status VARCHAR(50) DEFAULT 'active'
                );

                ALTER TABLE users ADD COLUMN IF NOT EXISTS branch VARCHAR(255) DEFAULT '';
                ALTER TABLE users ADD COLUMN IF NOT EXISTS enrollment_no VARCHAR(100) DEFAULT '';
                ALTER TABLE users ADD COLUMN IF NOT EXISTS phone VARCHAR(50) DEFAULT '';
                ALTER TABLE users ADD COLUMN IF NOT EXISTS id_card_image TEXT DEFAULT NULL;
                ALTER TABLE users ADD COLUMN IF NOT EXISTS id_card_front_image TEXT DEFAULT NULL;
                ALTER TABLE users ADD COLUMN IF NOT EXISTS id_card_back_image TEXT DEFAULT NULL;
                ALTER TABLE users ADD COLUMN IF NOT EXISTS college_id_front_path TEXT DEFAULT NULL;
                ALTER TABLE users ADD COLUMN IF NOT EXISTS college_id_back_path TEXT DEFAULT NULL;
                ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN DEFAULT FALSE;
                ALTER TABLE users ADD COLUMN IF NOT EXISTS verification_status VARCHAR(50) DEFAULT 'Unverified';
                ALTER TABLE users ADD COLUMN IF NOT EXISTS verified_at TIMESTAMP DEFAULT NULL;
                ALTER TABLE users ADD COLUMN IF NOT EXISTS hostel_room TEXT DEFAULT '';
                ALTER TABLE users ADD COLUMN IF NOT EXISTS verification_rejection_reason TEXT DEFAULT '';
                ALTER TABLE users ADD COLUMN IF NOT EXISTS security_question TEXT DEFAULT '';
                ALTER TABLE users ADD COLUMN IF NOT EXISTS security_answer_hash TEXT DEFAULT '';
                ALTER TABLE users ADD COLUMN IF NOT EXISTS security_question_updated_at TIMESTAMP DEFAULT NULL;
                ALTER TABLE users ADD COLUMN IF NOT EXISTS verification_submitted_at TIMESTAMP DEFAULT NULL;
                ALTER TABLE users ADD COLUMN IF NOT EXISTS verification_resubmitted_at TIMESTAMP DEFAULT NULL;
                ALTER TABLE users ADD COLUMN IF NOT EXISTS verification_reviewed_at TIMESTAMP DEFAULT NULL;
                ALTER TABLE users ADD COLUMN IF NOT EXISTS verification_reviewed_by INTEGER DEFAULT NULL;
                ALTER TABLE users ADD COLUMN IF NOT EXISTS session_version INTEGER NOT NULL DEFAULT 1;
                ALTER TABLE users ALTER COLUMN verification_status SET DEFAULT 'Not Submitted';

                CREATE TABLE IF NOT EXISTS items (
                    item_id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    title VARCHAR(255) NOT NULL,
                    category VARCHAR(100) NOT NULL,
                    transaction_type VARCHAR(50) NOT NULL,
                    description TEXT NOT NULL,
                    condition VARCHAR(50) NOT NULL,
                    brand VARCHAR(100) DEFAULT '',
                    model VARCHAR(100) DEFAULT '',
                    age VARCHAR(100) DEFAULT '',
                    price DOUBLE PRECISION DEFAULT 0.0,
                    rent_amount DOUBLE PRECISION DEFAULT 0.0,
                    rent_period VARCHAR(50) DEFAULT '',
                    exchange_preference TEXT DEFAULT '',
                    city VARCHAR(100) NOT NULL,
                    location VARCHAR(255) NOT NULL,
                    status VARCHAR(50) NOT NULL DEFAULT 'Available',
                    views_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS item_images (
                    image_id SERIAL PRIMARY KEY,
                    item_id INTEGER NOT NULL REFERENCES items(item_id) ON DELETE CASCADE,
                    image_path TEXT NOT NULL,
                    is_primary INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS rental_requests (
                    request_id SERIAL PRIMARY KEY,
                    item_id INTEGER NOT NULL REFERENCES items(item_id) ON DELETE CASCADE,
                    renter_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    owner_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    request_type VARCHAR(50) NOT NULL DEFAULT 'Rent',
                    start_date DATE,
                    due_date DATE,
                    returned_date VARCHAR(50),
                    total_price DOUBLE PRECISION DEFAULT 0.0,
                    security_deposit DOUBLE PRECISION DEFAULT 0.0,
                    status VARCHAR(50) NOT NULL DEFAULT 'PENDING',
                    agreement_accepted BOOLEAN DEFAULT FALSE,
                    agreement_accepted_at TIMESTAMP,
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS damage_reports (
                    report_id SERIAL PRIMARY KEY,
                    rental_id INTEGER NOT NULL REFERENCES rental_requests(request_id) ON DELETE CASCADE,
                    reporter_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    item_id INTEGER NOT NULL REFERENCES items(item_id) ON DELETE CASCADE,
                    description TEXT NOT NULL,
                    severity VARCHAR(50) NOT NULL DEFAULT 'Moderate',
                    photo_path TEXT,
                    status VARCHAR(50) NOT NULL DEFAULT 'Reported',
                    admin_remark TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS reviews (
                    review_id SERIAL PRIMARY KEY,
                    rental_id INTEGER,
                    reviewer_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    reviewed_user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    item_id INTEGER REFERENCES items(item_id) ON DELETE CASCADE,
                    rating DOUBLE PRECISION NOT NULL DEFAULT 5.0,
                    comment TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS reports (
                    report_id SERIAL PRIMARY KEY,
                    reporter_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    target_type VARCHAR(50) NOT NULL,
                    target_id INTEGER NOT NULL,
                    reason VARCHAR(100) NOT NULL,
                    description TEXT NOT NULL,
                    status VARCHAR(50) NOT NULL DEFAULT 'Open',
                    admin_remark TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS admin_actions (
                    action_id SERIAL PRIMARY KEY,
                    admin_id INTEGER,
                    admin_name VARCHAR(255),
                    action_type VARCHAR(100) NOT NULL,
                    target_type VARCHAR(50) NOT NULL,
                    target_id INTEGER,
                    details TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS ai_interactions (
                    interaction_id SERIAL PRIMARY KEY,
                    user_id INTEGER,
                    feature_type VARCHAR(50) NOT NULL,
                    query_text TEXT,
                    result_summary TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS rental_status_log (
                    log_id SERIAL PRIMARY KEY,
                    request_id INTEGER NOT NULL REFERENCES rental_requests(request_id) ON DELETE CASCADE,
                    previous_status VARCHAR(50),
                    new_status VARCHAR(50) NOT NULL,
                    changed_by INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
                    action_role VARCHAR(50) DEFAULT 'user',
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_items_category ON items(category);
                CREATE INDEX IF NOT EXISTS idx_items_transaction_type ON items(transaction_type);
                CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
                CREATE INDEX IF NOT EXISTS idx_items_user_id ON items(user_id);
                CREATE INDEX IF NOT EXISTS idx_item_images_item_id ON item_images(item_id);
                CREATE INDEX IF NOT EXISTS idx_rental_requests_users ON rental_requests(renter_id, owner_id);
                CREATE INDEX IF NOT EXISTS idx_rental_status_log_req ON rental_status_log(request_id);

                ALTER TABLE rental_requests ADD COLUMN IF NOT EXISTS offered_item_id INTEGER REFERENCES items(item_id) ON DELETE SET NULL DEFAULT NULL;
                ALTER TABLE rental_requests ADD COLUMN IF NOT EXISTS completion_confirmed_by_owner BOOLEAN DEFAULT FALSE;
                ALTER TABLE rental_requests ADD COLUMN IF NOT EXISTS completion_confirmed_by_renter BOOLEAN DEFAULT FALSE;
                ALTER TABLE rental_requests ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;

                ALTER TABLE damage_reports ADD COLUMN IF NOT EXISTS admin_remark TEXT;
                ALTER TABLE reports ADD COLUMN IF NOT EXISTS admin_remark TEXT;
                ALTER TABLE items ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(128) DEFAULT NULL;
                CREATE UNIQUE INDEX IF NOT EXISTS idx_items_user_idempotency ON items(user_id, idempotency_key) WHERE idempotency_key IS NOT NULL;
                CREATE UNIQUE INDEX IF NOT EXISTS idx_reviews_unique_rental_reviewer ON reviews(rental_id, reviewer_id) WHERE rental_id IS NOT NULL;

                CREATE TABLE IF NOT EXISTS verification_history (
                    history_id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    previous_status VARCHAR(50),
                    new_status VARCHAR(50) NOT NULL,
                    action_type VARCHAR(64) NOT NULL,
                    submitted_at TIMESTAMP DEFAULT NULL,
                    resubmitted_at TIMESTAMP DEFAULT NULL,
                    reviewed_at TIMESTAMP DEFAULT NULL,
                    reviewed_by INTEGER REFERENCES users(user_id) ON DELETE SET NULL DEFAULT NULL,
                    rejection_reason TEXT DEFAULT '',
                    notes TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_verification_history_user_id ON verification_history(user_id);

                CREATE TABLE IF NOT EXISTS auth_rate_limits (
                    scope_key VARCHAR(255) PRIMARY KEY,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    locked_until DOUBLE PRECISION NOT NULL DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS password_reset_tokens (
                    token_hash VARCHAR(128) PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    email VARCHAR(255) NOT NULL,
                    expires_at DOUBLE PRECISION NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_pwd_reset_expires ON password_reset_tokens(expires_at);

                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id SERIAL PRIMARY KEY,
                    listing_id INTEGER NOT NULL REFERENCES items(item_id) ON DELETE CASCADE,
                    owner_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    interested_user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    request_id INTEGER REFERENCES rental_requests(request_id) ON DELETE SET NULL DEFAULT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_message_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT chk_conversations_distinct_users CHECK (owner_id <> interested_user_id),
                    CONSTRAINT uq_conversations_listing_pair UNIQUE (listing_id, owner_id, interested_user_id)
                );

                CREATE TABLE IF NOT EXISTS messages (
                    message_id SERIAL PRIMARY KEY,
                    conversation_id INTEGER NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                    sender_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    message_text TEXT NOT NULL,
                    is_read BOOLEAN NOT NULL DEFAULT FALSE,
                    client_msg_id VARCHAR(64) DEFAULT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_conversations_owner_last ON conversations(owner_id, last_message_at DESC);
                CREATE INDEX IF NOT EXISTS idx_conversations_interested_last ON conversations(interested_user_id, last_message_at DESC);
                CREATE INDEX IF NOT EXISTS idx_conversations_listing ON conversations(listing_id);
                CREATE INDEX IF NOT EXISTS idx_messages_conv_id ON messages(conversation_id, message_id ASC);
                CREATE INDEX IF NOT EXISTS idx_messages_conv_unread ON messages(conversation_id, sender_id, is_read);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_client_idemp ON messages(conversation_id, sender_id, client_msg_id) WHERE client_msg_id IS NOT NULL;

                DO $$
                DECLARE
                    tbl RECORD;
                    pol_name TEXT;
                BEGIN
                    FOR tbl IN
                        SELECT tablename
                        FROM pg_tables
                        WHERE schemaname = 'public'
                    LOOP
                        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', tbl.tablename);
                        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
                            EXECUTE format('REVOKE ALL ON TABLE public.%I FROM anon', tbl.tablename);
                        END IF;
                        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
                            EXECUTE format('REVOKE ALL ON TABLE public.%I FROM authenticated', tbl.tablename);
                        END IF;
                        pol_name := tbl.tablename || '_deny_direct_public';
                        EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', pol_name, tbl.tablename);
                        EXECUTE format('CREATE POLICY %I ON public.%I FOR ALL TO PUBLIC USING (false) WITH CHECK (false)', pol_name, tbl.tablename);
                    END LOOP;
                END $$;
            """)

            conn.commit()
            cursor.close()
            conn.close()
            print("[Database] Supabase PostgreSQL initialized successfully.")
            seed_demo_data_if_empty()
            return
        except Exception as e:
            print(f"[Database Error] Could not initialize Supabase PostgreSQL ({type(e).__name__}). Falling back to SQLite.")

    # SQLite fallback
    os.makedirs(os.path.dirname(Config.DATABASE_PATH), exist_ok=True)
    conn = get_sqlite_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL,
            college TEXT NOT NULL DEFAULT 'General User',
            course TEXT NOT NULL DEFAULT 'General',
            branch TEXT DEFAULT '',
            year TEXT NOT NULL DEFAULT 'N/A',
            enrollment_no TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            location TEXT NOT NULL,
            profile_image TEXT DEFAULT NULL,
            id_card_image TEXT DEFAULT NULL,
            rating REAL DEFAULT 5.00,
            is_admin INTEGER DEFAULT 0,
            verification_status TEXT DEFAULT 'Not Submitted',
            verified_at TEXT DEFAULT NULL,
            hostel_room TEXT DEFAULT '',
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # Non-destructive migrations for existing SQLite users table
    cursor.execute("PRAGMA table_info(users);")
    existing_user_cols = [row["name"] for row in cursor.fetchall()]
    migration_cols = [
        ("branch", "TEXT DEFAULT ''"),
        ("enrollment_no", "TEXT DEFAULT ''"),
        ("phone", "TEXT DEFAULT ''"),
        ("id_card_image", "TEXT DEFAULT NULL"),
        ("id_card_front_image", "TEXT DEFAULT NULL"),
        ("id_card_back_image", "TEXT DEFAULT NULL"),
        ("college_id_front_path", "TEXT DEFAULT NULL"),
        ("college_id_back_path", "TEXT DEFAULT NULL"),
        ("is_admin", "INTEGER DEFAULT 0"),
        ("verification_status", "TEXT DEFAULT 'Not Submitted'"),
        ("verified_at", "TEXT DEFAULT NULL"),
        ("hostel_room", "TEXT DEFAULT ''"),
        ("verification_rejection_reason", "TEXT DEFAULT ''"),
        ("security_question", "TEXT DEFAULT ''"),
        ("security_answer_hash", "TEXT DEFAULT ''"),
        ("security_question_updated_at", "TEXT DEFAULT NULL"),
        ("verification_submitted_at", "TEXT DEFAULT NULL"),
        ("verification_resubmitted_at", "TEXT DEFAULT NULL"),
        ("verification_reviewed_at", "TEXT DEFAULT NULL"),
        ("verification_reviewed_by", "INTEGER DEFAULT NULL"),
        ("session_version", "INTEGER NOT NULL DEFAULT 1"),
    ]
    for col_name, col_type in migration_cols:
        if col_name not in existing_user_cols:
            cursor.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_type};")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS items (
            item_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            transaction_type TEXT NOT NULL,
            description TEXT NOT NULL,
            condition TEXT NOT NULL,
            brand TEXT DEFAULT '',
            model TEXT DEFAULT '',
            age TEXT DEFAULT '',
            price REAL DEFAULT 0.0,
            rent_amount REAL DEFAULT 0.0,
            rent_period TEXT DEFAULT '',
            exchange_preference TEXT DEFAULT '',
            city TEXT NOT NULL,
            location TEXT NOT NULL,
            status TEXT DEFAULT 'Available',
            views_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        );
    """)
    cursor.execute("PRAGMA table_info(items);")
    existing_item_cols = [row["name"] for row in cursor.fetchall()]
    if "idempotency_key" not in existing_item_cols:
        cursor.execute("ALTER TABLE items ADD COLUMN idempotency_key TEXT DEFAULT NULL;")
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_items_user_idempotency ON items(user_id, idempotency_key) WHERE idempotency_key IS NOT NULL;")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS item_images (
            image_id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            image_path TEXT NOT NULL,
            is_primary INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (item_id) REFERENCES items(item_id) ON DELETE CASCADE
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS rental_requests (
            request_id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            renter_id INTEGER NOT NULL,
            owner_id INTEGER NOT NULL,
            request_type TEXT NOT NULL DEFAULT 'Rent',
            start_date TEXT,
            due_date TEXT,
            returned_date TEXT,
            total_price REAL DEFAULT 0.0,
            security_deposit REAL DEFAULT 0.0,
            status TEXT NOT NULL DEFAULT 'PENDING',
            agreement_accepted INTEGER DEFAULT 0,
            agreement_accepted_at TEXT,
            notes TEXT,
            offered_item_id INTEGER DEFAULT NULL,
            completion_confirmed_by_owner INTEGER DEFAULT 0,
            completion_confirmed_by_renter INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (item_id) REFERENCES items(item_id) ON DELETE CASCADE,
            FOREIGN KEY (renter_id) REFERENCES users(user_id) ON DELETE CASCADE,
            FOREIGN KEY (owner_id) REFERENCES users(user_id) ON DELETE CASCADE,
            FOREIGN KEY (offered_item_id) REFERENCES items(item_id) ON DELETE SET NULL
        );
    """)
    cursor.execute("PRAGMA table_info(rental_requests);")
    existing_req_cols = [row["name"] for row in cursor.fetchall()]
    for col_name, col_type in [
        ("offered_item_id", "INTEGER DEFAULT NULL"),
        ("completion_confirmed_by_owner", "INTEGER DEFAULT 0"),
        ("completion_confirmed_by_renter", "INTEGER DEFAULT 0"),
        ("updated_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
    ]:
        if col_name not in existing_req_cols:
            cursor.execute(f"ALTER TABLE rental_requests ADD COLUMN {col_name} {col_type};")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS damage_reports (
            report_id INTEGER PRIMARY KEY AUTOINCREMENT,
            rental_id INTEGER NOT NULL,
            reporter_id INTEGER NOT NULL,
            item_id INTEGER NOT NULL,
            description TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'Moderate',
            photo_path TEXT,
            status TEXT NOT NULL DEFAULT 'Reported',
            admin_remark TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (rental_id) REFERENCES rental_requests(request_id) ON DELETE CASCADE,
            FOREIGN KEY (reporter_id) REFERENCES users(user_id) ON DELETE CASCADE,
            FOREIGN KEY (item_id) REFERENCES items(item_id) ON DELETE CASCADE
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reviews (
            review_id INTEGER PRIMARY KEY AUTOINCREMENT,
            rental_id INTEGER,
            reviewer_id INTEGER NOT NULL,
            reviewed_user_id INTEGER NOT NULL,
            item_id INTEGER,
            rating REAL NOT NULL DEFAULT 5.0,
            comment TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (reviewer_id) REFERENCES users(user_id) ON DELETE CASCADE,
            FOREIGN KEY (reviewed_user_id) REFERENCES users(user_id) ON DELETE CASCADE,
            FOREIGN KEY (item_id) REFERENCES items(item_id) ON DELETE CASCADE
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            report_id INTEGER PRIMARY KEY AUTOINCREMENT,
            reporter_id INTEGER NOT NULL,
            target_type TEXT NOT NULL,
            target_id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            description TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Open',
            admin_remark TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (reporter_id) REFERENCES users(user_id) ON DELETE CASCADE
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS admin_actions (
            action_id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER,
            admin_name TEXT,
            action_type TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id INTEGER,
            details TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ai_interactions (
            interaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            feature_type TEXT NOT NULL,
            query_text TEXT,
            result_summary TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS rental_status_log (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER NOT NULL,
            previous_status TEXT,
            new_status TEXT NOT NULL,
            changed_by INTEGER,
            action_role TEXT DEFAULT 'user',
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (request_id) REFERENCES rental_requests(request_id) ON DELETE CASCADE,
            FOREIGN KEY (changed_by) REFERENCES users(user_id) ON DELETE SET NULL
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS verification_history (
            history_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            previous_status TEXT,
            new_status TEXT NOT NULL,
            action_type TEXT NOT NULL,
            submitted_at TEXT DEFAULT NULL,
            resubmitted_at TEXT DEFAULT NULL,
            reviewed_at TEXT DEFAULT NULL,
            reviewed_by INTEGER DEFAULT NULL,
            rejection_reason TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        );
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_items_category ON items(category);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_items_transaction_type ON items(transaction_type);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_items_user_id ON items(user_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_item_images_item_id ON item_images(item_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_rental_requests_users ON rental_requests(renter_id, owner_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_rental_status_log_req ON rental_status_log(request_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_verification_history_user_id ON verification_history(user_id);")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            conversation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id INTEGER NOT NULL,
            owner_id INTEGER NOT NULL,
            interested_user_id INTEGER NOT NULL,
            request_id INTEGER DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_message_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            CHECK (owner_id <> interested_user_id),
            UNIQUE (listing_id, owner_id, interested_user_id),
            FOREIGN KEY (listing_id) REFERENCES items(item_id) ON DELETE CASCADE,
            FOREIGN KEY (owner_id) REFERENCES users(user_id) ON DELETE CASCADE,
            FOREIGN KEY (interested_user_id) REFERENCES users(user_id) ON DELETE CASCADE
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            message_id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            sender_id INTEGER NOT NULL,
            message_text TEXT NOT NULL,
            is_read INTEGER NOT NULL DEFAULT 0,
            client_msg_id TEXT DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE,
            FOREIGN KEY (sender_id) REFERENCES users(user_id) ON DELETE CASCADE
        );
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_conversations_owner_last ON conversations(owner_id, last_message_at DESC);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_conversations_interested_last ON conversations(interested_user_id, last_message_at DESC);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_conversations_listing ON conversations(listing_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_conv_id ON messages(conversation_id, message_id ASC);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_conv_unread ON messages(conversation_id, sender_id, is_read);")
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_client_idemp ON messages(conversation_id, sender_id, client_msg_id) WHERE client_msg_id IS NOT NULL;")
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_reviews_unique_rental_reviewer ON reviews(rental_id, reviewer_id) WHERE rental_id IS NOT NULL;")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS auth_rate_limits (
            scope_key TEXT PRIMARY KEY,
            attempts INTEGER NOT NULL DEFAULT 0,
            locked_until REAL NOT NULL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            email TEXT NOT NULL,
            expires_at REAL NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pwd_reset_expires ON password_reset_tokens(expires_at);")

    conn.commit()
    cursor.close()
    conn.close()

    seed_demo_data_if_empty()


def seed_demo_data_if_empty():
    """
    Optionally seed initial accounts ONLY when ENABLE_DEMO_SEED=true and passwords are
    explicitly supplied via environment variables (no hardcoded default credentials).
    """
    if not Config.ENABLE_DEMO_SEED:
        return

    admin_pw = os.getenv("SEED_ADMIN_PASSWORD", "").strip()
    student_pw = os.getenv("SEED_STUDENT_PASSWORD", "").strip()
    seller_pw = os.getenv("SEED_SELLER_PASSWORD", "").strip()
    if not (admin_pw or student_pw or seller_pw):
        return

    existing = execute_query_all("""
        SELECT user_id, LOWER(email) AS email, is_admin, verification_status
        FROM users
        WHERE LOWER(email) IN (
            'admin@rent2swap.com',
            'test.student@college.edu',
            'seller@rentitbro.com'
        )
    """)
    by_email = {row["email"]: row for row in existing}

    if admin_pw and "admin@rent2swap.com" not in by_email:
        hashed_admin_pw = generate_password_hash(admin_pw)
        execute_query_commit("""
            INSERT INTO users (name, email, password, college, course, branch, year, location, rating, is_admin, verification_status, status)
            VALUES ('System Administrator', 'admin@rent2swap.com', ?, 'Administration Block', 'Management', 'IT Admin', 'Staff', 'Campus Hub', 5.0, 1, 'Verified', 'active')
        """, (hashed_admin_pw,))

    if student_pw and "test.student@college.edu" not in by_email:
        hashed_student_pw = generate_password_hash(student_pw)
        execute_query_commit("""
            INSERT INTO users (name, email, password, college, course, branch, year, location, rating, is_admin, verification_status, status)
            VALUES ('Test Student', 'test.student@college.edu', ?, 'Bengaluru Institute of Technology', 'Computer Science', 'CSE', '3rd Year', 'Hostel Block B', 4.9, 0, 'Verified', 'active')
        """, (hashed_student_pw,))

    if seller_pw and "seller@rentitbro.com" not in by_email:
        hashed_pw = generate_password_hash(seller_pw)
        execute_query_commit("""
            INSERT INTO users (name, email, password, college, course, branch, year, location, rating, is_admin, verification_status, status)
            VALUES ('Vikram Malhotra', 'seller@rentitbro.com', ?, 'Indiranagar Campus', 'Business User', 'Management', 'Alumni', 'Indiranagar, Bengaluru', 4.95, 0, 'Verified', 'active')
        """, (hashed_pw,))
    return


# ==============================================================================
# HELPER FUNCTIONS
# ==============================================================================

def allowed_file(filename):
    """Verify if the uploaded file has an allowed extension."""
    if not filename or "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in Config.ALLOWED_EXTENSIONS


_EXT_TO_MIME = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "pdf": "application/pdf",
}


def _normalize_mime_type(ext: str, fallback_mime: str = "") -> str:
    """Return a canonical MIME type for supported image/document extensions."""
    ext_clean = (ext or "").strip().lower().lstrip(".")
    if ext_clean in _EXT_TO_MIME:
        return _EXT_TO_MIME[ext_clean]
    if fallback_mime and fallback_mime != "application/octet-stream":
        if fallback_mime == "image/jpg":
            return "image/jpeg"
        return fallback_mime
    return "image/jpeg"


def _is_resolvable_image(image_path) -> bool:
    """
    Return True if the image path points to a persistent remote URL, private verification doc,
    or an existing file inside static/. Returns False for blob:/data: URIs or missing local uploads.
    """
    if not image_path:
        return False
    img_str = str(image_path).strip()
    if not img_str:
        return False
    lower = img_str.lower()
    if lower.startswith("blob:") or lower.startswith("data:"):
        return False
    if lower.startswith("http://") or lower.startswith("https://"):
        return True
    if img_str.startswith("verifications/") or img_str.startswith("damage_reports/"):
        return True
    static_root = app.static_folder or os.path.join(Config.BASE_DIR, "static")
    if img_str.startswith("/static/"):
        rel = img_str[8:]
    elif img_str.startswith("static/"):
        rel = img_str[7:]
    else:
        rel = img_str
    if os.path.isfile(os.path.join(static_root, rel)):
        return True
    if rel.startswith("placeholders/") and os.path.isfile(os.path.join(static_root, "images", rel)):
        return True
    return False


def _pick_valid_primary_image(item_id, current_primary, category="Other"):
    """
    Prefer the current primary image when resolvable; otherwise look up another valid
    image row for the listing before falling back to a category SVG placeholder.
    """
    if current_primary and _is_resolvable_image(current_primary):
        return current_primary
    if item_id:
        candidates = execute_query_all(
            "SELECT image_path FROM item_images WHERE item_id = ? ORDER BY is_primary DESC, image_id ASC",
            (item_id,)
        )
        for cand in candidates:
            path = cand.get("image_path")
            if _is_resolvable_image(path):
                return path
    return current_primary or CATEGORY_PLACEHOLDERS.get(category, "placeholders/default.svg")


def _get_supabase_storage_key() -> str:
    """
    Return the privileged Supabase Storage credential strictly from SUPABASE_SERVICE_ROLE_KEY.
    Never falls back to anon/publishable keys.
    """
    return (getattr(Config, "SUPABASE_SERVICE_ROLE_KEY", "") or "").strip()


def _build_supabase_storage_headers(service_key: str, extra: dict | None = None) -> dict:
    """
    Build Supabase Storage REST headers compatible with both legacy JWT service_role keys
    (Authorization: Bearer + apikey) and modern sb_secret_ keys (apikey only).
    """
    headers = {"apikey": service_key}
    # Legacy JWT keys contain 3 dot-separated segments (usually starting with eyJ)
    if service_key and not service_key.startswith("sb_") and service_key.count(".") == 2:
        headers["Authorization"] = f"Bearer {service_key}"
    if extra:
        headers.update(extra)
    return headers


def upload_bytes_to_supabase(bucket: str, filename: str, data: bytes, mime_type: str) -> bool:
    """Upload raw file bytes directly to Supabase Storage REST endpoint using service-role key."""
    proj_id = Config.SUPABASE_PROJECT_ID
    service_key = _get_supabase_storage_key()
    if not (proj_id and service_key):
        print(f"[Supabase Storage] Storage write unavailable for {bucket}: SUPABASE_SERVICE_ROLE_KEY not configured.")
        return False
    safe_bucket = urllib.parse.quote(bucket, safe="")
    safe_name = urllib.parse.quote(filename, safe="")
    url = f"https://{proj_id}.supabase.co/storage/v1/object/{safe_bucket}/{safe_name}"
    headers = _build_supabase_storage_headers(
        service_key,
        {"Content-Type": mime_type, "x-upsert": "true"},
    )
    max_attempts = 2
    for attempt in range(1, max_attempts + 1):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                if resp.status in (200, 201):
                    return True
                print(f"[Supabase Storage Upload Error] Unexpected HTTP {resp.status} for {bucket}")
        except urllib.error.HTTPError as e:
            print(f"[Supabase Storage Upload Error] Attempt {attempt}/{max_attempts} HTTP {e.code} for {bucket}")
            if e.code not in (429, 500, 502, 503, 504):
                return False
        except Exception as e:
            print(f"[Supabase Storage Upload Error] Attempt {attempt}/{max_attempts} failed for {bucket}: {type(e).__name__}")
        if attempt < max_attempts:
            time.sleep(0.3 * attempt)
    return False


_EXPECTED_PIL_FORMATS = {
    "jpg": {"JPEG"},
    "jpeg": {"JPEG"},
    "png": {"PNG"},
    "webp": {"WEBP"},
}


def _verify_image_bytes(data: bytes, ext: str, raw_filename: str) -> tuple:
    """Verify real image binary content and format match using Pillow."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            fmt = (img.format or "").upper()
            img.verify()
        expected = _EXPECTED_PIL_FORMATS.get(ext.lower(), set())
        if not fmt or fmt not in expected:
            return False, f"Image content for '{raw_filename}' does not match its .{ext} extension."
        return True, None
    except (UnidentifiedImageError, Exception):
        return False, f"File '{raw_filename}' is corrupted or not a valid JPG, PNG, or WEBP image."


def validate_listing_image_file(file_storage) -> tuple:
    """Validate extension, MIME type, binary image header, and file size of a listing image."""
    if not file_storage or not getattr(file_storage, "filename", None):
        return False, "Empty image file submitted."

    raw_filename = file_storage.filename.strip()
    if "." not in raw_filename:
        return False, f"Image '{raw_filename}' is missing a file extension (JPG, PNG, or WEBP required)."

    ext = raw_filename.rsplit(".", 1)[-1].lower()
    if ext not in Config.ALLOWED_EXTENSIONS:
        return False, f"Unsupported image format for '{raw_filename}'. Please use JPG, PNG, or WEBP."

    mimetype = (getattr(file_storage, "mimetype", "") or "").lower()
    if mimetype and not mimetype.startswith("image/") and mimetype != "application/octet-stream":
        return False, f"Invalid file type ({mimetype}) for '{raw_filename}'. Only image files are allowed."

    try:
        file_storage.seek(0)
        file_bytes = file_storage.read()
        file_storage.seek(0)
    except Exception:
        return False, f"Could not read image '{raw_filename}'."

    file_size = len(file_bytes)
    if file_size <= 0:
        return False, f"Image '{raw_filename}' is empty (0 bytes)."

    max_single = getattr(Config, "MAX_IMAGE_FILE_SIZE", 10 * 1024 * 1024)
    if file_size > max_single:
        max_mb = max_single // (1024 * 1024)
        return False, f"Image '{raw_filename}' exceeds the {max_mb}MB per-photo size limit."

    return _verify_image_bytes(file_bytes, ext, raw_filename)


def validate_verification_document_file(file_storage) -> tuple:
    """Validate student ID document extension, size, and real binary signature (JPG/PNG/WEBP/PDF)."""
    if not file_storage or not getattr(file_storage, "filename", None):
        return False, "No College ID file selected."

    raw_filename = file_storage.filename.strip()
    if "." not in raw_filename:
        return False, f"Document '{raw_filename}' is missing a file extension (JPG, PNG, WEBP, or PDF required)."

    ext = raw_filename.rsplit(".", 1)[-1].lower()
    if ext not in (set(Config.ALLOWED_EXTENSIONS) | {"pdf"}):
        return False, f"Unsupported format for '{raw_filename}'. Allowed formats: JPG, PNG, WEBP, PDF."

    mimetype = (getattr(file_storage, "mimetype", "") or "").lower()
    if mimetype and not mimetype.startswith("image/") and mimetype not in ("application/pdf", "application/octet-stream"):
        return False, f"Invalid file MIME type for '{raw_filename}'."

    try:
        file_storage.seek(0)
        file_bytes = file_storage.read()
        file_storage.seek(0)
    except Exception:
        return False, f"Failed to read uploaded file '{raw_filename}'."

    file_size = len(file_bytes)
    if file_size <= 0:
        return False, f"Uploaded file '{raw_filename}' is empty."

    max_doc = getattr(Config, "MAX_VERIFICATION_FILE_SIZE", 10 * 1024 * 1024)
    if file_size > max_doc:
        return False, f"Document '{raw_filename}' exceeds the {max_doc // (1024 * 1024)}MB size limit."

    if ext == "pdf":
        if not file_bytes.lstrip().startswith(b"%PDF-"):
            return False, f"File '{raw_filename}' does not have a valid PDF signature."
        return True, None

    return _verify_image_bytes(file_bytes, ext, raw_filename)


def upload_listing_image_strict(file_storage, user_id: int | None = None) -> tuple:
    """
    Upload a validated listing image to Supabase Storage (listings bucket).
    Returns (success: bool, public_url: str|None, local_cache_path: str|None, error_msg: str|None).
    """
    ok, val_err = validate_listing_image_file(file_storage)
    if not ok:
        return False, None, None, val_err

    raw_filename = file_storage.filename.strip()
    ext = raw_filename.rsplit(".", 1)[-1].lower()
    try:
        file_storage.seek(0)
        file_bytes = file_storage.read()
        file_storage.seek(0)
    except Exception as read_err:
        return False, None, None, f"Failed to read '{raw_filename}': {read_err}"

    base_name = raw_filename.rsplit(".", 1)[0]
    clean_base = secure_filename(base_name) or "item_photo"
    owner_prefix = f"u{int(user_id)}_" if user_id is not None else ""
    new_filename = f"{owner_prefix}{uuid.uuid4().hex[:12]}_{clean_base}.{ext}"

    guessed_mime, _ = mimetypes.guess_type(new_filename)
    mime_type = _normalize_mime_type(ext, guessed_mime or file_storage.mimetype or "")

    uploaded = upload_bytes_to_supabase("listings", new_filename, file_bytes, mime_type)
    if not uploaded:
        return False, None, None, f"Could not upload photo '{raw_filename}' to cloud storage. Please try again."

    # Keep local cache copy when writable
    local_dest = os.path.join(Config.UPLOAD_FOLDER, new_filename)
    try:
        os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
        with open(local_dest, "wb") as f:
            f.write(file_bytes)
    except Exception as cache_err:
        print(f"[Local Cache Note] {cache_err}")
        local_dest = None

    public_url = f"https://{Config.SUPABASE_PROJECT_ID}.supabase.co/storage/v1/object/public/listings/{new_filename}"
    return True, public_url, local_dest, None


def _upload_prepared_images_parallel(prepared_files: list, user_id: int | None = None) -> tuple:
    """
    Concurrently upload pre-read image payloads [(raw_filename, ext, mimetype, file_bytes), ...]
    to Supabase Storage while preserving original order.
    Returns (success: bool, public_urls: list[str], local_caches: list[str], error_msg: str|None).
    """
    if not prepared_files:
        return True, [], [], None

    owner_prefix = f"u{int(user_id)}_" if user_id is not None else ""

    def _upload_one(idx_item):
        idx, raw_filename, ext, orig_mime, file_bytes = idx_item
        base_name = raw_filename.rsplit(".", 1)[0]
        clean_base = secure_filename(base_name) or "item_photo"
        new_filename = f"{owner_prefix}{uuid.uuid4().hex[:12]}_{clean_base}.{ext}"
        guessed_mime, _ = mimetypes.guess_type(new_filename)
        mime_type = _normalize_mime_type(ext, guessed_mime or orig_mime or "")

        uploaded = upload_bytes_to_supabase("listings", new_filename, file_bytes, mime_type)
        if not uploaded:
            return idx, False, None, None, f"Could not upload photo '{raw_filename}' to cloud storage. Please try again."

        local_dest = os.path.join(Config.UPLOAD_FOLDER, new_filename)
        try:
            os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
            with open(local_dest, "wb") as f:
                f.write(file_bytes)
        except Exception as cache_err:
            print(f"[Local Cache Note] {cache_err}")
            local_dest = None

        public_url = f"https://{Config.SUPABASE_PROJECT_ID}.supabase.co/storage/v1/object/public/listings/{new_filename}"
        return idx, True, public_url, local_dest, None

    ordered_urls = [None] * len(prepared_files)
    local_caches = []
    first_error = None
    max_workers = min(8, len(prepared_files))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(_upload_one, (idx, rf, ext, mm, fb))
            for idx, (rf, ext, mm, fb) in enumerate(prepared_files)
        ]
        for fut in as_completed(futures):
            idx, ok, pub_url, cache_path, err = fut.result()
            if cache_path:
                local_caches.append(cache_path)
            if ok and pub_url:
                ordered_urls[idx] = pub_url
            elif first_error is None:
                first_error = err

    if first_error or any(u is None for u in ordered_urls):
        for p in local_caches:
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass
        return False, [], [], (first_error or "Failed to upload one or more photos to cloud storage. Please try again.")

    return True, ordered_urls, local_caches, None


def save_upload_file(file_storage, bucket="listings"):
    """
    Safely validate and persist an uploaded image/PDF file to Supabase Storage.
    SECURITY: Verification ID documents ('verifications') and damage evidence ('damage_reports')
    are NEVER saved to public static/uploads/ so sensitive files cannot be fetched without authorization.
    """
    if bucket == "verifications":
        ok, _ = validate_verification_document_file(file_storage)
    else:
        ok, _ = validate_listing_image_file(file_storage)
    if not ok:
        return None

    raw_filename = file_storage.filename.strip()
    ext = raw_filename.rsplit(".", 1)[-1].lower()
    try:
        file_storage.seek(0)
        file_bytes = file_storage.read()
        file_storage.seek(0)
    except Exception as read_err:
        print(f"[Upload Read Error] {type(read_err).__name__}")
        return None

    base_name = raw_filename.rsplit(".", 1)[0]
    clean_base = secure_filename(base_name) or "item_photo"
    unique_prefix = uuid.uuid4().hex[:12]
    new_filename = f"{unique_prefix}_{clean_base}.{ext}"

    guessed_mime, _ = mimetypes.guess_type(new_filename)
    mime_type = _normalize_mime_type(ext, guessed_mime or file_storage.mimetype or "")

    uploaded_to_supabase = upload_bytes_to_supabase(bucket, new_filename, file_bytes, mime_type)
    if uploaded_to_supabase:
        if bucket == "listings":
            try:
                os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
                with open(os.path.join(Config.UPLOAD_FOLDER, new_filename), "wb") as f:
                    f.write(file_bytes)
            except Exception as cache_err:
                print(f"[Local Cache Note] {cache_err}")
            return f"https://{Config.SUPABASE_PROJECT_ID}.supabase.co/storage/v1/object/public/listings/{new_filename}"
        elif bucket in ("verifications", "damage_reports"):
            return f"{bucket}/{new_filename}"
        else:
            return f"{bucket}/{new_filename}"

    if bucket in ("verifications", "damage_reports"):
        print(f"[Supabase Storage] Failed to persist private document '{new_filename}' to bucket '{bucket}'.")
        return None

    os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
    destination_path = os.path.join(Config.UPLOAD_FOLDER, new_filename)
    try:
        with open(destination_path, "wb") as f:
            f.write(file_bytes)
    except Exception as e:
        print(f"[Local Save Error] {e}")
        return None

    return f"uploads/{new_filename}"


def _create_supabase_signed_url(bucket: str, filename: str, expires_in: int = 600) -> str | None:
    """
    Generate a short-lived Supabase Storage signed URL on the backend using service-role key.
    Never exposes Supabase API keys to the client; returns None if unavailable.
    """
    proj_id = Config.SUPABASE_PROJECT_ID
    service_key = _get_supabase_storage_key()
    if not (proj_id and service_key and bucket and filename):
        return None
    safe_bucket = urllib.parse.quote(bucket, safe="")
    safe_name = urllib.parse.quote(filename, safe="")
    sign_endpoint = f"https://{proj_id}.supabase.co/storage/v1/object/sign/{safe_bucket}/{safe_name}"
    payload = json.dumps({"expiresIn": int(expires_in)}).encode("utf-8")
    req = urllib.request.Request(
        sign_endpoint,
        data=payload,
        headers=_build_supabase_storage_headers(service_key, {"Content-Type": "application/json"}),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            signed_path = body.get("signedURL") or body.get("signedUrl")
            if signed_path:
                if signed_path.startswith("http://") or signed_path.startswith("https://"):
                    return signed_path
                if not signed_path.startswith("/"):
                    signed_path = "/" + signed_path
                return f"https://{proj_id}.supabase.co/storage/v1{signed_path}"
    except urllib.error.HTTPError as e:
        print(f"[Supabase Signed URL Error] HTTP {e.code} for {bucket}")
    except Exception as e:
        print(f"[Supabase Signed URL Error] {bucket}: {type(e).__name__}")
    return None


def _resolve_admin_verification_doc_info(user_id: int, doc_path: str | None, side: str) -> dict:
    """
    Inspect a stored verification document path and resolve a short-lived signed URL
    (or detect missing legacy files) exclusively for authorized admin review UI.
    """
    raw = str(doc_path or "").strip()
    if not raw:
        return {
            "has_doc": False,
            "status": "none",
            "raw_path": None,
            "is_pdf": False,
            "signed_url": None,
            "view_url": None,
            "error": "Not uploaded",
        }

    lower = raw.lower()
    is_pdf = lower.endswith(".pdf")
    filename = os.path.basename(raw.lstrip("/"))
    proxy_url = url_for("admin_serve_verification_doc", user_id=user_id, side=side)

    if lower.startswith("blob:") or lower.startswith("data:"):
        return {
            "has_doc": True,
            "status": "missing",
            "raw_path": raw,
            "is_pdf": is_pdf,
            "signed_url": None,
            "view_url": None,
            "error": "Non-persistent browser URI — student must re-upload.",
        }

    if raw.startswith("verifications/"):
        signed = _create_supabase_signed_url("verifications", filename, expires_in=600)
        if signed:
            return {
                "has_doc": True,
                "status": "ok",
                "raw_path": raw,
                "is_pdf": is_pdf,
                "signed_url": signed,
                "view_url": signed,
                "proxy_url": proxy_url,
                "error": None,
            }
        # Fallback check: check if local copy exists cleanly
        local_disk_path = os.path.join(Config.UPLOAD_FOLDER, filename)
        if os.path.isfile(local_disk_path):
            return {
                "has_doc": True,
                "status": "ok",
                "raw_path": raw,
                "is_pdf": is_pdf,
                "signed_url": None,
                "view_url": proxy_url,
                "proxy_url": proxy_url,
                "error": None,
            }
        return {
            "has_doc": True,
            "status": "missing",
            "raw_path": raw,
            "is_pdf": is_pdf,
            "signed_url": None,
            "view_url": None,
            "proxy_url": proxy_url,
            "error": f"Original file '{filename}' is missing from private Supabase Storage.",
        }

    # Legacy uploads/ path
    local_disk_path = os.path.join(Config.UPLOAD_FOLDER, filename)
    if os.path.isfile(local_disk_path):
        return {
            "has_doc": True,
            "status": "ok",
            "raw_path": raw,
            "is_pdf": is_pdf,
            "signed_url": None,
            "view_url": proxy_url,
            "proxy_url": proxy_url,
            "error": None,
        }

    # Also check if a file with that filename happens to exist in Supabase verifications bucket
    signed = _create_supabase_signed_url("verifications", filename, expires_in=600)
    if signed:
        return {
            "has_doc": True,
            "status": "ok",
            "raw_path": raw,
            "is_pdf": is_pdf,
            "signed_url": signed,
            "view_url": signed,
            "proxy_url": proxy_url,
            "error": None,
        }

    return {
        "has_doc": True,
        "status": "missing",
        "raw_path": raw,
        "is_pdf": is_pdf,
        "signed_url": None,
        "view_url": None,
        "proxy_url": proxy_url,
        "error": f"Legacy local file '{filename}' no longer exists on disk or in storage.",
    }


def resolve_image_url(image_path, category=None):
    """
    Safely resolve an item, profile, or id document path to a valid web URL.
    Handles Supabase public URLs, private verification documents, local uploads (with missing-file detection), and SVGs.
    """
    fallback = CATEGORY_PLACEHOLDERS.get(category, "placeholders/default.svg") if category else "placeholders/default.svg"
    fallback_nested = "images/" + fallback if fallback.startswith("placeholders/") else fallback

    if not image_path:
        return url_for("static", filename=fallback_nested)

    img_str = str(image_path).strip()
    if not img_str:
        return url_for("static", filename=fallback_nested)

    lower = img_str.lower()
    if lower.startswith("blob:") or lower.startswith("data:"):
        print(f"[Image Pipeline Warning] Blocked non-persistent image URI '{img_str[:60]}'; serving placeholder.")
        return url_for("static", filename=fallback_nested)

    if lower.startswith("http://") or lower.startswith("https://"):
        return img_str
    if img_str.startswith("verifications/"):
        return url_for("serve_verification_document", doc_path=img_str)
    if img_str.startswith("damage_reports/"):
        return url_for("static", filename=fallback_nested)

    static_root = app.static_folder or os.path.join(Config.BASE_DIR, "static")

    if img_str.startswith("/static/"):
        rel = img_str[8:]
        if rel.startswith("uploads/") and not os.path.isfile(os.path.join(static_root, rel)):
            print(f"[Image Pipeline Warning] Missing local upload file '{img_str}'; serving placeholder.")
            return url_for("static", filename=fallback_nested)
        return img_str

    if img_str.startswith("static/"):
        rel = img_str[7:]
        if rel.startswith("uploads/") and not os.path.isfile(os.path.join(static_root, rel)):
            print(f"[Image Pipeline Warning] Missing local upload file '{img_str}'; serving placeholder.")
            return url_for("static", filename=fallback_nested)
        return url_for("static", filename=rel)

    if img_str.startswith("uploads/"):
        if not os.path.isfile(os.path.join(static_root, img_str)):
            print(f"[Image Pipeline Warning] Missing local upload file '{img_str}'; serving placeholder.")
            return url_for("static", filename=fallback_nested)
        return url_for("static", filename=img_str)

    if img_str.startswith("placeholders/"):
        direct_path = os.path.join(static_root, img_str)
        if not os.path.exists(direct_path):
            nested_path = os.path.join(static_root, "images", img_str)
            if os.path.exists(nested_path):
                return url_for("static", filename="images/" + img_str)
    return url_for("static", filename=img_str)


# Register filter and global for templates
app.jinja_env.filters["image_url"] = resolve_image_url
app.jinja_env.globals["image_url"] = resolve_image_url
app.jinja_env.globals["is_sample_email"] = is_sample_email


def get_item_primary_image(item_id, category="Other"):
    """
    Retrieve the best resolvable primary image path for an item, falling back to a category placeholder.
    """
    rows = execute_query_all(
        "SELECT image_path FROM item_images WHERE item_id = ? ORDER BY is_primary DESC, image_id ASC",
        (item_id,)
    )
    for rec in rows:
        if _is_resolvable_image(rec.get("image_path")):
            return rec["image_path"]
    return CATEGORY_PLACEHOLDERS.get(category, "placeholders/default.svg")


def _wants_json_response() -> bool:
    """Return True when the request expects a JSON response (AJAX / fetch)."""
    return (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.is_json
        or "application/json" in request.headers.get("Accept", "")
        or request.args.get("ajax") == "1"
        or request.form.get("_ajax") == "1"
    )


def login_required(f):
    """
    Decorator for routes requiring authenticated, non-suspended user session.
    Redirects unauthenticated visitors to login, or returns 401/403 JSON for AJAX requests.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user_id = session.get("user_id")
        if not user_id:
            next_url = request.full_path if request.query_string else request.path
            if _wants_json_response():
                return jsonify({
                    "success": False,
                    "message": "Please sign in again to continue.",
                    "error": "Please sign in again to continue.",
                    "redirect": url_for("login", next=next_url)
                }), 401
            flash("Please sign in to access this feature.", "warning")
            return redirect(url_for("login", next=next_url))

        auth_user = execute_query_one(
            "SELECT user_id, name, email, is_admin, status, session_version FROM users WHERE user_id = ?",
            (user_id,)
        )
        sess_ver = session.get("session_version")
        ver_mismatch = (sess_ver is not None and int(auth_user.get("session_version") or 1) != int(sess_ver)) if auth_user else True
        if not auth_user or _is_user_suspended(auth_user) or ver_mismatch:
            is_susp = bool(auth_user and _is_user_suspended(auth_user))
            session.clear()
            msg = "Your account has been suspended by an administrator." if is_susp else "Your session is no longer valid. Please sign in again."
            if _wants_json_response():
                return jsonify({"success": False, "message": msg, "redirect": url_for("login")}), (403 if is_susp else 401)
            flash(msg, "danger" if is_susp else "warning")
            return redirect(url_for("login"))

        session["is_admin"] = _is_user_admin(auth_user)
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    """
    Decorator for routes requiring administrator access.
    Always re-verifies admin privileges, session version, and active account status against DB.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user_id = session.get("user_id")
        if not user_id:
            if _wants_json_response():
                return jsonify({
                    "success": False,
                    "message": "Please sign in as an administrator to access the staff portal.",
                    "redirect": url_for("login", next=request.url)
                }), 401
            flash("Please sign in as an administrator to access the staff portal.", "warning")
            return redirect(url_for("login", next=request.url))

        user = execute_query_one(
            "SELECT user_id, name, email, is_admin, status, session_version FROM users WHERE user_id = ?",
            (user_id,)
        )
        sess_ver = session.get("session_version")
        ver_mismatch = (sess_ver is not None and int(user.get("session_version") or 1) != int(sess_ver)) if user else True
        if not user or _is_user_suspended(user) or ver_mismatch:
            session.clear()
            msg = "Your administrator account is unavailable, suspended, or session expired."
            if _wants_json_response():
                return jsonify({"success": False, "message": msg, "redirect": url_for("login")}), 403
            flash(msg, "danger")
            return redirect(url_for("login"))

        if not _is_user_admin(user):
            session["is_admin"] = False
            msg = "Access denied. Administrator privileges required."
            if _wants_json_response():
                return jsonify({"success": False, "message": msg}), 403
            flash(msg, "danger")
            return redirect(url_for("home"))

        session["is_admin"] = True
        session["user_name"] = user.get("name") or session.get("user_name")
        return f(*args, **kwargs)
    return decorated_function


@app.route("/admin/verification-doc/<int:user_id>/<side>")
@admin_required
def admin_serve_verification_doc(user_id, side):
    """
    Strictly admin-only endpoint to view a student's College ID front or back image.
    Uses service-role Storage auth or local cache; returns 404 if missing.
    """
    side_norm = (side or "").strip().lower()
    if side_norm not in ("front", "back"):
        abort(404)

    target = execute_query_one("""
        SELECT user_id, id_card_image, id_card_front_image, id_card_back_image,
               college_id_front_path, college_id_back_path
        FROM users WHERE user_id = ?
    """, (user_id,))
    if not target:
        abort(404)

    if side_norm == "front":
        doc_path = target.get("id_card_front_image") or target.get("college_id_front_path") or target.get("id_card_image")
    else:
        doc_path = target.get("id_card_back_image") or target.get("college_id_back_path")

    if not doc_path:
        abort(404)

    filename = os.path.basename(str(doc_path).strip().lstrip("/"))
    if not filename or filename in (".", ".."):
        abort(404)

    if str(doc_path).startswith("verifications/"):
        proj_id = Config.SUPABASE_PROJECT_ID
        service_key = _get_supabase_storage_key()
        if proj_id and service_key:
            safe_name = urllib.parse.quote(filename, safe="")
            fetch_url = f"https://{proj_id}.supabase.co/storage/v1/object/authenticated/verifications/{safe_name}"
            req = urllib.request.Request(fetch_url, headers=_build_supabase_storage_headers(service_key))
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    content = resp.read()
                    mime, _ = mimetypes.guess_type(filename)
                    mime = mime or resp.headers.get_content_type() or "image/jpeg"
                    r = Response(content, mimetype=mime)
                    r.headers["Cache-Control"] = "private, no-store, max-age=0"
                    return r
            except Exception as e:
                print(f"[Admin Verification Doc Fetch Error] {type(e).__name__}")

    local_disk_path = os.path.join(Config.UPLOAD_FOLDER, filename)
    if os.path.isfile(local_disk_path):
        r = send_file(local_disk_path)
        r.headers["Cache-Control"] = "private, no-store, max-age=0"
        return r

    abort(404)


@app.route("/verification-docs/<path:doc_path>")
@login_required
def serve_verification_document(doc_path):
    """
    Strict privacy enforcement: Only the student who uploaded the ID document
    or an authorized administrator can view the ID card images/PDFs.
    """
    current_user_id = session.get("user_id")
    user = execute_query_one(
        "SELECT user_id, is_admin, status, email FROM users WHERE user_id = ?",
        (current_user_id,)
    )
    if not user or _is_user_suspended(user):
        abort(403)
    is_admin = _is_user_admin(user)

    doc_str = doc_path.strip().lstrip("/")
    filename = os.path.basename(doc_str)
    if not filename or filename in (".", ".."):
        abort(404)

    if not is_admin:
        ver_path = f"verifications/{filename}"
        up_path = f"uploads/{filename}"
        owned = execute_query_one("""
            SELECT user_id FROM users 
            WHERE user_id = ? AND (
                id_card_image IN (?, ?, ?) OR
                id_card_front_image IN (?, ?, ?) OR
                id_card_back_image IN (?, ?, ?) OR
                college_id_front_path IN (?, ?, ?) OR
                college_id_back_path IN (?, ?, ?)
            )
        """, (
            current_user_id,
            doc_str, up_path, ver_path,
            doc_str, up_path, ver_path,
            doc_str, up_path, ver_path,
            doc_str, up_path, ver_path,
            doc_str, up_path, ver_path,
        ))
        if not owned:
            abort(403)

    proj_id = Config.SUPABASE_PROJECT_ID
    service_key = _get_supabase_storage_key()
    if proj_id and service_key:
        safe_name = urllib.parse.quote(filename, safe="")
        fetch_url = f"https://{proj_id}.supabase.co/storage/v1/object/authenticated/verifications/{safe_name}"
        req = urllib.request.Request(fetch_url, headers=_build_supabase_storage_headers(service_key))
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                content = resp.read()
                mime, _ = mimetypes.guess_type(filename)
                mime = mime or resp.headers.get_content_type() or "image/jpeg"
                r = Response(content, mimetype=mime)
                r.headers["Cache-Control"] = "private, no-store, max-age=0"
                return r
        except Exception as e:
            print(f"[Verification Doc Fetch Error] {type(e).__name__}")

    local_disk_path = os.path.join(Config.UPLOAD_FOLDER, filename)
    if os.path.isfile(local_disk_path):
        r = send_file(local_disk_path)
        r.headers["Cache-Control"] = "private, no-store, max-age=0"
        return r

    abort(404)


@app.route("/damage-report-photo/<int:report_id>")
@login_required
def serve_damage_report_photo(report_id):
    """
    Strictly authorized endpoint to view evidence photos for a damage report.
    Accessible only to the reporter, transaction owner, transaction renter, or admins.
    """
    current_user_id = session.get("user_id")
    user = execute_query_one("SELECT user_id, is_admin, status FROM users WHERE user_id = ?", (current_user_id,))
    if not user or _is_user_suspended(user):
        abort(403)
    is_admin = _is_user_admin(user)

    rep = execute_query_one("""
        SELECT dr.report_id, dr.reporter_id, dr.photo_path, r.owner_id, r.renter_id
        FROM damage_reports dr
        JOIN rental_requests r ON dr.rental_id = r.request_id
        WHERE dr.report_id = ?
    """, (report_id,))
    if not rep or not rep.get("photo_path"):
        abort(404)

    participants = {int(rep["reporter_id"]), int(rep["owner_id"]), int(rep["renter_id"])}
    if not is_admin and int(current_user_id) not in participants:
        abort(403)

    photo_str = str(rep["photo_path"]).strip()
    filename = os.path.basename(photo_str.lstrip("/"))
    if not filename or filename in (".", ".."):
        abort(404)

    if photo_str.startswith("damage_reports/"):
        proj_id = Config.SUPABASE_PROJECT_ID
        service_key = _get_supabase_storage_key()
        if proj_id and service_key:
            safe_name = urllib.parse.quote(filename, safe="")
            fetch_url = f"https://{proj_id}.supabase.co/storage/v1/object/authenticated/damage_reports/{safe_name}"
            req = urllib.request.Request(fetch_url, headers=_build_supabase_storage_headers(service_key))
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    content = resp.read()
                    mime, _ = mimetypes.guess_type(filename)
                    mime = mime or resp.headers.get_content_type() or "image/jpeg"
                    r = Response(content, mimetype=mime)
                    r.headers["Cache-Control"] = "private, no-store, max-age=0"
                    return r
            except Exception as e:
                print(f"[Damage Report Photo Fetch Error] {type(e).__name__}")

    if photo_str.startswith("uploads/"):
        local_disk_path = os.path.join(Config.UPLOAD_FOLDER, filename)
        if os.path.isfile(local_disk_path):
            r = send_file(local_disk_path)
            r.headers["Cache-Control"] = "private, no-store, max-age=0"
            return r

    abort(404)


def log_admin_action(action_type: str, target_type: str, target_id: int, details: str):
    """Record administrative decision into admin_actions audit log."""
    admin_id = session.get("user_id")
    admin_name = session.get("user_name", "System Administrator")
    try:
        execute_query_commit("""
            INSERT INTO admin_actions (admin_id, admin_name, action_type, target_type, target_id, details)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (admin_id, admin_name, action_type, target_type, target_id, details))
    except Exception as e:
        print(f"[Admin Log Error] Could not record action: {e}")


def log_ai_interaction(feature_type: str, query_text: str, result_summary: str):
    """Log an AI query or match generation to ai_interactions table."""
    user_id = session.get("user_id")
    try:
        execute_query_commit("""
            INSERT INTO ai_interactions (user_id, feature_type, query_text, result_summary)
            VALUES (?, ?, ?, ?)
        """, (user_id, feature_type, query_text[:500] if query_text else "", result_summary[:500] if result_summary else ""))
    except Exception as e:
        print(f"[AI Log Error] Could not record interaction: {e}")


# ==============================================================================
# PUBLIC MARKETPLACE & CORE ROUTES
# ==============================================================================

def _attach_listing_card_metadata(items_list):
    """
    Enrich listing card dictionaries with:
    - verified primary_image (falling back to category placeholder if missing)
    - gallery_images (ordered list of unique resolvable image paths for multi-image preview)
    - image_count
    - normalized seller_verification_status
    - is_sample flag
    """
    if not items_list:
        return items_list

    item_ids = [it.get("item_id") for it in items_list if it.get("item_id")]
    images_by_item = {}
    if item_ids:
        placeholders = ",".join(["?"] * len(item_ids))
        raw_imgs = execute_query_all(
            f"SELECT item_id, image_path FROM item_images WHERE item_id IN ({placeholders}) ORDER BY is_primary DESC, image_id ASC",
            item_ids
        )
        for row in raw_imgs:
            iid = row.get("item_id")
            p = row.get("image_path")
            if iid and p and _is_resolvable_image(p):
                bucket = images_by_item.setdefault(iid, [])
                if p not in bucket:
                    bucket.append(p)

    for it in items_list:
        cat = it.get("category") or "Other"
        fallback_path = CATEGORY_PLACEHOLDERS.get(cat, "placeholders/default.svg")
        gallery = images_by_item.get(it.get("item_id"), [])
        curr_primary = it.get("primary_image")
        if curr_primary and _is_resolvable_image(curr_primary) and curr_primary not in gallery:
            gallery.insert(0, curr_primary)
        if not gallery:
            resolved = _pick_valid_primary_image(it.get("item_id"), curr_primary, cat)
            if resolved and _is_resolvable_image(resolved):
                gallery = [resolved]
            else:
                gallery = [fallback_path]
        it["gallery_images"] = gallery
        it["primary_image"] = gallery[0]
        it["image_count"] = len(gallery)
        it["fallback_image"] = fallback_path
        it["seller_verification_status"] = normalize_verification_status(it.get("seller_verification_status"))
        it["is_sample"] = bool(it.get("is_sample")) or is_sample_email(it.get("seller_email"))
    return items_list


@app.route("/")
def home():
    """
    Commercial marketplace home page featuring search, categories, and active listings.
    """
    # 1. Fetch featured available listings (up to 8) — omit private seller email
    featured_query = """
        SELECT i.*, u.name as seller_name,
               CASE WHEN LOWER(u.email) IN ('admin@rent2swap.com', 'admin@rentitbro.com', 'test.student@college.edu', 'seller@rentitbro.com') THEN 1 ELSE 0 END AS is_sample,
               u.verification_status as seller_verification_status,
               (SELECT image_path FROM item_images WHERE item_id = i.item_id ORDER BY is_primary DESC, image_id ASC LIMIT 1) as primary_image
        FROM items i
        JOIN users u ON i.user_id = u.user_id
        WHERE i.status = 'Available'
        ORDER BY i.created_at DESC
        LIMIT 8
    """
    featured_items = execute_query_all(featured_query)
    _attach_listing_card_metadata(featured_items)

    # 2. Compute category listing counts for badges
    cat_counts_raw = execute_query_all("""
        SELECT category, COUNT(*) as count 
        FROM items 
        WHERE status = 'Available' 
        GROUP BY category
    """)
    category_counts = {r["category"]: r["count"] for r in cat_counts_raw}

    # 3. Overall platform statistics
    stats = {
        "total_listings": count_query("SELECT COUNT(*) as c FROM items WHERE status = 'Available'"),
        "total_sellers": count_query("SELECT COUNT(DISTINCT user_id) as c FROM items"),
        "total_categories": len(CATEGORIES)
    }

    return render_template(
        "index.html",
        featured_items=featured_items,
        categories=CATEGORIES,
        category_counts=category_counts,
        category_placeholders=CATEGORY_PLACEHOLDERS,
        stats=stats
    )


@app.route("/marketplace")
@app.route("/items")
def marketplace():
    """
    Central discovery & browse page with multi-faceted search, filtering, and sorting.
    """
    query_param = request.args.get("q", "").strip()
    category_param = request.args.get("category", "").strip()
    type_param = request.args.get("type", "").strip()
    condition_param = request.args.get("condition", "").strip()
    min_price_str = request.args.get("min_price", "").strip()
    max_price_str = request.args.get("max_price", "").strip()
    city_param = request.args.get("city", "").strip()
    sort_param = request.args.get("sort", "newest").strip()

    # Base SQL query
    where_clauses = ["i.status = 'Available'"]
    params = []

    # 1. Search Query (Title, Description, Brand, Model, Category)
    if query_param:
        where_clauses.append(
            "(i.title LIKE ? OR i.description LIKE ? OR i.brand LIKE ? OR i.model LIKE ? OR i.category LIKE ?)"
        )
        like_str = f"%{query_param}%"
        params.extend([like_str, like_str, like_str, like_str, like_str])

    # 2. Category Filter
    if category_param and category_param != "All":
        where_clauses.append("i.category = ?")
        params.append(category_param)

    # 3. Transaction Type Filter (Sell, Rent, Exchange)
    if type_param and type_param != "All":
        where_clauses.append("i.transaction_type = ?")
        params.append(type_param)

    # 4. Condition Filter
    if condition_param and condition_param != "All":
        where_clauses.append("i.condition = ?")
        params.append(condition_param)

    # 5. City Filter
    if city_param and city_param != "All":
        where_clauses.append("i.city LIKE ?")
        params.append(f"%{city_param}%")

    # 6. Min/Max Price Filter (Applied to price or rent_amount depending on type)
    if min_price_str:
        try:
            min_val = float(min_price_str)
            where_clauses.append("(i.price >= ? OR i.rent_amount >= ?)")
            params.extend([min_val, min_val])
        except ValueError:
            pass

    if max_price_str:
        try:
            max_val = float(max_price_str)
            where_clauses.append("( (i.transaction_type = 'Sell' AND i.price <= ?) OR (i.transaction_type = 'Rent' AND i.rent_amount <= ?) OR (i.transaction_type = 'Exchange') )")
            params.extend([max_val, max_val])
        except ValueError:
            pass

    # 7. Sorting Order
    order_by = "i.created_at DESC"
    if sort_param == "oldest":
        order_by = "i.created_at ASC"
    elif sort_param == "price_asc":
        order_by = "CASE WHEN i.transaction_type = 'Sell' THEN i.price WHEN i.transaction_type = 'Rent' THEN i.rent_amount ELSE 999999999 END ASC"
    elif sort_param == "price_desc":
        order_by = "CASE WHEN i.transaction_type = 'Sell' THEN i.price WHEN i.transaction_type = 'Rent' THEN i.rent_amount ELSE 0 END DESC"

    # Assemble and execute query
    where_sql = " AND ".join(where_clauses)
    sql = f"""
        SELECT i.*, u.name as seller_name, u.rating as seller_rating,
               CASE WHEN LOWER(u.email) IN ('admin@rent2swap.com', 'admin@rentitbro.com', 'test.student@college.edu', 'seller@rentitbro.com') THEN 1 ELSE 0 END AS is_sample,
               u.verification_status as seller_verification_status,
               (SELECT image_path FROM item_images WHERE item_id = i.item_id ORDER BY is_primary DESC, image_id ASC LIMIT 1) as primary_image
        FROM items i
        JOIN users u ON i.user_id = u.user_id
        WHERE {where_sql}
        ORDER BY {order_by}
    """
    items = execute_query_all(sql, params)
    _attach_listing_card_metadata(items)

    # Fetch available cities for location filter dropdown
    cities_raw = execute_query_all("SELECT DISTINCT city FROM items WHERE city != '' ORDER BY city ASC")
    cities = [r["city"] for r in cities_raw]

    # Category counts for filter chips
    cat_counts_raw = execute_query_all("SELECT category, COUNT(*) as c FROM items WHERE status = 'Available' GROUP BY category")
    category_counts = {r["category"]: r["c"] for r in cat_counts_raw}

    filters = {
        "q": query_param,
        "category": category_param,
        "type": type_param,
        "condition": condition_param,
        "min_price": min_price_str,
        "max_price": max_price_str,
        "city": city_param,
        "sort": sort_param
    }

    return render_template(
        "marketplace.html",
        items=items,
        total_count=len(items),
        categories=CATEGORIES,
        transaction_types=TRANSACTION_TYPES,
        conditions=CONDITIONS,
        cities=cities,
        category_counts=category_counts,
        filters=filters
    )


@app.route("/item/<int:item_id>")
def item_details(item_id):
    """
    Comprehensive details view for an individual marketplace listing.
    """
    item_query = """
        SELECT i.*, u.name as seller_name, u.location as seller_location,
               CASE WHEN LOWER(u.email) IN ('admin@rent2swap.com', 'admin@rentitbro.com', 'test.student@college.edu', 'seller@rentitbro.com') THEN 1 ELSE 0 END AS is_sample,
               u.college as seller_college, u.verification_status as seller_verification_status,
               u.rating as seller_rating, u.created_at as seller_joined
        FROM items i
        JOIN users u ON i.user_id = u.user_id
        WHERE i.item_id = ?
    """
    item = execute_query_one(item_query, (item_id,))

    if not item:
        flash("The requested listing could not be found or may have been removed.", "warning")
        return redirect(url_for("marketplace"))

    # Increment view counter
    execute_query_commit("UPDATE items SET views_count = views_count + 1 WHERE item_id = ?", (item_id,))

    # Fetch all associated images
    images = execute_query_all(
        "SELECT * FROM item_images WHERE item_id = ? ORDER BY is_primary DESC, image_id ASC",
        (item_id,)
    )

    primary_image = CATEGORY_PLACEHOLDERS.get(item["category"], "placeholders/default.svg")
    for img in images:
        if _is_resolvable_image(img.get("image_path")):
            primary_image = img["image_path"]
            break
    else:
        if images and images[0].get("image_path"):
            primary_image = images[0]["image_path"]
    item["is_sample"] = bool(item.get("is_sample")) or is_sample_email(item.get("seller_email"))

    # Fetch related listings in the same category
    related_query = """
        SELECT i.*, u.name as seller_name,
               CASE WHEN LOWER(u.email) IN ('admin@rent2swap.com', 'admin@rentitbro.com', 'test.student@college.edu', 'seller@rentitbro.com') THEN 1 ELSE 0 END AS is_sample,
               u.verification_status as seller_verification_status,
               (SELECT image_path FROM item_images WHERE item_id = i.item_id ORDER BY is_primary DESC, image_id ASC LIMIT 1) as primary_image
        FROM items i
        JOIN users u ON i.user_id = u.user_id
        WHERE i.category = ? AND i.item_id != ? AND i.status = 'Available'
        ORDER BY i.created_at DESC
        LIMIT 4
    """
    related_items = execute_query_all(related_query, (item["category"], item_id))
    _attach_listing_card_metadata(related_items)

    # Check if the viewer is the owner
    is_owner = (session.get("user_id") == item["user_id"])
    today_val = date.today().strftime('%Y-%m-%d')
    default_due_val = (date.today() + timedelta(days=7)).strftime('%Y-%m-%d')

    # Fetch available listings belonging to the visiting student for Swap trade proposals
    user_available_items = []
    if "user_id" in session and session["user_id"] != item["user_id"]:
        user_available_items = execute_query_all("""
            SELECT i.item_id, i.title, i.category, i.condition, i.price,
                   (SELECT image_path FROM item_images WHERE item_id = i.item_id ORDER BY is_primary DESC, image_id ASC LIMIT 1) as primary_image
            FROM items i
            WHERE i.user_id = ? AND i.status = 'Available' AND i.item_id != ?
            ORDER BY i.title ASC
        """, (session["user_id"], item_id))
        for it in user_available_items:
            if not it.get("primary_image"):
                it["primary_image"] = CATEGORY_PLACEHOLDERS.get(it["category"], "placeholders/default.svg")

    return render_template(
        "item_details.html",
        item=item,
        images=images,
        primary_image=primary_image,
        related_items=related_items,
        is_owner=is_owner,
        user_available_items=user_available_items,
        today=today_val,
        default_due=default_due_val
    )


# ==============================================================================
# LISTING CREATION & MANAGEMENT (AUTHENTICATED)
# ==============================================================================

_submission_locks = {}
_submission_locks_guard = threading.Lock()


def _get_submission_lock(user_id, idempotency_key: str):
    """Return a per-(user_id, idempotency_key) mutex to serialize accidental duplicate POST retries."""
    key = (int(user_id), idempotency_key)
    with _submission_locks_guard:
        if len(_submission_locks) > 512:
            # Prune unlocked stale entries safely
            stale = [k for k, lk in _submission_locks.items() if not lk.locked()]
            for k in stale[:256]:
                _submission_locks.pop(k, None)
        if key not in _submission_locks:
            _submission_locks[key] = threading.Lock()
        return _submission_locks[key]


def _create_listing_transaction(item_params: tuple, image_records: list, idempotency_key: str = None) -> tuple:
    """
    Atomically insert a listing row into `items` and its photo rows into `item_images`
    within a single database transaction. Returns (success: bool, item_id: int|None, error_msg: str|None).
    """
    norm_key = idempotency_key.strip()[:120] if idempotency_key and idempotency_key.strip() else None
    insert_item_sql = """
        INSERT INTO items (
            user_id, title, category, transaction_type, description, condition,
            brand, model, age, price, rent_amount, rent_period, exchange_preference,
            city, location, status, idempotency_key
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Available', ?)
    """
    insert_image_sql = "INSERT INTO item_images (item_id, image_path, is_primary) VALUES (?, ?, ?)"
    full_params = item_params + (norm_key,)

    pool = get_pg_pool()
    if pool:
        for attempt in range(2):
            conn = None
            try:
                conn = _get_healthy_pg_conn(pool)
                pg_item_sql = _prepare_query(insert_item_sql.strip() + " RETURNING item_id", is_pg=True)
                pg_img_sql = _prepare_query(insert_image_sql, is_pg=True)
                cursor = conn.cursor(cursor_factory=RealDictCursor)
                cursor.execute(pg_item_sql, full_params)
                returned = cursor.fetchone()
                new_item_id = (returned.get("item_id") or list(returned.values())[0]) if returned else None
                if not new_item_id:
                    raise RuntimeError("PostgreSQL did not return a listing ID after insertion.")

                for img_path, is_primary in image_records:
                    cursor.execute(pg_img_sql, (new_item_id, img_path, is_primary))

                conn.commit()
                cursor.close()
                _return_pg_conn(pool, conn)
                return True, new_item_id, None
            except Exception as e:
                is_conn_drop = isinstance(e, (psycopg2.OperationalError, psycopg2.InterfaceError)) if PSYCOPG2_AVAILABLE else False
                is_unique_dup = (
                    PSYCOPG2_AVAILABLE
                    and getattr(e, "pgcode", None) == "23505"
                    and norm_key is not None
                )
                print(f"[Create Listing PG Transaction Error] (attempt {attempt + 1}): {e}")
                if conn:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    _return_pg_conn(pool, conn, force_close=is_conn_drop)
                if is_unique_dup:
                    existing = execute_query_one(
                        "SELECT item_id FROM items WHERE user_id = ? AND idempotency_key = ? LIMIT 1",
                        (item_params[0], norm_key)
                    )
                    if existing and existing.get("item_id"):
                        return True, existing["item_id"], None
                if is_conn_drop and attempt == 0:
                    continue
                return False, None, "Database error while saving your listing. Please try again."

    # SQLite fallback (only when PostgreSQL pool is unavailable/disabled)
    conn = get_sqlite_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(insert_item_sql, full_params)
        new_item_id = cursor.lastrowid
        if not new_item_id:
            raise RuntimeError("SQLite did not return a listing ID after insertion.")
        for img_path, is_primary in image_records:
            cursor.execute(insert_image_sql, (new_item_id, img_path, is_primary))
        conn.commit()
        cursor.close()
        conn.close()
        return True, new_item_id, None
    except Exception as e:
        print(f"[Create Listing SQLite Transaction Error]: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        conn.close()
        return False, None, "Database error while saving your listing. Please try again."


@app.route("/api/upload-listing-photo", methods=["POST"])
@login_required
def upload_listing_photo_api():
    """
    Upload a single listing photo individually so multi-photo uploads stay small,
    support per-photo retry on flaky connections, and avoid large-payload proxy resets.
    """
    blocked = _require_verified_for_marketplace_action("create marketplace listings")
    if blocked is not None:
        return blocked

    file_obj = request.files.get("image") or request.files.get("file")
    if not file_obj or not (getattr(file_obj, "filename", "") or "").strip():
        return jsonify({"success": False, "message": "No photo file provided."}), 400

    valid_img, img_err = validate_listing_image_file(file_obj)
    if not valid_img:
        return jsonify({"success": False, "message": img_err or "Invalid image file."}), 400

    raw_filename = file_obj.filename.strip()
    ext = raw_filename.rsplit(".", 1)[-1].lower()
    try:
        file_obj.seek(0)
        file_bytes = file_obj.read()
        file_obj.seek(0)
    except Exception as read_err:
        return jsonify({"success": False, "message": f"Failed to read '{raw_filename}': {read_err}"}), 400

    prepared = [(raw_filename, ext, getattr(file_obj, "mimetype", "") or "", file_bytes)]
    up_ok, saved_urls, _, up_err = _upload_prepared_images_parallel(prepared, user_id=session.get("user_id"))
    if not up_ok or not saved_urls:
        return jsonify({
            "success": False,
            "message": up_err or f"Could not upload '{raw_filename}' to cloud storage. Please try again."
        }), 502

    return jsonify({"success": True, "url": saved_urls[0]}), 201


@app.route("/api/check-listing-submission")
@login_required
def check_listing_submission():
    """Allow the frontend to check whether a timed-out publish already succeeded server-side."""
    key = request.args.get("key", "").strip()[:120]
    user_id = session.get("user_id")
    if not key or not user_id:
        return jsonify({"found": False}), 200
    row = execute_query_one(
        "SELECT item_id FROM items WHERE user_id = ? AND idempotency_key = ? LIMIT 1",
        (user_id, key)
    )
    if row and row.get("item_id"):
        return jsonify({
            "found": True,
            "item_id": row["item_id"],
            "redirect_url": url_for("item_details", item_id=row["item_id"])
        }), 200
    return jsonify({"found": False}), 200


@app.route("/add-listing", methods=["GET", "POST"])
@login_required
def add_listing():
    """
    Create a new marketplace listing (Sell, Rent, or Exchange/Swap).
    Features dynamic client + strict server-side validation, concurrent Supabase Storage
    image persistence, idempotency deduplication, and atomic database insertion.
    """
    if request.method == "POST":
        blocked = _require_verified_for_marketplace_action("create marketplace listings")
        if blocked is not None:
            return blocked

        is_ajax = _wants_json_response()
        user_id = session.get("user_id")
        if not user_id:
            msg = "Please sign in again to publish your listing."
            if is_ajax:
                return jsonify({"success": False, "message": msg, "redirect": url_for("login")}), 401
            flash(msg, "warning")
            return redirect(url_for("login", next=request.url))

        # Verify user account still exists and is active in the database
        auth_user = execute_query_one("SELECT user_id, status FROM users WHERE user_id = ?", (user_id,))
        if not auth_user:
            session.clear()
            msg = "Your session is no longer valid. Please sign in again."
            if is_ajax:
                return jsonify({"success": False, "message": msg, "redirect": url_for("login")}), 401
            flash(msg, "warning")
            return redirect(url_for("login", next=request.url))
        if str(auth_user.get("status", "active")).lower() in ("suspended", "disabled", "banned"):
            msg = "Your account is currently suspended and cannot publish new listings."
            if is_ajax:
                return jsonify({"success": False, "message": msg}), 403
            flash(msg, "danger")
            return redirect(url_for("dashboard"))

        idempotency_key = request.form.get("idempotency_key", "").strip()[:120] or None
        title = request.form.get("title", "").strip()
        category = request.form.get("category", "").strip()
        transaction_type_raw = request.form.get("transaction_type", "").strip()

        # Normalize transaction_type: seamlessly handle Swap/Exchange, Rent, Sell
        if transaction_type_raw.lower() in ("swap", "exchange"):
            transaction_type = "Exchange"
        elif transaction_type_raw.lower() == "rent":
            transaction_type = "Rent"
        elif transaction_type_raw.lower() == "sell":
            transaction_type = "Sell"
        else:
            transaction_type = transaction_type_raw

        description = request.form.get("description", "").strip()
        condition = request.form.get("condition", "").strip()
        brand = request.form.get("brand", "").strip()
        model = request.form.get("model", "").strip()
        age = request.form.get("age", "").strip()
        city = request.form.get("city", "").strip()
        location = request.form.get("location", "").strip()

        # Preserve form data for re-rendering on validation error
        form_data = request.form.to_dict()
        form_data["transaction_type"] = transaction_type

        def _fail(msg, status_code=400):
            if is_ajax:
                return jsonify({"success": False, "message": msg}), status_code
            flash(msg, "danger")
            return render_template("add_listing.html", form_data=form_data, categories=CATEGORIES, conditions=CONDITIONS, rent_periods=RENT_PERIODS), status_code

        # 1. Base Validations
        if not title or len(title) < 3:
            return _fail("Item name must be at least 3 characters long.", 400)

        if category not in CATEGORIES:
            return _fail("Please select a valid item category.", 400)

        if transaction_type not in TRANSACTION_TYPES:
            return _fail("Please select a valid transaction type (Sell, Rent, or Swap).", 400)

        if not description or len(description) < 10:
            return _fail("Please provide a descriptive explanation of your item (at least 10 characters).", 400)

        if condition not in CONDITIONS:
            return _fail("Please select the current condition of the item.", 400)

        if not city or not location:
            return _fail("Please provide both city and campus handover location.", 400)

        # 2. Transaction Type-Specific Validations
        price = 0.0
        rent_amount = 0.0
        rent_period = "Day"
        exchange_preference = ""

        if transaction_type == "Sell":
            all_prices = request.form.getlist("price")
            effective_price_raw = "0"
            for p_val in reversed(all_prices):
                p_str = p_val.strip()
                if p_str:
                    try:
                        if float(p_str) > 0:
                            effective_price_raw = p_str
                            break
                    except ValueError:
                        pass
            if effective_price_raw == "0":
                fallback_p = request.form.get("price", "0").strip()
                if fallback_p and fallback_p != "0":
                    effective_price_raw = fallback_p

            try:
                price = float(effective_price_raw)
                if not math.isfinite(price) or price <= 0:
                    raise ValueError
            except ValueError:
                return _fail("Selling price must be a valid positive amount in ₹.", 400)

        elif transaction_type == "Rent":
            rent_amount_raw = request.form.get("rent_amount", "0").strip()
            rent_period = request.form.get("rent_period", "Day").strip()
            try:
                rent_amount = float(rent_amount_raw)
                if not math.isfinite(rent_amount) or rent_amount <= 0:
                    raise ValueError
            except ValueError:
                return _fail("Rental rate must be a valid positive amount in ₹.", 400)

            if rent_period not in RENT_PERIODS:
                rent_period = "Day"

        elif transaction_type == "Exchange":
            exchange_preference = request.form.get("exchange_preference", "").strip()
            if not exchange_preference or len(exchange_preference) < 3:
                return _fail("Please specify what item(s) you are seeking in exchange.", 400)

            est_raw = request.form.get("estimated_value", "").strip() or request.form.get("price", "0").strip()
            if est_raw and est_raw != "0":
                try:
                    price = float(est_raw)
                    if not math.isfinite(price) or price < 0:
                        price = 0.0
                except ValueError:
                    price = 0.0

        # 3. Validate Pre-uploaded Photo URLs (owned by current user) & Direct Multipart Photos
        allowed_supabase_prefix = f"https://{Config.SUPABASE_PROJECT_ID}.supabase.co/storage/v1/object/public/listings/"
        owner_prefix = f"u{int(user_id)}_"
        preuploaded_urls = []
        for raw_url in request.form.getlist("uploaded_image_urls"):
            u = (raw_url or "").strip()
            if not u:
                continue
            fname = os.path.basename(u.split("?", 1)[0])
            if Config.SUPABASE_PROJECT_ID and u.startswith(allowed_supabase_prefix) and fname.startswith(owner_prefix):
                if u not in preuploaded_urls:
                    preuploaded_urls.append(u)

        raw_files = request.files.getlist("images")
        submitted_files = [f for f in raw_files if f and (getattr(f, "filename", "") or "").strip()]
        max_images = getattr(Config, "MAX_LISTING_IMAGES", 8)
        if len(preuploaded_urls) + len(submitted_files) > max_images:
            return _fail(f"You can upload a maximum of {max_images} photos per listing.", 400)

        prepared_files = []
        for file_obj in submitted_files:
            valid_img, img_err = validate_listing_image_file(file_obj)
            if not valid_img:
                return _fail(img_err, 400)
            raw_filename = file_obj.filename.strip()
            ext = raw_filename.rsplit(".", 1)[-1].lower()
            try:
                file_obj.seek(0)
                file_bytes = file_obj.read()
                file_obj.seek(0)
            except Exception as read_err:
                return _fail(f"Failed to read '{raw_filename}': {read_err}", 400)
            prepared_files.append((raw_filename, ext, getattr(file_obj, "mimetype", "") or "", file_bytes))

        # 4. Idempotency-Guarded Upload + Atomic DB Insertion
        def _execute_publish():
            if idempotency_key:
                existing = execute_query_one(
                    "SELECT item_id FROM items WHERE user_id = ? AND idempotency_key = ? LIMIT 1",
                    (user_id, idempotency_key)
                )
                if existing and existing.get("item_id"):
                    ex_id = existing["item_id"]
                    flash("Your listing has been published successfully!", "success")
                    if is_ajax:
                        return jsonify({
                            "success": True,
                            "duplicate_prevented": True,
                            "message": "Your listing has been published successfully!",
                            "redirect_url": url_for("item_details", item_id=ex_id),
                            "item_id": ex_id,
                            "image_count": len(preuploaded_urls) + len(prepared_files)
                        }), 201
                    return redirect(url_for("item_details", item_id=ex_id))

            up_ok, saved_image_paths, created_local_caches, up_err = _upload_prepared_images_parallel(prepared_files, user_id=user_id)
            if not up_ok:
                return _fail(up_err or "Failed to upload one or more photos to cloud storage. Please try again.", 502)

            all_image_paths = preuploaded_urls + saved_image_paths
            if all_image_paths:
                image_records = [(img_path, 1 if idx == 0 else 0) for idx, img_path in enumerate(all_image_paths)]
            else:
                fallback_path = CATEGORY_PLACEHOLDERS.get(category, "placeholders/default.svg")
                image_records = [(fallback_path, 1)]

            item_params = (
                user_id, title, category, transaction_type, description, condition,
                brand, model, age, price, rent_amount, rent_period, exchange_preference,
                city, location
            )
            tx_ok, new_item_id, tx_err = _create_listing_transaction(item_params, image_records, idempotency_key=idempotency_key)
            if not tx_ok or not new_item_id:
                for p in created_local_caches:
                    try:
                        if p and os.path.exists(p):
                            os.remove(p)
                    except Exception:
                        pass
                return _fail(tx_err or "We couldn't publish your listing due to a database error. Please try again.", 500)

            flash("Your listing has been published successfully!", "success")
            if is_ajax:
                return jsonify({
                    "success": True,
                    "message": "Your listing has been published successfully!",
                    "redirect_url": url_for("item_details", item_id=new_item_id),
                    "item_id": new_item_id,
                    "image_count": len(all_image_paths)
                }), 201

            return redirect(url_for("item_details", item_id=new_item_id))

        if idempotency_key:
            with _get_submission_lock(user_id, idempotency_key):
                return _execute_publish()
        return _execute_publish()

    user_rec = execute_query_one(
        "SELECT user_id, verification_status, verification_rejection_reason FROM users WHERE user_id = ?",
        (session["user_id"],)
    )
    verif_status = normalize_verification_status(user_rec.get("verification_status") if user_rec else None)
    return render_template(
        "add_listing.html",
        form_data={},
        categories=CATEGORIES,
        conditions=CONDITIONS,
        rent_periods=RENT_PERIODS,
        verification_gate=(verif_status != "Verified"),
        verification_status=verif_status,
        verification_rejection_reason=(user_rec.get("verification_rejection_reason") if user_rec else "") or ""
    )


@app.route("/edit-listing/<int:item_id>", methods=["GET", "POST"])
@login_required
def edit_listing(item_id):
    """
    Modify an existing listing.
    Server-side verification strictly enforces that only the item's owner can edit.
    """
    item = execute_query_one("SELECT * FROM items WHERE item_id = ?", (item_id,))

    if not item:
        flash("Listing not found.", "warning")
        return redirect(url_for("my_listings"))

    # Enforce strict owner authorization
    if item["user_id"] != session["user_id"]:
        flash("Unauthorized action: You can only edit listings you created.", "danger")
        return redirect(url_for("my_listings"))

    existing_images = execute_query_all(
        "SELECT * FROM item_images WHERE item_id = ? ORDER BY is_primary DESC, image_id ASC",
        (item_id,)
    )

    if request.method == "POST":
        blocked = _require_verified_for_marketplace_action("edit marketplace listings")
        if blocked is not None:
            return blocked

        title = request.form.get("title", "").strip()
        category = request.form.get("category", "").strip()
        transaction_type_raw = request.form.get("transaction_type", "").strip()
        if transaction_type_raw.lower() in ("swap", "exchange"):
            transaction_type = "Exchange"
        elif transaction_type_raw.lower() == "rent":
            transaction_type = "Rent"
        elif transaction_type_raw.lower() == "sell":
            transaction_type = "Sell"
        else:
            transaction_type = transaction_type_raw

        description = request.form.get("description", "").strip()
        condition = request.form.get("condition", "").strip()
        brand = request.form.get("brand", "").strip()
        model = request.form.get("model", "").strip()
        age = request.form.get("age", "").strip()
        city = request.form.get("city", "").strip()
        location = request.form.get("location", "").strip()
        requested_status = request.form.get("status", "Available").strip()

        price_raw = request.form.get("price", "0").strip()
        rent_amount_raw = request.form.get("rent_amount", "0").strip()
        rent_period = request.form.get("rent_period", "Day").strip()
        exchange_preference = request.form.get("exchange_preference", "").strip()

        def _render_edit_err(err_msg):
            flash(err_msg, "danger")
            return render_template("edit_listing.html", item=item, images=existing_images, categories=CATEGORIES, conditions=CONDITIONS, rent_periods=RENT_PERIODS, statuses=LISTING_STATUSES), 400

        # Validations matching add_listing
        if not title or len(title) < 3 or category not in CATEGORIES or transaction_type not in TRANSACTION_TYPES:
            return _render_edit_err("Please verify required fields (Title, Category, Transaction Type).")
        if not description or len(description) < 10:
            return _render_edit_err("Description must be at least 10 characters long.")
        if condition not in CONDITIONS:
            return _render_edit_err("Please select a valid item condition.")
        if not city or not location:
            return _render_edit_err("City and campus handover location are required.")

        current_item_status = item.get("status") or "Available"
        if current_item_status in ("Rented", "Sold", "Exchanged"):
            status = current_item_status
        else:
            status = requested_status if requested_status in ("Available", "Inactive") else current_item_status

        price = 0.0
        rent_amount = 0.0

        if transaction_type == "Sell":
            all_prices = request.form.getlist("price")
            effective_price_raw = "0"
            for p_val in reversed(all_prices):
                p_str = p_val.strip()
                if p_str:
                    try:
                        if float(p_str) > 0:
                            effective_price_raw = p_str
                            break
                    except ValueError:
                        pass
            if effective_price_raw == "0" and price_raw and price_raw != "0":
                effective_price_raw = price_raw
            try:
                price = float(effective_price_raw)
                if not math.isfinite(price) or price <= 0:
                    raise ValueError
            except ValueError:
                return _render_edit_err("Selling price must be a valid positive amount in ₹.")
        elif transaction_type == "Rent":
            try:
                rent_amount = float(rent_amount_raw)
                if not math.isfinite(rent_amount) or rent_amount <= 0:
                    raise ValueError
            except ValueError:
                return _render_edit_err("Rental rate must be a valid positive amount in ₹.")
            if rent_period not in RENT_PERIODS:
                rent_period = "Day"
        elif transaction_type == "Exchange":
            if not exchange_preference or len(exchange_preference) < 3:
                return _render_edit_err("Please specify what item(s) you are seeking in exchange.")
            est_raw = request.form.get("estimated_value", "").strip() or price_raw
            if est_raw and est_raw != "0":
                try:
                    price = float(est_raw)
                    if not math.isfinite(price) or price < 0:
                        price = 0.0
                except ValueError:
                    price = 0.0

        # Validate image count & newly uploaded image binaries before mutating DB
        delete_image_ids = [s for s in request.form.getlist("delete_image_ids") if s and s.isdigit()]
        non_placeholder_existing = [
            im for im in existing_images
            if not str(im.get("image_path") or "").startswith("placeholders/")
            and str(im.get("image_id")) not in delete_image_ids
        ]
        uploaded_files = [f for f in request.files.getlist("images") if f and (getattr(f, "filename", "") or "").strip()]
        max_images = getattr(Config, "MAX_LISTING_IMAGES", 8)
        if len(non_placeholder_existing) + len(uploaded_files) > max_images:
            return _render_edit_err(f"Listings may have a maximum of {max_images} photos.")

        for file_obj in uploaded_files:
            valid_img, img_err = validate_listing_image_file(file_obj)
            if not valid_img:
                return _render_edit_err(img_err)

        update_sql = """
            UPDATE items
            SET title = ?, category = ?, transaction_type = ?, description = ?, condition = ?,
                brand = ?, model = ?, age = ?, price = ?, rent_amount = ?, rent_period = ?,
                exchange_preference = ?, city = ?, location = ?, status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE item_id = ?
        """
        success, _ = execute_query_commit(update_sql, (
            title, category, transaction_type, description, condition,
            brand, model, age, price, rent_amount, rent_period,
            exchange_preference, city, location, status, item_id
        ))

        # 1. Handle deletion of existing photos selected by user
        for img_id_str in delete_image_ids:
            img_id = int(img_id_str)
            img_rec = execute_query_one("SELECT * FROM item_images WHERE image_id = ? AND item_id = ?", (img_id, item_id))
            if img_rec:
                execute_query_commit("DELETE FROM item_images WHERE image_id = ?", (img_id,))
                if img_rec.get("image_path") and img_rec["image_path"].startswith("uploads/"):
                    fname = os.path.basename(img_rec["image_path"])
                    file_disk_path = os.path.join(Config.UPLOAD_FOLDER, fname)
                    try:
                        if os.path.exists(file_disk_path):
                            os.remove(file_disk_path)
                    except Exception as e:
                        print(f"[Delete Image Error] {type(e).__name__}")

        # 2. Handle newly uploaded additional photos
        newly_saved = []
        for file_obj in uploaded_files:
            saved_path = save_upload_file(file_obj)
            if saved_path:
                newly_saved.append(saved_path)

        if newly_saved:
            execute_query_commit("DELETE FROM item_images WHERE item_id = ? AND image_path LIKE 'placeholders/%'", (item_id,))
            for saved_path in newly_saved:
                execute_query_commit(
                    "INSERT INTO item_images (item_id, image_path, is_primary) VALUES (?, ?, 0)",
                    (item_id, saved_path)
                )

        # 3. Ensure listing always has a designated, resolvable primary image
        primary_check = execute_query_one(
            "SELECT image_id, image_path FROM item_images WHERE item_id = ? AND is_primary = 1",
            (item_id,)
        )
        if not primary_check or not _is_resolvable_image(primary_check.get("image_path")):
            all_imgs = execute_query_all(
                "SELECT image_id, image_path FROM item_images WHERE item_id = ? ORDER BY image_id ASC",
                (item_id,)
            )
            chosen_img = next((im for im in all_imgs if _is_resolvable_image(im.get("image_path"))), None)
            if not chosen_img and all_imgs:
                chosen_img = all_imgs[0]
            if chosen_img:
                execute_query_commit("UPDATE item_images SET is_primary = 0 WHERE item_id = ?", (item_id,))
                execute_query_commit("UPDATE item_images SET is_primary = 1 WHERE image_id = ?", (chosen_img["image_id"],))
            else:
                # If all photos were removed, assign category placeholder
                fallback_path = CATEGORY_PLACEHOLDERS.get(category, "placeholders/default.svg")
                execute_query_commit(
                    "INSERT INTO item_images (item_id, image_path, is_primary) VALUES (?, ?, 1)",
                    (item_id, fallback_path)
                )

        if success:
            flash("Listing updated successfully!", "success")
            return redirect(url_for("item_details", item_id=item_id))
        else:
            flash("Failed to update listing. Please try again.", "danger")

    return render_template(
        "edit_listing.html",
        item=item,
        images=existing_images,
        categories=CATEGORIES,
        conditions=CONDITIONS,
        rent_periods=RENT_PERIODS,
        statuses=LISTING_STATUSES
    )


@app.route("/delete-listing/<int:item_id>", methods=["POST"])
@login_required
def delete_listing(item_id):
    """
    Securely delete an item listing and its image database records and physical files.
    Requires POST method to prevent accidental or CSRF-style GET deletion.
    """
    item = execute_query_one("SELECT * FROM items WHERE item_id = ?", (item_id,))

    if not item:
        flash("Listing not found.", "warning")
        return redirect(url_for("my_listings"))

    # Enforce strict owner authorization
    if item["user_id"] != session["user_id"]:
        flash("Unauthorized action: You can only delete your own listings.", "danger")
        return redirect(url_for("my_listings"))

    # Delete physical files
    images = execute_query_all("SELECT image_path FROM item_images WHERE item_id = ?", (item_id,))
    for img in images:
        if img.get("image_path") and img["image_path"].startswith("uploads/"):
            disk_path = os.path.join(Config.BASE_DIR, "static", img["image_path"])
            try:
                if os.path.exists(disk_path):
                    os.remove(disk_path)
            except Exception:
                pass

    # Delete image records and item
    execute_query_commit("DELETE FROM item_images WHERE item_id = ?", (item_id,))
    execute_query_commit("DELETE FROM items WHERE item_id = ?", (item_id,))

    flash(f"Listing '{item['title']}' has been permanently deleted.", "info")
    return redirect(url_for("my_listings"))


@app.route("/toggle-listing-status/<int:item_id>", methods=["POST"])
@login_required
def toggle_listing_status(item_id):
    """
    Quick toggle between 'Available' and 'Inactive' for user listings.
    """
    item = execute_query_one("SELECT * FROM items WHERE item_id = ?", (item_id,))

    if not item or item["user_id"] != session["user_id"]:
        flash("Unauthorized action or item not found.", "danger")
        return redirect(url_for("my_listings"))

    if item.get("status") in ("Rented", "Sold", "Exchanged"):
        flash(f"Listing '{item['title']}' is currently '{item['status']}' from a transaction and cannot be toggled manually.", "warning")
        return redirect(request.referrer or url_for("my_listings"))

    new_status = "Inactive" if item["status"] == "Available" else "Available"
    execute_query_commit("UPDATE items SET status = ? WHERE item_id = ?", (new_status, item_id))

    flash(f"Status for '{item['title']}' updated to '{new_status}'.", "success")
    return redirect(request.referrer or url_for("my_listings"))


@app.route("/my-listings")
@login_required
def my_listings():
    """
    Dashboard for all listings created by the logged-in user.
    """
    user_id = session["user_id"]
    query = """
        SELECT i.*, 
               (SELECT image_path FROM item_images WHERE item_id = i.item_id ORDER BY is_primary DESC, image_id ASC LIMIT 1) as primary_image
        FROM items i
        WHERE i.user_id = ?
        ORDER BY i.created_at DESC
    """
    user_items = execute_query_all(query, (user_id,))

    for it in user_items:
        it["primary_image"] = _pick_valid_primary_image(it.get("item_id"), it.get("primary_image"), it["category"])

    active_count = sum(1 for it in user_items if it["status"] == "Available")
    inactive_count = len(user_items) - active_count

    stats = {
        "total": len(user_items),
        "active": active_count,
        "inactive": inactive_count
    }

    return render_template("my_listings.html", items=user_items, stats=stats)


# ==============================================================================
# AUTHENTICATION & USER MANAGEMENT (PHASE 1 PRESERVED & EXPANDED)
# ==============================================================================

@app.route("/register", methods=["GET", "POST"])
def register():
    """
    General commercial user registration.
    Preserves backward compatibility with Phase 1 database columns.
    """
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        location = request.form.get("location", "").strip()
        security_question = request.form.get("security_question", "").strip()
        security_answer = request.form.get("security_answer", "").strip()
        confirm_security_answer = request.form.get("confirm_security_answer", "").strip()

        # Optional affiliation / profession field (backwards-compatible with college/course)
        affiliation = (request.form.get("affiliation") or request.form.get("college") or "General User").strip() or "General User"

        form_data = {
            "name": name,
            "email": email,
            "location": location,
            "affiliation": affiliation,
            "security_question": security_question
        }

        # 1. Validation
        if not name or not email or not password or not confirm_password or not location:
            flash("All required fields (*) must be completed.", "danger")
            return render_template("register.html", form_data=form_data)

        email_regex = r"^[\w\.-]+@[\w\.-]+\.\w+$"
        if not re.match(email_regex, email):
            flash("Please provide a valid email address.", "danger")
            return render_template("register.html", form_data=form_data)

        if len(password) < 6:
            flash("Password must be at least 6 characters long.", "danger")
            return render_template("register.html", form_data=form_data)

        if password != confirm_password:
            flash("Password and Confirm Password do not match.", "danger")
            return render_template("register.html", form_data=form_data)

        # Security Question & Answer Validation (Compulsory for new registrations)
        if not security_question or security_question not in SECURITY_QUESTIONS:
            flash("Please select a valid security question from the provided list.", "danger")
            return render_template("register.html", form_data=form_data)

        if not security_answer or not confirm_security_answer:
            flash("Security question answer and confirmation are both required.", "danger")
            return render_template("register.html", form_data=form_data)

        norm_answer = normalize_security_answer(security_answer)
        norm_confirm = normalize_security_answer(confirm_security_answer)
        if norm_answer != norm_confirm:
            flash("Security answer and confirm answer do not match.", "danger")
            return render_template("register.html", form_data=form_data)

        # 2. Check for duplicate account
        existing = execute_query_one("SELECT user_id FROM users WHERE LOWER(email) = LOWER(?)", (email,))
        if existing:
            flash("An account with this email address already exists. Please sign in instead.", "warning")
            return render_template("register.html", form_data=form_data)

        # 3. Hash password and security answer securely
        hashed_password = generate_password_hash(password)
        hashed_security_answer = generate_password_hash(norm_answer)

        # 4. Insert user record with explicit 'Not Submitted' verification status
        insert_sql = """
            INSERT INTO users (
                name, email, password, college, course, year, location, rating, verification_status, status,
                security_question, security_answer_hash, security_question_updated_at
            )
            VALUES (?, ?, ?, ?, 'General', 'N/A', ?, 0.00, 'Not Submitted', 'active', ?, ?, CURRENT_TIMESTAMP)
        """
        success, new_user_id = execute_query_commit(
            insert_sql,
            (name, email, hashed_password, affiliation, location, security_question, hashed_security_answer)
        )

        if success:
            # Auto-login upon registration for seamless instant access
            if not new_user_id:
                user_record = execute_query_one("SELECT user_id FROM users WHERE LOWER(email) = LOWER(?)", (email,))
                new_user_id = user_record["user_id"] if user_record else None

            if new_user_id:
                session.clear()
                session["user_id"] = new_user_id
                session["user_name"] = name
                session["user_email"] = email
                session["is_admin"] = False
                session["is_verified"] = False
                session["session_version"] = 1
                flash(f"Welcome to Rent2Swap, {name}! Complete your Student Verification from the dashboard to unlock listing, renting, and swapping.", "success")
                return redirect(url_for("dashboard"))

            flash("Your account has been created successfully! Please sign in.", "success")
            return redirect(url_for("login"))
        else:
            flash("An error occurred during account creation. Please try again.", "danger")
            return render_template("register.html", form_data=form_data)

    return render_template("register.html", form_data=None)


@app.route("/login", methods=["GET", "POST"])
def login():
    """
    User login authentication with rate limiting, unified error messages,
    session version tracking, and safe redirect validation.
    """
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    next_page = request.args.get("next", "")
    if next_page and not _is_safe_redirect_url(next_page):
        next_page = ""

    if request.method == "POST":
        is_ajax = _wants_json_response()
        login_input = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        next_target = request.form.get("next", "")
        client_ip = _get_client_ip()

        if not login_input or not password:
            msg = "Please enter both email/username and password."
            if is_ajax:
                return jsonify({"success": False, "message": msg}), 400
            flash(msg, "danger")
            return render_template("login.html", email=login_input, next=next_target)

        ip_ok, ip_msg = check_auth_rate_limit("login_ip", client_ip)
        id_ok, id_msg = check_auth_rate_limit("login_id", login_input)
        if not ip_ok or not id_ok:
            msg = ip_msg if not ip_ok else id_msg
            if is_ajax:
                return jsonify({"success": False, "message": msg}), 429
            flash(msg, "danger")
            return render_template("login.html", email=login_input, next=next_target), 429

        user = execute_query_one(
            "SELECT * FROM users WHERE LOWER(email) = LOWER(?) OR LOWER(name) = LOWER(?)",
            (login_input, login_input)
        )

        if not user or not check_password_hash(user["password"], password):
            record_failed_auth_attempt("login_ip", client_ip, max_attempts=10, lock_seconds=600)
            record_failed_auth_attempt("login_id", login_input, max_attempts=5, lock_seconds=600)
            msg = "Invalid email/username or password."
            if is_ajax:
                return jsonify({"success": False, "message": msg}), 401
            flash(msg, "danger")
            return render_template("login.html", email=login_input, next=next_target), 401

        if _is_user_suspended(user):
            msg = "Your account has been suspended by an administrator. Contact campus support for assistance."
            if is_ajax:
                return jsonify({"success": False, "message": msg}), 403
            flash(msg, "danger")
            return render_template("login.html", email=login_input, next=next_target), 403

        clear_auth_rate_limit("login_ip", client_ip)
        clear_auth_rate_limit("login_id", login_input)

        session.clear()
        session["user_id"] = user["user_id"]
        session["user_name"] = user["name"]
        session["user_email"] = user["email"]
        is_adm = _is_user_admin(user)
        session["is_admin"] = is_adm
        session["is_verified"] = is_student_verified(user)
        session["session_version"] = int(user.get("session_version") or 1)

        flash(f"Welcome back, {user['name']}!", "success")
        if next_target and _is_safe_redirect_url(next_target):
            redirect_url = next_target.strip()
        elif is_adm:
            redirect_url = url_for("admin_dashboard")
        else:
            redirect_url = url_for("dashboard")

        if is_ajax:
            return jsonify({"success": True, "redirect_url": redirect_url}), 200
        return redirect(redirect_url)

    return render_template("login.html", email="", next=next_page)


# ==============================================================================
# SECURITY QUESTION-BASED PASSWORD RECOVERY WORKFLOW
# ==============================================================================

@app.route("/forgot-password", methods=["GET", "POST"])
@app.route("/reset-password", methods=["GET", "POST"])
def forgot_password():
    """
    Security Question & Answer-based password recovery workflow.
    Step 1: Enter Registered Email
    Step 2: Display Security Question & Verify Answer
    Step 3: Reset Password with Short-lived Single-use Authorization
    Supports both interactive client-side flow and standard full-page form POST.
    """
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    email_param = request.args.get("email", "").strip()

    # Handle standard form POST submissions for complete non-JS fallback
    if request.method == "POST":
        action = request.form.get("action", "identify")
        email = request.form.get("email", "").strip().lower()

        if action == "identify":
            if not email:
                flash("Please enter your registered email address.", "danger")
                return render_template("forgot_password.html", step=1, email=email)

            allowed, rate_msg = check_recovery_rate_limit(email)
            if not allowed:
                flash(rate_msg, "danger")
                return render_template("forgot_password.html", step=1, email=email, locked=True)

            user = execute_query_one(
                "SELECT user_id, email, is_admin, status, security_question FROM users WHERE LOWER(email) = LOWER(?)",
                (email,),
            )
            if not user or _is_user_admin(user) or not user.get("security_question"):
                question_text = _decoy_security_question(email)
                return render_template("forgot_password.html", step=2, email=email, question=question_text)

            return render_template("forgot_password.html", step=2, email=user["email"], question=user["security_question"])

        elif action == "verify_answer":
            answer = request.form.get("security_answer", "").strip()
            if not email or not answer:
                flash("Please provide your answer to the security question.", "danger")
                return render_template("forgot_password.html", step=2, email=email, question=_decoy_security_question(email))

            allowed, rate_msg = check_recovery_rate_limit(email)
            if not allowed:
                flash(rate_msg, "danger")
                return render_template("forgot_password.html", step=1, email=email, locked=True)

            user = execute_query_one(
                "SELECT user_id, email, is_admin, status, security_question, security_answer_hash FROM users WHERE LOWER(email) = LOWER(?)",
                (email,),
            )
            valid_user = bool(user and not _is_user_admin(user) and user.get("security_answer_hash"))
            norm_ans = normalize_security_answer(answer)
            if not valid_user or not check_password_hash(user["security_answer_hash"], norm_ans):
                rem = record_failed_recovery_attempt(email)
                q_display = user["security_question"] if (valid_user and user.get("security_question")) else _decoy_security_question(email)
                if rem == 0:
                    flash("Too many incorrect attempts. Account recovery locked for 10 minutes.", "danger")
                    return render_template("forgot_password.html", step=1, email=email, locked=True)
                flash(f"Incorrect security answer. {rem} attempt(s) remaining before temporary lockout.", "danger")
                return render_template("forgot_password.html", step=2, email=email, question=q_display)

            clear_recovery_rate_limit(email)
            token = create_recovery_token(user["user_id"], user["email"])
            session["recovery_token"] = token
            return render_template("forgot_password.html", step=3, email=user["email"], reset_token=token)

        elif action == "reset_password":
            token = request.form.get("reset_token", "").strip() or session.get("recovery_token", "")
            new_pwd = request.form.get("new_password", "")
            confirm_pwd = request.form.get("confirm_new_password", "")

            token_data = verify_and_consume_recovery_token(token)
            if not token_data:
                flash("Your recovery session has expired or is invalid. Please start the recovery process again.", "danger")
                session.pop("recovery_token", None)
                return redirect(url_for("forgot_password"))

            target_user = execute_query_one(
                "SELECT user_id, is_admin, status FROM users WHERE user_id = ?",
                (token_data["user_id"],),
            )
            if not target_user or _is_user_admin(target_user):
                session.pop("recovery_token", None)
                flash("Password recovery is not permitted for this account.", "danger")
                return redirect(url_for("forgot_password"))

            if not new_pwd or len(new_pwd) < 6:
                flash("Password must be at least 6 characters long.", "danger")
                new_token = create_recovery_token(token_data["user_id"], token_data["email"])
                session["recovery_token"] = new_token
                return render_template("forgot_password.html", step=3, email=token_data["email"], reset_token=new_token)

            if new_pwd != confirm_pwd:
                flash("Passwords do not match. Please re-enter.", "danger")
                new_token = create_recovery_token(token_data["user_id"], token_data["email"])
                session["recovery_token"] = new_token
                return render_template("forgot_password.html", step=3, email=token_data["email"], reset_token=new_token)

            hashed = generate_password_hash(new_pwd)
            success, _ = execute_query_commit(
                "UPDATE users SET password = ?, session_version = COALESCE(session_version, 1) + 1 WHERE user_id = ?",
                (hashed, token_data["user_id"])
            )

            if success:
                session.pop("recovery_token", None)
                flash("Your password has been reset successfully. You can now log in using your new password.", "success")
                return redirect(url_for("login"))
            else:
                flash("An error occurred while updating the password. Please try again.", "danger")
                return redirect(url_for("forgot_password"))

    return render_template("forgot_password.html", step=1, email=email_param)


@app.route("/api/forgot-password/identify", methods=["POST"])
def api_forgot_password_identify():
    """Return security question without leaking account existence or admin role."""
    data = request.get_json(silent=True) or request.form
    email = data.get("email", "").strip().lower()

    if not email:
        return jsonify({"success": False, "message": "Please enter your registered email address."}), 400

    allowed, rate_msg = check_recovery_rate_limit(email)
    if not allowed:
        return jsonify({"success": False, "locked": True, "message": rate_msg}), 429

    user = execute_query_one(
        "SELECT user_id, email, is_admin, status, security_question FROM users WHERE LOWER(email) = LOWER(?)",
        (email,),
    )
    if not user or _is_user_admin(user) or not user.get("security_question"):
        return jsonify({
            "success": True,
            "email": email,
            "question": _decoy_security_question(email),
        })

    return jsonify({
        "success": True,
        "email": user["email"],
        "question": user["security_question"]
    })


@app.route("/api/forgot-password/verify-answer", methods=["POST"])
def api_forgot_password_verify_answer():
    """Verify submitted security answer against secure hash with persistent DB rate limiting."""
    data = request.get_json(silent=True) or request.form
    email = data.get("email", "").strip().lower()
    answer = data.get("answer", "").strip()

    if not email or not answer:
        return jsonify({"success": False, "message": "Please provide your answer to the security question."}), 400

    allowed, rate_msg = check_recovery_rate_limit(email)
    if not allowed:
        return jsonify({"success": False, "locked": True, "message": rate_msg}), 429

    user = execute_query_one(
        "SELECT user_id, email, is_admin, status, security_question, security_answer_hash FROM users WHERE LOWER(email) = LOWER(?)",
        (email,),
    )
    valid_user = bool(user and not _is_user_admin(user) and user.get("security_answer_hash"))
    norm_ans = normalize_security_answer(answer)

    if not valid_user or not check_password_hash(user["security_answer_hash"], norm_ans):
        rem = record_failed_recovery_attempt(email)
        if rem == 0:
            return jsonify({
                "success": False,
                "locked": True,
                "message": "Too many incorrect attempts. Account recovery locked for 10 minutes."
            }), 429
        return jsonify({
            "success": False,
            "message": f"Incorrect security answer. {rem} attempt(s) remaining before temporary lockout."
        }), 400

    clear_recovery_rate_limit(email)
    token = create_recovery_token(user["user_id"], user["email"])
    session["recovery_token"] = token

    return jsonify({
        "success": True,
        "reset_token": token,
        "message": "Security answer verified successfully."
    })


@app.route("/api/forgot-password/reset", methods=["POST"])
def api_forgot_password_reset():
    """Reset password using verified single-use hashed token and invalidate active sessions."""
    data = request.get_json(silent=True) or request.form
    token = data.get("reset_token", "").strip() or session.get("recovery_token", "")
    new_password = data.get("new_password", "")
    confirm_password = data.get("confirm_new_password", "")

    if not new_password or len(new_password) < 6:
        return jsonify({"success": False, "message": "Password must be at least 6 characters long."}), 400

    if new_password != confirm_password:
        return jsonify({"success": False, "message": "Passwords do not match. Please re-enter."}), 400

    token_data = verify_and_consume_recovery_token(token)
    if not token_data:
        return jsonify({
            "success": False,
            "message": "Your recovery authorization has expired or is invalid. Please start the recovery process again."
        }), 400

    target_user = execute_query_one(
        "SELECT user_id, is_admin, status FROM users WHERE user_id = ?",
        (token_data["user_id"],),
    )
    if not target_user or _is_user_admin(target_user):
        session.pop("recovery_token", None)
        return jsonify({"success": False, "message": "Password recovery is not permitted for this account."}), 403

    hashed = generate_password_hash(new_password)
    success, _ = execute_query_commit(
        "UPDATE users SET password = ?, session_version = COALESCE(session_version, 1) + 1 WHERE user_id = ?",
        (hashed, token_data["user_id"])
    )

    if success:
        session.pop("recovery_token", None)
        flash("Your password has been reset successfully. You can now log in using your new password.", "success")
        return jsonify({
            "success": True,
            "message": "Your password has been reset successfully. You can now log in using your new password.",
            "redirect_url": url_for("login")
        })
    else:
        return jsonify({"success": False, "message": "Database error while updating password. Please try again."}), 500


@app.route("/profile/security-question", methods=["POST"])
@login_required
def update_security_question():
    """Update or configure security question and answer from User Profile."""
    user_id = session["user_id"]
    user = execute_query_one("SELECT * FROM users WHERE user_id = ?", (user_id,))
    if not user:
        session.clear()
        return redirect(url_for("login"))

    question = request.form.get("security_question", "").strip()
    answer = request.form.get("security_answer", "").strip()
    confirm_answer = request.form.get("confirm_security_answer", "").strip()
    current_password = request.form.get("current_password", "")

    if not current_password or not check_password_hash(user["password"], current_password):
        flash("Incorrect current password. Verification required to modify security settings.", "danger")
        return redirect(url_for("profile") + "#security-settings")

    if not question or question not in SECURITY_QUESTIONS:
        flash("Please select a valid security question from the provided list.", "danger")
        return redirect(url_for("profile") + "#security-settings")

    if not answer or not confirm_answer:
        flash("Security answer and confirmation cannot be empty.", "danger")
        return redirect(url_for("profile") + "#security-settings")

    norm_ans = normalize_security_answer(answer)
    norm_conf = normalize_security_answer(confirm_answer)
    if norm_ans != norm_conf:
        flash("Security answer and confirm answer do not match.", "danger")
        return redirect(url_for("profile") + "#security-settings")

    hashed_answer = generate_password_hash(norm_ans)
    success, _ = execute_query_commit("""
        UPDATE users 
        SET security_question = ?, security_answer_hash = ?, security_question_updated_at = CURRENT_TIMESTAMP
        WHERE user_id = ?
    """, (question, hashed_answer, user_id))

    if success:
        flash("Your security question and recovery answer have been updated successfully.", "success")
    else:
        flash("Failed to update security settings. Please try again.", "danger")

    return redirect(url_for("profile") + "#security-settings")


@app.route("/logout", methods=["POST"])
def logout():
    """
    Terminate authenticated session securely via CSRF-protected POST and redirect to login page.
    """
    session.clear()
    session.modified = True
    flash("Logged out successfully.", "success")
    return redirect(url_for("login"))


@app.after_request
def add_security_cache_headers(response):
    """
    Apply security headers, strict frame protections (when iframe embedding disabled),
    explicit CORS allowlist (never '*'), and no-store cache headers on dynamic pages/APIs.
    """
    if request.endpoint != "static" and response.mimetype in ("text/html", "application/json"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"

    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")

    if request.is_secure or Config.IS_PRODUCTION:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")

    trusted_csp = getattr(Config, "TRUSTED_FRAME_CSP", "")
    if trusted_csp:
        response.headers.setdefault("Content-Security-Policy", trusted_csp)
    elif not Config.ALLOW_IFRAME_EMBED:
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")

    origin = request.headers.get("Origin", "").strip()
    if origin and origin in Config.CORS_ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-CSRFToken, X-Requested-With"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"

    return response


@app.route("/dashboard")
@login_required
def dashboard():
    """
    Student Dashboard with live overview metrics, verification status,
    active rentals, incoming/outgoing requests, and quick listing management.
    """
    sync_overdue_rentals()
    user_id = session["user_id"]
    user = execute_query_one("SELECT * FROM users WHERE user_id = ?", (user_id,))

    if not user:
        session.clear()
        flash("User session expired. Please sign in again.", "warning")
        return redirect(url_for("login"))

    # 1. User's listings
    my_items = execute_query_all("""
        SELECT i.*, 
               (SELECT image_path FROM item_images WHERE item_id = i.item_id ORDER BY is_primary DESC, image_id ASC LIMIT 1) as primary_image
        FROM items i
        WHERE i.user_id = ?
        ORDER BY i.created_at DESC
        LIMIT 6
    """, (user_id,))

    for it in my_items:
        it["primary_image"] = _pick_valid_primary_image(it.get("item_id"), it.get("primary_image"), it["category"])

    # 2. Active Rentals & Borrowed Gear (strictly Rent transactions)
    active_rentals = execute_query_all("""
        SELECT r.*, i.title as item_title, i.category as item_category,
               u_owner.name as owner_name, u_renter.name as renter_name,
               (SELECT image_path FROM item_images WHERE item_id = r.item_id ORDER BY is_primary DESC, image_id ASC LIMIT 1) as primary_image
        FROM rental_requests r
        JOIN items i ON r.item_id = i.item_id
        JOIN users u_owner ON r.owner_id = u_owner.user_id
        JOIN users u_renter ON r.renter_id = u_renter.user_id
        WHERE (r.renter_id = ? OR r.owner_id = ?)
          AND r.request_type = 'Rent'
          AND r.status IN ('ACCEPTED', 'ACTIVE', 'OVERDUE', 'RETURN_REQUESTED')
        ORDER BY r.due_date ASC
        LIMIT 6
    """, (user_id, user_id))

    today = date.today()
    for rent in active_rentals:
        rent["primary_image"] = _pick_valid_primary_image(rent.get("item_id"), rent.get("primary_image"), rent.get("item_category", "Other"))

        if rent["status"] == "RETURN_REQUESTED":
            rent["display_status"] = "RETURN_REQUESTED"
            if rent.get("due_date"):
                try:
                    due = datetime.strptime(str(rent["due_date"])[:10], "%Y-%m-%d").date()
                    rent["days_remaining"] = (due - today).days
                except Exception:
                    rent["days_remaining"] = None
            else:
                rent["days_remaining"] = None
        elif rent.get("due_date"):
            try:
                due = datetime.strptime(str(rent["due_date"])[:10], "%Y-%m-%d").date()
                days_left = (due - today).days
                rent["days_remaining"] = days_left
                if days_left < 0 or rent["status"] == "OVERDUE":
                    rent["display_status"] = "OVERDUE"
                elif days_left <= 2:
                    rent["display_status"] = "DUE SOON"
                else:
                    rent["display_status"] = "ACTIVE"
            except Exception:
                rent["days_remaining"] = None
                rent["display_status"] = rent["status"]
        else:
            rent["days_remaining"] = None
            rent["display_status"] = rent["status"]

    # 2b. Confirmed Sales & Swaps (Buy/Sell and Exchange deals, strictly separated from Rentals)
    sale_swap_deals = execute_query_all("""
        SELECT r.*, i.title as item_title, i.category as item_category, i.status as item_status,
               i_off.title as offered_item_title,
               u_owner.name as owner_name, u_renter.name as renter_name,
               (SELECT image_path FROM item_images WHERE item_id = r.item_id ORDER BY is_primary DESC, image_id ASC LIMIT 1) as primary_image
        FROM rental_requests r
        JOIN items i ON r.item_id = i.item_id
        LEFT JOIN items i_off ON r.offered_item_id = i_off.item_id
        JOIN users u_owner ON r.owner_id = u_owner.user_id
        JOIN users u_renter ON r.renter_id = u_renter.user_id
        WHERE (r.renter_id = ? OR r.owner_id = ?)
          AND r.request_type IN ('Sell', 'Exchange')
          AND r.status IN ('ACCEPTED', 'ACTIVE', 'COMPLETED')
        ORDER BY r.updated_at DESC, r.created_at DESC
        LIMIT 6
    """, (user_id, user_id))
    for dl in sale_swap_deals:
        dl["primary_image"] = _pick_valid_primary_image(dl.get("item_id"), dl.get("primary_image"), dl.get("item_category", "Other"))

    # 3. Pending Requests (Rent, Sell, and Swap proposals awaiting response)
    pending_requests = execute_query_all("""
        SELECT r.*, i.title as item_title, i.category as item_category,
               u_renter.name as renter_name, u_owner.name as owner_name,
               (SELECT image_path FROM item_images WHERE item_id = r.item_id ORDER BY is_primary DESC, image_id ASC LIMIT 1) as primary_image
        FROM rental_requests r
        JOIN items i ON r.item_id = i.item_id
        JOIN users u_renter ON r.renter_id = u_renter.user_id
        JOIN users u_owner ON r.owner_id = u_owner.user_id
        WHERE (r.owner_id = ? OR r.renter_id = ?)
          AND r.status = 'PENDING'
        ORDER BY r.created_at DESC
        LIMIT 5
    """, (user_id, user_id))

    for req in pending_requests:
        req["primary_image"] = _pick_valid_primary_image(req.get("item_id"), req.get("primary_image"), req.get("item_category", "Other"))

    # Metrics
    total_my_listings = count_query("SELECT COUNT(*) as c FROM items WHERE user_id = ?", (user_id,))
    active_my_listings = count_query("SELECT COUNT(*) as c FROM items WHERE user_id = ? AND status = 'Available'", (user_id,))
    active_rentals_count = len(active_rentals)
    pending_requests_count = len(pending_requests)

    norm_verif = normalize_verification_status(user.get("verification_status"))
    user["verification_status"] = norm_verif
    metrics = {
        "my_total": total_my_listings,
        "my_active": active_my_listings,
        "active_rentals_count": active_rentals_count,
        "pending_requests_count": pending_requests_count,
        "rating": user.get("rating") or 5.0,
        "verification_status": norm_verif
    }

    return render_template(
        "dashboard.html",
        user=user,
        items=my_items,
        active_rentals=active_rentals,
        sale_swap_deals=sale_swap_deals,
        pending_requests=pending_requests,
        metrics=metrics
    )


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    """
    View and update user profile information.
    Preserves Verified status on ordinary profile edits (name, location, course, year, phone).
    Requires re-verification only if a Verified user alters critical identity details (college).
    """
    user_id = session["user_id"]
    current_user = execute_query_one("SELECT * FROM users WHERE user_id = ?", (user_id,)) or {}
    current_verif = normalize_verification_status(current_user.get("verification_status"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        location = request.form.get("location", "").strip()
        college_input = request.form.get("college", "").strip()
        course = request.form.get("course", "").strip()
        year = request.form.get("year", "").strip()
        phone = request.form.get("phone", "").strip()

        if not name or not location:
            flash("Name and location are required.", "danger")
        else:
            existing_college = (current_user.get("college") or "").strip()
            new_college = college_input or existing_college or "General Student"
            college_changed = (
                bool(college_input)
                and bool(existing_college)
                and college_input.strip().lower() != existing_college.strip().lower()
            )

            if current_verif == "Verified" and college_changed:
                now_ts = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
                success, _ = execute_query_commit("""
                    UPDATE users 
                    SET name = ?, location = ?, college = ?, course = ?, year = ?, phone = ?,
                        verification_status = 'Pending', verified_at = NULL,
                        verification_resubmitted_at = ?, verification_rejection_reason = ''
                    WHERE user_id = ?
                """, (name, location, new_college, course, year, phone, now_ts, user_id))
                if success:
                    session["user_name"] = name
                    session["is_verified"] = False
                    log_verification_history(
                        user_id=user_id,
                        previous_status="Verified",
                        new_status="Pending",
                        action_type="REVERIFICATION_REQUIRED",
                        submitted_at=current_user.get("verification_submitted_at"),
                        resubmitted_at=now_ts,
                        notes=f"College institution updated on profile ({existing_college} -> {new_college}); re-verification required."
                    )
                    flash("Your college institution changed, so your account has been moved to Pending for admin re-verification.", "warning")
                else:
                    flash("Unable to update profile. Please try again.", "danger")
            else:
                # Preserve existing verification status on ordinary profile edits
                effective_college = existing_college if (current_verif == "Verified" and not college_input) else new_college
                success, _ = execute_query_commit("""
                    UPDATE users 
                    SET name = ?, location = ?, college = ?, course = ?, year = ?, phone = ? 
                    WHERE user_id = ?
                """, (name, location, effective_college, course, year, phone, user_id))

                if success:
                    session["user_name"] = name
                    flash("Your profile information has been updated.", "success")
                else:
                    flash("Unable to update profile. Please try again.", "danger")

    user = execute_query_one("SELECT * FROM users WHERE user_id = ?", (user_id,))
    if user:
        user["verification_status"] = normalize_verification_status(user.get("verification_status"))
    return render_template("profile.html", user=user)


# ==============================================================================
# PHASE 2: STUDENT VERIFICATION & TRUST
# ==============================================================================

@app.route("/verification", methods=["GET", "POST"])
@app.route("/student-verification", methods=["GET", "POST"])
@login_required
def verification():
    """
    Student Verification flow: College Details, Enrollment No, ID Card Front & Back, Contact Check.
    Enforces persistent private Supabase Storage, single-record lifecycle, duplicate prevention,
    re-verification when critical identity fields change, and full verification_history logging.
    """
    user_id = session["user_id"]
    current_user_data = execute_query_one("SELECT * FROM users WHERE user_id = ?", (user_id,)) or {}
    current_status = normalize_verification_status(current_user_data.get("verification_status"))

    if request.method == "POST":
        college = request.form.get("college", "").strip()
        branch = request.form.get("branch", "").strip() or request.form.get("course", "").strip()
        year = request.form.get("year", "").strip()
        enrollment_no = request.form.get("enrollment_no", "").strip()
        phone = request.form.get("phone", "").strip()
        hostel_room = request.form.get("hostel_room", "").strip()

        # Dual ID Card Upload: Front & Back
        front_file = request.files.get("id_card_front") or request.files.get("college_id_front") or request.files.get("id_card")
        back_file = request.files.get("id_card_back") or request.files.get("college_id_back")

        existing_front = (current_user_data.get("id_card_front_image") or 
                          current_user_data.get("college_id_front_path") or 
                          current_user_data.get("id_card_image"))
        existing_back = (current_user_data.get("id_card_back_image") or 
                         current_user_data.get("college_id_back_path"))

        front_selected = bool(front_file and (front_file.filename or "").strip())
        back_selected = bool(back_file and (back_file.filename or "").strip())

        saved_front = None
        if front_selected:
            saved_front = save_upload_file(front_file, bucket="verifications")
            if not saved_front:
                flash("Could not upload College ID Front image to secure storage. Please upload a valid JPG, PNG, WEBP, or PDF file.", "danger")
                return redirect(url_for("verification"))

        saved_back = None
        if back_selected:
            saved_back = save_upload_file(back_file, bucket="verifications")
            if not saved_back:
                flash("Could not upload College ID Back image to secure storage. Please upload a valid JPG, PNG, WEBP, or PDF file.", "danger")
                return redirect(url_for("verification"))

        final_front = saved_front or existing_front
        final_back = saved_back or existing_back

        if not college or not branch or not enrollment_no:
            flash("College name, course/branch, and student roll / enrollment number are required for verification.", "warning")
        elif not final_front or not final_back:
            flash("Both Front and Back images of your College ID card are required for verification.", "warning")
        else:
            now_ts = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
            prev_college = (current_user_data.get("college") or "").strip()
            prev_branch = (current_user_data.get("course") or current_user_data.get("branch") or "").strip()
            prev_year = (current_user_data.get("year") or "").strip()
            prev_enroll = (current_user_data.get("enrollment_no") or "").strip()
            prev_phone = (current_user_data.get("phone") or "").strip()
            prev_hostel = (current_user_data.get("hostel_room") or "").strip()

            critical_changed = (
                front_selected
                or back_selected
                or college.lower() != prev_college.lower()
                or enrollment_no.lower() != prev_enroll.lower()
            )
            any_field_changed = (
                critical_changed
                or branch != prev_branch
                or year != prev_year
                or phone != prev_phone
                or hostel_room != prev_hostel
            )

            # Case 1: Already Verified student
            if current_status == "Verified":
                if not any_field_changed:
                    flash("Your account is already verified and no details were changed.", "info")
                    return redirect(url_for("verification"))
                if not critical_changed:
                    # Preserve Verified status on non-critical edits
                    execute_query_commit("""
                        UPDATE users
                        SET course = ?, branch = ?, year = ?, phone = ?, hostel_room = ?
                        WHERE user_id = ?
                    """, (branch, branch, year, phone, hostel_room, user_id))
                    log_verification_history(
                        user_id=user_id,
                        previous_status="Verified",
                        new_status="Verified",
                        action_type="DETAILS_UPDATED",
                        submitted_at=current_user_data.get("verification_submitted_at"),
                        reviewed_at=current_user_data.get("verification_reviewed_at") or current_user_data.get("verified_at"),
                        reviewed_by=current_user_data.get("verification_reviewed_by"),
                        notes="Non-critical student details updated while keeping Verified status."
                    )
                    flash("Your non-critical student details have been updated. Your Verified Student badge remains active.", "success")
                    return redirect(url_for("verification"))
                else:
                    # Critical identity details or ID documents updated -> require re-verification
                    execute_query_commit("""
                        UPDATE users 
                        SET college = ?, course = ?, branch = ?, year = ?, enrollment_no = ?, phone = ?, hostel_room = ?,
                            id_card_image = ?, id_card_front_image = ?, id_card_back_image = ?,
                            college_id_front_path = ?, college_id_back_path = ?,
                            verification_status = 'Pending', verified_at = NULL,
                            verification_resubmitted_at = ?, verification_rejection_reason = ''
                        WHERE user_id = ?
                    """, (
                        college, branch, branch, year, enrollment_no, phone, hostel_room,
                        final_front, final_front, final_back,
                        final_front, final_back,
                        now_ts, user_id
                    ))
                    session["is_verified"] = False
                    log_verification_history(
                        user_id=user_id,
                        previous_status="Verified",
                        new_status="Pending",
                        action_type="REVERIFICATION_REQUIRED",
                        submitted_at=current_user_data.get("verification_submitted_at"),
                        resubmitted_at=now_ts,
                        notes="Critical identity information or College ID document updated; queued for admin re-verification."
                    )
                    flash("Because critical identity details or ID documents were changed, your verification status is now Pending admin re-review.", "info")
                    return redirect(url_for("verification"))

            # Case 2: Already Pending and nothing changed -> prevent duplicate submission
            if current_status == "Pending" and not any_field_changed:
                flash("Your verification request is already under admin review.", "info")
                return redirect(url_for("verification"))

            # Case 3: Not Submitted, Rejected, or Pending update
            submitted_at_val = current_user_data.get("verification_submitted_at") or now_ts
            resubmitted_at_val = now_ts if current_status in ("Rejected", "Pending") else None
            action_type = "RESUBMITTED" if current_status == "Rejected" else ("DETAILS_UPDATED" if current_status == "Pending" else "SUBMITTED")
            note_msg = (
                "Student corrected details and resubmitted College ID (Front & Back)."
                if current_status == "Rejected"
                else ("Student updated pending verification details." if current_status == "Pending" else "Initial verification submission with College ID Front & Back.")
            )

            execute_query_commit("""
                UPDATE users 
                SET college = ?, course = ?, branch = ?, year = ?, enrollment_no = ?, phone = ?, hostel_room = ?,
                    id_card_image = ?, id_card_front_image = ?, id_card_back_image = ?,
                    college_id_front_path = ?, college_id_back_path = ?,
                    verification_status = 'Pending', verified_at = NULL,
                    verification_submitted_at = COALESCE(verification_submitted_at, ?),
                    verification_resubmitted_at = ?,
                    verification_rejection_reason = ''
                WHERE user_id = ?
            """, (
                college, branch, branch, year, enrollment_no, phone, hostel_room,
                final_front, final_front, final_back,
                final_front, final_back,
                submitted_at_val, resubmitted_at_val,
                user_id
            ))
            session["is_verified"] = False
            log_verification_history(
                user_id=user_id,
                previous_status=current_status,
                new_status="Pending",
                action_type=action_type,
                submitted_at=submitted_at_val,
                resubmitted_at=resubmitted_at_val,
                notes=note_msg
            )

            if current_status == "Rejected":
                flash("Updated verification submitted! Your request is now Pending admin review.", "info")
            elif current_status == "Pending":
                flash("Your verification details have been updated and remain Pending admin review.", "info")
            else:
                flash("Verification submitted! Your request is under review by campus administrators.", "info")
            return redirect(url_for("verification"))

    user = execute_query_one("SELECT * FROM users WHERE user_id = ?", (user_id,))
    if user:
        user["verification_status"] = normalize_verification_status(user.get("verification_status"))
    history = execute_query_all("""
        SELECT h.*, adm.name as reviewer_name
        FROM verification_history h
        LEFT JOIN users adm ON h.reviewed_by = adm.user_id
        WHERE h.user_id = ?
        ORDER BY h.history_id DESC
        LIMIT 20
    """, (user_id,))
    return render_template("verification.html", user=user, history=history)


# ==============================================================================
# PHASE 2: RENTAL TRACKER, AUDIT TRAIL & LIFECYCLE
# ==============================================================================

def log_rental_status_transition(request_id, previous_status, new_status, changed_by=None, action_role=None, notes=None):
    """
    Log a rental lifecycle state transition into the rental_status_log table.
    Ensures complete end-to-end traceability of all changes.
    """
    try:
        execute_query_commit("""
            INSERT INTO rental_status_log (request_id, previous_status, new_status, changed_by, action_role, notes)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (request_id, previous_status, new_status, changed_by, action_role, notes))
    except Exception as e:
        print(f"[RentalStatusLog Error] Failed to log status transition for request #{request_id}: {e}")


def sync_overdue_rentals():
    """
    Detect active/accepted RENT transactions where due_date is in the past (< today)
    and update database status to OVERDUE with a recorded audit log entry.
    Strictly ignores Sell and Exchange transactions.
    """
    try:
        today_str = date.today().strftime('%Y-%m-%d')
        overdue_records = execute_query_all("""
            SELECT request_id, status, due_date FROM rental_requests
            WHERE request_type = 'Rent'
              AND due_date IS NOT NULL 
              AND due_date < ?
              AND status IN ('ACTIVE', 'ACCEPTED')
        """, (today_str,))
        
        for r in overdue_records:
            req_id = r["request_id"]
            prev_status = r["status"]
            execute_query_commit("UPDATE rental_requests SET status = 'OVERDUE', updated_at = CURRENT_TIMESTAMP WHERE request_id = ?", (req_id,))
            log_rental_status_transition(
                request_id=req_id,
                previous_status=prev_status,
                new_status='OVERDUE',
                changed_by=None,
                action_role='system',
                notes=f"System automatically flagged rental as OVERDUE past due date ({r['due_date']})"
            )
    except Exception as e:
        print(f"[Sync Overdue Error] {e}")


@app.route("/item/<int:item_id>/request", methods=["POST"])
@login_required
def submit_rental_request(item_id):
    """
    Submit a rental, swap/exchange, or purchase proposal based strictly on the listing's
    database transaction_type (Rent, Sell, or Exchange).
    Requires Verified student status; validates dates ONLY for Rent listings.
    """
    blocked = _require_verified_for_marketplace_action("send rental, purchase, or swap requests")
    if blocked is not None:
        return blocked

    renter_id = session["user_id"]
    item = execute_query_one("""
        SELECT i.*, u.name as seller_name
        FROM items i
        JOIN users u ON i.user_id = u.user_id
        WHERE i.item_id = ?
    """, (item_id,))

    if not item:
        flash("The requested item does not exist or has been removed.", "danger")
        return redirect(url_for("marketplace"))

    if item["user_id"] == renter_id:
        flash("You cannot submit a request on your own listing.", "warning")
        return redirect(url_for("item_details", item_id=item_id))

    if item.get("status") != "Available":
        flash(f"This item is currently {item.get('status', 'unavailable').lower()} and not accepting new requests.", "warning")
        return redirect(url_for("item_details", item_id=item_id))

    # Duplicate active/pending request check: prevent spamming multiple requests
    existing_req = execute_query_one("""
        SELECT request_id, status FROM rental_requests
        WHERE item_id = ? AND renter_id = ? AND status IN ('PENDING', 'ACCEPTED', 'ACTIVE', 'RETURN_REQUESTED')
    """, (item_id, renter_id))
    if existing_req:
        flash(f"You already have an active or pending request (#{existing_req['request_id']} - {existing_req['status']}) for this item.", "warning")
        return redirect(url_for("my_requests"))

    # Authoritative transaction type from database listing record (prevents client manipulation)
    request_type = str(item.get("transaction_type") or "Rent").strip()
    if request_type not in ("Rent", "Sell", "Exchange"):
        flash("Unsupported listing transaction type.", "danger")
        return redirect(url_for("item_details", item_id=item_id))

    submitted_type = request.form.get("request_type", "").strip()
    if submitted_type and submitted_type != request_type:
        flash("Transaction type mismatch detected. Please submit directly from the listing page.", "danger")
        return redirect(url_for("item_details", item_id=item_id))

    notes = request.form.get("notes", "").strip()
    agreement_checked = request.form.get("agreement")

    if not agreement_checked:
        flash("You must review and accept the Campus Handover Agreement to proceed.", "danger")
        return redirect(url_for("item_details", item_id=item_id))

    start_date_str_val = None
    due_date_str_val = None
    total_price = 0.0
    offered_item_id = None
    today = date.today()

    if request_type == "Rent":
        start_date_str = request.form.get("start_date", "").strip()
        due_date_str = request.form.get("due_date", "").strip()

        if start_date_str:
            try:
                start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            except ValueError:
                start_date = today
        else:
            start_date = today

        if due_date_str:
            try:
                due_date = datetime.strptime(due_date_str, "%Y-%m-%d").date()
            except ValueError:
                due_date = start_date + timedelta(days=7)
        else:
            due_date = start_date + timedelta(days=7)

        if start_date < today:
            flash("Rental start date cannot be in the past. Please select today or a future date.", "warning")
            return redirect(url_for("item_details", item_id=item_id))
        if due_date < start_date:
            flash("Return due date cannot be earlier than the rental start date.", "warning")
            return redirect(url_for("item_details", item_id=item_id))

        rent_rate = item.get("rent_amount") or 0.0
        days = max(1, (due_date - start_date).days)
        total_price = days * rent_rate
        start_date_str_val = start_date.strftime('%Y-%m-%d')
        due_date_str_val = due_date.strftime('%Y-%m-%d')

    elif request_type == "Exchange":
        raw_offered_id = request.form.get("offered_item_id")
        if not raw_offered_id:
            flash("Please select an item from your catalog to offer in exchange.", "warning")
            return redirect(url_for("item_details", item_id=item_id))
        try:
            offered_item_id = int(raw_offered_id)
        except ValueError:
            flash("Invalid offered item selected.", "danger")
            return redirect(url_for("item_details", item_id=item_id))

        offered_item = execute_query_one("SELECT * FROM items WHERE item_id = ? AND user_id = ?", (offered_item_id, renter_id))
        if not offered_item:
            flash("The offered item was not found in your catalog.", "danger")
            return redirect(url_for("item_details", item_id=item_id))
        if offered_item.get("status") != "Available":
            flash(f"Your item '{offered_item['title']}' is currently {offered_item.get('status', 'unavailable').lower()} and cannot be offered.", "warning")
            return redirect(url_for("item_details", item_id=item_id))
        if offered_item_id == item_id:
            flash("You cannot offer the same item you are requesting.", "danger")
            return redirect(url_for("item_details", item_id=item_id))

    elif request_type == "Sell":
        # Sale requests strictly have NO rental start_date or due_date
        total_price = item.get("price") or 0.0

    now_ts = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')

    success, new_req_id = execute_query_commit("""
        INSERT INTO rental_requests (
            item_id, renter_id, owner_id, request_type,
            start_date, due_date, total_price, security_deposit,
            status, agreement_accepted, agreement_accepted_at, notes,
            offered_item_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 0.0, 'PENDING', ?, ?, ?, ?)
    """, (item_id, renter_id, item["user_id"], request_type, start_date_str_val, due_date_str_val, total_price, True, now_ts, notes, offered_item_id))

    if success:
        if request_type == "Rent":
            init_note = f"Initial Rent request submitted ({start_date_str_val} to {due_date_str_val})"
            flash_msg = f"Your rental request for '{item['title']}' has been sent to {item.get('seller_name', 'the owner')}!"
        elif request_type == "Exchange":
            init_note = "Initial Swap exchange proposal submitted"
            flash_msg = f"Your swap exchange proposal for '{item['title']}' has been sent to {item.get('seller_name', 'the owner')}!"
        else:
            init_note = f"Initial purchase request submitted (Price: ₹{total_price:,.0f})"
            flash_msg = f"Your purchase request for '{item['title']}' has been sent to {item.get('seller_name', 'the seller')}!"

        log_rental_status_transition(
            request_id=new_req_id,
            previous_status=None,
            new_status='PENDING',
            changed_by=renter_id,
            action_role='buyer' if request_type == 'Sell' else 'renter',
            notes=init_note
        )
        flash(flash_msg, "success")
        return redirect(url_for("my_requests"))
    else:
        flash("Unable to submit proposal at this time. Please try again.", "danger")
        return redirect(url_for("item_details", item_id=item_id))


@app.route("/my-requests")
@login_required
def my_requests():
    """
    Central hub for managing Sent Requests, Received Offers, Active Rentals, Sales, and Swaps.
    Enforces distinct display status calculation per transaction type (Rent vs Sell vs Exchange).
    """
    sync_overdue_rentals()
    user_id = session["user_id"]
    active_tab = request.args.get("tab", "all").strip().lower()

    all_requests = execute_query_all("""
        SELECT r.*, i.title as item_title, i.category as item_category, i.price as item_price,
               i.rent_amount, i.rent_period, i.transaction_type as listing_transaction_type,
               i.status as item_status,
               u_owner.name as owner_name, u_owner.college as owner_college,
               u_renter.name as renter_name, u_renter.college as renter_college,
               (SELECT image_path FROM item_images WHERE item_id = r.item_id ORDER BY is_primary DESC, image_id ASC LIMIT 1) as primary_image,
               i_off.title as offered_item_title, i_off.category as offered_item_category,
               (SELECT image_path FROM item_images WHERE item_id = r.offered_item_id ORDER BY is_primary DESC, image_id ASC LIMIT 1) as offered_primary_image
        FROM rental_requests r
        JOIN items i ON r.item_id = i.item_id
        LEFT JOIN items i_off ON r.offered_item_id = i_off.item_id
        JOIN users u_owner ON r.owner_id = u_owner.user_id
        JOIN users u_renter ON r.renter_id = u_renter.user_id
        WHERE r.renter_id = ? OR r.owner_id = ?
        ORDER BY r.created_at DESC
    """, (user_id, user_id))

    # Fetch status logs for user's requests to populate acceptance/completion timestamps
    req_ids = [r["request_id"] for r in all_requests]
    accept_dates = {}
    complete_dates = {}
    if req_ids:
        ph = ",".join(["?"] * len(req_ids))
        log_rows = execute_query_all(
            f"SELECT request_id, new_status, created_at FROM rental_status_log WHERE request_id IN ({ph}) ORDER BY log_id ASC",
            tuple(req_ids)
        )
        for lr in log_rows:
            if lr["new_status"] in ("ACCEPTED", "ACTIVE") and lr["request_id"] not in accept_dates:
                accept_dates[lr["request_id"]] = lr.get("created_at")
            if lr["new_status"] == "COMPLETED":
                complete_dates[lr["request_id"]] = lr.get("created_at")

    today = date.today()
    for r in all_requests:
        r["primary_image"] = _pick_valid_primary_image(r.get("item_id"), r.get("primary_image"), r.get("item_category", "Other"))
        if r.get("offered_item_id"):
            r["offered_primary_image"] = _pick_valid_primary_image(r.get("offered_item_id"), r.get("offered_primary_image"), r.get("offered_item_category", "Other"))

        r["accepted_at"] = accept_dates.get(r["request_id"]) or (r.get("updated_at") if r["status"] in ("ACCEPTED", "ACTIVE", "COMPLETED") else None)
        r["completed_at"] = complete_dates.get(r["request_id"]) or (r.get("updated_at") if r["status"] == "COMPLETED" else None)

        tx_type = r.get("request_type") or r.get("listing_transaction_type") or "Rent"
        r["request_type"] = tx_type

        if tx_type == "Rent":
            if r["status"] == "RETURN_REQUESTED":
                r["display_status"] = "RETURN_REQUESTED"
                if r.get("due_date"):
                    try:
                        due = datetime.strptime(str(r["due_date"])[:10], "%Y-%m-%d").date()
                        r["days_remaining"] = (due - today).days
                    except Exception:
                        r["days_remaining"] = None
                else:
                    r["days_remaining"] = None
            elif r.get("due_date") and r["status"] in ("ACCEPTED", "ACTIVE", "OVERDUE"):
                try:
                    due = datetime.strptime(str(r["due_date"])[:10], "%Y-%m-%d").date()
                    diff = (due - today).days
                    r["days_remaining"] = diff
                    if diff < 0 or r["status"] == "OVERDUE":
                        r["display_status"] = "OVERDUE"
                    elif diff <= 2:
                        r["display_status"] = "DUE SOON"
                    else:
                        r["display_status"] = "ACTIVE"
                except Exception:
                    r["days_remaining"] = None
                    r["display_status"] = r["status"]
            else:
                r["days_remaining"] = None
                r["display_status"] = r["status"]
        else:
            # Sell and Exchange transactions NEVER have rental due dates or overdue/days_remaining
            r["days_remaining"] = None
            r["display_status"] = r["status"]

    sent_requests = [r for r in all_requests if r["renter_id"] == user_id]
    received_requests = [r for r in all_requests if r["owner_id"] == user_id]
    active_rentals = [r for r in all_requests if r["status"] in ("ACCEPTED", "ACTIVE", "OVERDUE", "RETURN_REQUESTED")]
    completed_rentals = [r for r in all_requests if r["status"] in ("COMPLETED", "RETURNED")]

    counts = {
        "all": len(all_requests),
        "sent": len(sent_requests),
        "received": len(received_requests),
        "active": len(active_rentals),
        "completed": len(completed_rentals),
    }

    return render_template(
        "requests.html",
        active_tab=active_tab,
        all_requests=all_requests,
        sent_requests=sent_requests,
        received_requests=received_requests,
        active_rentals=active_rentals,
        completed_rentals=completed_rentals,
        counts=counts
    )


def _execute_atomic_tx(pg_worker, sqlite_worker):
    """
    Execute a state-changing multi-statement database workflow atomically.
    Uses PostgreSQL transactions (with row locking inside pg_worker) or SQLite IMMEDIATE lock.
    """
    pool = get_pg_pool()
    if pool:
        for attempt in range(2):
            conn = None
            try:
                conn = _get_healthy_pg_conn(pool)
                cursor = conn.cursor(cursor_factory=RealDictCursor)
                result = pg_worker(cursor)
                conn.commit()
                cursor.close()
                _return_pg_conn(pool, conn)
                return result
            except Exception as e:
                is_conn_drop = isinstance(e, (psycopg2.OperationalError, psycopg2.InterfaceError)) if PSYCOPG2_AVAILABLE else False
                print(f"[Atomic PG Tx Error] Attempt {attempt + 1}: {type(e).__name__}")
                if conn:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    _return_pg_conn(pool, conn, force_close=is_conn_drop)
                if is_conn_drop and attempt == 0:
                    continue
                return False, "danger", "Database error while updating transaction. Please try again."

    conn = get_sqlite_connection()
    try:
        conn.isolation_level = "IMMEDIATE"
        cursor = conn.cursor()
        result = sqlite_worker(cursor)
        conn.commit()
        cursor.close()
        conn.close()
        return result
    except Exception as e:
        print(f"[Atomic SQLite Tx Error] {type(e).__name__}")
        try:
            conn.rollback()
        except Exception:
            pass
        conn.close()
        return False, "danger", "Database error while updating transaction. Please try again."


@app.route("/request/<int:request_id>/accept", methods=["POST"])
@login_required
def accept_request(request_id):
    """Owner atomically accepts an incoming Rent, Sell, or Exchange request with row locking."""
    blocked = _require_verified_for_marketplace_action("accept rental, purchase, or swap requests")
    if blocked is not None:
        return blocked

    user_id = session["user_id"]

    def _run_accept(cursor, is_pg: bool):
        ph = "%s" if is_pg else "?"
        lock_suffix = " FOR UPDATE" if is_pg else ""

        cursor.execute(f"SELECT * FROM rental_requests WHERE request_id = {ph}{lock_suffix}", (request_id,))
        row = cursor.fetchone()
        req = dict(row) if row else None
        if not req or req["owner_id"] != user_id:
            return False, "danger", "You are not authorized to accept this request."
        if req["status"] != "PENDING":
            return False, "warning", f"This request cannot be accepted because it is already {req['status'].lower()}."

        cursor.execute(f"SELECT * FROM items WHERE item_id = {ph}{lock_suffix}", (req["item_id"],))
        item_row = cursor.fetchone()
        item = dict(item_row) if item_row else None
        if not item or item.get("status") != "Available":
            return False, "warning", f"This item is currently {(item.get('status') if item else 'unavailable').lower()} and cannot accept new transactions."

        tx_type = item.get("transaction_type") or req.get("request_type") or "Rent"
        log_sql = (
            f"INSERT INTO rental_status_log (request_id, previous_status, new_status, changed_by, action_role, notes) "
            f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph})"
        )

        if tx_type == "Rent":
            cursor.execute(
                f"UPDATE rental_requests SET status = 'ACTIVE', request_type = 'Rent', updated_at = CURRENT_TIMESTAMP WHERE request_id = {ph}",
                (request_id,),
            )
            cursor.execute(log_sql, (request_id, "PENDING", "ACTIVE", user_id, "owner", "Owner accepted rental request"))
            cursor.execute(f"UPDATE items SET status = 'Rented', updated_at = CURRENT_TIMESTAMP WHERE item_id = {ph}", (req["item_id"],))
            cursor.execute(
                f"SELECT request_id FROM rental_requests WHERE item_id = {ph} AND request_id != {ph} AND status = 'PENDING'{lock_suffix}",
                (req["item_id"], request_id),
            )
            for comp in cursor.fetchall():
                comp_id = comp["request_id"]
                cursor.execute(
                    f"UPDATE rental_requests SET status = 'REJECTED', updated_at = CURRENT_TIMESTAMP WHERE request_id = {ph}",
                    (comp_id,),
                )
                cursor.execute(
                    log_sql,
                    (comp_id, "PENDING", "REJECTED", user_id, "system", "Automatically declined because item was rented to another student"),
                )
            return True, "success", "Rental request accepted! Coordinate with your peer for campus handover."

        if tx_type == "Exchange":
            if req.get("offered_item_id"):
                cursor.execute(
                    f"SELECT item_id, status FROM items WHERE item_id = {ph}{lock_suffix}",
                    (req["offered_item_id"],),
                )
                off_row = cursor.fetchone()
                off_item = dict(off_row) if off_row else None
                if not off_item or off_item.get("status") != "Available":
                    return False, "warning", "The offered swap item is no longer available."

            cursor.execute(
                f"UPDATE rental_requests SET status = 'ACCEPTED', request_type = 'Exchange', start_date = NULL, due_date = NULL, updated_at = CURRENT_TIMESTAMP WHERE request_id = {ph}",
                (request_id,),
            )
            cursor.execute(
                log_sql,
                (request_id, "PENDING", "ACCEPTED", user_id, "owner", "Owner accepted swap exchange proposal; awaiting campus handover confirmation"),
            )
            cursor.execute(f"UPDATE items SET status = 'Unavailable', updated_at = CURRENT_TIMESTAMP WHERE item_id = {ph}", (req["item_id"],))
            if req.get("offered_item_id"):
                cursor.execute(f"UPDATE items SET status = 'Unavailable', updated_at = CURRENT_TIMESTAMP WHERE item_id = {ph}", (req["offered_item_id"],))

            cursor.execute(
                f"SELECT request_id FROM rental_requests WHERE (item_id = {ph} OR (offered_item_id IS NOT NULL AND offered_item_id = {ph})) AND request_id != {ph} AND status = 'PENDING'{lock_suffix}",
                (req["item_id"], req.get("offered_item_id") or -1, request_id),
            )
            for comp in cursor.fetchall():
                comp_id = comp["request_id"]
                cursor.execute(
                    f"UPDATE rental_requests SET status = 'REJECTED', updated_at = CURRENT_TIMESTAMP WHERE request_id = {ph}",
                    (comp_id,),
                )
                cursor.execute(
                    log_sql,
                    (comp_id, "PENDING", "REJECTED", user_id, "system", "Automatically declined because item was committed to another swap proposal"),
                )
            return True, "success", "Swap exchange proposal accepted! Coordinate campus handover and confirm completion."

        if tx_type == "Sell":
            cursor.execute(
                f"UPDATE rental_requests SET status = 'ACCEPTED', request_type = 'Sell', start_date = NULL, due_date = NULL, updated_at = CURRENT_TIMESTAMP WHERE request_id = {ph}",
                (request_id,),
            )
            cursor.execute(
                log_sql,
                (request_id, "PENDING", "ACCEPTED", user_id, "seller", "Seller accepted purchase request; listing marked Sold pending campus handover"),
            )
            cursor.execute(f"UPDATE items SET status = 'Sold', updated_at = CURRENT_TIMESTAMP WHERE item_id = {ph}", (req["item_id"],))
            cursor.execute(
                f"SELECT request_id FROM rental_requests WHERE item_id = {ph} AND request_id != {ph} AND status = 'PENDING'{lock_suffix}",
                (req["item_id"], request_id),
            )
            for comp in cursor.fetchall():
                comp_id = comp["request_id"]
                cursor.execute(
                    f"UPDATE rental_requests SET status = 'REJECTED', updated_at = CURRENT_TIMESTAMP WHERE request_id = {ph}",
                    (comp_id,),
                )
                cursor.execute(
                    log_sql,
                    (comp_id, "PENDING", "REJECTED", user_id, "system", "Automatically declined because item was sold to another buyer"),
                )
            return True, "success", "Purchase request accepted! Listing is now marked Sold. Confirm sale handover once delivered on campus."

        return False, "danger", "Unsupported transaction type."

    _, category, message = _execute_atomic_tx(
        lambda cur: _run_accept(cur, True),
        lambda cur: _run_accept(cur, False),
    )
    flash(message, category)
    return redirect(url_for("my_requests"))


@app.route("/request/<int:request_id>/reject", methods=["POST"])
@login_required
def reject_request(request_id):
    """Owner rejects an incoming request."""
    user_id = session["user_id"]
    req = execute_query_one("SELECT * FROM rental_requests WHERE request_id = ?", (request_id,))

    if not req or req["owner_id"] != user_id:
        flash("You are not authorized to decline this request.", "danger")
        return redirect(url_for("my_requests"))

    if req["status"] != "PENDING":
        flash(f"Cannot decline request that is already {req['status'].lower()}.", "warning")
        return redirect(url_for("my_requests"))

    execute_query_commit("UPDATE rental_requests SET status = 'REJECTED', updated_at = CURRENT_TIMESTAMP WHERE request_id = ?", (request_id,))
    log_rental_status_transition(
        request_id=request_id,
        previous_status=req["status"],
        new_status='REJECTED',
        changed_by=user_id,
        action_role='owner',
        notes="Owner declined request"
    )

    flash("Request declined.", "info")
    return redirect(url_for("my_requests"))


@app.route("/request/<int:request_id>/cancel", methods=["POST"])
@login_required
def cancel_request(request_id):
    """Requester cancels their outgoing pending request."""
    user_id = session["user_id"]
    req = execute_query_one("SELECT * FROM rental_requests WHERE request_id = ?", (request_id,))

    if not req or req["renter_id"] != user_id:
        flash("You are not authorized to cancel this request.", "danger")
        return redirect(url_for("my_requests"))

    if req["status"] != "PENDING":
        flash(f"You can only cancel pending requests. This request is already {req['status'].lower()}.", "warning")
        return redirect(url_for("my_requests"))

    execute_query_commit("UPDATE rental_requests SET status = 'CANCELLED', updated_at = CURRENT_TIMESTAMP WHERE request_id = ?", (request_id,))
    log_rental_status_transition(
        request_id=request_id,
        previous_status=req["status"],
        new_status='CANCELLED',
        changed_by=user_id,
        action_role='requester',
        notes="Requester cancelled pending request"
    )

    flash("Your request has been cancelled.", "info")
    return redirect(url_for("my_requests"))


@app.route("/request/<int:request_id>/confirm-sale", methods=["POST"])
@login_required
def confirm_sale(request_id):
    """Atomically confirm campus handover and complete a Sale (Sell) transaction."""
    blocked = _require_verified_for_marketplace_action("confirm campus sale handovers")
    if blocked is not None:
        return blocked

    user_id = session["user_id"]

    def _run_confirm_sale(cursor, is_pg: bool):
        ph = "%s" if is_pg else "?"
        lock_suffix = " FOR UPDATE" if is_pg else ""
        bool_true = "TRUE" if is_pg else "1"

        cursor.execute(f"SELECT * FROM rental_requests WHERE request_id = {ph}{lock_suffix}", (request_id,))
        row = cursor.fetchone()
        req = dict(row) if row else None
        if not req or (req["owner_id"] != user_id and req["renter_id"] != user_id):
            return False, "danger", "You are not authorized to confirm this sale."
        if req.get("request_type") != "Sell":
            return False, "warning", "Sale handover confirmation is only valid for Sell listings."
        if req["status"] == "COMPLETED":
            return False, "info", "This sale has already been marked as completed."
        if req["status"] not in ("ACCEPTED", "ACTIVE"):
            return False, "warning", f"Cannot confirm sale handover while status is '{req['status']}'."

        cursor.execute(
            f"""
            UPDATE rental_requests
            SET status = 'COMPLETED',
                start_date = NULL,
                due_date = NULL,
                completion_confirmed_by_owner = {bool_true},
                completion_confirmed_by_renter = {bool_true},
                updated_at = CURRENT_TIMESTAMP
            WHERE request_id = {ph}
            """,
            (request_id,),
        )
        cursor.execute(
            f"INSERT INTO rental_status_log (request_id, previous_status, new_status, changed_by, action_role, notes) VALUES ({ph}, {ph}, 'COMPLETED', {ph}, {ph}, {ph})",
            (request_id, req["status"], user_id, "seller" if req["owner_id"] == user_id else "buyer", "Sale handover confirmed and completed on campus"),
        )
        cursor.execute(f"UPDATE items SET status = 'Sold', updated_at = CURRENT_TIMESTAMP WHERE item_id = {ph}", (req["item_id"],))
        return True, "success", "Sale handover completed! The item is permanently marked as Sold. Leave a review for your peer."

    ok, category, message = _execute_atomic_tx(
        lambda cur: _run_confirm_sale(cur, True),
        lambda cur: _run_confirm_sale(cur, False),
    )
    flash(message, category)
    if ok:
        return redirect(url_for("write_review", rental_id=request_id))
    return redirect(url_for("my_requests"))


@app.route("/request/<int:request_id>/confirm-exchange", methods=["POST"])
@login_required
def confirm_exchange(request_id):
    """Atomically confirm campus handover and complete the swap exchange."""
    blocked = _require_verified_for_marketplace_action("confirm swap exchanges")
    if blocked is not None:
        return blocked

    user_id = session["user_id"]

    def _run_confirm_exchange(cursor, is_pg: bool):
        ph = "%s" if is_pg else "?"
        lock_suffix = " FOR UPDATE" if is_pg else ""
        bool_true = "TRUE" if is_pg else "1"

        cursor.execute(f"SELECT * FROM rental_requests WHERE request_id = {ph}{lock_suffix}", (request_id,))
        row = cursor.fetchone()
        req = dict(row) if row else None
        if not req or (req["owner_id"] != user_id and req["renter_id"] != user_id):
            return False, "danger", "You are not authorized to confirm this exchange."
        if req.get("request_type") != "Exchange":
            return False, "warning", "Swap confirmation is only valid for Exchange listings."
        if req["status"] == "COMPLETED":
            return False, "info", "This swap exchange has already been completed."
        if req["status"] not in ("ACCEPTED", "ACTIVE"):
            return False, "warning", f"Cannot confirm exchange while status is '{req['status']}'."

        cursor.execute(
            f"""
            UPDATE rental_requests
            SET status = 'COMPLETED',
                start_date = NULL,
                due_date = NULL,
                completion_confirmed_by_owner = {bool_true},
                completion_confirmed_by_renter = {bool_true},
                updated_at = CURRENT_TIMESTAMP
            WHERE request_id = {ph}
            """,
            (request_id,),
        )
        cursor.execute(
            f"INSERT INTO rental_status_log (request_id, previous_status, new_status, changed_by, action_role, notes) VALUES ({ph}, {ph}, 'COMPLETED', {ph}, 'user', 'Swap exchange confirmed and completed on campus')",
            (request_id, req["status"], user_id),
        )
        cursor.execute(f"UPDATE items SET status = 'Exchanged', updated_at = CURRENT_TIMESTAMP WHERE item_id = {ph}", (req["item_id"],))
        if req.get("offered_item_id"):
            cursor.execute(f"UPDATE items SET status = 'Exchanged', updated_at = CURRENT_TIMESTAMP WHERE item_id = {ph}", (req["offered_item_id"],))
        return True, "success", "Swap exchange successfully completed! Both items have been marked as Exchanged."

    ok, category, message = _execute_atomic_tx(
        lambda cur: _run_confirm_exchange(cur, True),
        lambda cur: _run_confirm_exchange(cur, False),
    )
    flash(message, category)
    if ok:
        return redirect(url_for("write_review", rental_id=request_id))
    return redirect(url_for("my_requests"))


@app.route("/request/<int:request_id>/return", methods=["POST"])
@app.route("/rental/<int:request_id>/return", methods=["POST"])
@login_required
def return_rental_item(request_id):
    """
    Renter initiates return handover request (Rent transactions ONLY).
    Strictly rejects Sell and Exchange transactions.
    """
    user_id = session["user_id"]
    req = execute_query_one("SELECT * FROM rental_requests WHERE request_id = ?", (request_id,))

    if not req or (req["owner_id"] != user_id and req["renter_id"] != user_id):
        flash("You are not authorized to update this rental status.", "danger")
        return redirect(url_for("my_requests"))

    if req.get("request_type") == "Exchange":
        flash("Swap exchanges do not use rental returns. Use Confirm Swap Handover instead.", "warning")
        return redirect(url_for("my_requests"))

    if req.get("request_type") == "Sell":
        flash("Purchased items cannot be returned through the rental return workflow.", "warning")
        return redirect(url_for("my_requests"))

    if req["owner_id"] == user_id:
        return confirm_return_item(request_id)

    if req["status"] == "RETURN_REQUESTED":
        flash("You have already initiated return for this item. Awaiting owner inspection & confirmation.", "info")
        return redirect(url_for("my_requests"))

    if req["status"] not in ("ACCEPTED", "ACTIVE", "OVERDUE"):
        flash(f"Cannot request return for rental in status '{req['status']}'.", "warning")
        return redirect(url_for("my_requests"))

    execute_query_commit("UPDATE rental_requests SET status = 'RETURN_REQUESTED', updated_at = CURRENT_TIMESTAMP WHERE request_id = ?", (request_id,))
    log_rental_status_transition(
        request_id=request_id,
        previous_status=req["status"],
        new_status='RETURN_REQUESTED',
        changed_by=user_id,
        action_role='renter',
        notes="Renter initiated return handover on campus; awaiting owner confirmation"
    )

    flash("Item return initiated! Please hand over the item to the owner on campus. The owner will confirm receipt to complete the rental.", "info")
    return redirect(url_for("my_requests"))


@app.route("/request/<int:request_id>/confirm-return", methods=["POST"])
@login_required
def confirm_return_item(request_id):
    """
    Atomically confirm safe return of a rented item (Rent transactions ONLY).
    """
    user_id = session["user_id"]
    today_str = date.today().strftime('%Y-%m-%d')

    def _run_confirm_return(cursor, is_pg: bool):
        ph = "%s" if is_pg else "?"
        lock_suffix = " FOR UPDATE" if is_pg else ""

        cursor.execute(f"SELECT * FROM rental_requests WHERE request_id = {ph}{lock_suffix}", (request_id,))
        row = cursor.fetchone()
        req = dict(row) if row else None
        if not req or req["owner_id"] != user_id:
            return False, "danger", "Only the item owner can confirm safe return of this item."
        if req.get("request_type") != "Rent":
            return False, "warning", "Return confirmation applies only to Rent transactions."
        if req["status"] == "COMPLETED":
            return False, "info", "This rental has already been marked as completed."
        if req["status"] not in ("RETURN_REQUESTED", "ACCEPTED", "ACTIVE", "OVERDUE"):
            return False, "warning", f"Cannot confirm return for rental in status '{req['status']}'."

        cursor.execute(
            f"UPDATE rental_requests SET status = 'COMPLETED', returned_date = {ph}, updated_at = CURRENT_TIMESTAMP WHERE request_id = {ph}",
            (today_str, request_id),
        )
        cursor.execute(f"UPDATE items SET status = 'Available', updated_at = CURRENT_TIMESTAMP WHERE item_id = {ph}", (req["item_id"],))
        cursor.execute(
            f"INSERT INTO rental_status_log (request_id, previous_status, new_status, changed_by, action_role, notes) VALUES ({ph}, {ph}, 'COMPLETED', {ph}, 'owner', 'Owner confirmed safe receipt of returned item. Item restored to Available.')",
            (request_id, req["status"], user_id),
        )
        return True, "success", "Item return safely confirmed! Your item is now Available again on the campus catalog. Please leave a review for your peer."

    ok, category, message = _execute_atomic_tx(
        lambda cur: _run_confirm_return(cur, True),
        lambda cur: _run_confirm_return(cur, False),
    )
    flash(message, category)
    if ok:
        return redirect(url_for("write_review", rental_id=request_id))
    return redirect(url_for("my_requests"))


@app.route("/request/<int:request_id>/history")
@login_required
def get_request_history(request_id):
    """Retrieve full transaction tracker audit trail and state history."""
    user_id = session["user_id"]
    is_admin = session.get("is_admin", 0) == 1
    req = execute_query_one("SELECT * FROM rental_requests WHERE request_id = ?", (request_id,))
    if not req:
        return jsonify({"success": False, "error": "Transaction record not found"}), 404
    if req["renter_id"] != user_id and req["owner_id"] != user_id and not is_admin:
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    logs = execute_query_all("""
        SELECT l.*, u.name as user_name
        FROM rental_status_log l
        LEFT JOIN users u ON l.changed_by = u.user_id
        WHERE l.request_id = ?
        ORDER BY l.created_at ASC, l.log_id ASC
    """, (request_id,))
    return jsonify({
        "success": True,
        "request_id": request_id,
        "request_type": req.get("request_type", "Rent"),
        "status": req["status"],
        "history": logs
    })


@app.route("/request/<int:request_id>/report-not-returned", methods=["POST"])
@login_required
def report_not_returned(request_id):
    """Owner reports that a RENTED item was not returned past due date (Rent only)."""
    user_id = session["user_id"]
    req = execute_query_one("SELECT * FROM rental_requests WHERE request_id = ?", (request_id,))

    if not req or req["owner_id"] != user_id:
        flash("You are not authorized to report this rental.", "danger")
        return redirect(url_for("my_requests"))

    if req.get("request_type") != "Rent":
        flash("Overdue non-return reporting only applies to Rent transactions.", "warning")
        return redirect(url_for("my_requests"))

    execute_query_commit("UPDATE rental_requests SET status = 'OVERDUE', updated_at = CURRENT_TIMESTAMP WHERE request_id = ?", (request_id,))
    log_rental_status_transition(
        request_id=request_id,
        previous_status=req["status"],
        new_status='OVERDUE',
        changed_by=user_id,
        action_role='owner',
        notes="Owner reported item not returned past due date"
    )
    
    execute_query_commit("""
        INSERT INTO reports (reporter_id, target_type, target_id, reason, description, status)
        VALUES (?, 'Transaction', ?, 'Item not returned', 'Owner reported item overdue and not returned by peer.', 'Open')
    """, (user_id, request_id))

    flash("Item reported as not returned. Our campus mediation support has been notified.", "warning")
    return redirect(url_for("my_requests"))


# ==============================================================================
# PHASE 2: DAMAGE REPORTING
# ==============================================================================

@app.route("/request/<int:request_id>/damage-report", methods=["GET", "POST"])
@app.route("/damage-report/<int:request_id>", methods=["GET", "POST"])
@login_required
def damage_report(request_id):
    """
    Transparent Damage Reporting process:
    Allows user to provide evidence and severity assessment without arbitrary owner-fixed penalties.
    """
    user_id = session["user_id"]
    req = execute_query_one("""
        SELECT r.*, i.title as item_title, u_owner.name as owner_name, u_renter.name as renter_name
        FROM rental_requests r
        JOIN items i ON r.item_id = i.item_id
        JOIN users u_owner ON r.owner_id = u_owner.user_id
        JOIN users u_renter ON r.renter_id = u_renter.user_id
        WHERE r.request_id = ? AND (r.owner_id = ? OR r.renter_id = ?)
    """, (request_id, user_id, user_id))

    if not req:
        flash("Rental record not found or access denied.", "danger")
        return redirect(url_for("my_requests"))

    if request.method == "POST":
        if req.get("status") not in ("ACCEPTED", "ACTIVE", "OVERDUE", "RETURN_REQUESTED", "DISPUTED"):
            flash(f"Damage reports cannot be submitted for transactions in status '{req.get('status')}'.", "warning")
            return redirect(url_for("my_requests"))

        description = request.form.get("description", "").strip()
        severity = request.form.get("severity", "Moderate").strip()
        photo_file = request.files.get("damage_photo")

        if severity not in ("Minor", "Moderate", "Severe"):
            flash("Invalid severity level selected.", "danger")
        elif not description or len(description) < 10 or len(description) > 2000:
            flash("Please provide a clear description (between 10 and 2000 characters).", "warning")
        else:
            saved_photo_path = None
            if photo_file and (photo_file.filename or "").strip():
                ok_img, img_err = validate_listing_image_file(photo_file)
                if not ok_img:
                    flash(img_err or "Invalid damage evidence photo.", "danger")
                    existing_reports = execute_query_all("SELECT * FROM damage_reports WHERE rental_id = ?", (request_id,))
                    return render_template("damage_report.html", rental=req, existing_reports=existing_reports), 400
                saved_photo_path = save_upload_file(photo_file, bucket="damage_reports")
                if not saved_photo_path:
                    flash("Could not store evidence photo in private storage. Please try again later.", "danger")
                    existing_reports = execute_query_all("SELECT * FROM damage_reports WHERE rental_id = ?", (request_id,))
                    return render_template("damage_report.html", rental=req, existing_reports=existing_reports), 502

            execute_query_commit("""
                INSERT INTO damage_reports (rental_id, reporter_id, item_id, description, severity, photo_path, status)
                VALUES (?, ?, ?, ?, ?, ?, 'Reported')
            """, (request_id, user_id, req["item_id"], description, severity, saved_photo_path))

            execute_query_commit("UPDATE rental_requests SET status = 'DISPUTED', updated_at = CURRENT_TIMESTAMP WHERE request_id = ?", (request_id,))

            flash("Damage report submitted with evidence. Campus community mediators will review for fair resolution.", "info")
            return redirect(url_for("my_requests"))

    existing_reports = execute_query_all("SELECT * FROM damage_reports WHERE rental_id = ?", (request_id,))
    return render_template("damage_report.html", rental=req, existing_reports=existing_reports)


# ==============================================================================
# PHASE 2: REVIEWS, RATINGS & TRUST
# ==============================================================================

@app.route("/reviews")
@login_required
def reviews():
    """Display student trust profile, ratings overview, and reviews received/given."""
    user_id = session["user_id"]
    user = execute_query_one("SELECT * FROM users WHERE user_id = ?", (user_id,))

    # Reviews received by current student
    received_reviews = execute_query_all("""
        SELECT rev.*, u.name as reviewer_name, u.college as reviewer_college, i.title as item_title
        FROM reviews rev
        JOIN users u ON rev.reviewer_id = u.user_id
        LEFT JOIN items i ON rev.item_id = i.item_id
        WHERE rev.reviewed_user_id = ?
        ORDER BY rev.created_at DESC
    """, (user_id,))

    # Reviews written by current student
    given_reviews = execute_query_all("""
        SELECT rev.*, u.name as reviewed_name, u.college as reviewed_college, i.title as item_title
        FROM reviews rev
        JOIN users u ON rev.reviewed_user_id = u.user_id
        LEFT JOIN items i ON rev.item_id = i.item_id
        WHERE rev.reviewer_id = ?
        ORDER BY rev.created_at DESC
    """, (user_id,))

    # Completed rentals awaiting review from this user
    awaiting_reviews = execute_query_all("""
        SELECT r.*, i.title as item_title,
               CASE WHEN r.renter_id = ? THEN u_owner.name ELSE u_renter.name END as peer_name,
               CASE WHEN r.renter_id = ? THEN r.owner_id ELSE r.renter_id END as peer_id
        FROM rental_requests r
        JOIN items i ON r.item_id = i.item_id
        JOIN users u_owner ON r.owner_id = u_owner.user_id
        JOIN users u_renter ON r.renter_id = u_renter.user_id
        WHERE (r.renter_id = ? OR r.owner_id = ?)
          AND r.status = 'COMPLETED'
          AND r.request_id NOT IN (SELECT rental_id FROM reviews WHERE reviewer_id = ? AND rental_id IS NOT NULL)
        LIMIT 5
    """, (user_id, user_id, user_id, user_id, user_id))

    return render_template(
        "reviews.html",
        user=user,
        received_reviews=received_reviews,
        given_reviews=given_reviews,
        awaiting_reviews=awaiting_reviews
    )


@app.route("/review/<int:rental_id>", methods=["GET", "POST"])
@app.route("/write-review/<int:rental_id>", methods=["GET", "POST"])
@login_required
def write_review(rental_id):
    """Write a verified review after completing a rental transaction."""
    user_id = session["user_id"]
    req = execute_query_one("""
        SELECT r.*, i.title as item_title,
               u_owner.name as owner_name, u_renter.name as renter_name
        FROM rental_requests r
        JOIN items i ON r.item_id = i.item_id
        JOIN users u_owner ON r.owner_id = u_owner.user_id
        JOIN users u_renter ON r.renter_id = u_renter.user_id
        WHERE r.request_id = ? AND (r.owner_id = ? OR r.renter_id = ?)
    """, (rental_id, user_id, user_id))

    if not req:
        flash("Transaction not found or access denied.", "danger")
        return redirect(url_for("reviews"))

    if req.get("status") != "COMPLETED":
        flash("Reviews can only be submitted after the transaction is completed.", "warning")
        return redirect(url_for("my_requests"))

    existing_review = execute_query_one(
        "SELECT review_id FROM reviews WHERE rental_id = ? AND reviewer_id = ?",
        (rental_id, user_id),
    )
    if existing_review:
        flash("You have already submitted a review for this transaction.", "info")
        return redirect(url_for("reviews"))

    # Determine who is being reviewed using transaction-accurate role labels
    tx_type = req.get("request_type") or "Rent"
    if req["renter_id"] == user_id:
        target_user_id = req["owner_id"]
        target_name = req["owner_name"]
        role_label = "Seller" if tx_type == "Sell" else ("Swap Partner" if tx_type == "Exchange" else "Owner / Lender")
    else:
        target_user_id = req["renter_id"]
        target_name = req["renter_name"]
        role_label = "Buyer" if tx_type == "Sell" else ("Swap Partner" if tx_type == "Exchange" else "Borrower / Renter")

    if request.method == "POST":
        try:
            rating = int(request.form.get("rating", 5))
            rating = max(1, min(5, rating))
        except ValueError:
            rating = 5

        comment = request.form.get("comment", "").strip()[:1000]

        ok_rev, _ = execute_query_commit("""
            INSERT INTO reviews (rental_id, reviewer_id, reviewed_user_id, item_id, rating, comment)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (rental_id, user_id, target_user_id, req["item_id"], rating, comment))

        if not ok_rev:
            flash("Your review could not be saved or was already submitted.", "warning")
            return redirect(url_for("reviews"))

        avg_row = execute_query_one("SELECT AVG(rating) as avg_r FROM reviews WHERE reviewed_user_id = ?", (target_user_id,))
        if avg_row and avg_row["avg_r"] is not None:
            new_rating = round(float(avg_row["avg_r"]), 2)
            execute_query_commit("UPDATE users SET rating = ? WHERE user_id = ?", (new_rating, target_user_id))

        flash(f"Thank you! Your verified review for {target_name} has been published.", "success")
        return redirect(url_for("reviews"))

    return render_template(
        "write_review.html",
        rental=req,
        target_name=target_name,
        role_label=role_label
    )


# ==============================================================================
# PHASE 2: REPORTING (USER / LISTING / ISSUE)
# ==============================================================================

@app.route("/report", methods=["POST"])
@login_required
def submit_report():
    """Submit a validated safety, conduct, or listing dispute report."""
    reporter_id = session["user_id"]
    target_type = request.form.get("target_type", "Listing").strip()
    target_id_raw = request.form.get("target_id", "0").strip()
    reason = request.form.get("reason", "Suspicious listing").strip()[:100]
    description = request.form.get("description", "").strip()

    if target_type not in ("Listing", "User", "Transaction"):
        flash("Invalid report target type.", "danger")
        return redirect(request.referrer or url_for("marketplace"))

    try:
        target_id = int(target_id_raw)
    except ValueError:
        target_id = 0

    if target_id <= 0:
        flash("Invalid target identifier for report.", "danger")
        return redirect(request.referrer or url_for("marketplace"))

    if target_type == "Listing":
        exists_row = execute_query_one("SELECT item_id FROM items WHERE item_id = ?", (target_id,))
    elif target_type == "User":
        exists_row = execute_query_one("SELECT user_id FROM users WHERE user_id = ?", (target_id,))
    else:
        exists_row = execute_query_one("SELECT request_id FROM rental_requests WHERE request_id = ?", (target_id,))

    if not exists_row:
        flash("The reported entity could not be found.", "warning")
        return redirect(request.referrer or url_for("marketplace"))

    if not reason or not description or len(description) < 10 or len(description) > 2000:
        flash("Please provide clear details (10 to 2000 characters) for your report.", "warning")
        return redirect(request.referrer or url_for("marketplace"))

    execute_query_commit("""
        INSERT INTO reports (reporter_id, target_type, target_id, reason, description, status)
        VALUES (?, ?, ?, ?, ?, 'Open')
    """, (reporter_id, target_type, target_id, reason, description))

    flash("Thank you for helping keep the campus community safe. Your report has been submitted to moderators.", "success")
    return redirect(request.referrer or url_for("marketplace"))


# ==============================================================================
# PHASE 3: ADMIN CONTROL CENTER ROUTES
# ==============================================================================

def _pending_verifications_count() -> int:
    """Count non-admin student accounts awaiting verification review."""
    return count_query(
        "SELECT COUNT(*) as c FROM users WHERE (is_admin IS NULL OR is_admin = 0) AND verification_status = 'Pending'"
    )


@app.route("/admin")
@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    """
    Central administrative dashboard showing real live database metrics,
    pending verifications, active dispute cases, and recent audit logs.
    """
    sync_overdue_rentals()

    # 1. Real Database Statistics (consistently excluding admin rows from student metrics)
    total_students = count_query("SELECT COUNT(*) as c FROM users WHERE (is_admin IS NULL OR is_admin = 0)")
    verified_students = count_query(
        "SELECT COUNT(*) as c FROM users WHERE (is_admin IS NULL OR is_admin = 0) AND verification_status = 'Verified'"
    )
    pending_verifications = _pending_verifications_count()
    total_listings = count_query("SELECT COUNT(*) as c FROM items")
    active_listings = count_query("SELECT COUNT(*) as c FROM items WHERE status = 'Available'")
    total_rentals = count_query("SELECT COUNT(*) as c FROM rental_requests")
    active_rentals = count_query("SELECT COUNT(*) as c FROM rental_requests WHERE request_type = 'Rent' AND status IN ('ACCEPTED', 'ACTIVE', 'RETURN_REQUESTED')")
    pending_requests = count_query("SELECT COUNT(*) as c FROM rental_requests WHERE status = 'PENDING'")

    today_str = date.today().strftime('%Y-%m-%d')
    overdue_rentals = count_query("""
        SELECT COUNT(*) as c FROM rental_requests 
        WHERE request_type = 'Rent'
          AND (status = 'OVERDUE' OR (status IN ('ACCEPTED', 'ACTIVE') AND due_date < ?))
    """, (today_str,))

    open_reports = count_query("SELECT COUNT(*) as c FROM reports WHERE status = 'Open'")
    open_damage_reports = count_query(
        "SELECT COUNT(*) as c FROM damage_reports WHERE status IN ('Reported', 'Under Review', 'Escalated')"
    )

    stats = {
        "total_students": total_students,
        "verified_students": verified_students,
        "total_listings": total_listings,
        "active_listings": active_listings,
        "total_rentals": total_rentals,
        "active_rentals": active_rentals,
        "pending_verifications": pending_verifications,
        "pending_requests": pending_requests,
        "overdue_rentals": overdue_rentals,
        "open_reports": open_reports,
        "open_damage_reports": open_damage_reports,
    }

    # Actionable queues
    recent_verifications = execute_query_all("""
        SELECT user_id, name, email, college, course, branch, enrollment_no, phone, created_at,
               verification_submitted_at, verification_resubmitted_at,
               id_card_image, id_card_front_image, id_card_back_image, college_id_front_path, college_id_back_path
        FROM users 
        WHERE (is_admin IS NULL OR is_admin = 0) AND verification_status = 'Pending' 
        ORDER BY user_id DESC 
        LIMIT 5
    """)
    for rv in recent_verifications:
        f_raw = rv.get("id_card_front_image") or rv.get("college_id_front_path") or rv.get("id_card_image")
        b_raw = rv.get("id_card_back_image") or rv.get("college_id_back_path")
        rv["front_doc"] = _resolve_admin_verification_doc_info(rv["user_id"], f_raw, "front")
        rv["back_doc"] = _resolve_admin_verification_doc_info(rv["user_id"], b_raw, "back")

    recent_actions = execute_query_all("""
        SELECT * FROM admin_actions 
        ORDER BY action_id DESC 
        LIMIT 6
    """)

    return render_template(
        "admin/dashboard.html",
        stats=stats,
        recent_verifications=recent_verifications,
        recent_actions=recent_actions,
        pending_verif_count=pending_verifications
    )


@app.route("/admin/students")
@admin_required
def admin_students():
    """
    Student Directory & Account Privilege Management.
    Selects explicit safe columns (never exposing password hashes or security answers).
    """
    search_q = request.args.get("q", "").strip()
    verif_filter = request.args.get("verif", "").strip()
    status_filter = request.args.get("status", "").strip()

    query = """
        SELECT user_id, name, email, college, course, branch, year, enrollment_no,
               phone, location, profile_image, id_card_image, id_card_front_image,
               id_card_back_image, college_id_front_path, college_id_back_path,
               rating, is_admin, verification_status, verified_at, hostel_room,
               created_at, status, verification_rejection_reason,
               verification_submitted_at, verification_resubmitted_at, verification_reviewed_at
        FROM users
        WHERE (is_admin IS NULL OR is_admin = 0)
    """
    params = []

    if search_q:
        query += " AND (LOWER(name) LIKE LOWER(?) OR LOWER(email) LIKE LOWER(?) OR LOWER(college) LIKE LOWER(?) OR LOWER(enrollment_no) LIKE LOWER(?))"
        like_p = f"%{search_q}%"
        params.extend([like_p, like_p, like_p, like_p])

    if verif_filter:
        if verif_filter in ("Unverified", "Not Submitted"):
            query += " AND (verification_status IS NULL OR verification_status NOT IN ('Verified', 'Pending', 'Rejected'))"
        else:
            query += " AND verification_status = ?"
            params.append(verif_filter)

    if status_filter:
        query += " AND LOWER(status) = LOWER(?)"
        params.append(status_filter)

    query += " ORDER BY user_id DESC"
    students = execute_query_all(query, tuple(params))
    for s in students:
        s["verification_status"] = normalize_verification_status(s.get("verification_status"))
        f_raw = s.get("id_card_front_image") or s.get("college_id_front_path") or s.get("id_card_image")
        b_raw = s.get("id_card_back_image") or s.get("college_id_back_path")
        s["front_doc"] = _resolve_admin_verification_doc_info(s["user_id"], f_raw, "front")
        s["back_doc"] = _resolve_admin_verification_doc_info(s["user_id"], b_raw, "back")

    pending_count = _pending_verifications_count()

    return render_template(
        "admin/students.html",
        students=students,
        search_query=search_q,
        verif_filter=verif_filter,
        status_filter=status_filter,
        pending_verif_count=pending_count
    )


@app.route("/admin/student/<int:user_id>/toggle-status", methods=["POST"])
@admin_required
def admin_toggle_student_status(user_id):
    """Suspend or restore student access privileges (forbids modifying admin accounts)."""
    user = execute_query_one("SELECT user_id, name, email, is_admin, status FROM users WHERE user_id = ?", (user_id,))
    if not user:
        flash("Student account not found.", "danger")
        return redirect(url_for("admin_students"))

    if _is_user_admin(user) or bool(user.get("is_admin")):
        flash("Administrator accounts cannot be suspended from the student directory.", "danger")
        return redirect(request.referrer or url_for("admin_students"))

    current_status = str(user.get("status") or "active").strip().lower()
    new_status = "suspended" if current_status == "active" else "active"
    ok, _ = execute_query_commit("UPDATE users SET status = ? WHERE user_id = ?", (new_status, user_id))
    if not ok:
        flash("Failed to update student account status. Please try again.", "danger")
        return redirect(request.referrer or url_for("admin_students"))

    action_label = "Student Suspended" if new_status == "suspended" else "Student Activated"
    log_admin_action(action_label, "User", user_id, f"Changed status of student '{user['name']}' to {new_status}")

    flash(f"Account for {user['name']} has been {new_status}.", "info")
    return redirect(request.referrer or url_for("admin_students"))


@app.route("/admin/verifications")
@admin_required
def admin_verifications():
    """Review student identity documents (Front & Back via short-lived signed URLs), history, and approvals."""
    status_filter = request.args.get("status", "Pending").strip()
    select_cols = """
        u.user_id, u.name, u.email, u.college, u.course, u.branch, u.year, u.enrollment_no, u.phone,
        u.hostel_room, u.location, u.id_card_image, u.id_card_front_image, u.id_card_back_image,
        u.college_id_front_path, u.college_id_back_path, u.verification_status, u.verified_at,
        u.verification_rejection_reason, u.created_at, u.status,
        u.verification_submitted_at, u.verification_resubmitted_at,
        u.verification_reviewed_at, u.verification_reviewed_by,
        adm.name as reviewed_by_name
    """

    if status_filter == "All":
        verifications = execute_query_all(
            f"SELECT {select_cols} FROM users u LEFT JOIN users adm ON u.verification_reviewed_by = adm.user_id WHERE (u.is_admin IS NULL OR u.is_admin = 0) ORDER BY u.user_id DESC"
        )
    elif status_filter in ("Verified", "Rejected"):
        verifications = execute_query_all(
            f"SELECT {select_cols} FROM users u LEFT JOIN users adm ON u.verification_reviewed_by = adm.user_id WHERE (u.is_admin IS NULL OR u.is_admin = 0) AND u.verification_status = ? ORDER BY u.user_id DESC",
            (status_filter,)
        )
    elif status_filter == "Not Submitted":
        verifications = execute_query_all(
            f"SELECT {select_cols} FROM users u LEFT JOIN users adm ON u.verification_reviewed_by = adm.user_id WHERE (u.is_admin IS NULL OR u.is_admin = 0) AND (u.verification_status IS NULL OR u.verification_status NOT IN ('Verified', 'Pending', 'Rejected')) ORDER BY u.user_id DESC"
        )
    else:
        status_filter = "Pending"
        verifications = execute_query_all(
            f"SELECT {select_cols} FROM users u LEFT JOIN users adm ON u.verification_reviewed_by = adm.user_id WHERE (u.is_admin IS NULL OR u.is_admin = 0) AND u.verification_status = 'Pending' ORDER BY u.user_id DESC"
        )

    user_ids = [v["user_id"] for v in verifications]
    history_by_user = {}
    if user_ids:
        ph = ",".join(["?"] * len(user_ids))
        hist_rows = execute_query_all(f"""
            SELECT h.*, adm.name as reviewer_name
            FROM verification_history h
            LEFT JOIN users adm ON h.reviewed_by = adm.user_id
            WHERE h.user_id IN ({ph})
            ORDER BY h.history_id DESC
        """, tuple(user_ids))
        for hr in hist_rows:
            history_by_user.setdefault(hr["user_id"], []).append(hr)

    for v in verifications:
        v["verification_status"] = normalize_verification_status(v.get("verification_status"))
        f_raw = v.get("id_card_front_image") or v.get("college_id_front_path") or v.get("id_card_image")
        b_raw = v.get("id_card_back_image") or v.get("college_id_back_path")
        v["front_doc"] = _resolve_admin_verification_doc_info(v["user_id"], f_raw, "front")
        v["back_doc"] = _resolve_admin_verification_doc_info(v["user_id"], b_raw, "back")
        v["history"] = history_by_user.get(v["user_id"], [])[:10]

    counts = {
        "pending": _pending_verifications_count(),
        "verified": count_query("SELECT COUNT(*) as c FROM users WHERE (is_admin IS NULL OR is_admin = 0) AND verification_status = 'Verified'"),
        "rejected": count_query("SELECT COUNT(*) as c FROM users WHERE (is_admin IS NULL OR is_admin = 0) AND verification_status = 'Rejected'"),
        "not_submitted": count_query("SELECT COUNT(*) as c FROM users WHERE (is_admin IS NULL OR is_admin = 0) AND (verification_status IS NULL OR verification_status NOT IN ('Verified', 'Pending', 'Rejected'))"),
    }

    return render_template(
        "admin/verifications.html",
        verifications=verifications,
        status_filter=status_filter,
        counts=counts,
        pending_verif_count=counts["pending"]
    )


@app.route("/admin/verification/<int:user_id>/approve", methods=["POST"])
@admin_required
def admin_approve_verification(user_id):
    """Manually approve college enrollment and grant Verified Student status."""
    admin_id = session.get("user_id")
    user = execute_query_one(
        """SELECT user_id, name, email, is_admin, verification_status,
                  verification_submitted_at, verification_resubmitted_at,
                  id_card_image, id_card_front_image, id_card_back_image,
                  college_id_front_path, college_id_back_path
           FROM users WHERE user_id = ?""",
        (user_id,)
    )
    if not user:
        flash("Student account not found.", "danger")
        return redirect(request.referrer or url_for("admin_verifications"))

    if user.get("is_admin"):
        flash("Administrator accounts cannot be modified in the student verification queue.", "danger")
        return redirect(request.referrer or url_for("admin_verifications"))

    prev_status = normalize_verification_status(user.get("verification_status"))
    if prev_status == "Verified":
        flash(f"Verification for {user['name']} is already approved.", "info")
        return redirect(request.referrer or url_for("admin_verifications"))

    if prev_status == "Not Submitted":
        flash(f"Cannot approve {user['name']} because they have not submitted verification details yet.", "warning")
        return redirect(request.referrer or url_for("admin_verifications"))

    has_front = bool(user.get("id_card_front_image") or user.get("college_id_front_path") or user.get("id_card_image"))
    has_back = bool(user.get("id_card_back_image") or user.get("college_id_back_path"))
    if not (has_front and has_back):
        flash(f"Cannot approve {user['name']}: both College ID Front and Back documents must be present.", "warning")
        return redirect(request.referrer or url_for("admin_verifications"))

    now_ts = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    ok, _ = execute_query_commit("""
        UPDATE users 
        SET verification_status = 'Verified',
            verified_at = ?,
            verification_reviewed_at = ?,
            verification_reviewed_by = ?,
            verification_rejection_reason = ''
        WHERE user_id = ?
    """, (now_ts, now_ts, admin_id, user_id))
    if not ok:
        flash("Could not save verification approval due to a database error.", "danger")
        return redirect(request.referrer or url_for("admin_verifications"))

    log_verification_history(
        user_id=user_id,
        previous_status=prev_status,
        new_status="Verified",
        action_type="APPROVED",
        submitted_at=user.get("verification_submitted_at"),
        resubmitted_at=user.get("verification_resubmitted_at"),
        reviewed_at=now_ts,
        reviewed_by=admin_id,
        notes=f"Approved by administrator #{admin_id}."
    )
    log_admin_action("Verification Approved", "User", user_id, f"Approved college verification for student {user['name']}")

    flash(f"Student verification approved for {user['name']}! Campus peer trust badge is now active.", "success")
    return redirect(request.referrer or url_for("admin_verifications"))


@app.route("/admin/verification/<int:user_id>/reject", methods=["POST"])
@admin_required
def admin_reject_verification(user_id):
    """Reject invalid or unreadable college verification document with mandatory reason."""
    admin_id = session.get("user_id")
    user = execute_query_one(
        """SELECT user_id, name, is_admin, verification_status, verification_rejection_reason,
                  verification_submitted_at, verification_resubmitted_at
           FROM users WHERE user_id = ?""",
        (user_id,)
    )
    if not user:
        flash("Student account not found.", "danger")
        return redirect(request.referrer or url_for("admin_verifications"))

    if user.get("is_admin"):
        flash("Administrator accounts cannot be rejected here.", "danger")
        return redirect(request.referrer or url_for("admin_verifications"))

    rejection_reason = (request.form.get("rejection_reason") or "").strip()
    if not rejection_reason:
        flash("A rejection reason is mandatory so the student knows what to fix and resubmit.", "danger")
        return redirect(request.referrer or url_for("admin_verifications"))

    prev_status = normalize_verification_status(user.get("verification_status"))
    if prev_status == "Rejected" and (user.get("verification_rejection_reason") or "").strip() == rejection_reason:
        flash(f"Verification for {user['name']} is already rejected with this reason.", "info")
        return redirect(request.referrer or url_for("admin_verifications"))

    now_ts = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    ok, _ = execute_query_commit("""
        UPDATE users 
        SET verification_status = 'Rejected',
            verified_at = NULL,
            verification_reviewed_at = ?,
            verification_reviewed_by = ?,
            verification_rejection_reason = ? 
        WHERE user_id = ?
    """, (now_ts, admin_id, rejection_reason, user_id))
    if not ok:
        flash("Could not save verification rejection due to a database error.", "danger")
        return redirect(request.referrer or url_for("admin_verifications"))

    log_verification_history(
        user_id=user_id,
        previous_status=prev_status,
        new_status="Rejected",
        action_type="REJECTED",
        submitted_at=user.get("verification_submitted_at"),
        resubmitted_at=user.get("verification_resubmitted_at"),
        reviewed_at=now_ts,
        reviewed_by=admin_id,
        rejection_reason=rejection_reason,
        notes=f"Rejected by administrator #{admin_id}."
    )
    log_admin_action("Verification Rejected", "User", user_id, f"Rejected verification request for student {user['name']}: {rejection_reason}")

    flash(f"Verification request rejected for {user['name']}. Rejection reason was saved.", "warning")
    return redirect(request.referrer or url_for("admin_verifications"))


@app.route("/admin/verification/<int:user_id>/revoke", methods=["POST"])
@admin_required
def admin_revoke_verification(user_id):
    """Revoke a previously Verified student's verification with a mandatory reason and audit trail."""
    admin_id = session.get("user_id")
    user = execute_query_one(
        """SELECT user_id, name, is_admin, verification_status,
                  verification_submitted_at, verification_resubmitted_at
           FROM users WHERE user_id = ?""",
        (user_id,)
    )
    if not user:
        flash("Student account not found.", "danger")
        return redirect(request.referrer or url_for("admin_verifications"))

    if user.get("is_admin"):
        flash("Administrator accounts cannot have verification revoked here.", "danger")
        return redirect(request.referrer or url_for("admin_verifications"))

    prev_status = normalize_verification_status(user.get("verification_status"))
    if prev_status != "Verified":
        flash(f"Only Verified student accounts can be revoked (current status: {prev_status}).", "warning")
        return redirect(request.referrer or url_for("admin_verifications"))

    revoke_reason = (request.form.get("revoke_reason") or request.form.get("rejection_reason") or "").strip()
    if not revoke_reason:
        flash("A revocation reason is mandatory for audit accountability.", "danger")
        return redirect(request.referrer or url_for("admin_verifications"))

    now_ts = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    ok, _ = execute_query_commit("""
        UPDATE users
        SET verification_status = 'Rejected',
            verified_at = NULL,
            verification_reviewed_at = ?,
            verification_reviewed_by = ?,
            verification_rejection_reason = ?
        WHERE user_id = ?
    """, (now_ts, admin_id, f"Verification Revoked: {revoke_reason}", user_id))
    if not ok:
        flash("Could not revoke verification due to a database error.", "danger")
        return redirect(request.referrer or url_for("admin_verifications"))

    log_verification_history(
        user_id=user_id,
        previous_status="Verified",
        new_status="Rejected",
        action_type="REVOKED",
        submitted_at=user.get("verification_submitted_at"),
        resubmitted_at=user.get("verification_resubmitted_at"),
        reviewed_at=now_ts,
        reviewed_by=admin_id,
        rejection_reason=f"Verification Revoked: {revoke_reason}",
        notes=f"Verified status revoked by administrator #{admin_id}."
    )
    log_admin_action("Verification Revoked", "User", user_id, f"Revoked verification for student {user['name']}: {revoke_reason}")

    flash(f"Verification revoked for {user['name']}. Student must re-submit valid ID proof for approval.", "warning")
    return redirect(request.referrer or url_for("admin_verifications"))


@app.route("/admin/listings")
@admin_required
def admin_listings():
    """Catalog moderation: inspect items, categories, and disable reported or prohibited gear."""
    search_q = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "").strip()
    type_filter = request.args.get("type", "").strip()

    query = """
        SELECT i.*, u.name as seller_name, u.email as seller_email, u.college as seller_college,
               (SELECT image_path FROM item_images WHERE item_id = i.item_id ORDER BY is_primary DESC, image_id ASC LIMIT 1) as primary_image,
               (SELECT COUNT(*) FROM reports rep WHERE rep.target_type = 'Listing' AND rep.target_id = i.item_id AND rep.status = 'Open') as open_reports_count
        FROM items i
        JOIN users u ON i.user_id = u.user_id
        WHERE 1=1
    """
    params = []

    if search_q:
        query += " AND (LOWER(i.title) LIKE LOWER(?) OR LOWER(i.category) LIKE LOWER(?) OR LOWER(u.name) LIKE LOWER(?) OR LOWER(i.city) LIKE LOWER(?))"
        like_p = f"%{search_q}%"
        params.extend([like_p, like_p, like_p, like_p])

    if status_filter:
        query += " AND i.status = ?"
        params.append(status_filter)

    if type_filter:
        query += " AND i.transaction_type = ?"
        params.append(type_filter)

    query += " ORDER BY i.item_id DESC"
    listings = execute_query_all(query, tuple(params))

    for it in listings:
        it["primary_image"] = _pick_valid_primary_image(it.get("item_id"), it.get("primary_image"), it.get("category", "Other"))
        it["is_sample"] = is_sample_email(it.get("seller_email"))

    pending_count = _pending_verifications_count()

    return render_template(
        "admin/listings.html",
        listings=listings,
        search_query=search_q,
        status_filter=status_filter,
        type_filter=type_filter,
        pending_verif_count=pending_count
    )


@app.route("/admin/listing/<int:item_id>/toggle", methods=["POST"])
@admin_required
def admin_toggle_listing(item_id):
    """Safely disable an Available listing or re-enable an Inactive/Archived listing."""
    item = execute_query_one("SELECT * FROM items WHERE item_id = ?", (item_id,))
    if not item:
        flash("Listing not found.", "danger")
        return redirect(request.referrer or url_for("admin_listings"))

    current_status = item.get("status") or "Available"
    if current_status == "Available":
        new_status = "Inactive"
    elif current_status in ("Inactive", "Archived", "Unavailable"):
        new_status = "Available"
    else:
        flash(
            f"Listing '{item['title']}' is currently '{current_status}' due to a transaction and cannot be toggled directly.",
            "warning"
        )
        return redirect(request.referrer or url_for("admin_listings"))

    ok, _ = execute_query_commit("UPDATE items SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE item_id = ?", (new_status, item_id))
    if not ok:
        flash("Database error while updating listing status.", "danger")
        return redirect(request.referrer or url_for("admin_listings"))

    action_label = "Listing Disabled" if new_status == "Inactive" else "Listing Enabled"
    log_admin_action(action_label, "Listing", item_id, f"Changed listing status of '{item['title']}' from {current_status} to {new_status}")

    flash(f"Listing '{item['title']}' is now {new_status}.", "info")
    return redirect(request.referrer or url_for("admin_listings"))


@app.route("/admin/rentals")
@admin_required
def admin_rentals():
    """All Campus Rental Handovers, Due Dates, Pending Proposals, and Overdue Tracking."""
    sync_overdue_rentals()
    active_tab = request.args.get("tab", "all").strip().lower()

    all_rentals = execute_query_all("""
        SELECT r.*, i.title as item_title, i.category as item_category,
               i_off.title as offered_item_title,
               u_owner.name as owner_name, u_owner.college as owner_college,
               u_renter.name as renter_name, u_renter.college as renter_college,
               (SELECT image_path FROM item_images WHERE item_id = r.item_id ORDER BY is_primary DESC, image_id ASC LIMIT 1) as primary_image
        FROM rental_requests r
        JOIN items i ON r.item_id = i.item_id
        LEFT JOIN items i_off ON r.offered_item_id = i_off.item_id
        JOIN users u_owner ON r.owner_id = u_owner.user_id
        JOIN users u_renter ON r.renter_id = u_renter.user_id
        ORDER BY r.created_at DESC
    """)

    today = date.today()
    for r in all_rentals:
        r["primary_image"] = _pick_valid_primary_image(r.get("item_id"), r.get("primary_image"), r.get("item_category", "Other"))

        if r.get("request_type") == "Rent":
            if r["status"] == "RETURN_REQUESTED":
                r["display_status"] = "RETURN_REQUESTED"
                if r.get("due_date"):
                    try:
                        due = datetime.strptime(str(r["due_date"])[:10], "%Y-%m-%d").date()
                        r["days_remaining"] = (due - today).days
                    except Exception:
                        r["days_remaining"] = None
                else:
                    r["days_remaining"] = None
            elif r.get("due_date") and r["status"] in ("ACCEPTED", "ACTIVE", "OVERDUE"):
                try:
                    due = datetime.strptime(str(r["due_date"])[:10], "%Y-%m-%d").date()
                    diff = (due - today).days
                    r["days_remaining"] = diff
                    if diff < 0 or r["status"] == "OVERDUE":
                        r["display_status"] = "OVERDUE"
                    elif diff <= 2:
                        r["display_status"] = "DUE SOON"
                    else:
                        r["display_status"] = "ACTIVE"
                except Exception:
                    r["days_remaining"] = None
                    r["display_status"] = r["status"]
            else:
                r["days_remaining"] = None
                r["display_status"] = r["status"]
        else:
            r["days_remaining"] = None
            r["display_status"] = r["status"]

    pending_list = [r for r in all_rentals if r["status"] == "PENDING"]
    active_list = [r for r in all_rentals if r["status"] in ("ACCEPTED", "ACTIVE", "RETURN_REQUESTED") and r["display_status"] != "OVERDUE"]
    overdue_list = [r for r in all_rentals if r.get("request_type") == "Rent" and (r["display_status"] == "OVERDUE" or r["status"] == "OVERDUE")]
    disputed_list = [r for r in all_rentals if r["status"] == "DISPUTED"]
    completed_list = [r for r in all_rentals if r["status"] in ("COMPLETED", "RETURNED")]
    return_requested_list = [r for r in all_rentals if r.get("request_type") == "Rent" and r["status"] == "RETURN_REQUESTED"]
    closed_list = [r for r in all_rentals if r["status"] in ("REJECTED", "CANCELLED")]

    counts = {
        "all": len(all_rentals),
        "pending": len(pending_list),
        "active": len(active_list),
        "overdue": len(overdue_list),
        "disputed": len(disputed_list),
        "completed": len(completed_list),
        "return_requested": len(return_requested_list),
        "closed": len(closed_list),
    }

    display_list = all_rentals
    if active_tab == "pending":
        display_list = pending_list
    elif active_tab == "active":
        display_list = active_list
    elif active_tab == "overdue":
        display_list = overdue_list
    elif active_tab == "return_requested":
        display_list = return_requested_list
    elif active_tab == "disputed":
        display_list = disputed_list
    elif active_tab == "completed":
        display_list = completed_list
    elif active_tab == "closed":
        display_list = closed_list

    # Attach audit trail logs per rental request
    all_logs = execute_query_all("""
        SELECT l.*, u.name as changed_by_name
        FROM rental_status_log l
        LEFT JOIN users u ON l.changed_by = u.user_id
        ORDER BY l.created_at ASC, l.log_id ASC
    """)
    logs_by_req = {}
    for lg in all_logs:
        logs_by_req.setdefault(lg["request_id"], []).append(lg)
    for r in display_list:
        r["status_logs"] = logs_by_req.get(r["request_id"], [])

    pending_count = _pending_verifications_count()

    return render_template(
        "admin/rentals.html",
        rentals=display_list,
        active_tab=active_tab,
        counts=counts,
        pending_verif_count=pending_count
    )


@app.route("/admin/damage-reports")
@admin_required
def admin_damage_reports():
    """Mediate damage disputes and evaluate photographic evidence impartially."""
    reports = execute_query_all("""
        SELECT dr.*, i.title as item_title,
               r.status as rental_status,
               u_rep.name as reporter_name,
               u_owner.name as owner_name,
               u_renter.name as renter_name
        FROM damage_reports dr
        JOIN items i ON dr.item_id = i.item_id
        JOIN users u_rep ON dr.reporter_id = u_rep.user_id
        JOIN rental_requests r ON dr.rental_id = r.request_id
        JOIN users u_owner ON r.owner_id = u_owner.user_id
        JOIN users u_renter ON r.renter_id = u_renter.user_id
        ORDER BY dr.report_id DESC
    """)

    pending_count = _pending_verifications_count()

    return render_template(
        "admin/damage_reports.html",
        reports=reports,
        pending_verif_count=pending_count
    )


@app.route("/admin/damage-report/<int:report_id>/resolve", methods=["POST"])
@admin_required
def admin_resolve_damage_report(report_id):
    """
    Staff resolves a peer damage dispute.
    Strictly bars arbitrary owner-imposed penalties and keeps rental_requests + audit logs consistent.
    """
    rep = execute_query_one("SELECT * FROM damage_reports WHERE report_id = ?", (report_id,))
    if not rep:
        flash("Damage report record not found.", "danger")
        return redirect(url_for("admin_damage_reports"))

    allowed_verdicts = {
        "Under Review",
        "Resolved — No Penalty",
        "Resolved — Penalty Applicable",
        "Rejected",
        "Escalated",
    }
    verdict_status = request.form.get("status", "Resolved — No Penalty").strip()
    if verdict_status not in allowed_verdicts:
        flash("Invalid mediation verdict selected.", "danger")
        return redirect(url_for("admin_damage_reports"))

    admin_remark = request.form.get("admin_remark", "").strip()
    if not admin_remark:
        flash("Please provide an administrative remark explaining your verdict.", "warning")
        return redirect(url_for("admin_damage_reports"))

    if rep.get("status") == verdict_status and (rep.get("admin_remark") or "").strip() == admin_remark:
        flash(f"Damage dispute #{report_id} already has this verdict recorded.", "info")
        return redirect(url_for("admin_damage_reports"))

    ok, _ = execute_query_commit("""
        UPDATE damage_reports 
        SET status = ?, admin_remark = ? 
        WHERE report_id = ?
    """, (verdict_status, admin_remark, report_id))
    if not ok:
        flash("Failed to save damage dispute resolution.", "danger")
        return redirect(url_for("admin_damage_reports"))

    rental = execute_query_one("SELECT * FROM rental_requests WHERE request_id = ?", (rep["rental_id"],))
    if rental:
        prev_rental_status = rental.get("status")
        if verdict_status.startswith("Resolved"):
            if prev_rental_status != "COMPLETED":
                today_str = date.today().strftime('%Y-%m-%d')
                execute_query_commit(
                    "UPDATE rental_requests SET status = 'COMPLETED', returned_date = COALESCE(returned_date, ?), updated_at = CURRENT_TIMESTAMP WHERE request_id = ?",
                    (today_str, rep["rental_id"])
                )
                log_rental_status_transition(
                    request_id=rep["rental_id"],
                    previous_status=prev_rental_status,
                    new_status="COMPLETED",
                    changed_by=session.get("user_id"),
                    action_role="admin",
                    notes=f"Admin resolved damage dispute #{report_id}: {verdict_status}"
                )
            if rental.get("request_type") == "Rent":
                execute_query_commit(
                    "UPDATE items SET status = 'Available', updated_at = CURRENT_TIMESTAMP WHERE item_id = ? AND status = 'Rented'",
                    (rental["item_id"],)
                )
        elif verdict_status == "Rejected" and prev_rental_status == "DISPUTED":
            default_active = "ACTIVE" if rental.get("request_type") == "Rent" else "ACCEPTED"
            restored_status = "COMPLETED" if rental.get("returned_date") else default_active
            if restored_status == "ACTIVE" and rental.get("request_type") == "Rent" and rental.get("due_date") and str(rental["due_date"])[:10] < date.today().strftime('%Y-%m-%d'):
                restored_status = "OVERDUE"
            execute_query_commit(
                "UPDATE rental_requests SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE request_id = ?",
                (restored_status, rep["rental_id"])
            )
            log_rental_status_transition(
                request_id=rep["rental_id"],
                previous_status="DISPUTED",
                new_status=restored_status,
                changed_by=session.get("user_id"),
                action_role="admin",
                notes=f"Admin rejected damage claim #{report_id} and restored rental status to {restored_status}"
            )

    log_admin_action("Damage Dispute Resolved", "DamageReport", report_id, f"Verdict: {verdict_status}. Remark: {admin_remark}")

    flash(f"Damage dispute #{report_id} has been updated: {verdict_status}.", "success")
    return redirect(url_for("admin_damage_reports"))


@app.route("/admin/reports")
@admin_required
def admin_reports():
    """Campus Safety and Policy Violation Reports Queue."""
    status_filter = request.args.get("status", "All").strip()
    query = """
        SELECT rep.*, u.name as reporter_name, u.college as reporter_college
        FROM reports rep
        JOIN users u ON rep.reporter_id = u.user_id
    """
    params = []
    if status_filter in ("Open", "Resolved", "Dismissed"):
        query += " WHERE rep.status = ?"
        params.append(status_filter)
    else:
        status_filter = "All"
    query += " ORDER BY rep.report_id DESC"

    reports = execute_query_all(query, tuple(params))

    # Resolve target context (Listing title, User name, or Transaction item) for admin clarity
    for rep in reports:
        t_type = str(rep.get("target_type") or "").strip()
        t_id = rep.get("target_id")
        rep["target_label"] = f"{t_type} #{t_id}"
        rep["target_url"] = None
        if t_type == "Listing" and t_id:
            item_row = execute_query_one("SELECT title, status FROM items WHERE item_id = ?", (t_id,))
            if item_row:
                rep["target_label"] = f"{item_row['title']} ({item_row['status']})"
                rep["target_url"] = url_for("item_details", item_id=t_id)
        elif t_type == "User" and t_id:
            user_row = execute_query_one("SELECT name, status FROM users WHERE user_id = ?", (t_id,))
            if user_row:
                rep["target_label"] = f"{user_row['name']} ({user_row['status']})"
                rep["target_url"] = url_for("admin_students", q=user_row["name"])
        elif t_type == "Transaction" and t_id:
            tx_row = execute_query_one("""
                SELECT r.status, i.title as item_title
                FROM rental_requests r
                JOIN items i ON r.item_id = i.item_id
                WHERE r.request_id = ?
            """, (t_id,))
            if tx_row:
                rep["target_label"] = f"Rental #{t_id}: {tx_row['item_title']} ({tx_row['status']})"
                rep["target_url"] = url_for("admin_rentals")

    counts = {
        "all": count_query("SELECT COUNT(*) as c FROM reports"),
        "open": count_query("SELECT COUNT(*) as c FROM reports WHERE status = 'Open'"),
        "resolved": count_query("SELECT COUNT(*) as c FROM reports WHERE status = 'Resolved'"),
        "dismissed": count_query("SELECT COUNT(*) as c FROM reports WHERE status = 'Dismissed'"),
    }

    pending_count = _pending_verifications_count()

    return render_template(
        "admin/reports.html",
        reports=reports,
        status_filter=status_filter,
        counts=counts,
        pending_verif_count=pending_count
    )


@app.route("/admin/report/<int:report_id>/resolve", methods=["POST"])
@admin_required
def admin_resolve_report(report_id):
    """Resolve or dismiss a safety or misconduct report."""
    rep = execute_query_one("SELECT * FROM reports WHERE report_id = ?", (report_id,))
    if not rep:
        flash("Report record not found.", "danger")
        return redirect(request.referrer or url_for("admin_reports"))

    action_type = request.form.get("action", "resolve").strip().lower()
    if action_type not in ("resolve", "dismiss"):
        flash("Invalid report action specified.", "danger")
        return redirect(request.referrer or url_for("admin_reports"))

    admin_remark = request.form.get("admin_remark", "").strip()
    if not admin_remark:
        flash("Please enter a staff resolution note before closing this report.", "warning")
        return redirect(request.referrer or url_for("admin_reports"))

    new_status = "Resolved" if action_type == "resolve" else "Dismissed"
    if rep.get("status") == new_status and (rep.get("admin_remark") or "").strip() == admin_remark:
        flash(f"Report #{report_id} is already marked as {new_status}.", "info")
        return redirect(request.referrer or url_for("admin_reports"))

    ok, _ = execute_query_commit("""
        UPDATE reports 
        SET status = ?, admin_remark = ? 
        WHERE report_id = ?
    """, (new_status, admin_remark, report_id))
    if not ok:
        flash("Database error while updating report status.", "danger")
        return redirect(request.referrer or url_for("admin_reports"))

    log_admin_action(f"Report {new_status}", "Report", report_id, f"Remark: {admin_remark}")

    flash(f"Report #{report_id} marked as {new_status}.", "info")
    return redirect(request.referrer or url_for("admin_reports"))


@app.route("/admin/reviews")
@admin_required
def admin_reviews():
    """Review moderation: audit peer reviews and ratings for campus standards."""
    reviews = execute_query_all("""
        SELECT rev.*, 
               u_reviewer.name as reviewer_name, u_reviewer.college as reviewer_college,
               u_reviewed.name as reviewed_name, u_reviewed.college as reviewed_college,
               i.title as item_title
        FROM reviews rev
        JOIN users u_reviewer ON rev.reviewer_id = u_reviewer.user_id
        JOIN users u_reviewed ON rev.reviewed_user_id = u_reviewed.user_id
        LEFT JOIN items i ON rev.item_id = i.item_id
        ORDER BY rev.created_at DESC
    """)

    pending_count = _pending_verifications_count()

    return render_template(
        "admin/reviews.html",
        reviews=reviews,
        pending_verif_count=pending_count
    )


@app.route("/admin/review/<int:review_id>/delete", methods=["POST"])
@admin_required
def admin_delete_review(review_id):
    """Delete an inappropriate review and recalculate target student's rating."""
    rev = execute_query_one("SELECT * FROM reviews WHERE review_id = ?", (review_id,))
    if not rev:
        flash("Review not found.", "danger")
        return redirect(url_for("admin_reviews"))

    target_user_id = rev["reviewed_user_id"]
    execute_query_commit("DELETE FROM reviews WHERE review_id = ?", (review_id,))

    # Recalculate average rating
    avg_row = execute_query_one("SELECT AVG(rating) as avg_r FROM reviews WHERE reviewed_user_id = ?", (target_user_id,))
    new_rating = round(float(avg_row["avg_r"]), 2) if avg_row and avg_row["avg_r"] is not None else 5.0
    execute_query_commit("UPDATE users SET rating = ? WHERE user_id = ?", (new_rating, target_user_id))

    log_admin_action("Review Deleted", "Review", review_id, f"Deleted review for user #{target_user_id} and reset rating to {new_rating}")

    flash("Review removed and student trust score recalculated.", "info")
    return redirect(url_for("admin_reviews"))


@app.route("/admin/action-log")
@app.route("/admin/actions")
@admin_required
def admin_action_log():
    """Display history of administrative decisions and staff actions."""
    actions = execute_query_all("SELECT * FROM admin_actions ORDER BY action_id DESC LIMIT 100")
    pending_count = _pending_verifications_count()

    return render_template(
        "admin/action_log.html",
        actions=actions,
        pending_verif_count=pending_count
    )


# ==============================================================================
# PHASE 3: AI INTELLIGENCE ROUTES (GEMINI POWERED)
# ==============================================================================

@app.route("/ai-search")
def ai_search():
    """
    Part B: AI Smart Search.
    Translates plain student language into structured intent using Gemini,
    then executes precision filtering on live Supabase PostgreSQL items.
    """
    raw_query = request.args.get("q", "").strip()

    if not raw_query:
        return render_template("ai_search.html", is_searched=False, query="", items=[], understanding=None)

    # 1. Parse intent with Gemini API (or semantic fallback)
    understanding = gemini_service.parse_search_query_with_ai(raw_query)

    # 2. Build parameterized PostgreSQL query
    sql = """
        SELECT i.*, 
               (SELECT image_path FROM item_images WHERE item_id = i.item_id ORDER BY is_primary DESC, image_id ASC LIMIT 1) as primary_image
        FROM items i
        WHERE i.status = 'Available'
    """
    params = []

    # Category matching
    if understanding.get("category"):
        sql += " AND i.category = ?"
        params.append(understanding["category"])

    # Transaction type matching
    if understanding.get("transaction_type"):
        sql += " AND i.transaction_type = ?"
        params.append(understanding["transaction_type"])

    # Budget matching
    if understanding.get("max_price"):
        max_p = float(understanding["max_price"])
        if understanding.get("transaction_type") == "Rent":
            sql += " AND i.rent_amount <= ?"
            params.append(max_p)
        elif understanding.get("transaction_type") == "Sell":
            sql += " AND i.price <= ?"
            params.append(max_p)
        else:
            sql += " AND (i.rent_amount <= ? OR i.price <= ?)"
            params.extend([max_p, max_p])

    # Keyword matching
    kws = understanding.get("keywords") or []
    if kws:
        kw_clauses = []
        for kw in kws[:3]:
            kw_clauses.append("(i.title LIKE ? OR i.description LIKE ?)")
            p = f"%{kw}%"
            params.extend([p, p])
        if kw_clauses:
            sql += " AND (" + " OR ".join(kw_clauses) + ")"

    sql += " ORDER BY i.item_id DESC LIMIT 15"
    results = execute_query_all(sql, tuple(params))

    # If strict query yielded zero results, perform relaxed fallback search
    if not results and (understanding.get("category") or kws):
        relaxed_sql = """
            SELECT i.*, 
                   (SELECT image_path FROM item_images WHERE item_id = i.item_id ORDER BY is_primary DESC, image_id ASC LIMIT 1) as primary_image
            FROM items i
            WHERE i.status = 'Available'
        """
        relaxed_params = []
        if understanding.get("category"):
            relaxed_sql += " AND i.category = ?"
            relaxed_params.append(understanding["category"])
        elif kws:
            relaxed_sql += " AND (i.title LIKE ? OR i.description LIKE ?)"
            p = f"%{kws[0]}%"
            relaxed_params.extend([p, p])
        relaxed_sql += " ORDER BY i.item_id DESC LIMIT 12"
        results = execute_query_all(relaxed_sql, tuple(relaxed_params))

    # Attach primary images and reason explanation
    for it in results:
        it["primary_image"] = _pick_valid_primary_image(it.get("item_id"), it.get("primary_image"), it.get("category", "Other"))
        it["ai_reason"] = gemini_service.generate_match_reason(understanding.get("understanding_summary", ""), it)

    # Log interaction
    log_ai_interaction("smart_search", raw_query, f"Matched {len(results)} items. {understanding.get('understanding_summary')}")

    return render_template(
        "ai_search.html",
        is_searched=True,
        query=raw_query,
        items=results,
        understanding=understanding
    )


@app.route("/ai-matching")
@app.route("/smart-matching")
def ai_matching():
    """
    Part C: AI Smart Matching (Equipment Swap Engine).
    Identifies compatible barter needs between campus students without money.
    """
    current_user_id = session.get("user_id")
    user = execute_query_one("SELECT * FROM users WHERE user_id = ?", (current_user_id,)) if current_user_id else None

    # Fetch all items open to exchange
    all_exchange_items = execute_query_all("""
        SELECT i.*, u.name as seller_name, u.location as seller_location, u.college as seller_college,
               (SELECT image_path FROM item_images WHERE item_id = i.item_id ORDER BY is_primary DESC, image_id ASC LIMIT 1) as primary_image
        FROM items i
        JOIN users u ON i.user_id = u.user_id
        WHERE i.status = 'Available' AND (i.transaction_type = 'Exchange' OR i.exchange_preference != '')
        ORDER BY i.item_id DESC
    """)

    for it in all_exchange_items:
        it["primary_image"] = _pick_valid_primary_image(it.get("item_id"), it.get("primary_image"), it.get("category", "Other"))

    # If student is logged in, find personalized matches for their gear
    user_items = []
    personalized_matches = []
    if current_user_id:
        user_items = [it for it in all_exchange_items if it.get("user_id") == current_user_id]
        if not user_items:
            # Check if user has any active items at all
            user_items = execute_query_all("""
                SELECT i.*, 
                       (SELECT image_path FROM item_images WHERE item_id = i.item_id ORDER BY is_primary DESC, image_id ASC LIMIT 1) as primary_image
                FROM items i
                WHERE i.user_id = ? AND i.status = 'Available'
            """, (current_user_id,))
            for it in user_items:
                it["primary_image"] = _pick_valid_primary_image(it.get("item_id"), it.get("primary_image"), it.get("category", "Other"))

        personalized_matches = gemini_service.find_smart_swap_matches(user_items, all_exchange_items)

    # Campus-wide pairings
    campus_pairs = gemini_service.find_campus_wide_swap_pairs(all_exchange_items)

    log_ai_interaction("smart_matching", f"User #{current_user_id or 0}", f"Found {len(personalized_matches)} user matches, {len(campus_pairs)} campus pairs")

    return render_template(
        "ai_matching.html",
        user=user,
        user_items=user_items,
        personalized_matches=personalized_matches,
        campus_pairs=campus_pairs
    )


@app.route("/ai-recommendations")
def ai_recommendations():
    """
    Part D: AI Recommendations.
    Intelligent student-tailored gear recommendations grounded in live PostgreSQL data.
    """
    current_user_id = session.get("user_id")
    user = execute_query_one("SELECT * FROM users WHERE user_id = ?", (current_user_id,)) if current_user_id else None

    # Fetch active items with seller ratings and views
    all_active = execute_query_all("""
        SELECT i.*, u.name as seller_name, u.location as seller_location, u.rating as seller_rating, u.verification_status as seller_verified,
               (SELECT image_path FROM item_images WHERE item_id = i.item_id ORDER BY is_primary DESC, image_id ASC LIMIT 1) as primary_image
        FROM items i
        JOIN users u ON i.user_id = u.user_id
        WHERE i.status = 'Available'
        ORDER BY i.views_count DESC, i.item_id DESC
    """)

    for it in all_active:
        it["primary_image"] = _pick_valid_primary_image(it.get("item_id"), it.get("primary_image"), it.get("category", "Other"))

    # 1. Recommended for You: Top rated peer gear
    recommended_for_you = sorted(all_active, key=lambda x: (x.get("seller_rating") or 5.0, x.get("views_count") or 0), reverse=True)[:8]

    # 2. Best Rental Deals: sorted by rent amount
    rental_deals = sorted([it for it in all_active if it.get("transaction_type") == "Rent"], key=lambda x: x.get("rent_amount") or 0)[:8]

    # 3. Swap Picks: items open for exchange
    swap_picks = [it for it in all_active if it.get("transaction_type") == "Exchange" or it.get("exchange_preference")][:8]

    log_ai_interaction("recommendations", f"User #{current_user_id or 0}", f"Generated {len(recommended_for_you)} recs, {len(rental_deals)} rental deals")

    return render_template(
        "ai_recommendations.html",
        user=user,
        recommended_for_you=recommended_for_you,
        rental_deals=rental_deals,
        swap_picks=swap_picks
    )


# ==============================================================================
# PRIVATE MESSAGING SYSTEM (CONVERSATIONS & POLLING CHAT)
# ==============================================================================

MAX_CHAT_MESSAGE_LENGTH = 1000


def _format_listing_price_badge(row: dict) -> str:
    tx_type = (row.get("listing_type") or row.get("transaction_type") or "Rent").strip()
    if tx_type == "Sell":
        price = float(row.get("listing_price") or row.get("price") or 0)
        return f"₹{price:,.0f}"
    if tx_type == "Rent":
        rent_amt = float(row.get("listing_rent_amount") or row.get("rent_amount") or 0)
        period = (row.get("listing_rent_period") or row.get("rent_period") or "Day").lower()
        return f"₹{rent_amt:,.0f}/{period}"
    return "Swap / Exchange"


def _resolve_listing_thumb(raw_path: str | None, category: str | None) -> str:
    path = (raw_path or "").strip()
    if path and _is_resolvable_image(path):
        return resolve_image_url(path, category)
    fallback = CATEGORY_PLACEHOLDERS.get(category or "", "placeholders/default.svg")
    return url_for("static", filename=f"images/{fallback}")


def _get_or_create_conversation(listing_id: int, owner_id: int, interested_user_id: int, request_id: int | None = None) -> dict | None:
    """
    Atomically locate or create the unique 1-to-1 conversation for
    (listing_id, owner_id, interested_user_id). Never creates duplicates.
    """
    if owner_id == interested_user_id:
        return None

    pool = get_pg_pool()
    if pool:
        for attempt in range(2):
            conn = None
            try:
                conn = _get_healthy_pg_conn(pool)
                cur = conn.cursor(cursor_factory=RealDictCursor)
                cur.execute(
                    """
                    INSERT INTO conversations (listing_id, owner_id, interested_user_id, request_id)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (listing_id, owner_id, interested_user_id) DO NOTHING
                    """,
                    (listing_id, owner_id, interested_user_id, request_id),
                )
                if request_id is not None:
                    cur.execute(
                        """
                        UPDATE conversations
                        SET request_id = COALESCE(request_id, %s)
                        WHERE listing_id = %s AND owner_id = %s AND interested_user_id = %s
                        """,
                        (request_id, listing_id, owner_id, interested_user_id),
                    )
                cur.execute(
                    """
                    SELECT * FROM conversations
                    WHERE listing_id = %s AND owner_id = %s AND interested_user_id = %s
                    LIMIT 1
                    """,
                    (listing_id, owner_id, interested_user_id),
                )
                row = cur.fetchone()
                conn.commit()
                cur.close()
                _return_pg_conn(pool, conn)
                return _serialize_row(dict(row)) if row else None
            except Exception as e:
                is_conn_drop = isinstance(e, (psycopg2.OperationalError, psycopg2.InterfaceError)) if PSYCOPG2_AVAILABLE else False
                print(f"[Messaging PG Error] _get_or_create_conversation: {e}")
                if conn:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    _return_pg_conn(pool, conn, force_close=is_conn_drop)
                if is_conn_drop and attempt == 0:
                    continue
                return None

    # SQLite fallback
    conn = get_sqlite_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR IGNORE INTO conversations (listing_id, owner_id, interested_user_id, request_id)
            VALUES (?, ?, ?, ?)
            """,
            (listing_id, owner_id, interested_user_id, request_id),
        )
        if request_id is not None:
            cur.execute(
                """
                UPDATE conversations
                SET request_id = COALESCE(request_id, ?)
                WHERE listing_id = ? AND owner_id = ? AND interested_user_id = ?
                """,
                (request_id, listing_id, owner_id, interested_user_id),
            )
        cur.execute(
            """
            SELECT * FROM conversations
            WHERE listing_id = ? AND owner_id = ? AND interested_user_id = ?
            LIMIT 1
            """,
            (listing_id, owner_id, interested_user_id),
        )
        row = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        return _serialize_row(dict(row)) if row else None
    except Exception as e:
        print(f"[Messaging SQLite Error] _get_or_create_conversation: {e}")
        conn.close()
        return None


def _get_conversation_for_participant(conversation_id: int, user_id: int) -> tuple:
    """
    Fetch conversation metadata with privacy-safe peer & listing info.
    Returns (status_code: int, data: dict | None).
    - 404 if conversation doesn't exist
    - 403 if current user is NOT owner_id or interested_user_id
    - 200 if authorized
    """
    conv = execute_query_one(
        """
        SELECT c.conversation_id, c.listing_id, c.owner_id, c.interested_user_id,
               c.request_id, c.created_at, c.last_message_at,
               i.title AS listing_title, i.category AS listing_category,
               i.transaction_type AS listing_type, i.status AS listing_status,
               i.price AS listing_price, i.rent_amount AS listing_rent_amount,
               i.rent_period AS listing_rent_period,
               (SELECT image_path FROM item_images WHERE item_id = i.item_id ORDER BY is_primary DESC, image_id ASC LIMIT 1) AS listing_image,
               owner_u.name AS owner_name, owner_u.verification_status AS owner_verification,
               int_u.name AS interested_name, int_u.verification_status AS interested_verification
        FROM conversations c
        JOIN items i ON c.listing_id = i.item_id
        JOIN users owner_u ON c.owner_id = owner_u.user_id
        JOIN users int_u ON c.interested_user_id = int_u.user_id
        WHERE c.conversation_id = ?
        LIMIT 1
        """,
        (conversation_id,),
    )
    if not conv:
        return 404, None

    if int(user_id) not in (int(conv["owner_id"]), int(conv["interested_user_id"])):
        return 403, None

    is_owner = (int(user_id) == int(conv["owner_id"]))
    other_name = (conv["interested_name"] if is_owner else conv["owner_name"]) or "Student"
    other_verif = normalize_verification_status(
        conv["interested_verification"] if is_owner else conv["owner_verification"]
    )
    other_initial = other_name.strip()[0].upper() if other_name.strip() else "S"

    payload = {
        "conversation_id": int(conv["conversation_id"]),
        "listing_id": int(conv["listing_id"]),
        "listing_title": conv["listing_title"],
        "listing_category": conv["listing_category"],
        "listing_type": conv["listing_type"],
        "listing_status": conv["listing_status"],
        "listing_price_display": _format_listing_price_badge(conv),
        "listing_thumb": _resolve_listing_thumb(conv.get("listing_image"), conv.get("listing_category")),
        "listing_url": url_for("item_details", item_id=conv["listing_id"]),
        "other_user_name": other_name,
        "other_user_initial": other_initial,
        "other_user_verified": (other_verif == "Verified"),
        "role_label": "Interested Student" if is_owner else "Listing Owner",
        "request_id": conv.get("request_id"),
        "last_message_at": str(conv.get("last_message_at") or conv.get("created_at") or ""),
    }
    return 200, payload


def _get_user_conversations(user_id: int) -> list:
    """
    Fetch all conversations belonging strictly to `user_id` (as owner or interested user),
    sorted by most recent activity, using privacy-safe fields only.
    """
    rows = execute_query_all(
        """
        SELECT c.conversation_id, c.listing_id, c.owner_id, c.interested_user_id,
               c.request_id, c.created_at, c.last_message_at,
               i.title AS listing_title, i.category AS listing_category,
               i.transaction_type AS listing_type, i.status AS listing_status,
               i.price AS listing_price, i.rent_amount AS listing_rent_amount,
               i.rent_period AS listing_rent_period,
               (SELECT image_path FROM item_images WHERE item_id = i.item_id ORDER BY is_primary DESC, image_id ASC LIMIT 1) AS listing_image,
               owner_u.name AS owner_name, owner_u.verification_status AS owner_verification,
               int_u.name AS interested_name, int_u.verification_status AS interested_verification,
               (SELECT message_text FROM messages m WHERE m.conversation_id = c.conversation_id ORDER BY m.message_id DESC LIMIT 1) AS last_message_text,
               (SELECT sender_id FROM messages m WHERE m.conversation_id = c.conversation_id ORDER BY m.message_id DESC LIMIT 1) AS last_message_sender_id,
               (SELECT created_at FROM messages m WHERE m.conversation_id = c.conversation_id ORDER BY m.message_id DESC LIMIT 1) AS last_msg_created_at,
               (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.conversation_id AND m.sender_id != ? AND NOT m.is_read) AS unread_count
        FROM conversations c
        JOIN items i ON c.listing_id = i.item_id
        JOIN users owner_u ON c.owner_id = owner_u.user_id
        JOIN users int_u ON c.interested_user_id = int_u.user_id
        WHERE c.owner_id = ? OR c.interested_user_id = ?
        ORDER BY c.last_message_at DESC, c.conversation_id DESC
        """,
        (user_id, user_id, user_id),
    )

    conversations = []
    for r in rows:
        is_owner = (int(r["owner_id"]) == int(user_id))
        other_name = (r["interested_name"] if is_owner else r["owner_name"]) or "Student"
        other_verif = normalize_verification_status(
            r["interested_verification"] if is_owner else r["owner_verification"]
        )
        other_initial = other_name.strip()[0].upper() if other_name.strip() else "S"
        last_text = r.get("last_message_text") or ""
        if last_text and int(r.get("last_message_sender_id") or 0) == int(user_id):
            preview = f"You: {last_text}"
        elif last_text:
            preview = last_text
        else:
            preview = "No messages yet — say hello!"

        conversations.append({
            "conversation_id": int(r["conversation_id"]),
            "listing_id": int(r["listing_id"]),
            "listing_title": r["listing_title"],
            "listing_category": r["listing_category"],
            "listing_type": r["listing_type"],
            "listing_status": r["listing_status"],
            "listing_price_display": _format_listing_price_badge(r),
            "listing_thumb": _resolve_listing_thumb(r.get("listing_image"), r.get("listing_category")),
            "listing_url": url_for("item_details", item_id=r["listing_id"]),
            "other_user_name": other_name,
            "other_user_initial": other_initial,
            "other_user_verified": (other_verif == "Verified"),
            "role_label": "Interested Student" if is_owner else "Listing Owner",
            "last_message_preview": preview,
            "last_message_at": str(r.get("last_msg_created_at") or r.get("last_message_at") or r.get("created_at") or ""),
            "unread_count": int(r.get("unread_count") or 0),
            "request_id": r.get("request_id"),
        })
    return conversations


def _get_total_unread_messages(user_id: int) -> int:
    row = execute_query_one(
        """
        SELECT COUNT(*) AS c
        FROM messages m
        JOIN conversations c ON m.conversation_id = c.conversation_id
        WHERE (c.owner_id = ? OR c.interested_user_id = ?)
          AND m.sender_id != ?
          AND NOT m.is_read
        """,
        (user_id, user_id, user_id),
    )
    return int(row.get("c") or 0) if row else 0


def _insert_message_atomic(conversation_id: int, sender_id: int, message_text: str, client_msg_id: str | None) -> tuple:
    """
    Atomically insert a validated text message and update conversation.last_message_at.
    Supports idempotency via optional client_msg_id.
    Returns (success: bool, message_row: dict | None).
    """
    norm_client_id = client_msg_id.strip()[:64] if client_msg_id and client_msg_id.strip() else None

    pool = get_pg_pool()
    if pool:
        for attempt in range(2):
            conn = None
            try:
                conn = _get_healthy_pg_conn(pool)
                cur = conn.cursor(cursor_factory=RealDictCursor)
                if norm_client_id:
                    cur.execute(
                        """
                        INSERT INTO messages (conversation_id, sender_id, message_text, is_read, client_msg_id)
                        VALUES (%s, %s, %s, FALSE, %s)
                        ON CONFLICT (conversation_id, sender_id, client_msg_id) WHERE client_msg_id IS NOT NULL
                        DO NOTHING
                        RETURNING message_id, conversation_id, sender_id, message_text, is_read, created_at
                        """,
                        (conversation_id, sender_id, message_text, norm_client_id),
                    )
                    row = cur.fetchone()
                    if not row:
                        cur.execute(
                            """
                            SELECT message_id, conversation_id, sender_id, message_text, is_read, created_at
                            FROM messages
                            WHERE conversation_id = %s AND sender_id = %s AND client_msg_id = %s
                            LIMIT 1
                            """,
                            (conversation_id, sender_id, norm_client_id),
                        )
                        row = cur.fetchone()
                        conn.commit()
                        cur.close()
                        _return_pg_conn(pool, conn)
                        return True, _serialize_row(dict(row)) if row else None
                else:
                    cur.execute(
                        """
                        INSERT INTO messages (conversation_id, sender_id, message_text, is_read)
                        VALUES (%s, %s, %s, FALSE)
                        RETURNING message_id, conversation_id, sender_id, message_text, is_read, created_at
                        """,
                        (conversation_id, sender_id, message_text),
                    )
                    row = cur.fetchone()

                cur.execute(
                    "UPDATE conversations SET last_message_at = CURRENT_TIMESTAMP WHERE conversation_id = %s",
                    (conversation_id,),
                )
                conn.commit()
                cur.close()
                _return_pg_conn(pool, conn)
                return True, _serialize_row(dict(row)) if row else None
            except Exception as e:
                is_conn_drop = isinstance(e, (psycopg2.OperationalError, psycopg2.InterfaceError)) if PSYCOPG2_AVAILABLE else False
                print(f"[Messaging PG Error] _insert_message_atomic: {e}")
                if conn:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    _return_pg_conn(pool, conn, force_close=is_conn_drop)
                if is_conn_drop and attempt == 0:
                    continue
                return False, None

    # SQLite fallback
    conn = get_sqlite_connection()
    try:
        cur = conn.cursor()
        if norm_client_id:
            cur.execute(
                """
                SELECT message_id, conversation_id, sender_id, message_text, is_read, created_at
                FROM messages
                WHERE conversation_id = ? AND sender_id = ? AND client_msg_id = ?
                LIMIT 1
                """,
                (conversation_id, sender_id, norm_client_id),
            )
            existing = cur.fetchone()
            if existing:
                conn.close()
                return True, _serialize_row(dict(existing))

        cur.execute(
            """
            INSERT INTO messages (conversation_id, sender_id, message_text, is_read, client_msg_id)
            VALUES (?, ?, ?, 0, ?)
            """,
            (conversation_id, sender_id, message_text, norm_client_id),
        )
        msg_id = cur.lastrowid
        cur.execute(
            "UPDATE conversations SET last_message_at = CURRENT_TIMESTAMP WHERE conversation_id = ?",
            (conversation_id,),
        )
        cur.execute(
            "SELECT message_id, conversation_id, sender_id, message_text, is_read, created_at FROM messages WHERE message_id = ?",
            (msg_id,),
        )
        row = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        return True, _serialize_row(dict(row)) if row else None
    except Exception as e:
        print(f"[Messaging SQLite Error] _insert_message_atomic: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        conn.close()
        return False, None


@app.route("/inbox")
@app.route("/messages", endpoint="messages_inbox_alias")
@login_required
def inbox():
    """
    Private Messaging Inbox & Split-Pane Chat Interface.
    """
    user_id = session["user_id"]
    conversations = _get_user_conversations(user_id)

    requested_conv_id = request.args.get("c", type=int)
    active_conv = None
    if requested_conv_id:
        code, conv_meta = _get_conversation_for_participant(requested_conv_id, user_id)
        if code == 200 and conv_meta:
            active_conv = conv_meta
        elif code == 403:
            flash("Access denied: You can only open your own conversations.", "danger")
        else:
            flash("Requested conversation could not be found.", "warning")

    return render_template(
        "messages.html",
        conversations=conversations,
        initial_conversation=active_conv,
    )


@app.route("/messages/start/<int:item_id>", methods=["POST"])
@app.route("/api/conversations/start/<int:item_id>", methods=["POST"], endpoint="api_start_conversation")
@login_required
def start_conversation(item_id):
    """
    Create or reuse a private 1-to-1 conversation between listing owner and interested student.
    Supports both form POST redirects and JSON API responses.
    Never triggers or modifies rental/sale/swap transaction states.
    """
    is_ajax = _wants_json_response() or request.path.startswith("/api/")
    user_id = int(session["user_id"])

    user_row = execute_query_one("SELECT user_id, status FROM users WHERE user_id = ?", (user_id,))
    if not user_row or _is_user_suspended(user_row):
        session.clear()
        msg = "Your account is unavailable or suspended."
        if is_ajax:
            return jsonify({"success": False, "message": msg}), 403
        flash(msg, "danger")
        return redirect(url_for("login"))

    item = execute_query_one(
        "SELECT item_id, user_id, title, transaction_type FROM items WHERE item_id = ?",
        (item_id,),
    )
    if not item:
        msg = "Listing could not be found."
        if is_ajax:
            return jsonify({"success": False, "message": msg}), 404
        flash(msg, "warning")
        return redirect(url_for("marketplace"))

    owner_id = int(item["user_id"])
    target_peer_raw = request.form.get("peer_id") or (request.get_json(silent=True) or {}).get("peer_id")
    interested_user_id = user_id

    if user_id == owner_id:
        # Owner opening chat from an existing request card on Requests page
        if target_peer_raw and str(target_peer_raw).isdigit():
            peer_id = int(target_peer_raw)
            existing_req = execute_query_one(
                """
                SELECT request_id FROM rental_requests
                WHERE item_id = ? AND owner_id = ? AND renter_id = ?
                ORDER BY request_id DESC LIMIT 1
                """,
                (item_id, owner_id, peer_id),
            )
            existing_conv = execute_query_one(
                """
                SELECT conversation_id FROM conversations
                WHERE listing_id = ? AND owner_id = ? AND interested_user_id = ?
                LIMIT 1
                """,
                (item_id, owner_id, peer_id),
            )
            if not existing_req and not existing_conv:
                msg = "No matching student conversation or request exists for this listing."
                if is_ajax:
                    return jsonify({"success": False, "message": msg}), 403
                flash(msg, "warning")
                return redirect(url_for("item_details", item_id=item_id))
            interested_user_id = peer_id
        else:
            msg = "You cannot start a buyer message on your own listing."
            if is_ajax:
                return jsonify({"success": False, "message": msg}), 400
            flash(msg, "info")
            return redirect(url_for("item_details", item_id=item_id))

    linked_req = execute_query_one(
        """
        SELECT request_id FROM rental_requests
        WHERE item_id = ? AND owner_id = ? AND renter_id = ?
        ORDER BY request_id DESC LIMIT 1
        """,
        (item_id, owner_id, interested_user_id),
    )
    req_id = int(linked_req["request_id"]) if linked_req else None

    conv = _get_or_create_conversation(item_id, owner_id, interested_user_id, request_id=req_id)
    if not conv:
        msg = "Could not open conversation right now. Please try again."
        if is_ajax:
            return jsonify({"success": False, "message": msg}), 500
        flash(msg, "danger")
        return redirect(url_for("item_details", item_id=item_id))

    conv_id = int(conv["conversation_id"])
    target_url = url_for("inbox", c=conv_id)
    if is_ajax:
        return jsonify({
            "success": True,
            "conversation_id": conv_id,
            "redirect_url": target_url,
        }), 200
    return redirect(target_url)


@app.route("/api/conversations", methods=["GET"])
@login_required
def api_list_conversations():
    """Return all private conversations for the authenticated participant."""
    user_id = int(session["user_id"])
    conversations = _get_user_conversations(user_id)
    total_unread = sum(c["unread_count"] for c in conversations)
    return jsonify({
        "success": True,
        "conversations": conversations,
        "total_unread": total_unread,
    }), 200


@app.route("/api/conversations/<int:conversation_id>/messages", methods=["GET"])
@login_required
def api_get_conversation_messages(conversation_id):
    """
    Fetch messages for a conversation with strict participant authorization.
    Supports incremental polling via ?after_id=<last_message_id>.
    """
    user_id = int(session["user_id"])
    code, conv_meta = _get_conversation_for_participant(conversation_id, user_id)
    if code == 404:
        return jsonify({"success": False, "message": "Conversation not found."}), 404
    if code == 403 or not conv_meta:
        return jsonify({"success": False, "message": "Access denied."}), 403

    after_id = request.args.get("after_id", default=0, type=int) or 0
    if after_id < 0:
        after_id = 0

    # Mark incoming messages from the other user as read
    execute_query_commit(
        """
        UPDATE messages
        SET is_read = ?
        WHERE conversation_id = ? AND sender_id != ? AND NOT is_read
        """,
        (True, conversation_id, user_id),
    )

    rows = execute_query_all(
        """
        SELECT message_id, conversation_id, sender_id, message_text, is_read, created_at
        FROM messages
        WHERE conversation_id = ? AND message_id > ?
        ORDER BY message_id ASC
        LIMIT 100
        """,
        (conversation_id, after_id),
    )

    messages_out = [
        {
            "message_id": int(m["message_id"]),
            "conversation_id": int(m["conversation_id"]),
            "is_mine": (int(m["sender_id"]) == user_id),
            "message_text": m["message_text"],
            "is_read": bool(m.get("is_read")),
            "created_at": str(m.get("created_at") or ""),
        }
        for m in rows
    ]

    # Return read receipts for messages sent by the current user so 'Read' updates quietly
    read_rows = execute_query_all(
        """
        SELECT message_id
        FROM messages
        WHERE conversation_id = ? AND sender_id = ? AND is_read = ?
        ORDER BY message_id DESC
        LIMIT 50
        """,
        (conversation_id, user_id, True),
    )
    read_own_ids = [int(r["message_id"]) for r in read_rows]

    return jsonify({
        "success": True,
        "conversation": conv_meta,
        "messages": messages_out,
        "read_own_ids": read_own_ids,
        "total_unread": _get_total_unread_messages(user_id),
    }), 200


@app.route("/api/conversations/<int:conversation_id>/messages", methods=["POST"])
@login_required
def api_send_conversation_message(conversation_id):
    """
    Send a text-only private message inside a conversation.
    Strictly restricted to the two conversation participants.
    """
    user_id = int(session["user_id"])
    user_row = execute_query_one("SELECT user_id, status FROM users WHERE user_id = ?", (user_id,))
    if not user_row or _is_user_suspended(user_row):
        return jsonify({"success": False, "message": "Your account cannot send messages."}), 403

    code, conv_meta = _get_conversation_for_participant(conversation_id, user_id)
    if code == 404:
        return jsonify({"success": False, "message": "Conversation not found."}), 404
    if code == 403 or not conv_meta:
        return jsonify({"success": False, "message": "You are not a participant in this conversation."}), 403

    payload = request.get_json(silent=True) or {}
    raw_text = payload.get("message_text") if "message_text" in payload else request.form.get("message_text", "")
    if not isinstance(raw_text, str):
        return jsonify({"success": False, "message": "Invalid message format."}), 400

    cleaned_text = raw_text.strip()
    if not cleaned_text:
        return jsonify({"success": False, "message": "Please enter a message before sending."}), 400
    if len(cleaned_text) > MAX_CHAT_MESSAGE_LENGTH:
        return jsonify({
            "success": False,
            "message": f"Message is too long ({len(cleaned_text)}/{MAX_CHAT_MESSAGE_LENGTH} characters max)."
        }), 400

    client_msg_id = payload.get("client_msg_id") or request.form.get("client_msg_id")
    ok, saved = _insert_message_atomic(conversation_id, user_id, cleaned_text, client_msg_id)
    if not ok or not saved:
        return jsonify({"success": False, "message": "Could not send message. Please try again."}), 500

    return jsonify({
        "success": True,
        "message": {
            "message_id": int(saved["message_id"]),
            "conversation_id": int(saved["conversation_id"]),
            "is_mine": True,
            "message_text": saved["message_text"],
            "is_read": bool(saved.get("is_read")),
            "created_at": str(saved.get("created_at") or ""),
        },
        "total_unread": _get_total_unread_messages(user_id),
    }), 201


@app.route("/api/messages/unread-count", methods=["GET"])
@login_required
def api_unread_messages_count():
    """Return total unread private messages for the current user."""
    user_id = int(session["user_id"])
    return jsonify({
        "success": True,
        "unread_count": _get_total_unread_messages(user_id),
    }), 200


# ==============================================================================
# ERROR HANDLERS
# ==============================================================================

@app.errorhandler(404)
def page_not_found(e):
    if _wants_json_response():
        return jsonify({"success": False, "message": "Requested endpoint could not be found."}), 404
    return render_template("base.html", error_title="Page Not Found", error_msg="The page you requested could not be located."), 404


@app.errorhandler(413)
def request_entity_too_large(e):
    msg = "Selected photos exceed the 30MB total upload limit. Please choose fewer or smaller images and try again."
    if _wants_json_response():
        return jsonify({"success": False, "message": msg}), 413
    flash(msg, "danger")
    return render_template("base.html", error_title="Upload Too Large", error_msg=msg), 413


@app.errorhandler(500)
def internal_server_error(e):
    msg = "An unexpected server error occurred. Please try again."
    if _wants_json_response():
        return jsonify({"success": False, "message": msg}), 500
    return render_template("base.html", error_title="System Error", error_msg=msg), 500


@app.route("/health")
def health_check():
    return jsonify({"status": "ok"}), 200


@app.before_request
def _ensure_db_ready():
    global _db_init_started
    if request.path == "/health" or request.path.startswith("/static/"):
        return
    if not _db_init_started:
        with _db_init_lock:
            if not _db_init_started:
                _db_init_started = True
                try:
                    init_database()
                except Exception as exc:
                    print(f"[Startup Error] Lazy DB init failed: {type(exc).__name__}")
                finally:
                    _db_init_event.set()
    elif not _db_init_event.is_set():
        _db_init_event.wait(timeout=8.0)


# ==============================================================================
# MAIN EXECUTION
# ==============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rent It Bro Marketplace Application")
    parser.add_argument("--host", default="0.0.0.0", help="Host interface to bind to")
    parser.add_argument("--port", type=int, default=3000, help="Port to listen on")
    args, _ = parser.parse_known_args()

    # Run database initialization in background so port 3000 binds immediately
    _db_init_started = True

    def _bg_init_database():
        try:
            init_database()
        except Exception as exc:
            print(f"[Startup Error] Database initialization failed: {exc}")
        finally:
            _db_init_event.set()

    threading.Thread(target=_bg_init_database, daemon=True).start()

    print(f" * Rent2Swap running on http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False, threaded=True, use_reloader=False)
