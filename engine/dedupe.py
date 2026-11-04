"""Near-duplicate detection: the same story from ET, Mint, MC and Google News is one alert."""
from __future__ import annotations

import re
from difflib import SequenceMatcher

_NON_ALNUM = re.compile(r"[^a-z0-9. ]+")
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3})")
_NUM = re.compile(r"\d+(?:\.\d+)?")
_STOP = {"the", "and", "for", "with", "from", "after", "amid", "over", "says", "into", "its"}


def normalize(title: str) -> str:
    t = title.strip()
    if " - " in t:                    # Google News appends " - Publisher"
        head, tail = t.rsplit(" - ", 1)
        if len(tail) <= 40:
            t = head
    t = _THOUSANDS.sub("", t.lower())          # 1,225 -> 1225
    t = _NON_ALNUM.sub(" ", t)
    t = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", t)   # keep decimal points only
    return " ".join(t.split())


def _tokens(n: str) -> frozenset[str]:
    return frozenset(w for w in n.split() if len(w) > 2 and w not in _STOP)


def _numbers(n: str) -> frozenset[str]:
    return frozenset(_NUM.findall(n))


class DupIndex:
    def __init__(self, recent: list[tuple[str, str]], ratio: float = 0.86, jaccard: float = 0.7):
        self.ratio = ratio
        self.jaccard = jaccard
        self.entries: list[tuple[str, str, frozenset[str], frozenset[str]]] = []
        for uid, title in recent:
            self.add(uid, title)

    def add(self, uid: str, title: str) -> None:
        n = normalize(title)
        self.entries.append((uid, n, _tokens(n), _numbers(n)))

    def find(self, title: str) -> str | None:
        n = normalize(title)
        tn, nn = _tokens(n), _numbers(n)
        for uid, other, to, no in self.entries:
            if n == other:
                return uid
            if nn and no and nn != no:     # different dates/figures = different story
                continue
            if tn and to and len(tn & to) / len(tn | to) >= self.jaccard:
                return uid
            sm = SequenceMatcher(None, n, other)
            if sm.real_quick_ratio() >= self.ratio and sm.quick_ratio() >= self.ratio \
                    and sm.ratio() >= self.ratio:
                return uid
        return None
