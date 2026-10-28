#!/usr/bin/env python3
"""Deterministic page-topic classifier.

This produces a `page_topic` value that is deliberately kept separate from
`section_tags`. The statement tags in `section_tags` answer "does this page hold
rows step 08 must extract?" and are precision-tuned for that. They are relied on
by extraction and QC, so they are not extended here.

`page_topic` answers a different question: "what would a researcher be looking
for when this page is the right answer?" It must cover every page, including the
narrative and navigation pages that `section_tags` correctly leaves empty.

No model is involved. Every assignment carries the rule that produced it so a
disputed tag can be traced back to the line of text that caused it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# --- vocabulary -------------------------------------------------------------

# Navigation and front/back matter. A page in this group is almost never the
# right answer to an analyst question, and this group is what currently wins
# BM25 for structural phrases like "financial statements".
NON_CONTENT = {
    "front_matter",
    "non_english",
    "shareholder_admin",
    "governance",
    "narrative_review",
    "sustainability",
}

# Note and disclosure topics. This is the layer `section_tags` does not have:
# it labels ~60% of an annual report that is currently a single undifferentiated
# `statement_note` or nothing at all.
NOTE_TOPICS = {
    "accounting_policy",
    "debt_and_borrowings",
    "financial_risk",
    "capital_management",
    "segment_information",
    "related_party",
    "employee_benefits",
    "taxation",
    "ppe_and_assets",
    "investments",
    "contingencies_commitments",
    "revenue_and_costs",
    "dividends_and_eps",
    "receivables_payables",
}

# Primary statements. Reused verbatim from `section_tags` where that field
# already has a confident answer, rather than re-deriving them badly.
STATEMENTS = {
    "profit_loss",
    "balance_sheet",
    "cash_flow",
    "changes_in_equity",
    "comprehensive_income",
}

ALL_TOPICS = NON_CONTENT | NOTE_TOPICS | STATEMENTS | {"unclassified"}


@dataclass
class Classification:
    topic: str
    confidence: float
    rule: str
    secondary: tuple[str, ...] = ()


# --- structural detection ---------------------------------------------------

HEADING_RE = re.compile(r"^\s{0,3}#{1,3}\s+(.+?)\s*$", re.MULTILINE)
# Filings frequently emphasise a statement title in bold instead of using a
# markdown heading. Treating a bold-only line as a heading recovers pages like
# "**CONDENSED INTERIM STATEMENT OF CHANGES IN EQUITY**", which carry no `#`.
BOLD_HEADING_RE = re.compile(r"^\s{0,3}\*\*(.{4,120}?)\*\*\s*$", re.MULTILINE)
# Urdu sections of annual reports. These carry no English keywords and usually
# no search_text, so they are unreachable by an English phrase rule.
ARABIC_SCRIPT_RE = re.compile(r"[؀-ۿݐ-ݿ]")
# A contents row: a markdown table cell followed by a bare page number.
TOC_ROW_RE = re.compile(r"^\s*\|[^|]{3,90}\|\s*\d{1,4}\s*\|\s*$", re.MULTILINE)
# The other common contents form: dotted leaders ending in a page number.
DOTTED_LEADER_RE = re.compile(r"\.{4,}\s*\d{1,4}\s*$", re.MULTILINE)


def _headings(text: str) -> list[str]:
    found = HEADING_RE.findall(text) + BOLD_HEADING_RE.findall(text)
    return [h.strip().strip("*_ ").lower() for h in found]


def _clean(text: str) -> str:
    """Lowercased text with markdown emphasis removed, for phrase matching."""
    return re.sub(r"[*_`]", "", text).lower()


_BOUNDARY_CACHE: dict[str, re.Pattern[str]] = {}


def _contains_phrase(haystack: str, phrase: str) -> bool:
    """Word-boundary phrase match.

    Plain substring matching silently misfires on short words: "vision" matches
    inside "provision", which tagged UBL's off-balance-sheet provisions page as
    a mission-statement page. Anchoring both ends removes that whole class of
    error.
    """
    pattern = _BOUNDARY_CACHE.get(phrase)
    if pattern is None:
        # Trailing-truncated stems such as "contingenc" or "related part" are
        # intentional prefixes, so only the leading edge is anchored for those.
        tail = r"\b" if phrase[-1].isalnum() and not phrase.endswith(("part", "contingenc", "lease liabilit")) else ""
        pattern = re.compile(rf"\b{re.escape(phrase)}{tail}")
        _BOUNDARY_CACHE[phrase] = pattern
    return bool(pattern.search(haystack))


# Ordered most specific first. A page matching an earlier entry does not fall
# through, because several of these phrases also appear inside a contents table.
HEADING_RULES: list[tuple[str, str, tuple[str, ...]]] = [
    ("front_matter", "heading:contents", ("contents", "table of contents", "index")),
    (
        "shareholder_admin",
        "heading:meeting",
        (
            "notice of annual general meeting",
            "notice of extraordinary general meeting",
            "ballot paper",
            "form of proxy",
            "proxy form",
            "pattern of shareholding",
            "categories of shareholders",
        ),
    ),
    (
        "governance",
        "heading:governance",
        (
            "independent auditor",
            "auditor's report",
            "auditors' report",
            "report of the audit committee",
            "statement of compliance",
            "code of corporate governance",
            "board of directors",
            "board committees",
            "directors' report",
            "director's report",
            "report of the directors",
            "internal control",
            "company information",
            "corporate information",
        ),
    ),
    (
        "sustainability",
        "heading:sustainability",
        (
            "sustainability",
            "corporate social responsibility",
            "environmental, social",
            "esg",
            "shariah board",
            "shariah advisor",
        ),
    ),
    (
        "narrative_review",
        "heading:narrative",
        (
            "chairman's review",
            "chairman review",
            "ceo's message",
            "ceo message",
            "president's message",
            "business review",
            "management discussion",
            "growth at a glance",
            "vision",
            "mission",
            "our journey",
            "milestones",
        ),
    ),
]


# --- topical detection ------------------------------------------------------

# Weighted phrase sets. A phrase in a heading counts far more than one in the
# body, because note pages share a single generic H1 and state their real
# subject in a numbered subheading.
TOPIC_PHRASES: dict[str, tuple[str, ...]] = {
    "related_party": (
        "related part",
        "key management personnel",
        "associated undertaking",
    ),
    "segment_information": ("segment information", "segment report", "operating segment"),
    "employee_benefits": (
        "defined benefit",
        "defined contribution",
        "gratuity",
        "pension fund",
        "actuarial",
        "employee benefit",
        "provident fund",
    ),
    "taxation": ("deferred tax", "current tax", "taxation", "income tax", "tax expense"),
    "debt_and_borrowings": (
        "term finance certificate",
        "long term financing",
        "long-term financing",
        "short term borrowing",
        "short-term borrowing",
        "lease liabilit",
        "sukuk",
        "subordinated debt",
        "borrowings from",
        "running finance",
    ),
    "contingencies_commitments": (
        "contingenc",
        "commitments",
        "letters of credit",
        "letter of credit",
        "bank guarantee",
        "legal claim",
    ),
    "financial_risk": (
        "credit risk",
        "market risk",
        "liquidity risk",
        "interest rate risk",
        "currency risk",
        "foreign exchange risk",
        "sensitivity analysis",
        "risk management framework",
        "maturity analysis",
    ),
    "capital_management": (
        "capital adequacy",
        "tier 1 capital",
        "tier i capital",
        "capital management",
        "leverage ratio",
        "risk weighted asset",
    ),
    "ppe_and_assets": (
        "property, plant",
        "property plant",
        "operating fixed asset",
        "intangible asset",
        "right of use",
        "right-of-use",
        "capital work in progress",
        "depreciation",
    ),
    "investments": (
        "investment in subsidiar",
        "investment in associate",
        "investments in shares",
        "mutual fund",
        "available for sale",
        "held to maturity",
        "investment properties",
    ),
    "receivables_payables": (
        "trade debts",
        "trade receivable",
        "trade payable",
        "trade and other payable",
        "other receivable",
        "circular debt",
    ),
    "accounting_policy": (
        "basis of preparation",
        "material accounting policy",
        "significant accounting policies",
        "accounting policies",
        "new standards",
        "standards, interpretations",
        "statement of compliance with",
    ),
    "dividends_and_eps": (
        "earnings per share",
        "dividend per share",
        "cash dividend",
        "bonus shares",
        "final dividend",
        "interim dividend",
    ),
    "revenue_and_costs": (
        "revenue from contracts",
        "cost of sales",
        "gross profit",
        "administrative expense",
        "distribution cost",
        "other income",
        "net sales",
    ),
}


def _score_topics(
    clean_text: str,
    headings: list[str],
    summary: str = "",
) -> list[tuple[str, float]]:
    """Scores topics over headings, the indexed summary, and the body.

    `search_text` is weighted heavily because it is already a topical
    distillation of the page. A segment table rendered as a bare markdown grid
    has no usable heading and no distinctive body prose, but its summary reads
    "fertilizer polymer terminal power mining segment profit" — which is the
    clearest topical signal on the page.
    """
    heading_blob = " ".join(headings)
    scored: list[tuple[str, float]] = []
    for topic, phrases in TOPIC_PHRASES.items():
        score = 0.0
        for phrase in phrases:
            if phrase in heading_blob:
                score += 3.0
            if summary and phrase in summary:
                score += 2.0
            hits = clean_text.count(phrase)
            if hits:
                # Diminishing returns: ten mentions is not ten times the signal.
                score += min(hits, 4) * 0.5
        if score > 0:
            scored.append((topic, score))
    scored.sort(key=lambda item: (-item[1], item[0]))
    return scored


def classify(
    text: str,
    page_number: int,
    existing_section_tags: list[dict] | None = None,
    search_text: str = "",
) -> Classification:
    """Assign one primary topic to a page."""
    raw = text or ""
    clean = _clean(raw)
    summary = _clean(search_text or "")
    headings = _headings(raw)
    body_len = len(raw.strip())

    # 1. A confident statement tag already answers this. Trust it rather than
    #    re-deriving a worse version from keywords.
    for tag in existing_section_tags or []:
        name = str(tag.get("tag", "")).lower()
        if name in STATEMENTS and float(tag.get("confidence") or 0) >= 0.9:
            return Classification(name, float(tag["confidence"]), "section_tags:statement")

    # 2. Contents pages, by structure rather than by wording. This is the class
    #    that currently wins BM25 for structural queries, and it is the most
    #    reliably detectable: a page that is mostly "label -> page number" rows.
    toc_rows = len(TOC_ROW_RE.findall(raw)) + len(DOTTED_LEADER_RE.findall(raw))
    total_lines = max(1, raw.count("\n"))
    if toc_rows >= 6 and toc_rows / total_lines > 0.25:
        return Classification("front_matter", 0.95, f"structure:toc_rows={toc_rows}")

    # 3. Urdu-language sections. These are a real class in Pakistani annual
    #    reports, they carry no English phrases and usually no search_text, and
    #    without their own label they sit in "unclassified" looking like a
    #    tagger failure rather than a language boundary.
    if body_len > 200:
        script_hits = len(ARABIC_SCRIPT_RE.findall(raw))
        if script_hits / body_len > 0.15:
            return Classification("non_english", 0.9, f"script:arabic={script_hits}")

    # 4. Headings, most specific first.
    for topic, rule, phrases in HEADING_RULES:
        for phrase in phrases:
            if any(phrase in heading for heading in headings):
                return Classification(topic, 0.9, f"{rule}:{phrase}")

    # 5. A near-empty page is front matter (dividers, blank backs, image plates).
    if body_len < 400:
        return Classification("front_matter", 0.6, f"structure:short={body_len}")

    # 6. Topical note classification.
    scored = _score_topics(clean, headings, summary)
    if scored:
        top, top_score = scored[0]
        runner_up = scored[1][1] if len(scored) > 1 else 0.0
        # Confidence reflects both absolute evidence and separation from the
        # next candidate, since note pages genuinely do mix subjects.
        margin = (top_score - runner_up) / top_score if top_score else 0.0
        confidence = max(0.35, min(0.9, 0.4 + 0.3 * margin + min(top_score, 6) * 0.05))
        if top_score >= 2.0:
            secondary = tuple(name for name, value in scored[1:4] if value >= top_score * 0.5)
            return Classification(top, round(confidence, 2), "phrases", secondary)

    # 7. A statement tag below the confidence bar is still better than nothing.
    for tag in existing_section_tags or []:
        name = str(tag.get("tag", "")).lower()
        if name in STATEMENTS:
            return Classification(name, 0.5, "section_tags:statement_lowconf")
        if name == "statement_note":
            return Classification("unclassified", 0.3, "section_tags:statement_note")

    return Classification("unclassified", 0.0, "none")


def is_non_content(topic: str) -> bool:
    return topic in NON_CONTENT
