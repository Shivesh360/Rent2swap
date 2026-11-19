import os
import re
import secrets
import urllib.parse
from dotenv import load_dotenv

# Load environment variables from the .env file if present (do not hardcode secrets here)
load_dotenv(override=True)


def _is_production_env() -> bool:
    return bool(
        os.getenv("RENDER")
        or os.getenv("FLASK_ENV", "").strip().lower() == "production"
        or os.getenv("APP_ENV", "").strip().lower() == "production"
    )


def _sanitize_db_config():
    """
    Resolve database parameters strictly from environment variables.
    URL-encodes username/password safely when constructing DATABASE_URL.
    """
    supabase_project_id = os.getenv("SUPABASE_PROJECT_ID", "").strip()
    if supabase_project_id.lower() in ("hidden", "none"):
        supabase_project_id = ""

    raw_supabase_url = os.getenv("SUPABASE_URL", "").strip()
    if not supabase_project_id and raw_supabase_url:
        m_url = re.search(r"https?://([a-zA-Z0-9_-]+)\.supabase\.co", raw_supabase_url)
        if m_url:
            supabase_project_id = m_url.group(1)

    raw_host = os.getenv("DB_HOST", "").strip()
    raw_user = os.getenv("DB_USER", "").strip()
    raw_password = os.getenv("DB_PASSWORD", "").strip()
    raw_name = os.getenv("DB_NAME", "").strip()
    raw_port = os.getenv("DB_PORT", "").strip()
    raw_url = os.getenv("DATABASE_URL", "").strip()

    # Extract project id from supabase.co host if SUPABASE_PROJECT_ID was omitted
    for src in (raw_host, raw_url):
        if not supabase_project_id and "supabase.co" in src:
            m = re.search(r"db\.([a-zA-Z0-9_-]+)\.supabase\.co", src)
            if m:
                supabase_project_id = m.group(1)
                break

    if raw_host and raw_host.lower() not in ("hidden", "none"):
        host = raw_host
    elif supabase_project_id:
        host = f"db.{supabase_project_id}.supabase.co"
    else:
        host = ""

    user = "" if raw_user.lower() in ("hidden", "none") else raw_user
    password = "" if raw_password.lower() in ("hidden", "none") else raw_password
    name = "postgres" if raw_name.lower() in ("", "hidden", "none") else raw_name

    try:
        port = int(raw_port) if raw_port and raw_port.isdigit() else 5432
    except Exception:
        port = 5432

    database_url = raw_url
    if not database_url and host and user and password:
        enc_user = urllib.parse.quote_plus(user)
        enc_pass = urllib.parse.quote_plus(password)
        database_url = f"postgresql://{enc_user}:{enc_pass}@{host}:{port}/{name}"
    elif database_url and user and password and host:
        # Ensure DATABASE_URL has properly URL-encoded credentials
        enc_user = urllib.parse.quote_plus(user)
        enc_pass = urllib.parse.quote_plus(password)
        database_url = f"postgresql://{enc_user}:{enc_pass}@{host}:{port}/{name}"

    return host, port, user, password, name, supabase_project_id, database_url


_host, _port, _user, _password, _name, _project_id, _database_url = _sanitize_db_config()

_IS_PROD = _is_production_env()
_RAW_SECRET_KEY = os.getenv("SECRET_KEY", "").strip()
_PLACEHOLDER_SECRETS = {
    "",
    "rent-it-bro-commercial-marketplace-secret-key",
    "rent_it_bro_commercial_marketplace_secret_key_change_in_production",
    "rent_it_bro_super_secret_key_change_in_production",
    "change-me",
    "your-secret-key",
}

if _IS_PROD and _RAW_SECRET_KEY.lower() in _PLACEHOLDER_SECRETS:
    raise RuntimeError(
        "CRITICAL SECURITY ERROR: A strong SECRET_KEY environment variable is required in production."
    )

if _RAW_SECRET_KEY and _RAW_SECRET_KEY.lower() not in _PLACEHOLDER_SECRETS:
    _RESOLVED_SECRET_KEY = _RAW_SECRET_KEY
else:
    print("[Security Warning] SECRET_KEY env var missing or placeholder; using ephemeral per-process dev key.")
    _RESOLVED_SECRET_KEY = secrets.token_hex(32)

_RUNTIME_CSP = os.getenv("CSP_HEADER_VALUE", "").strip()
_APP_URL = os.getenv("APP_URL", "").strip()
_HAS_BARE_WILDCARD_CSP = bool(re.search(r"frame-ancestors[^;]*\s\*(?:\s|;|$)", _RUNTIME_CSP))
_IN_AISTUDIO_PREVIEW = (
    (not _IS_PROD)
    and bool(os.getenv("K_SERVICE"))
    and ("frame-ancestors" in _RUNTIME_CSP)
    and (not _HAS_BARE_WILDCARD_CSP)
)
_EXPLICIT_ALLOW_IFRAME = os.getenv("ALLOW_IFRAME_EMBED", "").strip().lower()
if _EXPLICIT_ALLOW_IFRAME in ("true", "1", "yes"):
    _ALLOW_IFRAME = True
    _TRUSTED_FRAME_CSP = _RUNTIME_CSP if (_IN_AISTUDIO_PREVIEW and _RUNTIME_CSP) else ""
elif _EXPLICIT_ALLOW_IFRAME in ("false", "0", "no"):
    _ALLOW_IFRAME = False
    _TRUSTED_FRAME_CSP = ""
elif _IN_AISTUDIO_PREVIEW:
    _ALLOW_IFRAME = True
    _TRUSTED_FRAME_CSP = _RUNTIME_CSP
else:
    _ALLOW_IFRAME = False
    _TRUSTED_FRAME_CSP = ""

_RAW_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "").strip()
if _RAW_SAMESITE:
    _SESSION_SAMESITE = _RAW_SAMESITE
elif _IN_AISTUDIO_PREVIEW and _APP_URL.startswith("https://"):
    _SESSION_SAMESITE = "None"
else:
    _SESSION_SAMESITE = "Lax"

_RAW_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "").strip().lower()
if _RAW_COOKIE_SECURE:
    _SESSION_SECURE = _RAW_COOKIE_SECURE in ("true", "1", "yes")
elif _IS_PROD or (_IN_AISTUDIO_PREVIEW and _APP_URL.startswith("https://")):
    _SESSION_SECURE = True
else:
    _SESSION_SECURE = False


class Config:
    """Application configuration loaded exclusively from environment variables."""

    IS_PRODUCTION = _IS_PROD
    SECRET_KEY = _RESOLVED_SECRET_KEY

    # Supabase PostgreSQL Database Configuration
    USE_POSTGRES = os.getenv("USE_POSTGRES", "true").strip().lower() in ("true", "1", "yes")
    SUPABASE_PROJECT_ID = _project_id
    SUPABASE_URL = (
        os.getenv("SUPABASE_URL", "").strip()
        or (f"https://{_project_id}.supabase.co" if _project_id else "")
    )
    SUPABASE_API_KEY = (
        os.getenv("SUPABASE_API_KEY", "").strip()
        or os.getenv("SUPABASE_ANON_KEY", "").strip()
    )
    SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()

    DB_HOST = _host
    DB_PORT = _port
    DB_USER = _user
    DB_PASSWORD = _password
    DB_NAME = _name
    DATABASE_URL = _database_url

    if IS_PRODUCTION and USE_POSTGRES and not (DB_PASSWORD or DATABASE_URL):
        raise RuntimeError(
            "CRITICAL SECURITY ERROR: PostgreSQL credentials (DB_PASSWORD / DATABASE_URL) are required in production."
        )

    # SQLite Database Configuration (fallback if USE_POSTGRES is disabled)
    DATABASE_PATH = os.getenv(
        "DATABASE_PATH",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "database", "student_exchange.db"),
    )

    # Security & Deployment Settings
    ALLOW_IFRAME_EMBED = _ALLOW_IFRAME
    TRUSTED_FRAME_CSP = _TRUSTED_FRAME_CSP
    SESSION_COOKIE_SAMESITE = _SESSION_SAMESITE
    SESSION_COOKIE_SECURE = _SESSION_SECURE
    ENABLE_DEMO_SEED = os.getenv("ENABLE_DEMO_SEED", "false").strip().lower() in ("true", "1", "yes")
    CORS_ALLOWED_ORIGINS = [
        origin.strip() for origin in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if origin.strip()
    ]

    # Uploads configuration
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
    MAX_CONTENT_LENGTH = 30 * 1024 * 1024  # 30 MB max total payload per request
    MAX_IMAGE_FILE_SIZE = 10 * 1024 * 1024  # 10 MB max per image
    MAX_VERIFICATION_FILE_SIZE = 10 * 1024 * 1024  # 10 MB max per verification document
    MAX_LISTING_IMAGES = 8
    ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
