#!/usr/bin/env python3
"""Step 8 — Extract (DeepSeek V4 Pro, registry-driven). One extractor for all statement
types; per-type config in registry.py. Reads the route manifest. Emits reported values
into Ref-column markdown (the model never derives Q4/LTM).

Input:  artifacts/06_route/manifest.json  (falls back to legacy step2_statement_pages.json)
Output: data/extracted_{type}/{ticker}_{period}_{basis}.md ; artifacts/reports/08_extract.json

Usage:
    python pipeline/08_extract.py --type cf --ticker AABS --annual-only
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types
from openai import OpenAI

sys.path.insert(0, str(Path(__file__).resolve().parent))
load_dotenv()
from shared.prompt_packs import read_statement_prompt_pack  # noqa: E402
from shared.constants import PROJECT_ROOT, ARTIFACTS  # noqa: E402
from shared import pricing  # noqa: E402
from shared.report import write_report, emit_decision, Action, write_stage_issues, write_stage_lineage  # noqa: E402
from registry import STATEMENTS  # noqa: E402

MARKDOWN_DIR = PROJECT_ROOT / "markdown_pages"
PDF_DIR = PROJECT_ROOT / "pdf_pages"
ROUTE_MANIFEST = ARTIFACTS / "06_route" / "manifest.json"
LEGACY_MANIFEST = ARTIFACTS / "06_route" / "statement_pages.json"
ISSUES_DIR = ARTIFACTS / "issues"
TICKERS_FILE = PROJECT_ROOT / "tickers100.json"
SCHEMA_FILE = PROJECT_ROOT / "canonical_schema_fixed.json"
BASE = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com")
MODEL = os.getenv("DEEPSEEK_EXTRACT_MODEL", "deepseek-v4-pro")
GEMINI_MODEL = os.getenv("GEMINI_EXTRACT_MODEL", os.getenv("GEMINI_PARSE_MODEL", "gemini-3.1-flash-lite"))
MONTHS = {1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
          7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec"}
MONTHS_FULL = {1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
               7: "July", 8: "August", 9: "September", 10: "October", 11: "November", 12: "December"}


def company_type(ticker, industries):
    ind = industries.get(ticker, "").lower()
    if "bank" in ind:
        return "BANK"
    if "insurance" in ind or "takaful" in ind:
        return "INSURANCE"
    return "CORPORATE"


def markdown_path(ticker, period):
    if period.startswith("annual_"):
        y = period.replace("annual_", "")
        return MARKDOWN_DIR / ticker / y / f"{ticker}_Annual_{y}"
    if period.startswith("quarterly_"):
        dp = period.replace("quarterly_", "")
        return MARKDOWN_DIR / ticker / dp.split("-")[0] / f"{ticker}_Quarterly_{dp}"
    return Path()


def pdf_path(ticker, period):
    if period.startswith("annual_"):
        y = period.replace("annual_", "")
        return PDF_DIR / ticker / y / f"{ticker}_Annual_{y}"
    if period.startswith("quarterly_"):
        dp = period.replace("quarterly_", "")
        return PDF_DIR / ticker / dp.split("-")[0] / f"{ticker}_Quarterly_{dp}"
    return Path()


def load_pages(path, nums):
    return [(n, (path / f"page_{n:03d}.md").read_text()) for n in nums if (path / f"page_{n:03d}.md").exists()]


def build_instruction(cfg, ticker, period, section, ctype, schema, fiscal):
    fields = schema.get(ctype, schema["CORPORATE"])[cfg["schema_field"]]
    fy = int(fiscal.split("-")[0])
    repl = {"{section_label}": section.upper(), "{ticker}": ticker, "{period}": period,
            "{company_type}": ctype, "{fiscal_period}": fiscal,
            "{fy_month_name}": MONTHS_FULL.get(fy, "June"),
            "{fy_start_month_name}": MONTHS_FULL.get((fy % 12) + 1, "July"),
            "{type_note}": cfg["notes"].get(ctype, ""),
            "{', '.join(pl_fields)}": ", ".join(fields), "{', '.join(bs_fields)}": ", ".join(fields),
            "{', '.join(cf_fields)}": ", ".join(fields)}
    if cfg["quarter_mapping"]:
        q = [((fy + k - 1) % 12) + 1 for k in (3, 6, 9)] + [fy]
        repl["{quarter_mapping}"] = (f"- {MONTHS[q[0]]} period → 3M (Q1)\n"
                                     f"- {MONTHS[q[1]]} period → 6M cumulative or 3M if \"Quarter ended\"\n"
                                     f"- {MONTHS[q[2]]} period → 9M cumulative or 3M if \"Quarter ended\"\n"
                                     f"- {MONTHS[q[3]]} period → 12M (full year) or 3M if \"Quarter ended\"")
    body = read_statement_prompt_pack(cfg["pack"], repl)
    if body is None:
        raise FileNotFoundError(f"context/{cfg['pack']} missing")
    return body


def build_markdown_prompt(cfg, pages, ticker, period, section, ctype, schema, fiscal):
    content = "\n\n---\n\n".join(f"<!-- Page {pg} -->\n{c}" for pg, c in pages)
    return build_instruction(cfg, ticker, period, section, ctype, schema, fiscal) + "\n\n## SOURCE PAGES\n\n" + content


def load_pdf_parts(path: Path, nums: list[int]) -> list[tuple[int, types.Part]]:
    parts = []
    for n in nums:
        pdf = path / f"page_{n:03d}.pdf"
        if pdf.exists():
            parts.append((n, types.Part.from_bytes(data=pdf.read_bytes(), mime_type="application/pdf")))
    return parts


def clean(out):
    out = out.strip()
    for f in ("```markdown", "```"):
        if out.startswith(f):
            out = out[len(f):].strip()
    return out[:-3].strip() if out.endswith("```") else out


def call(client, prompt):
    msgs = [{"role": "system", "content": "You extract financial statements from PSX filings into structured markdown tables. Output ONLY the markdown, no explanations."},
            {"role": "user", "content": prompt}]
    for i in range(1, 4):
        try:
            return client.chat.completions.create(model=MODEL, messages=msgs, temperature=0.1).choices[0].message.content or ""
        except Exception as e:
            if i == 3:
                raise RuntimeError(f"DeepSeek failed: {e}") from e
            time.sleep(5.0)
    return ""


def call_gemini(client: genai.Client, prompt: str, pdf_parts: list[types.Part]) -> str:
    contents = [
        "You extract financial statements from PSX filing page PDFs into structured markdown tables. "
        "Use the attached PDF pages as the source of truth. Output ONLY the markdown, no explanations.",
        prompt,
        *pdf_parts,
    ]
    for i in range(1, 4):
        try:
            resp = client.models.generate_content(model=GEMINI_MODEL, contents=contents)
            return resp.text or ""
        except Exception as e:
            if i == 3:
                raise RuntimeError(f"Gemini failed: {e}") from e
            time.sleep(5.0)
    return ""


def repair_keys(stmt_type: str, ticker: str | None, year: str | None) -> set[str]:
    path = ISSUES_DIR / f"09_qc_{stmt_type}.jsonl"
    keys = set()
    if not path.exists():
        return keys
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if ticker and row.get("ticker") != ticker:
            continue
        if year and f"_{year}" not in str(row.get("filing") or ""):
            continue
        source_file = row.get("source_file") or ""
        if source_file.endswith(".md"):
            keys.add(source_file[:-3])
    return keys


def main() -> int:
    ap = argparse.ArgumentParser(description="Step 8 — extract")
    ap.add_argument("--type", required=True, choices=sorted(STATEMENTS))
    ap.add_argument("--ticker"); ap.add_argument("--year")
    ap.add_argument("--annual-only", action="store_true")
    ap.add_argument("--limit", type=int); ap.add_argument("--reset", action="store_true")
    ap.add_argument("--source", choices=["markdown", "pdf"], default="markdown",
                    help="Source input for extraction. Use --source pdf for Gemini repair from original PDF pages.")
    ap.add_argument("--repair-failures", action="store_true",
                    help="Only rerun statements currently failing Step 09 QC for this statement type.")
    ap.add_argument("--workers", type=int, default=50)
    a = ap.parse_args()
    cfg = STATEMENTS[a.type]

    manifest_path = ROUTE_MANIFEST if ROUTE_MANIFEST.exists() else LEGACY_MANIFEST
    pages_map = json.load(open(manifest_path))
    tickers = json.loads(TICKERS_FILE.read_text()) if TICKERS_FILE.exists() else []
    industries = {t["Symbol"]: t.get("Industry", "") for t in tickers}
    fiscal = {t["Symbol"]: t.get("fiscal_period", "06-30") for t in tickers}
    schema = json.load(open(SCHEMA_FILE))
    out_dir = PROJECT_ROOT / cfg["output_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)
    ckpt = PROJECT_ROOT / cfg["checkpoint"]
    done = set() if a.reset else set(json.load(open(ckpt)).get("completed", [])) if ckpt.exists() else set()

    deepseek_client = None
    gemini_client = None
    if a.source == "pdf":
        gemini_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    else:
        deepseek_client = OpenAI(api_key=os.environ["DEEPSEEK_API_KEY"], base_url=BASE)
    targets = repair_keys(a.type, a.ticker.upper() if a.ticker else None, a.year) if a.repair_failures else None
    work = []
    for ticker, periods in pages_map.items():
        if a.ticker and ticker != a.ticker:
            continue
        ctype = company_type(ticker, industries)
        for period, sections in periods.items():
            if a.year and f"_{a.year}" not in period:
                continue
            if a.annual_only and not period.startswith("annual_"):
                continue
            for section in ("consolidated", "unconsolidated"):
                pgs = sections.get(section, {}).get(cfg["stmt_key"], [])
                if not pgs:
                    continue
                k = f"{ticker}_{period}_{section}"
                if targets is not None and k not in targets:
                    continue
                if not a.reset and k in done:
                    continue
                work.append((ticker, period, section, pgs, ctype, fiscal.get(ticker, "06-30"), k))
    if a.limit:
        work = work[: a.limit]
    if not work:
        emit_decision("08_extract", f"[{a.type}] nothing to extract (manifest: {manifest_path.name}).")
        return 0

    ok = 0
    lineage = []
    issues = []
    def process(it):
        ticker, period, section, pgs, ctype, fp, k = it
        source_folder = markdown_path(ticker, period)
        pdf_folder = pdf_path(ticker, period)
        output_file = out_dir / f"{k}.md"
        try:
            if a.source == "pdf":
                loaded = load_pdf_parts(pdf_folder, pgs)
                if not loaded:
                    return (k, False, "no source PDF pages", {
                        "ticker": ticker, "filing": period, "filing_period": period,
                        "statement_type": a.type, "section": section, "source_pages": pgs,
                        "source_folder": str(pdf_folder.relative_to(PROJECT_ROOT)) if pdf_folder else "",
                        "output_file": str(output_file.relative_to(PROJECT_ROOT)),
                    })
                src_pages = [pg for pg, _ in loaded]
                prompt = build_instruction(cfg, ticker, period, section, ctype, schema, fp)
                text = call_gemini(gemini_client, prompt, [part for _, part in loaded])  # type: ignore[arg-type]
                source_rel = [str((pdf_folder / f"page_{pg:03d}.pdf").relative_to(PDF_DIR)) for pg in src_pages]
                model = GEMINI_MODEL
            else:
                src = load_pages(source_folder, pgs)
                if not src:
                    return (k, False, "no source markdown pages", {
                        "ticker": ticker, "filing": period, "filing_period": period,
                        "statement_type": a.type, "section": section, "source_pages": pgs,
                        "source_folder": str(source_folder.relative_to(PROJECT_ROOT)) if source_folder else "",
                        "output_file": str(output_file.relative_to(PROJECT_ROOT)),
                    })
                text = call(deepseek_client, build_markdown_prompt(cfg, src, ticker, period, section, ctype, schema, fp))  # type: ignore[arg-type]
                source_rel = [str((source_folder / f"page_{pg:03d}.md").relative_to(MARKDOWN_DIR)) for pg, _ in src]
                model = MODEL
            output_file.write_text(clean(text))
            return (k, True, None, {
                "ticker": ticker, "filing": period, "filing_period": period,
                "statement_type": a.type, "section": section, "source_pages": pgs,
                "source_relative_paths": source_rel,
                "output_file": str(output_file.relative_to(PROJECT_ROOT)),
                "model": model, "input_source": a.source, "company_type": ctype, "fiscal_period": fp,
                "origin_stage": "06_route",
            })
        except Exception as e:
            return (k, False, str(e), {
                "ticker": ticker, "filing": period, "filing_period": period,
                "statement_type": a.type, "section": section, "source_pages": pgs,
                "source_folder": str(source_folder.relative_to(PROJECT_ROOT)) if source_folder else "",
                "output_file": str(output_file.relative_to(PROJECT_ROOT)),
            })

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for fut in as_completed([ex.submit(process, it) for it in work]):
            k, success, err, meta = fut.result()
            if success:
                ok += 1; done.add(k)
                lineage.append({**meta, "status": "ok", "job_key": k})
                ckpt.parent.mkdir(parents=True, exist_ok=True)
                json.dump({"completed": sorted(done)}, open(ckpt, "w"), indent=2)
            else:
                print(f"  FAIL {k}: {err}")
                lineage.append({**meta, "status": "failed", "job_key": k, "error": err})
                issues.append({
                    **meta,
                    "severity": "blocker",
                    "rule": "extract_job_failed",
                    "message": err,
                    "origin_stage": "08_extract",
                    "recommended_command": f"python3 pipeline/08_extract.py --type {a.type} --ticker {meta['ticker']} --source pdf --repair-failures --reset",
                })

    cost = pricing.extract_cost(ok) if a.source == "markdown" else 0.0
    write_stage_lineage("08_extract", lineage)
    write_stage_issues("08_extract", issues)
    write_report("08_extract", {"type": a.type, "extracted": ok, "of": len(work), "failed": len(issues),
                                "source": a.source, "repair_failures": a.repair_failures,
                                "model": GEMINI_MODEL if a.source == "pdf" else MODEL,
                                "lineage_rows": len(lineage), "est_cost_usd": cost})
    nxt = {"pl": "qc_extract", "bs": "qc_extract", "cf": "qc_extract"}
    emit_decision("08_extract", f"[{a.type}] extracted {ok}/{len(work)} statements → {out_dir.name}.",
                  cost_usd=cost, actions=[Action("Run QC", f"python pipeline/09_qc.py --type {a.type}")])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
