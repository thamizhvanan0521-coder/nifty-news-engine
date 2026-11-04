from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class NewsItem:
    source: str
    tier: str              # official | india_media | global | corporate
    title: str
    url: str
    published: datetime    # timezone-aware, UTC
    summary: str = ""
    symbols: list[str] = field(default_factory=list)
    category_hint: str = ""
    uid: str = ""

    def __post_init__(self) -> None:
        if not self.uid:
            key = (self.url or "").strip() or self.title.strip().lower()
            self.uid = hashlib.sha1(key.encode("utf-8")).hexdigest()
