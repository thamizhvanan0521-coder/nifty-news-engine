"""Claude-based impact scoring. Uses a forced tool call so the output is always valid JSON."""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime

from .timeutil import fmt_ist, now_ist

log = logging.getLogger(__name__)

CATEGORIES = [
    "rbi_monetary", "india_macro_data", "govt_policy_budget", "sebi_regulatory",
    "global_central_bank", "global_macro_data", "global_markets", "commodity_crude",
    "currency", "geopolitical", "fii_dii_flows", "corporate_results", "corporate_action",
    "corporate_other", "sector_news", "market_report", "other",
]

TOOL = {
    "name": "record_classifications",
    "description": "Record the Indian-market impact classification for every news item in the batch.",
    "input_schema": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "relevant": {"type": "boolean",
                                     "description": "Could this move Indian equities at all?"},
                        "category": {"type": "string", "enum": CATEGORIES},
                        "scope": {"type": "string", "enum": ["market", "sector", "stock", "none"]},
                        "sectors": {"type": "array", "items": {"type": "string"}},
                        "symbols": {"type": "array", "items": {"type": "string"},
                                    "description": "NSE trading symbols, uppercase, only if certain"},
                        "direction": {"type": "string",
                                      "enum": ["bullish", "bearish", "neutral", "mixed"]},
                        "impact": {"type": "integer", "minimum": 0, "maximum": 10,
                                   "description": "Expected impact on NIFTY 50 (see scale)"},
                        "horizon": {"type": "string",
                                    "enum": ["intraday", "1-3 days", "weeks", "long-term"]},
                        "summary": {"type": "string", "description": "One plain sentence, max 30 words"},
                        "why": {"type": "string", "description": "Why it matters for NIFTY, max 25 words"},
                    },
                    "required": ["id", "relevant", "category", "scope", "sectors", "symbols",
                                 "direction", "impact", "horizon", "summary", "why"],
                },
            }
        },
        "required": ["items"],
    },
}

SYSTEM = """You are a senior Indian equity-market news analyst working for a NIFTY 50 intraday
options trader. For each news item, judge how much it can move the NIFTY 50 index.

IMPACT SCALE (effect on NIFTY 50, not on a single stock):
10-9  Rare shock: surprise RBI rate move, Union Budget tax shock, war outbreak involving
      India or a major oil route, SEBI structural F&O change effective soon, circuit-breaker crash.
8-7   Big market mover: Fed decision/surprise, crude move >3%, US-India tariff action,
      results/major news of a top-10 NIFTY weight (HDFCBANK, RELIANCE, ICICIBANK, INFY, BHARTIARTL,
      TCS, LT, ITC, SBIN, AXISBANK, KOTAKBANK) with a clear beat/miss, record FII selling/buying day,
      India CPI/GDP well away from expectations.
6-5   Noticeable: in-line macro data, results of other NIFTY 50 stocks, sector-wide policy
      (PLI, duty changes), rupee at record low, major global index sell-off/rally, SEBI circulars
      on trading rules.
4-3   Minor: non-NIFTY stock news, routine corporate filings, analyst views, small regulatory notes.
2-0   Irrelevant to Indian equities (lifestyle, sports, local crime, generic tech).

RULES
- Direction: for scope=market or sector, direction is for NIFTY/that sector; for scope=stock,
  direction is for that stock.
- Market wrap / "Sensex closes higher" type reports describe the past: impact <= 3.
- If an item is old (published 12h+ before now) or clearly already known, cut impact by 1-3.
- Opinion/prediction pieces with no new fact: impact <= 3.
- Be conservative: most items are 0-4. Reserve 7+ for things a trader must act on.
- symbols: only NSE symbols you are sure of; empty list otherwise.
- Return exactly one entry per input id, via the record_classifications tool."""


class Classifier:
    def __init__(self, cfg: dict):
        from anthropic import Anthropic  # imported here so offline commands work without a key
        self.client = Anthropic()
        self.model = cfg.get("classify_model", "claude-haiku-5-5")
        self.max_tokens = int(cfg.get("max_tokens", 4096))

    def classify(self, rows: list[sqlite3.Row]) -> dict[str, dict]:
        """Returns {uid: classification}. Raises on API error (caller retries next cycle)."""
        id_map = {f"n{i}": r["uid"] for i, r in enumerate(rows, 1)}
        lines = [f"Now: {now_ist().strftime('%a %d-%b-%Y %H:%M IST')}", "", "ITEMS:"]
        for sid, r in zip(id_map, rows):
            pub =fmt_ist(datetime.fromisoformat(r["published_utc"]))
            syms = ", ".join(json.loads(r["symbols"] or "[]"))
            lines.append(f"[{sid}] source={r['source']} ({r['tier']}) | published={pub}"
                         + (f" | symbols={syms}" if syms else ""))
            lines.append(f"TITLE: {r['title']}")
            if r["summary"]:
                lines.append(f"TEXT: {r['summary'][:500]}")
            lines.append("")

        resp = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=SYSTEM,
            tools=[TOOL],
            tool_choice={"type": "tool", "name": TOOL["name"]},
            messages=[{"role": "user", "content": "\n".join(lines)}],
        )
        data = next((b.input for b in resp.content if getattr(b, "type", "") == "tool_use"), None)
        if not data:
            raise RuntimeError(f"no tool_use block in response (stop_reason={resp.stop_reason})")

        out: dict[str, dict] = {}
        for item in data.get("items", []):
            uid = id_map.get(str(item.get("id")))
            if uid:
                item["impact"] = max(0, min(10, int(item.get("impact", 0))))
                item["symbols"] = [s.upper().strip() for s in item.get("symbols", []) if s]
                out[uid] = item
        missing = set(id_map.values()) - set(out)
        if missing:
            log.warning("classifier skipped %d of %d items", len(missing), len(rows))
        return out
