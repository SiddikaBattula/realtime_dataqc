"""
Login by email + password from data/login.json.

Each person has a row: role, base_region and password. The role and region are
fixed by the file, never chosen by the person logging in. New people are added
from the dashboard, and only by someone signed in as a Base_head.
"""

import hashlib
import hmac
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from config import Config
from logger import get_logger

log = get_logger("auth")

SESSION_SECRET = os.getenv("SESSION_SECRET", "")
HTTPS_ONLY = os.getenv("SESSION_HTTPS_ONLY", "0") == "1"
ALLOWED_DOMAIN = os.getenv("AUTH_EMAIL_DOMAIN", "ofiindia.com").lower()
LOGIN_FILE = Path(Config.DATA_DIR) / "login.json"

# ---------------------------------------------------------------------------
# Role -> permissions. Keys are lower-case; matching ignores case.
# ---------------------------------------------------------------------------

_HEAD = {"view", "view_logs", "edit_rules", "add_well", "stop_well",
         "display_names", "email_settings","add_user"}

ROLE_PERMISSIONS = {
    "rtoc_team_head": set(_HEAD),
    "rtoc_team_member": {"view", "view_logs", "edit_rules"},
    "base_coordinator": {"view", "view_logs"},
    # add_user is Base_head's alone - nobody else can create a login.
    "base_head": {"view", "view_logs", "add_user"},
}

# Older spellings still in use. Same permissions as the role they point at.
ALIASES = {"rtoc_header": "rtoc_team_head", "base_codinator": "base_coordinator"}

# Roles offered in the "add new person" form.
CREATABLE_ROLES = ["RTOC_Team_Head", "RTOC_Team_Member", "Base_coordinator", "Base_head"]

def role_key(role) -> str:
    key = str(role).strip().lower()
    return ALIASES.get(key, key)


def required_permission(method: str, path: str) -> str:
    if method in ("GET", "HEAD"):
        if path.startswith("/alerts/all/"):
            return "view_logs"
        if path.startswith("/email/"):
            return "email_settings"
        return "view"

    if path == "/wells" and method == "POST":
        return "add_well"
    if path.startswith("/wells/") and method == "DELETE":
        return "stop_well"
    if path.startswith("/wells/") and method == "PUT":
        return "edit_rules"
    if path == "/rules/display_name" and method == "PUT":
        return "display_names"
    if path.startswith("/rules/") and method == "PUT":
        return "edit_rules"
    if path.startswith("/email/"):
        return "email_settings"

    return "forbidden"


# ---------------------------------------------------------------------------
# login.json - re-read when the file changes
# ---------------------------------------------------------------------------

_cache = {"stamp": None, "users": {}}
_write_lock = threading.Lock()


def _email(value) -> str:
    return str(value).strip().lower()


def load_logins() -> dict:
    """{ email: {email, label, key, region, password} }"""
    try:
        stamp = LOGIN_FILE.stat().st_mtime_ns
    except OSError:
        log.error("%s not found - nobody can sign in", LOGIN_FILE)
        return {}

    if stamp == _cache["stamp"]:
        return _cache["users"]

    _cache["stamp"] = stamp

    try:
        doc = json.loads(LOGIN_FILE.read_text(encoding="utf-8"))
        users = {}

        for raw_email, entry in doc.items():
            email = _email(raw_email)

            if not isinstance(entry, dict):
                continue

            key = role_key(entry.get("role", ""))
            password = entry.get("Password")

            if key not in ROLE_PERMISSIONS:
                log.warning("login.json: %s has unknown role %r - skipped",
                            email, entry.get("role"))
                continue

            if not isinstance(password, str) or not password:
                log.warning("login.json: %s has no Password - skipped", email)
                continue

            users[email] = {
                "email": email,
                "label": str(entry.get("role")),
                "key": key,
                "region": str(entry.get("base_region", "")).strip(),
                "password": password,
            }

        _cache["users"] = users
        log.info("login.json loaded: %d user(s)", len(users))

    except (OSError, ValueError, AttributeError) as exc:
        log.error("login.json could not be read (%s) - keeping the previous list", exc)

    return _cache["users"]


def _add_user(email, role, region, password):
    """Append one person to login.json, written safely. Returns an error or None."""
    with _write_lock:
        try:
            doc = json.loads(LOGIN_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return "login.json could not be read, so nobody was added."

        if any(_email(existing) == email for existing in doc):
            return f"{email} already exists."

        # Stored exactly as typed, at the owner's request, so login.json shows
        # each person's password. check_password still accepts a pbkdf2$ hash.
        entry = {"role": role, "Password": password}

        if region:
            entry["base_region"] = region

        doc[email] = entry

        temporary = LOGIN_FILE.with_name(LOGIN_FILE.name + ".writing")

        with open(temporary, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=4, ensure_ascii=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())

        os.replace(temporary, LOGIN_FILE)

    return None


# ---------------------------------------------------------------------------
# Passwords: plain text works; "pbkdf2$iterations$salt$hash" is also accepted.
# New people added from the dashboard are stored as plain text, as typed.
# Make one by hand with:  python auth.py hash "the password"
# ---------------------------------------------------------------------------

def make_hash(password: str, iterations: int = 200_000) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2${iterations}${salt.hex()}${digest.hex()}"


def check_password(stored: str, supplied: str) -> bool:
    if stored.startswith("pbkdf2$"):
        try:
            _, iterations, salt, expected = stored.split("$")
            digest = hashlib.pbkdf2_hmac(
                "sha256", supplied.encode(), bytes.fromhex(salt), int(iterations)
            )
            return hmac.compare_digest(digest.hex(), expected)
        except ValueError:
            return False

    return hmac.compare_digest(stored.encode(), supplied.encode())


def _fingerprint(stored: str) -> str:
    """Changes when this person's password changes, which signs them out."""
    return hashlib.sha256(stored.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Throttle: 5 wrong attempts from one address locks it out for a minute
# ---------------------------------------------------------------------------

_failures = {}
MAX_FAILS, LOCK_SECONDS = 5, 60


def _locked(ip) -> bool:
    count, since = _failures.get(ip, (0, 0))

    if count >= MAX_FAILS and time.time() - since < LOCK_SECONDS:
        return True

    if count >= MAX_FAILS:
        _failures.pop(ip, None)

    return False


def _fail(ip):
    count, _ = _failures.get(ip, (0, 0))
    _failures[ip] = (count + 1, time.time())


# ---------------------------------------------------------------------------
# Who is signed in
# ---------------------------------------------------------------------------

def current_user(request: Request):
    email = request.session.get("email")

    if not email:
        return None

    user = load_logins().get(email)

    if user is None or request.session.get("fp") != _fingerprint(user["password"]):
        return None     # removed, or their password was changed

    return user


router = APIRouter(prefix="/auth", include_in_schema=False)


async def _json(request: Request) -> dict:
    try:
        body = await request.json()
        return body if isinstance(body, dict) else {}
    except ValueError:
        return {}


def _ip(request: Request) -> str:
    return request.client.host if request.client else "?"


@router.post("/login")
async def login(request: Request):
    ip = _ip(request)

    if _locked(ip):
        return JSONResponse(
            {"detail": f"Too many attempts. Wait {LOCK_SECONDS} seconds."}, status_code=429
        )

    body = await _json(request)
    email = _email(body.get("email", ""))
    user = load_logins().get(email)

    if user is None or not check_password(user["password"], str(body.get("password", ""))):
        _fail(ip)
        log.warning("Failed login for %s from %s", email or "(none)", ip)
        return JSONResponse({"detail": "Wrong email or password."}, status_code=401)

    _failures.pop(ip, None)
    request.session.clear()
    request.session["email"] = email
    request.session["fp"] = _fingerprint(user["password"])

    log.info("Signed in: %s (%s, %s)", email, user["label"], user["region"] or "no region")
    return {"status": "ok"}


def _may_add_people(request: Request):
    """The signed-in user if they may add people, otherwise a response refusing it."""
    user = current_user(request)

    if user is None:
        return JSONResponse({"detail": "Sign in required"}, status_code=401)

    if "add_user" not in ROLE_PERMISSIONS[user["key"]]:
        log.warning("Denied add_user for %s", user["email"])
        return JSONResponse(
            {"detail": "Only a Base head can add new people."}, status_code=403
        )

    return user


@router.get("/roles")
async def roles(request: Request):
    """Roles offered when adding a person."""
    user = _may_add_people(request)

    if isinstance(user, JSONResponse):
        return user

    return {"roles": CREATABLE_ROLES}


@router.post("/register")
async def register(request: Request):
    """Add a person. Only a signed-in Base_head may."""
    approver = _may_add_people(request)

    if isinstance(approver, JSONResponse):
        return approver

    body = await _json(request)

    email = _email(body.get("email", ""))
    role = str(body.get("role", "")).strip()
    region = str(body.get("base_region", "")).strip().lower()
    password = str(body.get("password", ""))

    if not re.fullmatch(r"[^@\s]+@" + re.escape(ALLOWED_DOMAIN), email):
        return JSONResponse({"detail": f"Email must end with @{ALLOWED_DOMAIN}."}, status_code=400)

    if role not in CREATABLE_ROLES:
        return JSONResponse({"detail": "Choose a role from the list."}, status_code=400)

    if len(password) < 8:
        return JSONResponse({"detail": "Password must be at least 8 characters."}, status_code=400)

    problem = _add_user(email, role, region, password)

    if problem:
        return JSONResponse({"detail": problem}, status_code=400)

    log.info("%s added %s as %s (%s)", approver["email"], email, role, region or "no region")
    return {"status": "added", "email": email}


@router.get("/me")
async def me(request: Request):
    user = current_user(request)

    if user is None:
        return JSONResponse({"detail": "Sign in required"}, status_code=401)

    return {
        "email": user["email"],
        "role": user["label"],
        "base_region": user["region"],
        "permissions": sorted(ROLE_PERMISSIONS[user["key"]]),
    }


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login.html")


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

# Everything the sign-in page loads, since nobody is signed in yet to fetch it.
PUBLIC_PATHS = {
    "/health", "/login.html",
    "/css/style.css", "/css/login.css",
    "/js/login.js", "/js/secret-input.js",
    "/images/logo-default-223x59.png",
}


async def auth_gate(request: Request, call_next):
    path = request.url.path

    if request.method == "OPTIONS" or path.startswith("/auth/") or path in PUBLIC_PATHS:
        return await call_next(request)

    user = current_user(request)

    if user is None:
        if "text/html" in request.headers.get("accept", ""):
            return RedirectResponse("/login.html")

        return JSONResponse({"detail": "Sign in required"}, status_code=401)

    need = required_permission(request.method, path)

    if need not in ROLE_PERMISSIONS[user["key"]]:
        log.warning("Denied %s %s %s for %s", need, request.method, path, user["email"])
        return JSONResponse(
            {"detail": f"Your role ({user['label']}) is not allowed to do this."},
            status_code=403,
        )

    return await call_next(request)


def install(app):
    """Call once, straight after `app = FastAPI(...)`."""
    if not SESSION_SECRET:
        raise RuntimeError("Set SESSION_SECRET in .env")

    app.include_router(router)
    app.middleware("http")(auth_gate)
    app.add_middleware(
        SessionMiddleware,
        secret_key=SESSION_SECRET,
        same_site="lax",
        https_only=HTTPS_ONLY,
        max_age=8 * 3600,
    )


if __name__ == "__main__" and len(sys.argv) == 3 and sys.argv[1] == "hash":
    print(make_hash(sys.argv[2]))