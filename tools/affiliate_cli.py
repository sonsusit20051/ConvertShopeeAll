#!/usr/bin/env python3
import argparse
import re
import ssl
import sys
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import parse_qsl, quote, urlparse, urlunparse
from urllib.request import Request, urlopen

BASE_REDIRECT = "https://s.shopee.vn/an_redir"
URL_CANDIDATE_REGEX = re.compile(
    r"((?:https?://)?(?:[a-z0-9-]+\.)*(?:shopee\.[a-z.]{2,}|shope\.ee|shp\.ee)(?:/[^\s<>\"']*)?)",
    re.IGNORECASE,
)
PRODUCT_PATTERN = re.compile(r"/product/(\d+)/(\d+)")
SLUG_PATTERN = re.compile(r"-i\.(\d+)\.(\d+)")


def trim_trailing_punctuation(text: str) -> str:
    return re.sub(r"[),.;!?\]\\s]+$", "", str(text or ""))


def ensure_protocol(text: str) -> str:
    value = str(text or "").strip()
    if value.lower().startswith(("http://", "https://")):
        return value
    return f"https://{value}"


def is_shortlink_host(hostname: str) -> bool:
    host = str(hostname or "").lower()
    return host in ("shope.ee", "shp.ee") or host.endswith(".shp.ee") or host.startswith("s.shopee.")


def is_shopee_landing_host(hostname: str) -> bool:
    host = str(hostname or "").lower()
    return bool(re.match(r"^([a-z0-9-]+\.)*shopee\.[a-z.]{2,}$", host, re.IGNORECASE))


def extract_product_ids(path: str) -> Tuple[Optional[str], Optional[str]]:
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


def resolve_shortlink_url(raw_url: str, timeout_sec: float = 10.0) -> str:
    parsed = urlparse(str(raw_url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return str(raw_url or "")
    if not is_shortlink_host(parsed.hostname or ""):
        return str(raw_url or "")

    req = Request(
        urlunparse(parsed),
        method="GET",
        headers={
            "User-Agent": "Shopee-Affiliate-CLI/1.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )

    try:
        with urlopen(req, timeout=timeout_sec) as resp:
            return str(resp.geturl() or raw_url)
    except Exception:
        try:
            insecure_ctx = ssl._create_unverified_context()
            with urlopen(req, timeout=timeout_sec, context=insecure_ctx) as resp:
                return str(resp.geturl() or raw_url)
        except Exception:
            return str(raw_url or "")


def extract_product_ids_from_url(raw_url: str, resolve_shortlink: bool = False) -> Tuple[Optional[str], Optional[str]]:
    text = str(raw_url or "").strip()
    if not text:
        return None, None

    try:
        parsed = urlparse(text)
    except Exception:
        return None, None

    if not parsed.scheme or not parsed.netloc:
        return None, None

    if str(parsed.path or "").endswith("/an_redir"):
        origin = ""
        for k, v in parse_qsl(parsed.query, keep_blank_values=True):
            if k == "origin_link":
                origin = str(v or "").strip()
                break
        if origin:
            return extract_product_ids_from_url(origin, resolve_shortlink=resolve_shortlink)

    shop_id, item_id = extract_product_ids(parsed.path)
    if shop_id and item_id:
        return shop_id, item_id

    if resolve_shortlink and is_shortlink_host(parsed.hostname or ""):
        resolved = resolve_shortlink_url(text)
        if resolved and resolved != text:
            return extract_product_ids_from_url(resolved, resolve_shortlink=False)

    return None, None


def extract_single_candidate(raw_text: str) -> str:
    candidates = [trim_trailing_punctuation(m.group(1)) for m in URL_CANDIDATE_REGEX.finditer(str(raw_text or ""))]
    if not candidates:
        raise ValueError("Không tìm thấy link Shopee trong input.")
    if len(candidates) > 1:
        raise ValueError("Mỗi dòng chỉ cho phép 1 link Shopee.")
    return ensure_protocol(candidates[0])


def build_affiliate_link(product_url: str, affiliate_id: str, sub_id: str, resolve_shortlink: bool = False) -> str:
    affiliate = str(affiliate_id or "").strip()
    sub = str(sub_id or "").strip()
    if not affiliate:
        raise ValueError("Thiếu affiliate_id.")
    if not sub:
        raise ValueError("Thiếu sub_id.")

    normalized = extract_single_candidate(product_url)
    parsed = urlparse(normalized)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("Link không hợp lệ.")

    host = str(parsed.hostname or "")
    if not (is_shortlink_host(host) or is_shopee_landing_host(host)):
        raise ValueError("Domain không được hỗ trợ.")

    shop_id, item_id = extract_product_ids_from_url(normalized, resolve_shortlink=resolve_shortlink)
    if not shop_id or not item_id:
        raise ValueError("Không trích xuất được shop_id/item_id.")

    origin = f"https://shopee.vn/product/{shop_id}/{item_id}"
    return (
        f"{BASE_REDIRECT}?origin_link={quote(origin, safe='')}"
        f"&affiliate_id={quote(affiliate, safe='')}"
        f"&sub_id={quote(sub, safe='')}"
    )


def upsert_key(lines: list[str], key: str, value: str) -> list[str]:
    target = f"{key}="
    next_lines = []
    found = False
    for line in lines:
        if line.startswith(target):
            next_lines.append(f"{key}={value}")
            found = True
        else:
            next_lines.append(line)
    if not found:
        next_lines.append(f"{key}={value}")
    return next_lines


def cmd_convert(args: argparse.Namespace) -> int:
    try:
        result = build_affiliate_link(
            args.url,
            affiliate_id=args.affiliate_id,
            sub_id=args.sub_id,
            resolve_shortlink=bool(args.resolve_shortlink),
        )
        print(result)
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def cmd_batch(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    output_path = Path(args.output)
    if not input_path.exists():
        print(f"ERROR: Không tìm thấy file input: {input_path}", file=sys.stderr)
        return 1

    rows = input_path.read_text(encoding="utf-8").splitlines()
    out_rows = []
    ok_count = 0
    fail_count = 0

    for idx, raw in enumerate(rows, start=1):
        line = str(raw or "").strip()
        if not line or line.startswith("#"):
            continue
        try:
            aff = build_affiliate_link(
                line,
                affiliate_id=args.affiliate_id,
                sub_id=args.sub_id,
                resolve_shortlink=bool(args.resolve_shortlink),
            )
            out_rows.append(f"{line}\t{aff}")
            ok_count += 1
        except Exception as exc:
            out_rows.append(f"{line}\tERROR: {exc}")
            fail_count += 1
            print(f"[line {idx}] ERROR: {exc}", file=sys.stderr)

    output_path.write_text("\n".join(out_rows) + ("\n" if out_rows else ""), encoding="utf-8")
    print(f"DONE: success={ok_count}, failed={fail_count}, output={output_path}")
    return 0 if fail_count == 0 else 2


def cmd_set_env(args: argparse.Namespace) -> int:
    env_path = Path(args.env)
    lines = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()

    lines = upsert_key(lines, "AFFILIATE_ID", str(args.affiliate_id).strip())
    lines = upsert_key(lines, "AFFILIATE_ID_YT", str(args.affiliate_id_yt or args.affiliate_id).strip())
    if args.sub_id:
        lines = upsert_key(lines, "SUB_ID", str(args.sub_id).strip())
    if args.sub_id_yt:
        lines = upsert_key(lines, "SUB_ID_YT", str(args.sub_id_yt).strip())

    env_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    print(f"UPDATED: {env_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="CLI đổi affiliate id/link Shopee không cần chạy backend/worker.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_convert = sub.add_parser("convert", help="Đổi 1 link sản phẩm sang affiliate link")
    p_convert.add_argument("--url", required=True, help="Link sản phẩm Shopee")
    p_convert.add_argument("--affiliate-id", required=True, help="Affiliate ID")
    p_convert.add_argument("--sub-id", default="cvweb", help="Sub ID (default: cvweb)")
    p_convert.add_argument("--resolve-shortlink", action="store_true", help="Resolve shortlink trước khi convert")
    p_convert.set_defaults(func=cmd_convert)

    p_batch = sub.add_parser("batch", help="Đổi nhiều link (mỗi dòng 1 link)")
    p_batch.add_argument("--input", required=True, help="File input txt")
    p_batch.add_argument("--output", required=True, help="File output txt")
    p_batch.add_argument("--affiliate-id", required=True, help="Affiliate ID")
    p_batch.add_argument("--sub-id", default="cvweb", help="Sub ID (default: cvweb)")
    p_batch.add_argument("--resolve-shortlink", action="store_true", help="Resolve shortlink trước khi convert")
    p_batch.set_defaults(func=cmd_batch)

    p_env = sub.add_parser("set-env", help="Đổi affiliate id mặc định trong file .env")
    p_env.add_argument("--env", default=".env", help="Đường dẫn file env (default: .env)")
    p_env.add_argument("--affiliate-id", required=True, help="Giá trị mới cho AFFILIATE_ID")
    p_env.add_argument("--affiliate-id-yt", default="", help="Giá trị mới cho AFFILIATE_ID_YT")
    p_env.add_argument("--sub-id", default="", help="Giá trị mới cho SUB_ID")
    p_env.add_argument("--sub-id-yt", default="", help="Giá trị mới cho SUB_ID_YT")
    p_env.set_defaults(func=cmd_set_env)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
