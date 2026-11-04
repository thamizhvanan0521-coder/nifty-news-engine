from __future__ import annotations

import calendar
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

import feedparser
import requests

from ..models import NewsItem
from . import clean_text

IST_OFFSET = timedelta(hours=5, minutes=30)


class RSSSource:
    """Any RSS/Atom feed (RBI, SEBI, ET, Moneycontrol, Mint, CNBC ...)."""

    def __init__(self, name: str, url: str, tier: str, session: requests.Session,
                 interval: int = 120, timeout: int = 15):
        self.name = name
        self.url = url
        self.tier = tier
        self.session = session
        self.interval = interval
        self.timeout = timeout

    def fetch(self) -> list[NewsItem]:
        r = self.session.get(self.url, timeout=self.timeout)
        r.raise_for_status()
        feed = feedparser.parse(r.content)
        if feed.bozo and not feed.entries:
            raise RuntimeError(f"unparseable feed: {feed.get('bozo_exception')}")

        now = datetime.now(timezone.utc)
        out: list[NewsItem] = []
        for e in feed.entries:
            title = clean_text(e.get("title"))
            if not title:
                continue
            out.append(NewsItem(
                source=self.name,
                tier=self.tier,
                title=title,
                url=(e.get("link") or "").strip(),
                published=_published(e, now),
                summary=clean_text(e.get("summary") or e.get("description"))[:800],
            ))
        return out


def _published(entry, now: datetime) -> datetime:
    st = entry.get("published_parsed") or entry.get("updated_parsed")
    if not st:
        return now
    pub = datetime.fromtimestamp(calendar.timegm(st), tz=timezone.utc)
    # Some Indian feeds publish IST times with no offset; feedparser then reads
    # them as UTC and they land 5h30m in the future. Pull those back.
    if pub > now + timedelta(minutes=15):
        pub -= IST_OFFSET
        if pub > now + timedelta(minutes=15):
            pub = now
    return pub


def google_news_source(query: str, tier: str, session: requests.Session,
                       interval: int = 600) -> RSSSource:
    """Google News RSS search, restricted to the last day, India edition."""
    q = quote_plus(f"{query} when:1d")
    url = f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
    return RSSSource(f"GNews: {query}", url, tier, session, interval=interval)
