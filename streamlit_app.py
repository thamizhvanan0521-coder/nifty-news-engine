"""NIFTY News Engine — phone-friendly view of the scored news feed.

Reads feed.json that the GitHub job publishes to the repo's `data` branch.
Set FEED_URL in Streamlit secrets, e.g.
FEED_URL = "https://raw.githubusercontent.com/<you>/<repo>/data/feed.json"
"""
from __future__ import annotations

import html
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import streamlit as st

IST = ZoneInfo("Asia/Kolkata")
DIR = {
    "bullish": ("▲", "#16a34a"),
    "bearish": ("▼", "#dc2626"),
    "mixed": ("◆", "#d97706"),
    "neutral": ("●", "#64748b"),
}

st.set_page_config(page_title="NIFTY News Engine", page_icon="📰", layout="centered")

st.markdown("""
<style>
.block-container {padding-top: 1.2rem; padding-bottom: 3rem; max-width: 760px;}
.ne-card {border: 1px solid rgba(128,128,128,.25); border-left-width: 5px; border-radius: 10px;
          padding: .7rem .85rem; margin: 0 0 .65rem 0;}
.ne-top {display: flex; gap: .5rem; align-items: center; flex-wrap: wrap; font-size: .78rem;
         opacity: .85; margin-bottom: .3rem;}
.ne-pill {color: #fff; border-radius: 999px; padding: .08rem .55rem; font-weight: 700; font-size: .78rem;}
.ne-title {font-weight: 650; font-size: 1rem; line-height: 1.35; margin: .1rem 0 .3rem 0;}
.ne-title a {color: inherit; text-decoration: none;}
.ne-title a:hover {text-decoration: underline;}
.ne-sum {font-size: .9rem; line-height: 1.4; margin: 0 0 .25rem 0;}
.ne-why {font-size: .85rem; opacity: .8; margin: 0;}
.ne-tags {font-size: .75rem; opacity: .7; margin-top: .35rem;}
.ne-status {font-size: .85rem; opacity: .8; margin: -.4rem 0 .6rem 0;}
</style>
""", unsafe_allow_html=True)


def feed_source() -> str:
    try:
        url = st.secrets.get("FEED_URL", "")
    except Exception:  # noqa: BLE001  (no secrets file locally)
        url = ""
    return url or os.getenv("FEED_URL", "")


@st.cache_data(ttl=120, show_spinner=False)
def load_feed(src: str, bust: int) -> dict:
    if src.startswith("http"):
        r = requests.get(src, params={"t": bust}, timeout=15)
        r.raise_for_status()
        return r.json()
    return json.loads(Path(src).read_text(encoding="utf-8"))


def ago(dt: datetime) -> str:
    mins = int((datetime.now(timezone.utc) - dt).total_seconds() // 60)
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{mins} min ago"
    if mins < 48 * 60:
        return f"{mins // 60}h {mins % 60:02d}m ago"
    return f"{mins // 1440} days ago"


def card(it: dict) -> str:
    e = html.escape
    icon, color = DIR.get(it.get("direction") or "neutral", DIR["neutral"])
    imp = int(it.get("impact") or 0)
    pub = datetime.fromisoformat(it["published_utc"])
    target = "stock" if it.get("scope") == "stock" else "NIFTY"
    title = e(it["title"])
    url = it.get("url") or ""
    if url.startswith("http"):
        title = f'<a href="{e(url, quote=True)}" target="_blank">{title}</a>'
    tags = ", ".join((it.get("sectors") or []) + (it.get("symbols") or []))
    bell = " · 🔔 alerted" if it.get("alerted") else ""
    if it.get("method") == "keyword":
        bell += " · ⚙️ keyword score"
    return f"""
<div class="ne-card" style="border-left-color:{color}">
  <div class="ne-top">
    <span class="ne-pill" style="background:{color}">{icon} {imp}/10</span>
    <span>{e((it.get('direction') or '').upper())} for {target}</span>
    <span>· {pub.astimezone(IST).strftime('%d-%b %H:%M')} IST ({ago(pub)})</span>
  </div>
  <div class="ne-title">{title}</div>
  <p class="ne-sum">{e(it.get('summary') or '')}</p>
  <p class="ne-why"><b>Why:</b> {e(it.get('why') or '')}</p>
  <div class="ne-tags">{e(tags) + ' · ' if tags else ''}{e(it.get('horizon') or '')} · {e(it['source'])}{bell}</div>
</div>"""


# ------------------------------------------------------------------ page
st.markdown("### 📰 NIFTY News Engine")

src = feed_source()
if not src:
    st.error("FEED_URL is not set. In Streamlit Cloud: app → Settings → Secrets, add\n\n"
             'FEED_URL = "https://raw.githubusercontent.com/<you>/<repo>/data/feed.json"')
    st.stop()

if "bust" not in st.session_state:
    st.session_state.bust = 0

try:
    feed = load_feed(src, st.session_state.bust)
except Exception as ex:  # noqa: BLE001
    st.error(f"Could not load the feed yet ({type(ex).__name__}). If you just set this up, "
             "run the GitHub workflow once (Actions → news-engine → Run workflow).")
    st.stop()

gen = datetime.fromisoformat(feed["generated_utc"])
stats = feed.get("stats", {})
st.markdown(f'<p class="ne-status">Updated {ago(gen)} · {stats.get("high_24h", 0)} high-impact '
            f'and {stats.get("items_24h", 0)} scored in 24h</p>', unsafe_allow_html=True)
if datetime.now(timezone.utc) - gen > timedelta(minutes=75):
    st.warning("No update for over an hour — check the GitHub Actions tab (the job may be paused or failing).")

# Pre-market brief
b = feed.get("brief") or {}
if b.get("text"):
    now_ist = datetime.now(IST)
    fresh = b.get("date") == now_ist.date().isoformat()
    label = datetime.fromisoformat(b["date"]).strftime("%a %d-%b") if b.get("date") else ""
    with st.expander(f"☀️ Pre-market brief · {label}" + ("" if fresh else " (old)"),
                     expanded=fresh and now_ist.hour < 16):
        st.text(b["text"])

# Filters
c1, c2 = st.columns([3, 2])
level = c1.radio("Show", ["High 7+", "Watch 5+", "All"], horizontal=True, index=1,
                 label_visibility="collapsed")
hours = c2.selectbox("Period", [6, 12, 24, 72], index=2, format_func=lambda h: f"Last {h}h",
                     label_visibility="collapsed")
min_imp = {"High 7+": 7, "Watch 5+": 5, "All": 0}[level]

with st.expander("More filters"):
    dirs = st.multiselect("Direction", list(DIR), default=list(DIR))
    q = st.text_input("Search", placeholder="e.g. RBI, crude, HDFCBANK").strip().lower()
    sort_by = st.radio("Sort", ["Newest", "Impact"], horizontal=True)

if st.button("↻ Refresh"):
    st.session_state.bust += 1
    load_feed.clear()
    st.rerun()

cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
items = [i for i in feed.get("items", [])
         if int(i.get("impact") or 0) >= min_imp
         and i["published_utc"] >= cutoff
         and (i.get("direction") or "neutral") in dirs
         and (not q or q in " ".join([i["title"], i.get("summary") or "", i.get("why") or "",
                                      *(i.get("symbols") or []), *(i.get("sectors") or [])]).lower())]
if sort_by == "Impact":
    items.sort(key=lambda i: (int(i.get("impact") or 0), i["published_utc"]), reverse=True)

bull = sum(1 for i in items if i.get("direction") == "bullish")
bear = sum(1 for i in items if i.get("direction") == "bearish")
m1, m2, m3 = st.columns(3)
m1.metric("Showing", len(items))
m2.metric("Bullish ▲", bull)
m3.metric("Bearish ▼", bear)

if not items:
    st.info("Nothing at this level in this period. Try 'All' or a longer period.")
for it in items:
    st.markdown(card(it), unsafe_allow_html=True)

st.caption("Scores are an AI estimate of news impact on NIFTY 50, not trade signals.")
