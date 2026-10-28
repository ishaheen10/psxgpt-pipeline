#!/usr/bin/env python3
"""Step M2 — Push parsed macro documents to Cloudflare D1.

Reads Gemini-parsed markdown from macro_markdown/, respects the
skip_manifest from 03_gate.py, tags each surviving page as 'table' or
'text', then uploads to macro_documents + macro_fts.

Run 03_gate.py first so skip_manifest.json is current.

Input:  macro_markdown/{source_dir}/{chapter}/page_NNN.md
        artifacts/03_gate/skip_manifest.json
Output: D1 tables `macro_documents`, `macro_fts`
        artifacts/macro_push/macro_{ts}.sql

Usage:
    python pipeline/macro_push.py
    python pipeline/macro_push.py --doc economic_survey
    python pipeline/macro_push.py --sql-only
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import re
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from dotenv import load_dotenv
    load_dotenv()
except ModuleNotFoundError:
    pass
from shared.constants import PROJECT_ROOT, ARTIFACTS  # noqa: E402
from shared.report import write_report, emit_decision  # noqa: E402

MD_ROOT = PROJECT_ROOT / "macro_markdown"
PAGE_ROOT = PROJECT_ROOT / "macro_pages"
THUMB_ROOT = PROJECT_ROOT / "macro_thumbnails"
SQL_DIR = ARTIFACTS / "macro_push"
ASSET_MANIFEST = SQL_DIR / "asset_manifest.json"
SKIP_MANIFEST = ARTIFACTS / "03_gate" / "skip_manifest.json"
D1_DB = "psx"
R2_BUCKET = os.getenv("CLOUDFLARE_R2_BUCKET_NAME", "psx")
R2_ENDPOINT = os.getenv("CLOUDFLARE_R2_ENDPOINT", "")
R2_ACCESS_KEY_ID = os.getenv("CLOUDFLARE_R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.getenv("CLOUDFLARE_R2_SECRET_ACCESS_KEY", "")
_asset_lock = threading.Lock()
_asset_local = threading.local()

PAGE_COMMENT = re.compile(r"<!--\s*Page\s+\d+\s*-->", re.I)

DIR_META: dict[str, dict] = {
    "economic_survey_2025": {"doc_type": "economic_survey", "fiscal_year": "2024-25"},
    "budget_2025_26":       {"doc_type": "budget",          "fiscal_year": "2025-26"},
    "sbp_annual_2025":      {"doc_type": "sbp_report",      "fiscal_year": "2024-25"},
}

DDL = """\
DROP TABLE IF EXISTS macro_fts;
DROP TABLE IF EXISTS macro_documents;

CREATE TABLE macro_documents (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_type     TEXT NOT NULL,
  fiscal_year  TEXT NOT NULL,
  source_doc   TEXT NOT NULL,
  chapter      TEXT,
  page_num     INTEGER,
  content_type TEXT NOT NULL DEFAULT 'text',
  content      TEXT,
  created_at   TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_macro_lookup
  ON macro_documents(doc_type, fiscal_year);
CREATE INDEX IF NOT EXISTS idx_macro_type
  ON macro_documents(doc_type, content_type);
"""

DDL_FTS = """\
CREATE VIRTUAL TABLE IF NOT EXISTS macro_fts USING fts5(
  doc_type, fiscal_year, chapter, content_type, content,
  content='macro_documents', content_rowid='id'
);
INSERT INTO macro_fts(macro_fts) VALUES('rebuild');
"""


def esc(s: str) -> str:
    if not s:
        return ""
    s = s.replace("\x00", "")
    s = s.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    return s.replace("'", "''")


def has_table(md: str) -> bool:
    """True if markdown contains a table separator row (|---|)."""
    return any(
        re.match(r"^\s*\|[\s\-:|]+\|\s*$", line) and line.count("-") >= 2
        for line in md.splitlines()
    )


# Patterns that indicate Gemini hallucinated US corporate financial statements
# onto blank/image-only pages instead of returning empty output.
_HALLUCINATION = re.compile(
    r"Consolidated Statements? of (Operations|Cash Flows|Earnings)|"
    r"Net income \| \$|"
    r"Cost of (?:revenue|goods sold)[^\n]*\| \$[\d,]+|"
    r"Q[1-4] 20\d\d \| Q[1-4] 20\d\d",
    re.I,
)


def is_hallucinated(md: str) -> bool:
    return bool(_HALLUCINATION.search(md))


def generate_sql(doc_filter: str | None, skip: set[str]) -> tuple[Path, int, dict]:
    SQL_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    sql_path = SQL_DIR / f"macro_{ts}.sql"
    total = 0
    by_type: dict[str, int] = {"table": 0, "text": 0}
    n_skipped = 0
    n_hallucinated = 0

    with open(sql_path, "w") as f:
        f.write(f"-- macro_documents upload — {ts}\n\n")
        f.write(DDL)

        for source_dir in sorted(MD_ROOT.iterdir()):
            if not source_dir.is_dir():
                continue
            meta = DIR_META.get(source_dir.name)
            if not meta:
                continue
            doc_type = meta["doc_type"]
            if doc_filter and doc_type != doc_filter:
                continue
            fiscal_year = meta["fiscal_year"]

            for chapter_dir in sorted(source_dir.iterdir()):
                if not chapter_dir.is_dir():
                    continue
                chapter = chapter_dir.name
                for md_file in sorted(chapter_dir.glob("page_*.md")):
                    # Keep the historical skip_manifest contract even though
                    # macro markdown now lives outside markdown_pages/.
                    rel = f"macro/{md_file.relative_to(MD_ROOT).as_posix()}"
                    if rel in skip:
                        n_skipped += 1
                        continue

                    page_num = int("".join(c for c in md_file.stem if c.isdigit()))
                    md = md_file.read_text(errors="replace")
                    if is_hallucinated(md):
                        n_hallucinated += 1
                        continue
                    content = PAGE_COMMENT.sub("", md).strip()
                    content_type = "table" if has_table(md) else "text"
                    by_type[content_type] += 1

                    f.write(
                        f"INSERT INTO macro_documents "
                        f"(doc_type, fiscal_year, source_doc, chapter, page_num, content_type, content) "
                        f"VALUES ('{esc(doc_type)}', '{esc(fiscal_year)}', "
                        f"'{esc(source_dir.name)}', '{esc(chapter)}', "
                        f"{page_num}, '{content_type}', '{esc(content)}');\n"
                    )
                    total += 1
                    if total % 500 == 0:
                        print(f"    {total:,} rows...")

    print(f"  Generated {sql_path.name}: {total:,} rows "
          f"(table={by_type['table']}, text={by_type['text']}, "
          f"gate-skipped={n_skipped}, hallucination-filtered={n_hallucinated})")
    return sql_path, total, by_type, n_hallucinated


def upload(sql_path: Path, label: str = "") -> bool:
    size_mb = sql_path.stat().st_size / 1_048_576
    print(f"  Uploading {label or sql_path.name} ({size_mb:.1f} MB)...")
    res = subprocess.run(
        ["npx", "wrangler", "d1", "execute", D1_DB, "--remote", f"--file={sql_path}"],
        capture_output=True, text=True, cwd=PROJECT_ROOT,
    )
    if res.returncode != 0:
        print(f"  FAILED: {res.stderr[-2000:]}")
        return False
    print("  Done.")
    return True


def r2_client():
    if not hasattr(_asset_local, "client"):
        try:
            import boto3
            from botocore.config import Config
        except ModuleNotFoundError as exc:
            raise RuntimeError("boto3/botocore are required for macro R2 upload") from exc
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


def macro_asset_pairs(doc_filter: str | None) -> list[tuple[str, Path, str]]:
    pairs: list[tuple[str, Path, str]] = []
    for source_dir in sorted(PAGE_ROOT.iterdir() if PAGE_ROOT.exists() else []):
        meta = DIR_META.get(source_dir.name)
        if not source_dir.is_dir() or not meta:
            continue
        if doc_filter and meta["doc_type"] != doc_filter:
            continue
        for pdf_path in sorted(source_dir.glob("*/page_*.pdf")):
            rel = pdf_path.relative_to(PAGE_ROOT).as_posix()
            pairs.append((f"MACRO_PAGES/{rel}", pdf_path, "application/pdf"))
            thumb_path = THUMB_ROOT / Path(rel).with_suffix(".jpg")
            if thumb_path.exists():
                pairs.append((f"MACRO_THUMBNAILS/{Path(rel).with_suffix('.jpg').as_posix()}", thumb_path, "image/jpeg"))
    return pairs


def upload_one_asset(item: tuple[str, Path, str], existing: set[str]) -> tuple[int, int, str, str | None]:
    key, path, content_type = item
    if key in existing:
        return 0, 1, "", None
    if not path.exists():
        return 0, 0, f"{key}: missing local file", None
    try:
        r2_client().upload_file(str(path), R2_BUCKET, key, ExtraArgs={"ContentType": content_type})
        return 1, 0, "", key
    except Exception as exc:
        return 0, 0, f"{key}: {exc}", None


def publish_assets(doc_filter: str | None, workers: int) -> dict:
    if not (R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY and R2_ENDPOINT):
        raise SystemExit("missing CLOUDFLARE_R2_ACCESS_KEY_ID / SECRET_ACCESS_KEY / ENDPOINT in env")
    pairs = macro_asset_pairs(doc_filter)
    if not pairs:
        return {"asset_rows": 0, "asset_uploaded": 0, "asset_skipped": 0, "asset_errors": 0}
    existing = load_asset_manifest()
    uploaded = skipped = done = 0
    errors: list[str] = []
    new_keys: set[str] = set()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {ex.submit(upload_one_asset, item, existing): item for item in pairs}
        for fut in as_completed(futs):
            up, skip, err, key = fut.result()
            uploaded += up
            skipped += skip
            done += 1
            if key:
                new_keys.add(key)
            if err:
                errors.append(err)
            if done % 500 == 0:
                with _asset_lock:
                    save_asset_manifest(existing | new_keys)
                print(f"  macro assets {done}/{len(pairs)} uploaded={uploaded} skipped={skipped} errors={len(errors)}", flush=True)
    save_asset_manifest(existing | new_keys)
    print(f"macro assets: files={len(pairs)} uploaded={uploaded} skipped={skipped} errors={len(errors)}")
    for error in errors[:10]:
        print(f"  {error}")
    return {
        "asset_rows": len(pairs),
        "asset_uploaded": uploaded,
        "asset_skipped": skipped,
        "asset_errors": len(errors),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Step M2 — push macro docs to D1")
    ap.add_argument("--doc", choices=[m["doc_type"] for m in DIR_META.values()])
    ap.add_argument("--sql-only", action="store_true")
    ap.add_argument("--upload-assets", action="store_true", help="upload macro page PDFs and thumbnails to R2")
    ap.add_argument("--assets-only", action="store_true", help="upload R2 assets without regenerating/pushing D1 SQL")
    ap.add_argument("--workers", type=int, default=50, help="parallel R2 upload workers")
    a = ap.parse_args()

    if not MD_ROOT.exists() or not any(MD_ROOT.rglob("page_*.md")):
        print(f"No markdown at {MD_ROOT} — parse macro page PDFs into macro_markdown/ first")
        return 1

    if a.assets_only:
        stats = publish_assets(a.doc, a.workers)
        write_report("macro_push_assets", {"doc_filter": a.doc, **stats})
        emit_decision("macro_push_assets", f"Uploaded macro source assets: {stats}.")
        return 0 if stats.get("asset_errors", 0) == 0 else 1

    skip: set[str] = set()
    if SKIP_MANIFEST.exists():
        skip = set(json.loads(SKIP_MANIFEST.read_text()).keys())
        print(f"  Loaded {len(skip):,} skip entries from 03_gate")
    else:
        print("  Warning: skip_manifest not found — run 03_gate.py first for full gating")

    sql_path, total, by_type, n_hallucinated = generate_sql(a.doc, skip)

    if total == 0:
        print("No rows generated.")
        return 1

    if a.sql_only:
        print(f"--sql-only: SQL at {sql_path}")
        if a.upload_assets:
            stats = publish_assets(a.doc, a.workers)
            write_report("macro_push_assets", {"doc_filter": a.doc, **stats})
        return 0

    if not upload(sql_path, "content table + inserts"):
        return 1

    fts_path = SQL_DIR / "macro_fts.sql"
    fts_path.write_text(DDL_FTS)
    if not upload(fts_path, "FTS index build"):
        print("  Warning: FTS build failed — content still queryable via regular SQL")

    result = subprocess.run(
        ["npx", "wrangler", "d1", "execute", D1_DB, "--remote",
         "--command", "SELECT COUNT(*) as n FROM macro_documents"],
        capture_output=True, text=True, cwd=PROJECT_ROOT,
    )
    m = re.search(r'"n":\s*(\d+)', result.stdout)
    n = int(m.group(1)) if m else -1
    print(f"\n{'─'*60}")
    print(f"macro_documents: {n:,} rows in D1  (table={by_type['table']}, text={by_type['text']})")
    print(f"{'─'*60}")

    asset_stats = publish_assets(a.doc, a.workers) if a.upload_assets else {}

    write_report("macro_push", {"doc_filter": a.doc, "rows": total, "d1_count": n,
                               "hallucination_filtered": n_hallucinated, **by_type, **asset_stats})
    emit_decision("macro_push", f"Pushed {total:,} macro rows to D1 ({n:,} confirmed). "
                  f"table={by_type['table']}, text={by_type['text']}, "
                  f"hallucination_filtered={n_hallucinated}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
