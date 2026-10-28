"""Pipeline constants and paths."""

from pathlib import Path

# Project root (psxgpt-pipeline repo root)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Key directories
ARTIFACTS = PROJECT_ROOT / "artifacts"
MARKDOWN_ROOT = PROJECT_ROOT / "markdown_pages"

ALLOWLISTS = ARTIFACTS / "allowlists"
