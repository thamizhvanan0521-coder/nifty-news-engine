"""Index constituents (NIFTY 50 / 100 / 500) used to filter corporate filings."""
from __future__ import annotations

import csv
import io
import logging
import re
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)

_FILES = {
    "nifty50": "ind_nifty50list.csv",
    "nifty100": "ind_nifty100list.csv",
    "nifty200": "ind_nifty200list.csv",
    "nifty500": "ind_nifty500list.csv",
}
_BASES = [
    "https://www.niftyindices.com/IndexConstituent/",
    "https://archives.nseindia.com/content/indices/",
]
_SUFFIX = re.compile(r"\b(limited|ltd|india|the|corporation|corp|company|co)\b\.?")


def _norm_name(name: str) -> str:
    n = re.sub(r"[^a-z0-9 ]+", " ", name.lower())
    return " ".join(_SUFFIX.sub(" ", n).split())


class Universe:
    def __init__(self, rows: dict[str, dict], allow_all: bool = False):
        self.rows = rows                      # SYMBOL -> {"name", "industry"}
        self.allow_all = allow_all
        self._names = {_norm_name(v["name"]): k for k, v in rows.items() if v.get("name")}

    @property
    def symbols(self) -> set[str]:
        return set(self.rows)

    def contains_any(self, symbols: list[str]) -> bool:
        return self.allow_all or any(s in self.rows for s in symbols)

    def industry(self, symbol: str) -> str:
        return self.rows.get(symbol, {}).get("industry", "")

    def symbol_for_name(self, company: str) -> str | None:
        n = _norm_name(company)
        if not n:
            return None
        if n in self._names:
            return self._names[n]
        for name, sym in self._names.items():
            if name and (name in n or n in name):
                return sym
        return None


def _parse_csv(text: str) -> dict[str, dict]:
    rows = {}
    for r in csv.DictReader(io.StringIO(text)):
        r = {k.strip(): (v or "").strip() for k, v in r.items() if k}
        sym = r.get("Symbol", "").upper()
        if sym:
            rows[sym] = {"name": r.get("Company Name", ""), "industry": r.get("Industry", "")}
    return rows


def load_universe(name: str, data_dir: Path, fallback: list[str],
                  session: requests.Session, max_age_days: int = 7) -> Universe:
    name = name.lower()
    if name == "all":
        return Universe({s: {"name": "", "industry": ""} for s in fallback}, allow_all=True)
    fname = _FILES.get(name)
    if not fname:
        raise ValueError(f"unknown universe '{name}', use one of {list(_FILES)} or 'all'")

    cache = data_dir / fname
    fresh = cache.exists() and (time.time() - cache.stat().st_mtime) < max_age_days * 86400
    if not fresh:
        for base in _BASES:
            try:
                r = session.get(base + fname, timeout=20)
                r.raise_for_status()
                rows = _parse_csv(r.text)
                if len(rows) >= 40:
                    cache.write_text(r.text, encoding="utf-8")
                    log.info("universe %s refreshed from %s (%d symbols)", name, base, len(rows))
                    return Universe(rows)
            except Exception as e:  # noqa: BLE001
                log.warning("universe download failed from %s: %s", base, e)

    if cache.exists():
        rows = _parse_csv(cache.read_text(encoding="utf-8"))
        log.info("universe %s loaded from cache (%d symbols)", name, len(rows))
        return Universe(rows)

    log.warning("universe %s unavailable; using %d fallback symbols", name, len(fallback))
    return Universe({s: {"name": "", "industry": ""} for s in fallback})
