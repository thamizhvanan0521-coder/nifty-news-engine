"""Telegram + desktop popup + sound alerts."""
from __future__ import annotations

import html
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone

import requests

from .timeutil import fmt_ist, in_window, now_ist, parse_hhmm

log = logging.getLogger(__name__)

DIR_ICON = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪", "mixed": "🟡"}


class Alerter:
    def __init__(self, cfg: dict, heavyweights: set[str]):
        self.cfg = cfg
        self.heavy = heavyweights
        self.min_impact = int(cfg.get("min_impact", 7))
        self.heavy_min = int(cfg.get("heavyweight_min_impact", 6))
        self.max_age_min = int(cfg.get("max_age_minutes", 90))
        q = cfg.get("quiet_hours") or ["23:00", "06:00"]
        self.quiet = (parse_hhmm(q[0]), parse_hhmm(q[1]))
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        self.session = requests.Session()
        if cfg.get("telegram", True) and not (self.token and self.chat_id):
            log.warning("Telegram enabled but TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set in .env")

    # ---------- decision ----------
    def should_alert(self, row: sqlite3.Row, cls: dict) -> bool:
        if not cls.get("relevant"):
            return False
        published = datetime.fromisoformat(row["published_utc"])
        age_min = (datetime.now(timezone.utc) - published).total_seconds() / 60
        if age_min > self.max_age_min:          # old news: store it, don't ping
            return False
        imp = int(cls.get("impact", 0))
        if imp >= self.min_impact:
            return True
        syms = set(cls.get("symbols", [])) | set(json.loads(row["symbols"] or "[]"))
        return imp >= self.heavy_min and bool(syms & self.heavy)

    def is_quiet(self) -> bool:
        return in_window(now_ist().time(), *self.quiet)

    # ---------- send ----------
    def send(self, row: sqlite3.Row, cls: dict) -> None:
        quiet = self.is_quiet()
        if self.cfg.get("telegram", True):
            self.telegram(format_telegram(row, cls), silent=quiet)
        if not quiet:
            imp = int(cls.get("impact", 0))
            icon = DIR_ICON.get(cls.get("direction", ""), "")
            if self.cfg.get("desktop", True):
                self.desktop(f"{icon} {imp}/10 {cls.get('direction', '').upper()}",
                             f"{row['title']}\n{cls.get('why', '')}")
            if self.cfg.get("sound", True):
                self.sound(imp)

    def telegram(self, text: str, silent: bool = False) -> bool:
        if not (self.token and self.chat_id):
            return False
        ok = True
        for chunk in _split(text, 4000):
            try:
                r = self.session.post(
                    f"https://api.telegram.org/bot{self.token}/sendMessage",
                    json={"chat_id": self.chat_id, "text": chunk, "parse_mode": "HTML",
                          "disable_web_page_preview": True, "disable_notification": silent},
                    timeout=15)
                if not r.ok:
                    log.error("telegram error %s: %s", r.status_code, r.text[:200])
                    ok = False
            except requests.RequestException as e:
                log.error("telegram send failed: %s", e)
                ok = False
        return ok

    @staticmethod
    def desktop(title: str, message: str) -> None:
        try:
            from plyer import notification
            notification.notify(title=title[:63], message=message[:250],
                                app_name="News Engine", timeout=12)
        except Exception as e:  # noqa: BLE001
            log.debug("desktop notification unavailable: %s", e)

    @staticmethod
    def sound(impact: int) -> None:
        try:
            if sys.platform == "win32":
                import winsound
                beeps = 3 if impact >= 9 else 2 if impact >= 7 else 1
                for _ in range(beeps):
                    winsound.Beep(1200 if impact >= 8 else 900, 250)
            else:
                print("\a", end="", flush=True)
        except Exception as e:  # noqa: BLE001
            log.debug("sound unavailable: %s", e)


def format_telegram(row: sqlite3.Row, cls: dict) -> str:
    e = html.escape
    imp = int(cls.get("impact", 0))
    direction = cls.get("direction", "neutral")
    level = "HIGH IMPACT" if imp >= 7 else "WATCH"
    bar = "█" * imp + "░" * (10 - imp)
    scope = cls.get("scope", "")
    tags = ", ".join(cls.get("sectors", []) + cls.get("symbols", []))
    published = datetime.fromisoformat(row["published_utc"])
    target = "stock" if scope == "stock" else "NIFTY"
    lines = [
        f"{DIR_ICON.get(direction, '')} <b>{level} {imp}/10</b> · {e(direction.upper())} for {target}",
        f"<code>{bar}</code>",
        "",
        f"<b>{e(row['title'])}</b>",
        "",
        e(cls.get("summary", "")),
        f"<i>Why:</i> {e(cls.get('why', ''))}",
    ]
    if tags:
        lines.append(f"<i>Tags:</i> {e(tags)} · {e(cls.get('horizon', ''))}")
    lines.append(f"{e(row['source'])} · {fmt_ist(published)}")
    url = row["url"] or ""
    if url.startswith("http"):
        lines.append(f'<a href="{e(url, quote=True)}">Open source</a>')
    return "\n".join(lines)


def _split(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks, cur = [], ""
    for para in text.split("\n"):
        if len(cur) + len(para) + 1 > limit and cur:
            chunks.append(cur)
            cur = ""
        while len(para) > limit:
            chunks.append(para[:limit])
            para = para[limit:]
        cur = f"{cur}\n{para}" if cur else para
    if cur:
        chunks.append(cur)
    return chunks
