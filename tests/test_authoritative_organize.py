from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "pipeline" / "01_authoritative_organize.py"
SPEC = importlib.util.spec_from_file_location("authoritative_organize", MODULE_PATH)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def test_display_name_removes_pdf_suffix_and_filename_underscores() -> None:
    assert module.display_name("Annual_report_2020.pdf") == "Annual report 2020"
    assert module.display_name("HS Code/ PCT Code") == "HS Code - PCT Code"
