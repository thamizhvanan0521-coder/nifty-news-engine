"""NSE corporate announcements (results, board outcomes, M&A, orders ...)."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import feedparser
import requests

from ..models import NewsItem
from ..timeutil import IST
from ..universe import Universe
from . import clean_text

log = logging.getLogger(__name__)


class NSEAnnouncements:
    HOME = "https://www.nseindia.com/"
    REFERER = "https://www.nseindia.com/companies-listing/corporate-filings-announcements"
    # Without dates NSE returns only the latest 20 filings; with today's date it returns the full day.
    API = "https://www.nseindia.com/api/corporate-announcements?index=equities&from_date={d}&to_date={d}"
    RSS = "https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml"

    def __init__(self, session: requests.Session, universe: Universe,
                 interval: int = 120, timeout: int = 15):
        self.name = "NSE Announcements"
        self.tier = "corporate"
        self.session = session
        self.universe = universe
        self.interval = interval
        self.timeout = timeout
        self._primed_at = 0.0

    # NSE sets anti-bot cookies on the home page; the API refuses requests without them.
    def _prime(self) -> None:
        self.session.get(self.HOME, timeout=self.timeout,
                         headers={"Accept": "text/html,application/xhtml+xml"})
        self._primed_at = time.time()

    def fetch(self) -> list[NewsItem]:
        try:
            items = self._fetch_api()
        except Exception as e:  # noqa: BLE001
            log.warning("NSE API failed (%s); falling back to RSS", e)
            items = self._fetch_rss()
        return [it for it in items if self.universe.contains_any(it.symbols)]

    def _fetch_api(self) -> list[NewsItem]:
        if time.time() - self._primed_at > 600:
            self._prime()
        headers = {"Accept": "application/json, text/plain, */*", "Referer": self.REFERER}
        url = self.API.format(d=datetime.now(IST).strftime("%d-%m-%Y"))
        r = self.session.get(url, timeout=self.timeout, headers=headers)
        if r.status_code in (401, 403):
            self._prime()
            r = self.session.get(url, timeout=self.timeout, headers=headers)
        r.raise_for_status()
        data = r.json()
        rows = data if isinstance(data, list) else data.get("data", [])

        out = []
        for row in rows:
            sym = (row.get("symbol") or "").strip().upper()
            desc = clean_text(row.get("desc"))
            if not sym or not desc:
                continue
            company = clean_text(row.get("sm_name"))
            url = (row.get("attchmntFile") or "").strip() or f"nse://{sym}/{row.get('seq_id', desc)}"
            out.append(NewsItem(
                source=self.name,
                tier=self.tier,
                title=f"{sym} ({company}): {desc}" if company else f"{sym}: {desc}",
                url=url,
                published=_parse_nse_time(row),
                summary=clean_text(row.get("attchmntText"))[:800],
                symbols=[sym],
                category_hint=desc,
            ))
        return out

    def _fetch_rss(self) -> list[NewsItem]:
        r = self.session.get(self.RSS, timeout=self.timeout)
        r.raise_for_status()
        feed = feedparser.parse(r.content)
        out = []
        for e in feed.entries:
            title = clean_text(e.get("title"))
            if not title:
                continue
            sym = self.universe.symbol_for_name(title)
            st = e.get("published_parsed")
            pub = (datetime(*st[:6], tzinfo=timezone.utc) if st else datetime.now(timezone.utc))
            summary = clean_text(e.get("summary") or e.get("description"))
            out.append(NewsItem(
                source=self.name + " (RSS)",
                tier=self.tier,
                title=f"{sym or title}: {summary[:160]}" if summary else title,
                url=(e.get("link") or "").strip(),
                published=pub,
                summary=summary[:800],
                symbols=[sym] if sym else [],
            ))
        return out


def _parse_nse_time(row: dict) -> datetime:
    for key, fmt in (("sort_date", "%Y-%m-%d %H:%M:%S"), ("an_dt", "%d-%b-%Y %H:%M:%S")):
        v = (row.get(key) or "").strip()
        if v:
            try:
                return datetime.strptime(v, fmt).replace(tzinfo=IST).astimezone(timezone.utc)
            except ValueError:
                pass
    return datetime.now(timezone.utc)
