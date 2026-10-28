from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "pipeline" / "03_authoritative_parse.py"
SPEC = importlib.util.spec_from_file_location("authoritative_parse", MODULE_PATH)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def record() -> dict:
    return {
        "identity": "abc123",
        "source_id": "pk_sbp",
        "country": "PK",
        "publisher": "State Bank of Pakistan",
        "collection_id": "core_reports",
        "title": "Governor Annual Report 2025",
        "canonical_url": "https://sbp.org.pk/report.pdf",
        "current": {
            "sha256": "f" * 64,
            "local_path": (
                "authoritative_documents/pk/pk_sbp/core_reports/2025/"
                "governor-annual-report-2025--ffffffffffff.pdf"
            ),
        },
    }


def test_document_relative_dir_preserves_source_collection_and_year() -> None:
    assert module.document_relative_dir(record()).as_posix() == (
        "pk/pk_sbp/core_reports/2025/"
        "governor-annual-report-2025--ffffffffffff--abc123"
    )


def test_asset_pages_require_matching_content_hash() -> None:
    assets = {
        "documents": {
            "abc123": {
                "content_sha256": "f" * 64,
                "pages": [{"page": 1, "source_url": "https://source/page_001.pdf"}],
            }
        }
    }
    assert module.asset_pages(assets, record())[1]["source_url"].endswith("page_001.pdf")
    changed = record()
    changed["current"]["sha256"] = "e" * 64
    assert module.asset_pages(assets, changed) == {}


def test_strip_frontmatter_returns_only_page_text() -> None:
    assert module.strip_frontmatter("---\ntitle: \"x\"\n---\n\nRevenue grew.\n") == "Revenue grew.\n"
