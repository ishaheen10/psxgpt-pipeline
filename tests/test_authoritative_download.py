from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "pipeline" / "01_authoritative_download.py"
SPEC = importlib.util.spec_from_file_location("authoritative_download", MODULE_PATH)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def source_config() -> dict:
    return {
        "id": "pk_example",
        "country": "PK",
        "publisher": "Example Authority",
        "authority_type": "regulator",
        "official_domains": ["example.gov.pk"],
    }


def test_extract_candidates_applies_domain_url_and_title_boundaries() -> None:
    html = """
    <a href="/reports/annual-2025.pdf"><strong>Annual Report 2025</strong></a>
    <a href="/reports/random.pdf">Tender Form</a>
    <a href="https://mirror.invalid/annual-2024.pdf">Annual Report 2024</a>
    <a href="/reports/annual-2025.pdf">duplicate</a>
    """
    collection = {
        "id": "annual_reports",
        "landing_urls": ["https://example.gov.pk/reports"],
        "document_url_patterns": [r"(?i)\.pdf(?:$|[?#])"],
        "document_exclude_url_patterns": [r"(?i)/reports/excluded-"],
        "link_text_patterns": [r"(?i)annual report"],
    }
    rows = module.extract_candidates(
        html,
        landing_url="https://example.gov.pk/reports",
        source=source_config(),
        collection=collection,
    )
    assert len(rows) == 1
    assert rows[0].url == "https://example.gov.pk/reports/annual-2025.pdf"
    assert rows[0].title == "Annual Report 2025"


def test_canonical_url_sorts_query_and_removes_fragment() -> None:
    assert (
        module.canonical_url("HTTPS://EXAMPLE.GOV.PK/download?b=2&a=1#page=4")
        == "https://example.gov.pk/download?a=1&b=2"
    )
    assert (
        module.canonical_url(
            "https://example.gov.pk/document/report/?wpdmdl=12&refresh=volatile"
        )
        == "https://example.gov.pk/document/report/?wpdmdl=12"
    )


def test_pdf_validation_requires_header_or_content_type() -> None:
    assert module.is_pdf(b"%PDF-1.7\n", "application/octet-stream")
    assert module.is_pdf(b"proxy bytes", "application/pdf; charset=binary")
    assert not module.is_pdf(b"<html>blocked</html>", "text/html")


def test_publication_year_handles_underscores() -> None:
    candidate = module.Candidate(
        source_id="pk_example",
        country="PK",
        publisher="Example",
        authority_type="regulator",
        collection_id="annual_reports",
        landing_url="https://example.gov.pk/reports",
        url="https://example.gov.pk/annual_report_2020.pdf",
        title="annual_report_2020.pdf",
        verify_tls=True,
    )
    assert module.publication_year(candidate) == 2020
    fiscal = module.Candidate(
        source_id="pk_example",
        country="PK",
        publisher="Example",
        authority_type="regulator",
        collection_id="annual_reports",
        landing_url="https://example.gov.pk/reports",
        url="https://example.gov.pk/annual_report_15_16.pdf",
        title="annual_report_15_16.pdf",
        verify_tls=True,
    )
    assert module.publication_year(fiscal) == 2015


def test_generic_link_text_uses_descriptive_filename() -> None:
    assert (
        module.document_title(
            "Download",
            "https://example.gov.pk/reports/Annual-Report-2025.pdf",
        )
        == "Annual-Report-2025.pdf"
    )
    assert (
        module.document_title(
            "Download PDF",
            "https://example.gov.pk/reports/Annual-Report-2026.pdf",
        )
        == "Annual-Report-2026.pdf"
    )


def test_follow_urls_are_official_deduplicated_and_pattern_bounded() -> None:
    html = """
    <a href="/survey_2026.html">Economic Survey 2025-26</a>
    <a href="/survey_2026.html">duplicate</a>
    <a href="/other.html">Other</a>
    <a href="https://mirror.invalid/survey_2025.html">mirror</a>
    """
    source = source_config()
    collection = {
        "follow_url_patterns": [r"(?i)survey_[0-9]+\.html"],
    }
    assert module.extract_follow_urls(
        html,
        landing_url="https://example.gov.pk/",
        source=source,
        collection=collection,
    ) == ["https://example.gov.pk/survey_2026.html"]


def test_registry_loads_and_has_unique_source_collection_pairs() -> None:
    payload = module.load_config(ROOT / "config" / "authoritative_sources.json")
    pairs = [
        (source["id"], collection["id"])
        for source in payload["sources"]
        for collection in source["collections"]
    ]
    assert pairs
    assert len(pairs) == len(set(pairs))


def test_local_record_error_rejects_missing_file() -> None:
    record = {"current": {"local_path": "missing-test-file.pdf", "bytes": 12}}
    with patch.object(module, "PROJECT_ROOT", Path("/tmp/authoritative-test-root")):
        assert "missing" in module.local_record_error(record)
