"""BSE corporate announcements. Off by default: NSE already covers every NIFTY/SENSEX
constituent, so BSE mostly adds duplicates. Turn on in config if you want it."""
from __future__ import annotations

from datetime import datetime, timezone

import requests

from ..models import NewsItem
from ..timeutil import IST, now_ist
from ..universe import Universe
from . import clean_text


class BSEAnnouncements:
    API = ("https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
           "?pageno=1&strCat=-1&strPrevDate={d}&strScrip=&strSearch=P&strToDate={d}"
           "&strType=C&subcategory=-1")
    ATTACH = "https://www.bseindia.com/xml-data/corpfiling/AttachLive/"

    def __init__(self, session: requests.Session, universe: Universe,
                 interval: int = 180, timeout: int = 15):
        self.name = "BSE Announcements"
        self.tier = "corporate"
        self.session = session
        self.universe = universe
        self.interval = interval
        self.timeout = timeout

    def fetch(self) -> list[NewsItem]:
        d = now_ist().strftime("%Y%m%d")
        r = self.session.get(self.API.format(d=d), timeout=self.timeout, headers={
            "Accept": "application/json", "Referer": "https://www.bseindia.com/",
            "Origin": "https://www.bseindia.com"})
        r.raise_for_status()
        out = []
        for row in r.json().get("Table", []):
            company = clean_text(row.get("SLONGNAME"))
            sym = self.universe.symbol_for_name(company)
            if not sym and not self.universe.allow_all:
                continue
            headline = clean_text(row.get("NEWSSUB") or row.get("HEADLINE"))
            att = (row.get("ATTACHMENTNAME") or "").strip()
            out.append(NewsItem(
                source=self.name,
                tier=self.tier,
                title=f"{sym or company}: {headline}",
                url=self.ATTACH + att if att else f"bse://{row.get('NEWSID')}",
                published=_parse_bse_time(row.get("DT_TM") or row.get("NEWS_DT")),
                summary=clean_text(row.get("HEADLINE"))[:800],
                symbols=[sym] if sym else [],
                category_hint=clean_text(row.get("CATEGORYNAME")),
            ))
        return out


def _parse_bse_time(v: str | None) -> datetime:
    v = (v or "").strip().split(".")[0]
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(v, fmt).replace(tzinfo=IST).astimezone(timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)
