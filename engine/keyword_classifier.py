"""Free fallback scorer used when no Claude API key is set.

Rough rules, no AI: impact from the keyword prescore, direction from word polarity
(with India-specific flips: rising crude/dollar is bearish, a rising rupee is bullish).
Market wraps and "live updates" pages are capped so they don't trigger alerts.
"""
from __future__ import annotations

import json
import re
import sqlite3

from .prefilter import Prefilter

_W = lambda words: re.compile(r"(?<![a-z])(" + "|".join(words) + r")(?![a-z])", re.I)  # noqa: E731

UP = _W(["surge[sd]?", "soar[sed]*", "jump[sed]*", "rall(?:y|ies|ied)", "rise[sn]?", "rising", "gain[sed]*",
         "climb[sed]*", "beat[s]?", "record high", "upgrade[sd]?", "inflows?", "boost[sed]*", "spike[sd]?",
         "higher", "up \\d"])
DOWN = _W(["fall[sn]?", "fell", "slump[sed]*", "plunge[sd]?", "crash(?:es|ed)?", "drop[sped]*", "decline[sd]?",
           "tumble[sd]?", "slide[sd]?", "sink[s]?", "miss(?:es|ed)?", "downgrade[sd]?", "outflows?",
           "record low", "lower", "down \\d", "eases?", "easing", "cools?", "weak(?:er|ens?)?", "slips?",
           "slipped", "retreats?", "retreated", "dips?", "dipped", "snaps?"])
IGNORE_ASSET = _W(["gold", "silver", "bullion", "bitcoin", "crypto"])
CLAUSE_SPLIT = re.compile(r"[;|:,]| - | as | while | despite | amid | after ", re.I)
BAD_ALWAYS = _W(["war", "attack", "missile", "sanctions?", "fraud", "raid", "probe", "penalty", "default",
                 "recession", "tariff hike", "resigns?", "selling", "rate hikes?",
                 "hikes? (?:the )?(?:repo |interest |policy )?rates?"])
GOOD_ALWAYS = _W(["ceasefire", "peace", "trade deal", "rate cuts?", "cuts? (?:the )?(?:repo |interest |policy )?rates?",
                   "stimulus", "buying", "buyback"])
INVERSE_ASSET = _W(["crude", "oil", "brent", "wti", "dollar", "dxy", "treasury yields?", "bond yields?",
                    "inflation", "cpi", "vix"])
PREOPEN = re.compile(r"gift nifty (?:signals?|indicates?|points?)|pre-?market", re.I)
WRAP = re.compile(r"(live updates?|stock markets? live|highlights|stock markets? today|market (?:wrap|outlook|today)|"
                  r"trade setup|stocks to (?:watch|buy)|ahead of market|closing bell|opening bell|"
                  r"prediction|things that will decide|(?:sensex|nifty)[^.]{0,40}\b(?:closes?|ends?|settles?|opens?)\b)",
                  re.I)

CATEGORY_RULES = [
    ("rbi_monetary", _W(["rbi", "repo rate", "monetary policy", "mpc"])),
    ("sebi_regulatory", _W(["sebi"])),
    ("global_central_bank", _W(["fed", "federal reserve", "fomc", "powell", "ecb", "bank of japan"])),
    ("commodity_crude", _W(["crude", "oil", "brent", "opec", "hormuz"])),
    ("currency", _W(["rupee", "dollar", "usd/inr", "forex"])),
    ("fii_dii_flows", _W(["fii", "fiis", "fpi", "fpis", "dii", "foreign investors"])),
    ("geopolitical", _W(["war", "missile", "ceasefire", "sanctions", "geopolitical"])),
    ("india_macro_data", _W(["gdp", "cpi", "inflation", "iip", "pmi", "fiscal deficit"])),
    ("govt_policy_budget", _W(["budget", "gst", "finance ministry", "tax"])),
    ("corporate_results", _W(["results", "q1", "q2", "q3", "q4", "net profit", "earnings"])),
]


class KeywordClassifier:
    method = "keyword"

    def __init__(self, prefilter: Prefilter):
        self.pf = prefilter

    def classify(self, rows: list[sqlite3.Row]) -> dict[str, dict]:
        return {r["uid"]: self._one(r) for r in rows}

    def _one(self, r: sqlite3.Row) -> dict:
        title, summary = r["title"], r["summary"] or ""
        text = f"{title} {summary}"
        symbols = json.loads(r["symbols"] or "[]")
        score = float(r["prescore"] or 0)

        impact = max(0, min(8, round(score / 2)))   # never 9-10 without AI judgement
        is_wrap = bool(WRAP.search(title))
        if is_wrap:
            impact = min(impact, 3)
        elif PREOPEN.search(title):
            impact = min(impact, 5)               # useful pre-open read, but not an alert

        hits = [k for k, p in self.pf.keywords if p.search(text)]
        category = next((c for c, rx in CATEGORY_RULES if rx.search(text)), "other")
        scope = "stock" if symbols and r["tier"] == "corporate" else "market"

        return {
            "relevant": score > 0,
            "category": "market_report" if is_wrap else category,
            "scope": scope,
            "sectors": [],
            "symbols": symbols,
            "direction": self._direction(title),
            "impact": impact,
            "horizon": "intraday",
            "summary": title[:200],
            "why": ("Market wrap/outlook - capped. " if is_wrap else "")
                   + "Keywords: " + ", ".join(hits[:5]),
            "method": self.method,
        }

    @staticmethod
    def _direction(title: str) -> str:
        """Judge each clause on its own: 'Nasdaq falls as oil slips' = one bearish, one bullish."""
        bull = bear = 0
        for clause in CLAUSE_SPLIT.split(title):
            if not clause.strip() or IGNORE_ASSET.search(clause):
                continue                          # gold/silver moves say little about NIFTY
            up, down = bool(UP.search(clause)), bool(DOWN.search(clause))
            if INVERSE_ASSET.search(clause) and not re.search(r"rupee|sensex|nifty|stocks?|shares?", clause, re.I):
                up, down = down, up               # crude/dollar/yields up = bad for NIFTY
            bull += up or bool(GOOD_ALWAYS.search(clause))
            bear += down or bool(BAD_ALWAYS.search(clause))
        if bull and bear:
            return "mixed" if bull == bear else ("bullish" if bull > bear else "bearish")
        return "bullish" if bull else "bearish" if bear else "neutral"
