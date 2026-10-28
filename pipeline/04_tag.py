#!/usr/bin/env python3
"""Step 4 — Tag (DeepSeek V4 Pro, one call/page).

Per surviving page returns: named statement tags + basis, extraction_score, search_text.
Pack: context/04_tag/. Pages flagged ReOCR feed step 5 (fix).

Input:  artifacts/03_gate/tag_manifest.json
Output: review/<ticker>/<year>/<filing>.jsonl ; artifacts/reports/04_tag.json

Usage:
    python pipeline/04_tag.py [--ticker LUCK] [--workers 50] [--reset]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent))
load_dotenv()
from shared.constants import PROJECT_ROOT, MARKDOWN_ROOT, ARTIFACTS  # noqa: E402
from shared.prompt_packs import read_prompt_pack  # noqa: E402
from shared import pricing  # noqa: E402
from shared.report import write_report, emit_decision, Action, write_stage_issues, write_stage_lineage  # noqa: E402

REVIEW_ROOT = PROJECT_ROOT / "review"
TAG_MANIFEST = ARTIFACTS / "03_gate" / "tag_manifest.json"
MODEL = os.getenv("DEEPSEEK_REVIEW_MODEL", "deepseek-v4-pro")
BASE = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com")
MAX_CHARS = 4000
PROMPT = read_prompt_pack("04_tag")
if PROMPT is None:
    raise FileNotFoundError("context/04_tag pack missing")
STMT = {"profit_loss", "balance_sheet", "cash_flow", "changes_in_equity", "comprehensive_income"}
ALLOWED = STMT | {"statement_note", "multi_year", "ceo_comp", "operational_metrics"}


def normalize(res: dict) -> dict:
    tags = []
    for t in res.get("section_tags") or []:
        if not isinstance(t, dict):
            continue
        name = str(t.get("tag", "")).strip().lower()
        if name not in ALLOWED:
            continue
        try:
            conf = max(0.0, min(1.0, float(t.get("confidence", 0.5))))
        except (TypeError, ValueError):
            conf = 0.5
        basis = t.get("basis") if name in STMT else None
        if basis not in ("consolidated", "unconsolidated", "unknown", None):
            basis = "unknown"
        tags.append({"tag": name, "confidence": round(conf, 2), "basis": basis})
    score = str(res.get("extraction_score", "OK")).strip()
    return {"section_tags": tags, "extraction_score": score if score in ("OK", "Fix", "ReOCR") else "OK",
            "search_text": str(res.get("search_text", "") or "")}


def tag_page(client: OpenAI, text: str) -> dict:
    body = text if len(text) <= MAX_CHARS else text[:MAX_CHARS] + "\n\n...[TRUNCATED]..."
    msgs = [{"role": "system", "content": "You analyze PSX filing pages and always respond with strict JSON."},
            {"role": "user", "content": f"{PROMPT}\n\nPage content:\n{body}"}]
    for attempt in range(3):
        try:
            r = client.chat.completions.create(model=MODEL, messages=msgs, temperature=0.1,
                                               response_format={"type": "json_object"})
            return json.loads(r.choices[0].message.content)
        except Exception:
            if attempt == 2:
                raise
            time.sleep(4 * (attempt + 1))
    return {}


def main() -> int:
    ap = argparse.ArgumentParser(description="Step 4 — tag")
    ap.add_argument("--ticker"); ap.add_argument("--workers", type=int, default=50)
    ap.add_argument("--limit", type=int); ap.add_argument("--reset", action="store_true")
    a = ap.parse_args()

    if not TAG_MANIFEST.exists():
        raise SystemExit("No tag manifest — run 03_gate.py first.")
    pages = json.loads(TAG_MANIFEST.read_text())["pages"]
    by_filing: dict[str, list[str]] = defaultdict(list)
    for rp in pages:
        if a.ticker and rp.split("/")[0].upper() != a.ticker.upper():
            continue
        by_filing["/".join(rp.split("/")[:3])].append(rp)

    filings = sorted(by_filing)
    if not a.reset:
        filings = [f for f in filings if not (REVIEW_ROOT / f).with_suffix(".jsonl").exists()]
    if a.limit:
        filings = filings[: a.limit]
    if not filings:
        emit_decision("04_tag", "Nothing to tag — all filings already reviewed.")
        return 0

    client = OpenAI(api_key=os.environ["DEEPSEEK_API_KEY"], base_url=BASE)
    tag_counts: Counter = Counter()
    reocr = pages_done = 0
    lineage: list[dict] = []
    issues: list[dict] = []
    lock = threading.Lock()

    for filing in tqdm(filings, desc="tag"):
        fpages = sorted(by_filing[filing])
        recs: dict[str, dict] = {}

        def work(rp):
            try:
                rec = normalize(tag_page(client, (MARKDOWN_ROOT / rp).read_text(errors="ignore")))
            except Exception as e:
                rec = {"section_tags": [], "extraction_score": "ReOCR", "search_text": "", "error": str(e)}
            with lock:
                recs[rp] = rec

        with ThreadPoolExecutor(max_workers=a.workers) as ex:
            list(as_completed([ex.submit(work, rp) for rp in fpages]))

        out = (REVIEW_ROOT / filing).with_suffix(".jsonl")
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w") as f:
            for rp in fpages:
                r = recs[rp]
                f.write(json.dumps({"relative_path": rp, "source": "tag", **r}, ensure_ascii=False) + "\n")
                for t in r["section_tags"]:
                    tag_counts[t["tag"]] += 1
                page = int("".join(c for c in Path(rp).stem if c.isdigit()))
                lineage.append({"ticker": rp.split("/")[0].upper(), "filing": "/".join(rp.split("/")[:3]),
                                "relative_path": rp, "page": page, "section_tags": r["section_tags"],
                                "extraction_score": r["extraction_score"], "search_text_chars": len(r.get("search_text", "")),
                                "model": MODEL, "origin_stage": "03_gate"})
                if r["extraction_score"] == "ReOCR":
                    issues.append({"severity": "blocker", "ticker": rp.split("/")[0].upper(),
                                   "filing": "/".join(rp.split("/")[:3]), "relative_path": rp, "page": page,
                                   "rule": "review_reocr", "message": r.get("error") or "Review marked page ReOCR",
                                   "origin_stage": "04_tag",
                                   "recommended_command": f"python3 pipeline/02_parse.py --filing {'/'.join(rp.split('/')[:3])} --pages {page} --reset && python3 pipeline/04_tag.py --ticker {rp.split('/')[0].upper()} --reset"})
                reocr += r["extraction_score"] == "ReOCR"
                pages_done += 1

    write_stage_lineage("04_tag", lineage)
    write_stage_issues("04_tag", issues)
    write_report("04_tag", {"filings": len(filings), "pages": pages_done,
                            "tags": dict(tag_counts), "reocr_pages": reocr,
                            "lineage_rows": len(lineage), "issues": len(issues)})
    actions = [Action("Run route", "python pipeline/06_route.py")]
    if reocr:
        actions.insert(0, Action(f"Fix {reocr} ReOCR pages", "python pipeline/05_fix.py"))
    emit_decision("04_tag", f"Tagged {pages_done:,} pages in {len(filings)} filings. "
                  f"{reocr} flagged ReOCR. Tags: {dict(tag_counts)}", actions=actions)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
