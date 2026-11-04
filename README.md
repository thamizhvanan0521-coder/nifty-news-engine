# NIFTY News Engine — cloud version

Runs without your PC.

```
GitHub Actions (free, every 10 min on weekdays 07:30–16:30 IST, every 30 min otherwise)
   fetch 31 sources -> filter -> Claude scores impact -> Telegram alert if 7+
   -> writes feed.json to the repo's `data` branch
Streamlit page (free) reads feed.json -> open on your phone anytime
```

The repo must be **public**: GitHub Actions is free on public repos, while a private
repo's free allowance runs out in about 10 days at this schedule. Your API key and
Telegram token go in **GitHub Secrets**, never in the code. The only public output is
`feed.json`, which holds news headlines and scores.

---

## Step 1 — Create the GitHub repo

1. github.com → **New repository** → name `nifty-news-engine` → **Public** → Create.
2. **Add file → Upload files** → drag in everything from this folder **except** the
   `.github` folder (the web uploader skips hidden folders) → Commit.
3. **Add file → Create new file** → type the name
   `.github/workflows/news-engine.yml` → paste the contents of that file from this folder
   → Commit.

## Step 2 — Add your secrets

Repo → **Settings → Secrets and variables → Actions → New repository secret**, three times:

| Name | Value |
|---|---|
| `ANTHROPIC_API_KEY` | from console.anthropic.com |
| `TELEGRAM_BOT_TOKEN` | from @BotFather |
| `TELEGRAM_CHAT_ID` | your chat id (see below) |

To get the chat id: send your bot any message, then open
`https://api.telegram.org/bot<TOKEN>/getUpdates` and copy the number at `"chat":{"id":`.

## Step 3 — First run

Repo → **Actions** tab → enable workflows if asked → **news-engine** → **Run workflow**.

- It takes about 1–2 minutes. A green tick means it worked.
- If it shows red, click the run → open the **Run engine** step to see the error.
- After it's green, the branch list will show a new **`data`** branch with `feed.json` in it.

From then on it runs by itself.

## Step 4 — The web page

1. share.streamlit.io → **Create app** → pick this repo, branch `main`, file `streamlit_app.py`.
2. Open **Advanced settings → Secrets** and paste (with your GitHub username):
   ```
   FEED_URL = "https://raw.githubusercontent.com/<your-username>/nifty-news-engine/data/feed.json"
   ```
3. Deploy. On your phone, open the link → browser menu → **Add to Home screen**.

---

## What to expect

- **Alerts can arrive late.** GitHub may delay scheduled jobs by 5–30 minutes at busy
  times, and occasionally skips a run. Treat this as a news radar, not a tick-by-tick feed.
- **The page sleeps when unused.** On first open after a while it takes a few seconds to
  wake. The GitHub job keeps running regardless.
- **The page updates a few minutes behind.** GitHub caches `feed.json` briefly, and the
  page's **↻ Refresh** button forces a reload.
- **Watch for a paused schedule.** GitHub pauses schedules on public repos after 60 days
  with no repo activity. If the page says "No update for over an hour", open the
  **Actions** tab and re-enable the workflow.
- **NSE filings may fall back to RSS.** NSE sometimes blocks cloud servers; the engine
  then falls back to NSE's RSS feed automatically.

## Changing settings

Edit `config.yaml` in the repo on GitHub (pencil icon) → Commit. The next run uses it.

- `alerts.min_impact` — raise to 8 for fewer pings.
- `nse_holidays` — add this year's holidays so no brief goes out on those days.
- `sources.google_news` — add any search you want watched.

## Running on your PC instead (optional)

```bat
pip install -r requirements-engine.txt
copy .env.example .env   &  notepad .env
python main.py once
streamlit run streamlit_app.py
```
For a local page, set `FEED_URL=public\feed.json` first.
