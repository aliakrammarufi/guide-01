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
4. Works in groups/channels too – add the bot, send `/subscribe` there.

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
| `/subscribe` / `/unsubscribe` | scheduled report every `ANALYSIS_INTERVAL_MIN` minutes |
| `/alerts on|off` | bias-change, big-move and breaking-news alerts |
| `/status` | what the bot is doing and with which settings |

### Trade signals – when to buy, when to sell

The bot turns its analysis into explicit calls and follows them for you:

* A **BUY** (or **SELL**) call opens when the bias is bullish (bearish) with
  confidence ≥ `SIGNAL_MIN_CONF` (default 45 %) and no call is already open.
  Entry = price at that moment; **SL**, **TP1**, **TP2** come from the ATR/structure
  levels. Every subscriber gets a `📡 NEW BUY SIGNAL` message.
* The live price is watched: `🎯 TP1 HIT` (stop moves to break-even),
  `🏁 TP2 HIT – closed`, `🛑 STOPPED OUT – closed`. A call also closes when the
  engine's bias flips to the other side (`🔁`) or after `SIGNAL_MAX_HOURS` (`⌛`).
* Results are measured in **R** (multiples of the initial risk), so a +1.7R call
  made 1.7× what it risked. `/signals` and the terminal show win rate, total R
  and average R over closed calls.

Suggested handling of a call: take half off at TP1, move the stop to break-even,
let the rest run to TP2. These are calls, not orders – you place the trade on
your own platform, and you decide the size.

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
                 EMA stack (H1 + daily), EMA slope, MACD, RSI, Bollinger, 24h momentum
news feeds ──► per-headline impact (-2..+2) ──► news score (-100..+100)
                 Claude classifier (if key)         time-decayed, confidence-weighted
                 or keyword rules (always)
                              │
                              ▼
          combined = 0.6 × technical + 0.4 × news   (weights configurable)
          bias:  ≥55 strong buy · ≥25 buy · >-25 neutral · ≤-25 sell · ≤-55 strong sell
          confidence: |combined|, +15 when tech & news agree, −25 when they conflict,
                      −5 per warning flag (overbought, stretched band, squeeze…)
          levels: stop = 1.5 × ATR (tucked under/over structure), T1 = 1.25 × ATR, T2 = 2.5 × ATR
```

**Keyword rules** understand the real gold drivers and are subject-aware:
"dollar slides" is bullish, "gold slides" is bearish, "rate-cut hopes dented" is
bearish even though it contains "rate cut".

**With `ANTHROPIC_API_KEY`** every new headline is sent (in batches) to the
classifier model, which returns an impact, a confidence, a theme and a one-line
reason, and the analyst model writes a short briefing for each report. Cost is
small: a few thousand tokens per hour at default settings. Without a key everything
still works – you just get rule-based scores and no written briefing.

---

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
| `ANALYSIS_INTERVAL_MIN` | 15 | how often the full report (chart, AI briefing) is produced and sent |
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
