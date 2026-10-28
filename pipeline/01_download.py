#!/usr/bin/env python3
"""Step 1 — Download PDFs from PSX financials portal using Playwright.

Navigates financials.psx.com.pk, finds each ticker's filing links per year,
and downloads Annual + Quarterly PDFs into database_pdfs/<ticker>/<year>/.

Skips filings that already exist locally. Safe to re-run.

Output: database_pdfs/<ticker>/<year>/<ticker>_<type>_<date>.pdf

Usage:
    python pipeline/01_download.py                        # all tickers, 2021-2026
    python pipeline/01_download.py --start-year 2025      # from 2025 only
    python pipeline/01_download.py --ticker AABS          # single ticker
    python pipeline/01_download.py --ticker AABS --year 2026

Requires: pip install playwright && playwright install chromium
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
load_dotenv()
from shared.constants import PROJECT_ROOT, ARTIFACTS  # noqa: E402
from shared.report import write_report, emit_decision, Action  # noqa: E402

LOCAL_PDF_DIR = PROJECT_ROOT / "database_pdfs"
TICKERS_FILE = PROJECT_ROOT / "tickers100.json"
BASE_URL = "https://financials.psx.com.pk/"

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

MODAL_SELECTORS = [
    "div.modal.fade.show", "div.modal.show", "div.modal[style*='display: block']",
    "div[role='dialog'][aria-modal='true']", "#myModal[style*='display: block']",
    ".modal:visible", "div.modal.in",
]

FILES_DOWNLOADED = 0
FILES_SKIPPED = 0


def sanitize(value: str) -> str:
    cleaned = re.sub(r"[^\w\-]+", "_", value.strip())
    return cleaned.strip("_") or "value"


def report_stem(ticker: str, report_type: str, period_date: str, year: int) -> str:
    identifier = sanitize(period_date) if period_date and period_date != "Unknown" else str(year)
    return f"{ticker.upper()}_{sanitize(report_type)}_{identifier}"


def report_exists(ticker: str, year: int, report_type: str, period_date: str) -> bool:
    d = LOCAL_PDF_DIR / ticker.upper() / str(year)
    if not d.exists():
        return False
    stem = report_stem(ticker, report_type, period_date, year)
    return any(d.glob(f"{stem}.*"))


def count_local(ticker: str, year: int) -> int:
    d = LOCAL_PDF_DIR / ticker.upper() / str(year)
    return sum(1 for p in d.glob("*.pdf")) if d.exists() else 0


def click_el(page, element, description: str):
    try:
        page.evaluate("el => el.click()", element)
        return True
    except Exception:
        try:
            element.click()
            return True
        except Exception as e:
            logger.error(f"Click failed for {description}: {e}")
            raise


def close_modal(page):
    try:
        for sel in MODAL_SELECTORS:
            modal = page.query_selector(sel)
            if modal and modal.is_visible():
                btn = modal.query_selector("button.close, .btn-close, button:has-text('Close'), button:has-text('×')")
                try:
                    if btn and btn.is_visible():
                        click_el(page, btn, "modal close")
                    else:
                        page.keyboard.press("Escape")
                except Exception:
                    page.keyboard.press("Escape")
                time.sleep(1)
                break
    except Exception as e:
        logger.warning(f"close_modal: {e}")


def click_year(page, year: int):
    selectors = [
        f"a:has-text('{year}')", f".sidebar a:has-text('{year}')",
        f"#sidebar a:has-text('{year}')", f"nav a:has-text('{year}')",
        f"li a:has-text('{year}')",
    ]
    for sel in selectors:
        links = page.query_selector_all(sel)
        if links:
            exact = next((l for l in links if l.inner_text().strip() == str(year)), None)
            link = exact or links[0]
            click_el(page, link, f"year {year}")
            page.wait_for_load_state("networkidle")
            time.sleep(5)
            return
    logger.warning(f"Year {year} link not found")


def find_download_button(page, company_el):
    try:
        box = company_el.bounding_box()
        if not box:
            return None
        buttons = page.query_selector_all("button:has-text('Download File')")
        best, min_dist = None, float("inf")
        for btn in buttons:
            b = btn.bounding_box()
            if not b:
                continue
            yd = abs(b["y"] - box["y"])
            xd = b["x"] - (box["x"] + box["width"])
            if yd < 20 and xd > 0:
                d = yd * 3 + xd
                if d < min_dist:
                    min_dist, best = d, btn
        return best
    except Exception as e:
        logger.error(f"find_download_button: {e}")
        return None


def download_file(page, link, ticker: str, report_type: str, period_date: str, year: int, idx: int) -> bool:
    global FILES_DOWNLOADED, FILES_SKIPPED
    link_text = link.inner_text().strip() or f"Link_{idx}"
    try:
        if report_exists(ticker, year, report_type, period_date):
            logger.debug(f"Skip (exists): {ticker} {report_type} {period_date}")
            FILES_SKIPPED += 1
            return True
        with page.expect_download(timeout=20000) as dl_info:
            click_el(page, link, f"download '{link_text}'")
            time.sleep(0.5)
        dl = dl_info.value
        ext = Path(dl.suggested_filename or "").suffix or ".pdf"
        dest = LOCAL_PDF_DIR / ticker.upper() / str(year)
        dest.mkdir(parents=True, exist_ok=True)
        path = dest / f"{report_stem(ticker, report_type, period_date, year)}{ext}"
        dl.save_as(str(path))
        FILES_DOWNLOADED += 1
        logger.info(f"Downloaded: {path.name}")
        return True
    except Exception as e:
        if "closed" in str(e).lower():
            raise
        logger.error(f"Download failed '{link_text}': {e}")
        return False


def handle_modal(page, ticker: str, download_btn, target_year: int) -> int:
    count = 0
    modal_sel = None
    try:
        click_el(page, download_btn, f"{ticker} download button")
        time.sleep(1)
        for sel in MODAL_SELECTORS:
            m = page.query_selector(sel)
            if m and m.is_visible():
                modal_sel = sel
                break
        if not modal_sel:
            logger.warning(f"No modal for {ticker}")
            return 0
        time.sleep(1)

        # Extract period dates from modal
        period_dates: dict[str, str] = {}
        quarterly_dates: list[str] = []
        for sel in [
            f"{modal_sel} .modal-body p:has-text('Period')",
            f"{modal_sel} .modal-body div:has-text('Period')",
            f"{modal_sel} .modal-body:has-text('Period')",
            f"{modal_sel} table",
        ]:
            el = page.query_selector(sel)
            if el:
                text = el.inner_text().strip()
                for line in text.split("\n"):
                    if "Quarterly" in line:
                        m = re.search(r"(\d{4}-\d{2}-\d{2})", line)
                        if m:
                            quarterly_dates.append(m.group(1))
                    if "Annual" in line:
                        m = re.search(r"\b(20\d{2})\b", line)
                        if m:
                            period_dates["Annual"] = m.group(1)
                if not quarterly_dates and "Annual" not in period_dates:
                    dates = re.findall(r"(\d{4}-\d{2}-\d{2})", text)
                    if dates:
                        period_dates["default"] = dates[0]
                    years = re.findall(r"\b(20\d{2})\b", text)
                    if years and "Annual" not in period_dates:
                        period_dates["Annual"] = years[0]
                break

        links = page.query_selector_all(f"{modal_sel} a") or page.query_selector_all("div.modal a")

        # Filter to target year
        filtered = []
        for link in links:
            try:
                lt = link.inner_text().strip()
                if lt.isdigit():
                    if int(lt) == target_year:
                        filtered.append(link)
                    continue
                ym = re.search(r"\((\d{4})\)", lt)
                if ym:
                    if int(ym.group(1)) == target_year:
                        filtered.append(link)
                    continue
                filtered.append(link)
            except Exception:
                filtered.append(link)

        if not filtered:
            close_modal(page)
            return 0

        q_count = 0
        for j, link in enumerate(filtered):
            try:
                lt = link.inner_text().strip() or f"Link {j+1}"
                lt_low = lt.lower()
                if "annual" in lt_low:
                    rtype, pdate = "Annual", period_dates.get("Annual", "Unknown")
                elif "quarter" in lt_low:
                    q_count += 1
                    rtype = "Quarterly"
                    pdate = quarterly_dates[q_count - 1] if q_count - 1 < len(quarterly_dates) else (quarterly_dates[-1] if quarterly_dates else "Unknown")
                else:
                    rtype, pdate = "Report", period_dates.get("default", "Unknown")

                # Year guard
                if pdate != "Unknown":
                    if rtype == "Annual" and pdate != str(target_year):
                        continue
                    if rtype == "Quarterly" and not pdate.startswith(str(target_year)):
                        continue

                if download_file(page, link, ticker, rtype, pdate, target_year, j + 1):
                    count += 1
            except Exception as e:
                if "closed" in str(e).lower():
                    raise
                logger.error(f"Link {j+1} error: {e}")
    except Exception as e:
        logger.error(f"Modal error for {ticker}: {e}")
    finally:
        if modal_sel:
            for sel in [
                f"{modal_sel} button.close", f"{modal_sel} .btn-close",
                f"{modal_sel} button:has-text('Close')", "[aria-label='Close']",
            ]:
                btn = page.query_selector(sel)
                if btn and btn.is_visible():
                    try:
                        click_el(page, btn, "close modal")
                    except Exception:
                        pass
                    break
            else:
                page.keyboard.press("Escape")
        time.sleep(2)
    return count


def process_year(page, tickers: list[dict], target_year: int, ticker_filter: str | None) -> int:
    total = 0
    skipped = 0
    for info in tickers:
        ticker = info["Symbol"]
        if ticker_filter and ticker.upper() != ticker_filter.upper():
            continue
        if count_local(ticker, target_year) >= 4:
            skipped += 1
            continue
        logger.info(f"Processing {ticker} / {target_year}")
        elements = [el for el in page.query_selector_all("td") if el.inner_text().strip() == ticker]
        if not elements:
            logger.warning(f"  {ticker} not found in table")
            continue
        for el in elements:
            close_modal(page)
            btn = find_download_button(page, el)
            if not btn:
                logger.warning(f"  No download button for {ticker}")
                continue
            total += handle_modal(page, ticker, btn, target_year)
    logger.info(f"Year {target_year}: {total} downloads, {skipped} tickers skipped (already complete)")
    return total


def main() -> int:
    global FILES_DOWNLOADED, FILES_SKIPPED

    ap = argparse.ArgumentParser(description="Step 1 — download PDFs from PSX")
    ap.add_argument("--ticker")
    ap.add_argument("--year", type=int)
    ap.add_argument("--start-year", type=int, default=2021)
    ap.add_argument("--end-year", type=int, default=2026)
    a = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright not installed. Run: pip install playwright && playwright install chromium")
        return 1

    LOCAL_PDF_DIR.mkdir(parents=True, exist_ok=True)
    tickers = json.loads(TICKERS_FILE.read_text()) if TICKERS_FILE.exists() else []
    years = [a.year] if a.year else list(range(a.start_year, a.end_year + 1))

    t0 = time.time()
    total = 0
    browser = None
    try:
        p = sync_playwright().start()
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(accept_downloads=True, viewport={"width": 1280, "height": 800})
        page = ctx.new_page()

        logger.info(f"Navigating to {BASE_URL}")
        page.goto(BASE_URL)
        page.wait_for_load_state("networkidle")
        time.sleep(3)

        for year in years:
            logger.info(f"=== Year {year} ===")
            click_year(page, year)
            try:
                page.wait_for_selector("button:has-text('Download File')", timeout=30000)
            except Exception:
                logger.error(f"Table did not load for {year}, skipping")
                continue
            close_modal(page)
            total += process_year(page, tickers, year, a.ticker)

    except Exception as e:
        logger.error(f"Fatal: {e}")
        logger.error(traceback.format_exc())
        return 1
    finally:
        if browser:
            browser.close()

    elapsed = time.time() - t0
    rep = {"downloaded": FILES_DOWNLOADED, "skipped": FILES_SKIPPED,
           "years": years, "elapsed_s": round(elapsed, 1)}
    write_report("01_download", rep)
    emit_decision("01_download", f"Downloaded {FILES_DOWNLOADED} new PDFs ({FILES_SKIPPED} already existed).",
                  actions=[Action("Run ingest coverage check", "python pipeline/01_ingest.py"),
                           Action("Parse new PDFs", "python pipeline/02_parse.py --gap")])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
