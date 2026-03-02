import hashlib
import html
import hmac
import json
import os
import re
import secrets
import ssl
import sqlite3
import string
import threading
import time
import uuid
from contextlib import closing
from datetime import datetime
from typing import Any, Optional
from urllib.parse import parse_qsl, quote, unquote, urlparse, urlunparse
from urllib.request import Request as UrlRequest, urlopen
from urllib.error import URLError

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
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
ADMIN_KEY = os.getenv("ADMIN_KEY", "24092005").strip()
DB_PATH = os.getenv("DB_PATH", "data/app.db").strip()

# Queue/worker and YT-like convert settings.
WORKER_TOKEN = os.getenv("WORKER_TOKEN", "dev-worker-token").strip()
JOB_TTL_SEC = int(os.getenv("JOB_TTL_SEC", "300"))
JOB_PROCESS_TIMEOUT_SEC = int(os.getenv("JOB_PROCESS_TIMEOUT_SEC", "120"))
WORKER_STALE_SEC = int(os.getenv("WORKER_STALE_SEC", "60"))
SYNC_WAIT_TIMEOUT_SEC = int(os.getenv("SYNC_WAIT_TIMEOUT_SEC", "90"))
SYNC_WORKER_PRIORITY_WAIT_SEC = float(os.getenv("SYNC_WORKER_PRIORITY_WAIT_SEC", "3.2"))
SYNC_WAIT_POLL_MS = int(os.getenv("SYNC_WAIT_POLL_MS", "220"))
BASE_REDIRECT = os.getenv("BASE_REDIRECT", "https://s.shopee.vn/an_redir").strip()
FORCE_YT_MODE = as_bool(os.getenv("FORCE_YT_MODE", "0"), default=False)
AFFILIATE_ID_DEFAULT = os.getenv("AFFILIATE_ID", "17322940169").strip() or "17322940169"
SUB_ID_DEFAULT = os.getenv("SUB_ID", "cvweb").strip() or "cvweb"
AFFILIATE_ID_YT_DEFAULT = os.getenv("AFFILIATE_ID_YT", "17391540096").strip() or "17391540096"
SUB_ID_YT_DEFAULT = os.getenv("SUB_ID_YT", "YT3").strip() or "YT3"
FORCED_AFFILIATE_ID = os.getenv("FORCED_AFFILIATE_ID", "").strip()
RESOLVE_TIMEOUT_SEC = float(os.getenv("RESOLVE_TIMEOUT_SEC", "10"))
ALLOW_INSECURE_TLS_RETRY = as_bool(os.getenv("ALLOW_INSECURE_TLS_RETRY", "1"), default=True)
SHORT_CODE_LEN = int(os.getenv("SHORT_CODE_LEN", "4"))
SHORT_TTL_SEC = int(os.getenv("SHORT_TTL_SEC", "604800"))
SHORT_PUBLIC_BASE = os.getenv("SHORT_PUBLIC_BASE", "").strip()
PRODUCT_INFO_TIMEOUT_SEC = float(os.getenv("PRODUCT_INFO_TIMEOUT_SEC", "2.8"))
PRODUCT_INFO_HTML_TIMEOUT_SEC = float(os.getenv("PRODUCT_INFO_HTML_TIMEOUT_SEC", "1.8"))
PRODUCT_INFO_RESOLVE_TIMEOUT_SEC = float(os.getenv("PRODUCT_INFO_RESOLVE_TIMEOUT_SEC", "1.8"))
PRODUCT_INFO_SHOP_TIMEOUT_SEC = float(os.getenv("PRODUCT_INFO_SHOP_TIMEOUT_SEC", "1.2"))
PRODUCT_INFO_CACHE_TTL_SEC = int(os.getenv("PRODUCT_INFO_CACHE_TTL_SEC", "600"))
PRODUCT_INFO_CACHE_MAX = int(os.getenv("PRODUCT_INFO_CACHE_MAX", "800"))
PRODUCT_INFO_INCLUDE_SHOP_LOOKUP = as_bool(os.getenv("PRODUCT_INFO_INCLUDE_SHOP_LOOKUP", "0"), default=False)

PRODUCT_PATTERN = re.compile(r"/product/(\d+)/(\d+)")
SLUG_PATTERN = re.compile(r"-i\.(\d+)\.(\d+)")
SOURCE_FB = "fb"
SOURCE_YT = "yt"

# Admin session
SESSION_COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "admin_session").strip()
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "86400"))
_session_secret_env = os.getenv("SESSION_SECRET", "").strip()
if _session_secret_env and _session_secret_env.lower() != "please-change-this-secret":
    SESSION_SECRET = _session_secret_env
else:
    SESSION_SECRET = f"runtime-{secrets.token_hex(24)}"
SESSION_TOKEN_VERSION = secrets.token_hex(6)
COOKIE_SECURE = as_bool(os.getenv("COOKIE_SECURE"), default=False)

# Basic public rate-limit
RATE_LIMIT_MAX_REQUESTS = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "10"))
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))

URL_CANDIDATE_REGEX = re.compile(
    r"((?:https?://)?(?:[a-z0-9-]+\.)*(?:shopee\.[a-z.]{2,}|shope\.ee|shp\.ee)(?:/[^\s<>\"']*)?)",
    re.IGNORECASE,
)
JOBS: dict[str, dict[str, Any]] = {}
PENDING_QUEUE: list[str] = []
WORKERS: dict[str, dict[str, Any]] = {}
SHORT_LINKS: dict[str, dict[str, Any]] = {}
STORE_LOCK = threading.Lock()

_rate_limit_lock = threading.Lock()
_rate_limit_state: dict[str, tuple[int, int]] = {}
_product_info_cache_lock = threading.Lock()
_product_info_cache: dict[str, dict[str, Any]] = {}


app = FastAPI(title=APP_TITLE)
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")


class ConvertRequest(BaseModel):
    input_url: Optional[str] = Field(default=None, max_length=4096)
    input: Optional[str] = Field(default=None, max_length=4096)
    url: Optional[str] = Field(default=None, max_length=4096)
    affiliate_id: Optional[str] = Field(default=None, max_length=100)
    sub_id: Optional[str] = Field(default=None, max_length=100)
    source: Optional[str] = Field(default=None, max_length=40)


class AdminLoginRequest(BaseModel):
    key: str = Field(min_length=1, max_length=200)


class WorkerPollRequest(BaseModel):
    workerId: Optional[str] = None
    workerName: Optional[str] = None
    affiliateId: Optional[str] = None
    subId: Optional[str] = None
    workerToken: Optional[str] = None


class WorkerSubmitRequest(BaseModel):
    workerId: Optional[str] = None
    jobId: Optional[str] = None
    success: Optional[bool] = None
    message: Optional[str] = None
    affiliateLink: Optional[str] = None
    landingUrl: Optional[str] = None
    cleanLandingUrl: Optional[str] = None
    campaignRawAffiliateLink: Optional[str] = None
    campaignSource: Optional[str] = None
    campaignAffiliateId: Optional[str] = None
    campaignSubId: Optional[str] = None
    workerToken: Optional[str] = None


def now_local_string() -> str:
    return datetime.now().isoformat(timespec="seconds")


def now_ts() -> int:
    return int(time.time())


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
                user_agent TEXT,
                source TEXT NOT NULL DEFAULT 'fb'
            )
            """
        )
        cols = {
            str(row[1]).strip().lower()
            for row in conn.execute("PRAGMA table_info(conversions)").fetchall()
        }
        if "source" not in cols:
            conn.execute("ALTER TABLE conversions ADD COLUMN source TEXT NOT NULL DEFAULT 'fb'")
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


def normalize_source(raw_source: Optional[str], fallback: str = SOURCE_FB) -> str:
    value = str(raw_source or "").strip().lower()
    if value in {"fb", "facebook"}:
        return SOURCE_FB
    if value in {"yt", "youtube", "shopee"}:
        return SOURCE_YT
    return SOURCE_YT if str(fallback).strip().lower() in {"yt", "youtube", "shopee"} else SOURCE_FB


def source_defaults(source: str) -> tuple[str, str]:
    if normalize_source(source) == SOURCE_YT:
        return AFFILIATE_ID_YT_DEFAULT, SUB_ID_YT_DEFAULT
    return AFFILIATE_ID_DEFAULT, SUB_ID_DEFAULT


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
    source: str = SOURCE_FB,
) -> int:
    source_key = normalize_source(source)
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
                user_agent,
                source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                source_key,
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def sign_value(value: str) -> str:
    return hmac.new(SESSION_SECRET.encode(), value.encode(), hashlib.sha256).hexdigest()


def create_admin_session_token() -> str:
    expires_at = int(time.time()) + SESSION_TTL_SECONDS
    payload = f"{SESSION_TOKEN_VERSION}:{expires_at}"
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
        version, expires_raw = payload.split(":", 1)
        if version != SESSION_TOKEN_VERSION:
            return False
        expires_at = int(expires_raw)
    except ValueError:
        return False
    except Exception:
        return False
    return expires_at >= int(time.time())


def is_admin_authenticated(request: Request) -> bool:
    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    return bool(token and verify_admin_session_token(token))


def require_admin_session(request: Request) -> None:
    if not is_admin_authenticated(request):
        raise HTTPException(status_code=401, detail="Bạn chưa đăng nhập admin.")


# -----------------------------
# URL parsing / normalization
# -----------------------------

def trim_trailing_punctuation(text: str) -> str:
    return re.sub(r"[),.;!?\]\\s]+$", "", str(text or ""))


def ensure_protocol(text: str) -> str:
    value = str(text or "").strip()
    if value.lower().startswith(("http://", "https://")):
        return value
    return f"https://{value}"


def extract_link_candidates(raw_text: str) -> list[str]:
    if not raw_text:
        return []
    return [trim_trailing_punctuation(m.group(1)) for m in URL_CANDIDATE_REGEX.finditer(str(raw_text))]


def is_allowed_host(hostname: str) -> bool:
    host = (hostname or "").lower()
    if host == "shope.ee":
        return True
    if re.match(r"^([a-z0-9-]+\.)*shp\.ee$", host, re.IGNORECASE):
        return True
    if re.match(r"^([a-z0-9-]+\.)*shopee\.[a-z.]{2,}$", host, re.IGNORECASE):
        return True
    return False


def is_shortlink_host(hostname: str) -> bool:
    host = (hostname or "").lower()
    return host in ("shope.ee", "shp.ee") or host.endswith(".shp.ee") or host.startswith("s.shopee.")


def is_shopee_landing_host(hostname: str) -> bool:
    host = (hostname or "").lower()
    return bool(re.match(r"^([a-z0-9-]+\.)*shopee\.[a-z.]{2,}$", host, re.IGNORECASE))


def is_supported_shopee_path(parsed_url) -> bool:
    path = str(parsed_url.path or "/")
    host = str(parsed_url.hostname or "").lower()

    if is_shortlink_host(host):
        return bool(path and path != "/" and len(path) > 1)

    if not is_shopee_landing_host(host):
        return False

    patterns = [
        r"^/product/\d+/\d+/?$",
        r"^/.+-i\.\d+\.\d+/?$",
        r"^/[^/?#]+/\d+/\d+/?$",
        r"^/\d+/\d+/?$",
        r"^/shop/\d+/?$",
        r"^/[^/?#]+/?$",
        r"^/.+-cat\.\d+/?$",
        r"^/search/?$",
        r"^/m/voucher(?:-shop)?(?:/[^/?#]+)?/?$",
        r"^/m/[^/?#]+/?$",
        r"^/flash_sale/?$",
        r"^/cart/?$",
    ]
    return any(re.match(pattern, path, re.IGNORECASE) for pattern in patterns)


def normalize_input(raw_input: str) -> dict[str, Any]:
    trimmed = str(raw_input or "").strip()
    if not trimmed:
        return {"ok": False, "error": "Vui lòng dán link Shopee trước khi tạo link."}

    candidates = extract_link_candidates(trimmed)
    if len(candidates) > 1:
        return {"ok": False, "error": "Vui lòng chỉ dán 1 link Shopee mỗi lần."}

    extracted = candidates[0] if candidates else ""
    if not extracted:
        return {"ok": False, "error": "Không tìm thấy link Shopee hợp lệ trong nội dung bạn dán."}

    try:
        parsed = urlparse(ensure_protocol(extracted))
    except Exception:
        return {"ok": False, "error": "Link không đúng định dạng URL."}

    if not parsed.scheme or not parsed.netloc:
        return {"ok": False, "error": "Link không đúng định dạng URL."}

    if not is_allowed_host(parsed.hostname or ""):
        return {
            "ok": False,
            "error": "Domain không được hỗ trợ. Chỉ chấp nhận *.shopee.*, s.shopee.*, shope.ee và *.shp.ee.",
        }

    if not is_supported_shopee_path(parsed):
        return {
            "ok": False,
            "error": "Định dạng link chưa hỗ trợ. Hãy dùng link sản phẩm, shop, category, search, voucher, flash sale, cart hoặc shortlink Shopee.",
        }

    parsed = parsed._replace(fragment="")
    return {"ok": True, "url": parsed.geturl()}


def is_item_detail_path(path: str) -> bool:
    value = str(path or "")
    patterns = [
        r"^/product/\d+/\d+/?$",
        r"^/.+-i\.\d+\.\d+/?$",
        r"^/[^/?#]+/\d+/\d+/?$",
        r"^/\d+/\d+/?$",
    ]
    return any(re.match(pattern, value, re.IGNORECASE) for pattern in patterns)


def extract_product_ids(path: str) -> tuple[Optional[str], Optional[str]]:
    full_path = str(path or "")

    product_match = PRODUCT_PATTERN.search(full_path)
    if product_match:
        return product_match.group(1), product_match.group(2)

    slug_match = SLUG_PATTERN.search(full_path)
    if slug_match:
        return slug_match.group(1), slug_match.group(2)

    parts = [p for p in full_path.split("/") if p]
    if len(parts) >= 2 and parts[-1].isdigit() and parts[-2].isdigit():
        return parts[-2], parts[-1]

    return None, None


def decode_nested_url(raw_url: str, max_rounds: int = 3) -> str:
    value = str(raw_url or "").strip()
    if not value:
        return ""

    current = value
    for _ in range(max(1, int(max_rounds or 1))):
        parsed = urlparse(current)
        if parsed.scheme in ("http", "https") and parsed.netloc:
            return current
        decoded = unquote(current)
        if decoded == current:
            break
        current = str(decoded or "").strip()

    return current


def sanitize_embedded_affiliate_noise(raw_url: str) -> str:
    text = str(raw_url or "").strip()
    if not text:
        return ""
    if "?" in text:
        return text
    for marker in ("&affiliate_id=", "&sub_id=", "&smtt=", "&deep_and_deferred=1"):
        idx = text.find(marker)
        if idx > 0:
            return text[:idx]
    return text


def normalize_origin_link(raw_url: str) -> Optional[str]:
    if not raw_url:
        return None

    parsed = urlparse(str(raw_url))
    if not parsed.scheme or not parsed.netloc:
        return None

    if not is_allowed_host(parsed.hostname or ""):
        return None

    if not is_shopee_landing_host(parsed.hostname or ""):
        return None

    normalized = parsed._replace(scheme="https", fragment="")
    if is_item_detail_path(parsed.path):
        normalized = normalized._replace(query="")
    return urlunparse(normalized)


def canonical_product_origin(raw_url: str) -> Optional[str]:
    normalized = normalize_origin_link(raw_url)
    if not normalized:
        return None

    parsed = urlparse(normalized)
    shop_id, item_id = extract_product_ids(parsed.path)
    if not shop_id or not item_id:
        return normalized

    canonical = parsed._replace(
        scheme="https",
        path=f"/product/{shop_id}/{item_id}",
        query="",
        fragment="",
    )
    return urlunparse(canonical)


def extract_affiliate_parts(affiliate_link: str) -> tuple[str, str, str]:
    parsed = urlparse(str(affiliate_link or ""))
    if not parsed.query:
        return "", "", ""

    affiliate_id = ""
    sub_id = ""
    origin_link = ""
    for k, v in parse_qsl(parsed.query, keep_blank_values=True):
        if k == "affiliate_id":
            affiliate_id = v
        elif k == "sub_id":
            sub_id = v
        elif k == "origin_link":
            origin_link = v
    return affiliate_id, sub_id, origin_link


def parse_affiliate_meta(affiliate_link: str) -> tuple[str, str]:
    affiliate_id, sub_id, _ = extract_affiliate_parts(affiliate_link)
    return affiliate_id, sub_id


def build_compact_affiliate_link(origin_link_url: str, affiliate_id: str, sub_id: str) -> str:
    parsed_base = urlparse(str(BASE_REDIRECT or ""))
    if not parsed_base.scheme or not parsed_base.netloc:
        return ""

    origin = str(origin_link_url or "").strip()
    aff = str(affiliate_id or "").strip()
    sid = str(sub_id or "").strip()
    if not origin or not aff or not sid:
        return ""

    return (
        f"{parsed_base.scheme}://{parsed_base.netloc}{parsed_base.path}"
        f"?origin_link={quote(origin, safe='')}"
        f"&affiliate_id={quote(aff, safe='')}"
        f"&sub_id={quote(sid, safe='')}"
    )


def compact_from_affiliate(raw_affiliate_link: str, fallback_origin_link: str) -> str:
    raw = str(raw_affiliate_link or "").strip()
    if not raw:
        return ""

    aff_id, sub_id, origin_from_aff = extract_affiliate_parts(raw)
    if not aff_id:
        return raw
    if not sub_id:
        sub_id = SUB_ID_DEFAULT

    preserved_origin = normalize_origin_link(origin_from_aff or fallback_origin_link)
    if not preserved_origin:
        return raw

    compact = build_compact_affiliate_link(preserved_origin, aff_id, sub_id)
    return compact or raw


def canonicalize_landing_url(raw_url: str) -> Optional[str]:
    normalized = normalize_origin_link(raw_url)
    if not normalized:
        return None

    parsed = urlparse(normalized)
    shop_id, item_id = extract_product_ids(parsed.path)
    if not shop_id or not item_id:
        return None

    gads_sig = ""
    for k, v in parse_qsl(parsed.query, keep_blank_values=True):
        if k.lower() == "gads_t_sig" and v:
            gads_sig = str(v).strip()
            break
    if not gads_sig:
        return None

    canonical = parsed._replace(
        scheme="https",
        path=f"/product/{shop_id}/{item_id}",
        query=f"gads_t_sig={quote(gads_sig, safe='')}",
        fragment="",
    )
    return urlunparse(canonical)


def build_strict_affiliate_link(origin_link_url: str, affiliate_id: str, sub_id: str) -> str:
    parsed_base = urlparse(str(BASE_REDIRECT or ""))
    if not parsed_base.scheme or not parsed_base.netloc:
        return ""

    origin = str(origin_link_url or "").strip()
    aff = str(affiliate_id or "").strip()
    sid = str(sub_id or "").strip()
    if not origin or not aff or not sid:
        return ""

    return (
        f"{parsed_base.scheme}://{parsed_base.netloc}{parsed_base.path}"
        f"?affiliate_id={quote(aff, safe='')}"
        f"&sub_id={quote(sid, safe='')}"
        f"&origin_link={quote(origin, safe='')}"
    )


def choose_yt_sub_id(sub_id: str) -> str:
    sid = str(sub_id or "").strip()
    if sid and sid.upper().startswith("YT"):
        return sid
    return SUB_ID_YT_DEFAULT


def choose_affiliate_id(*candidates: str) -> str:
    if FORCED_AFFILIATE_ID:
        return FORCED_AFFILIATE_ID
    for value in candidates:
        normalized = str(value or "").strip()
        if normalized:
            return normalized
    return AFFILIATE_ID_YT_DEFAULT


def resolve_shortlink_url(raw_url: str, timeout_sec: float = RESOLVE_TIMEOUT_SEC) -> str:
    parsed = urlparse(str(raw_url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return str(raw_url or "")
    if not is_shortlink_host(parsed.hostname or ""):
        return str(raw_url or "")

    req = UrlRequest(
        urlunparse(parsed),
        method="GET",
        headers={
            "User-Agent": "Shopee-Link-Converter/1.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    try:
        with urlopen(req, timeout=timeout_sec) as resp:
            return str(resp.geturl() or raw_url)
    except URLError as e:
        if (
            ALLOW_INSECURE_TLS_RETRY
            and "CERTIFICATE_VERIFY_FAILED" in str(e)
            and str(parsed.scheme).lower() == "https"
        ):
            insecure_ctx = ssl._create_unverified_context()
            with urlopen(req, timeout=timeout_sec, context=insecure_ctx) as resp:
                return str(resp.geturl() or raw_url)
        return str(raw_url or "")
    except Exception:
        return str(raw_url or "")


def extract_product_ids_from_url(raw_url: str) -> tuple[Optional[str], Optional[str]]:
    text = sanitize_embedded_affiliate_noise(decode_nested_url(raw_url, max_rounds=4))
    if not text:
        return None, None

    try:
        parsed = urlparse(text)
    except Exception:
        return None, None

    if not parsed.scheme or not parsed.netloc:
        return None, None

    query_shop_id = ""
    query_item_id = ""
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        k = str(key or "").strip().lower()
        v = str(value or "").strip()
        if not v:
            continue
        if k in {"shop_id", "shopid", "sh"}:
            query_shop_id = v
        elif k in {"item_id", "itemid", "itm"}:
            query_item_id = v
    if query_shop_id and query_item_id:
        return query_shop_id, query_item_id

    if str(parsed.path or "").endswith("/an_redir"):
        origin = ""
        for k, v in parse_qsl(parsed.query, keep_blank_values=True):
            if k == "origin_link":
                origin = sanitize_embedded_affiliate_noise(decode_nested_url(v, max_rounds=4))
                break
        if origin:
            return extract_product_ids_from_url(origin)

    return extract_product_ids(parsed.path)


def direct_convert_by_template(input_url: str, affiliate_id: str, sub_id: str) -> tuple[str, str]:
    normalized_input = str(input_url or "").strip()
    if not normalized_input:
        return "", ""

    shop_id, item_id = extract_product_ids_from_url(normalized_input)
    if (not shop_id or not item_id) and is_shortlink_host(urlparse(normalized_input).hostname or ""):
        resolved = resolve_shortlink_url(normalized_input)
        shop_id, item_id = extract_product_ids_from_url(resolved)

    if not shop_id or not item_id:
        return "", ""

    origin = f"https://shopee.vn/product/{shop_id}/{item_id}"
    link = build_compact_affiliate_link(origin, affiliate_id, sub_id)
    return link, origin


def is_http_url(text: str) -> bool:
    try:
        parsed = urlparse(str(text or "").strip())
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


def _product_info_cache_url_key(raw_url: str) -> str:
    text = str(raw_url or "").strip()
    if not text:
        return ""
    try:
        parsed = urlparse(text)
    except Exception:
        return ""
    if not parsed.scheme or not parsed.netloc:
        return ""
    normalized = parsed._replace(
        scheme=str(parsed.scheme).lower(),
        netloc=str(parsed.netloc).lower(),
        query="",
        fragment="",
    )
    return urlunparse(normalized)


def _product_info_cache_keys(raw_url: str, shop_id: str = "", item_id: str = "") -> list[str]:
    keys: list[str] = []
    sid = str(shop_id or "").strip()
    iid = str(item_id or "").strip()
    if sid and iid:
        keys.append(f"item:{sid}:{iid}")
    url_key = _product_info_cache_url_key(raw_url)
    if url_key:
        keys.append(f"url:{url_key}")
    return keys


def _product_info_cache_get(keys: list[str]) -> Optional[dict[str, Any]]:
    if not keys:
        return None
    now = now_ts()
    with _product_info_cache_lock:
        for key in keys:
            record = _product_info_cache.get(key)
            if not isinstance(record, dict):
                continue
            expires_at = int(record.get("expiresAt") or 0)
            if expires_at <= now:
                _product_info_cache.pop(key, None)
                continue
            payload = record.get("payload")
            if isinstance(payload, dict):
                return payload
    return None


def _product_info_cache_set(keys: list[str], payload: dict[str, Any]) -> None:
    if not keys or not isinstance(payload, dict):
        return
    expires_at = now_ts() + max(1, PRODUCT_INFO_CACHE_TTL_SEC)
    with _product_info_cache_lock:
        if len(_product_info_cache) >= max(16, PRODUCT_INFO_CACHE_MAX):
            now = now_ts()
            stale_keys = [k for k, v in _product_info_cache.items() if int((v or {}).get("expiresAt") or 0) <= now]
            for stale_key in stale_keys:
                _product_info_cache.pop(stale_key, None)
            while len(_product_info_cache) >= max(16, PRODUCT_INFO_CACHE_MAX):
                oldest = next(iter(_product_info_cache), None)
                if oldest is None:
                    break
                _product_info_cache.pop(oldest, None)

        for key in keys:
            _product_info_cache[key] = {"expiresAt": expires_at, "payload": payload}


def fetch_json_url(url: str, timeout_sec: float = RESOLVE_TIMEOUT_SEC) -> Optional[dict[str, Any]]:
    req = UrlRequest(
        str(url),
        method="GET",
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://shopee.vn/",
            "Origin": "https://shopee.vn",
        },
    )
    try:
        with urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
            return json.loads(raw or "{}")
    except URLError as e:
        if (
            ALLOW_INSECURE_TLS_RETRY
            and "CERTIFICATE_VERIFY_FAILED" in str(e)
            and str(urlparse(str(url)).scheme).lower() == "https"
        ):
            try:
                insecure_ctx = ssl._create_unverified_context()
                with urlopen(req, timeout=timeout_sec, context=insecure_ctx) as resp:
                    raw = resp.read().decode("utf-8", errors="ignore")
                    return json.loads(raw or "{}")
            except Exception:
                return None
        return None
    except Exception:
        return None


def normalize_money_value(raw_value: Any) -> Optional[int]:
    try:
        value = float(raw_value or 0)
    except Exception:
        return None
    if value <= 0:
        return None
    if value >= 100000000:
        value = value / 100000.0
    return int(round(value))


def format_vnd(value: Optional[int]) -> str:
    if not value or value <= 0:
        return ""
    return f"{value:,}".replace(",", ".") + " đ"


def format_compact_number(value: Any) -> str:
    try:
        number = int(float(value))
    except Exception:
        return ""
    if number < 0:
        number = 0
    return f"{number:,}".replace(",", ".")


def normalize_rating(value: Any) -> Optional[float]:
    try:
        rating = float(value)
    except Exception:
        return None
    if rating < 0:
        return None
    return round(rating, 1)


def to_shopee_image_url(image_token: str) -> str:
    token = str(image_token or "").strip()
    if not token:
        return ""
    if token.startswith("http://") or token.startswith("https://"):
        return token
    return f"https://cf.shopee.vn/file/{token}"


def fetch_text_url(url: str, timeout_sec: float = RESOLVE_TIMEOUT_SEC) -> str:
    req = UrlRequest(
        str(url),
        method="GET",
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
        },
    )

    try:
        with urlopen(req, timeout=timeout_sec) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    except URLError as e:
        if (
            ALLOW_INSECURE_TLS_RETRY
            and "CERTIFICATE_VERIFY_FAILED" in str(e)
            and str(urlparse(str(url)).scheme).lower() == "https"
        ):
            try:
                insecure_ctx = ssl._create_unverified_context()
                with urlopen(req, timeout=timeout_sec, context=insecure_ctx) as resp:
                    return resp.read().decode("utf-8", errors="ignore")
            except Exception:
                return ""
        return ""
    except Exception:
        return ""


def parse_html_meta(content: str, key: str) -> str:
    if not content:
        return ""
    patterns = [
        rf'<meta[^>]+property=["\']{re.escape(key)}["\'][^>]+content=["\']([^"\']+)["\']',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']{re.escape(key)}["\']',
        rf'<meta[^>]+name=["\']{re.escape(key)}["\'][^>]+content=["\']([^"\']+)["\']',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']{re.escape(key)}["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, content, re.IGNORECASE)
        if match and match.group(1):
            return html.unescape(str(match.group(1)).strip())
    return ""


def find_product_jsonld(data: Any) -> Optional[dict[str, Any]]:
    if isinstance(data, dict):
        item_type = str(data.get("@type") or "").lower()
        if item_type == "product":
            return data
        for value in data.values():
            found = find_product_jsonld(value)
            if found:
                return found
    if isinstance(data, list):
        for value in data:
            found = find_product_jsonld(value)
            if found:
                return found
    return None


def parse_product_info_from_html(html_text: str, shop_id: str = "", item_id: str = "") -> Optional[dict[str, Any]]:
    if not html_text:
        return None

    name = ""
    image_url = ""
    shop_name = ""
    price_value: Optional[int] = None
    sold_value = 0
    rating_value: Optional[float] = None

    for block in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html_text,
        re.IGNORECASE | re.DOTALL,
    ):
        raw = html.unescape(str(block or "").strip())
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except Exception:
            continue
        product = find_product_jsonld(parsed)
        if not isinstance(product, dict):
            continue

        if not name:
            name = str(product.get("name") or "").strip()

        if not image_url:
            image = product.get("image")
            if isinstance(image, list) and image:
                image_url = str(image[0] or "").strip()
            elif isinstance(image, str):
                image_url = image.strip()

        if not shop_name:
            brand = product.get("brand")
            if isinstance(brand, dict):
                shop_name = str(brand.get("name") or "").strip()
            elif isinstance(brand, str):
                shop_name = brand.strip()

        offers = product.get("offers")
        if isinstance(offers, dict) and price_value is None:
            price_value = normalize_money_value(offers.get("price"))
        elif isinstance(offers, list) and offers and price_value is None and isinstance(offers[0], dict):
            price_value = normalize_money_value(offers[0].get("price"))

        rating = product.get("aggregateRating")
        if isinstance(rating, dict) and rating_value is None:
            rating_value = normalize_rating(rating.get("ratingValue"))

    if not name:
        name = parse_html_meta(html_text, "og:title")
    if not image_url:
        image_url = parse_html_meta(html_text, "og:image")
    if not shop_name:
        shop_name = parse_html_meta(html_text, "product:brand")
    if price_value is None:
        price_value = normalize_money_value(parse_html_meta(html_text, "product:price:amount"))
    if rating_value is None:
        rating_value = normalize_rating(parse_html_meta(html_text, "rating"))

    if not name:
        title_match = re.search(r"<title>(.*?)</title>", html_text, re.IGNORECASE | re.DOTALL)
        if title_match and title_match.group(1):
            title = html.unescape(str(title_match.group(1))).strip()
            if "|" in title:
                title = title.split("|", 1)[0].strip()
            name = title

    sold_match = re.search(r"Đã bán\\s*([0-9.,kK]+)", html_text, re.IGNORECASE)
    if sold_match and sold_match.group(1):
        sold_text = str(sold_match.group(1)).lower().replace(",", ".")
        if sold_text.endswith("k"):
            try:
                sold_value = int(float(sold_text[:-1]) * 1000)
            except Exception:
                sold_value = 0
        else:
            sold_value = int(float(sold_text.replace(".", "")))

    if not name:
        return None

    return {
        "shopId": str(shop_id or ""),
        "itemId": str(item_id or ""),
        "name": name,
        "shopName": shop_name,
        "image": image_url,
        "price": price_value or 0,
        "priceText": format_vnd(price_value),
        "sold": sold_value,
        "soldText": format_compact_number(sold_value),
        "rating": rating_value if rating_value is not None else 0,
        "ratingText": f"{rating_value:.1f}" if rating_value is not None else "",
    }


def infer_product_name_from_url(raw_url: str, item_id: str = "") -> str:
    text = str(raw_url or "").strip()
    if not text:
        return f"Sản phẩm Shopee #{item_id}" if item_id else "Sản phẩm Shopee"

    try:
        parsed = urlparse(text)
    except Exception:
        return f"Sản phẩm Shopee #{item_id}" if item_id else "Sản phẩm Shopee"

    path = str(parsed.path or "").strip("/")
    if not path:
        return f"Sản phẩm Shopee #{item_id}" if item_id else "Sản phẩm Shopee"

    tail = path.split("/")[-1]
    if "-i." in tail:
        tail = tail.split("-i.", 1)[0]
    tail = tail.replace("-", " ").replace("_", " ").strip()
    tail = re.sub(r"\s+", " ", tail)
    meaningless = {"an redir", "an_redir", "product", "item", "p", "redirect"}
    if tail and not tail.isdigit() and tail.lower() not in meaningless:
        return tail[:120]
    return f"Sản phẩm Shopee #{item_id}" if item_id else "Sản phẩm Shopee"


def build_fallback_product_info(raw_url: str, shop_id: str = "", item_id: str = "") -> dict[str, Any]:
    return {
        "shopId": str(shop_id or ""),
        "itemId": str(item_id or ""),
        "name": infer_product_name_from_url(raw_url, item_id),
        "shopName": "Shopee",
        "image": "",
        "price": 0,
        "priceText": "",
        "sold": 0,
        "soldText": "0",
        "rating": 0,
        "ratingText": "0.0",
    }


def fetch_shop_name(shop_id: str, timeout_sec: float = PRODUCT_INFO_SHOP_TIMEOUT_SEC) -> str:
    payload = fetch_json_url(
        f"https://shopee.vn/api/v4/shop/get?shopid={quote(str(shop_id), safe='')}",
        timeout_sec=max(0.6, float(timeout_sec or PRODUCT_INFO_SHOP_TIMEOUT_SEC)),
    )
    if not isinstance(payload, dict) or payload.get("error") not in (0, None):
        return ""
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        return ""
    for key in ("name", "shop_name"):
        value = str(data.get(key) or "").strip()
        if value:
            return value
    account = data.get("account") or {}
    if isinstance(account, dict):
        for key in ("username", "name"):
            value = str(account.get(key) or "").strip()
            if value:
                return value
    return ""


def parse_item_payload_to_product(item_payload: Any, shop_id: str, item_id: str, include_shop_lookup: bool, timeout_sec: float) -> Optional[dict[str, Any]]:
    if not isinstance(item_payload, dict):
        return None

    name = str(item_payload.get("name") or "").strip()
    images = item_payload.get("images") or []
    first_image = ""
    if isinstance(images, list) and images:
        first_image = to_shopee_image_url(str(images[0] or ""))

    shop_name = str(item_payload.get("shop_name") or "").strip()
    if not shop_name and include_shop_lookup:
        shop_name = fetch_shop_name(
            shop_id,
            timeout_sec=min(float(timeout_sec or PRODUCT_INFO_TIMEOUT_SEC), PRODUCT_INFO_SHOP_TIMEOUT_SEC),
        )

    price_value = None
    for price_key in ("price_min", "price", "price_max"):
        price_value = normalize_money_value(item_payload.get(price_key))
        if price_value:
            break

    sold_value = 0
    try:
        sold_value = int(float(item_payload.get("historical_sold") or 0))
    except Exception:
        sold_value = 0
    sold_value = max(sold_value, 0)

    rating_raw = None
    item_rating = item_payload.get("item_rating") or {}
    if isinstance(item_rating, dict):
        rating_raw = item_rating.get("rating_star")
    rating_value = normalize_rating(rating_raw)

    if not name:
        return None

    return {
        "shopId": str(shop_id),
        "itemId": str(item_id),
        "name": name,
        "shopName": shop_name,
        "image": first_image,
        "price": price_value or 0,
        "priceText": format_vnd(price_value),
        "sold": sold_value,
        "soldText": format_compact_number(sold_value),
        "rating": rating_value if rating_value is not None else 0,
        "ratingText": f"{rating_value:.1f}" if rating_value is not None else "",
    }


def fetch_product_info_by_ids(
    shop_id: str,
    item_id: str,
    timeout_sec: float = PRODUCT_INFO_TIMEOUT_SEC,
    include_shop_lookup: bool = PRODUCT_INFO_INCLUDE_SHOP_LOOKUP,
) -> Optional[dict[str, Any]]:
    req_timeout = max(0.8, float(timeout_sec or PRODUCT_INFO_TIMEOUT_SEC))

    payload = fetch_json_url(
        f"https://shopee.vn/api/v4/item/get?itemid={quote(str(item_id), safe='')}&shopid={quote(str(shop_id), safe='')}",
        timeout_sec=req_timeout,
    )
    if isinstance(payload, dict) and payload.get("error") in (0, None):
        data = payload.get("data") or {}
        if isinstance(data, dict):
            item = data.get("item_basic")
            if not isinstance(item, dict):
                item = data
            product = parse_item_payload_to_product(item, shop_id, item_id, include_shop_lookup, req_timeout)
            if product:
                return product

    payload = fetch_json_url(
        f"https://shopee.vn/api/v4/pdp/get_pc?item_id={quote(str(item_id), safe='')}&shop_id={quote(str(shop_id), safe='')}",
        timeout_sec=max(0.8, min(req_timeout, PRODUCT_INFO_HTML_TIMEOUT_SEC + 0.6)),
    )
    if isinstance(payload, dict) and payload.get("error") in (0, None):
        data = payload.get("data") or {}
        if isinstance(data, dict):
            item = data.get("item")
            if not isinstance(item, dict):
                item = data.get("item_basic")
            if not isinstance(item, dict):
                item = data
            product = parse_item_payload_to_product(item, shop_id, item_id, include_shop_lookup, req_timeout)
            if product:
                return product

    return None


# -----------------------------
# Queue/worker store helpers
# -----------------------------

def create_job(
    input_raw: str,
    normalized_url: str,
    preferred_affiliate_id: str = "",
    preferred_sub_id: str = "",
    source: str = SOURCE_FB,
    client_ip: str = "",
    client_user_agent: str = "",
) -> dict[str, Any]:
    ts = now_ts()
    job_id = f"job_{uuid.uuid4().hex[:12]}"

    job = {
        "id": job_id,
        "input": input_raw,
        "url": normalized_url,
        "source": normalize_source(source),
        "status": "queued",
        "message": "Đã nhận yêu cầu, đang chờ worker xử lý.",
        "createdAt": ts,
        "updatedAt": ts,
        "startedAt": None,
        "assignedWorker": None,
        "affiliateLink": "",
        "longAffiliateLink": "",
        "campaignRawAffiliateLink": "",
        "campaignSource": "",
        "campaignAffiliateId": "",
        "campaignSubId": "",
        "requestedAffiliateId": str(preferred_affiliate_id or "").strip(),
        "requestedSubId": str(preferred_sub_id or "").strip(),
        "clientIp": str(client_ip or "").strip(),
        "clientUserAgent": str(client_user_agent or "").strip(),
        "landingUrl": "",
        "cleanLandingUrl": "",
    }

    with STORE_LOCK:
        JOBS[job_id] = job
        PENDING_QUEUE.append(job_id)

    return job


def claim_next_job(worker_id: str) -> Optional[dict[str, Any]]:
    ts = now_ts()
    with STORE_LOCK:
        claimed_idx = None
        claimed_job = None

        for idx, job_id in enumerate(PENDING_QUEUE):
            job = JOBS.get(job_id)
            if not job:
                continue
            if job["status"] != "queued":
                continue
            claimed_idx = idx
            claimed_job = job
            break

        if claimed_job is None:
            return None

        PENDING_QUEUE.pop(claimed_idx)
        claimed_job["status"] = "processing"
        claimed_job["message"] = "Worker đang xử lý yêu cầu."
        claimed_job["updatedAt"] = ts
        claimed_job["startedAt"] = ts
        claimed_job["assignedWorker"] = worker_id

        return {
            "id": claimed_job["id"],
            "input": claimed_job["input"],
            "url": claimed_job["url"],
            "source": claimed_job.get("source", SOURCE_FB),
            "requestedAffiliateId": str(claimed_job.get("requestedAffiliateId") or ""),
            "requestedSubId": str(claimed_job.get("requestedSubId") or ""),
        }


def upsert_worker(body: WorkerPollRequest) -> str:
    ts = now_ts()
    worker_id = str(body.workerId or "").strip() or f"worker-{uuid.uuid4().hex[:8]}"

    with STORE_LOCK:
        worker = WORKERS.get(worker_id) or {
            "id": worker_id,
            "name": "",
            "affiliateId": "",
            "subId": "",
            "lastSeen": ts,
            "online": True,
            "createdAt": ts,
        }

        worker["name"] = str(body.workerName or worker.get("name") or worker_id)
        worker["affiliateId"] = str(body.affiliateId or worker.get("affiliateId") or "")
        worker["subId"] = str(body.subId or worker.get("subId") or "")
        worker["lastSeen"] = ts
        worker["online"] = True

        WORKERS[worker_id] = worker

    return worker_id


def workers_summary() -> dict[str, int]:
    ts = now_ts()
    with STORE_LOCK:
        total = len(WORKERS)
        online = sum(1 for w in WORKERS.values() if ts - int(w.get("lastSeen", ts)) <= WORKER_STALE_SEC)
    return {"total": total, "online": online}


def queue_size() -> int:
    with STORE_LOCK:
        return len(PENDING_QUEUE)


def cleanup_state() -> None:
    ts = now_ts()

    with STORE_LOCK:
        expired_codes: list[str] = []
        for code, rec in SHORT_LINKS.items():
            if ts - int(rec.get("createdAt", ts)) > SHORT_TTL_SEC:
                expired_codes.append(code)
        for code in expired_codes:
            SHORT_LINKS.pop(code, None)

        for worker in WORKERS.values():
            if ts - int(worker.get("lastSeen", ts)) > WORKER_STALE_SEC:
                worker["online"] = False

        for job in JOBS.values():
            if job["status"] in ("success", "error", "expired"):
                continue

            age = ts - int(job["createdAt"])
            if age > JOB_TTL_SEC:
                job["status"] = "expired"
                job["message"] = "Yêu cầu đã hết hạn do quá thời gian chờ worker."
                job["updatedAt"] = ts
                continue

            if job["status"] == "processing":
                run_for = ts - int(job.get("startedAt") or job["updatedAt"])
                if run_for > JOB_PROCESS_TIMEOUT_SEC:
                    job["status"] = "error"
                    job["message"] = "Worker xử lý quá lâu. Vui lòng thử lại."
                    job["updatedAt"] = ts


def public_job_view(job: dict[str, Any]) -> dict[str, Any]:
    view: dict[str, Any] = {
        "id": job["id"],
        "source": job.get("source", SOURCE_FB),
        "status": job["status"],
        "message": job.get("message", ""),
        "createdAt": job["createdAt"],
        "updatedAt": job["updatedAt"],
    }

    if job["status"] == "success":
        view["result"] = {
            "affiliateLink": job.get("affiliateLink", ""),
            "longAffiliateLink": job.get("longAffiliateLink", ""),
            "campaignRawAffiliateLink": job.get("campaignRawAffiliateLink", ""),
            "campaignSource": job.get("campaignSource", ""),
            "campaignAffiliateId": job.get("campaignAffiliateId", ""),
            "campaignSubId": job.get("campaignSubId", ""),
            "requestedAffiliateId": job.get("requestedAffiliateId", ""),
            "requestedSubId": job.get("requestedSubId", ""),
            "landingUrl": job.get("landingUrl", ""),
            "cleanLandingUrl": job.get("cleanLandingUrl", ""),
            "workerId": job.get("assignedWorker", ""),
        }

    return view


def random_code(length: int = SHORT_CODE_LEN) -> str:
    alphabet = string.ascii_letters + string.digits
    size = max(3, int(length or 4))
    return "".join(secrets.choice(alphabet) for _ in range(size))


def create_short_code(long_url: str) -> str:
    ts = now_ts()
    with STORE_LOCK:
        for _ in range(20):
            code = random_code(SHORT_CODE_LEN)
            if code not in SHORT_LINKS:
                SHORT_LINKS[code] = {"url": long_url, "createdAt": ts, "hits": 0}
                return code

        code = f"{random_code(max(SHORT_CODE_LEN, 4))}{int(time.time() % 1000)}"
        SHORT_LINKS[code] = {"url": long_url, "createdAt": ts, "hits": 0}
        return code


def get_short_target(code: str) -> Optional[str]:
    if not code:
        return None

    with STORE_LOCK:
        rec = SHORT_LINKS.get(code)
        if not rec:
            return None
        rec["hits"] = int(rec.get("hits", 0)) + 1
        return str(rec.get("url") or "")


def infer_base_url(request: Request) -> str:
    if SHORT_PUBLIC_BASE:
        return SHORT_PUBLIC_BASE.rstrip("/")

    xfp = request.headers.get("x-forwarded-proto")
    proto = xfp or request.url.scheme
    host = request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


def make_short_link(request: Request, long_url: str) -> tuple[str, str]:
    code = create_short_code(long_url)
    base = infer_base_url(request)
    return f"{base}/r/{code}", code


def require_worker_token(request: Request, body_token: Optional[str]) -> bool:
    provided = request.headers.get("X-Worker-Token") or str(body_token or "")
    return str(provided).strip() == WORKER_TOKEN


def wait_for_job_terminal(job_id: str, timeout_sec: float = SYNC_WAIT_TIMEOUT_SEC) -> dict[str, Any]:
    deadline = time.time() + max(1.0, float(timeout_sec or 1))
    poll_sec = max(0.15, SYNC_WAIT_POLL_MS / 1000.0)

    while time.time() < deadline:
        cleanup_state()
        with STORE_LOCK:
            job = JOBS.get(job_id)
            if not job:
                return {"ok": False, "message": "Không tìm thấy job sau khi submit."}

            status = job.get("status")
            if status == "success":
                return {"ok": True, "job": dict(job)}
            if status in ("error", "expired"):
                return {"ok": False, "message": job.get("message") or "Job thất bại."}

        time.sleep(poll_sec)

    return {"ok": False, "message": "Timeout chờ worker xử lý."}


def cancel_queued_job(job_id: str, message: str = "Yêu cầu đã bị hủy.") -> None:
    if not job_id:
        return
    ts = now_ts()
    with STORE_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        if str(job.get("status") or "") == "queued":
            while job_id in PENDING_QUEUE:
                PENDING_QUEUE.remove(job_id)
            job["status"] = "expired"
            job["message"] = str(message or "Yêu cầu đã bị hủy.")
            job["updatedAt"] = ts


def submit_job_result(body: WorkerSubmitRequest) -> tuple[int, dict[str, Any]]:
    worker_id = str(body.workerId or "").strip()
    job_id = str(body.jobId or "").strip()
    success = bool(body.success)

    if not worker_id or not job_id:
        return 400, {"ok": False, "message": "Thiếu workerId hoặc jobId."}

    with STORE_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return 404, {"ok": False, "message": "Không tìm thấy job."}

        if job.get("assignedWorker") != worker_id:
            return 409, {"ok": False, "message": "Job không thuộc worker này."}

        ts = now_ts()
        source = normalize_source(str(job.get("source") or SOURCE_FB))
        requested_aff_id = str(job.get("requestedAffiliateId") or "").strip()
        requested_sub_id = str(job.get("requestedSubId") or "").strip()
        if success:
            affiliate_link = str(body.affiliateLink or "").strip()
            if not affiliate_link:
                return 400, {"ok": False, "message": "Thiếu affiliateLink cho kết quả thành công."}

            campaign_raw_link = str(body.campaignRawAffiliateLink or "").strip()
            campaign_source = str(body.campaignSource or "").strip()
            campaign_aff_id = str(body.campaignAffiliateId or "").strip()
            campaign_sub_id = str(body.campaignSubId or "").strip()

            raw_landing_url = str(body.landingUrl or "").strip()
            raw_clean_url = str(body.cleanLandingUrl or "").strip()
            src_aff_id, src_sub_id, origin_from_aff = extract_affiliate_parts(affiliate_link)
            if not src_aff_id and campaign_aff_id:
                src_aff_id = campaign_aff_id
            if not src_sub_id and campaign_sub_id:
                src_sub_id = campaign_sub_id

            worker_aff_id = str((WORKERS.get(worker_id) or {}).get("affiliateId") or "").strip()
            if source == SOURCE_YT:
                canonical_clean = canonicalize_landing_url(raw_clean_url or raw_landing_url)
                final_aff_id = choose_affiliate_id(
                    worker_aff_id,
                    campaign_aff_id,
                    src_aff_id,
                    requested_aff_id,
                    AFFILIATE_ID_YT_DEFAULT,
                )
                final_sub_id = choose_yt_sub_id(campaign_sub_id or src_sub_id or requested_sub_id)
                final_affiliate_link = ""
                if canonical_clean:
                    final_affiliate_link = build_strict_affiliate_link(canonical_clean, final_aff_id, final_sub_id)

                display_affiliate = ""
                if is_http_url(campaign_raw_link):
                    display_affiliate = campaign_raw_link
                elif is_http_url(affiliate_link):
                    display_affiliate = affiliate_link
                elif final_affiliate_link:
                    display_affiliate = final_affiliate_link

                if not display_affiliate:
                    return 422, {"ok": False, "message": "Không có affiliate link hợp lệ để trả kết quả."}

                long_affiliate = final_affiliate_link or affiliate_link or display_affiliate
                final_origin_link = canonical_clean or raw_clean_url or raw_landing_url
                resolved_campaign_sub = campaign_sub_id or src_sub_id or final_sub_id
            else:
                normalized_from_landing = normalize_origin_link(raw_clean_url or raw_landing_url)
                normalized_from_affiliate = normalize_origin_link(origin_from_aff)
                final_origin_link = normalized_from_landing or normalized_from_affiliate or raw_clean_url or raw_landing_url
                display_affiliate = compact_from_affiliate(affiliate_link, final_origin_link or "")
                long_affiliate = affiliate_link
                resolved_campaign_sub = campaign_sub_id or src_sub_id or requested_sub_id or SUB_ID_DEFAULT

            job["status"] = "success"
            job["message"] = "Tạo link thành công."
            job["source"] = source
            job["affiliateLink"] = display_affiliate
            job["longAffiliateLink"] = long_affiliate
            job["campaignRawAffiliateLink"] = campaign_raw_link
            job["campaignSource"] = campaign_source
            job["campaignAffiliateId"] = campaign_aff_id or src_aff_id or worker_aff_id
            job["campaignSubId"] = resolved_campaign_sub
            job["landingUrl"] = raw_landing_url
            job["cleanLandingUrl"] = final_origin_link
            job["updatedAt"] = ts

            # Store history for admin dashboard.
            save_conversion(
                input_url=str(job.get("url") or job.get("input") or ""),
                resolved_url=raw_landing_url or None,
                origin_link=(final_origin_link or None),
                affiliate_link=(long_affiliate or display_affiliate or None),
                success=True,
                error_message=None,
                client_ip=str(job.get("clientIp") or "") or None,
                user_agent=str(job.get("clientUserAgent") or "").strip() or f"worker:{worker_id}",
                source=source,
            )
        else:
            err_message = str(body.message or "Worker không tạo được link.")
            job["status"] = "error"
            job["message"] = err_message
            job["updatedAt"] = ts

            save_conversion(
                input_url=str(job.get("url") or job.get("input") or ""),
                resolved_url=None,
                origin_link=None,
                affiliate_link=None,
                success=False,
                error_message=err_message,
                client_ip=str(job.get("clientIp") or "") or None,
                user_agent=str(job.get("clientUserAgent") or "").strip() or f"worker:{worker_id}",
                source=source,
            )

    return 200, {"ok": True, "message": "Đã cập nhật kết quả job."}


# -----------------------------
# Public + admin endpoints
# -----------------------------

@app.get("/", response_class=HTMLResponse)
@app.get("/index.php", response_class=HTMLResponse)
def home(
    request: Request,
    url: Optional[str] = Query(default=None),
    affiliate_id: Optional[str] = Query(default=None),
    sub_id: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    yt: str = Query(default="0"),
):
    cleanup_state()

    # Sync API mode: /?url=<encoded>&yt=1
    if url:
        client_ip = get_client_ip(request)
        user_agent = request.headers.get("user-agent")
        yt_enabled = str(yt).strip().lower() not in {"0", "false", "no", "off"}
        fallback_source = SOURCE_YT if FORCE_YT_MODE or yt_enabled else SOURCE_FB
        source_key = normalize_source(source, fallback=fallback_source)
        default_affiliate_id, default_sub_id = source_defaults(source_key)
        summary = workers_summary()
        has_online_worker = summary["online"] > 0

        allowed, retry_after = check_rate_limit(client_ip)
        if not allowed:
            save_conversion(
                input_url=str(url).strip(),
                resolved_url=None,
                origin_link=None,
                affiliate_link=None,
                success=False,
                error_message=f"rate_limited:{retry_after}s",
                client_ip=client_ip,
                user_agent=user_agent,
                source=source_key,
            )
            return JSONResponse(
                status_code=429,
                content={"success": False, "message": f"Bạn thao tác quá nhanh. Vui lòng thử lại sau {retry_after}s."},
                headers={"Retry-After": str(retry_after)},
            )

        mode = source_key
        parsed_input = normalize_input(url)
        if not parsed_input["ok"]:
            return JSONResponse(status_code=400, content={"success": False, "message": parsed_input["error"], "mode": mode})

        direct_affiliate_candidate, direct_origin = direct_convert_by_template(
            str(parsed_input["url"]),
            default_affiliate_id,
            default_sub_id,
        )
        if (not has_online_worker) and direct_affiliate_candidate:
            save_conversion(
                input_url=str(parsed_input["url"]),
                resolved_url=None,
                origin_link=direct_origin or None,
                affiliate_link=direct_affiliate_candidate,
                success=True,
                error_message=None,
                client_ip=client_ip,
                user_agent=user_agent,
                source=source_key,
            )
            return JSONResponse(
                status_code=200,
                content={
                    "success": True,
                    "affiliateLink": direct_affiliate_candidate,
                    "longAffiliateLink": direct_affiliate_candidate,
                    "mode": "template",
                    "source": source_key,
                    "affiliate_id": default_affiliate_id,
                    "sub_id": default_sub_id,
                    "jobId": "",
                },
            )

        if not has_online_worker:
            return JSONResponse(
                status_code=422,
                content={
                    "success": False,
                    "message": "Hiện chưa có worker/extension online và link này không convert được theo luồng mặc định.",
                    "mode": mode,
                    "source": source_key,
                    "jobId": "",
                },
            )

        requested_affiliate_id = str(affiliate_id or "").strip() or default_affiliate_id
        requested_sub_id = str(sub_id or "").strip() or default_sub_id
        job = create_job(
            str(url),
            str(parsed_input["url"]),
            preferred_affiliate_id=requested_affiliate_id,
            preferred_sub_id=requested_sub_id,
            source=source_key,
            client_ip=str(client_ip or ""),
            client_user_agent=str(user_agent or ""),
        )
        wait_timeout = float(SYNC_WAIT_TIMEOUT_SEC)
        if direct_affiliate_candidate:
            wait_timeout = min(float(SYNC_WAIT_TIMEOUT_SEC), float(SYNC_WORKER_PRIORITY_WAIT_SEC))
        done = wait_for_job_terminal(job["id"], wait_timeout)
        if not done["ok"]:
            if direct_affiliate_candidate:
                cancel_queued_job(job["id"], "Đã fallback sang luồng template nhanh.")
                save_conversion(
                    input_url=str(parsed_input["url"]),
                    resolved_url=None,
                    origin_link=direct_origin or None,
                    affiliate_link=direct_affiliate_candidate,
                    success=True,
                    error_message=None,
                    client_ip=client_ip,
                    user_agent=user_agent,
                    source=source_key,
                )
                return JSONResponse(
                    status_code=200,
                    content={
                        "success": True,
                        "affiliateLink": direct_affiliate_candidate,
                        "longAffiliateLink": direct_affiliate_candidate,
                        "mode": "template_fallback",
                        "source": source_key,
                        "affiliate_id": default_affiliate_id,
                        "sub_id": default_sub_id,
                        "jobId": job["id"],
                        "message": "Worker phản hồi chậm, đã chuyển sang luồng nhanh.",
                    },
                )
            return JSONResponse(
                status_code=422,
                content={
                    "success": False,
                    "message": done["message"],
                    "mode": mode,
                    "source": source_key,
                    "jobId": job["id"],
                },
            )

        final_job = done["job"]
        display_affiliate = str(final_job.get("affiliateLink") or "").strip()
        long_affiliate = str(final_job.get("longAffiliateLink") or "").strip() or display_affiliate

        long_aff_id, long_sub_id = parse_affiliate_meta(long_affiliate)
        campaign_aff_id = str(final_job.get("campaignAffiliateId") or "").strip()
        campaign_sub_id = str(final_job.get("campaignSubId") or "").strip()

        affiliate_id, sub_id = parse_affiliate_meta(display_affiliate)
        if not affiliate_id:
            affiliate_id = campaign_aff_id or long_aff_id
        if not sub_id:
            sub_id = campaign_sub_id or long_sub_id or default_sub_id

        if not display_affiliate:
            display_affiliate = long_affiliate
        final_output_affiliate = display_affiliate or long_affiliate

        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "affiliateLink": final_output_affiliate,
                "longAffiliateLink": long_affiliate,
                "mode": mode,
                "source": source_key,
                "affiliate_id": affiliate_id,
                "sub_id": sub_id,
                "jobId": str(final_job.get("id") or ""),
            },
        )

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "default_affiliate_id_fb": AFFILIATE_ID_DEFAULT,
            "default_sub_id_fb": SUB_ID_DEFAULT,
            "default_affiliate_id_yt": AFFILIATE_ID_YT_DEFAULT,
            "default_sub_id_yt": SUB_ID_YT_DEFAULT,
            "asset_version": SESSION_TOKEN_VERSION,
        },
    )


@app.get("/r/{code}")
def short_redirect(code: str):
    cleanup_state()
    target = get_short_target(code)
    if not target:
        return JSONResponse(status_code=404, content={"success": False, "message": "Short code không tồn tại hoặc đã hết hạn."})
    return RedirectResponse(url=target, status_code=302)


@app.get("/api/health")
def api_health() -> dict[str, Any]:
    cleanup_state()
    return {
        "ok": True,
        "workers": workers_summary(),
        "queueSize": queue_size(),
        "timestamp": now_ts(),
    }


@app.get("/api/product-info")
def api_product_info(url: str = Query(..., min_length=6, max_length=4096)):
    parsed = normalize_input(url)
    if not parsed["ok"]:
        return {"ok": False, "message": parsed["error"]}

    normalized = str(parsed["url"])
    lookup_url = normalized
    shop_id, item_id = extract_product_ids_from_url(normalized)

    if (not shop_id or not item_id) and is_shortlink_host(urlparse(normalized).hostname or ""):
        resolved = resolve_shortlink_url(normalized, timeout_sec=PRODUCT_INFO_RESOLVE_TIMEOUT_SEC)
        if resolved:
            lookup_url = resolved
            shop_id, item_id = extract_product_ids_from_url(resolved)

    cache_keys = _product_info_cache_keys(lookup_url or normalized, shop_id or "", item_id or "")
    cached_payload = _product_info_cache_get(cache_keys)
    if cached_payload:
        return cached_payload

    product = None
    if shop_id and item_id:
        product = fetch_product_info_by_ids(
            shop_id,
            item_id,
            timeout_sec=PRODUCT_INFO_TIMEOUT_SEC,
            include_shop_lookup=PRODUCT_INFO_INCLUDE_SHOP_LOOKUP,
        )

    if not product:
        html_url = ""
        if shop_id and item_id:
            html_url = f"https://shopee.vn/product/{shop_id}/{item_id}"
        elif lookup_url:
            html_url = lookup_url
        if html_url:
            html_text = fetch_text_url(html_url, timeout_sec=PRODUCT_INFO_HTML_TIMEOUT_SEC)
            product = parse_product_info_from_html(html_text, shop_id or "", item_id or "")

    if not product:
        if shop_id or item_id:
            fallback_url = lookup_url or normalized
            if shop_id and item_id:
                fallback_url = f"https://shopee.vn/product/{shop_id}/{item_id}"
            payload = {
                "ok": True,
                "fallback": True,
                "product": build_fallback_product_info(fallback_url, shop_id or "", item_id or ""),
            }
            _product_info_cache_set(cache_keys, payload)
            return payload
        payload = {
            "ok": True,
            "fallback": True,
            "product": build_fallback_product_info(lookup_url or normalized, "", ""),
        }
        _product_info_cache_set(cache_keys, payload)
        return payload

    payload = {"ok": True, "product": product}
    _product_info_cache_set(cache_keys, payload)
    return payload


@app.post("/api/convert")
def api_convert(payload: ConvertRequest, request: Request):
    cleanup_state()
    client_ip = get_client_ip(request)
    user_agent = request.headers.get("user-agent")
    source_key = normalize_source(payload.source, fallback=SOURCE_FB)
    default_affiliate_id, default_sub_id = source_defaults(source_key)
    summary = workers_summary()
    has_online_worker = summary["online"] > 0

    allowed, retry_after = check_rate_limit(client_ip)
    if not allowed:
        raw_text = str(payload.input_url or payload.input or payload.url or "").strip()
        save_conversion(
            input_url=raw_text,
            resolved_url=None,
            origin_link=None,
            affiliate_link=None,
            success=False,
            error_message=f"rate_limited:{retry_after}s",
            client_ip=client_ip,
            user_agent=user_agent,
            source=source_key,
        )
        return JSONResponse(
            status_code=429,
            content={"ok": False, "message": f"Bạn thao tác quá nhanh. Vui lòng thử lại sau {retry_after}s."},
            headers={"Retry-After": str(retry_after)},
        )

    raw_input = payload.url or payload.input or payload.input_url or ""
    parsed = normalize_input(raw_input)
    if not parsed["ok"]:
        return JSONResponse(status_code=400, content={"ok": False, "message": parsed["error"]})

    direct_affiliate, direct_origin = "", ""
    if not has_online_worker:
        direct_affiliate, direct_origin = direct_convert_by_template(
            str(parsed["url"]),
            default_affiliate_id,
            default_sub_id,
        )
    if direct_affiliate:
        save_conversion(
            input_url=str(parsed["url"]),
            resolved_url=None,
            origin_link=direct_origin or None,
            affiliate_link=direct_affiliate,
            success=True,
            error_message=None,
            client_ip=client_ip,
            user_agent=user_agent,
            source=source_key,
        )
        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "mode": "template",
                "source": source_key,
                "affiliateLink": direct_affiliate,
                "affiliate_id": default_affiliate_id,
                "sub_id": default_sub_id,
            },
        )

    if not has_online_worker:
        return JSONResponse(
            status_code=422,
            content={
                "ok": False,
                "message": "Hiện chưa có worker/extension online và link này không convert được theo luồng mặc định.",
                "source": source_key,
            },
        )

    requested_affiliate_id = str(payload.affiliate_id or "").strip() or default_affiliate_id
    requested_sub_id = str(payload.sub_id or "").strip() or default_sub_id
    job = create_job(
        str(raw_input),
        str(parsed["url"]),
        preferred_affiliate_id=requested_affiliate_id,
        preferred_sub_id=requested_sub_id,
        source=source_key,
        client_ip=str(client_ip or ""),
        client_user_agent=str(user_agent or ""),
    )
    msg = "Đã xếp hàng xử lý."
    if summary["online"] == 0:
        msg = "Đã nhận yêu cầu nhưng chưa có worker online."

    return JSONResponse(
        status_code=202,
        content={
            "ok": True,
            "source": source_key,
            "jobId": job["id"],
            "status": job["status"],
            "message": msg,
        },
    )


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str):
    cleanup_state()
    with STORE_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return JSONResponse(status_code=404, content={"ok": False, "message": "Không tìm thấy job."})
        data = public_job_view(job)
    return {"ok": True, "job": data}


@app.post("/worker/poll")
def worker_poll(payload: WorkerPollRequest, request: Request):
    cleanup_state()
    if not require_worker_token(request, payload.workerToken):
        return JSONResponse(status_code=401, content={"ok": False, "message": "Worker token không hợp lệ."})

    worker_id = upsert_worker(payload)
    job = claim_next_job(worker_id)
    return {
        "ok": True,
        "workerId": worker_id,
        "job": job,
        "waitMs": 180,
    }


@app.post("/worker/submit")
def worker_submit(payload: WorkerSubmitRequest, request: Request):
    cleanup_state()
    if not require_worker_token(request, payload.workerToken):
        return JSONResponse(status_code=401, content={"ok": False, "message": "Worker token không hợp lệ."})

    status_code, data = submit_job_result(payload)
    return JSONResponse(status_code=status_code, content=data)


@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request):
    return templates.TemplateResponse("admin_login.html", {"request": request})


@app.post("/api/admin/login")
def admin_login(payload: AdminLoginRequest):
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
def admin_logout():
    response = JSONResponse({"success": True})
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return response


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    if not is_admin_authenticated(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    return templates.TemplateResponse("admin.html", {"request": request})


@app.get("/api/admin/stats")
def admin_stats(request: Request):
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
        by_source_rows = conn.execute(
            """
            SELECT
                source,
                COUNT(*) AS total,
                SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS success
            FROM conversions
            GROUP BY source
            """
        ).fetchall()
        by_source_today_rows = conn.execute(
            """
            SELECT
                source,
                COUNT(*) AS total
            FROM conversions
            WHERE date(created_at) = date('now', 'localtime')
            GROUP BY source
            """
        ).fetchall()
        recent = conn.execute(
            """
            SELECT
                id,
                created_at,
                source,
                input_url,
                resolved_url,
                affiliate_link,
                success,
                error_message,
                client_ip
            FROM conversions
            ORDER BY id DESC
            LIMIT 100
            """
        ).fetchall()

    by_source: dict[str, dict[str, int]] = {
        SOURCE_FB: {"total": 0, "success": 0, "failed": 0, "today": 0},
        SOURCE_YT: {"total": 0, "success": 0, "failed": 0, "today": 0},
    }
    for row in by_source_rows:
        source_key = normalize_source(str(row["source"] or SOURCE_FB))
        total_count = int(row["total"] or 0)
        success_count = int(row["success"] or 0)
        by_source[source_key] = {
            "total": total_count,
            "success": success_count,
            "failed": max(total_count - success_count, 0),
            "today": by_source.get(source_key, {}).get("today", 0),
        }

    for row in by_source_today_rows:
        source_key = normalize_source(str(row["source"] or SOURCE_FB))
        today_count = int(row["total"] or 0)
        data = by_source.get(source_key) or {"total": 0, "success": 0, "failed": 0, "today": 0}
        data["today"] = today_count
        by_source[source_key] = data

    return {
        "success": True,
        "summary": {
            "total": total,
            "success": success_total,
            "failed": failed_total,
            "today": today,
            "queue": queue_size(),
            "workers_online": workers_summary()["online"],
            "by_source": by_source,
        },
        "history": [dict(row) for row in recent],
    }
