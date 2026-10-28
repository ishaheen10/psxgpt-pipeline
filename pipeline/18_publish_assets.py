#!/usr/bin/env python3
"""Step 18 — Publish source assets: page thumbnails + R2 upload.

Two deterministic phases for the selected tickers:
  1. Thumbnails: pdf_pages/<rel>.pdf -> pdf_thumbnails/<rel>.jpg (360px/q70/110dpi,
     pdf2image + PIL, ProcessPoolExecutor). Skips existing unless --force-thumbs.
  2. Upload: pdf_pages + pdf_thumbnails -> R2 PDF_PAGES/ + PDF_THUMBNAILS/ via the
     boto3 **S3 API** (path-style, 150 ThreadPool workers, local manifest skips done keys).

Ported/merged from psxgptinfrav3 Step4_ConvertToJPG + Step5_UploadToR2.

R2 auth (.env): CLOUDFLARE_R2_ACCESS_KEY_ID / CLOUDFLARE_R2_SECRET_ACCESS_KEY /
CLOUDFLARE_R2_ENDPOINT — an R2 *S3 token's* credentials, NOT a cfat_/cfut_ API-token value
(that only works over the rate-limited Cloudflare REST API). See .env.example.

Usage:
    python3 pipeline/18_publish_assets.py --tickers FECTC
    python3 pipeline/18_publish_assets.py --tickers-file artifacts/18_publish_assets/targets.txt
    python3 pipeline/18_publish_assets.py --all
    python3 pipeline/18_publish_assets.py --tickers FECTC --thumbnails-only
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from multiprocessing import cpu_count
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT / "pipeline"))

PDF_PAGES = ROOT / "pdf_pages"
THUMBS = ROOT / "pdf_thumbnails"
MANIFEST = ROOT / "artifacts" / "18_publish_assets" / "manifest.json"

# thumbnail spec (matches psxgptinfrav3)
TARGET_WIDTH, JPEG_QUALITY, DPI = 360, 70, 110
UPLOAD_WORKERS = 150

BUCKET = os.getenv("CLOUDFLARE_R2_BUCKET_NAME", "psx")
ENDPOINT = os.getenv("CLOUDFLARE_R2_ENDPOINT", "")
AKID = os.getenv("CLOUDFLARE_R2_ACCESS_KEY_ID", "")
SECRET = os.getenv("CLOUDFLARE_R2_SECRET_ACCESS_KEY", "")

_mlock = threading.Lock()
_local = threading.local()


# ── target selection ──
def gap_tickers() -> set[str]:
    sql = ("SELECT DISTINCT ticker FROM financial_statements WHERE ticker NOT IN "
           "(SELECT DISTINCT ticker FROM financial_documents_new) ORDER BY ticker")
    res = subprocess.run(["npx", "wrangler", "d1", "execute", "psx", "--remote", "--json", "--command", sql],
                         capture_output=True, text=True, cwd=ROOT)
    if res.returncode != 0:
        raise SystemExit(f"gap query failed:\n{res.stderr[-1500:]}")
    data = json.loads(res.stdout[res.stdout.find("["):])
    rows = data[0]["results"] if isinstance(data, list) else data["result"][0]["results"]
    return {str(r["ticker"]).strip().upper() for r in rows if r.get("ticker")}


def page_pdfs(tickers: set[str] | None) -> list[Path]:
    out = []
    for pdf in PDF_PAGES.rglob("*.pdf"):
        if tickers is None or pdf.relative_to(PDF_PAGES).parts[0].upper() in tickers:
            out.append(pdf)
    return out


# ── phase 1: thumbnails ──
def convert(args: tuple) -> str:
    pdf, out_dir, force = args
    try:
        from pdf2image import convert_from_path
        from PIL import Image
        out_dir.mkdir(parents=True, exist_ok=True)
        jpg = out_dir / (pdf.stem + ".jpg")
        if jpg.exists() and not force:
            return "skip"
        imgs = convert_from_path(pdf, dpi=DPI, first_page=1, last_page=1, fmt="jpeg")
        if not imgs:
            return f"fail {pdf}: no image"
        img = imgs[0].convert("RGB")
        w, h = img.size
        img.resize((TARGET_WIDTH, int(TARGET_WIDTH * h / w)), Image.Resampling.LANCZOS).save(
            jpg, "JPEG", quality=JPEG_QUALITY, optimize=True)
        return "ok"
    except Exception as e:
        return f"fail {pdf}: {e}"


def make_thumbnails(pdfs: list[Path], force: bool, workers: int) -> int:
    tasks = [(p, THUMBS / p.relative_to(PDF_PAGES).parent, force) for p in pdfs]
    ok = skip = fail = 0
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for i, r in enumerate(as_completed([ex.submit(convert, t) for t in tasks]), 1):
            try:
                v = r.result()
            except Exception as e:
                v = f"fail: {e}"
            ok += v == "ok"; skip += v == "skip"; fail += v.startswith("fail")
            if i % 5000 == 0:
                print(f"  thumbs {i}/{len(tasks)} ok={ok} skip={skip} fail={fail}", flush=True)
    print(f"thumbnails: ok={ok} skipped={skip} failed={fail}")
    return fail


# ── phase 2: upload ──
def s3():
    if not hasattr(_local, "c"):
        try:
            import boto3
            from botocore.config import Config
        except ModuleNotFoundError as exc:
            raise RuntimeError("boto3/botocore are required for R2 upload; run via python3 run.py or .venv/bin/python") from exc
        _local.c = boto3.client("s3", endpoint_url=ENDPOINT, aws_access_key_id=AKID,
                                aws_secret_access_key=SECRET, region_name="auto",
                                config=Config(signature_version="s3v4", s3={"addressing_style": "path"},
                                              retries={"max_attempts": 5, "mode": "standard"},
                                              max_pool_connections=50))
    return _local.c


def load_manifest() -> set[str]:
    if MANIFEST.exists():
        try:
            return set(json.loads(MANIFEST.read_text()).get("uploaded_keys", []))
        except Exception:
            pass
    return set()


def save_manifest(keys: set[str]) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps({"count": len(keys), "uploaded_keys": sorted(keys)}))


def upload_page(pdf: Path, existing: set[str]) -> tuple[int, int, str]:
    rel = str(pdf.relative_to(PDF_PAGES))
    rel_jpg = str(Path(rel).with_suffix(".jpg"))
    jpg = THUMBS / rel_jpg
    up = skip = 0
    try:
        for key, path, ct in [(f"PDF_PAGES/{rel}", pdf, "application/pdf"),
                              (f"PDF_THUMBNAILS/{rel_jpg}", jpg, "image/jpeg")]:
            if key in existing:
                skip += 1; continue
            if not path.exists():
                return up, skip, f"{key}: missing local file (run thumbnails first)"
            s3().upload_file(str(path), BUCKET, key, ExtraArgs={"ContentType": ct})
            up += 1
        return up, skip, ""
    except Exception as e:
        return up, skip, f"{rel}: {e}"


def upload(pdfs: list[Path], workers: int) -> int:
    existing = load_manifest()
    up = skip = done = 0
    errors: list[str] = []
    new: set[str] = set()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(upload_page, p, existing): p for p in pdfs}
        for f in as_completed(futs):
            u, s, err = f.result(); up += u; skip += s; done += 1
            if err:
                errors.append(err)
            else:
                rel = str(futs[f].relative_to(PDF_PAGES))
                new.add(f"PDF_PAGES/{rel}"); new.add(f"PDF_THUMBNAILS/{Path(rel).with_suffix('.jpg')}")
            if done % 2000 == 0:
                with _mlock:
                    save_manifest(existing | new)
                print(f"  upload {done}/{len(pdfs)} up={up} skip={skip} err={len(errors)}", flush=True)
    save_manifest(existing | new)
    print(f"upload: uploaded={up} skipped={skip} errors={len(errors)}")
    for e in errors[:10]:
        print(f"  {e}")
    return 1 if errors else 0


def reconcile_from_r2() -> int:
    """Rebuild the local manifest from what is actually in R2 (read-only list).

    The manifest is otherwise only seeded by uploads from THIS environment, so assets
    uploaded by earlier runs/processes are absent — which makes coverage checks (e.g. the
    state gate) report false 'missing asset' warnings. This lists the bucket and writes the
    true set of uploaded keys. No uploads, no DB writes."""
    if not (AKID and SECRET and ENDPOINT):
        raise SystemExit("missing CLOUDFLARE_R2_ACCESS_KEY_ID / SECRET_ACCESS_KEY / ENDPOINT in env")
    client = s3()
    keys: set[str] = set()
    for prefix in ("PDF_PAGES/", "PDF_THUMBNAILS/"):
        token = None
        while True:
            kwargs = {"Bucket": BUCKET, "Prefix": prefix, "MaxKeys": 1000}
            if token:
                kwargs["ContinuationToken"] = token
            resp = client.list_objects_v2(**kwargs)
            for obj in resp.get("Contents", []):
                keys.add(obj["Key"])
            if resp.get("IsTruncated"):
                token = resp.get("NextContinuationToken")
            else:
                break
            if len(keys) % 50000 < 1000:
                print(f"  listed {len(keys):,} keys...", flush=True)
    save_manifest(keys)
    print(f"reconciled manifest from R2: {len(keys):,} keys")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Publish source assets (thumbnails + R2 upload)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--tickers", help="comma-separated ticker list")
    g.add_argument("--tickers-file", help="file with one ticker per line")
    g.add_argument("--gap", action="store_true", help="tickers missing from financial_documents_new")
    g.add_argument("--all", action="store_true", help="every ticker under pdf_pages/")
    g.add_argument("--reconcile", action="store_true", help="rebuild local manifest from R2 (read-only list, no upload)")
    ap.add_argument("--thumbnails-only", action="store_true", help="generate thumbnails, skip upload")
    ap.add_argument("--skip-thumbnails", action="store_true", help="upload only; assume thumbnails exist")
    ap.add_argument("--force-thumbs", action="store_true", help="regenerate thumbnails even if present")
    ap.add_argument("--thumb-workers", type=int, default=max(1, cpu_count() - 1))
    ap.add_argument("--upload-workers", type=int, default=UPLOAD_WORKERS)
    a = ap.parse_args()

    if a.reconcile:
        return reconcile_from_r2()

    if a.all:
        tickers = None
    elif a.tickers:
        tickers = {t.strip().upper() for t in a.tickers.split(",") if t.strip()}
    elif a.tickers_file:
        tickers = {ln.strip().upper() for ln in Path(a.tickers_file).read_text().splitlines() if ln.strip()}
    else:
        tickers = gap_tickers()

    pdfs = page_pdfs(tickers)
    print(f"assets: {'ALL' if tickers is None else len(tickers)} tickers, {len(pdfs)} pages")
    if not pdfs:
        print("  nothing to do"); return 0

    if not a.skip_thumbnails:
        thumb_failures = make_thumbnails(pdfs, a.force_thumbs, a.thumb_workers)
        if thumb_failures:
            print("thumbnail generation failed; run through the repo .venv or install pdf2image/Pillow/poppler")
            return 1
    if a.thumbnails_only:
        return 0
    if not (AKID and SECRET and ENDPOINT):
        raise SystemExit("missing CLOUDFLARE_R2_ACCESS_KEY_ID / SECRET_ACCESS_KEY / ENDPOINT in env")
    return upload(pdfs, a.upload_workers)


if __name__ == "__main__":
    raise SystemExit(main())
