#!/usr/bin/env python3
"""Step 20 — Download PSX corporate announcements and publish an FTS corpus.

The current PSX corporate-announcements page exposes ticker/date metadata through
an AJAX endpoint, while file links first return a tiny JavaScript redirect. This
step resolves that redirect deterministically, downloads the attachment, parses it
to markdown with Gemini, builds a compact BM25 discovery string, and emits scoped
SQL for D1.

This is an unstructured text corpus only. It does not perform structured
financial extraction, arithmetic derivation, or QC.

Usage:
    .venv/bin/python pipeline/20_publish_announcements.py --ticker MARI --date 2026-06-24
    .venv/bin/python pipeline/20_publish_announcements.py --days 7 --universe-only --download-only
    .venv/bin/python pipeline/20_publish_announcements.py --ticker MARI --days 30 --push-d1
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv
from google import genai
from google.genai import types

sys.path.insert(0, str(Path(__file__).resolve().parent))
load_dotenv()
from shared.constants import ARTIFACTS, PROJECT_ROOT  # noqa: E402
from shared.report import Action, emit_decision, write_report  # noqa: E402

BASE_PAGE = "https://www.psx.com.pk/psx/announcement/corporate-announcements"
SEARCH_URL = "https://www.psx.com.pk/psx/Announcement/corporate-search"
FILE_URL = "https://www.psx.com.pk/psx/file/{name}"
FILE_ATTACHMENT_URL = "https://www.psx.com.pk/psx/file-attachment/{name}"
DIRECT_FILE_URL = "https://www.psx.com.pk/psx/files/?{query}"
DIRECT_ATTACHMENT_URL = "https://www.psx.com.pk/psx/files-attachment/?{query}"

SQL_DIR = ARTIFACTS / "20_publish_announcements"
DOWNLOAD_ROOT = PROJECT_ROOT / "announcement_documents"
MARKDOWN_ROOT = PROJECT_ROOT / "announcement_markdown"
THUMB_ROOT = PROJECT_ROOT / "announcement_thumbnails"
ASSET_MANIFEST = ARTIFACTS / "20_publish_announcements" / "asset_manifest.json"
D1_DB = "psx"
TABLE = "psx_announcements"
FTS_TABLE = "psx_announcements_fts"
MODEL = os.getenv("GEMINI_PARSE_MODEL", "gemini-3.1-flash-lite")
GEMINI_TIMEOUT_MS = int(os.getenv("GEMINI_PARSE_TIMEOUT_MS", "120000"))
PARSE_PROMPT = (
    "Convert this PSX corporate announcement into clean GitHub-flavored markdown. "
    "Preserve all visible wording, tables, dates, names, and numbers exactly as printed. "
    "Do not add commentary or analysis. Output only markdown."
)
MAX_TEXT_CHARS = 60_000
MAX_SUMMARY_CHARS = 900
MAX_D1_TEXT_CHARS = 12_000
DOWNLOAD_TIMEOUT = (10, 30)
DOWNLOAD_CHUNK_BYTES = 256 * 1024
TARGET_WIDTH, JPEG_QUALITY, DPI = 360, 70, 110
R2_BUCKET = os.getenv("CLOUDFLARE_R2_BUCKET_NAME", "psx")
R2_ENDPOINT = os.getenv("CLOUDFLARE_R2_ENDPOINT", "")
R2_ACCESS_KEY_ID = os.getenv("CLOUDFLARE_R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.getenv("CLOUDFLARE_R2_SECRET_ACCESS_KEY", "")
_asset_lock = threading.Lock()
_asset_local = threading.local()


@dataclass(frozen=True)
class Announcement:
    ticker: str
    company_name: str
    announcement_date: str
    announcement_time: str
    title: str
    notice_status: str
    psx_id: int | None
    pdf_id: str
    attachment: str
    source_url: str
    direct_url: str
    local_path: str
    content_type: str
    bytes_downloaded: int
    text: str
    download_status: str = ""
    markdown_path: str = ""

    @property
    def metadata_tags(self) -> str:
        tags = [
            f"ticker:{self.ticker}",
            f"date:{self.announcement_date}",
        ]
        if self.announcement_date:
            tags.append(f"year:{self.announcement_date[:4]}")
        if self.company_name:
            tags.append(f"company:{self.company_name}")
        return " ".join(tags)

    @property
    def summary(self) -> str:
        return build_summary(self.title, self.text)

    @property
    def search_text(self) -> str:
        summary = self.summary
        parts = [
            f"Metadata: {self.metadata_tags}",
            f"Ticker: {self.ticker}",
            f"Date: {self.announcement_date}",
            f"Time: {self.announcement_time}" if self.announcement_time else "",
            f"Company: {self.company_name}" if self.company_name else "",
            f"Title: {self.title}",
            f"Notice status: {self.notice_status}" if self.notice_status else "",
            f"Content type: {self.content_type}" if self.content_type else "",
            f"Summary: {summary}" if summary else "",
        ]
        return "\n".join(p for p in parts if p)


def headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    out = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": BASE_PAGE,
    }
    if extra:
        out.update(extra)
    return out


def escape(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def sanitize(value: str) -> str:
    cleaned = re.sub(r"[^\w\-]+", "_", str(value).strip())
    return cleaned.strip("_") or "value"


def compact_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def strip_frontmatter(value: str) -> str:
    if value.startswith("---"):
        parts = value.split("---", 2)
        if len(parts) == 3:
            return parts[2].strip()
    return value


def truncate_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars].rsplit(" ", 1)[0] + "\n\n[truncated; full markdown is stored in announcement_markdown]"


def is_parse_failure_text(value: str) -> bool:
    return "Gemini parse failed:" in (value or "")


def build_summary(title: str, text: str) -> str:
    """Deterministic extractive summary for retrieval, not analysis.

    Announcements should stay as an unstructured source corpus. This summary
    intentionally avoids structured extraction; it gives BM25 a compact, clean
    representation of what the notice is about.
    """
    clean_title = compact_text(title)
    clean_text = compact_text(strip_frontmatter(text))
    if not clean_text:
        return clean_title
    if len(clean_text) <= MAX_SUMMARY_CHARS:
        body = clean_text
    else:
        body = clean_text[:MAX_SUMMARY_CHARS].rsplit(" ", 1)[0] + "..."
    if clean_title and clean_title.lower() not in body[:200].lower():
        return f"{clean_title}. {body}"
    return body


def load_universe_tickers() -> set[str]:
    path = PROJECT_ROOT / "tickers100.json"
    if not path.exists():
        return set()
    rows = json.loads(path.read_text())
    return {
        str(row.get("Symbol") or "").strip().upper()
        for row in rows
        if row.get("Symbol")
    }


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def date_range(start: date, end: date) -> Iterable[date]:
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def fetch_xid(session: requests.Session) -> str:
    response = session.get(BASE_PAGE, headers=headers({"Accept": "text/html"}), timeout=30)
    response.raise_for_status()
    match = re.search(r'id="XID"\s+value="([^"]+)"', response.text)
    if not match:
        raise RuntimeError("Could not find PSX XID token on corporate announcements page")
    return match.group(1)


def fetch_day(session: requests.Session, xid: str, target: date, attempts: int = 3) -> list[dict]:
    params = {
        "month": f"{target.month:02d}",
        "year": str(target.year),
        "day": f"{target.day:02d}",
        "XID": xid,
    }
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = session.get(
                SEARCH_URL,
                params=params,
                headers=headers({"X-Requested-With": "XMLHttpRequest"}),
                timeout=(10, 30),
            )
            response.raise_for_status()
            payload = response.json()
            break
        except Exception as exc:
            last_exc = exc
            if attempt < attempts:
                time.sleep(1.5 * attempt)
    else:
        raise RuntimeError(f"PSX announcement feed failed for {target}: {last_exc}") from last_exc
    if isinstance(payload, list):
        return []
    rows = payload.get("keyone") or []
    if not isinstance(rows, list):
        raise RuntimeError(f"Unexpected PSX response for {target}: keyone is not a list")
    return rows


def source_url(row: dict) -> str:
    attachment = str(row.get("attachment") or "").strip()
    pdf_id = str(row.get("pdfId") or "").strip()
    if attachment:
        return FILE_ATTACHMENT_URL.format(name=attachment)
    return FILE_URL.format(name=pdf_id)


def direct_url(row: dict) -> str:
    attachment = str(row.get("attachment") or "").strip()
    pdf_id = str(row.get("pdfId") or "").strip()
    if attachment:
        return DIRECT_ATTACHMENT_URL.format(query=urlencode({"file": attachment}))
    return DIRECT_FILE_URL.format(query=urlencode({"file": pdf_id}))


def extension_for(name: str, content_type: str) -> str:
    suffix = Path(name).suffix.lower()
    if suffix:
        return suffix
    guessed = mimetypes.guess_extension(content_type.split(";")[0].strip())
    return guessed or ".bin"


def announcement_filename(row: dict, content_type: str) -> str:
    ticker = sanitize(str(row.get("symbolCode") or "OTHER").upper())
    dt = sanitize(str(row.get("date") or "unknown"))
    tm = sanitize(str(row.get("time") or ""))
    psx_id = sanitize(str(row.get("id") or row.get("pdfId") or "unknown"))
    name = str(row.get("attachment") or row.get("pdfId") or "")
    ext = extension_for(name, content_type)
    return f"{ticker}_{dt}_{tm}_{psx_id}{ext}"


def download_file(session: requests.Session, row: dict) -> tuple[Path, str, int, str]:
    url = direct_url(row)
    ticker = sanitize(str(row.get("symbolCode") or "OTHER").upper())
    year = str(row.get("date") or date.today().isoformat())[:4]
    out_dir = DOWNLOAD_ROOT / ticker / year
    out_dir.mkdir(parents=True, exist_ok=True)
    name = str(row.get("attachment") or row.get("pdfId") or "")
    if name.lower().endswith(".pdf"):
        content_type = "application/pdf"
        out_path = out_dir / announcement_filename(row, content_type)
        if out_path.exists() and out_path.stat().st_size > 0:
            return out_path, content_type, out_path.stat().st_size, "existing"

    with session.get(url, headers=headers({"Accept": "*/*"}), timeout=DOWNLOAD_TIMEOUT, stream=True) as response:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "application/octet-stream")
        out_path = out_dir / announcement_filename(row, content_type)
        if out_path.exists() and out_path.stat().st_size > 0:
            return out_path, content_type, out_path.stat().st_size, "existing"
        tmp_path = out_path.with_suffix(out_path.suffix + ".part")
        total = 0
        with tmp_path.open("wb") as fh:
            for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_BYTES):
                if not chunk:
                    continue
                fh.write(chunk)
                total += len(chunk)
        tmp_path.replace(out_path)
    return out_path, content_type, total, "downloaded"


def is_pdf_download(path: Path, content_type: str) -> bool:
    return "pdf" in content_type.lower() or path.suffix.lower() == ".pdf"


def markdown_for(row: dict, local_path: Path) -> Path:
    ticker = sanitize(str(row.get("symbolCode") or "OTHER").upper())
    year = str(row.get("date") or date.today().isoformat())[:4]
    return MARKDOWN_ROOT / ticker / year / f"{local_path.stem}.md"


def markdown_frontmatter(row: dict, source_path: Path, content_type: str) -> str:
    meta = {
        "ticker": str(row.get("symbolCode") or "").strip().upper(),
        "date": str(row.get("date") or ""),
        "time": str(row.get("time") or ""),
        "company": str(row.get("companyName") or "").strip(),
        "title": str(row.get("title") or "").strip(),
        "psx_id": row.get("id"),
        "source_url": source_url(row),
        "local_path": str(source_path.relative_to(PROJECT_ROOT)),
        "content_type": content_type,
        "parser_method": "gemini",
        "parser_model": MODEL,
    }
    lines = ["---"]
    for key, value in meta.items():
        value = "" if value is None else str(value).replace("\n", " ").strip()
        lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def gemini_parse(path: Path, content_type: str, client: genai.Client | None) -> str:
    if client is None:
        return ""
    mime = content_type.split(";")[0].strip() or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    try:
        resp = client.models.generate_content(
            model=MODEL,
            contents=[
                PARSE_PROMPT,
                types.Part.from_bytes(data=path.read_bytes(), mime_type=mime),
            ],
        )
        return (resp.text or "").strip()
    except Exception:
        return ""


def is_current_gemini_markdown(path: Path) -> bool:
    try:
        head = path.read_text(errors="ignore")[:1200]
    except OSError:
        return False
    if "Gemini parse failed:" in head:
        return False
    return "parser_method: gemini" in head and f"parser_model: {MODEL}" in head


def parse_attachment_to_markdown(
    row: dict,
    source_path: Path,
    content_type: str,
    client: genai.Client | None,
    reset: bool,
) -> tuple[Path, str, str]:
    out = markdown_for(row, source_path)
    if not reset and out.exists() and out.stat().st_size > 0:
        body = out.read_text(errors="ignore")
        if is_current_gemini_markdown(out):
            return out, body, "existing"

    body = gemini_parse(source_path, content_type, client)
    method = "gemini" if body.strip() else "gemini_failed"
    if not body.strip():
        body = "[Gemini parse failed: no markdown text was returned for this announcement attachment.]"
    markdown = markdown_frontmatter(row, source_path, content_type) + body.strip() + "\n"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(markdown)
    return out, markdown, method


def make_announcement(
    row: dict,
    local_path: str = "",
    content_type: str = "",
    size: int = 0,
    text: str = "",
    download_status: str = "",
    markdown_path: str = "",
) -> Announcement:
    row_ticker = str(row.get("symbolCode") or "").strip().upper()
    return Announcement(
        ticker=row_ticker,
        company_name=str(row.get("companyName") or "").strip(),
        announcement_date=str(row.get("date") or ""),
        announcement_time=str(row.get("time") or ""),
        title=str(row.get("title") or "").strip(),
        notice_status=str(row.get("noticeStatus") or "").strip(),
        psx_id=int(row["id"]) if str(row.get("id") or "").isdigit() else None,
        pdf_id=str(row.get("pdfId") or "").strip(),
        attachment=str(row.get("attachment") or "").strip(),
        source_url=source_url(row),
        direct_url=direct_url(row),
        local_path=local_path,
        content_type=content_type,
        bytes_downloaded=size,
        text=text,
        download_status=download_status,
        markdown_path=markdown_path,
    )


def download_announcement(
    row: dict,
    pdf_only: bool,
    parse_markdown: bool,
    reset_markdown: bool,
    client: genai.Client | None = None,
) -> tuple[str, Announcement | None, str]:
    session = requests.Session()
    path, content_type, size, download_status = download_file(session, row)
    if pdf_only and not is_pdf_download(path, content_type):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return "non_pdf", None, ""
    text = ""
    markdown_path = ""
    parse_method = ""
    if parse_markdown:
        if client is None:
            client = genai.Client(
                api_key=os.environ["GEMINI_API_KEY"],
                http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_MS),
            )
        md_path, text, parse_method = parse_attachment_to_markdown(
            row,
            path,
            content_type,
            client=client,
            reset=reset_markdown,
        )
        markdown_path = str(md_path.relative_to(PROJECT_ROOT))
    return "ok", make_announcement(
        row,
        local_path=str(path.relative_to(PROJECT_ROOT)),
        content_type=content_type,
        size=size,
        text=text,
        download_status=download_status,
        markdown_path=markdown_path,
    ), parse_method


def collect_announcements(
    ticker: str | None,
    start: date,
    end: date,
    download: bool,
    pdf_only: bool,
    universe_only: bool,
    parse_markdown: bool,
    reset_markdown: bool,
    workers: int,
    limit: int | None,
) -> tuple[list[Announcement], dict]:
    session = requests.Session()
    xid = fetch_xid(session)
    wanted = ticker.upper() if ticker else None
    universe = load_universe_tickers() if universe_only else set()
    announcements: list[Announcement] = []
    candidates: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    stats = {
        "feed_rows_seen": 0,
        "duplicates_skipped": 0,
        "non_pdf_skipped": 0,
        "download_failures": 0,
        "existing_files": 0,
        "new_downloads": 0,
        "out_of_universe_skipped": 0,
        "date_fetch_failures": 0,
        "failed_dates": [],
        "markdown_parsed": 0,
        "markdown_existing": 0,
        "markdown_methods": {},
    }
    parse_methods: Counter = Counter()
    for target in date_range(start, end):
        try:
            day_rows = fetch_day(session, xid, target)
        except Exception as exc:
            stats["date_fetch_failures"] += 1
            stats["failed_dates"].append({"date": target.isoformat(), "error": str(exc)})
            continue
        for row in day_rows:
            row_ticker = str(row.get("symbolCode") or "").strip().upper()
            if wanted and row_ticker != wanted:
                continue
            if universe_only and row_ticker not in universe:
                stats["out_of_universe_skipped"] += 1
                continue
            stats["feed_rows_seen"] += 1
            key = (row_ticker, str(row.get("date") or ""), str(row.get("id") or row.get("pdfId") or ""))
            if key in seen:
                stats["duplicates_skipped"] += 1
                continue
            seen.add(key)
            if download:
                if pdf_only:
                    name = str(row.get("attachment") or row.get("pdfId") or "").lower()
                    if not name.endswith(".pdf"):
                        stats["non_pdf_skipped"] += 1
                        continue
                candidates.append(row)
                if limit and len(candidates) >= limit:
                    break
                continue
            elif pdf_only:
                name = str(row.get("attachment") or row.get("pdfId") or "").lower()
                if not name.endswith(".pdf"):
                    stats["non_pdf_skipped"] += 1
                    continue
            announcements.append(make_announcement(row))
            if limit and len(announcements) >= limit:
                break
        if limit and ((download and len(candidates) >= limit) or (not download and len(announcements) >= limit)):
            break

    if download and candidates:
        max_workers = max(1, workers)
        client = genai.Client(
            api_key=os.environ["GEMINI_API_KEY"],
            http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_MS),
        ) if parse_markdown else None
        completed = 0
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [
                pool.submit(download_announcement, row, pdf_only, parse_markdown, reset_markdown, client)
                for row in candidates
            ]
            for future in as_completed(futures):
                completed += 1
                try:
                    status, announcement, parse_method = future.result()
                except Exception:
                    stats["download_failures"] += 1
                    continue
                if status == "non_pdf":
                    stats["non_pdf_skipped"] += 1
                    continue
                if announcement is None:
                    stats["download_failures"] += 1
                    continue
                if announcement.download_status == "existing":
                    stats["existing_files"] += 1
                elif announcement.download_status == "downloaded":
                    stats["new_downloads"] += 1
                if parse_method:
                    parse_methods[parse_method] += 1
                    if parse_method == "existing":
                        stats["markdown_existing"] += 1
                    else:
                        stats["markdown_parsed"] += 1
                announcements.append(announcement)
                if parse_markdown and (completed == len(futures) or completed % 25 == 0):
                    print(
                        f"announcement parse progress: {completed}/{len(futures)} "
                        f"methods={dict(sorted(parse_methods.items()))}",
                        flush=True,
                    )
    stats["markdown_methods"] = dict(sorted(parse_methods.items()))
    announcements.sort(key=lambda r: (r.announcement_date, r.announcement_time, r.ticker, r.psx_id or 0))
    return announcements, stats


def scope_where(ticker: str | None, start: date, end: date) -> str:
    clauses = [
        f"announcement_date >= '{start.isoformat()}'",
        f"announcement_date <= '{end.isoformat()}'",
    ]
    if ticker:
        clauses.append(f"ticker = '{ticker.upper()}'")
    return " AND ".join(clauses)


def make_insert(row: Announcement) -> str:
    columns = [
        "ticker",
        "company_name",
        "announcement_date",
        "announcement_time",
        "title",
        "notice_status",
        "psx_id",
        "pdf_id",
        "attachment",
        "source_url",
        "direct_url",
        "local_path",
        "content_type",
        "bytes_downloaded",
        "search_text",
        "text",
        "ingested_at",
    ]
    payload = {
        "ticker": row.ticker,
        "company_name": row.company_name,
        "announcement_date": row.announcement_date,
        "announcement_time": row.announcement_time,
        "title": row.title,
        "notice_status": row.notice_status,
        "psx_id": row.psx_id,
        "pdf_id": row.pdf_id,
        "attachment": row.attachment,
        "source_url": row.source_url,
        "direct_url": row.direct_url,
        "local_path": row.local_path,
        "content_type": row.content_type,
        "bytes_downloaded": row.bytes_downloaded,
        "search_text": row.search_text,
        "text": truncate_text(row.text, MAX_D1_TEXT_CHARS),
        "ingested_at": datetime.now(timezone.utc).isoformat(),
    }
    cols = ", ".join(f'"{col}"' for col in columns)
    vals = ", ".join(escape(payload[col]) for col in columns)
    return f"INSERT INTO {TABLE} ({cols}) VALUES ({vals});"


def generate_sql(rows: list[Announcement], ticker: str | None, start: date, end: date) -> Path:
    SQL_DIR.mkdir(parents=True, exist_ok=True)
    scope = f"_{ticker.upper()}" if ticker else ""
    out = SQL_DIR / f"psx_announcements{scope}_{start.isoformat()}_{end.isoformat()}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.sql"
    where = scope_where(ticker, start, end)
    with out.open("w") as f:
        f.write(f"-- D1 upload: {TABLE}\n")
        f.write(f"-- Generated: {datetime.now(timezone.utc).isoformat()}\n")
        f.write(f"-- Scope: {where}\n")
        f.write(f"-- Rows: {len(rows)}\n\n")
        f.write("-- Unstructured corpus only: ticker/date metadata, retrieval summary, extracted text.\n")
        f.write("-- No structured extraction, derivation, or QC is performed in this step.\n\n")
        f.write(f"CREATE TABLE IF NOT EXISTS {TABLE} (\n")
        f.write("  id INTEGER PRIMARY KEY AUTOINCREMENT,\n")
        f.write("  ticker TEXT NOT NULL,\n")
        f.write("  company_name TEXT,\n")
        f.write("  announcement_date TEXT NOT NULL,\n")
        f.write("  announcement_time TEXT,\n")
        f.write("  title TEXT NOT NULL,\n")
        f.write("  notice_status TEXT,\n")
        f.write("  psx_id INTEGER,\n")
        f.write("  pdf_id TEXT,\n")
        f.write("  attachment TEXT,\n")
        f.write("  source_url TEXT NOT NULL,\n")
        f.write("  direct_url TEXT NOT NULL,\n")
        f.write("  local_path TEXT,\n")
        f.write("  content_type TEXT,\n")
        f.write("  bytes_downloaded INTEGER,\n")
        f.write("  search_text TEXT,\n")
        f.write("  text TEXT,\n")
        f.write("  ingested_at TEXT\n")
        f.write(");\n")
        f.write(f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{TABLE}_source_url ON {TABLE}(source_url);\n")
        f.write(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_ticker_date ON {TABLE}(ticker, announcement_date);\n")
        f.write(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} "
            "USING fts5(ticker, title, search_text, text);\n\n"
        )
        f.write(f"DELETE FROM {FTS_TABLE} WHERE rowid IN (SELECT id FROM {TABLE} WHERE {where});\n")
        f.write(f"DELETE FROM {TABLE} WHERE {where};\n\n")
        for row in rows:
            f.write(make_insert(row) + "\n")
        f.write("\n")
        f.write(
            f"INSERT INTO {FTS_TABLE}(rowid, ticker, title, search_text, text) "
            f"SELECT id, ticker, title, search_text, text FROM {TABLE} WHERE {where};\n"
        )
    return out


def write_rows_jsonl(rows: list[Announcement], ticker: str | None, start: date, end: date) -> Path:
    SQL_DIR.mkdir(parents=True, exist_ok=True)
    scope = f"_{ticker.upper()}" if ticker else ""
    out = SQL_DIR / f"psx_announcements{scope}_{start.isoformat()}_{end.isoformat()}.jsonl"
    with out.open("w") as fh:
        for row in rows:
            payload = {
                **row.__dict__,
                "metadata_tags": row.metadata_tags,
                "summary": row.summary,
                "search_text": row.search_text,
            }
            fh.write(json.dumps(payload, sort_keys=True, default=str) + "\n")
    return out


def run_wrangler_d1(sql_path: Path) -> bool:
    serve_analysis = PROJECT_ROOT.parent / "psxgpt-serve" / "apps" / "analysis"
    if not serve_analysis.exists():
        print(f"Could not find serve wrangler context: {serve_analysis}", file=sys.stderr)
        return False
    res = subprocess.run(
        ["pnpm", "--dir", str(serve_analysis), "exec", "wrangler", "d1", "execute", D1_DB, "--remote", f"--file={sql_path}"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    if res.returncode != 0:
        print(res.stderr[-4000:])
        return False
    print(res.stdout[-2000:])
    return True


def is_pdf_asset(row: Announcement) -> bool:
    return row.local_path.lower().endswith(".pdf") or "pdf" in row.content_type.lower()


def asset_content_type(path: Path, content_type: str) -> str:
    clean = content_type.split(";")[0].strip().lower()
    if clean and clean != "application/octet-stream":
        return clean
    guessed = mimetypes.guess_type(path.name)[0]
    return guessed or "application/octet-stream"


def announcement_asset_rel(local_path: str) -> Path:
    path = Path(local_path)
    parts = path.parts
    if parts and parts[0] == "announcement_documents":
        return Path(*parts[1:])
    return path


def render_announcement_thumbnail(pdf_path: Path, thumb_path: Path, force: bool = False) -> str:
    if thumb_path.exists() and not force:
        return "skip"
    try:
        from pdf2image import convert_from_path
        from PIL import Image
        thumb_path.parent.mkdir(parents=True, exist_ok=True)
        imgs = convert_from_path(pdf_path, dpi=DPI, first_page=1, last_page=1, fmt="jpeg")
        if not imgs:
            return f"fail {pdf_path}: no image"
        img = imgs[0].convert("RGB")
        w, h = img.size
        img.resize((TARGET_WIDTH, int(TARGET_WIDTH * h / w)), Image.Resampling.LANCZOS).save(
            thumb_path, "JPEG", quality=JPEG_QUALITY, optimize=True
        )
        return "ok"
    except Exception as exc:
        return f"fail {pdf_path}: {exc}"


def r2_client():
    if not hasattr(_asset_local, "client"):
        try:
            import boto3
            from botocore.config import Config
        except ModuleNotFoundError as exc:
            raise RuntimeError("boto3/botocore are required for announcement R2 upload") from exc
        _asset_local.client = boto3.client(
            "s3",
            endpoint_url=R2_ENDPOINT,
            aws_access_key_id=R2_ACCESS_KEY_ID,
            aws_secret_access_key=R2_SECRET_ACCESS_KEY,
            region_name="auto",
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"max_attempts": 5, "mode": "standard"},
                max_pool_connections=50,
            ),
        )
    return _asset_local.client


def load_asset_manifest() -> set[str]:
    if ASSET_MANIFEST.exists():
        try:
            return set(json.loads(ASSET_MANIFEST.read_text()).get("uploaded_keys", []))
        except Exception:
            pass
    return set()


def save_asset_manifest(keys: set[str]) -> None:
    ASSET_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    ASSET_MANIFEST.write_text(json.dumps({"count": len(keys), "uploaded_keys": sorted(keys)}, indent=2))


def local_announcement_asset_rows() -> list[Announcement]:
    if not DOWNLOAD_ROOT.exists():
        return []
    rows: list[Announcement] = []
    for path in DOWNLOAD_ROOT.glob("*/*/*"):
        if not path.is_file():
            continue
        rel = path.relative_to(PROJECT_ROOT)
        try:
            ticker = path.relative_to(DOWNLOAD_ROOT).parts[0].upper()
        except Exception:
            ticker = ""
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        rows.append(make_announcement(
            {"symbolCode": ticker},
            local_path=str(rel),
            content_type=content_type,
            size=path.stat().st_size,
            text="",
            download_status="local_asset",
            markdown_path="",
        ))
    return rows


def merge_asset_rows(rows: list[Announcement]) -> list[Announcement]:
    merged: dict[str, Announcement] = {}
    for row in rows:
        if row.local_path:
            merged[row.local_path] = row
    for row in local_announcement_asset_rows():
        if row.local_path and row.local_path not in merged:
            merged[row.local_path] = row
    return list(merged.values())


def upload_announcement_asset(row: Announcement, existing: set[str], force_thumbs: bool) -> tuple[int, int, str, list[str]]:
    if not row.local_path:
        return 0, 0, "", []
    rel = announcement_asset_rel(row.local_path)
    asset_path = PROJECT_ROOT / row.local_path
    thumb_rel = rel.with_suffix(".jpg")
    thumb_path = THUMB_ROOT / thumb_rel
    if not asset_path.exists():
        return 0, 0, f"{row.local_path}: missing local announcement asset", []
    uploads = [(f"ANNOUNCEMENTS/{rel.as_posix()}", asset_path, asset_content_type(asset_path, row.content_type))]
    thumb_error = ""
    if is_pdf_asset(row):
        thumb_status = render_announcement_thumbnail(asset_path, thumb_path, force=force_thumbs)
        if thumb_status.startswith("fail"):
            thumb_error = thumb_status
        else:
            uploads.append((f"ANNOUNCEMENT_THUMBNAILS/{thumb_rel.as_posix()}", thumb_path, "image/jpeg"))
    up = skip = 0
    written: list[str] = []
    try:
        for key, path, content_type in uploads:
            if key in existing:
                skip += 1
                continue
            if not path.exists():
                return up, skip, f"{key}: missing local file", written
            r2_client().upload_file(str(path), R2_BUCKET, key, ExtraArgs={"ContentType": content_type})
            up += 1
            written.append(key)
        return up, skip, thumb_error, written
    except Exception as exc:
        return up, skip, f"{rel.as_posix()}: {exc}", written


def publish_announcement_assets(rows: list[Announcement], workers: int, force_thumbs: bool = False) -> dict:
    asset_rows = [row for row in merge_asset_rows(rows) if row.local_path]
    pdf_rows = [row for row in asset_rows if is_pdf_asset(row)]
    non_pdf_rows = [row for row in asset_rows if not is_pdf_asset(row)]
    if not asset_rows:
        return {"asset_rows": 0, "asset_pdf_rows": 0, "asset_non_pdf_rows": 0, "asset_uploaded": 0, "asset_skipped": 0, "asset_errors": 0}
    if not (R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY and R2_ENDPOINT):
        raise SystemExit("missing CLOUDFLARE_R2_ACCESS_KEY_ID / SECRET_ACCESS_KEY / ENDPOINT in env")

    existing = load_asset_manifest()
    uploaded = skipped = done = 0
    errors: list[str] = []
    new_keys: set[str] = set()
    width = max(1, workers)
    with ThreadPoolExecutor(max_workers=width) as ex:
        futs = {ex.submit(upload_announcement_asset, row, existing, force_thumbs): row for row in asset_rows}
        for fut in as_completed(futs):
            up, skip, err, keys = fut.result()
            uploaded += up
            skipped += skip
            done += 1
            new_keys.update(keys)
            if err:
                errors.append(err)
            if done % 500 == 0:
                with _asset_lock:
                    save_asset_manifest(existing | new_keys)
                print(f"  announcement assets {done}/{len(asset_rows)} uploaded={uploaded} skipped={skipped} errors={len(errors)}", flush=True)
    save_asset_manifest(existing | new_keys)
    print(f"announcement assets: rows={len(asset_rows)} pdfs={len(pdf_rows)} non_pdfs={len(non_pdf_rows)} uploaded={uploaded} skipped={skipped} errors={len(errors)}")
    for error in errors[:10]:
        print(f"  {error}")
    return {
        "asset_rows": len(asset_rows),
        "asset_pdf_rows": len(pdf_rows),
        "asset_non_pdf_rows": len(non_pdf_rows),
        "asset_uploaded": uploaded,
        "asset_skipped": skipped,
        "asset_errors": len(errors),
        "asset_error_samples": errors[:10],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Download PSX corporate announcements and publish psx_announcements rows")
    ap.add_argument("--ticker", help="scope to one PSX ticker")
    ap.add_argument("--date", help="single announcement date, YYYY-MM-DD")
    ap.add_argument("--start-date", help="start date, YYYY-MM-DD")
    ap.add_argument("--end-date", help="end date, YYYY-MM-DD")
    ap.add_argument("--days", type=int, default=1, help="look back this many calendar days when no explicit date range is supplied")
    ap.add_argument("--metadata-only", action="store_true", help="do not download attachments or extract text")
    ap.add_argument("--download-only", action="store_true", help="download scoped source files and write report/jsonl; do not generate SQL or push D1")
    ap.add_argument("--parse-markdown", action="store_true", help="parse downloaded attachments into announcement_markdown and use markdown as table text")
    ap.add_argument("--reset-markdown", action="store_true", help="overwrite existing announcement markdown")
    ap.add_argument("--pdf-only", action="store_true", help="debug filter: keep only PDF announcements; by default all attachment formats are kept")
    ap.add_argument("--universe-only", action="store_true", help="keep only tickers present in tickers100.json")
    ap.add_argument("--workers", type=int, default=8, help="parallel download workers for attachment files")
    ap.add_argument("--limit", type=int, help="limit rows for scoped parse/publish batches")
    ap.add_argument("--include-parse-failures", action="store_true", help="publish Gemini parse failure placeholders instead of skipping them")
    ap.add_argument("--push-d1", action="store_true", help="push generated SQL to remote D1")
    ap.add_argument("--upload-assets", action="store_true", help="upload announcement PDFs and first-page thumbnails to R2")
    ap.add_argument("--force-thumbs", action="store_true", help="regenerate announcement thumbnails before upload")
    a = ap.parse_args()

    if a.date:
        start = end = parse_date(a.date)
    elif a.start_date or a.end_date:
        start = parse_date(a.start_date) if a.start_date else parse_date(a.end_date)
        end = parse_date(a.end_date) if a.end_date else start
    else:
        end = date.today()
        start = end - timedelta(days=max(a.days - 1, 0))

    if start > end:
        raise SystemExit("--start-date cannot be after --end-date")
    if a.push_d1 and not (a.ticker or a.date or a.start_date or a.end_date):
        raise SystemExit("--push-d1 requires --ticker or an explicit date/date range")
    if a.push_d1 and a.limit:
        raise SystemExit("--push-d1 cannot be combined with --limit; limited batches would only partially replace the scoped date/ticker rows")

    ticker = a.ticker.upper() if a.ticker else None
    rows, collection_stats = collect_announcements(
        ticker,
        start,
        end,
        download=not a.metadata_only,
        pdf_only=a.pdf_only,
        universe_only=a.universe_only,
        parse_markdown=a.parse_markdown or not a.download_only,
        reset_markdown=a.reset_markdown,
        workers=a.workers,
        limit=a.limit,
    )
    parse_failure_rows = [row for row in rows if is_parse_failure_text(row.text)]
    publish_rows = rows
    if not a.download_only and parse_failure_rows and not a.include_parse_failures:
        publish_rows = [row for row in rows if not is_parse_failure_text(row.text)]
    jsonl_path = write_rows_jsonl(rows, ticker, start, end)
    sql_path = None
    pushed = False
    if not a.download_only:
        sql_path = generate_sql(publish_rows, ticker, start, end)
        pushed = run_wrangler_d1(sql_path) if a.push_d1 else False
    asset_report = {}
    if a.upload_assets:
        asset_report = publish_announcement_assets(rows, workers=max(1, a.workers), force_thumbs=a.force_thumbs)

    pdf_rows = sum(1 for row in rows if "pdf" in row.content_type.lower() or row.local_path.endswith(".pdf"))
    text_rows = sum(1 for row in rows if row.text.strip())
    suffix_counts = Counter(Path(row.local_path).suffix.lower() or "none" for row in rows if row.local_path)
    content_type_counts = Counter(row.content_type or "unknown" for row in rows)
    report = {
        "ticker": ticker,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "rows": len(rows),
        "publish_rows": len(publish_rows),
        "parse_failure_rows": len(parse_failure_rows),
        "parse_failures_skipped_from_sql": (not a.download_only and not a.include_parse_failures and len(parse_failure_rows) > 0),
        "pdf_rows": pdf_rows,
        "text_rows": text_rows,
        "downloaded_bytes": sum(row.bytes_downloaded for row in rows),
        "file_suffix_counts": dict(sorted(suffix_counts.items())),
        "content_type_counts": dict(sorted(content_type_counts.items())),
        **collection_stats,
        "metadata_only": a.metadata_only,
        "download_only": a.download_only,
        "parse_markdown": a.parse_markdown or not a.download_only,
        "reset_markdown": a.reset_markdown,
        "pdf_only": a.pdf_only,
        "universe_only": a.universe_only,
        "workers": a.workers,
        "limit": a.limit,
        "include_parse_failures": a.include_parse_failures,
        "upload_assets": a.upload_assets,
        **asset_report,
        "sql_path": str(sql_path.relative_to(PROJECT_ROOT)) if sql_path else None,
        "jsonl_path": str(jsonl_path.relative_to(PROJECT_ROOT)),
        "sample_rows": [
            {
                "ticker": row.ticker,
                "date": row.announcement_date,
                "time": row.announcement_time,
                "title": row.title,
                "source_url": row.source_url,
                "local_path": row.local_path,
                "content_type": row.content_type,
                "markdown_path": row.markdown_path,
                "metadata_tags": row.metadata_tags,
                "summary": row.summary,
                "text_chars": len(row.text),
                "download_status": row.download_status,
            }
            for row in rows[:5]
        ],
        "d1_pushed": pushed,
    }
    write_report("20_publish_announcements", report)
    if a.download_only:
        noun = "PDFs" if a.pdf_only else "files"
        emit_decision(
            "20_publish_announcements",
            f"Downloaded {pdf_rows if a.pdf_only else len(rows)} PSX announcement {noun} for {ticker or 'all tickers'} ({start.isoformat()} to {end.isoformat()}); D1 was not touched.",
            actions=[
                Action("Generate announcement SQL for same scope", f".venv/bin/python pipeline/20_publish_announcements.py {'--ticker ' + ticker + ' ' if ticker else ''}--start-date {start.isoformat()} --end-date {end.isoformat()} {'--pdf-only ' if a.pdf_only else ''}"),
            ],
        )
        return 0
    emit_decision(
        "20_publish_announcements",
        f"Prepared {len(publish_rows)} PSX announcement rows for {ticker or 'all tickers'} ({start.isoformat()} to {end.isoformat()}); skipped {len(parse_failure_rows) if not a.include_parse_failures else 0} parse failures.",
        actions=[
            Action("Push scoped announcement rows", f".venv/bin/python pipeline/20_publish_announcements.py --ticker {ticker or 'SYMBOL'} --date {start.isoformat()} --push-d1"),
            Action("Query announcement table", f"pnpm --dir ../psxgpt-serve/apps/analysis exec wrangler d1 execute psx --remote --json --command \"SELECT ticker, announcement_date, title FROM {TABLE} WHERE ticker='{ticker or 'SYMBOL'}' ORDER BY announcement_date DESC LIMIT 5;\""),
        ],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
