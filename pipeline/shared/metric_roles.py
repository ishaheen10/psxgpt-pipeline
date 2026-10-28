"""Audited mappings from financial-statement rows to downstream metric roles."""

from __future__ import annotations

from typing import Any


# Downstream calculations should depend on these roles, not scattered string
# checks. Keep NCI dividends out of the dividend-yield numerator.
METRIC_ROLES: dict[str, set[tuple[str, str]]] = {
    "market_net_income": {
        ("profit_loss", "net_profit"),
    },
    "market_dividends_paid": {
        ("cash_flow", "dividends_paid"),
    },
}


# Legacy compiled rows may carry a key_metric instead of canonical_name. These
# are compatibility only; new pipeline rows should use canonical_name.
LEGACY_KEY_METRIC_ROLES: dict[str, set[str]] = {
    "market_net_income": {"net_profit"},
    "market_dividends_paid": {"dividend_paid"},
}


def row_has_metric_role(row: dict[str, Any], role: str) -> bool:
    statement_type = row.get("statement_type")
    canonical_name = row.get("canonical_name")
    if (statement_type, canonical_name) in METRIC_ROLES.get(role, set()):
        return True

    key_metric = row.get("key_metric")
    return bool(key_metric and key_metric in LEGACY_KEY_METRIC_ROLES.get(role, set()))
