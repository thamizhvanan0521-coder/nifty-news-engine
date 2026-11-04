"""Cheap keyword scoring so only market-relevant items reach Claude (saves API cost)."""
from __future__ import annotations

import re

from .models import NewsItem
from .universe import Universe

DEFAULT_KEYWORDS: dict[str, float] = {
    # RBI / Indian macro
    "repo rate": 4, "rbi": 3, "monetary policy": 4, "mpc": 3, "crr": 2, "inflation": 3,
    "cpi": 3, "wpi": 2, "gdp": 3, "iip": 2, "fiscal deficit": 3, "union budget": 4,
    "budget": 2, "gst": 2, "pmi": 2, "trade deficit": 2, "forex reserves": 2,
    "finance ministry": 2, "nirmala sitharaman": 2, "tax": 1, "disinvestment": 2,
    # Global
    "federal reserve": 4, "fomc": 4, "powell": 3, "us fed": 3, "fed rate": 3,
    "treasury yield": 3, "bond yield": 2, "us cpi": 3, "nonfarm": 3, "payrolls": 3,
    "crude": 3, "brent": 3, "opec": 3, "oil prices": 3, "dollar index": 2, "dxy": 2,
    "tariff": 3, "tariffs": 3, "recession": 3, "wall street": 2, "nasdaq": 2,
    "dow jones": 2, "s&p 500": 2, "china": 1, "ecb": 2, "bank of japan": 2, "yen": 1,
    "war": 2, "ceasefire": 2, "sanctions": 2, "missile": 2, "geopolitical": 2,
    "gold prices": 2, "gift nifty": 3, "oil": 2, "hormuz": 3, "red sea": 2, "lng": 1,
    "dollar": 1, "yields": 2, "election": 1, "midterm": 2, "trump": 1, "rate cut": 3,
    "rate hike": 3, "stimulus": 2, "asian markets": 2, "nikkei": 1, "hang seng": 1,
    # Flows / market structure
    "fii": 3, "fiis": 3, "fpi": 3, "fpis": 3, "dii": 2, "foreign investors": 3,
    "outflow": 2, "outflows": 2, "inflow": 2, "inflows": 2,
    "sensex": 2, "nifty": 2, "bank nifty": 2, "stock market": 1, "market crash": 4,
    "sebi": 3, "f&o": 3, "derivatives": 2, "expiry": 2, "lot size": 3, "stt": 3,
    "india vix": 2, "circuit": 2, "rupee": 2, "usd/inr": 2,
    # Corporate
    "results": 2, "q1": 2, "q2": 2, "q3": 2, "q4": 2, "net profit": 2, "earnings": 2,
    "guidance": 2, "acquisition": 2, "acquire": 2, "merger": 2, "amalgamation": 2,
    "demerger": 2, "stake": 1, "block deal": 2, "bulk deal": 1, "buyback": 2,
    "dividend": 1, "bonus": 1, "stock split": 1, "order win": 2, "bags order": 2,
    "contract": 1, "downgrade": 2, "upgrade": 2, "credit rating": 2, "fraud": 3,
    "raid": 3, "probe": 2, "penalty": 2, "resigns": 2, "resignation": 2, "ceo": 1,
    "qip": 2, "ipo": 1, "outcome of board meeting": 2, "financial results": 3,
}

DEFAULT_NOISE = [
    "trading window", "loss of share certificate", "duplicate share", "regulation 74(5)",
    "copy of newspaper publication", "newspaper advertisement", "change in registrar",
    "certificate under sebi (depositories", "esop", "esos", "allotment of equity shares under",
    "closure of trading window", "updates on postal ballot", "record date for interest",
    "intimation of loss", "reg. 40", "share transfer", "investor grievance",
    "horoscope", "recipe", "cricket", "bollywood", "box office",
    # RBI/SEBI routine notices that hit keywords but never move NIFTY
    "implementation of section 51a of uapa", "sanctions list", "consumer confidence survey",
    "inflation expectations survey", "money market operations", "exemption order in the matter",
    "settlement order in the matter", "adjudication order in the matter",
]

TIER_BONUS = {"official": 2.0, "global": 0.0, "india_media": 0.0, "corporate": 0.0}


def _pattern(phrase: str) -> re.Pattern:
    return re.compile(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", re.I)


class Prefilter:
    def __init__(self, cfg: dict, universe: Universe, heavyweights: set[str]):
        kw = {**DEFAULT_KEYWORDS, **(cfg.get("extra_keywords") or {})}
        self.patterns = [(_pattern(k), float(w)) for k, w in kw.items()]
        noise = DEFAULT_NOISE + list(cfg.get("extra_noise") or [])
        self.noise = [_pattern(n) for n in noise]
        self.universe = universe
        self.heavy = heavyweights

    def score(self, it: NewsItem) -> float:
        text = f"{it.title} {it.summary}"
        if any(p.search(text) for p in self.noise):
            return -10.0
        s = sum(w for p, w in self.patterns if p.search(text))
        s += TIER_BONUS.get(it.tier, 0.0)
        for sym in it.symbols:
            if sym in self.heavy:
                s += 4
            elif sym in self.universe.rows:
                s += 2
        return s
