#!/usr/bin/env python3
"""Step 2 — Parse (Gemini Flash Lite): page PDF -> markdown.

Scopes:
  --filing TICKER/YEAR/FILING    one filing
  --gap                          unparsed PDFs + image_only pages flagged for backfill
  --pages 12,13                  specific pages (with --filing)
  --reparse-broken               re-parse garbled (repetition-loop) pages corpus-wide

Input:  pdf_pages/<...>/page_###.pdf
Output: markdown_pages/<...>/page_###.md ; artifacts/01_ingest/parse_usage.jsonl ;
        artifacts/reports/02_parse.json

Gemini occasionally loops and emits a huge garbled page (text only — review/search_text
stays sane), which bloats financial_documents_new.text and can hit SQLITE_TOOBIG. A page is
"broken" if its markdown exceeds --max-bytes OR a single non-empty line repeats more than
--max-repeat times. --reparse-broken re-parses them (forces --reset) and writes the affected
tickers to artifacts/reports/02_parse.json so they can be re-published (17 --push-d1).

Usage:
    python pipeline/02_parse.py --filing LUCK/2024/LUCK_Annual_2024
    python pipeline/02_parse.py --gap
    python pipeline/02_parse.py --reparse-broken --max-repeat 100
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent))
load_dotenv()
from shared.constants import PROJECT_ROOT, ARTIFACTS  # noqa: E402
from shared.report import write_report, emit_decision, Action, write_stage_issues, write_stage_lineage  # noqa: E402

PDF_ROOT = PROJECT_ROOT / "pdf_pages"
MD_ROOT = PROJECT_ROOT / "markdown_pages"
USAGE = ARTIFACTS / "01_ingest" / "parse_usage.jsonl"
GAP_PDFS = ARTIFACTS / "convergence" / "gap_unparsed_pdfs.txt"
GAP_IMG = ARTIFACTS / "convergence" / "gap_image_pages.jsonl"
MODEL = os.getenv("GEMINI_PARSE_MODEL", "gemini-3.1-flash-lite")
PROMPT = ("Convert this single PDF page into clean GitHub-flavored markdown. Preserve every "
          "table as a markdown table, keep all numbers and line-item labels exactly as printed, "
          "keep headings. Output only the markdown, no commentary.")
_lock = threading.Lock()


def broken_reason(md: Path, max_bytes: int, max_repeat: int) -> str | None:
    try:
        size = md.stat().st_size
    except OSError:
        return None
    if size > max_bytes:
        return f"oversized:{size}"
    if size > 8_000:
        counts = Counter(ln for ln in md.read_text(errors="ignore").splitlines() if ln.strip())
        if counts and counts.most_common(1)[0][1] > max_repeat:
            return f"repeat:{counts.most_common(1)[0][1]}"
    return None


def broken_targets(max_bytes: int, max_repeat: int) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for md in MD_ROOT.rglob("page_*.md"):
        rel = md.relative_to(MD_ROOT)
        if rel.parts[0] == "macro":
            continue
        if broken_reason(md, max_bytes, max_repeat):
            filing = "/".join(rel.parts[:3])
            out.append((filing, int("".join(c for c in rel.name if c.isdigit()))))
    return out


def targets(a) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    if getattr(a, "reparse_broken", False):
        return broken_targets(a.max_bytes, a.max_repeat)
    if a.gap:
        # Explicit gap file (step 05 convergence loop)
        if GAP_PDFS.exists():
            for rel in (x.strip() for x in GAP_PDFS.read_text().splitlines() if x.strip()):
                out += [(rel, int("".join(c for c in p.stem if c.isdigit())))
                        for p in sorted((PDF_ROOT / rel).glob("page_*.pdf"))]
        if GAP_IMG.exists():
            for line in (x for x in GAP_IMG.read_text().splitlines() if x.strip()):
                d = json.loads(line)
                out += [(d["filing"], int(n)) for n in d.get("image_pages", [])]
        # Auto-scan: any filing dir in pdf_pages/ that has no corresponding markdown_pages/ dir
        for page_pdf in sorted(PDF_ROOT.rglob("page_*.pdf")):
            rel = str(page_pdf.parent.relative_to(PDF_ROOT))
            n = int("".join(c for c in page_pdf.stem if c.isdigit()))
            md = MD_ROOT / rel / f"page_{n:03d}.md"
            if not md.exists():
                out.append((rel, n))
    elif a.filing:
        fdir = PDF_ROOT / a.filing
        nums = ([int(x) for x in a.pages.split(",")] if a.pages else
                [int("".join(c for c in p.stem if c.isdigit())) for p in sorted(fdir.glob("page_*.pdf"))])
        out = [(a.filing, n) for n in nums]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Step 2 — Gemini parse")
    ap.add_argument("--filing"); ap.add_argument("--gap", action="store_true")
    ap.add_argument("--pages"); ap.add_argument("--workers", type=int, default=20)
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--reparse-broken", dest="reparse_broken", action="store_true",
                    help="re-parse garbled pages (forces --reset)")
    ap.add_argument("--max-bytes", type=int, default=60_000)
    ap.add_argument("--max-repeat", type=int, default=50)
    a = ap.parse_args()
    if a.reparse_broken:
        a.reset = True  # broken pages already have markdown; must overwrite

    tgts = targets(a)
    affected_tickers = sorted({f.split("/")[0] for f, _ in tgts})
    if not a.reset:
        tgts = [(f, n) for f, n in tgts if not (MD_ROOT / f / f"page_{n:03d}.md").exists()]
    if not tgts:
        emit_decision("02_parse", "Nothing to parse — all targets already have markdown.")
        return 0

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    USAGE.parent.mkdir(parents=True, exist_ok=True)
    ok = fail = 0
    failures: list[dict] = []
    lineage: list[dict] = []
    rlock = threading.Lock()

    def work(t):
        nonlocal ok, fail
        rel, n = t
        try:
            resp = client.models.generate_content(
                model=MODEL,
                contents=[PROMPT, types.Part.from_bytes(
                    data=(PDF_ROOT / rel / f"page_{n:03d}.pdf").read_bytes(), mime_type="application/pdf")])
            out = MD_ROOT / rel / f"page_{n:03d}.md"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(f"<!-- Page {n} -->\n\n{(resp.text or '').strip()}\n")
            um = getattr(resp, "usage_metadata", None)
            with _lock, open(USAGE, "a") as f:
                f.write(json.dumps({"filing": rel, "page": n, "model": MODEL,
                                    "in": getattr(um, "prompt_token_count", 0) or 0,
                                    "out": getattr(um, "candidates_token_count", 0) or 0}) + "\n")
            with rlock:
                ok += 1
                lineage.append({"filing": rel, "ticker": rel.split("/")[0].upper(), "page": n,
                                "relative_path": f"{rel}/page_{n:03d}.md", "status": "ok",
                                "model": MODEL})
        except Exception as e:
            with rlock:
                fail += 1
                failures.append({"severity": "blocker", "ticker": rel.split("/")[0].upper(),
                                 "filing": rel, "page": n, "relative_path": f"{rel}/page_{n:03d}.pdf",
                                 "rule": "parse_failed", "message": str(e), "origin_stage": "02_parse",
                                 "recommended_command": f"python3 pipeline/02_parse.py --filing {rel} --pages {n} --reset"})
                lineage.append({"filing": rel, "ticker": rel.split("/")[0].upper(), "page": n,
                                "relative_path": f"{rel}/page_{n:03d}.md", "status": "failed",
                                "model": MODEL, "error": str(e)})
            return f"{rel}/page_{n:03d}: {e}"

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for fut in tqdm(as_completed([ex.submit(work, t) for t in tgts]), total=len(tgts), desc="parse"):
            err = fut.result()
            if err:
                print(" ", err)

    write_stage_lineage("02_parse", lineage)
    write_stage_issues("02_parse", failures)
    report = {"parsed": ok, "failed": fail, "model": MODEL,
              "lineage_rows": len(lineage), "issues": len(failures),
              "failed_pages": failures[:100]}
    if a.reparse_broken:
        report["affected_tickers"] = affected_tickers
        write_report("02_parse", report)
        emit_decision("02_parse", f"Re-parsed {ok} broken pages, {fail} failed. Re-publish affected tickers.",
                      actions=[Action("Re-publish each affected ticker", "python3 pipeline/17_publish_documents.py --ticker <TICKER> --push-d1")])
        print(f"affected tickers (re-publish each with: python3 pipeline/17_publish_documents.py --ticker <TICKER> --push-d1):\n  {' '.join(affected_tickers)}")
        return 0
    write_report("02_parse", report)
    emit_decision("02_parse", f"Parsed {ok} pages, {fail} failed.",
                  actions=[Action("Run gate", "python pipeline/03_gate.py")])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
