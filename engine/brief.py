"""Pre-market brief: everything material since the last 15:30 close, sent once before 09:15."""
from __future__ import annotations

import html
import json
import logging
import os
from datetime import datetime

from .alerts import Alerter
from .store import Store
from .timeutil import MARKET_OPEN, fmt_ist, is_trading_day, last_session_close, now_ist, parse_hhmm

log = logging.getLogger(__name__)

BRIEF_SYSTEM = """You write the pre-market news brief for a NIFTY 50 intraday options buyer.
You get pre-scored news items (impact 0-10 on NIFTY) collected since the last market close.
Write in plain text (no markdown symbols like ** or #), short lines, Indian market terms.

Sections, in this order, with the header in CAPS:
NEWS TONE: one line - Positive / Negative / Mixed / Quiet for NIFTY at open, with a one-line reason.
  This is the tone of the news only; you have no price or GIFT Nifty data, say so if relevant.
GLOBAL CUES: 2-5 bullets (US/Fed, crude, dollar, Asia, geopolitics).
INDIA MACRO & POLICY: RBI, govt, SEBI, data releases. Skip if nothing.
STOCKS IN FOCUS: one line each - SYMBOL: what happened -> likely bias. NIFTY heavyweights first.
WATCH TODAY: scheduled events or risks during market hours, if any are mentioned.
Keep it under 350 words. Do not invent facts that are not in the items."""


def _has_key() -> bool:
    k = os.getenv("ANTHROPIC_API_KEY", "").strip()
    return k.startswith("sk-ant-") and len(k) > 30


def is_due(store: Store, cfg: dict, holidays: set[str]) -> bool:
    now = now_ist()
    if not cfg.get("enabled", True) or not is_trading_day(now.date(), holidays):
        return False
    if store.get_meta("last_brief_date") == now.date().isoformat():
        return False
    return parse_hhmm(cfg.get("time", "08:45")) <= now.time() < MARKET_OPEN


def build_and_send(store: Store, cfg: dict, alerter: Alerter, holidays: set[str],
                   model: str) -> str:
    now = now_ist()
    since = last_session_close(now, holidays)
    rows = store.classified_since(since, int(cfg.get("min_impact", 4)), limit=60)

    if not rows:
        body = f"Quiet: no material news scored since {fmt_ist(since)}."
        text = f"☀️ <b>PRE-MARKET BRIEF · {now.strftime('%a %d-%b')}</b>\n\n{body}"
    elif not _has_key():
        # Free mode: no AI summary, just the top scored headlines since the close.
        lines = [f"{'▲' if json.loads(r['classification'])['direction'] == 'bullish' else '▼' if json.loads(r['classification'])['direction'] == 'bearish' else '•'} "
                 f"{r['impact']}/10  {r['title']}" for r in rows[:12]]
        body = "Keyword-scored headlines (no AI):\n" + "\n".join(lines)
        text = (f"☀️ <b>PRE-MARKET BRIEF · {now.strftime('%a %d-%b')}</b>\n"
                f"<i>{len(rows)} items since {fmt_ist(since)}</i>\n\n{html.escape(body)}")
    else:
        items = []
        for r in rows:
            c = json.loads(r["classification"])
            items.append(f"- [{c['impact']}/10 {c['direction']} {c['scope']}] {r['title']} | "
                         f"{c.get('summary', '')} | tags: {', '.join(c['sectors'] + c['symbols'])} | "
                         f"{fmt_ist(datetime.fromisoformat(r['published_utc']))}")
        from anthropic import Anthropic
        resp = Anthropic().messages.create(
            model=model, max_tokens=1500, system=BRIEF_SYSTEM,
            messages=[{"role": "user", "content":
                       f"Today: {now.strftime('%A %d-%b-%Y')}. Items since {fmt_ist(since)}:\n"
                       + "\n".join(items)}])
        body = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        text = (f"☀️ <b>PRE-MARKET BRIEF · {now.strftime('%a %d-%b')}</b>\n"
                f"<i>{len(rows)} items since {fmt_ist(since)}</i>\n\n{html.escape(body)}")

    if cfg.get("telegram", True):
        alerter.telegram(text)
    if cfg.get("desktop", True):
        alerter.desktop("☀️ Pre-market brief ready", f"{len(rows)} items scored since last close")
    store.set_meta("last_brief_date", now.date().isoformat())
    store.set_meta("last_brief_text", body)          # plain text, shown on the web page
    store.set_meta("last_brief_items", str(len(rows)))
    log.info("pre-market brief sent (%d items)", len(rows))
    return text
