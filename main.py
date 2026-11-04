"""Thamizh News Engine — finds news that affects the Indian stock market.

  python main.py run            start the engine (default)
  python main.py once           one fetch -> score -> alert cycle, then exit
  python main.py check-sources  test every source and print what it returns
  python main.py brief          build and send the pre-market brief now
  python main.py recent         show scored news from the last N hours
  python main.py test-alert     send a sample alert to Telegram + desktop
  python main.py selftest       offline test of filter/dedupe/store/format (no network)
  python main.py export         write public/feed.json for the web page (cloud)

Cloud mode (GitHub Actions): set NEWS_ENGINE_CLOUD=1 -> no desktop popup/sound.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import yaml
from dotenv import load_dotenv

from engine import brief as brief_mod
from engine.alerts import Alerter, format_telegram
from engine.dedupe import DupIndex
from engine.models import NewsItem
from engine.prefilter import Prefilter
from engine.sources import make_session
from engine.sources.bse import BSEAnnouncements
from engine.sources.nse import NSEAnnouncements
from engine.sources.rss import RSSSource, google_news_source
from engine.store import Store
from engine.timeutil import fmt_ist, in_window, now_ist, parse_hhmm
from engine.universe import Universe, load_universe

ROOT = Path(__file__).resolve().parent
log = logging.getLogger("engine")


# ------------------------------------------------------------------ setup
def setup_logging() -> None:
    (ROOT / "logs").mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    fh = RotatingFileHandler(ROOT / "logs" / "engine.log", maxBytes=5_000_000, backupCount=5,
                             encoding="utf-8")
    fh.setFormatter(fmt)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers[:] = [fh, ch]
    for noisy in ("httpx", "urllib3", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def load_config() -> dict:
    load_dotenv(ROOT / ".env")
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if os.getenv("NEWS_ENGINE_CLOUD"):
        # No screen or speaker on a cloud runner: Telegram + web page only.
        for section in ("alerts", "brief"):
            cfg.setdefault(section, {}).update({"desktop": False, "sound": False})
    return cfg


def has_claude_key() -> bool:
    k = os.getenv("ANTHROPIC_API_KEY", "").strip()
    return k.startswith("sk-ant-") and len(k) > 30


def build_sources(cfg: dict, session, universe: Universe) -> list:
    sc = cfg["sources"]
    fast = int(cfg["poll"]["market_seconds"])
    srcs: list = [RSSSource(s["name"], s["url"], s["tier"], session, interval=fast)
                  for s in sc.get("rss", [])]
    gi = int(sc.get("google_news_interval", 600))
    srcs += [google_news_source(g["q"], g.get("tier", "india_media"), session, interval=gi)
             for g in sc.get("google_news", [])]
    if sc.get("nse_announcements", True):
        srcs.append(NSEAnnouncements(session, universe, interval=fast))
    if sc.get("bse_announcements", False):
        srcs.append(BSEAnnouncements(session, universe, interval=max(fast, 180)))
    return srcs


# ------------------------------------------------------------------ engine
class Engine:
    def __init__(self, cfg: dict, need_claude: bool = True):
        self.cfg = cfg
        (ROOT / "data").mkdir(exist_ok=True)
        self.store = Store(ROOT / "data" / "news.db")
        self.session = make_session()
        self.heavy = {s.upper() for s in cfg.get("heavyweights", [])}
        self.universe = load_universe(cfg.get("universe", "nifty100"), ROOT / "data",
                                      sorted(self.heavy), self.session)
        self.prefilter = Prefilter(cfg.get("prefilter", {}), self.universe, self.heavy)
        self.alerter = Alerter(cfg.get("alerts", {}), self.heavy)
        self.sources = build_sources(cfg, self.session, self.universe)
        self.holidays = set(map(str, cfg.get("nse_holidays") or []))
        self.last_fetch: dict[str, float] = {}
        self.fail_count: dict[str, int] = {}
        self.classifier = None
        self.ai = has_claude_key()
        if need_claude:
            if self.ai:
                from engine.classifier import Classifier
                self.classifier = Classifier(cfg.get("claude", {}))
                log.info("scoring mode: Claude AI")
            else:
                from engine.keyword_classifier import KeywordClassifier
                self.classifier = KeywordClassifier(self.prefilter)
                log.info("scoring mode: KEYWORD (free) - no valid ANTHROPIC_API_KEY found")

    # ---- 1. fetch
    def fetch_all(self, force: bool = False) -> list[NewsItem]:
        now = time.time()
        due = [s for s in self.sources
               if force or now - self.last_fetch.get(s.name, 0) >= s.interval - 5]
        items: list[NewsItem] = []
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = {ex.submit(s.fetch): s for s in due}
            for f in as_completed(futs):
                s = futs[f]
                self.last_fetch[s.name] = now
                try:
                    got = f.result()
                    items.extend(got)
                    if self.fail_count.pop(s.name, 0) >= 3:
                        log.info("source recovered: %s", s.name)
                except Exception as e:  # noqa: BLE001
                    n = self.fail_count[s.name] = self.fail_count.get(s.name, 0) + 1
                    if n in (1, 3) or n % 30 == 0:
                        log.warning("source failed (%dx) %s: %s", n, s.name, e)
        return items

    # ---- 2. triage: stale / skipped / dup / pending
    def triage(self, fresh: list[NewsItem]) -> dict[str, int]:
        stats = {"stale": 0, "skipped": 0, "dup": 0, "pending": 0}
        if not fresh:
            return stats
        max_age = timedelta(hours=int(self.cfg.get("max_age_hours", 18)))
        min_score = float(self.cfg.get("prefilter", {}).get("min_score", 3))
        dups = DupIndex(self.store.recent_titles(hours=12))
        now = datetime.now(timezone.utc)
        for it in sorted(fresh, key=lambda x: x.published):
            if now - it.published > max_age:
                status, score, dup_of = "stale", None, None
            else:
                score = self.prefilter.score(it)
                dup_of = None
                if score < min_score:
                    status = "skipped"
                elif it.tier != "corporate" and (dup_of := dups.find(it.title)):
                    status = "dup"
                else:
                    status = "pending"
                    if it.tier != "corporate":
                        dups.add(it.uid, it.title)
            self.store.set_status(it.uid, status, prescore=score, dup_of=dup_of)
            stats[status] += 1
        return stats

    # ---- 3. score with Claude, alert
    def classify_pending(self) -> int:
        if not self.classifier:
            return 0
        c = self.cfg.get("claude", {})
        batch, max_batches = int(c.get("batch_size", 12)), int(c.get("max_batches_per_cycle", 8))
        cap = int(c.get("max_calls_per_day", 400))
        self.store.expire_pending(int(self.cfg.get("max_age_hours", 18)))
        done = 0
        for _ in range(max_batches):
            rows = self.store.pending(batch)
            if not rows:
                break
            key = f"claude_calls_{now_ist().date().isoformat()}"
            if self.ai and int(self.store.get_meta(key, "0")) >= cap:
                log.warning("daily Claude cap (%d) reached; %d+ items waiting", cap, len(rows))
                break
            try:
                results = self.classifier.classify(rows)
            except Exception as e:  # noqa: BLE001
                log.error("classify failed, will retry next cycle: %s", e)
                break
            finally:
                if self.ai:
                    self.store.incr_counter(key)
            for r in rows:
                cls = results.get(r["uid"])
                if not cls:
                    continue
                self.store.save_classification(r["uid"], cls)
                done += 1
                if self.alerter.should_alert(r, cls):
                    self.alerter.send(r, cls)
                    self.store.mark_alerted(r["uid"])
                    log.info("ALERT %d/10 %s | %s", cls["impact"], cls["direction"], r["title"][:120])
            # items Claude silently dropped stay pending; avoid an infinite loop on them
            if len(results) == 0:
                break
        return done

    def maybe_brief(self) -> None:
        bcfg = self.cfg.get("brief", {})
        if brief_mod.is_due(self.store, bcfg, self.holidays):
            try:
                brief_mod.build_and_send(self.store, bcfg, self.alerter, self.holidays,
                                         self.cfg.get("claude", {}).get("brief_model", "claude-sonnet-5-5"))
            except Exception as e:  # noqa: BLE001
                log.error("brief failed: %s", e)

    def cycle(self, force_fetch: bool = False) -> None:
        t0 = time.time()
        items = self.fetch_all(force=force_fetch)
        fresh = self.store.insert_new(items)
        stats = self.triage(fresh)
        scored = self.classify_pending()
        self.maybe_brief()
        log.info("cycle: fetched=%d new=%d pending=%d dup=%d skipped=%d stale=%d scored=%d (%.1fs)",
                 len(items), len(fresh), stats["pending"], stats["dup"], stats["skipped"],
                 stats["stale"], scored, time.time() - t0)

    def run_forever(self) -> None:
        p = self.cfg["poll"]
        w0, w1 = parse_hhmm(p["market_window"][0]), parse_hhmm(p["market_window"][1])
        log.info("engine started: %d sources, universe=%d symbols", len(self.sources),
                 len(self.universe.rows))
        while True:
            start = time.time()
            try:
                self.cycle()
            except Exception:  # noqa: BLE001
                log.exception("cycle crashed; continuing")
            fast = in_window(now_ist().time(), w0, w1)
            interval = int(p["market_seconds"] if fast else p["off_hours_seconds"])
            time.sleep(max(5, interval - (time.time() - start)))


# ------------------------------------------------------------------ commands
def cmd_check_sources(cfg: dict) -> None:
    eng = Engine(cfg, need_claude=False)
    print(f"Universe: {len(eng.universe.rows)} symbols\n")
    ok = 0
    for s in eng.sources:
        t0 = time.time()
        try:
            items = s.fetch()
            newest = max((i.published for i in items), default=None)
            frozen = newest and datetime.now(timezone.utc) - newest > timedelta(days=3)
            tag = "OLD " if frozen else "OK  "
            print(f"  {tag} {s.name:<40} {len(items):>4} items  newest={fmt_ist(newest) if newest else '-'}"
                  f"  ({time.time() - t0:.1f}s)" + ("  <- feed looks frozen" if frozen else ""))
            if items:
                print(f"       e.g. {items[0].title[:100]}")
            ok += 1
        except Exception as e:  # noqa: BLE001
            print(f"  FAIL {s.name:<40} {type(e).__name__}: {str(e)[:120]}")
    print(f"\n{ok}/{len(eng.sources)} sources working. Remove or fix failing URLs in config.yaml.")


def cmd_recent(cfg: dict, hours: int, min_impact: int) -> None:
    store = Store(ROOT / "data" / "news.db")
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = store.classified_since(since, min_impact, limit=200)
    print(f"{len(rows)} items, impact >= {min_impact}, last {hours}h\n")
    for r in rows:
        c = json.loads(r["classification"])
        print(f"{c['impact']:>2}/10 {c['direction']:<8} {fmt_ist(datetime.fromisoformat(r['published_utc']))}"
              f"  {r['title'][:110]}")
        print(f"        {c.get('why', '')}   [{r['source']}]")
    print("\nStatus counts:", store.counts_by_status())


FEED_FIELDS = ("impact", "direction", "category", "scope", "sectors", "symbols",
               "horizon", "summary", "why", "method")


def cmd_export(cfg: dict) -> Path:
    """Write public/feed.json: scored news of the last N hours + latest brief, for the web page."""
    ex = cfg.get("export", {})
    hours, min_impact, limit = int(ex.get("hours", 72)), int(ex.get("min_impact", 2)), int(ex.get("max_items", 600))
    store = Store(ROOT / "data" / "news.db")
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    items = []
    for r in store.classified_since(since, min_impact, limit=limit):
        c = json.loads(r["classification"])
        if not c.get("relevant", True):
            continue
        items.append({"title": r["title"], "url": r["url"], "source": r["source"], "tier": r["tier"],
                      "published_utc": r["published_utc"], "alerted": bool(r["alerted"]),
                      **{k: c.get(k) for k in FEED_FIELDS}})
    items.sort(key=lambda x: x["published_utc"], reverse=True)
    day_ago = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
    feed = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "brief": {"date": store.get_meta("last_brief_date"), "text": store.get_meta("last_brief_text"),
                  "items": int(store.get_meta("last_brief_items", "0") or 0)},
        "stats": {"items_24h": sum(1 for i in items if i["published_utc"] >= day_ago),
                  "high_24h": sum(1 for i in items if i["published_utc"] >= day_ago and i["impact"] >= 7),
                  "db_status": store.counts_by_status()},
        "items": items,
    }
    out = ROOT / "public" / "feed.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(feed, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    pruned = store.prune(int(ex.get("keep_days", 7)))
    log.info("exported %d items to %s (pruned %d old rows)", len(items), out, pruned)
    return out


def cmd_test_alert(cfg: dict) -> None:
    heavy = {s.upper() for s in cfg.get("heavyweights", [])}
    al = Alerter(cfg.get("alerts", {}), heavy)
    row = _fake_row("TEST: RBI cuts repo rate by 25 bps to 5.25% in surprise move",
                    "Reserve Bank of India", "https://www.rbi.org.in/")
    cls = {"relevant": True, "impact": 9, "direction": "bullish", "scope": "market",
           "sectors": ["Banks", "NBFC", "Auto"], "symbols": [], "horizon": "1-3 days",
           "summary": "This is a test alert from your News Engine.", "why": "Rate-sensitive rally likely."}
    sent = al.telegram(format_telegram(row, cls))
    al.desktop("🟢 9/10 BULLISH (test)", row["title"])
    al.sound(9)
    print("Telegram:", "sent" if sent else "NOT sent - check TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID in .env")


def _fake_row(title: str, source: str, url: str, symbols: list[str] | None = None,
              published: datetime | None = None) -> dict:
    return {"uid": "test", "title": title, "source": source, "url": url,
            "symbols": json.dumps(symbols or []), "summary": "",
            "published_utc": (published or datetime.now(timezone.utc)).isoformat(timespec="seconds")}


def cmd_selftest() -> None:
    """Offline checks. No network, no API key."""
    import tempfile

    print("selftest: prefilter / dedupe / store / alert rules / formatting")
    heavy = {"HDFCBANK", "RELIANCE", "INFY"}
    uni = Universe({"HDFCBANK": {"name": "HDFC Bank Limited", "industry": "Financial Services"},
                    "RELIANCE": {"name": "Reliance Industries Limited", "industry": "Oil Gas"},
                    "TATASTEEL": {"name": "Tata Steel Limited", "industry": "Metals"}})
    pf = Prefilter({}, uni, heavy)
    now = datetime.now(timezone.utc)
    mk = lambda t, tier="india_media", syms=None, age=10: NewsItem(  # noqa: E731
        "test", tier, t, f"https://x/{abs(hash(t))}", now - timedelta(minutes=age), symbols=syms or [])

    a = mk("RBI keeps repo rate unchanged, MPC shifts stance to neutral")
    b = mk("RBI keeps repo rate unchanged; MPC shifts stance to neutral - Mint")
    c = mk("Tata Steel: Trading Window closure intimation", "corporate", ["TATASTEEL"])
    d = mk("HDFCBANK (HDFC Bank Limited): Financial Results for Q2", "corporate", ["HDFCBANK"])
    e = mk("10 best monsoon recipes for the weekend")
    f = mk("Crude oil jumps 4% as OPEC+ announces surprise cut", "global")

    scores = {x.title[:40]: pf.score(x) for x in (a, c, d, e, f)}
    for k, v in scores.items():
        print(f"  score {v:>6.1f}  {k}")
    assert pf.score(a) >= 3 and pf.score(d) >= 3 and pf.score(f) >= 3
    assert pf.score(c) < 0, "noise filing should be negative"
    assert pf.score(e) < 3, "lifestyle should be filtered"

    idx = DupIndex([])
    idx.add(a.uid, a.title)
    assert idx.find(b.title) == a.uid, "Google-News style duplicate not caught"
    assert idx.find(f.title) is None
    idx.add("mmo", "Money Market Operations as on November 03, 2026")
    assert idx.find("Money Market Operations as on November 02, 2026") is None, "different days merged"
    idx.add("sx", "Sensex jumps 1,225 pts at close, Nifty crosses 24,000")
    assert idx.find("Sensex jumps 1225 pts at close; Nifty crosses 24000 - ET") == "sx"
    print("  dedupe OK")

    with tempfile.TemporaryDirectory() as td:
        st = Store(Path(td) / "t.db")
        assert len(st.insert_new([a, b, c])) == 3
        assert len(st.insert_new([a])) == 0, "duplicate uid inserted twice"
        st.set_status(a.uid, "pending", prescore=7)
        rows = st.pending(10)
        assert rows and rows[0]["uid"] == a.uid
        st.save_classification(a.uid, {"impact": 8, "direction": "bearish"})
        assert st.counts_by_status().get("classified") == 1
        assert st.incr_counter("k") == 1 and st.incr_counter("k") == 2
        st.conn.close()
    print("  store OK")

    al = Alerter({"telegram": False, "min_impact": 7, "heavyweight_min_impact": 6,
                  "max_age_minutes": 90}, heavy)
    fresh_row = _fake_row(d.title, "NSE", "https://nse/x", ["HDFCBANK"])
    old_row = _fake_row(d.title, "NSE", "https://nse/x", ["HDFCBANK"], now - timedelta(hours=3))
    base = {"relevant": True, "direction": "bullish", "scope": "stock", "symbols": ["HDFCBANK"],
            "sectors": ["Banks"], "horizon": "1-3 days", "summary": "s", "why": "w"}
    assert al.should_alert(fresh_row, {**base, "impact": 6}), "heavyweight 6 should alert"
    assert not al.should_alert(fresh_row, {**base, "impact": 5}), "5 is below both thresholds"
    assert not al.should_alert(old_row, {**base, "impact": 9}), "old news must not alert"
    assert not al.should_alert(fresh_row, {**base, "impact": 9, "relevant": False})
    print("  alert rules OK")

    msg = format_telegram(_fake_row("RBI <surprise> & cut", "RBI", "https://rbi.org.in/?a=1&b=2"),
                          {**base, "impact": 9, "scope": "market", "symbols": []})
    assert "&lt;surprise&gt;" in msg and "&amp;b=2" in msg, "HTML not escaped"
    print("  telegram format OK\n")
    print(msg)
    print("\nselftest PASSED")


def main() -> None:
    ap = argparse.ArgumentParser(description="Thamizh News Engine")
    ap.add_argument("command", nargs="?", default="run",
                    choices=["run", "once", "check-sources", "brief", "recent", "test-alert", "selftest",
                             "export"])
    ap.add_argument("--hours", type=int, default=6)
    ap.add_argument("--min-impact", type=int, default=5)
    args = ap.parse_args()

    if args.command == "selftest":
        cmd_selftest()
        return
    setup_logging()
    cfg = load_config()
    if args.command == "check-sources":
        cmd_check_sources(cfg)
    elif args.command == "recent":
        cmd_recent(cfg, args.hours, args.min_impact)
    elif args.command == "test-alert":
        cmd_test_alert(cfg)
    elif args.command == "brief":
        eng = Engine(cfg)
        print(brief_mod.build_and_send(eng.store, cfg.get("brief", {}), eng.alerter, eng.holidays,
                                       cfg.get("claude", {}).get("brief_model", "claude-sonnet-5-5")))
    elif args.command == "once":
        Engine(cfg).cycle(force_fetch=True)
    elif args.command == "export":
        cmd_export(cfg)
    else:
        try:
            Engine(cfg).run_forever()
        except KeyboardInterrupt:
            log.info("stopped by user")


if __name__ == "__main__":
    main()
