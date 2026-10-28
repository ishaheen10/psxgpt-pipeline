#!/usr/bin/env python3
"""Audit deterministic TOC-to-physical-page boundary coverage.

The audit deliberately does not classify sections or invent page tags. It:

1. Treats the unsplit PDF path as the canonical document identity.
2. Locates textual TOC pages in parsed Markdown or the opening PDF pages.
3. Extracts ordered ``(section title, printed page)`` entries.
4. Matches several TOC titles to destination-page headings/text.
5. Accepts one printed-to-physical offset only when those anchors agree.

Outputs are audit artifacts, not production indexes.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
import statistics
import subprocess
import sys
import warnings
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
PDF_ROOT = PROJECT_ROOT / "database_pdfs"
MARKDOWN_ROOT = PROJECT_ROOT / "markdown_pages"
OUTPUT_ROOT = PROJECT_ROOT / "artifacts" / "corpus_browser_document_boundaries"

TOC_HEADING = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:table\s+of\s+)?contents?\s*:?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
PAGE_COMMENT = re.compile(r"<!--\s*Page\s+(\d+)\s*-->", re.IGNORECASE)
MARKDOWN_DECORATION = re.compile(r"[*_`~#|]")
LEADING_ENTRY = re.compile(r"^\s*(\d{1,4})\s+(.{3,220}?)\s*$")
TRAILING_ENTRY = re.compile(
    r"^\s*(.{3,220}?)\s+(?:\.{2,}\s*)?(\d{1,4})\s*$"
)
TABLE_LEADING_ENTRY = re.compile(
    r"^\|\s*(?:\*\*)?(\d{1,4})(?:\*\*)?\s*\|\s*(.{3,220}?)\s*\|\s*$"
)
TABLE_TRAILING_ENTRY = re.compile(
    r"^\|\s*(.{3,220}?)\s*\|\s*(?:\*\*)?(\d{1,4})(?:\*\*)?\s*\|\s*$"
)
WORDS = re.compile(r"[a-z0-9]+")

# These are grammatical glue, not domain aliases or section-specific rules.
STOPWORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "by",
    "for",
    "from",
    "in",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}


@dataclass(frozen=True)
class TocEntry:
    title: str
    printed_page: int
    toc_physical_page: int


@dataclass(frozen=True)
class Anchor:
    title: str
    printed_page: int
    physical_page: int
    score: float
    margin: float
    page_scale_denominator: int = 1

    @property
    def offset(self) -> int:
        return self.physical_page - math.floor(
            self.printed_page / self.page_scale_denominator
        )


@dataclass(frozen=True)
class PageTransform:
    scale_denominator: int
    offset: int
    anchors: tuple[Anchor, ...]
    support_ratio: float
    score: float


@dataclass
class DocumentAudit:
    document_id: str
    pdf_path: str
    document_type: str
    physical_pages: int
    markdown_pages: int
    toc_pages: list[int]
    toc_entries: int
    matched_anchors: int
    monotonic_anchors: int
    stable_offset_anchors: int
    page_scale_denominator: int | None
    inferred_offset: int | None
    boundary_count: int
    materialized_boundaries: int
    verified_boundary_starts: int
    boundary_start_verification_pct: float
    range_integrity_ok: bool
    offset_holdout_tests: int
    offset_holdout_passes: int
    offset_holdout_accuracy_pct: float
    validation_pass: bool
    confidence: str
    reason: str
    anchors: list[dict]
    boundaries: list[dict]


def normalize_text(value: str) -> str:
    value = MARKDOWN_DECORATION.sub(" ", value)
    value = value.replace("&", " and ")
    return " ".join(WORDS.findall(value.casefold()))


def content_tokens(value: str) -> list[str]:
    return [
        token
        for token in WORDS.findall(value.casefold())
        if token not in STOPWORDS and (len(token) >= 3 or token.isdigit())
    ]


def markdown_page_number(path: Path, text: str) -> int:
    # The filename is produced by the deterministic PDF split and is the
    # canonical physical page. Gemini's embedded Page comment can instead
    # reflect a printed page label, so it must never override the filename.
    match = re.search(r"(\d+)", path.stem)
    if match:
        return int(match.group(1))
    match = PAGE_COMMENT.search(text[:200])
    return int(match.group(1)) if match else 0


def clean_entry_title(value: str) -> str:
    value = re.sub(r"^\s*[-*•]+\s*", "", value)
    value = re.sub(r"\s+", " ", value)
    value = MARKDOWN_DECORATION.sub(" ", value)
    value = re.sub(r"[\s.\-–—:]+$", "", value)
    return re.sub(r"\s+", " ", value).strip()


def plausible_entry(title: str, printed_page: int, physical_pages: int) -> bool:
    tokens = content_tokens(title)
    alpha = sum(character.isalpha() for character in title)
    return (
        2 <= len(tokens) <= 28
        and alpha >= 4
        and 0 < printed_page <= physical_pages * 4 + 80
        and normalize_text(title) not in {"contents", "table of contents"}
    )


def extract_toc_entries(
    toc_pages: list[tuple[int, str]], physical_pages: int
) -> list[TocEntry]:
    entries: list[TocEntry] = []
    seen: set[tuple[str, int]] = set()

    for physical_page, text in toc_pages:
        lines = [line.strip() for line in text.splitlines()]
        for line in lines:
            raw_line = re.sub(r"\s+", " ", line).strip()
            if not raw_line or len(raw_line) > 260:
                continue

            match = TABLE_LEADING_ENTRY.match(raw_line)
            if match:
                printed_page = int(match.group(1))
                title = clean_entry_title(match.group(2))
            else:
                match = TABLE_TRAILING_ENTRY.match(raw_line)
                if match:
                    title = clean_entry_title(match.group(1))
                    printed_page = int(match.group(2))
                else:
                    line = re.sub(r"^\s*[-*•]+\s*", "", raw_line)
                    match = LEADING_ENTRY.match(line)
                    if match:
                        printed_page = int(match.group(1))
                        title = clean_entry_title(match.group(2))
                    else:
                        match = TRAILING_ENTRY.match(line)
                        if not match:
                            continue
                        title = clean_entry_title(match.group(1))
                        printed_page = int(match.group(2))

            if not plausible_entry(title, printed_page, physical_pages):
                continue
            key = (normalize_text(title), printed_page)
            if key in seen:
                continue
            seen.add(key)
            entries.append(
                TocEntry(
                    title=title,
                    printed_page=printed_page,
                    toc_physical_page=physical_page,
                )
            )

    return entries


def ordered_entry_density(entries: list[TocEntry]) -> bool:
    if len(entries) < 6:
        return False
    ordered_pairs = sum(
        left.printed_page < right.printed_page
        for left, right in zip(entries, entries[1:])
    )
    return ordered_pairs / max(1, len(entries) - 1) >= 0.75


def pdf_text_toc_pages(
    pdf_path: Path,
    physical_pages: int,
    scan_pages: int = 15,
) -> list[tuple[int, str]]:
    last_page = min(scan_pages, physical_pages)
    if last_page <= 0:
        return []
    try:
        result = subprocess.run(
            [
                "pdftotext",
                "-f",
                "1",
                "-l",
                str(last_page),
                "-layout",
                str(pdf_path),
                "-",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0 or not result.stdout.strip():
        return []

    raw_pages = {
        page_number: text
        for page_number, text in enumerate(result.stdout.split("\f"), start=1)
        if page_number <= last_page and text.strip()
    }
    candidate_pages: set[int] = set()
    for page_number, text in raw_pages.items():
        entries = extract_toc_entries([(page_number, text)], physical_pages)
        if TOC_HEADING.search(text) or ordered_entry_density(entries):
            candidate_pages.add(page_number)

    expanded = set(candidate_pages)
    for page_number in candidate_pages:
        for neighbor in (page_number - 1, page_number + 1, page_number + 2):
            text = raw_pages.get(neighbor)
            if not text:
                continue
            entries = extract_toc_entries([(neighbor, text)], physical_pages)
            if ordered_entry_density(entries):
                expanded.add(neighbor)
    return [
        (page_number, raw_pages[page_number])
        for page_number in sorted(expanded)
    ]


def page_search_text(markdown: str) -> str:
    lines = markdown.splitlines()
    headings = [
        re.sub(r"^#{1,6}\s+", "", line).strip()
        for line in lines
        if re.match(r"^#{1,6}\s+\S", line.strip())
    ]
    # Titles overwhelmingly occur near the beginning of destination pages.
    return normalize_text(" ".join(headings) + "\n" + markdown[:2400])


def title_score(title: str, page_text: str) -> float:
    normalized_title = normalize_text(title)
    if not normalized_title or not page_text:
        return 0.0
    if normalized_title in page_text:
        return 1.0

    tokens = list(dict.fromkeys(content_tokens(title)))
    if not tokens:
        return 0.0
    matched = sum(token in page_text for token in tokens)
    coverage = matched / len(tokens)
    # Penalize short ambiguous labels unless every meaningful token matches.
    if len(tokens) <= 2 and coverage < 1:
        return 0.0
    return coverage


def best_anchor(
    entry: TocEntry,
    searchable_pages: list[tuple[int, str]],
) -> Anchor | None:
    scored = sorted(
        (
            (title_score(entry.title, page_text), physical_page)
            for physical_page, page_text in searchable_pages
            if physical_page > entry.toc_physical_page
        ),
        reverse=True,
    )
    if not scored or scored[0][0] < 0.78:
        return None
    best_score, best_page = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0
    margin = best_score - second_score
    # Exact titles may repeat in running headers. Prefer the candidate whose
    # physical/printed offset is smallest among tied exact matches.
    if best_score == 1.0:
        tied_pages = [page for score, page in scored if score == 1.0]
        best_page = min(
            tied_pages,
            key=lambda page: (abs(page - entry.printed_page), page),
        )
        margin = 1.0 if len(tied_pages) == 1 else 0.0
    elif margin < 0.08:
        return None

    return Anchor(
        title=entry.title,
        printed_page=entry.printed_page,
        physical_page=best_page,
        score=round(best_score, 3),
        margin=round(margin, 3),
    )


def fit_page_transform(
    entries: list[TocEntry],
    searchable_pages: list[tuple[int, str]],
    physical_pages: int,
) -> PageTransform | None:
    if len(entries) < 3 or not searchable_pages:
        return None
    candidate_matches: list[tuple[TocEntry, list[tuple[int, float]]]] = []
    for entry in entries:
        matches = [
            (physical_page, score)
            for physical_page, page_text in searchable_pages
            if physical_page > entry.toc_physical_page
            and (score := title_score(entry.title, page_text)) >= 0.78
        ]
        candidate_matches.append((entry, matches))

    best: PageTransform | None = None
    for denominator in range(1, 5):
        offsets: Counter[int] = Counter()
        for entry, matches in candidate_matches:
            printed_base = math.floor(entry.printed_page / denominator)
            for physical_page, _ in matches:
                offsets[physical_page - printed_base] += 1

        for offset, _ in offsets.most_common(64):
            anchors: list[Anchor] = []
            total_score = 0.0
            total_residual = 0
            exact_predictions = 0
            for entry, matches in candidate_matches:
                predicted_page = (
                    math.floor(entry.printed_page / denominator) + offset
                )
                if not 1 <= predicted_page <= physical_pages:
                    continue
                nearby_matches = [
                    (physical_page, score)
                    for physical_page, score in matches
                    if abs(physical_page - predicted_page) <= 2
                ]
                if not nearby_matches:
                    continue
                physical_page, score = max(
                    nearby_matches,
                    key=lambda item: (item[1], -abs(item[0] - predicted_page)),
                )
                anchors.append(
                    Anchor(
                        title=entry.title,
                        printed_page=entry.printed_page,
                        physical_page=physical_page,
                        score=round(score, 3),
                        margin=round(score, 3),
                        page_scale_denominator=denominator,
                    )
                )
                total_score += score
                residual = abs(physical_page - predicted_page)
                total_residual += residual
                exact_predictions += residual == 0

            if not anchors:
                continue
            unique_pages = sorted({anchor.physical_page for anchor in anchors})
            span = unique_pages[-1] - unique_pages[0] if len(unique_pages) > 1 else 0
            support_ratio = len(anchors) / len(entries)
            candidate = PageTransform(
                scale_denominator=denominator,
                offset=offset,
                anchors=tuple(sorted(anchors, key=lambda anchor: anchor.printed_page)),
                support_ratio=support_ratio,
                score=total_score,
            )
            candidate_rank = (
                len(anchors),
                exact_predictions,
                -total_residual,
                round(total_score, 6),
                span,
                -denominator,
            )
            if best is None:
                best = candidate
                best_rank = candidate_rank
            elif candidate_rank > best_rank:
                best = candidate
                best_rank = candidate_rank
    return best


def longest_monotonic(anchors: list[Anchor]) -> list[Anchor]:
    ordered = sorted(anchors, key=lambda item: (item.printed_page, item.physical_page))
    if not ordered:
        return []
    lengths = [1] * len(ordered)
    previous = [-1] * len(ordered)
    for current in range(len(ordered)):
        for prior in range(current):
            if (
                ordered[prior].printed_page < ordered[current].printed_page
                and ordered[prior].physical_page < ordered[current].physical_page
                and lengths[prior] + 1 > lengths[current]
            ):
                lengths[current] = lengths[prior] + 1
                previous[current] = prior
    cursor = max(range(len(ordered)), key=lambda index: lengths[index])
    result: list[Anchor] = []
    while cursor >= 0:
        result.append(ordered[cursor])
        cursor = previous[cursor]
    return list(reversed(result))


def stable_offset(anchors: list[Anchor]) -> tuple[int | None, list[Anchor]]:
    if not anchors:
        return None, []
    offsets = [anchor.offset for anchor in anchors]
    median = int(round(statistics.median(offsets)))
    stable = [anchor for anchor in anchors if abs(anchor.offset - median) <= 2]
    return median, stable


def classify(
    physical_pages: int,
    entries: list[TocEntry],
    monotonic: list[Anchor],
    stable: list[Anchor],
) -> tuple[str, str, int]:
    if len(entries) < 3:
        return "not_ready", "fewer than three usable textual TOC entries", 0
    if len(monotonic) < 3:
        return "not_ready", "fewer than three TOC titles align to ordered pages", 0

    anchor_span = monotonic[-1].physical_page - monotonic[0].physical_page
    span_ratio = anchor_span / max(1, physical_pages)
    stable_ratio = len(stable) / len(entries)

    if len(stable) >= 5 and stable_ratio >= 0.70 and span_ratio >= 0.20:
        return (
            "strong_textual_toc",
            "at least 70% of TOC entries support one printed-to-physical page transform",
            len(entries),
        )
    if len(monotonic) >= 3 and span_ratio >= 0.10:
        return (
            "partial_textual_toc",
            "three or more ordered TOC anchors create verified partial boundaries",
            len(monotonic),
        )
    return "not_ready", "aligned anchors are too concentrated to define useful ranges", 0


def materialize_boundaries(
    entries: list[TocEntry],
    monotonic: list[Anchor],
    stable: list[Anchor],
    scale_denominator: int | None,
    inferred_offset: int | None,
    confidence: str,
    searchable_pages: list[tuple[int, str]],
    physical_pages: int,
) -> tuple[list[dict], dict]:
    page_map = dict(searchable_pages)
    points: list[tuple[int, int, str, int]] = []

    if (
        confidence == "strong_textual_toc"
        and scale_denominator is not None
        and inferred_offset is not None
    ):
        points = [
            (
                math.floor(entry.printed_page / scale_denominator)
                + inferred_offset,
                entry.printed_page,
                entry.title,
                entry.toc_physical_page,
            )
            for entry in entries
            if 1
            <= math.floor(entry.printed_page / scale_denominator)
            + inferred_offset
            <= physical_pages
        ]
    elif confidence == "partial_textual_toc":
        toc_lookup = {
            (normalize_text(entry.title), entry.printed_page): entry.toc_physical_page
            for entry in entries
        }
        points = [
            (
                anchor.physical_page,
                anchor.printed_page,
                anchor.title,
                toc_lookup.get((normalize_text(anchor.title), anchor.printed_page), 0),
            )
            for anchor in monotonic
            if 1 <= anchor.physical_page <= physical_pages
        ]

    grouped: dict[int, list[tuple[int, str, int]]] = {}
    for physical_page, printed_page, title, toc_page in points:
        grouped.setdefault(physical_page, []).append((printed_page, title, toc_page))

    starts = sorted(grouped)
    boundaries: list[dict] = []
    for index, start_page in enumerate(starts):
        if confidence == "strong_textual_toc":
            end_page = (
                starts[index + 1] - 1
                if index + 1 < len(starts)
                else physical_pages
            )
        else:
            # With no stable transform, a matched TOC title proves a section
            # start only. It does not prove that all pages until the next
            # matched title belong to that section because intermediate TOC
            # entries may be unmatched.
            end_page = start_page
        values = grouped[start_page]
        titles = list(dict.fromkeys(title for _, title, _ in values))
        scores = [title_score(title, page_map.get(start_page, "")) for title in titles]
        start_score = max(scores, default=0.0)
        boundaries.append(
            {
                "section_titles": titles,
                "toc_physical_pages": sorted(
                    {toc_page for _, _, toc_page in values if toc_page}
                ),
                "printed_start_pages": sorted(
                    {printed_page for printed_page, _, _ in values}
                ),
                "physical_start_page": start_page,
                "physical_end_page": end_page,
                "start_title_score": round(start_score, 3),
                "start_verified": start_score >= 0.78,
                "mode": (
                    "complete_offset"
                    if confidence == "strong_textual_toc"
                    else "verified_section_start"
                ),
            }
        )

    integrity_ok = all(
        1 <= boundary["physical_start_page"] <= boundary["physical_end_page"] <= physical_pages
        and (
            confidence != "strong_textual_toc"
            or index == 0
            or boundaries[index - 1]["physical_end_page"] + 1
            == boundary["physical_start_page"]
        )
        for index, boundary in enumerate(boundaries)
    )
    verified_starts = sum(boundary["start_verified"] for boundary in boundaries)
    verification_pct = round(
        100 * verified_starts / max(1, len(boundaries)), 1
    )

    holdout_tests = 0
    holdout_passes = 0
    if confidence == "strong_textual_toc" and len(stable) >= 5:
        ordered_stable = sorted(stable, key=lambda anchor: anchor.printed_page)
        calibration_indices = {
            0,
            len(ordered_stable) // 2,
            len(ordered_stable) - 1,
        }
        calibration_offsets = [
            anchor.physical_page
            - math.floor(anchor.printed_page / (scale_denominator or 1))
            for index, anchor in enumerate(ordered_stable)
            if index in calibration_indices
        ]
        calibration_offset = int(round(statistics.median(calibration_offsets)))
        holdouts = [
            anchor
            for index, anchor in enumerate(ordered_stable)
            if index not in calibration_indices
        ]
        holdout_tests = len(holdouts)
        holdout_passes = sum(
            abs(
                anchor.physical_page
                - (
                    math.floor(
                        anchor.printed_page / (scale_denominator or 1)
                    )
                    + calibration_offset
                )
            )
            <= 2
            for anchor in holdouts
        )
    holdout_accuracy = round(
        100 * holdout_passes / max(1, holdout_tests), 1
    )

    validation_pass = (
        confidence == "strong_textual_toc"
        and bool(boundaries)
        and integrity_ok
        and verification_pct >= 70
        and holdout_tests >= 2
        and holdout_accuracy >= 80
    )
    return boundaries, {
        "verified_starts": verified_starts,
        "verification_pct": verification_pct,
        "integrity_ok": integrity_ok,
        "holdout_tests": holdout_tests,
        "holdout_passes": holdout_passes,
        "holdout_accuracy": holdout_accuracy,
        "validation_pass": validation_pass,
    }


def audit_document(pdf_path: Path) -> DocumentAudit:
    relative_pdf = pdf_path.relative_to(PDF_ROOT)
    document_id = relative_pdf.with_suffix("").as_posix()
    markdown_dir = MARKDOWN_ROOT / relative_pdf.with_suffix("")
    document_type = (
        "annual" if "_Annual_" in pdf_path.stem else "quarterly"
    )

    markdown_files = sorted(markdown_dir.glob("page_*.md"))
    pages: list[tuple[int, str]] = []
    raw_pages: dict[int, str] = {}
    explicit_toc_pages: list[int] = []
    for markdown_path in markdown_files:
        try:
            markdown = markdown_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        page_number = markdown_page_number(markdown_path, markdown)
        raw_pages[page_number] = markdown
        pages.append((page_number, page_search_text(markdown)))
        if TOC_HEADING.search(markdown):
            explicit_toc_pages.append(page_number)
    physical_pages = max((page_number for page_number, _ in pages), default=0)

    if physical_pages == 0:
        return DocumentAudit(
            document_id=document_id,
            pdf_path=str(relative_pdf),
            document_type=document_type,
            physical_pages=0,
            markdown_pages=0,
            toc_pages=[],
            toc_entries=0,
            matched_anchors=0,
            monotonic_anchors=0,
            stable_offset_anchors=0,
            page_scale_denominator=None,
            inferred_offset=None,
            boundary_count=0,
            materialized_boundaries=0,
            verified_boundary_starts=0,
            boundary_start_verification_pct=0.0,
            range_integrity_ok=False,
            offset_holdout_tests=0,
            offset_holdout_passes=0,
            offset_holdout_accuracy_pct=0.0,
            validation_pass=False,
            confidence="not_ready",
            reason="no parsed physical pages are available for section assignment",
            anchors=[],
            boundaries=[],
        )

    toc_candidates: list[tuple[tuple[int, int, int], list[int]]] = []
    for start_page in explicit_toc_pages:
        candidate_pages = [start_page]
        for candidate_page in range(start_page + 1, start_page + 3):
            candidate_text = raw_pages.get(candidate_page)
            if not candidate_text:
                break
            continuation_entries = extract_toc_entries(
                [(candidate_page, candidate_text)], physical_pages
            )
            if not ordered_entry_density(continuation_entries):
                break
            candidate_pages.append(candidate_page)
        candidate_entries = extract_toc_entries(
            [(page, raw_pages[page]) for page in candidate_pages],
            physical_pages,
        )
        if ordered_entry_density(candidate_entries):
            printed_span = (
                candidate_entries[-1].printed_page
                - candidate_entries[0].printed_page
            )
            toc_candidates.append(
                (
                    (len(candidate_entries), printed_span, -start_page),
                    candidate_pages,
                )
            )
    toc_page_numbers = set(
        max(toc_candidates, default=((0, 0, 0), []), key=lambda item: item[0])[1]
    )
    toc_pages = [
        (page_number, raw_pages[page_number])
        for page_number in sorted(toc_page_numbers)
    ]

    entries = extract_toc_entries(toc_pages, physical_pages)
    if len(entries) < 3:
        pdf_toc_pages = pdf_text_toc_pages(pdf_path, physical_pages)
        if pdf_toc_pages:
            toc_pages_by_number = dict(toc_pages)
            toc_pages_by_number.update(pdf_toc_pages)
            toc_pages = sorted(toc_pages_by_number.items())
            entries = extract_toc_entries(toc_pages, physical_pages)
    transform = fit_page_transform(entries, pages, physical_pages)
    if transform is None:
        anchors: list[Anchor] = []
        monotonic: list[Anchor] = []
        stable: list[Anchor] = []
        page_scale_denominator = None
        inferred_offset = None
    else:
        anchors = list(transform.anchors)
        monotonic = anchors
        stable = anchors
        page_scale_denominator = transform.scale_denominator
        inferred_offset = transform.offset
    confidence, reason, boundary_count = classify(
        physical_pages,
        entries,
        monotonic,
        stable,
    )
    boundaries, validation = materialize_boundaries(
        entries,
        monotonic,
        stable,
        page_scale_denominator,
        inferred_offset,
        confidence,
        pages,
        physical_pages,
    )

    return DocumentAudit(
        document_id=document_id,
        pdf_path=str(relative_pdf),
        document_type=document_type,
        physical_pages=physical_pages,
        markdown_pages=len(pages),
        toc_pages=[page for page, _ in toc_pages],
        toc_entries=len(entries),
        matched_anchors=len(anchors),
        monotonic_anchors=len(monotonic),
        stable_offset_anchors=len(stable),
        page_scale_denominator=page_scale_denominator,
        inferred_offset=inferred_offset,
        boundary_count=boundary_count,
        materialized_boundaries=len(boundaries),
        verified_boundary_starts=validation["verified_starts"],
        boundary_start_verification_pct=validation["verification_pct"],
        range_integrity_ok=validation["integrity_ok"],
        offset_holdout_tests=validation["holdout_tests"],
        offset_holdout_passes=validation["holdout_passes"],
        offset_holdout_accuracy_pct=validation["holdout_accuracy"],
        validation_pass=validation["validation_pass"],
        confidence=confidence,
        reason=reason,
        anchors=[asdict(anchor) | {"offset": anchor.offset} for anchor in monotonic[:24]],
        boundaries=boundaries,
    )


def percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    return sorted(values)[math.floor((len(values) - 1) * fraction)]


def summarize(rows: list[DocumentAudit]) -> dict:
    confidence = Counter(row.confidence for row in rows)
    by_type: dict[str, dict] = {}
    for document_type in sorted({row.document_type for row in rows}):
        subset = [row for row in rows if row.document_type == document_type]
        counts = Counter(row.confidence for row in subset)
        ready = sum(
            counts[key]
            for key in ("strong_textual_toc", "partial_textual_toc")
        )
        by_type[document_type] = {
            "documents": len(subset),
            "boundary_ready": ready,
            "boundary_ready_pct": round(100 * ready / max(1, len(subset)), 1),
            "confidence": dict(sorted(counts.items())),
        }

    ready = sum(
        confidence[key]
        for key in ("strong_textual_toc", "partial_textual_toc")
    )
    strong = confidence["strong_textual_toc"]
    strong_rows = [row for row in rows if row.confidence == "strong_textual_toc"]
    validated_rows = [row for row in strong_rows if row.validation_pass]
    materialized = [row for row in rows if row.materialized_boundaries]
    materialized_boundaries = sum(row.materialized_boundaries for row in materialized)
    verified_boundary_starts = sum(row.verified_boundary_starts for row in materialized)
    validated_strong = sum(row.validation_pass for row in strong_rows)
    validated_boundaries = sum(
        row.materialized_boundaries for row in validated_rows
    )
    validated_tagged_pages = sum(
        boundary["physical_end_page"] - boundary["physical_start_page"] + 1
        for row in validated_rows
        for boundary in row.boundaries
    )
    transform_denominators = Counter(
        row.page_scale_denominator for row in strong_rows
    )
    validated_transform_denominators = Counter(
        row.page_scale_denominator for row in validated_rows
    )
    by_year: dict[str, dict] = {}
    for year in sorted({row.document_id.split("/")[1] for row in rows}):
        subset = [row for row in rows if row.document_id.split("/")[1] == year]
        located = sum(bool(row.toc_pages) for row in subset)
        year_strong = sum(
            row.confidence == "strong_textual_toc" for row in subset
        )
        year_validated = sum(row.validation_pass for row in subset)
        by_year[year] = {
            "documents": len(subset),
            "toc_located": located,
            "toc_located_pct": round(100 * located / max(1, len(subset)), 1),
            "strong_boundaries": year_strong,
            "strong_boundaries_pct": round(
                100 * year_strong / max(1, len(subset)), 1
            ),
            "validation_pass": year_validated,
            "validation_pass_pct_of_strong": round(
                100 * year_validated / max(1, year_strong), 1
            ),
        }
    page_counts = [row.physical_pages for row in rows if row.physical_pages]
    return {
        "method_version": "document-boundary-audit.v2",
        "documents": len(rows),
        "physical_pages": sum(page_counts),
        "page_count_median": statistics.median(page_counts) if page_counts else None,
        "page_count_p90": percentile(page_counts, 0.90),
        "with_textual_toc": sum(bool(row.toc_pages) for row in rows),
        "with_3plus_toc_entries": sum(row.toc_entries >= 3 for row in rows),
        "strong_boundary_ready": strong,
        "strong_boundary_ready_pct": round(100 * strong / max(1, len(rows)), 1),
        "boundary_ready_including_partial": ready,
        "boundary_ready_including_partial_pct": round(
            100 * ready / max(1, len(rows)), 1
        ),
        "materialized_documents": len(materialized),
        "materialized_boundaries": materialized_boundaries,
        "verified_boundary_starts": verified_boundary_starts,
        "verified_boundary_start_pct": round(
            100 * verified_boundary_starts / max(1, materialized_boundaries), 1
        ),
        "strong_documents_validation_pass": validated_strong,
        "strong_documents_validation_pass_pct": round(
            100 * validated_strong / max(1, strong), 1
        ),
        "validated_boundaries": validated_boundaries,
        "validated_tagged_pages": validated_tagged_pages,
        "strong_transform_denominators": {
            str(key): value
            for key, value in sorted(transform_denominators.items())
        },
        "validated_transform_denominators": {
            str(key): value
            for key, value in sorted(validated_transform_denominators.items())
        },
        "confidence": dict(sorted(confidence.items())),
        "by_document_type": by_type,
        "by_year": by_year,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--ticker")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.disable(logging.CRITICAL)
    warnings.filterwarnings("ignore")
    pdfs = sorted(PDF_ROOT.glob("*/*/*.pdf"))
    if args.ticker:
        pdfs = [path for path in pdfs if path.parts[-3] == args.ticker.upper()]
    if args.limit:
        pdfs = pdfs[: args.limit]

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        rows = list(executor.map(audit_document, pdfs))

    report = summarize(rows)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    (OUTPUT_ROOT / "document_audit.jsonl").write_text(
        "".join(json.dumps(asdict(row), ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    (OUTPUT_ROOT / "section_boundaries.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "document_id": row.document_id,
                    "pdf_path": row.pdf_path,
                    "document_type": row.document_type,
                    "document_confidence": row.confidence,
                    "document_validation_pass": row.validation_pass,
                    "page_scale_denominator": row.page_scale_denominator,
                    "inferred_page_offset": row.inferred_offset,
                    **boundary,
                },
                ensure_ascii=False,
            )
            + "\n"
            for row in rows
            for boundary in row.boundaries
        ),
        encoding="utf-8",
    )
    validated_boundary_rows = [
        {
            "document_id": row.document_id,
            "pdf_path": row.pdf_path,
            "document_type": row.document_type,
            "page_scale_denominator": row.page_scale_denominator,
            "inferred_page_offset": row.inferred_offset,
            **boundary,
        }
        for row in rows
        if row.validation_pass
        for boundary in row.boundaries
    ]
    (OUTPUT_ROOT / "validated_section_boundaries.jsonl").write_text(
        "".join(
            json.dumps(boundary, ensure_ascii=False) + "\n"
            for boundary in validated_boundary_rows
        ),
        encoding="utf-8",
    )
    (OUTPUT_ROOT / "page_section_tags.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "document_id": boundary["document_id"],
                    "physical_page": physical_page,
                    "section_titles": boundary["section_titles"],
                    "section_start_page": boundary["physical_start_page"],
                    "section_end_page": boundary["physical_end_page"],
                    "source": "table_of_contents",
                    "confidence": "validated_deterministic",
                },
                ensure_ascii=False,
            )
            + "\n"
            for boundary in validated_boundary_rows
            for physical_page in range(
                boundary["physical_start_page"],
                boundary["physical_end_page"] + 1,
            )
        ),
        encoding="utf-8",
    )
    (OUTPUT_ROOT / "audit_report.json").write_text(
        json.dumps(report, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
