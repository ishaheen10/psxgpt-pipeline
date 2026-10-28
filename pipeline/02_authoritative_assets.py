#!/usr/bin/env python3
"""Verify authoritative originals, split them into pages, and optionally publish to R2.

The download manifest is the only input contract. Local preparation is safe by
default; remote writes require --upload-assets.

Outputs:
    authoritative_pages/{country}/{source}/{collection}/{year}/{document}/page_###.pdf
    authoritative_thumbnails/{same path}/page_###.jpg
    artifacts/02_authoritative_assets/manifest.json
    artifacts/reports/02_authoritative_assets.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shared.constants import ARTIFACTS, PROJECT_ROOT  # noqa: E402
from shared.report import Action, emit_decision, write_report  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

DOWNLOAD_MANIFEST = ARTIFACTS / "01_authoritative_download" / "manifest.json"
PAGE_ROOT = PROJECT_ROOT / "authoritative_pages"
THUMB_ROOT = PROJECT_ROOT / "authoritative_thumbnails"
STATE_DIR = ARTIFACTS / "02_authoritative_assets"
ASSET_MANIFEST = STATE_DIR / "manifest.json"

R2_BUCKET = os.getenv("CLOUDFLARE_R2_BUCKET_NAME", "psx")
R2_ENDPOINT = os.getenv("CLOUDFLARE_R2_ENDPOINT", "")
R2_ACCESS_KEY_ID = os.getenv("CLOUDFLARE_R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.getenv("CLOUDFLARE_R2_SECRET_ACCESS_KEY", "")
SOURCE_BASE_URL = os.getenv("SOURCE_BASE_URL", "https://source.psxgpt.com").rstrip("/")

TARGET_WIDTH, JPEG_QUALITY, DPI = 360, 70, 110
_local = threading.local()


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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_download_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"download manifest not found: {path}")
    payload = json.loads(path.read_text())
    if payload.get("schema_version") != 1 or not isinstance(payload.get("documents"), dict):
        raise ValueError(f"unsupported or corrupt download manifest: {path}")
    return payload


def load_asset_manifest() -> dict[str, Any]:
    if not ASSET_MANIFEST.exists():
        return {"schema_version": 1, "documents": {}, "uploaded_keys": []}
    payload = json.loads(ASSET_MANIFEST.read_text())
    if payload.get("schema_version") != 1:
        raise ValueError(f"unsupported asset manifest: {ASSET_MANIFEST}")
    payload.setdefault("documents", {})
    payload.setdefault("uploaded_keys", [])
    return payload


def selected_documents(
    manifest: dict[str, Any],
    countries: set[str],
    sources: set[str],
    identities: set[str],
    max_documents: int | None,
) -> list[dict[str, Any]]:
    rows = []
    for identity, record in sorted(manifest["documents"].items()):
        row = dict(record)
        row["identity"] = identity
        if countries and str(row.get("country", "")).upper() not in countries:
            continue
        if sources and row.get("source_id") not in sources:
            continue
        if identities and identity not in identities:
            continue
        rows.append(row)
    return rows[:max_documents] if max_documents is not None else rows


def document_relative_dir(record: dict[str, Any]) -> Path:
    current = record["current"]
    original = Path(current["local_path"])
    year = original.parent.name
    stem = original.stem
    return Path(
        str(record["country"]).lower(),
        str(record["source_id"]),
        str(record["collection_id"]),
        year,
        f"{stem}--{record['identity']}",
    )


def page_metadata(relative_dir: Path, page_count: int, uploaded: set[str]) -> list[dict[str, Any]]:
    rows = []
    for page_number in range(1, page_count + 1):
        pdf_rel = relative_dir / f"page_{page_number:03d}.pdf"
        jpg_rel = relative_dir / f"page_{page_number:03d}.jpg"
        pdf_key = f"AUTHORITATIVE_PAGES/{pdf_rel.as_posix()}"
        jpg_key = f"AUTHORITATIVE_THUMBNAILS/{jpg_rel.as_posix()}"
        rows.append(
            {
                "page": page_number,
                "pdf_local_path": (Path("authoritative_pages") / pdf_rel).as_posix(),
                "thumbnail_local_path": (Path("authoritative_thumbnails") / jpg_rel).as_posix(),
                "pdf_r2_key": pdf_key,
                "thumbnail_r2_key": jpg_key,
                "source_url": f"{SOURCE_BASE_URL}/{pdf_key}" if pdf_key in uploaded else None,
                "thumbnail_url": f"{SOURCE_BASE_URL}/{jpg_key}" if jpg_key in uploaded else None,
            }
        )
    return rows


def make_thumbnail(page_pdf: Path, thumbnail: Path) -> None:
    from pdf2image import convert_from_path
    from PIL import Image

    images = convert_from_path(page_pdf, dpi=DPI, first_page=1, last_page=1, fmt="jpeg")
    if not images:
        raise ValueError("no image rendered")
    image = images[0].convert("RGB")
    width, height = image.size
    thumbnail.parent.mkdir(parents=True, exist_ok=True)
    image.resize(
        (TARGET_WIDTH, int(TARGET_WIDTH * height / width)),
        Image.Resampling.LANCZOS,
    ).save(thumbnail, "JPEG", quality=JPEG_QUALITY, optimize=True)


def prepare_document(record: dict[str, Any], uploaded: set[str], force: bool) -> dict[str, Any]:
    from pypdf import PdfReader, PdfWriter

    current = record.get("current") or {}
    original = PROJECT_ROOT / current.get("local_path", "")
    if not original.is_file():
        raise FileNotFoundError(f"missing original: {original}")
    actual_size = original.stat().st_size
    if actual_size != int(current.get("bytes", -1)):
        raise ValueError(f"size mismatch: manifest={current.get('bytes')} local={actual_size}")
    actual_sha256 = sha256_file(original)
    if actual_sha256 != current.get("sha256"):
        raise ValueError(f"sha256 mismatch: manifest={current.get('sha256')} local={actual_sha256}")

    reader = PdfReader(str(original), strict=False)
    page_count = len(reader.pages)
    if page_count <= 0:
        raise ValueError("PDF has zero pages")
    relative_dir = document_relative_dir(record)
    page_dir = PAGE_ROOT / relative_dir
    thumb_dir = THUMB_ROOT / relative_dir
    page_dir.mkdir(parents=True, exist_ok=True)
    thumb_dir.mkdir(parents=True, exist_ok=True)
    pages_written = thumbnails_written = 0
    for index, page in enumerate(reader.pages, 1):
        page_pdf = page_dir / f"page_{index:03d}.pdf"
        thumbnail = thumb_dir / f"page_{index:03d}.jpg"
        if force or not page_pdf.exists():
            writer = PdfWriter()
            writer.add_page(page)
            with page_pdf.open("wb") as handle:
                writer.write(handle)
            pages_written += 1
        if force or not thumbnail.exists():
            make_thumbnail(page_pdf, thumbnail)
            thumbnails_written += 1

    actual_pages = sorted(page_dir.glob("page_*.pdf"))
    actual_thumbnails = sorted(thumb_dir.glob("page_*.jpg"))
    if len(actual_pages) != page_count or len(actual_thumbnails) != page_count:
        raise ValueError(
            f"incomplete assets: expected={page_count} pages={len(actual_pages)} "
            f"thumbnails={len(actual_thumbnails)}"
        )
    original_rel = original.relative_to(PROJECT_ROOT).as_posix()
    original_key = f"AUTHORITATIVE_DOCUMENTS/{original_rel.removeprefix('authoritative_documents/')}"
    return {
        "identity": record["identity"],
        "source_id": record["source_id"],
        "country": record["country"],
        "publisher": record["publisher"],
        "collection_id": record["collection_id"],
        "title": record["title"],
        "canonical_url": record["canonical_url"],
        "content_sha256": actual_sha256,
        "bytes": actual_size,
        "page_count": page_count,
        "original_local_path": original_rel,
        "original_r2_key": original_key,
        "original_url": f"{SOURCE_BASE_URL}/{original_key}" if original_key in uploaded else None,
        "relative_asset_dir": relative_dir.as_posix(),
        "pages_written": pages_written,
        "thumbnails_written": thumbnails_written,
        "pages": page_metadata(relative_dir, page_count, uploaded),
        "verified_at": utc_now(),
    }


def r2_client():
    if not hasattr(_local, "client"):
        import boto3
        from botocore.config import Config

        _local.client = boto3.client(
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
    return _local.client


def asset_items(document: dict[str, Any]) -> list[tuple[str, Path, str]]:
    items = [
        (
            document["original_r2_key"],
            PROJECT_ROOT / document["original_local_path"],
            "application/pdf",
        )
    ]
    for page in document["pages"]:
        items.extend(
            [
                (page["pdf_r2_key"], PROJECT_ROOT / page["pdf_local_path"], "application/pdf"),
                (
                    page["thumbnail_r2_key"],
                    PROJECT_ROOT / page["thumbnail_local_path"],
                    "image/jpeg",
                ),
            ]
        )
    return items


def upload_one(item: tuple[str, Path, str], existing: set[str]) -> tuple[str, str]:
    key, path, content_type = item
    if key in existing:
        return "skipped", key
    if not path.is_file():
        raise FileNotFoundError(f"{key}: missing {path}")
    r2_client().upload_file(
        str(path),
        R2_BUCKET,
        key,
        ExtraArgs={
            "ContentType": content_type,
            "CacheControl": "public, max-age=31536000, immutable",
        },
    )
    return "uploaded", key


def upload_documents(
    documents: list[dict[str, Any]], existing: set[str], workers: int
) -> tuple[set[str], dict[str, int], list[str]]:
    items = [item for document in documents for item in asset_items(document)]
    new_keys: set[str] = set()
    counts = {"asset_files": len(items), "uploaded": 0, "skipped": 0, "failed": 0}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(upload_one, item, existing): item for item in items}
        for future in as_completed(futures):
            try:
                status, key = future.result()
                counts[status] += 1
                new_keys.add(key)
            except Exception as exc:
                counts["failed"] += 1
                if len(errors) < 100:
                    errors.append(str(exc))
    return existing | new_keys, counts, errors


def list_authoritative_r2_keys() -> set[str]:
    keys: set[str] = set()
    client = r2_client()
    for prefix in (
        "AUTHORITATIVE_DOCUMENTS/",
        "AUTHORITATIVE_PAGES/",
        "AUTHORITATIVE_THUMBNAILS/",
    ):
        token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"Bucket": R2_BUCKET, "Prefix": prefix, "MaxKeys": 1000}
            if token:
                kwargs["ContinuationToken"] = token
            response = client.list_objects_v2(**kwargs)
            keys.update(item["Key"] for item in response.get("Contents", []))
            if not response.get("IsTruncated"):
                break
            token = response.get("NextContinuationToken")
    return keys


def refresh_urls(document: dict[str, Any], uploaded: set[str]) -> None:
    original_key = document["original_r2_key"]
    document["original_url"] = f"{SOURCE_BASE_URL}/{original_key}" if original_key in uploaded else None
    for page in document["pages"]:
        pdf_key = page["pdf_r2_key"]
        jpg_key = page["thumbnail_r2_key"]
        page["source_url"] = f"{SOURCE_BASE_URL}/{pdf_key}" if pdf_key in uploaded else None
        page["thumbnail_url"] = f"{SOURCE_BASE_URL}/{jpg_key}" if jpg_key in uploaded else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare and publish authoritative source assets")
    parser.add_argument("--manifest", type=Path, default=DOWNLOAD_MANIFEST)
    parser.add_argument("--country", action="append", default=[])
    parser.add_argument("--source", action="append", default=[])
    parser.add_argument("--document", action="append", default=[], help="download-manifest identity")
    parser.add_argument("--max-documents", type=int)
    parser.add_argument(
        "--new-only",
        action="store_true",
        help="prepare only missing or content-changed asset-manifest documents",
    )
    parser.add_argument("--force", action="store_true", help="rebuild existing page files and thumbnails")
    parser.add_argument("--upload-assets", action="store_true", help="write originals/pages/thumbnails to R2")
    parser.add_argument(
        "--verify-r2",
        action="store_true",
        help="list authoritative R2 prefixes and prove selected expected keys exist",
    )
    parser.add_argument("--upload-workers", type=int, default=24)
    args = parser.parse_args()

    if (args.upload_assets or args.verify_r2) and not (
        R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY and R2_ENDPOINT
    ):
        parser.error("R2 credentials are missing from .env")
    download_manifest = load_download_manifest(args.manifest)
    asset_manifest = load_asset_manifest()
    uploaded = set(asset_manifest["uploaded_keys"])
    selected = selected_documents(
        download_manifest,
        {value.upper() for value in args.country},
        set(args.source),
        set(args.document),
        None if args.new_only else args.max_documents,
    )
    if args.new_only:
        selected = [
            record
            for record in selected
            if (
                (asset_manifest["documents"].get(record["identity"]) or {}).get(
                    "content_sha256"
                )
                != record["current"]["sha256"]
            )
        ]
        if args.max_documents is not None:
            selected = selected[: max(0, args.max_documents)]
    if not selected:
        if args.new_only:
            print("no new or content-changed authoritative documents require assets")
            return 0
        parser.error("no downloaded documents matched the requested scope")

    started = time.monotonic()
    prepared: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for record in selected:
        try:
            document = prepare_document(record, uploaded, args.force)
            prepared.append(document)
            print(
                f"verified {record['identity']} {record['source_id']}: "
                f"{document['page_count']} pages "
                f"(new pages={document['pages_written']}, thumbnails={document['thumbnails_written']})",
                flush=True,
            )
        except Exception as exc:
            errors.append({"identity": record["identity"], "stage": "prepare", "error": str(exc)})

    upload_counts = {"asset_files": 0, "uploaded": 0, "skipped": 0, "failed": 0}
    if args.upload_assets and not errors:
        uploaded, upload_counts, upload_errors = upload_documents(
            prepared, uploaded, args.upload_workers
        )
        errors.extend({"identity": "", "stage": "upload", "error": value} for value in upload_errors)
    elif args.upload_assets and errors:
        errors.append(
            {
                "identity": "",
                "stage": "upload_gate",
                "error": "upload skipped because one or more selected documents failed verification",
            }
        )

    r2_expected = {key for document in prepared for key, _, _ in asset_items(document)}
    r2_missing: list[str] = []
    if args.verify_r2 and not errors:
        uploaded = list_authoritative_r2_keys()
        r2_missing = sorted(r2_expected - uploaded)
        if r2_missing:
            errors.append(
                {
                    "identity": "",
                    "stage": "r2_verify",
                    "error": f"{len(r2_missing)} selected expected keys are missing from R2",
                }
            )

    for document in prepared:
        refresh_urls(document, uploaded)
        asset_manifest["documents"][document["identity"]] = document
    asset_manifest["uploaded_keys"] = sorted(uploaded)
    asset_manifest["generated_at"] = utc_now()
    asset_manifest["document_count"] = len(asset_manifest["documents"])
    atomic_json_write(ASSET_MANIFEST, asset_manifest)

    report = {
        "selected_documents": len(selected),
        "verified_documents": len(prepared),
        "failed_documents": len(errors),
        "page_count": sum(row["page_count"] for row in prepared),
        "pages_written": sum(row["pages_written"] for row in prepared),
        "thumbnails_written": sum(row["thumbnails_written"] for row in prepared),
        "upload_requested": args.upload_assets,
        "r2_verify_requested": args.verify_r2,
        "r2_expected_keys": len(r2_expected),
        "r2_verified_keys": len(r2_expected - set(r2_missing)) if args.verify_r2 else 0,
        "r2_missing_keys": r2_missing[:100],
        **upload_counts,
        "elapsed_s": round(time.monotonic() - started, 2),
        "download_manifest": args.manifest.relative_to(PROJECT_ROOT).as_posix(),
        "asset_manifest": ASSET_MANIFEST.relative_to(PROJECT_ROOT).as_posix(),
        "errors": errors,
    }
    write_report("02_authoritative_assets", report)
    summary = (
        f"Verified {len(prepared)}/{len(selected)} authoritative documents and "
        f"{report['page_count']} pages; uploaded {upload_counts['uploaded']} assets, "
        f"failed {len(errors)}."
    )
    emit_decision(
        "02_authoritative_assets",
        summary,
        actions=[
            Action("Inspect asset report", "cat artifacts/reports/02_authoritative_assets.json"),
            Action(
                "Publish verified assets",
                ".venv/bin/python pipeline/02_authoritative_assets.py --country PK --upload-assets",
            ),
        ],
    )
    print(summary)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
