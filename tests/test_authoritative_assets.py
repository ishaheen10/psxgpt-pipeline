from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "pipeline" / "02_authoritative_assets.py"
SPEC = importlib.util.spec_from_file_location("authoritative_assets", MODULE_PATH)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def record() -> dict:
    return {
        "identity": "abc123",
        "source_id": "pk_nepra",
        "country": "PK",
        "publisher": "NEPRA",
        "collection_id": "annual_reports",
        "title": "Annual Report 2025",
        "canonical_url": "https://nepra.org.pk/report.pdf",
        "current": {
            "sha256": "f" * 64,
            "bytes": 10,
            "local_path": (
                "authoritative_documents/pk/pk_nepra/annual_reports/2025/"
                "annual-report-2025--ffffffffffff.pdf"
            ),
        },
    }


def test_document_relative_dir_is_stable_and_namespaced() -> None:
    assert module.document_relative_dir(record()).as_posix() == (
        "pk/pk_nepra/annual_reports/2025/"
        "annual-report-2025--ffffffffffff--abc123"
    )


def test_page_urls_only_appear_for_uploaded_keys() -> None:
    relative = Path("pk/pk_nepra/annual_reports/2025/report")
    pdf_key = (
        "AUTHORITATIVE_PAGES/pk/pk_nepra/annual_reports/2025/report/page_001.pdf"
    )
    rows = module.page_metadata(relative, 1, {pdf_key})
    assert rows[0]["source_url"] == f"https://source.psxgpt.com/{pdf_key}"
    assert rows[0]["thumbnail_url"] is None


def test_selection_respects_country_source_and_limit() -> None:
    first = record()
    second = {**record(), "identity": "def456", "source_id": "pk_psx"}
    manifest = {"documents": {"abc123": first, "def456": second}}
    rows = module.selected_documents(manifest, {"PK"}, {"pk_psx"}, set(), 1)
    assert [row["identity"] for row in rows] == ["def456"]
