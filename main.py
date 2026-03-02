import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode, urlparse

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from dotenv import load_dotenv


load_dotenv()

APP_TITLE = "Shopee Link Converter"
AFFILIATE_ID = os.getenv("AFFILIATE_ID", "17322940169").strip()
SUB_ID = os.getenv("SUB_ID", "addlivetag----").strip()
ADMIN_KEY = os.getenv("ADMIN_KEY", "240905").strip()
DB_PATH = os.getenv("DB_PATH", "data/app.db").strip()
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "15"))


PRODUCT_PATTERN = re.compile(r"/product/(\d+)/(\d+)")
SLUG_PATTERN = re.compile(r"-i\.(\d+)\.(\d+)")


app = FastAPI(title=APP_TITLE)
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")


class ConvertRequest(BaseModel):
    input_url: str = Field(min_length=8, max_length=4096)


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


def require_admin_key(key: str) -> None:
    if key != ADMIN_KEY:
        raise HTTPException(status_code=401, detail="Sai admin key.")


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
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    normalized = ""
    resolved = None
    origin = None
    affiliate = None

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


@app.get("/admin", response_class=HTMLResponse)
def admin_page(
    request: Request,
    key: str = Query(default="", description="Admin key"),
) -> HTMLResponse:
    require_admin_key(key)
    return templates.TemplateResponse("admin.html", {"request": request, "key": key})


@app.get("/api/admin/stats")
def admin_stats(key: str = Query(default="")) -> dict:
    require_admin_key(key)
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
