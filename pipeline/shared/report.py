"""The uniform step contract: every step writes a report and a decision.

A step ends with:
    rep = {... metrics ...}
    write_report("02_parse", rep)
    emit_decision("02_parse", summary, cost_usd, actions)

`14_triage` and the mobile status read artifacts/reports/*.json to stack every
step's "here's what happened, here's the cost, approve?" card.
"""
from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path

from .constants import ARTIFACTS

REPORTS_DIR = ARTIFACTS / "reports"
ISSUES_DIR = ARTIFACTS / "issues"
LINEAGE_DIR = ARTIFACTS / "lineage"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Action:
    label: str                 # what the user taps: "Run tag", "Find missing PDFs"
    command: str               # the scoped command it runs
    cost_usd: float = 0.0
    auto_ok: bool = False      # may auto-run under the cost cap


@dataclass
class Decision:
    step: str
    summary: str
    cost_usd: float = 0.0
    actions: list[Action] = field(default_factory=list)
    generated_at: str = ""


def write_report(step: str, data: dict) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    data = {**data, "step": step, "generated_at": utc_now()}
    path = REPORTS_DIR / f"{step}.json"
    path.write_text(json.dumps(data, indent=2))
    return path


def load_report(step: str) -> dict | None:
    p = REPORTS_DIR / f"{step}.json"
    return json.loads(p.read_text()) if p.exists() else None


def emit_decision(step: str, summary: str, cost_usd: float = 0.0,
                  actions: list[Action] | None = None) -> Decision:
    d = Decision(step=step, summary=summary, cost_usd=round(cost_usd, 2),
                 actions=actions or [], generated_at=utc_now())
    (REPORTS_DIR / f"{step}.decision.json").write_text(
        json.dumps({**asdict(d)}, indent=2))
    print("\n" + "─" * 60)
    print(d.summary)
    if d.cost_usd:
        print(f"Estimated cost: ${d.cost_usd}")
    for a in d.actions:
        tag = " [auto]" if a.auto_ok else ""
        c = f" (${a.cost_usd})" if a.cost_usd else ""
        print(f"  → {a.label}{c}{tag}:  {a.command}")
    print("─" * 60)
    return d


def stable_id(prefix: str, payload: dict) -> str:
    core = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return f"{prefix}-{hashlib.sha1(core.encode()).hexdigest()[:16]}"


def write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")
    return path


def write_stage_issues(step: str, issues: list[dict], scope_tickers: set[str] | None = None) -> Path:
    """Refresh stage-specific and global issue ledgers for one existing pipeline step."""
    now = utc_now()
    normalized = []
    for issue in issues:
        row = {**issue, "stage": step, "generated_at": now}
        row.setdefault("severity", "warning")
        row.setdefault("origin_stage", step)
        row.setdefault("issue_id", stable_id(step, {
            k: row.get(k)
            for k in (
                "stage", "ticker", "filing", "statement_type", "section", "period_end",
                "period_duration", "source_file", "canonical_name", "rule", "got", "expected",
            )
        }))
        normalized.append(row)

    stage_path = ISSUES_DIR / f"{step}.jsonl"
    stage_rows: list[dict] = []
    if scope_tickers is not None and stage_path.exists():
        for line in stage_path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(row.get("ticker") or "").upper() not in scope_tickers:
                stage_rows.append(row)
    stage_rows += normalized
    write_jsonl(stage_path, stage_rows)

    global_path = ISSUES_DIR / "issues.jsonl"
    retained: list[dict] = []
    if global_path.exists():
        for line in global_path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("stage") != step:
                retained.append(row)
            elif scope_tickers is not None and str(row.get("ticker") or "").upper() not in scope_tickers:
                retained.append(row)
    write_jsonl(global_path, retained + stage_rows if scope_tickers is None else retained + normalized)
    return stage_path


def write_stage_lineage(step: str, rows: list[dict], scope_tickers: set[str] | None = None) -> Path:
    now = utc_now()
    normalized = []
    for row in rows:
        out = {**row, "stage": step, "generated_at": now}
        out.setdefault("lineage_id", stable_id(step, out))
        normalized.append(out)
    path = LINEAGE_DIR / f"{step}.jsonl"
    if scope_tickers is None or not path.exists():
        return write_jsonl(path, normalized)
    retained: list[dict] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(row.get("ticker") or "").upper() not in scope_tickers:
            retained.append(row)
    return write_jsonl(path, retained + normalized)


def llm_synthesis(report: dict, instruction: str) -> str | None:
    """Optional: turn a report into a judgment summary via DeepSeek. Used only where a
    count+price line isn't enough (e.g. download coverage). Returns None on any failure."""
    import os
    try:
        from dotenv import load_dotenv
        load_dotenv()
        from openai import OpenAI
        key = os.getenv("DEEPSEEK_API_KEY")
        if not key:
            return None
        client = OpenAI(api_key=key, base_url=os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com"))
        r = client.chat.completions.create(
            model=os.getenv("DEEPSEEK_REVIEW_MODEL", "deepseek-v4-pro"),
            messages=[{"role": "system", "content": "You summarize a pipeline-stage report for an operator. Be concise: 2-4 lines, then a clear recommendation."},
                      {"role": "user", "content": f"{instruction}\n\nReport:\n{json.dumps(report, indent=2)}"}],
            temperature=0.2)
        return r.choices[0].message.content
    except Exception:
        return None
