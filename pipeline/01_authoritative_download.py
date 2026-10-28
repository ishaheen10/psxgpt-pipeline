#!/usr/bin/env python3
"""Download immutable PDFs from configured authoritative publication pages.

This is the generic Step 01 collector for non-company sources that publish
documents on websites without a stable public API. Website-specific knowledge
belongs in config/authoritative_sources.json; downloaded bytes and crawl state
belong in the manifest.

Outputs:
    authoritative_documents/{country}/{source}/{collection}/...pdf
    artifacts/01_authoritative_download/manifest.json
    artifacts/reports/01_authoritative_download.json

Examples:
    .venv/bin/python pipeline/01_authoritative_download.py --country PK
    .venv/bin/python pipeline/01_authoritative_download.py --source pk_nepra
    .venv/bin/python pipeline/01_authoritative_download.py --source pk_nepra --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit

import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shared.constants import ARTIFACTS, PROJECT_ROOT  # noqa: E402
from shared.report import Action, emit_decision, write_report  # noqa: E402

CONFIG_PATH = PROJECT_ROOT / "config" / "authoritative_sources.json"
DOWNLOAD_ROOT = PROJECT_ROOT / "authoritative_documents"
STATE_DIR = ARTIFACTS / "01_authoritative_download"
MANIFEST_PATH = STATE_DIR / "manifest.json"
DISCOVERY_PATH = STATE_DIR / "discovery.json"
CATALOG_PATH = STATE_DIR / "catalog.json"
USER_AGENT = os.getenv(
    "AUTHORITATIVE_DOWNLOAD_USER_AGENT",
    "psxgpt-authoritative-corpus/0.1 (+https://psxgpt.com)",
)
REQUEST_TIMEOUT = (15, 90)
MAX_ERROR_ROWS = 100
VOLATILE_QUERY_PARAMETERS = {"_", "cachebuster", "refresh"}


@dataclass(frozen=True)
class Candidate:
    source_id: str
    country: str
    publisher: str
    authority_type: str
    collection_id: str
    landing_url: str
    url: str
    title: str
    verify_tls: bool

    @property
    def identity(self) -> str:
        raw = f"{self.source_id}\n{self.collection_id}\n{canonical_url(self.url)}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []
        self._anchor_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a" and self._href is None:
            self._href = dict(attrs).get("href")
            self._text = []
            self._anchor_depth = 1
        elif self._href is not None:
            self._anchor_depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        return

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._href is None:
            return
        self._anchor_depth -= 1
        if tag.lower() == "a" or self._anchor_depth <= 0:
            title = re.sub(r"\s+", " ", "".join(self._text)).strip()
            self.links.append((self._href, title))
            self._href = None
            self._text = []
            self._anchor_depth = 0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_url(value: str) -> str:
    parts = urlsplit(value.strip())
    query = urlencode(
        sorted(
            (key, item)
            for key, item in parse_qsl(parts.query, keep_blank_values=True)
            if key.lower() not in VOLATILE_QUERY_PARAMETERS
        )
    )
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path,
            query,
            "",
        )
    )


def domain_allowed(url: str, allowed: Iterable[str]) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(host == item.lower() or host.endswith("." + item.lower()) for item in allowed)


def matches_any(value: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, value) for pattern in patterns)


def document_title(link_text: str, url: str) -> str:
    title = re.sub(r"\s+", " ", link_text).strip()
    if title and not re.fullmatch(
        r"(?i)(download(?:\s+pdf)?|view(?:\s+pdf)?|preview|read\s+more|pdf|document)",
        title,
    ):
        return title
    filename = Path(unquote(urlsplit(url).path)).name
    return filename or title or "document"


def extract_candidates(
    html: str,
    *,
    landing_url: str,
    source: dict[str, Any],
    collection: dict[str, Any],
) -> list[Candidate]:
    parser = LinkParser()
    parser.feed(html)
    url_patterns = collection.get("document_url_patterns") or [r"(?i)\.pdf(?:$|[?#])"]
    exclude_url_patterns = collection.get("document_exclude_url_patterns") or []
    title_patterns = collection.get("link_text_patterns") or []
    allowed = source["official_domains"]
    seen: set[str] = set()
    out: list[Candidate] = []
    for href, title in parser.links:
        if not href or href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        url = canonical_url(urljoin(landing_url, href))
        if url in seen or not domain_allowed(url, allowed):
            continue
        if not matches_any(url, url_patterns):
            continue
        if exclude_url_patterns and matches_any(url, exclude_url_patterns):
            continue
        if title_patterns and not matches_any(title, title_patterns):
            continue
        seen.add(url)
        out.append(
            Candidate(
                source_id=source["id"],
                country=source["country"].upper(),
                publisher=source["publisher"],
                authority_type=source["authority_type"],
                collection_id=collection["id"],
                landing_url=landing_url,
                url=url,
                title=document_title(title, url),
                verify_tls=bool(source.get("verify_tls", True)),
            )
        )
    return out


def extract_follow_urls(
    html: str,
    *,
    landing_url: str,
    source: dict[str, Any],
    collection: dict[str, Any],
) -> list[str]:
    patterns = collection.get("follow_url_patterns") or []
    if not patterns:
        return []
    parser = LinkParser()
    parser.feed(html)
    seen: set[str] = set()
    out: list[str] = []
    for href, title in parser.links:
        if not href or href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        url = canonical_url(urljoin(landing_url, href))
        if url in seen or not domain_allowed(url, source["official_domains"]):
            continue
        if not matches_any(f"{url} {title}", patterns):
            continue
        seen.add(url)
        out.append(url)
    return out


def load_config(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    if payload.get("schema_version") != 1:
        raise ValueError(f"Unsupported authoritative source schema: {payload.get('schema_version')}")
    ids: set[str] = set()
    for source in payload.get("sources", []):
        required = {"id", "country", "publisher", "authority_type", "official_domains", "collections"}
        missing = sorted(required - source.keys())
        if missing:
            raise ValueError(f"Source is missing {missing}: {source}")
        if source["id"] in ids:
            raise ValueError(f"Duplicate source id: {source['id']}")
        ids.add(source["id"])
        collection_ids: set[str] = set()
        for collection in source["collections"]:
            if not collection.get("id") or not collection.get("landing_urls"):
                raise ValueError(f"{source['id']} has a collection without id/landing_urls")
            if collection["id"] in collection_ids:
                raise ValueError(f"Duplicate collection id in {source['id']}: {collection['id']}")
            collection_ids.add(collection["id"])
    return payload


def load_manifest() -> dict[str, Any]:
    if not MANIFEST_PATH.exists():
        return {"schema_version": 1, "documents": {}}
    payload = json.loads(MANIFEST_PATH.read_text())
    if payload.get("schema_version") != 1 or not isinstance(payload.get("documents"), dict):
        raise ValueError(f"Unsupported or corrupt manifest: {MANIFEST_PATH}")
    return payload


def atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def request_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD"}),
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,application/pdf,*/*;q=0.8"})
    return session


def fetch_with_playwright(url: str, verify_tls: bool) -> tuple[str, str]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is required for this source but is not installed") from exc
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            context = browser.new_context(ignore_https_errors=not verify_tls, user_agent=USER_AGENT)
            page = context.new_page()
            response = page.goto(url, wait_until="networkidle", timeout=90_000)
            status = response.status if response else 0
            html = page.content()
            if status >= 400:
                raise requests.HTTPError(f"HTTP {status} from Playwright for {url}")
            return html, page.url
        finally:
            browser.close()


def fetch_landing(
    session: requests.Session,
    url: str,
    *,
    mode: str,
    verify_tls: bool,
) -> tuple[str, str]:
    if mode not in {"requests", "playwright", "auto"}:
        raise ValueError(f"Unknown landing_fetch mode: {mode}")
    if mode == "playwright":
        return fetch_with_playwright(url, verify_tls)
    try:
        response = session.get(url, timeout=REQUEST_TIMEOUT, verify=verify_tls)
        response.raise_for_status()
        return response.text, response.url
    except (requests.RequestException, RuntimeError):
        if mode != "auto":
            raise
        return fetch_with_playwright(url, verify_tls)


def is_pdf(content: bytes, content_type: str) -> bool:
    return content.lstrip()[:5] == b"%PDF-" or "application/pdf" in content_type.lower()


def slug(value: str, max_length: int = 90) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return (cleaned or "document")[:max_length].rstrip("-")


def publication_year(candidate: Candidate) -> int | None:
    value = f"{candidate.title} {candidate.url} {candidate.landing_url}"
    match = re.search(r"(?<!\d)(19|20)\d{2}(?!\d)", value)
    if match:
        return int(match.group(0))
    short = re.search(
        r"(?i)(?:annual[_ -]?report|chapter|fy|ended|sept|march)[^0-9]{0,12}([0-9]{2})(?![0-9])",
        value,
    )
    return 2000 + int(short.group(1)) if short else None


def pdf_path(candidate: Candidate, digest: str) -> Path:
    year = publication_year(candidate)
    base = (
        DOWNLOAD_ROOT
        / candidate.country.lower()
        / candidate.source_id
        / candidate.collection_id
        / (str(year) if year else "undated")
    )
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{slug(candidate.title)}--{digest[:12]}.pdf"


def conditional_headers(record: dict[str, Any] | None) -> dict[str, str]:
    if not record:
        return {}
    current = record.get("current") or {}
    out: dict[str, str] = {}
    if current.get("etag"):
        out["If-None-Match"] = current["etag"]
    if current.get("last_modified"):
        out["If-Modified-Since"] = current["last_modified"]
    return out


def local_record_error(record: dict[str, Any] | None) -> str | None:
    current = (record or {}).get("current") or {}
    local_path = current.get("local_path")
    if not local_path:
        return "manifest current.local_path is missing"
    path = PROJECT_ROOT / local_path
    if not path.is_file():
        return f"local file is missing: {local_path}"
    expected_bytes = current.get("bytes")
    if expected_bytes is None:
        return "manifest current.bytes is missing"
    if path.stat().st_size != int(expected_bytes):
        return (
            f"local size mismatch: {local_path} expected={expected_bytes} "
            f"actual={path.stat().st_size}"
        )
    return None


def download_candidate(
    session: requests.Session,
    candidate: Candidate,
    manifest: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    documents = manifest["documents"]
    record = documents.get(candidate.identity)
    candidate_year = publication_year(candidate)
    if record:
        record["publication_year"] = candidate_year
        current_path = (record.get("current") or {}).get("local_path")
        if current_path:
            record["canonical_filename"] = Path(current_path).name
    request_kwargs = {
        "timeout": REQUEST_TIMEOUT,
        "verify": candidate.verify_tls,
        "stream": True,
    }
    response = session.get(candidate.url, headers=conditional_headers(record), **request_kwargs)
    if response.status_code == 304 and record:
        if local_record_error(record) is None:
            response.close()
            record["last_seen_at"] = utc_now()
            record["title"] = candidate.title
            return "unchanged", record
        response.close()
        response = session.get(candidate.url, **request_kwargs)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "application/octet-stream")
    DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".authoritative.", suffix=".download", dir=DOWNLOAD_ROOT)
    digest_builder = hashlib.sha256()
    total_bytes = 0
    first_bytes = b""
    try:
        with os.fdopen(fd, "wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                if len(first_bytes) < 16:
                    first_bytes += chunk[: 16 - len(first_bytes)]
                handle.write(chunk)
                digest_builder.update(chunk)
                total_bytes += len(chunk)
        if not is_pdf(first_bytes, content_type):
            raise ValueError(f"not_pdf content_type={content_type} bytes={total_bytes}")
        digest = digest_builder.hexdigest()
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise
    finally:
        response.close()
    now = utc_now()
    if record and record.get("current", {}).get("sha256") == digest:
        current = record["current"]
        local_error = local_record_error(record)
        if local_error:
            repair_path = PROJECT_ROOT / current.get("local_path", "")
            if not current.get("local_path"):
                repair_path = pdf_path(candidate, digest)
                current["local_path"] = repair_path.relative_to(PROJECT_ROOT).as_posix()
            repair_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp_name, repair_path)
            current["bytes"] = total_bytes
            status = "repaired"
        else:
            os.unlink(tmp_name)
            status = "unchanged"
        record["last_seen_at"] = now
        record["title"] = candidate.title
        current["etag"] = response.headers.get("etag") or current.get("etag")
        current["last_modified"] = response.headers.get("last-modified") or current.get("last_modified")
        return status, record

    path = pdf_path(candidate, digest)
    if path.exists():
        os.unlink(tmp_name)
    else:
        os.replace(tmp_name, path)
    version = {
        "sha256": digest,
        "bytes": total_bytes,
        "content_type": content_type,
        "local_path": path.relative_to(PROJECT_ROOT).as_posix(),
        "downloaded_at": now,
        "etag": response.headers.get("etag"),
        "last_modified": response.headers.get("last-modified"),
        "final_url": canonical_url(response.url),
    }
    status = "updated" if record else "downloaded"
    if not record:
        record = {
            "identity": candidate.identity,
            "source_id": candidate.source_id,
            "country": candidate.country,
            "publisher": candidate.publisher,
            "authority_type": candidate.authority_type,
            "collection_id": candidate.collection_id,
            "canonical_url": canonical_url(candidate.url),
            "landing_url": candidate.landing_url,
            "title": candidate.title,
            "publication_year": candidate_year,
            "canonical_filename": path.name,
            "first_seen_at": now,
            "versions": [],
        }
    record["last_seen_at"] = now
    record["title"] = candidate.title
    record["current"] = version
    record.setdefault("versions", []).append(version)
    documents[candidate.identity] = record
    return status, record


def selected_sources(
    config: dict[str, Any],
    *,
    countries: set[str],
    source_ids: set[str],
    collection_ids: set[str],
    include_disabled: bool = False,
) -> list[dict[str, Any]]:
    out = []
    for source in config["sources"]:
        if not source.get("enabled", True) and not include_disabled:
            continue
        if countries and source["country"].upper() not in countries:
            continue
        if source_ids and source["id"] not in source_ids:
            continue
        source = dict(source)
        if collection_ids:
            source["collections"] = [
                item for item in source["collections"] if item["id"] in collection_ids
            ]
        if source["collections"]:
            out.append(source)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Download PDFs from authoritative publication pages")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--country", action="append", default=[], help="ISO country code; repeatable")
    parser.add_argument("--source", action="append", default=[], help="source id; repeatable")
    parser.add_argument("--collection", action="append", default=[], help="collection id; repeatable")
    parser.add_argument("--dry-run", action="store_true", help="discover links without downloading documents")
    parser.add_argument("--list-sources", action="store_true")
    parser.add_argument(
        "--include-disabled",
        action="store_true",
        help="include disabled registry entries for explicit validation/backfill",
    )
    parser.add_argument(
        "--new-only",
        action="store_true",
        help="select only document identities not already present in the manifest",
    )
    parser.add_argument("--max-documents", type=int, help="cap candidates per run for validation")
    parser.add_argument(
        "--offset-documents",
        type=int,
        default=0,
        help="skip this many eligible candidates before applying --max-documents",
    )
    parser.add_argument("--since-year", type=int, help="include dated documents from this year onward")
    parser.add_argument("--polite-delay", type=float, default=0.2, help="seconds between document requests")
    args = parser.parse_args()

    config = load_config(args.config)
    sources = selected_sources(
        config,
        countries={item.upper() for item in args.country},
        source_ids=set(args.source),
        collection_ids=set(args.collection),
        include_disabled=args.include_disabled,
    )
    if args.list_sources:
        for source in sources:
            collections = ", ".join(item["id"] for item in source["collections"])
            print(f"{source['id']}\t{source['country']}\t{source['publisher']}\t{collections}")
        return 0
    if not sources:
        parser.error("No enabled sources matched the requested scope")

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    manifest = load_manifest()
    session = request_session()
    started = time.monotonic()
    totals = {
        "sources": len(sources),
        "collections": sum(len(source["collections"]) for source in sources),
        "landing_pages": 0,
        "discovered": 0,
        "selected": 0,
        "downloaded": 0,
        "updated": 0,
        "repaired": 0,
        "unchanged": 0,
        "failed": 0,
        "blocked": 0,
    }
    source_reports: list[dict[str, Any]] = []
    discovery_rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    remaining = args.max_documents

    for source in sources:
        source_counts = {
            "source_id": source["id"],
            "publisher": source["publisher"],
            "country": source["country"],
            "discovered": 0,
            "selected": 0,
            "downloaded": 0,
            "updated": 0,
            "repaired": 0,
            "unchanged": 0,
            "failed": 0,
            "blocked": 0,
            "collections": [],
        }
        verify_tls = bool(source.get("verify_tls", True))
        mode = source.get("landing_fetch", "requests")
        for collection in source["collections"]:
            collection_counts = {
                "collection_id": collection["id"],
                "landing_pages": 0,
                "discovered": 0,
                "selected": 0,
                "downloaded": 0,
                "updated": 0,
                "repaired": 0,
                "unchanged": 0,
                "failed": 0,
                "blocked": 0,
            }
            candidates: list[Candidate] = []
            follow_urls: list[str] = []
            for landing_url in collection["landing_urls"]:
                totals["landing_pages"] += 1
                collection_counts["landing_pages"] += 1
                try:
                    html, final_url = fetch_landing(
                        session,
                        landing_url,
                        mode=mode,
                        verify_tls=verify_tls,
                    )
                    candidates.extend(
                        extract_candidates(
                            html,
                            landing_url=final_url,
                            source=source,
                            collection=collection,
                        )
                    )
                    follow_urls.extend(
                        extract_follow_urls(
                            html,
                            landing_url=final_url,
                            source=source,
                            collection=collection,
                        )
                    )
                except Exception as exc:
                    message = str(exc)
                    blocked = "403" in message or "blocked" in message.lower()
                    key = "blocked" if blocked else "failed"
                    totals[key] += 1
                    collection_counts[key] += 1
                    source_counts[key] += 1
                    if len(errors) < MAX_ERROR_ROWS:
                        errors.append(
                            {
                                "source_id": source["id"],
                                "collection_id": collection["id"],
                                "url": landing_url,
                                "stage": "landing",
                                "error": message,
                            }
                        )
            max_follow_pages = int(collection.get("max_follow_pages", 100))
            for follow_url in list(dict.fromkeys(follow_urls))[:max_follow_pages]:
                totals["landing_pages"] += 1
                collection_counts["landing_pages"] += 1
                try:
                    html, final_url = fetch_landing(
                        session,
                        follow_url,
                        mode=mode,
                        verify_tls=verify_tls,
                    )
                    candidates.extend(
                        extract_candidates(
                            html,
                            landing_url=final_url,
                            source=source,
                            collection=collection,
                        )
                    )
                except Exception as exc:
                    message = str(exc)
                    blocked = "403" in message or "blocked" in message.lower()
                    key = "blocked" if blocked else "failed"
                    totals[key] += 1
                    collection_counts[key] += 1
                    source_counts[key] += 1
                    if len(errors) < MAX_ERROR_ROWS:
                        errors.append(
                            {
                                "source_id": source["id"],
                                "collection_id": collection["id"],
                                "url": follow_url,
                                "stage": "follow_landing",
                                "error": message,
                            }
                        )
            unique = {candidate.identity: candidate for candidate in candidates}
            candidates = list(unique.values())
            if args.since_year is not None:
                candidates = [
                    candidate
                    for candidate in candidates
                    if publication_year(candidate) is None
                    or publication_year(candidate) >= args.since_year
                ]
            discovered_candidates = list(candidates)
            totals["discovered"] += len(candidates)
            collection_counts["discovered"] += len(candidates)
            source_counts["discovered"] += len(candidates)
            if args.new_only:
                candidates = [
                    candidate
                    for candidate in candidates
                    if candidate.identity not in manifest["documents"]
                ]
            if args.offset_documents:
                candidates = candidates[max(0, args.offset_documents) :]
            if remaining is not None:
                candidates = candidates[: max(0, remaining)]
                remaining -= len(candidates)
            selected_identities = {candidate.identity for candidate in candidates}
            for candidate in discovered_candidates:
                year = publication_year(candidate)
                discovery_rows.append(
                    {
                        "identity": candidate.identity,
                        "source_id": candidate.source_id,
                        "country": candidate.country,
                        "publisher": candidate.publisher,
                        "authority_type": candidate.authority_type,
                        "collection_id": candidate.collection_id,
                        "title": candidate.title,
                        "publication_year": year,
                        "canonical_url": canonical_url(candidate.url),
                        "landing_url": candidate.landing_url,
                        "already_downloaded": candidate.identity in manifest["documents"],
                        "selected": candidate.identity in selected_identities,
                        "proposed_folder": (
                            Path(
                                candidate.country.lower(),
                                candidate.source_id,
                                candidate.collection_id,
                                str(year) if year else "undated",
                            ).as_posix()
                        ),
                        "proposed_filename": f"{slug(candidate.title)}--<sha256-12>.pdf",
                    }
                )
            totals["selected"] += len(candidates)
            collection_counts["selected"] += len(candidates)
            source_counts["selected"] += len(candidates)
            if not args.dry_run:
                for candidate in candidates:
                    try:
                        status, _ = download_candidate(session, candidate, manifest)
                        totals[status] += 1
                        collection_counts[status] += 1
                        source_counts[status] += 1
                    except Exception as exc:
                        totals["failed"] += 1
                        collection_counts["failed"] += 1
                        source_counts["failed"] += 1
                        if len(errors) < MAX_ERROR_ROWS:
                            errors.append(
                                {
                                    "source_id": source["id"],
                                    "collection_id": collection["id"],
                                    "url": candidate.url,
                                    "stage": "download",
                                    "error": str(exc),
                                }
                            )
                    time.sleep(max(0, args.polite_delay))
            source_counts["collections"].append(collection_counts)
            if remaining is not None and remaining <= 0:
                break
        source_reports.append(source_counts)
        if remaining is not None and remaining <= 0:
            break

    report = {
        **totals,
        "dry_run": args.dry_run,
        "new_only": args.new_only,
        "since_year": args.since_year,
        "elapsed_s": round(time.monotonic() - started, 2),
        "config_path": args.config.relative_to(PROJECT_ROOT).as_posix(),
        "manifest_path": MANIFEST_PATH.relative_to(PROJECT_ROOT).as_posix(),
        "discovery_path": DISCOVERY_PATH.relative_to(PROJECT_ROOT).as_posix(),
        "catalog_path": CATALOG_PATH.relative_to(PROJECT_ROOT).as_posix(),
        "download_root": DOWNLOAD_ROOT.relative_to(PROJECT_ROOT).as_posix(),
        "source_reports": source_reports,
        "errors": errors,
    }
    local_errors: list[dict[str, str]] = []
    if not args.dry_run:
        selected_source_ids = {source["id"] for source in sources}
        for identity, record in manifest["documents"].items():
            if record.get("source_id") not in selected_source_ids:
                continue
            error = local_record_error(record)
            if error:
                local_errors.append(
                    {"identity": identity, "source_id": record.get("source_id", ""), "error": error}
                )
        report["local_integrity_checked"] = sum(
            1
            for record in manifest["documents"].values()
            if record.get("source_id") in selected_source_ids
        )
        report["local_integrity_errors"] = local_errors
        report["local_integrity_ok"] = not local_errors
    else:
        report["local_integrity_checked"] = 0
        report["local_integrity_errors"] = []
        report["local_integrity_ok"] = None
    if not args.dry_run:
        manifest["generated_at"] = utc_now()
        manifest["document_count"] = len(manifest["documents"])
        atomic_json_write(MANIFEST_PATH, manifest)
    discovery_generated_at = utc_now()
    for row in discovery_rows:
        row["already_downloaded"] = row["identity"] in manifest["documents"]
    atomic_json_write(
        DISCOVERY_PATH,
        {
            "schema_version": 1,
            "generated_at": discovery_generated_at,
            "dry_run": args.dry_run,
            "new_only": args.new_only,
            "since_year": args.since_year,
            "document_count": len(discovery_rows),
            "documents": discovery_rows,
        },
    )
    catalog = (
        json.loads(CATALOG_PATH.read_text())
        if CATALOG_PATH.exists()
        else {"schema_version": 1, "documents": {}}
    )
    for row in discovery_rows:
        existing = catalog["documents"].get(row["identity"]) or {}
        row = dict(row)
        row["first_discovered_at"] = existing.get("first_discovered_at", discovery_generated_at)
        row["last_discovered_at"] = discovery_generated_at
        catalog["documents"][row["identity"]] = row
    for identity, row in catalog["documents"].items():
        row["already_downloaded"] = identity in manifest["documents"]
    catalog["generated_at"] = discovery_generated_at
    catalog["document_count"] = len(catalog["documents"])
    atomic_json_write(CATALOG_PATH, catalog)
    write_report("01_authoritative_download", report)
    summary = (
        f"Discovered {totals['discovered']} authoritative PDFs; "
        f"downloaded {totals['downloaded']}, updated {totals['updated']}, "
        f"repaired {totals['repaired']}, unchanged {totals['unchanged']}, "
        f"failed {totals['failed']}, "
        f"blocked sources {totals['blocked']}."
    )
    emit_decision(
        "01_authoritative_download",
        summary,
        actions=[
            Action("Inspect source health report", "cat artifacts/reports/01_authoritative_download.json"),
            Action("Run Pakistan refresh", ".venv/bin/python pipeline/01_authoritative_download.py --country PK"),
        ],
    )
    print(summary)
    return 1 if totals["failed"] or totals["blocked"] or local_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
