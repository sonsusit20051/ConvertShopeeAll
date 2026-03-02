import hashlib
import hmac
import os
import re
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode, urlparse

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field


load_dotenv()


def as_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


APP_TITLE = "Shopee Link Converter"
AFFILIATE_ID = os.getenv("AFFILIATE_ID", "17322940169").strip()
SUB_ID = os.getenv("SUB_ID", "addlivetag----").strip()
ADMIN_KEY = os.getenv("ADMIN_KEY", "240905").strip()
DB_PATH = os.getenv("DB_PATH", "data/app.db").strip()
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "15"))

SESSION_COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "admin_session").strip()
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "86400"))
SESSION_SECRET = os.getenv("SESSION_SECRET", f"local-secret-{ADMIN_KEY}").strip()
COOKIE_SECURE = as_bool(os.getenv("COOKIE_SECURE"), default=False)

RATE_LIMIT_MAX_REQUESTS = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "10"))
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))

PRODUCT_PATTERN = re.compile(r"/product/(\d+)/(\d+)")
SLUG_PATTERN = re.compile(r"-i\.(\d+)\.(\d+)")

_rate_limit_lock = threading.Lock()
_rate_limit_state: dict[str, tuple[int, int]] = {}


app = FastAPI(title=APP_TITLE)
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")


class ConvertRequest(BaseModel):
    input_url: str = Field(min_length=8, max_length=4096)


class AdminLoginRequest(BaseModel):
    key: str = Field(min_length=1, max_length=200)


def now_local_string() -> str:
    return datetime.now().isoformat(timespec="seconds")


def ensure_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                input_url TEXT NOT NULL,
                resolved_url TEXT,
                origin_link TEXT,
                affiliate_link TEXT,
                success INTEGER NOT NULL,
                error_message TEXT,
                client_ip TEXT,
                user_agent TEXT
            )
            """
        )
        conn.commit()


@app.on_event("startup")
def on_startup() -> None:
    ensure_db()


def get_client_ip(request: Request) -> Optional[str]:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


def check_rate_limit(client_ip: Optional[str]) -> tuple[bool, int]:
    if not client_ip:
        return True, 0
    if RATE_LIMIT_MAX_REQUESTS <= 0 or RATE_LIMIT_WINDOW_SECONDS <= 0:
        return True, 0

    now = int(time.time())
    window_id = now // RATE_LIMIT_WINDOW_SECONDS
    retry_after = RATE_LIMIT_WINDOW_SECONDS - (now % RATE_LIMIT_WINDOW_SECONDS)

    with _rate_limit_lock:
        current_window, current_count = _rate_limit_state.get(client_ip, (-1, 0))
        if current_window != window_id:
            _rate_limit_state[client_ip] = (window_id, 1)
            if len(_rate_limit_state) > 20000:
                for ip, state in list(_rate_limit_state.items()):
                    if state[0] < window_id - 1:
                        _rate_limit_state.pop(ip, None)
            return True, 0

        if current_count >= RATE_LIMIT_MAX_REQUESTS:
            return False, max(retry_after, 1)

        _rate_limit_state[client_ip] = (window_id, current_count + 1)
        return True, 0


def normalize_input_url(input_url: str) -> str:
    clean = input_url.strip()
    if not clean:
        raise ValueError("Link trống.")
    if not clean.startswith(("http://", "https://")):
        clean = f"https://{clean}"
    parsed = urlparse(clean)
    if not parsed.netloc:
        raise ValueError("Link không hợp lệ.")
    return clean


def resolve_final_url(url: str) -> str:
    with httpx.Client(
        follow_redirects=True,
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": "Mozilla/5.0 LinkConverter/1.0"},
    ) as client:
        response = client.get(url)
        response.raise_for_status()
        return str(response.url)


def canonical_shopee_link(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path or ""
    host = (parsed.netloc or "").lower()

    product_match = PRODUCT_PATTERN.search(path)
    if product_match:
        return f"https://shopee.vn/product/{product_match.group(1)}/{product_match.group(2)}"

    slug_match = SLUG_PATTERN.search(path)
    if slug_match:
        return f"https://shopee.vn/product/{slug_match.group(1)}/{slug_match.group(2)}"

    if "shopee." in host:
        segments = [part for part in path.split("/") if part]
        if len(segments) >= 2 and segments[-1].isdigit() and segments[-2].isdigit():
            return f"https://shopee.vn/product/{segments[-2]}/{segments[-1]}"

    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}" if parsed.netloc else url


def build_affiliate_link(origin_link: str) -> str:
    query = {"origin_link": origin_link, "affiliate_id": AFFILIATE_ID}
    if SUB_ID:
        query["sub_id"] = SUB_ID
    return f"https://s.shopee.vn/an_redir?{urlencode(query)}"


def save_conversion(
    *,
    input_url: str,
    resolved_url: Optional[str],
    origin_link: Optional[str],
    affiliate_link: Optional[str],
    success: bool,
    error_message: Optional[str],
    client_ip: Optional[str],
    user_agent: Optional[str],
) -> int:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cursor = conn.execute(
            """
            INSERT INTO conversions (
                created_at,
                input_url,
                resolved_url,
                origin_link,
                affiliate_link,
                success,
                error_message,
                client_ip,
                user_agent
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now_local_string(),
                input_url,
                resolved_url,
                origin_link,
                affiliate_link,
                1 if success else 0,
                error_message,
                client_ip,
                user_agent,
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def sign_value(value: str) -> str:
    return hmac.new(SESSION_SECRET.encode(), value.encode(), hashlib.sha256).hexdigest()


def create_admin_session_token() -> str:
    expires_at = int(time.time()) + SESSION_TTL_SECONDS
    payload = str(expires_at)
    signature = sign_value(payload)
    return f"{payload}.{signature}"


def verify_admin_session_token(token: str) -> bool:
    if "." not in token:
        return False
    payload, signature = token.rsplit(".", 1)
    expected = sign_value(payload)
    if not hmac.compare_digest(signature, expected):
        return False
    try:
        expires_at = int(payload)
    except ValueError:
        return False
    return expires_at >= int(time.time())


def is_admin_authenticated(request: Request) -> bool:
    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    return bool(token and verify_admin_session_token(token))


def require_admin_session(request: Request) -> None:
    if not is_admin_authenticated(request):
        raise HTTPException(status_code=401, detail="Bạn chưa đăng nhập admin.")


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "affiliate_id": AFFILIATE_ID,
        },
    )


@app.post("/api/convert")
def convert_link(payload: ConvertRequest, request: Request) -> dict:
    client_ip = get_client_ip(request)
    user_agent = request.headers.get("user-agent")
    normalized = ""
    resolved = None
    origin = None
    affiliate = None

    allowed, retry_after = check_rate_limit(client_ip)
    if not allowed:
        save_conversion(
            input_url=payload.input_url.strip(),
            resolved_url=None,
            origin_link=None,
            affiliate_link=None,
            success=False,
            error_message=f"rate_limited:{retry_after}s",
            client_ip=client_ip,
            user_agent=user_agent,
        )
        raise HTTPException(
            status_code=429,
            detail=f"Bạn thao tác quá nhanh. Vui lòng thử lại sau {retry_after}s.",
            headers={"Retry-After": str(retry_after)},
        )

    try:
        normalized = normalize_input_url(payload.input_url)
        resolved = resolve_final_url(normalized)
        origin = canonical_shopee_link(resolved)
        affiliate = build_affiliate_link(origin)

        conversion_id = save_conversion(
            input_url=normalized,
            resolved_url=resolved,
            origin_link=origin,
            affiliate_link=affiliate,
            success=True,
            error_message=None,
            client_ip=client_ip,
            user_agent=user_agent,
        )
        return {
            "success": True,
            "id": conversion_id,
            "input_url": normalized,
            "resolved_url": resolved,
            "origin_link": origin,
            "affiliate_link": affiliate,
        }
    except Exception as exc:  # noqa: BLE001
        error_text = str(exc)[:500]
        save_conversion(
            input_url=normalized or payload.input_url,
            resolved_url=resolved,
            origin_link=origin,
            affiliate_link=affiliate,
            success=False,
            error_message=error_text,
            client_ip=client_ip,
            user_agent=user_agent,
        )
        raise HTTPException(status_code=400, detail=f"Không convert được: {error_text}")


@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request) -> HTMLResponse:
    if is_admin_authenticated(request):
        return RedirectResponse(url="/admin", status_code=302)
    return templates.TemplateResponse("admin_login.html", {"request": request})


@app.post("/api/admin/login")
def admin_login(payload: AdminLoginRequest) -> JSONResponse:
    if payload.key.strip() != ADMIN_KEY:
        raise HTTPException(status_code=401, detail="Sai admin key.")

    token = create_admin_session_token()
    response = JSONResponse({"success": True})
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
        path="/",
    )
    return response


@app.post("/api/admin/logout")
def admin_logout() -> JSONResponse:
    response = JSONResponse({"success": True})
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return response


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request) -> HTMLResponse:
    if not is_admin_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    return templates.TemplateResponse("admin.html", {"request": request})


@app.get("/api/admin/stats")
def admin_stats(request: Request) -> dict:
    require_admin_session(request)
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        total = conn.execute("SELECT COUNT(*) AS n FROM conversions").fetchone()["n"]
        success_total = conn.execute(
            "SELECT COUNT(*) AS n FROM conversions WHERE success = 1"
        ).fetchone()["n"]
        failed_total = total - success_total
        today = conn.execute(
            "SELECT COUNT(*) AS n FROM conversions WHERE date(created_at) = date('now', 'localtime')"
        ).fetchone()["n"]
        recent = conn.execute(
            """
            SELECT id, created_at, input_url, resolved_url, affiliate_link, success, error_message
            FROM conversions
            ORDER BY id DESC
            LIMIT 100
            """
        ).fetchall()

    return {
        "success": True,
        "summary": {
            "total": total,
            "success": success_total,
            "failed": failed_total,
            "today": today,
        },
        "history": [dict(row) for row in recent],
    }
