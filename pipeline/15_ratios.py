#!/usr/bin/env python3
"""Step 15 — Rebuild ratios table on Cloudflare D1.

Drops and recreates the `ratios` table from the `financial_statements` table.
Computed ratios: ROE, ROA, net_margin, gross_margin, current_ratio,
debt_to_equity, asset_turnover.

Usage:
    python pipeline/15_ratios.py
    python pipeline/15_ratios.py --sql-only
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shared.constants import PROJECT_ROOT, ARTIFACTS  # noqa: E402
from shared.report import write_report, emit_decision, write_stage_issues, write_stage_lineage  # noqa: E402

SQL_DIR = ARTIFACTS / "15_ratios"
D1_DB = "psx"
USER_VISIBLE_RATIO_DIAGNOSTICS = {"unit_or_scale_suspect"}


def d1_query(sql: str) -> list[dict]:
    result = subprocess.run(
        ["npx", "wrangler", "d1", "execute", D1_DB, "--remote", "--json", "--command", sql],
        cwd=PROJECT_ROOT, text=True, capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-2000:] if result.stderr else "wrangler d1 query failed")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Could not parse wrangler JSON output: {result.stdout[-1000:]}") from exc
    if not payload:
        return []
    return payload[0].get("results") or []

def build_sql() -> str:
    # CTEs repeated per-statement because D1's SQLite rejects compound SELECTs
    # with more than a few UNION ALL terms in a single INSERT.
    # Each INSERT is a self-contained statement with its own WITH clause.

    def _flow_raw(durations: str) -> str:
        return f"""\
  SELECT
    fs.ticker, fs.period_end, fs.period_duration, fs.section, fs.canonical_name, fs.value, fs.method,
    CASE
      WHEN fs.method LIKE 'LTM_9M%' THEN 4
      WHEN fs.method LIKE 'LTM_6M%' THEN 3
      WHEN fs.method LIKE 'LTM_3M%' THEN 2
      ELSE 1
    END AS method_rank
  FROM financial_statements fs
  LEFT JOIN ratio_blocked_statement_groups blocked
    ON blocked.ticker = fs.ticker
   AND blocked.period_end = fs.period_end
   AND blocked.period_duration = fs.period_duration
   AND blocked.section = fs.section
   AND blocked.statement_type = fs.statement_type
   AND blocked.canonical_name = fs.canonical_name
  WHERE fs.section IN ('consolidated','unconsolidated')
    AND fs.statement_type = 'profit_loss'
    AND fs.period_duration IN ({durations})
    AND fs.value IS NOT NULL
    AND COALESCE(fs.qc_flag, '') = ''
    AND blocked.ticker IS NULL"""

    def _flow(durations: str) -> str:
        raw = _flow_raw(durations)
        return f"""\
  SELECT ticker, period_end, period_duration, section, canonical_name, value
  FROM (
    SELECT
      ticker, period_end, period_duration, section, canonical_name, value,
      ROW_NUMBER() OVER (
        PARTITION BY ticker, period_end, period_duration, section, canonical_name
        ORDER BY method_rank DESC
      ) AS rn
    FROM ({raw})
  )
  WHERE rn = 1"""

    FLOW = _flow("'12M','LTM'")
    FLOW_ALL = _flow("'3M','6M','9M','12M','LTM'")
    FLOW_RAW_ALL = _flow_raw("'3M','6M','9M','12M','LTM'")
    BS = """\
  SELECT ticker, period_end, period_duration, section, canonical_name, value
  FROM (
    SELECT
      fs.ticker, fs.period_end, fs.period_duration, fs.section, fs.canonical_name, fs.value,
      ROW_NUMBER() OVER (
        PARTITION BY fs.ticker, fs.period_end, fs.section, fs.canonical_name
        ORDER BY fs.id DESC
      ) AS rn
    FROM financial_statements fs
    LEFT JOIN ratio_blocked_statement_groups blocked
      ON blocked.ticker = fs.ticker
     AND blocked.period_end = fs.period_end
     AND blocked.period_duration = fs.period_duration
     AND blocked.section = fs.section
     AND blocked.statement_type = fs.statement_type
     AND blocked.canonical_name = fs.canonical_name
    WHERE fs.section IN ('consolidated','unconsolidated')
      AND fs.statement_type = 'balance_sheet'
      AND fs.period_duration = 'PIT'
      AND fs.value IS NOT NULL
      AND COALESCE(fs.qc_flag, '') = ''
      AND blocked.ticker IS NULL
  )
  WHERE rn = 1"""

    return f"""
DROP TABLE IF EXISTS ratios;

CREATE TABLE ratios (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker TEXT NOT NULL,
  period_end TEXT NOT NULL,
  period_duration TEXT NOT NULL,
  section TEXT NOT NULL,
  ratio_name TEXT NOT NULL,
  value REAL,
  basis TEXT,
  computed_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ratios_lookup
  ON ratios(ticker, ratio_name, period_end);

DROP TABLE IF EXISTS ratio_blocked_statement_groups;

CREATE TABLE ratio_blocked_statement_groups AS
SELECT DISTINCT ticker, period_end, period_duration, section, statement_type, canonical_name
FROM financial_statements
WHERE value IS NOT NULL
  AND COALESCE(qc_flag, '') <> '';

CREATE INDEX IF NOT EXISTS idx_ratio_blocked_statement_groups
  ON ratio_blocked_statement_groups(ticker, period_end, period_duration, section, statement_type, canonical_name);

-- ROE = net_profit / total_equity
WITH best_flow AS ({FLOW}),
best_bs AS ({BS})
INSERT INTO ratios (ticker, period_end, period_duration, section, ratio_name, value, basis)
SELECT p.ticker, p.period_end, p.period_duration, p.section,
  'roe', p.value * 1.0 / NULLIF(b.value, 0), 'total_equity'
FROM best_flow p
JOIN best_bs b
  ON b.ticker = p.ticker
 AND b.section = p.section
 AND b.canonical_name = 'total_equity'
 AND b.period_end = (
   SELECT MAX(bx.period_end)
   FROM best_bs bx
   WHERE bx.ticker = p.ticker
     AND bx.section = p.section
     AND bx.canonical_name = 'total_equity'
     AND bx.period_end <= p.period_end
 )
WHERE p.canonical_name = 'net_profit' AND b.canonical_name = 'total_equity';

-- ROA = net_profit / total_assets
WITH best_flow AS ({FLOW}),
best_bs AS ({BS})
INSERT INTO ratios (ticker, period_end, period_duration, section, ratio_name, value, basis)
SELECT p.ticker, p.period_end, p.period_duration, p.section,
  'roa', p.value * 1.0 / NULLIF(b.value, 0), 'total_assets'
FROM best_flow p
JOIN best_bs b
  ON b.ticker = p.ticker
 AND b.section = p.section
 AND b.canonical_name = 'total_assets'
 AND b.period_end = (
   SELECT MAX(bx.period_end)
   FROM best_bs bx
   WHERE bx.ticker = p.ticker
     AND bx.section = p.section
     AND bx.canonical_name = 'total_assets'
     AND bx.period_end <= p.period_end
 )
WHERE p.canonical_name = 'net_profit' AND b.canonical_name = 'total_assets';

-- Net margin: net_profit / revenue_net.
-- Multi-component flow ratios must use a complete same-method set. Otherwise
-- a flagged LTM component can make the ratio mix unrelated 9M and 3M builds.
WITH flow_components AS ({FLOW_RAW_ALL}),
candidates AS (
  SELECT ticker, period_end, period_duration, section, method, method_rank,
    MAX(CASE WHEN canonical_name = 'net_profit'  THEN value END) AS numerator,
    MAX(CASE WHEN canonical_name = 'revenue_net' THEN value END) AS denominator
  FROM flow_components
  WHERE canonical_name IN ('net_profit', 'revenue_net')
  GROUP BY ticker, period_end, period_duration, section, method, method_rank
  HAVING numerator IS NOT NULL
     AND denominator IS NOT NULL
     AND NULLIF(denominator, 0) IS NOT NULL
),
ranked AS (
  SELECT *, ROW_NUMBER() OVER (
    PARTITION BY ticker, period_end, period_duration, section
    ORDER BY method_rank DESC, method DESC
  ) AS rn
  FROM candidates
)
INSERT INTO ratios (ticker, period_end, period_duration, section, ratio_name, value, basis)
SELECT ticker, period_end, period_duration, section,
  'net_margin', numerator * 1.0 / denominator,
  'revenue_net'
FROM ranked
WHERE rn = 1;

-- Net margin fallback 2: net_profit / total_income (banks).
-- Use only when no valid net_profit/revenue_net same-method candidate exists.
WITH flow_components AS ({FLOW_RAW_ALL}),
primary_candidates AS (
  SELECT ticker, period_end, period_duration, section, method
  FROM flow_components
  WHERE canonical_name IN ('net_profit', 'revenue_net')
  GROUP BY ticker, period_end, period_duration, section, method
  HAVING MAX(CASE WHEN canonical_name = 'net_profit' THEN value END) IS NOT NULL
     AND MAX(CASE WHEN canonical_name = 'revenue_net' THEN value END) IS NOT NULL
     AND NULLIF(MAX(CASE WHEN canonical_name = 'revenue_net' THEN value END), 0) IS NOT NULL
),
candidates AS (
  SELECT ticker, period_end, period_duration, section, method, method_rank,
    MAX(CASE WHEN canonical_name = 'net_profit'   THEN value END) AS numerator,
    MAX(CASE WHEN canonical_name = 'total_income' THEN value END) AS denominator
  FROM flow_components
  WHERE canonical_name IN ('net_profit', 'total_income')
  GROUP BY ticker, period_end, period_duration, section, method, method_rank
  HAVING numerator IS NOT NULL
     AND denominator IS NOT NULL
     AND NULLIF(denominator, 0) IS NOT NULL
),
ranked AS (
  SELECT *, ROW_NUMBER() OVER (
    PARTITION BY ticker, period_end, period_duration, section
    ORDER BY method_rank DESC, method DESC
  ) AS rn
  FROM candidates c
  WHERE NOT EXISTS (
    SELECT 1 FROM primary_candidates p
    WHERE p.ticker=c.ticker AND p.period_end=c.period_end
      AND p.period_duration=c.period_duration AND p.section=c.section
  )
)
INSERT INTO ratios (ticker, period_end, period_duration, section, ratio_name, value, basis)
SELECT ticker, period_end, period_duration, section,
  'net_margin', numerator * 1.0 / denominator,
  'total_income'
FROM ranked
WHERE rn = 1;

-- Net margin fallback 3: net_profit / net_premium (insurance).
-- Use only when no revenue_net or total_income same-method candidate exists.
WITH flow_components AS ({FLOW_RAW_ALL}),
higher_candidates AS (
  SELECT ticker, period_end, period_duration, section, method
  FROM flow_components
  WHERE canonical_name IN ('net_profit', 'revenue_net', 'total_income')
  GROUP BY ticker, period_end, period_duration, section, method
  HAVING MAX(CASE WHEN canonical_name = 'net_profit' THEN value END) IS NOT NULL
     AND (
       (MAX(CASE WHEN canonical_name = 'revenue_net' THEN value END) IS NOT NULL
        AND NULLIF(MAX(CASE WHEN canonical_name = 'revenue_net' THEN value END), 0) IS NOT NULL)
       OR
       (MAX(CASE WHEN canonical_name = 'total_income' THEN value END) IS NOT NULL
        AND NULLIF(MAX(CASE WHEN canonical_name = 'total_income' THEN value END), 0) IS NOT NULL)
     )
),
candidates AS (
  SELECT ticker, period_end, period_duration, section, method, method_rank,
    MAX(CASE WHEN canonical_name = 'net_profit'  THEN value END) AS numerator,
    MAX(CASE WHEN canonical_name = 'net_premium' THEN value END) AS denominator
  FROM flow_components
  WHERE canonical_name IN ('net_profit', 'net_premium')
  GROUP BY ticker, period_end, period_duration, section, method, method_rank
  HAVING numerator IS NOT NULL
     AND denominator IS NOT NULL
     AND NULLIF(denominator, 0) IS NOT NULL
),
ranked AS (
  SELECT *, ROW_NUMBER() OVER (
    PARTITION BY ticker, period_end, period_duration, section
    ORDER BY method_rank DESC, method DESC
  ) AS rn
  FROM candidates c
  WHERE NOT EXISTS (
    SELECT 1 FROM higher_candidates h
    WHERE h.ticker=c.ticker AND h.period_end=c.period_end
      AND h.period_duration=c.period_duration AND h.section=c.section
  )
)
INSERT INTO ratios (ticker, period_end, period_duration, section, ratio_name, value, basis)
SELECT ticker, period_end, period_duration, section,
  'net_margin', numerator * 1.0 / denominator,
  'net_premium'
FROM ranked
WHERE rn = 1;

-- Net margin fallback 4: net_profit / operating_revenue (holding cos, FCSC etc).
-- Use only when no higher-priority same-method candidate exists.
WITH flow_components AS ({FLOW_RAW_ALL}),
higher_candidates AS (
  SELECT ticker, period_end, period_duration, section, method
  FROM flow_components
  WHERE canonical_name IN ('net_profit', 'revenue_net', 'total_income', 'net_premium')
  GROUP BY ticker, period_end, period_duration, section, method
  HAVING MAX(CASE WHEN canonical_name = 'net_profit' THEN value END) IS NOT NULL
     AND (
       (MAX(CASE WHEN canonical_name = 'revenue_net' THEN value END) IS NOT NULL
        AND NULLIF(MAX(CASE WHEN canonical_name = 'revenue_net' THEN value END), 0) IS NOT NULL)
       OR
       (MAX(CASE WHEN canonical_name = 'total_income' THEN value END) IS NOT NULL
        AND NULLIF(MAX(CASE WHEN canonical_name = 'total_income' THEN value END), 0) IS NOT NULL)
       OR
       (MAX(CASE WHEN canonical_name = 'net_premium' THEN value END) IS NOT NULL
        AND NULLIF(MAX(CASE WHEN canonical_name = 'net_premium' THEN value END), 0) IS NOT NULL)
     )
),
candidates AS (
  SELECT ticker, period_end, period_duration, section, method, method_rank,
    MAX(CASE WHEN canonical_name = 'net_profit'        THEN value END) AS numerator,
    MAX(CASE WHEN canonical_name = 'operating_revenue' THEN value END) AS denominator
  FROM flow_components
  WHERE canonical_name IN ('net_profit', 'operating_revenue')
  GROUP BY ticker, period_end, period_duration, section, method, method_rank
  HAVING numerator IS NOT NULL
     AND denominator IS NOT NULL
     AND NULLIF(denominator, 0) IS NOT NULL
),
ranked AS (
  SELECT *, ROW_NUMBER() OVER (
    PARTITION BY ticker, period_end, period_duration, section
    ORDER BY method_rank DESC, method DESC
  ) AS rn
  FROM candidates c
  WHERE NOT EXISTS (
    SELECT 1 FROM higher_candidates h
    WHERE h.ticker=c.ticker AND h.period_end=c.period_end
      AND h.period_duration=c.period_duration AND h.section=c.section
  )
)
INSERT INTO ratios (ticker, period_end, period_duration, section, ratio_name, value, basis)
SELECT ticker, period_end, period_duration, section,
  'net_margin', numerator * 1.0 / denominator,
  'operating_revenue'
FROM ranked
WHERE rn = 1;

-- Gross margin = gross_profit / revenue_net
WITH flow_components AS ({FLOW_RAW_ALL}),
candidates AS (
  SELECT ticker, period_end, period_duration, section, method, method_rank,
    MAX(CASE WHEN canonical_name = 'gross_profit' THEN value END) AS numerator,
    MAX(CASE WHEN canonical_name = 'revenue_net'  THEN value END) AS denominator
  FROM flow_components
  WHERE canonical_name IN ('gross_profit', 'revenue_net')
  GROUP BY ticker, period_end, period_duration, section, method, method_rank
  HAVING numerator IS NOT NULL
     AND denominator IS NOT NULL
     AND NULLIF(denominator, 0) IS NOT NULL
),
ranked AS (
  SELECT *, ROW_NUMBER() OVER (
    PARTITION BY ticker, period_end, period_duration, section
    ORDER BY method_rank DESC, method DESC
  ) AS rn
  FROM candidates
)
INSERT INTO ratios (ticker, period_end, period_duration, section, ratio_name, value, basis)
SELECT ticker, period_end, period_duration, section,
  'gross_margin', numerator * 1.0 / denominator,
  'revenue_net'
FROM ranked
WHERE rn = 1;

-- Current ratio = total_current_assets / total_current_liabilities
WITH best_bs AS ({BS})
INSERT INTO ratios (ticker, period_end, period_duration, section, ratio_name, value, basis)
SELECT ticker, period_end, period_duration, section,
  'current_ratio',
  MAX(CASE WHEN canonical_name = 'total_current_assets'       THEN value END) * 1.0
  / NULLIF(MAX(CASE WHEN canonical_name = 'total_current_liabilities' THEN value END), 0),
  'total_current_liabilities'
FROM best_bs
WHERE canonical_name IN ('total_current_assets', 'total_current_liabilities')
GROUP BY ticker, period_end, period_duration, section
HAVING MAX(CASE WHEN canonical_name = 'total_current_assets'       THEN value END) IS NOT NULL
   AND MAX(CASE WHEN canonical_name = 'total_current_liabilities' THEN value END) IS NOT NULL
   AND NULLIF(MAX(CASE WHEN canonical_name = 'total_current_liabilities' THEN value END), 0) IS NOT NULL;

-- Debt-to-equity = total_liabilities / total_equity
WITH best_bs AS ({BS})
INSERT INTO ratios (ticker, period_end, period_duration, section, ratio_name, value, basis)
SELECT ticker, period_end, period_duration, section,
  'debt_to_equity',
  MAX(CASE WHEN canonical_name = 'total_liabilities' THEN value END) * 1.0
  / NULLIF(MAX(CASE WHEN canonical_name = 'total_equity'      THEN value END), 0),
  'total_equity'
FROM best_bs
WHERE canonical_name IN ('total_liabilities', 'total_equity')
GROUP BY ticker, period_end, period_duration, section
HAVING MAX(CASE WHEN canonical_name = 'total_liabilities' THEN value END) IS NOT NULL
   AND MAX(CASE WHEN canonical_name = 'total_equity'      THEN value END) IS NOT NULL
   AND NULLIF(MAX(CASE WHEN canonical_name = 'total_equity' THEN value END), 0) IS NOT NULL;

-- Asset turnover = revenue_net (12M/LTM) / latest PIT total_assets on or before flow period_end
WITH best_flow AS ({FLOW}),
best_bs AS ({BS})
INSERT INTO ratios (ticker, period_end, period_duration, section, ratio_name, value, basis)
SELECT p.ticker, p.period_end, p.period_duration, p.section,
  'asset_turnover', p.value * 1.0 / NULLIF(b.value, 0), 'revenue_net'
FROM best_flow p
JOIN best_bs b
  ON b.ticker = p.ticker
 AND b.section = p.section
 AND b.canonical_name = 'total_assets'
 AND b.period_end = (
   SELECT MAX(bx.period_end)
   FROM best_bs bx
   WHERE bx.ticker = p.ticker
     AND bx.section = p.section
     AND bx.canonical_name = 'total_assets'
     AND bx.period_end <= p.period_end
 )
WHERE p.canonical_name = 'revenue_net' AND b.canonical_name = 'total_assets';

-- Asset turnover fallback 2: total_income / total_assets (banks).
-- Only when revenue_net is absent for that ticker+period.
WITH best_flow AS ({FLOW}),
best_bs AS ({BS}),
has_rev AS (
  SELECT DISTINCT ticker, period_end, period_duration, section
  FROM financial_statements
  WHERE canonical_name='revenue_net' AND period_duration IN ('12M','LTM')
)
INSERT INTO ratios (ticker, period_end, period_duration, section, ratio_name, value, basis)
SELECT p.ticker, p.period_end, p.period_duration, p.section,
  'asset_turnover', p.value * 1.0 / NULLIF(b.value, 0), 'total_income'
FROM best_flow p
JOIN best_bs b
  ON b.ticker = p.ticker
 AND b.section = p.section
 AND b.canonical_name = 'total_assets'
 AND b.period_end = (
   SELECT MAX(bx.period_end)
   FROM best_bs bx
   WHERE bx.ticker = p.ticker
     AND bx.section = p.section
     AND bx.canonical_name = 'total_assets'
     AND bx.period_end <= p.period_end
 )
WHERE p.canonical_name = 'total_income'
  AND b.canonical_name = 'total_assets'
  AND NOT EXISTS (
    SELECT 1 FROM has_rev hr
    WHERE hr.ticker=p.ticker AND hr.period_end=p.period_end
      AND hr.period_duration=p.period_duration AND hr.section=p.section
  );

-- Asset turnover fallback 3: net_premium / total_assets (insurance).
-- Only when revenue_net and total_income are both absent.
WITH best_flow AS ({FLOW}),
best_bs AS ({BS}),
has_rev AS (
  SELECT DISTINCT ticker, period_end, period_duration, section
  FROM financial_statements
  WHERE canonical_name IN ('revenue_net','total_income') AND period_duration IN ('12M','LTM')
)
INSERT INTO ratios (ticker, period_end, period_duration, section, ratio_name, value, basis)
SELECT p.ticker, p.period_end, p.period_duration, p.section,
  'asset_turnover', p.value * 1.0 / NULLIF(b.value, 0), 'net_premium'
FROM best_flow p
JOIN best_bs b
  ON b.ticker = p.ticker
 AND b.section = p.section
 AND b.canonical_name = 'total_assets'
 AND b.period_end = (
   SELECT MAX(bx.period_end)
   FROM best_bs bx
   WHERE bx.ticker = p.ticker
     AND bx.section = p.section
     AND bx.canonical_name = 'total_assets'
     AND bx.period_end <= p.period_end
 )
WHERE p.canonical_name = 'net_premium'
  AND b.canonical_name = 'total_assets'
  AND NOT EXISTS (
    SELECT 1 FROM has_rev hr
    WHERE hr.ticker=p.ticker AND hr.period_end=p.period_end
      AND hr.period_duration=p.period_duration AND hr.section=p.section
  );

-- Asset turnover fallback 4: operating_revenue / total_assets (holding cos, FCSC etc).
-- Only when revenue_net, total_income, and net_premium are all absent.
WITH best_flow AS ({FLOW}),
best_bs AS ({BS}),
has_rev AS (
  SELECT DISTINCT ticker, period_end, period_duration, section
  FROM financial_statements
  WHERE canonical_name IN ('revenue_net','total_income','net_premium')
    AND period_duration IN ('12M','LTM')
)
INSERT INTO ratios (ticker, period_end, period_duration, section, ratio_name, value, basis)
SELECT p.ticker, p.period_end, p.period_duration, p.section,
  'asset_turnover', p.value * 1.0 / NULLIF(b.value, 0), 'operating_revenue'
FROM best_flow p
JOIN best_bs b
  ON b.ticker = p.ticker
 AND b.section = p.section
 AND b.canonical_name = 'total_assets'
 AND b.period_end = (
   SELECT MAX(bx.period_end)
   FROM best_bs bx
   WHERE bx.ticker = p.ticker
     AND bx.section = p.section
     AND bx.canonical_name = 'total_assets'
     AND bx.period_end <= p.period_end
 )
WHERE p.canonical_name = 'operating_revenue'
  AND b.canonical_name = 'total_assets'
  AND NOT EXISTS (
    SELECT 1 FROM has_rev hr
    WHERE hr.ticker=p.ticker AND hr.period_end=p.period_end
      AND hr.period_duration=p.period_duration AND hr.section=p.section
  );

-- ── BANKING RATIOS ────────────────────────────────────────────────────────────

-- cost_to_income = -operating_expenses / total_income (banks; operating_expenses is negative).
-- Restricted to income-style reporters: total_income present, revenue_net absent.
WITH flow_components AS ({FLOW_RAW_ALL}),
candidates AS (
  SELECT ticker, period_end, period_duration, section, method, method_rank,
    MAX(CASE WHEN canonical_name = 'operating_expenses' THEN value END) AS numerator,
    MAX(CASE WHEN canonical_name = 'total_income'       THEN value END) AS denominator,
    MAX(CASE WHEN canonical_name = 'revenue_net'        THEN value END) AS revenue_net
  FROM flow_components
  WHERE canonical_name IN ('operating_expenses', 'total_income', 'revenue_net')
  GROUP BY ticker, period_end, period_duration, section, method, method_rank
  HAVING numerator IS NOT NULL
     AND numerator < 0
     AND denominator IS NOT NULL
     AND revenue_net IS NULL
     AND NULLIF(denominator, 0) IS NOT NULL
),
ranked AS (
  SELECT *, ROW_NUMBER() OVER (
    PARTITION BY ticker, period_end, period_duration, section
    ORDER BY method_rank DESC, method DESC
  ) AS rn
  FROM candidates
)
INSERT INTO ratios (ticker, period_end, period_duration, section, ratio_name, value, basis)
SELECT ticker, period_end, period_duration, section,
  'cost_to_income', -numerator * 1.0 / denominator,
  'total_income'
FROM ranked
WHERE rn = 1;

-- net_interest_margin = net_interest_income / total_assets (banks).
-- Uses latest-prior-or-same-date balance sheet (same join pattern as asset_turnover).
WITH best_flow AS ({FLOW}),
best_bs AS ({BS})
INSERT INTO ratios (ticker, period_end, period_duration, section, ratio_name, value, basis)
SELECT p.ticker, p.period_end, p.period_duration, p.section,
  'net_interest_margin', p.value * 1.0 / NULLIF(b.value, 0), 'total_assets'
FROM best_flow p
JOIN best_bs b
  ON b.ticker = p.ticker
 AND b.section = p.section
 AND b.canonical_name = 'total_assets'
 AND b.period_end = (
   SELECT MAX(bx.period_end)
   FROM best_bs bx
   WHERE bx.ticker = p.ticker
     AND bx.section = p.section
     AND bx.canonical_name = 'total_assets'
     AND bx.period_end <= p.period_end
 )
WHERE p.canonical_name = 'net_interest_income' AND b.canonical_name = 'total_assets';

-- ── INSURANCE RATIOS ──────────────────────────────────────────────────────────

-- loss_ratio = -net_claims / net_premium (net_claims stored as negative expense).
WITH flow_components AS ({FLOW_RAW_ALL}),
candidates AS (
  SELECT ticker, period_end, period_duration, section, method, method_rank,
    MAX(CASE WHEN canonical_name = 'net_claims'  THEN value END) AS numerator,
    MAX(CASE WHEN canonical_name = 'net_premium' THEN value END) AS denominator
  FROM flow_components
  WHERE canonical_name IN ('net_claims', 'net_premium')
  GROUP BY ticker, period_end, period_duration, section, method, method_rank
  HAVING numerator IS NOT NULL
     AND denominator IS NOT NULL
     AND NULLIF(denominator, 0) IS NOT NULL
),
ranked AS (
  SELECT *, ROW_NUMBER() OVER (
    PARTITION BY ticker, period_end, period_duration, section
    ORDER BY method_rank DESC, method DESC
  ) AS rn
  FROM candidates
)
INSERT INTO ratios (ticker, period_end, period_duration, section, ratio_name, value, basis)
SELECT ticker, period_end, period_duration, section,
  'loss_ratio', -numerator * 1.0 / denominator,
  'net_premium'
FROM ranked
WHERE rn = 1;

-- expense_ratio = -operating_expenses / net_premium (insurance; operating_expenses is negative).
WITH flow_components AS ({FLOW_RAW_ALL}),
candidates AS (
  SELECT ticker, period_end, period_duration, section, method, method_rank,
    MAX(CASE WHEN canonical_name = 'operating_expenses' THEN value END) AS numerator,
    MAX(CASE WHEN canonical_name = 'net_premium'        THEN value END) AS denominator
  FROM flow_components
  WHERE canonical_name IN ('operating_expenses', 'net_premium')
  GROUP BY ticker, period_end, period_duration, section, method, method_rank
  HAVING numerator IS NOT NULL
     AND denominator IS NOT NULL
     AND NULLIF(denominator, 0) IS NOT NULL
),
ranked AS (
  SELECT *, ROW_NUMBER() OVER (
    PARTITION BY ticker, period_end, period_duration, section
    ORDER BY method_rank DESC, method DESC
  ) AS rn
  FROM candidates
)
INSERT INTO ratios (ticker, period_end, period_duration, section, ratio_name, value, basis)
SELECT ticker, period_end, period_duration, section,
  'expense_ratio', -numerator * 1.0 / denominator,
  'net_premium'
FROM ranked
WHERE rn = 1;

-- combined_ratio = (-net_claims - operating_expenses) / net_premium (insurance).
-- Requires both net_claims and operating_expenses to be present.
WITH flow_components AS ({FLOW_RAW_ALL}),
candidates AS (
  SELECT ticker, period_end, period_duration, section, method, method_rank,
    MAX(CASE WHEN canonical_name = 'net_claims'         THEN value END) AS claims,
    MAX(CASE WHEN canonical_name = 'operating_expenses' THEN value END) AS expenses,
    MAX(CASE WHEN canonical_name = 'net_premium'        THEN value END) AS denominator
  FROM flow_components
  WHERE canonical_name IN ('net_claims', 'operating_expenses', 'net_premium')
  GROUP BY ticker, period_end, period_duration, section, method, method_rank
  HAVING claims IS NOT NULL
     AND expenses IS NOT NULL
     AND denominator IS NOT NULL
     AND NULLIF(denominator, 0) IS NOT NULL
),
ranked AS (
  SELECT *, ROW_NUMBER() OVER (
    PARTITION BY ticker, period_end, period_duration, section
    ORDER BY method_rank DESC, method DESC
  ) AS rn
  FROM candidates
)
INSERT INTO ratios (ticker, period_end, period_duration, section, ratio_name, value, basis)
SELECT ticker, period_end, period_duration, section,
  'combined_ratio', (-claims - expenses) * 1.0 / denominator,
  'net_premium'
FROM ranked
WHERE rn = 1;

-- underwriting_margin = underwriting_profit / net_premium (insurance).
WITH flow_components AS ({FLOW_RAW_ALL}),
candidates AS (
  SELECT ticker, period_end, period_duration, section, method, method_rank,
    MAX(CASE WHEN canonical_name = 'underwriting_profit' THEN value END) AS numerator,
    MAX(CASE WHEN canonical_name = 'net_premium'         THEN value END) AS denominator
  FROM flow_components
  WHERE canonical_name IN ('underwriting_profit', 'net_premium')
  GROUP BY ticker, period_end, period_duration, section, method, method_rank
  HAVING numerator IS NOT NULL
     AND denominator IS NOT NULL
     AND NULLIF(denominator, 0) IS NOT NULL
),
ranked AS (
  SELECT *, ROW_NUMBER() OVER (
    PARTITION BY ticker, period_end, period_duration, section
    ORDER BY method_rank DESC, method DESC
  ) AS rn
  FROM candidates
)
INSERT INTO ratios (ticker, period_end, period_duration, section, ratio_name, value, basis)
SELECT ticker, period_end, period_duration, section,
  'underwriting_margin', numerator * 1.0 / denominator,
  'net_premium'
FROM ranked
WHERE rn = 1;

-- investment_yield = investment_income / net_premium (insurance).
WITH flow_components AS ({FLOW_RAW_ALL}),
candidates AS (
  SELECT ticker, period_end, period_duration, section, method, method_rank,
    MAX(CASE WHEN canonical_name = 'investment_income' THEN value END) AS numerator,
    MAX(CASE WHEN canonical_name = 'net_premium'       THEN value END) AS denominator
  FROM flow_components
  WHERE canonical_name IN ('investment_income', 'net_premium')
  GROUP BY ticker, period_end, period_duration, section, method, method_rank
  HAVING numerator IS NOT NULL
     AND denominator IS NOT NULL
     AND NULLIF(denominator, 0) IS NOT NULL
),
ranked AS (
  SELECT *, ROW_NUMBER() OVER (
    PARTITION BY ticker, period_end, period_duration, section
    ORDER BY method_rank DESC, method DESC
  ) AS rn
  FROM candidates
)
INSERT INTO ratios (ticker, period_end, period_duration, section, ratio_name, value, basis)
SELECT ticker, period_end, period_duration, section,
  'investment_yield', numerator * 1.0 / denominator,
  'net_premium'
FROM ranked
WHERE rn = 1;

DROP TABLE IF EXISTS ratio_blocked_statement_groups;
""".strip() + "\n"


def run_sql(sql: str) -> int:
    SQL_DIR.mkdir(parents=True, exist_ok=True)
    sql_path = SQL_DIR / "rebuild_ratios.sql"
    sql_path.write_text(sql)

    # wrangler d1 execute --file uses the import API (INSERT-only); split into individual
    # statements and execute each via --command to support CTEs and INSERT...SELECT.
    import re as _re
    raw = _re.split(r';\s*\n', sql)
    stmts = [s.strip() for s in raw if s.strip()]
    for i, stmt in enumerate(stmts, 1):
        # Strip SQL line comments — wrangler treats leading -- as CLI flags
        clean = "\n".join(
            line for line in stmt.splitlines() if not line.lstrip().startswith("--")
        ).strip()
        if not clean:
            continue
        result = subprocess.run(
            ["npx", "wrangler", "d1", "execute", D1_DB, "--remote", "--command", clean],
            cwd=PROJECT_ROOT, text=True, capture_output=True,
        )
        if result.returncode != 0:
            print(f"Statement {i} failed:\n{stmt[:200]}", file=sys.stderr)
            print(result.stderr[-1000:] if result.stderr else "(no stderr)", file=sys.stderr)
            return result.returncode
        print(f"  [{i}/{len(stmts)}] ok")
    return 0


def count_ratios() -> int:
    result = subprocess.run(
        ["npx", "wrangler", "d1", "execute", D1_DB, "--remote",
         "--command", "SELECT COUNT(*) as n FROM ratios"],
        cwd=PROJECT_ROOT, text=True, capture_output=True,
    )
    import re
    m = re.search(r'"n":\s*(\d+)', result.stdout)
    return int(m.group(1)) if m else -1


RATIO_COMPONENT_FIELDS = {
    "net_profit", "gross_profit", "revenue_net", "total_income", "net_premium", "operating_revenue",
    "total_assets", "total_equity", "total_liabilities", "total_current_assets", "total_current_liabilities",
    "operating_expenses", "net_interest_income", "net_claims", "underwriting_profit", "investment_income",
}


def _sql_list(values: list[str]) -> str:
    return ",".join("'" + v.replace("'", "''") + "'" for v in values)


def _fetch_ratio_component_rows(tickers: list[str]) -> list[dict]:
    rows: list[dict] = []
    fields = sorted(RATIO_COMPONENT_FIELDS)
    for i in range(0, len(tickers), 60):
        chunk = tickers[i:i + 60]
        rows.extend(d1_query(f"""
SELECT ticker, period_end, period_duration, section, statement_type, canonical_name, value, method, source_file, source_pages
FROM financial_statements
WHERE ticker IN ({_sql_list(chunk)})
  AND canonical_name IN ({_sql_list(fields)})
  AND section IN ('consolidated','unconsolidated')
  AND COALESCE(qc_flag, '') = ''
ORDER BY ticker, period_end, period_duration, section, canonical_name;
"""))
    return rows


def _scale_suspect_keys(fs_rows: list[dict]) -> set[tuple[str, str, str, str, str]]:
    grouped: dict = defaultdict(list)
    for row in fs_rows:
        value = row.get("value")
        if value is None or value == 0:
            continue
        key = (row.get("ticker"), row.get("section"), row.get("canonical_name"), row.get("period_duration"))
        grouped[key].append(row)
    suspect: set[tuple[str, str, str, str, str]] = set()
    for key, rows in grouped.items():
        rows.sort(key=lambda r: r.get("period_end") or "")
        for prev, cur in zip(rows, rows[1:]):
            a, b = abs(prev.get("value") or 0), abs(cur.get("value") or 0)
            if a < 1_000 or b < 1_000:
                continue
            ratio = max(a, b) / max(min(a, b), 1e-9)
            if ratio >= 100:
                suspect.add((cur.get("ticker"), cur.get("section"), cur.get("canonical_name"),
                             cur.get("period_duration"), cur.get("period_end")))
    return suspect


def enrich_ratio_outliers(ratio_rows: list[dict]) -> list[dict]:
    if not ratio_rows:
        return []
    tickers = sorted({row["ticker"] for row in ratio_rows if row.get("ticker")})
    fs_rows = _fetch_ratio_component_rows(tickers)
    by_key: dict = defaultdict(list)
    bs_by: dict = defaultdict(list)
    for row in fs_rows:
        by_key[(row["ticker"], row["period_end"], row["period_duration"], row["section"], row["canonical_name"])].append(row)
        if row["period_duration"] == "PIT":
            bs_by[(row["ticker"], row["section"], row["canonical_name"])].append(row)
    for rows in bs_by.values():
        rows.sort(key=lambda r: r.get("period_end") or "")
    scale_suspects = _scale_suspect_keys(fs_rows)

    def val(ticker, period_end, duration, section, canonical):
        rows = by_key.get((ticker, period_end, duration, section, canonical)) or []
        return rows[-1] if rows else None

    def bs_val(ticker, period_end, section, canonical):
        rows = [r for r in bs_by.get((ticker, section, canonical), []) if r.get("period_end") <= period_end]
        return rows[-1] if rows else None

    def same_method_pair(ticker, period_end, duration, section, numerator_name, denominator_name, ratio_value):
        num_rows = val_candidates(ticker, period_end, duration, section, numerator_name)
        den_rows = val_candidates(ticker, period_end, duration, section, denominator_name)
        best = None
        for num in num_rows:
            for den in den_rows:
                if num.get("method") != den.get("method"):
                    continue
                denominator = den.get("value")
                if denominator in (None, 0):
                    continue
                computed = (num.get("value") or 0) / denominator
                distance = abs(computed - ratio_value) if ratio_value is not None else 0
                rank = method_rank(num.get("method"))
                candidate = (distance, -rank, num.get("method") or "", num, den)
                if best is None or candidate < best:
                    best = candidate
        if best:
            return best[3], best[4]
        return val(ticker, period_end, duration, section, numerator_name), val(ticker, period_end, duration, section, denominator_name)

    def val_candidates(ticker, period_end, duration, section, canonical):
        return by_key.get((ticker, period_end, duration, section, canonical)) or []

    def method_rank(method: str | None) -> int:
        if not method:
            return 1
        if method.startswith("LTM_9M"):
            return 4
        if method.startswith("LTM_6M"):
            return 3
        if method.startswith("LTM_3M"):
            return 2
        return 1

    def components(row: dict) -> tuple[dict | None, dict | None, str | None, str | None]:
        ticker, period_end, duration, section = row["ticker"], row["period_end"], row["period_duration"], row["section"]
        metric, basis = row["ratio_name"], row.get("basis")
        if metric == "net_margin":
            num, den = same_method_pair(ticker, period_end, duration, section, "net_profit", basis, row.get("value"))
            return num, den, "net_profit", basis
        if metric == "gross_margin":
            num, den = same_method_pair(ticker, period_end, duration, section, "gross_profit", "revenue_net", row.get("value"))
            return num, den, "gross_profit", "revenue_net"
        if metric == "roe":
            return val(ticker, period_end, duration, section, "net_profit"), bs_val(ticker, period_end, section, "total_equity"), "net_profit", "total_equity"
        if metric == "roa":
            return val(ticker, period_end, duration, section, "net_profit"), bs_val(ticker, period_end, section, "total_assets"), "net_profit", "total_assets"
        if metric == "asset_turnover":
            return val(ticker, period_end, duration, section, basis), bs_val(ticker, period_end, section, "total_assets"), basis, "total_assets"
        if metric == "cost_to_income":
            num, den = same_method_pair(ticker, period_end, duration, section, "operating_expenses", "total_income", -(row.get("value") or 0))
            num = {**num, "value": -num["value"]} if num and num.get("value") is not None else None
            return num, den, "abs(operating_expenses)", "total_income"
        if metric == "net_interest_margin":
            return val(ticker, period_end, duration, section, "net_interest_income"), bs_val(ticker, period_end, section, "total_assets"), "net_interest_income", "total_assets"
        if metric == "debt_to_equity":
            return val(ticker, period_end, "PIT", section, "total_liabilities"), val(ticker, period_end, "PIT", section, "total_equity"), "total_liabilities", "total_equity"
        if metric == "current_ratio":
            return val(ticker, period_end, "PIT", section, "total_current_assets"), val(ticker, period_end, "PIT", section, "total_current_liabilities"), "total_current_assets", "total_current_liabilities"
        if metric == "loss_ratio":
            num, den = same_method_pair(ticker, period_end, duration, section, "net_claims", "net_premium", -(row.get("value") or 0))
            num = {**num, "value": -num["value"]} if num and num.get("value") is not None else None
            return num, den, "abs(net_claims)", "net_premium"
        if metric == "expense_ratio":
            num, den = same_method_pair(ticker, period_end, duration, section, "operating_expenses", "net_premium", -(row.get("value") or 0))
            num = {**num, "value": -num["value"]} if num and num.get("value") is not None else None
            return num, den, "abs(operating_expenses)", "net_premium"
        if metric == "combined_ratio":
            claims = expenses = premium = None
            claim_rows = val_candidates(ticker, period_end, duration, section, "net_claims")
            expense_rows = val_candidates(ticker, period_end, duration, section, "operating_expenses")
            premium_rows = val_candidates(ticker, period_end, duration, section, "net_premium")
            best = None
            for claim in claim_rows:
                for expense in expense_rows:
                    for candidate_premium in premium_rows:
                        if claim.get("method") != expense.get("method") or claim.get("method") != candidate_premium.get("method"):
                            continue
                        denominator = candidate_premium.get("value")
                        if denominator in (None, 0):
                            continue
                        computed = (-(claim.get("value") or 0) - (expense.get("value") or 0)) / denominator
                        distance = abs(computed - (row.get("value") or 0))
                        candidate = (distance, -method_rank(claim.get("method")), claim.get("method") or "", claim, expense, candidate_premium)
                        if best is None or candidate < best:
                            best = candidate
            if best:
                claims, expenses, premium = best[3], best[4], best[5]
            if claims and expenses and claims.get("value") is not None and expenses.get("value") is not None:
                num = {**claims, "value": -claims["value"] - expenses["value"]}
            else:
                num = None
            return num, premium, "abs(net_claims)+abs(operating_expenses)", "net_premium"
        if metric == "underwriting_margin":
            num, den = same_method_pair(ticker, period_end, duration, section, "underwriting_profit", "net_premium", row.get("value"))
            return num, den, "underwriting_profit", "net_premium"
        if metric == "investment_yield":
            num, den = same_method_pair(ticker, period_end, duration, section, "investment_income", "net_premium", row.get("value"))
            return num, den, "investment_income", "net_premium"
        return None, None, None, None

    enriched = []
    for row in ratio_rows:
        num_row, den_row, num_name, den_name = components(row)
        numerator = num_row.get("value") if num_row else None
        denominator = den_row.get("value") if den_row else None
        component_keys = []
        for component in (num_row, den_row):
            if component:
                component_keys.append((component.get("ticker"), component.get("section"), component.get("canonical_name"),
                                       component.get("period_duration"), component.get("period_end")))
        if numerator is None or denominator is None:
            diagnostic = "missing_component"
        elif denominator == 0:
            diagnostic = "zero_denominator_should_not_emit"
        elif abs(denominator) < max(1_000, 0.01 * abs(numerator)):
            diagnostic = "tiny_denominator"
        elif denominator < 0 and row["ratio_name"] in {"net_margin", "gross_margin", "cost_to_income", "asset_turnover"}:
            diagnostic = "negative_denominator"
        elif denominator < 0 and den_name == "total_equity":
            diagnostic = "negative_equity"
        elif any(key in scale_suspects for key in component_keys) and row["ratio_name"] != "current_ratio":
            diagnostic = "unit_or_scale_suspect"
        else:
            diagnostic = "plausible_business_outlier"
        enriched.append({
            **row,
            "diagnostic": diagnostic,
            "numerator_name": num_name,
            "numerator": numerator,
            "numerator_source_file": num_row.get("source_file") if num_row else None,
            "denominator_name": den_name,
            "denominator": denominator,
            "denominator_source_file": den_row.get("source_file") if den_row else None,
        })
    return enriched


def build_coverage_report(total_ratio_rows: int) -> dict:
    coverage = d1_query("""
SELECT section, period_duration, ratio_name, COUNT(*) AS rows, COUNT(DISTINCT ticker) AS tickers
FROM ratios
GROUP BY section, period_duration, ratio_name
ORDER BY section, period_duration, ratio_name;
""")
    universe = d1_query("""
WITH u AS (SELECT DISTINCT ticker FROM financial_statements WHERE section='consolidated'),
     ltm AS (SELECT DISTINCT ticker FROM financial_statements WHERE section='consolidated' AND period_duration='LTM'),
     r AS (SELECT DISTINCT ticker FROM ratios WHERE section='consolidated' AND period_duration='LTM')
SELECT COUNT(*) AS consolidated_universe,
       SUM(CASE WHEN ltm.ticker IS NOT NULL THEN 1 ELSE 0 END) AS fs_has_any_ltm,
       SUM(CASE WHEN r.ticker IS NOT NULL THEN 1 ELSE 0 END) AS ratios_has_any_ltm,
       SUM(CASE WHEN ltm.ticker IS NULL THEN 1 ELSE 0 END) AS fs_missing_any_ltm
FROM u
LEFT JOIN ltm ON ltm.ticker=u.ticker
LEFT JOIN r ON r.ticker=u.ticker;
""")
    missing_ltm_statement = d1_query("""
WITH u AS (SELECT DISTINCT ticker FROM financial_statements WHERE section='consolidated'),
     ltm AS (SELECT DISTINCT ticker FROM financial_statements WHERE section='consolidated' AND period_duration='LTM')
SELECT u.ticker
FROM u LEFT JOIN ltm ON ltm.ticker=u.ticker
WHERE ltm.ticker IS NULL
ORDER BY u.ticker;
""")
    missing_ltm_ratio_detail = d1_query("""
WITH fs AS (
  SELECT DISTINCT ticker FROM financial_statements
  WHERE section='consolidated' AND period_duration='LTM'
),
r AS (
  SELECT DISTINCT ticker FROM ratios
  WHERE section='consolidated' AND period_duration='LTM'
),
missing AS (
  SELECT fs.ticker FROM fs LEFT JOIN r ON r.ticker=fs.ticker WHERE r.ticker IS NULL
),
pl AS (
  SELECT ticker, period_end,
         MAX(CASE WHEN canonical_name='net_profit' THEN 1 ELSE 0 END) AS has_np,
         MAX(CASE WHEN canonical_name IN ('revenue_net','total_income','net_premium','operating_revenue') THEN 1 ELSE 0 END) AS has_income
  FROM financial_statements
  WHERE statement_type='profit_loss' AND section='consolidated' AND period_duration='LTM'
  GROUP BY ticker, period_end
),
bs AS (
  SELECT ticker, period_end,
         MAX(CASE WHEN canonical_name='total_equity' THEN 1 ELSE 0 END) AS has_equity,
         MAX(CASE WHEN canonical_name='total_assets' THEN 1 ELSE 0 END) AS has_assets
  FROM financial_statements
  WHERE statement_type='balance_sheet' AND section='consolidated' AND period_duration='PIT'
  GROUP BY ticker, period_end
)
SELECT m.ticker,
       COUNT(pl.period_end) AS ltm_pl_periods,
       SUM(CASE WHEN pl.has_np=1 THEN 1 ELSE 0 END) AS periods_with_net_profit,
       SUM(CASE WHEN pl.has_income=1 THEN 1 ELSE 0 END) AS periods_with_revenue_or_income,
       SUM(CASE WHEN bs.period_end IS NOT NULL THEN 1 ELSE 0 END) AS periods_with_same_date_bs,
       SUM(CASE WHEN EXISTS (
         SELECT 1 FROM bs bx
         WHERE bx.ticker=m.ticker AND bx.period_end <= pl.period_end
           AND (bx.has_equity=1 OR bx.has_assets=1)
       ) THEN 1 ELSE 0 END) AS periods_with_prior_or_same_bs,
       SUM(CASE WHEN pl.has_np=1 AND bs.has_equity=1 THEN 1 ELSE 0 END) AS possible_roe_periods,
       SUM(CASE WHEN pl.has_np=1 AND bs.has_assets=1 THEN 1 ELSE 0 END) AS possible_roa_periods,
       SUM(CASE WHEN pl.has_np=1 AND EXISTS (
         SELECT 1 FROM bs bx
         WHERE bx.ticker=m.ticker AND bx.period_end <= pl.period_end AND bx.has_equity=1
       ) THEN 1 ELSE 0 END) AS possible_roe_periods_with_prior_bs,
       SUM(CASE WHEN pl.has_np=1 AND EXISTS (
         SELECT 1 FROM bs bx
         WHERE bx.ticker=m.ticker AND bx.period_end <= pl.period_end AND bx.has_assets=1
       ) THEN 1 ELSE 0 END) AS possible_roa_periods_with_prior_bs,
       SUM(CASE WHEN pl.has_np=1 AND pl.has_income=1 THEN 1 ELSE 0 END) AS possible_margin_periods
FROM missing m
LEFT JOIN pl ON pl.ticker=m.ticker
LEFT JOIN bs ON bs.ticker=m.ticker AND bs.period_end=pl.period_end
GROUP BY m.ticker
ORDER BY m.ticker;
""")
    ltm_profit_profile = d1_query("""
WITH latest AS (
  SELECT ticker, MAX(period_end) AS period_end
  FROM financial_statements
  WHERE statement_type='profit_loss'
    AND section='consolidated'
    AND period_duration='LTM'
    AND canonical_name='net_profit'
  GROUP BY ticker
),
latest_values AS (
  SELECT fs.ticker, MAX(fs.value) AS value
  FROM latest l
  JOIN financial_statements fs
    ON fs.ticker=l.ticker
   AND fs.period_end=l.period_end
   AND fs.statement_type='profit_loss'
   AND fs.section='consolidated'
   AND fs.period_duration='LTM'
   AND fs.canonical_name='net_profit'
  GROUP BY fs.ticker
)
SELECT COUNT(*) AS tickers_with_ltm_net_profit,
       SUM(CASE WHEN value < 0 THEN 1 ELSE 0 END) AS latest_ltm_net_loss_tickers,
       SUM(CASE WHEN value = 0 THEN 1 ELSE 0 END) AS latest_ltm_zero_net_profit_tickers,
       SUM(CASE WHEN value > 0 THEN 1 ELSE 0 END) AS latest_ltm_profitable_tickers
FROM latest_values;
""")
    ratio_outliers_raw = d1_query("""
SELECT ticker, period_end, period_duration, section, ratio_name, value, basis
FROM ratios
WHERE
  (ratio_name IN ('net_margin','gross_margin','roe','roa') AND ABS(value) > 2)
  OR (ratio_name='asset_turnover' AND (value > 10 OR value < 0))
  OR (ratio_name='cost_to_income' AND (value > 3 OR value < 0))
  OR (ratio_name='debt_to_equity' AND ABS(value) > 50)
  OR (ratio_name='current_ratio' AND value > 50)
  OR (ratio_name IN ('loss_ratio','expense_ratio','combined_ratio','underwriting_margin','investment_yield') AND ABS(value) > 5)
ORDER BY ABS(value) DESC;
""")
    ratio_outliers = enrich_ratio_outliers(ratio_outliers_raw)
    ratio_diagnostic_counts = Counter(row["diagnostic"] for row in ratio_outliers)

    missing_cause_counts = {
        "no_ltm_pl_periods": 0,
        "missing_net_profit": 0,
        "missing_revenue_or_income": 0,
        "missing_same_date_balance_sheet": 0,
        "missing_prior_or_same_balance_sheet": 0,
        "has_prior_bs_but_no_exact_bs": 0,
        "has_margin_inputs_but_no_bs_join": 0,
    }
    for row in missing_ltm_ratio_detail:
        if int(row.get("ltm_pl_periods") or 0) == 0:
            missing_cause_counts["no_ltm_pl_periods"] += 1
        if int(row.get("periods_with_net_profit") or 0) == 0:
            missing_cause_counts["missing_net_profit"] += 1
        if int(row.get("periods_with_revenue_or_income") or 0) == 0:
            missing_cause_counts["missing_revenue_or_income"] += 1
        if int(row.get("periods_with_same_date_bs") or 0) == 0:
            missing_cause_counts["missing_same_date_balance_sheet"] += 1
        if int(row.get("periods_with_prior_or_same_bs") or 0) == 0:
            missing_cause_counts["missing_prior_or_same_balance_sheet"] += 1
        if int(row.get("periods_with_prior_or_same_bs") or 0) > 0 and int(row.get("periods_with_same_date_bs") or 0) == 0:
            missing_cause_counts["has_prior_bs_but_no_exact_bs"] += 1
        if int(row.get("possible_margin_periods") or 0) > 0 and int(row.get("periods_with_prior_or_same_bs") or 0) == 0:
            missing_cause_counts["has_margin_inputs_but_no_bs_join"] += 1

    report = {
        "total_ratio_rows": total_ratio_rows,
        "coverage": coverage,
        "consolidated_ltm_universe": universe[0] if universe else {},
        "consolidated_tickers_missing_any_ltm_statement": [r["ticker"] for r in missing_ltm_statement],
        "consolidated_ltm_ratio_missing_detail": missing_ltm_ratio_detail,
        "consolidated_ltm_ratio_missing_cause_counts": missing_cause_counts,
        "consolidated_ltm_net_profit_profile": ltm_profit_profile[0] if ltm_profit_profile else {},
        "ratio_outliers": ratio_outliers,
        "ratio_outlier_rows": len(ratio_outliers),
        "ratio_outlier_tickers": len({r["ticker"] for r in ratio_outliers}),
        "ratio_outlier_diagnostic_counts": dict(ratio_diagnostic_counts),
    }
    SQL_DIR.mkdir(parents=True, exist_ok=True)
    (SQL_DIR / "coverage_report.json").write_text(json.dumps(report, indent=2))
    return report


def format_coverage_decision(report: dict) -> str:
    universe = report.get("consolidated_ltm_universe", {})
    profit = report.get("consolidated_ltm_net_profit_profile", {})
    causes = report.get("consolidated_ltm_ratio_missing_cause_counts", {})
    diagnostics = report.get("ratio_outlier_diagnostic_counts", {})
    diag_text = ", ".join(f"{k}={v:,}" for k, v in sorted(diagnostics.items())) or "none"
    return (
        f"Ratios rebuilt with {report['total_ratio_rows']:,} rows. "
        f"Consolidated LTM statements cover {int(universe.get('fs_has_any_ltm') or 0):,}/"
        f"{int(universe.get('consolidated_universe') or 0):,} tickers; LTM ratios cover "
        f"{int(universe.get('ratios_has_any_ltm') or 0):,}. "
        f"Latest LTM net profit profile: {int(profit.get('latest_ltm_net_loss_tickers') or 0):,} net-loss tickers, "
        f"{int(profit.get('latest_ltm_profitable_tickers') or 0):,} profitable tickers. "
        f"Missing LTM ratio causes include {causes.get('no_ltm_pl_periods', 0):,} with no LTM PL periods, "
        f"{causes.get('missing_revenue_or_income', 0):,} missing revenue/income, and "
        f"{causes.get('missing_prior_or_same_balance_sheet', 0):,} missing usable prior/same-date balance-sheet joins "
        f"({causes.get('has_prior_bs_but_no_exact_bs', 0):,} have prior BS but no exact-date BS). "
        f"Ratio warning diagnostics: {diag_text}. "
        "See artifacts/15_ratios/coverage_report.json."
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Step 15 — Rebuild ratios on D1")
    ap.add_argument("--sql-only", action="store_true", help="Write SQL without uploading")
    a = ap.parse_args()

    sql = build_sql()
    SQL_DIR.mkdir(parents=True, exist_ok=True)
    sql_path = SQL_DIR / "rebuild_ratios.sql"
    sql_path.write_text(sql)
    print(f"SQL written → {sql_path}")

    if a.sql_only:
        return 0

    print("Uploading to D1 (drop + recreate ratios)...")
    rc = run_sql(sql)
    if rc != 0:
        print("ERROR: wrangler returned non-zero exit code", file=sys.stderr)
        return rc

    n = count_ratios()
    coverage_report = build_coverage_report(n)
    ratio_issues = []
    for row in coverage_report.get("ratio_outliers", []):
        diagnostic = row.get("diagnostic", "ratio_outlier")
        if diagnostic not in USER_VISIBLE_RATIO_DIAGNOSTICS:
            continue
        ratio_issues.append({
            "severity": "warning",
            "ticker": row.get("ticker"),
            "statement_type": "ratio",
            "period_end": row.get("period_end"),
            "period_duration": row.get("period_duration"),
            "section": row.get("section"),
            "canonical_name": row.get("ratio_name"),
            "value": row.get("value"),
            "basis": row.get("basis"),
            "rule": diagnostic,
            "numerator_name": row.get("numerator_name"),
            "numerator": row.get("numerator"),
            "denominator_name": row.get("denominator_name"),
            "denominator": row.get("denominator"),
            "message": (
                f"{row.get('ratio_name')}={row.get('value')} using {row.get('basis')} "
                f"diagnostic={diagnostic} numerator={row.get('numerator')} denominator={row.get('denominator')}"
            ),
            "origin_stage": "15_ratios",
            "recommended_command": f"python3 pipeline/15_ratios.py && python3 run.py --chain state",
        })
    write_stage_issues("15_ratios", ratio_issues)
    write_stage_lineage("15_ratios", coverage_report.get("ratio_outliers", []))
    write_report("15_ratios", coverage_report)
    emit_decision("15_ratios", format_coverage_decision(coverage_report))
    print(f"\n{'─'*60}")
    print(f"ratios table rebuilt — {n:,} rows")
    print(f"{'─'*60}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
