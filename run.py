#!/usr/bin/env python3
"""psxgpt-pipeline runner.

Runs named stage chains in the flat pipeline/  directory.
Each stage is a numbered script; chains wire them together.

Usage:
    python3 run.py --chain pl --ticker HBL
    python3 run.py --chain extract --ticker HBL --year 2024
    python3 run.py --chain full
    python3 run.py --only 09_qc --chain pl --ticker HBL
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent
PY = str(ROOT / ".venv" / "bin" / "python") if (ROOT / ".venv" / "bin" / "python").exists() else sys.executable
P = "pipeline"


@dataclass(frozen=True)
class Stage:
    name: str
    script: str
    args: Callable[[argparse.Namespace], list[str]] = field(default=lambda _a: [])


def _scoped(*extra: str) -> Callable[[argparse.Namespace], list[str]]:
    def build(a: argparse.Namespace) -> list[str]:
        out: list[str] = []
        if a.ticker:
            out += ["--ticker", a.ticker.upper()]
        out += list(extra)
        return out
    return build


def _typed(stmt: str) -> Callable[[argparse.Namespace], list[str]]:
    def build(a: argparse.Namespace) -> list[str]:
        out = ["--type", stmt]
        if a.ticker:
            out += ["--ticker", a.ticker.upper()]
        return out
    return build


def _extract_args(stmt: str) -> Callable[[argparse.Namespace], list[str]]:
    def build(a: argparse.Namespace) -> list[str]:
        out = ["--type", stmt]
        if a.ticker:
            out += ["--ticker", a.ticker.upper()]
        if a.year:
            out += ["--year", str(a.year)]
        return out
    return build


def _publish_args(a: argparse.Namespace) -> list[str]:
    out: list[str] = []
    if a.ticker:
        out += ["--ticker", a.ticker.upper()]
    return out


def _documents_args(a: argparse.Namespace) -> list[str]:
    out: list[str] = []
    if a.ticker:
        out += ["--ticker", a.ticker.upper()]
    if getattr(a, "push_d1", False):
        out += ["--push-d1"]
    if getattr(a, "download_only", False):
        out += ["--download-only"]
    if getattr(a, "parse_markdown", False):
        out += ["--parse-markdown"]
    if getattr(a, "reset_markdown", False):
        out += ["--reset-markdown"]
    if getattr(a, "pdf_only", False):
        out += ["--pdf-only"]
    if getattr(a, "universe_only", False):
        out += ["--universe-only"]
    if getattr(a, "workers", None):
        out += ["--workers", str(a.workers)]
    if getattr(a, "limit", None):
        out += ["--limit", str(a.limit)]
    return out


def _scs_ratio_args(a: argparse.Namespace) -> list[str]:
    out = _scoped()(a)
    if getattr(a, "scs_only", False):
        out += ["--scs-only"]
    if getattr(a, "limit", None):
        out += ["--limit", str(a.limit)]
    return out


def _announcements_args(a: argparse.Namespace) -> list[str]:
    out: list[str] = []
    if a.ticker:
        out += ["--ticker", a.ticker.upper()]
    if getattr(a, "date", None):
        out += ["--date", a.date]
    elif getattr(a, "start_date", None) or getattr(a, "end_date", None):
        if a.start_date:
            out += ["--start-date", a.start_date]
        if a.end_date:
            out += ["--end-date", a.end_date]
    elif getattr(a, "days", None):
        out += ["--days", str(a.days)]
    for flag in ("download_only", "parse_markdown", "reset_markdown", "pdf_only", "universe_only", "include_parse_failures", "upload_assets", "force_thumbs"):
        if getattr(a, flag, False):
            out.append("--" + flag.replace("_", "-"))
    if getattr(a, "workers", None):
        out += ["--workers", str(a.workers)]
    if getattr(a, "push_d1", False):
        out += ["--push-d1"]
    return out


def _assets_args(a: argparse.Namespace) -> list[str]:
    # Scoped to one ticker, else the whole corpus (manifest skips already-uploaded).
    return ["--tickers", a.ticker.upper()] if a.ticker else ["--all"]


DOWNLOAD = [
    Stage("01_download", f"{P}/01_download.py",  _scoped()),
]

SPLIT = [
    Stage("01_split",    f"{P}/01_ingest.py",    lambda a: (["--split", "--ticker", a.ticker.upper()] if a.ticker else ["--split"])),
]

PARSE = [
    Stage("01_ingest",   f"{P}/01_ingest.py",   _scoped()),
    Stage("02_parse",    f"{P}/02_parse.py",     lambda a: ["--gap"]),
    Stage("03_gate",     f"{P}/03_gate.py",      _scoped()),
    Stage("04_tag",      f"{P}/04_tag.py",       _scoped()),
    Stage("05_fix",      f"{P}/05_fix.py",       _scoped()),
    Stage("06_route",    f"{P}/06_route.py",     _scoped()),
]

EXTRACT_PL = [
    Stage("08_extract_pl",  f"{P}/08_extract.py",   _extract_args("pl")),
    Stage("09_qc_pl",       f"{P}/09_qc.py",         _typed("pl")),
    Stage("normalize_pl",   f"{P}/10_normalize.py",     _typed("pl")),
    Stage("derive_pl",      f"{P}/11_derive.py",        _typed("pl")),
    Stage("flatten_pl",     f"{P}/12_flatten.py",       _typed("pl")),
]

EXTRACT_BS = [
    Stage("08_extract_bs",  f"{P}/08_extract.py",   _extract_args("bs")),
    Stage("09_qc_bs",       f"{P}/09_qc.py",         _typed("bs")),
    Stage("normalize_bs",   f"{P}/10_normalize.py",     _typed("bs")),
    Stage("flatten_bs",     f"{P}/12_flatten.py",       _typed("bs")),
]

EXTRACT_CF = [
    Stage("08_extract_cf",  f"{P}/08_extract.py",   _extract_args("cf")),
    Stage("09_qc_cf",       f"{P}/09_qc.py",         _typed("cf")),
    Stage("normalize_cf",   f"{P}/10_normalize.py",     _typed("cf")),
    Stage("derive_cf",      f"{P}/11_derive.py",        _typed("cf")),
    Stage("flatten_cf",     f"{P}/12_flatten.py",       _typed("cf")),
]

PUBLISH = [
    Stage("13_publish", f"{P}/13_publish.py", _publish_args),
]

TRIAGE = [
    Stage("14_triage", f"{P}/14_triage.py"),
]

RATIOS = [
    Stage("15_ratios", f"{P}/15_ratios.py"),
]

MARKET_QC = [
    Stage("16_market_qc", f"{P}/16_market_qc.py", _scoped()),
]

SCS_RATIO_QC = [
    Stage("16_scs_ratio_qc", f"{P}/16_scs_ratio_qc.py", _scs_ratio_args),
]

MARKET = [
    Stage("16_market_data", f"{P}/16_market_data.py", _documents_args),
    Stage("16_market_qc", f"{P}/16_market_qc.py", _scoped()),
    Stage("16_scs_ratio_qc", f"{P}/16_scs_ratio_qc.py", _scs_ratio_args),
]

STATE = [
    Stage("19_pipeline_state", f"{P}/19_pipeline_state.py"),
]

# Detect + re-parse garbled (repetition-loop) markdown pages (02_parse --reparse-broken),
# then re-publish the affected tickers listed in the 02_parse report.
REPARSE = [
    Stage("02_reparse_broken", f"{P}/02_parse.py", lambda a: ["--reparse-broken"]),
]

# Page/document corpus publish — the full source-corpus flow:
#   17_publish_documents financial_documents_new + filings_fts_new  (SQL-only unless --push-d1)
#   18_publish_assets    thumbnails + page PDFs to R2                (only with --upload-assets)
# Run alongside 13_d1_push + 15_ratios after a universe expansion. Example:
#   python3 run.py --chain documents --ticker FECTC --push-d1 --upload-assets
DOCUMENTS = [
    Stage("17_publish_documents", f"{P}/17_publish_documents.py", _documents_args),
    Stage("18_publish_assets", f"{P}/18_publish_assets.py", _assets_args),  # gated on --upload-assets
    *STATE,  # recompute the cross-stage state ledger after publishing so drift is caught immediately
]

# Ticker/date corporate announcements from psx.com.pk into psx_announcements +
# psx_announcements_fts. SQL-only unless --push-d1 is passed.
ANNOUNCEMENTS = [
    Stage("20_publish_announcements", f"{P}/20_publish_announcements.py", _announcements_args),
]

CHAINS: dict[str, list[Stage]] = {
    "download":  DOWNLOAD,
    "split":     SPLIT,
    "parse":     PARSE,
    "pl":        [*PARSE, *EXTRACT_PL],
    "bs":        [*PARSE, *EXTRACT_BS],
    "cf":        [*PARSE, *EXTRACT_CF],
    "extract":   [*PARSE, *EXTRACT_PL, *EXTRACT_BS, *EXTRACT_CF],
    "publish":   PUBLISH,
    "reparse":   REPARSE,
    "documents": DOCUMENTS,
    "announcements": ANNOUNCEMENTS,
    "triage":    TRIAGE,
    "ratios":    RATIOS,
    "market":    MARKET,
    "market_qc": MARKET_QC,
    "scs_ratio_qc": SCS_RATIO_QC,
    "state":     STATE,
    "full":      [*PARSE, *EXTRACT_PL, *EXTRACT_BS, *EXTRACT_CF, *PUBLISH],
}


def selected_stages(chain: str, only: str | None) -> list[Stage]:
    stages = CHAINS[chain]
    if only:
        stages = [s for s in stages if s.name == only]
        if not stages:
            valid = ", ".join(s.name for s in CHAINS[chain])
            raise SystemExit(f"Unknown --only stage for chain '{chain}': {only}\nValid: {valid}")
    return stages


def run_stage(stage: Stage, a: argparse.Namespace) -> bool:
    # Asset upload writes to remote storage; only run it when explicitly asked.
    if stage.name == "18_publish_assets" and not getattr(a, "upload_assets", False):
        print(f"\nSTAGE: {stage.name} — skipped (pass --upload-assets to upload page PDFs/thumbnails to R2)")
        return True
    args = stage.args(a)
    cmd = [PY, stage.script, *args]
    print(f"\n{'=' * 72}\nSTAGE: {stage.name}\nCMD: {' '.join(cmd)}\n{'=' * 72}")
    return subprocess.run(cmd, cwd=ROOT).returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Run psxgpt-pipeline stage chains")
    ap.add_argument("--chain", default="pl", choices=sorted(CHAINS))
    ap.add_argument("--ticker", help="Ticker scope where the stage supports it")
    ap.add_argument("--year", help="Filing year scope for extraction stages")
    ap.add_argument("--only", help="Run one stage from the selected chain by name")
    ap.add_argument("--push-d1", dest="push_d1", action="store_true", help="documents chain: push generated SQL to remote D1")
    ap.add_argument("--upload-assets", dest="upload_assets", action="store_true", help="documents chain: upload page PDFs/thumbnails to R2")
    ap.add_argument("--force-thumbs", dest="force_thumbs", action="store_true", help="asset chains: regenerate thumbnails before upload")
    ap.add_argument("--scs-only", dest="scs_only", action="store_true", help="scs_ratio_qc chain: only compare tickers present in SCS reference rows")
    ap.add_argument("--limit", type=int, help="scs_ratio_qc chain: limit compared tickers for rehearsal")
    ap.add_argument("--date", help="announcements chain: single announcement date, YYYY-MM-DD")
    ap.add_argument("--start-date", dest="start_date", help="announcements chain: start date, YYYY-MM-DD")
    ap.add_argument("--end-date", dest="end_date", help="announcements chain: end date, YYYY-MM-DD")
    ap.add_argument("--days", type=int, help="announcements chain: look back this many days")
    ap.add_argument("--download-only", dest="download_only", action="store_true", help="announcements chain: download files and reports only; do not generate SQL or push D1")
    ap.add_argument("--parse-markdown", dest="parse_markdown", action="store_true", help="announcements chain: parse attachments into announcement_markdown")
    ap.add_argument("--reset-markdown", dest="reset_markdown", action="store_true", help="announcements chain: overwrite existing announcement markdown")
    ap.add_argument("--pdf-only", dest="pdf_only", action="store_true", help="announcements chain debug filter: keep only PDF announcements")
    ap.add_argument("--universe-only", dest="universe_only", action="store_true", help="announcements chain: keep only tickers present in tickers100.json")
    ap.add_argument("--include-parse-failures", dest="include_parse_failures", action="store_true", help="announcements chain debug flag: publish Gemini parse failure placeholders")
    ap.add_argument("--workers", type=int, help="announcements chain: parallel download worker count")
    a = ap.parse_args()
    print(f"Chain: {a.chain}")
    for stage in selected_stages(a.chain, a.only):
        if not run_stage(stage, a):
            print(f"\n[GATE] Stage '{stage.name}' failed. Fix it and rerun with --only {stage.name}.")
            return 1
    print("\nChain complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
