from __future__ import annotations

import html
import re

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

_TAG = re.compile(r"<[^>]+>")


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept-Language": "en-IN,en;q=0.9",
        "Accept": "*/*",
    })
    return s


def clean_text(s: str | None) -> str:
    """Strip HTML tags/entities and collapse whitespace."""
    return " ".join(html.unescape(_TAG.sub(" ", s or "")).split())
