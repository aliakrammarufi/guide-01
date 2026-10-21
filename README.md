# XAU Sentinel

An AI-assisted gold (XAU/USD) market monitor. It pulls price data, runs technical
analysis, scans the news that moves gold, scores each headline as bullish or
bearish, combines everything into one bias with a confidence level, and delivers
it two ways:

* **Telegram bot** – commands on demand, a scheduled report with a chart, and
  alerts when the bias flips, when price moves fast, or when high-impact news lands.
* **Web terminal** – a live, animated dashboard at `http://localhost:8080`:
  a scrolling ticker tape (gold, DXY, US10Y, silver, oil, S&P 500, EUR/USD, VIX,
  BTC), a price that ticks in real time with flashes and trade prints, a live
  sparkline, a spring-loaded bias dial, RSI / volatility / confidence rings, a
  DOM-style price ladder whose marker follows the live price, a candlestick chart
  (EMA 20/50/200, support, resistance, stop/targets) whose last candle updates with
  every tick, market-session clocks, technical readouts, a news feed with sentiment
  (new headlines glow), and the AI briefing typed out as it arrives.

It is **decision support only**. There is no broker connection and it never places
orders – you decide and trade on your own platform.

---

## Quick start

```bash
# 1. Python 3.11+  (3.12 recommended)
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. Configure
cp .env.example .env            # then edit .env – at minimum TELEGRAM_BOT_TOKEN

# 3. Try it without any keys first (synthetic prices + sample headlines)
python main.py --demo --no-telegram        # open http://localhost:8080

# 4. Run for real
python main.py                             # Telegram bot + dashboard
```

Other ways to run:

| Command | What it does |
|---|---|
| `python main.py --once` | one analysis printed to the terminal, then exit (handy for cron or a quick check) |
| `python main.py --no-web` | Telegram only |
| `python main.py --no-telegram --port 9000` | dashboard only, on another port |
| `docker compose up -d` | everything in a container (uses `.env`, keeps the database in `./data`) |
| `python -m pytest` | run the offline test-suite |
| `python tools/build_static_demo.py` | build a single-file, server-less demo of the terminal (simulated ticks) you can open from disk or host anywhere |

---

## Setting up the Telegram bot

1. In Telegram, message **@BotFather** → `/newbot` → follow the prompts → copy the token
   into `TELEGRAM_BOT_TOKEN`.
2. Start the app, open your bot and send `/start`. That subscribes the chat to the
   scheduled reports.
3. Recommended: lock the bot to yourself. Your chat id is printed when the bot
   replies to an unknown chat, or ask **@userinfobot**. Put it in
   `TELEGRAM_ALLOWED_CHAT_IDS` (comma-separated for several).
4. Works in groups too – add the bot, allow the group's (negative) chat id in `TELEGRAM_ALLOWED_CHAT_IDS`, and it is subscribed automatically at start. Channels can't send commands; add a channel's id to the allow-list and it receives the reports.

### Commands

| Command | Reply |
|---|---|
| `/price` | price, 1h / 24h / ~1w change, ATR, RSI, trend |
| `/signal` | bias (strong buy … strong sell), confidence, combined/technical/news scores, reference entry / stop / T1 / T2, nearest S/R |
| `/analyze` | the full report: chart image, signal, AI briefing, technicals, news |
| `/news` | fresh scan of the feeds and the most influential headlines with their sentiment |
| `/signals` | the open BUY/SELL call (entry, SL, TP1, TP2, open R), recent results, win rate and total R |
| `/levels` | swing support & resistance, daily pivots, EMAs |
| `/calendar` | upcoming USD releases (CPI, FOMC, payrolls, PCE, Fed speakers) with forecast / previous and a countdown |
| `/subscribe` / `/unsubscribe` | the short scheduled update every `REPORT_INTERVAL_MIN` minutes (market hours), plus every call and alert as it happens |
| `/alerts on|off` | bias-change, big-move and breaking-news alerts |
| `/backtest` | how the signal rules did on the last ~60 days of real 15-minute bars (technical-only replay) |
| `/status` | what the bot is doing and with which settings |

### Trade signals – when to buy, when to sell

A call has two stages, like a trader's own plan:

1. **Setup** – posted when the bias is BUY/SELL with confidence ≥ `SIGNAL_MIN_CONF` (45%) *and* the session
   allows new entries. It gives an **entry zone** (a pullback between the H1 EMA20 and the nearest
   support/EMA50 – or a shallow pullback when price is extended), a **stop** beyond the zone and under
   structure, **TP1 / TP2** measured from the middle of the zone (TP1 pulled in front of a nearby wall),
   and a **don't-chase level**: if price runs there without a pullback, the setup is *missed* and no trade
   is taken.
2. **Live call** – the setup confirms when price is in the zone and the 15-minute chart prints an
   **EMA20 reclaim**, a **bullish/bearish engulfing candle**, a **rejection wick** or a **break of the
   pullback high/low**. Entry = close of the confirming bar. From there: TP1 → book half; the stop
   stays where it is (`SIGNAL_BREAKEVEN_AFTER_TP1=false`, the default – the 3-year
   replay showed a break-even move gives back most of the edge in gold's noise) or moves to break-even
   (`=true`); TP2 / stop / bias flip / 48 h max hold closes the rest. Results are in R (multiples of
   the initial risk) and count the half booked at TP1 — e.g. TP1 then the original stop is about −0.1R,
   TP1 then TP2 about +1.2R.

Setups that never confirm end as *missed*, *invalidated* (price went through the stop first), *cancelled*
(bias changed) or *expired* (12 h) – all "no trade", and they are counted separately from the win rate.

**Sessions.** The London open and the London/New York overlap get full confidence; the Asian session is
allowed with lower confidence; the NY-close dead zone (21–23 UTC), Friday evening, the weekend, the Sunday
open and ±30/15 min around high-impact USD releases block new entries (`SESSION_FILTER`, `ASIA_ENTRIES`,
`NEWS_BLACKOUT_*`).

**Position size.** Send `/risk 5000 1` (account size, % risk per trade) and every call includes the lot
size for its stop: `lots = account × risk% ÷ (stop distance × 100 oz)`, rounded down to 0.01. The terminal
has the same calculator (Account / Risk inputs in the Signals panel, kept in your browser).

### Alerts (sent to subscribed chats with alerts on)

* **Bias changed** – e.g. Neutral → Bullish bias, with the new levels.
* **Big move** – price moved more than `PRICE_MOVE_ALERT_PCT` in the last hour (2 h cooldown).
* **High-impact news** – a headline matches `HIGH_IMPACT_TERMS` (FOMC, CPI, payrolls,
  war, tariff, record high …) or the AI rates it ±1.5 or stronger.
* **Pre-release heads-up** – `EVENT_WARN_MIN` minutes (default 30) before every
  high-impact USD release on the economic calendar, with forecast and previous.

---

## How the analysis works

```
price data ──► technical score (-100..+100)
                 EMA stack (H1 + daily), market structure (H1 + H4), EMA slope, MACD, RSI,
                 Bollinger, 24h momentum · discounted when ADX says "range" · ± RSI divergence, VWAP side
news feeds ──► per-headline impact (-2..+2) ──► news score (-100..+100)
                 Claude classifier (if key)         time-decayed, confidence-weighted
                 or keyword rules (always)
                              │
                              ▼
          combined = 0.6 × technical + 0.4 × news   (weights configurable)
          bias:  ≥55 strong buy · ≥25 buy · >-25 neutral · ≤-25 sell · ≤-55 strong sell
          confidence: |combined|, +15 when tech & news agree, −25 when they conflict,
                      −5 per warning flag (overbought, stretched band, squeeze…)
          levels: stop = 1.5 × ATR (tucked under/over structure), T1 = 1.5 × ATR, T2 = 3.0 × ATR (≈ 1R / 2R; `ATR_TARGET_MULT`)
```

**Price action** (`market/structure.py`) reads the chart the way a discretionary trader does and feeds both
the score and the entry plan:

* *Market structure* on H1 and H4 – swing highs/lows, HH·HL / LH·LL (equal highs and lows within 0.15 ATR
  count as equal), the latest break of structure (continuation) and change of character (first break against
  the trend).
* *Key levels* – previous day / week high & low, the Asian-session range, the daily VWAP, round numbers.
* *Last impulse + Fibonacci* 38.2 / 50 / 61.8 / 78.6 % retracements.
* *Imbalances* – unfilled fair-value gaps and unmitigated order blocks near price.
* *Regime* – ADX (trending ≥ 25, ranging < 18 → trend parts of the score ×0.6) and the ATR percentile.
* *RSI divergence* at the last swing and a one-line read of the last closed H1 candle.

The entry zone is still the EMA20 ↔ EMA50/support pullback, but a 50/61.8 % fib or an order block just
beyond it pulls the far edge out (bounded at 1.5 ATR), and everything coinciding with the zone is listed as
**confluence** ("EMA50 · 61.8% fib · prev-day low") on Telegram, the terminal and the replay's
`by_confluence` table. `/analyze` has a PRICE ACTION section; `/levels` adds session levels and fibs; the
chart draws PDH / PDL / VWAP and shades the zone.

**Macro context** (`market/macro.py`): the dollar index, the US 10-year yield and the S&P 500 (daily, Yahoo,
cached an hour) each add or remove a few points depending on whether they sit above or below their 20-day
average — a weaker dollar and lower yields help gold, risk-off adds a little — capped at ±8, shown as a MACRO
block in `/analyze`, in the terminal and in the replay (`no macro context` is one of the compared variants).

**Keyword rules** understand the real gold drivers and are subject-aware:
"dollar slides" is bullish, "gold slides" is bearish, "rate-cut hopes dented" is
bearish even though it contains "rate cut".

**With `ANTHROPIC_API_KEY`** every new headline is sent (in batches) to the
classifier model, which returns an impact, a confidence, a theme and a one-line
reason, and the analyst model writes a short briefing for each report. Cost is
small: a few thousand tokens per hour at default settings. Without a key everything
still works – you just get rule-based scores and no written briefing.

---

## Hosting notes

* **Render free tier has no persistent disk**: every deploy or restart would start with an empty database.
  Set `REDIS_URL` to a Redis-compatible store (a free Render Key Value instance works, internal URL
  `redis://red-xxxx:6379`) and the bot snapshots the whole database there every 2 minutes and on shutdown,
  then restores it on the next start — open calls, history, subscribers, `/risk` settings and the self-check
  all survive a redeploy. (A free Key Value instance keeps data in memory only; Render may restart it, which
  loses the copy — rare, and the bot simply starts fresh.) The allow-listed chats are re-subscribed on every
  start anyway, and `ACCOUNT_SIZE` / `RISK_PCT` / `FIXED_LOTS` env defaults always survive. The Starter plan
  with the 1 GB disk in `render.yaml` needs none of this.
* On SIGTERM (a redeploy) the bot takes a last snapshot, stops Telegram polling and exits within seconds, so
  the old and the new instance barely overlap.
* News feeds are fetched with conditional requests (`ETag` / `If-Modified-Since`): publishers that support
  them answer "304 Not Modified" instead of resending the whole feed every scan.
* Tick-driven events (setup confirmed, TP1, TP2, stop) reach Telegram within a second of the tick; the
  scheduled report and the news scan are separate.
* **Live candles.** The tick stream also builds the bot's own 15-minute and hourly candles
  (`market/bars.py`); they are overlaid on the provider's bars (complete bars with enough ticks replace
  them, the forming bar is extended), so the chart read is current to the second. A fast read runs a few
  seconds after every 15-minute close (confirmations are caught at once) and every minute in between,
  reusing the provider's bars for up to 150 s so Yahoo is not hit more often. A **"zone reached"** event is
  sent the moment price enters a setup's zone.

## Data sources

| Setting | Default | Notes |
|---|---|---|
| `QUOTE_PROVIDER=goldapi` | every 1 s | Live spot XAU/USD ticks for the terminal from gold-api.com – free, no key. Falls back to the data provider. |
| `DATA_PROVIDER=yfinance` | `GC=F` | COMEX gold futures bars, free, no key. With `SPOT_BASIS=true` (default) the bars are **rescaled onto the live spot price** at every refresh, so candles, support/resistance and SL/TP are on the same scale as your MT5 quote. Falls back to `XAUUSD=X`, then `GLD` if Yahoo refuses the symbol. |
| `DATA_PROVIDER=twelvedata` | `XAU/USD` | True spot bars. Free key at twelvedata.com (800 requests/day is plenty). Falls back to yfinance on error. |
| `NEWS_FEEDS` | 45 built-in feeds | see below. All are fetched **concurrently** (16 workers, 8 s timeout) so a full scan takes a few seconds; any feed that fails is skipped. |
| `CALENDAR_URL` | ForexFactory weekly JSON | free economic calendar, `CALENDAR_CURRENCIES=USD` by default. ForexFactory rate-limits shared cloud IPs, so the bot falls back to TradingView's calendar feed, then Nasdaq's (impact classified by event name). |

### The 45 built-in news feeds

**Google News searches** (each aggregates Reuters, Bloomberg, AP, CNBC, WSJ, FT,
MarketWatch and hundreds more – the cheapest way to get the big wires):
gold price · XAUUSD / gold futures / spot gold · gold record high · gold ETF flows ·
central-bank gold buying / PBOC · World Gold Council / India & China demand ·
silver & precious metals · Federal Reserve / FOMC / Powell · rate cut / rate hike
expectations · US dollar / DXY · Treasury yields · CPI / PCE inflation · payrolls /
jobs / jobless claims · recession / slowdown · geopolitics / safe haven · Middle East /
Ukraine / Taiwan · tariffs / trade war / sanctions · debt ceiling / shutdown / deficit ·
plus publisher-scoped searches for **reuters.com, bloomberg.com, kitco.com, cnbc.com,
marketwatch.com, ft.com**.

**Direct publisher feeds:** Bloomberg Markets & Economics · WSJ Markets · MarketWatch
Top Stories & Market Pulse · CNBC Top News, Economy & Finance · NYT Business ·
BBC Business & World · The Guardian Business · Al Jazeera (geopolitics) ·
**Federal Reserve press releases** and **BLS releases** (CPI / jobs, first-hand, the
moment they publish) · Kitco · FXStreet · ForexLive · DailyFX · Investing.com
Commodities & Economic Indicators · Mining.com.

Feeds and the lexicon live in `config.py` and `news/sentiment.py` – both are plain
lists you can edit, and `NEWS_FEEDS` in `.env` overrides the whole list.

### Speed

| Setting | Default | What it controls |
|---|---|---|
| `NEWS_SCAN_INTERVAL_MIN` | 3 | how often all feeds are re-scanned and alerts checked |
| `ANALYSIS_INTERVAL_MIN` | 15 | how often the scheduled analysis runs (bias changes, setups, calls) |
| `REPORT_INTERVAL_MIN` | 15 | how often subscribers get the short update: price, bias, plan or live call, what's next. `/analyze` gives the full picture on demand |
| `QUOTE_PROVIDER` / `QUOTE_INTERVAL_SEC` | goldapi / 1 | live price tick on the terminal: gold-api.com every second (free, no key); falls back to the data provider |
| `FEED_WORKERS` / `FEED_TIMEOUT_SEC` | 16 / 8 | concurrency and per-feed timeout |

Scanning every 3 minutes with 46 feeds is fine for Google News and the publishers
above. Going below ~2 minutes gains little (feeds themselves update every few minutes)
and risks being rate-limited.

---

## Project layout

```
main.py                entry point (CLI flags above)
config.py              all settings, read from .env
pipeline.py            price → technicals → news → sentiment → signal → briefing
state.py               shared cache of the latest report (bot + dashboard)
demo.py                --demo mode (synthetic data, no keys)
market/  data.py       yfinance / Twelve Data providers
         indicators.py EMA, RSI, MACD, ATR, Bollinger, swing levels, pivots
         analysis.py   technical score and reasons
news/    fetch.py      RSS fetching, relevance filter, de-duplication
         sentiment.py  keyword rules, Claude classifier, aggregation
         store.py      SQLite: articles, subscribers, state
signals/ engine.py     combined bias, confidence, reference levels
         tracker.py    BUY/SELL calls: open, TP1/TP2/SL tracking, R results, events
ai/      analyst.py    written briefing (Claude)
bot/     telegram_bot.py, formatting.py, chart.py
market/  quote.py      fast live quotes + ticker-tape instruments (cached)
web/     server.py (FastAPI) + static/index.html (terminal UI)
         API: GET /api/report · GET /api/quote · GET /api/tape · POST /api/refresh · GET /api/health
tests/                 offline unit tests (synthetic data)
```

---

## The terminal, piece by piece

| Element | What it shows | Updates |
|---|---|---|
| Intro | gold coin splash while the first analysis loads, then panels rise in and the chart wipes in | once |
| Ticker tape | gold and the instruments that drive it, with change vs. previous close | every 60 s (every few seconds in demo) |
| Price | live quote on rolling odometer digits; green/red glow and a floating "+0.40" print on every tick; the panel's glow follows the day's direction | every 8 s (1.5 s in demo) |
| Tick tape | the last eight quotes with time and change | every tick |
| Session timeline | Sydney / Tokyo / London / New York lanes across 24 h UTC with a moving "now" marker; open sessions glow | every second |
| Toasts | slide-in alerts when the bias flips, a level is crossed, a gold-moving headline lands, or a high-impact release is 30 min away | as they happen |
| Backdrop | drifting price traces and gold dust on a canvas, breathing colour glows, an animated gold ring around the signal panel | continuous |
| Sparkline | the last ~90 ticks | every tick |
| Signals | the open BUY/SELL call with entry, SL, TP1, TP2, live open R and a track; history with win rate / total R | every analysis + every tick |
| Bias dial | combined score −100…+100, needle springs to the new value, arc glows in the bias colour | every analysis |
| Rings | RSI, volatility (ATR as % of price), confidence | every analysis |
| Price ladder | pivots, swing S/R, stop and targets stacked by price; the marker rides the live price and rows flash when crossed | every tick |
| Chart | H1 candles with EMA 20/50/200 and level lines; the last candle updates live | every tick |
| Heat strip | last 24 candles, brighter = bigger move | every analysis |
| Sessions | Sydney / Tokyo / London / New York open or closed, UTC and local clocks | every second |
| News | headlines scored and time-decayed; ones that arrived since you opened the page are tagged NEW and glow | every analysis |

The look is a cyberpunk holographic HUD: indigo night, electric cyan and magenta, angled panels with neon edges, a holographic multi-ring gauge for the bias (the arc fills clockwise for bullish, counter-clockwise for bearish; orbiting satellites ping on every tick), LED segment meters, a trade monitor and an event stream. Two alternative skins ship alongside – `web/static/index_gold.html` (dark + gold) and `web/static/index_matrix.html` (green phosphor terminal); copy either over `index.html` to switch.

Everything animated respects the operating-system "reduce motion" setting.

## Self-check (historical replay)

With `BACKTEST_ON_START=true` the bot replays the last ~60 days of real 15-minute bars through the exact
production path a few minutes after start-up (and again when the stored result is older than
`BACKTEST_MAX_AGE_DAYS` or the rules changed): technical analysis → setup zone → 15-minute confirmation →
stop / TP1 / TP2, on a private in-memory tracker. `/backtest` on Telegram, the Signals panel of the terminal and
`GET /api/backtest` show the result: trades, win rate, average and total R, profit factor, max drawdown, TP1
rate, and breakdowns by confidence, session and confirmation type, plus "what if the confidence bar were
higher". `python tools/backtest.py --min-conf 55` does the same from the command line.

It is technical-only (no news history, no calendar blackouts), fills at the confirming 15-minute close,
checks the stop before the targets inside a bar, books half at TP1 and charges `SPREAD_USD` plus
`SLIPPAGE_USD` on stop fills — a way to judge the rules on numbers, not a forecast.

**Deep check.** `DEEP_CHECK=true` adds the same replay over `DEEP_CHECK_YEARS` (default 3) of spot XAU/USD
15-minute bars built from Dukascopy's public 1-minute candles (`market/history.py`; cached compressed in the
key-value store, only new days are fetched later). It takes hours on a small instance and runs in the
background after the 60-day check; `/backtest` then shows a per-year table and `/api/selfcheck` carries the
full report with `by_year` / `by_quarter`. `python tools/backtest.py --deep 3` runs it locally.

## Tuning tips

* Trading mostly on news days? Raise `NEWS_WEIGHT` to 0.5 and shorten
  `NEWS_HALF_LIFE_HOURS` to 6 so stale headlines fade faster.
* Too many alerts? Raise `PRICE_MOVE_ALERT_PCT` or trim `HIGH_IMPACT_TERMS`.
* Scalping? The engine is built on H1 bars; `ANALYSIS_INTERVAL_MIN=15` keeps the
  picture fresh, but the indicators still describe the hourly trend.
* Keep the bot private: set `TELEGRAM_ALLOWED_CHAT_IDS`.

## Disclaimer

Markets are risky. The scores here are heuristics over public data and language
models; they can be wrong, late, or fooled by a misleading headline. Nothing in this
project is financial advice. Never size positions on a signal alone.
