#!/usr/bin/env python3
"""Extract authoritative PDFs page-by-page and maintain a local FTS5 search index.

This stage is incremental by document content hash. It never invents text for
image-only pages: those pages are written with provenance and reported as
needing OCR.

Outputs:
    authoritative_markdown/{country}/{source}/{collection}/{year}/{document}/page_###.md
    artifacts/03_authoritative_parse/manifest.json
    artifacts/03_authoritative_parse/search.sqlite
    artifacts/reports/03_authoritative_parse.json
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shared.constants import ARTIFACTS, PROJECT_ROOT  # noqa: E402
from shared.report import Action, emit_decision, write_report  # noqa: E402

DOWNLOAD_MANIFEST = ARTIFACTS / "01_authoritative_download" / "manifest.json"
ASSET_MANIFEST = ARTIFACTS / "02_authoritative_assets" / "manifest.json"
TEXT_ROOT = PROJECT_ROOT / "authoritative_markdown"
STATE_DIR = ARTIFACTS / "03_authoritative_parse"
PARSE_MANIFEST = STATE_DIR / "manifest.json"
SEARCH_DB = STATE_DIR / "search.sqlite"
MIN_SEARCHABLE_CHARS = 40


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def load_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(path)
    return json.loads(path.read_text())


def document_relative_dir(record: dict[str, Any]) -> Path:
    original = Path(record["current"]["local_path"])
    return Path(
        str(record["country"]).lower(),
        str(record["source_id"]),
        str(record["collection_id"]),
        original.parent.name,
        f"{original.stem}--{record['identity']}",
    )


def asset_pages(asset_manifest: dict[str, Any], record: dict[str, Any]) -> dict[int, dict[str, Any]]:
    document = asset_manifest.get("documents", {}).get(record["identity"]) or {}
    if document.get("content_sha256") != record["current"]["sha256"]:
        return {}
    return {int(page["page"]): page for page in document.get("pages", [])}


def frontmatter(record: dict[str, Any], page_number: int, source_url: str | None) -> str:
    fields = {
        "identity": record["identity"],
        "source_id": record["source_id"],
        "publisher": record["publisher"],
        "collection_id": record["collection_id"],
        "title": record["title"],
        "page": page_number,
        "canonical_url": record["canonical_url"],
        "source_url": source_url,
    }
    lines = ["---"]
    lines.extend(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in fields.items())
    lines.append("---")
    return "\n".join(lines)


def write_text_page(
    path: Path,
    record: dict[str, Any],
    page_number: int,
    text: str,
    source_url: str | None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(frontmatter(record, page_number, source_url))
            handle.write("\n\n")
            handle.write(text.strip())
            handle.write("\n")
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def strip_frontmatter(markdown: str) -> str:
    if not markdown.startswith("---\n"):
        return markdown
    parts = markdown.split("---\n", 2)
    return parts[2].lstrip() if len(parts) == 3 else markdown


def parse_document(
    record: dict[str, Any],
    assets: dict[str, Any],
) -> dict[str, Any]:
    from pypdf import PdfReader

    original = PROJECT_ROOT / record["current"]["local_path"]
    if not original.is_file():
        raise FileNotFoundError(original)
    reader = PdfReader(str(original), strict=False)
    relative_dir = document_relative_dir(record)
    output_dir = TEXT_ROOT / relative_dir
    page_assets = asset_pages(assets, record)
    pages: list[dict[str, Any]] = []
    searchable_pages = needs_ocr_pages = extraction_errors = 0
    for page_number, page in enumerate(reader.pages, 1):
        error: str | None = None
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            text = ""
            error = str(exc)
            extraction_errors += 1
        compact_length = len("".join(text.split()))
        needs_ocr = compact_length < MIN_SEARCHABLE_CHARS
        if needs_ocr:
            needs_ocr_pages += 1
        else:
            searchable_pages += 1
        source_url = (page_assets.get(page_number) or {}).get("source_url")
        text_path = output_dir / f"page_{page_number:03d}.md"
        write_text_page(text_path, record, page_number, text, source_url)
        pages.append(
            {
                "page": page_number,
                "text_path": text_path.relative_to(PROJECT_ROOT).as_posix(),
                "characters": len(text),
                "needs_ocr": needs_ocr,
                "extraction_error": error,
                "source_url": source_url,
            }
        )
    if not pages:
        raise ValueError("PDF has zero pages")
    return {
        "identity": record["identity"],
        "source_id": record["source_id"],
        "country": record["country"],
        "publisher": record["publisher"],
        "collection_id": record["collection_id"],
        "title": record["title"],
        "canonical_url": record["canonical_url"],
        "content_sha256": record["current"]["sha256"],
        "page_count": len(pages),
        "searchable_pages": searchable_pages,
        "needs_ocr_pages": needs_ocr_pages,
        "extraction_errors": extraction_errors,
        "asset_page_count": sum(
            1 for page in pages if page.get("source_url")
        ),
        "relative_text_dir": relative_dir.as_posix(),
        "pages": pages,
        "parsed_at": utc_now(),
    }


def rebuild_search_index(parse_manifest: dict[str, Any]) -> dict[str, int]:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".search.", suffix=".sqlite", dir=STATE_DIR)
    os.close(fd)
    rows = indexed = skipped = 0
    try:
        connection = sqlite3.connect(tmp_name)
        connection.execute(
            """
            CREATE VIRTUAL TABLE pages_fts USING fts5(
                page_id UNINDEXED,
                source_id,
                publisher,
                collection_id,
                title,
                body,
                source_url UNINDEXED,
                canonical_url UNINDEXED,
                tokenize='unicode61'
            )
            """
        )
        for document in parse_manifest.get("documents", {}).values():
            for page in document.get("pages", []):
                rows += 1
                path = PROJECT_ROOT / page["text_path"]
                if not path.is_file() or page.get("needs_ocr"):
                    skipped += 1
                    continue
                body = strip_frontmatter(path.read_text(errors="replace"))
                page_id = f"{document['identity']}:{int(page['page']):04d}"
                connection.execute(
                    "INSERT INTO pages_fts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        page_id,
                        document["source_id"],
                        document["publisher"],
                        document["collection_id"],
                        document["title"],
                        body,
                        page.get("source_url"),
                        document["canonical_url"],
                    ),
                )
                indexed += 1
        connection.commit()
        connection.close()
        os.replace(tmp_name, SEARCH_DB)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return {"page_rows": rows, "indexed_pages": indexed, "unindexed_pages": skipped}


def search(query: str, limit: int) -> int:
    if not SEARCH_DB.exists():
        raise SystemExit(f"search index not found: {SEARCH_DB}")
    connection = sqlite3.connect(SEARCH_DB)
    rows = connection.execute(
        """
        SELECT page_id, source_id, title, source_url, canonical_url,
               snippet(pages_fts, 5, '[', ']', ' … ', 24) AS excerpt,
               bm25(pages_fts) AS score
        FROM pages_fts
        WHERE pages_fts MATCH ?
        ORDER BY score
        LIMIT ?
        """,
        (query, max(1, limit)),
    ).fetchall()
    connection.close()
    for page_id, source_id, title, source_url, canonical_url, excerpt, score in rows:
        print(f"{page_id}\t{source_id}\t{score:.4f}\t{title}")
        print(f"  {source_url or canonical_url}")
        print(f"  {excerpt}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse authoritative PDFs into page text and FTS5")
    parser.add_argument("--country", action="append", default=[])
    parser.add_argument("--source", action="append", default=[])
    parser.add_argument("--document", action="append", default=[])
    parser.add_argument("--max-documents", type=int)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--rebuild-index-only", action="store_true")
    parser.add_argument("--query", help="search the existing FTS index instead of parsing")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    if args.query:
        return search(args.query, args.limit)

    download_manifest = load_json(DOWNLOAD_MANIFEST)
    assets = load_json(ASSET_MANIFEST, {"documents": {}})
    parse_manifest = load_json(
        PARSE_MANIFEST,
        {"schema_version": 1, "documents": {}},
    )
    selected: list[dict[str, Any]] = []
    if not args.rebuild_index_only:
        countries = {item.upper() for item in args.country}
        sources = set(args.source)
        identities = set(args.document)
        for identity, value in sorted(download_manifest["documents"].items()):
            record = dict(value)
            record["identity"] = identity
            if countries and str(record.get("country", "")).upper() not in countries:
                continue
            if sources and record.get("source_id") not in sources:
                continue
            if identities and identity not in identities:
                continue
            existing = parse_manifest["documents"].get(identity) or {}
            current_asset_count = sum(
                1
                for page in asset_pages(assets, record).values()
                if page.get("source_url")
            )
            if (
                not args.force
                and existing.get("content_sha256") == record["current"]["sha256"]
                and int(existing.get("asset_page_count", 0)) == current_asset_count
            ):
                continue
            selected.append(record)
        if args.max_documents is not None:
            selected = selected[: max(0, args.max_documents)]

    started = time.monotonic()
    parsed: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for record in selected:
        try:
            document = parse_document(record, assets)
            parse_manifest["documents"][record["identity"]] = document
            parsed.append(document)
            print(
                f"parsed {record['source_id']} {record['identity']}: "
                f"{document['page_count']} pages, OCR needed={document['needs_ocr_pages']}",
                flush=True,
            )
        except Exception as exc:
            errors.append({"identity": record["identity"], "source_id": record["source_id"], "error": str(exc)})

    parse_manifest["generated_at"] = utc_now()
    parse_manifest["document_count"] = len(parse_manifest["documents"])
    atomic_json_write(PARSE_MANIFEST, parse_manifest)
    index_counts = rebuild_search_index(parse_manifest)
    report = {
        "selected_documents": len(selected),
        "parsed_documents": len(parsed),
        "manifest_documents": len(parse_manifest["documents"]),
        "parsed_pages": sum(item["page_count"] for item in parsed),
        "searchable_pages": sum(item["searchable_pages"] for item in parsed),
        "needs_ocr_pages": sum(item["needs_ocr_pages"] for item in parsed),
        "extraction_errors": sum(item["extraction_errors"] for item in parsed),
        **index_counts,
        "elapsed_s": round(time.monotonic() - started, 2),
        "parse_manifest": PARSE_MANIFEST.relative_to(PROJECT_ROOT).as_posix(),
        "search_db": SEARCH_DB.relative_to(PROJECT_ROOT).as_posix(),
        "errors": errors,
    }
    write_report("03_authoritative_parse", report)
    summary = (
        f"Parsed {len(parsed)}/{len(selected)} documents and {report['parsed_pages']} pages; "
        f"indexed {index_counts['indexed_pages']} pages, OCR needed for "
        f"{report['needs_ocr_pages']} newly parsed pages."
    )
    emit_decision(
        "03_authoritative_parse",
        summary,
        actions=[
            Action(
                "Search the corpus",
                '.venv/bin/python pipeline/03_authoritative_parse.py --query \'"power tariff"\'',
            ),
            Action(
                "Parse the next Pakistan batch",
                ".venv/bin/python pipeline/03_authoritative_parse.py --country PK --max-documents 10",
            ),
        ],
    )
    print(summary)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
