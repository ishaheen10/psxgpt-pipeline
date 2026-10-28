"""Local model price table + cost estimators. Cost is computed by code, never model-reported."""
from __future__ import annotations

# USD per 1M tokens (cache-miss input). Effective 2026-06.
PRICES = {
    "gemini-3.1-flash-lite": {"in": 0.10, "out": 0.40},
    "deepseek-v4-pro": {"in": 0.435, "out": 0.87},
    "deepseek-v4-flash": {"in": 0.14, "out": 0.28},
}

# Rough tokens/page (input, output) per model step, for pre-run estimates.
PARSE_TOK = (1200, 800)      # gemini: a page PDF in, markdown out
TAG_TOK = (1700, 200)        # pro: page md + prompt prefix in, json out
EXTRACT_TOK = (3500, 1500)   # pro: statement pages in, ref-table md out


def cost(model: str, in_tok: int, out_tok: int) -> float:
    p = PRICES[model]
    return round((in_tok * p["in"] + out_tok * p["out"]) / 1_000_000, 4)


def estimate(model: str, pages: int, toks: tuple[int, int]) -> float:
    return round(pages * cost(model, *toks), 2)


def parse_cost(pages: int, model: str = "gemini-3.1-flash-lite") -> float:
    return estimate(model, pages, PARSE_TOK)


def tag_cost(pages: int, model: str = "deepseek-v4-pro") -> float:
    return estimate(model, pages, TAG_TOK)


def extract_cost(units: int, model: str = "deepseek-v4-pro") -> float:
    return estimate(model, units, EXTRACT_TOK)
