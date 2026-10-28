"""Assemble LLM prompts from decomposed context packs.

Each stage's prompt is split by CONCERN into context/<pack>/:
  - fact.md         domain knowledge the model must KNOW (mappings, definitions)
  - instruction.md  the procedure / rules (HOW to extract)
  - schema.md       the output contract (table format)

Course convention: assemble in order instruction -> schema -> fact.
Placeholders in the packs are filled by the caller via `repl`.
"""
from pathlib import Path

CONTEXT_DIR = Path(__file__).resolve().parent.parent.parent / "context"
DEFAULT_ORDER = ["rules.md", "format.md", "fields.md"]
STATEMENT_ORDER = ["format.md", "rules.md"]


def read_prompt_pack(pack: str, repl: dict | None = None, order: list[str] | None = None) -> str | None:
    base = CONTEXT_DIR / pack
    if not base.exists():
        return None  # caller falls back to inline prompt
    parts = []
    for fname in (order or DEFAULT_ORDER):
        f = base / fname
        if f.exists():
            parts.append(f.read_text())
    text = "\n\n".join(p.strip() for p in parts if p.strip())
    if repl:
        for k, v in repl.items():
            text = text.replace(k, v)
    return text


def fill_placeholders(text: str, repl: dict | None = None) -> str:
    if not repl:
        return text
    for k, v in repl.items():
        text = text.replace(k, v)
    return text


def read_statement_prompt_pack(pack: str, repl: dict | None = None) -> str | None:
    """Assemble statement extraction packs with canonical fields at the end.

    The statement packs keep filing metadata and stable domain facts in fact.md.
    A marker separates the prompt intro from long canonical-field guidance so the
    model sees task/schema/instructions first, with mappings as reference context.
    """
    base = CONTEXT_DIR / pack
    if not base.exists():
        return None

    fact_path = base / "fields.md"
    fact = fact_path.read_text() if fact_path.exists() else ""
    if "<!-- CANONICAL_SECTIONS -->" in fact:
        intro, canonical = fact.split("<!-- CANONICAL_SECTIONS -->", 1)
    else:
        intro, canonical = fact, ""

    parts = [intro.strip()]
    for fname in STATEMENT_ORDER:
        path = base / fname
        if path.exists():
            parts.append(path.read_text().strip())
    if canonical.strip():
        parts.append(canonical.strip())

    text = "\n\n".join(p for p in parts if p)
    return fill_placeholders(text, repl)
