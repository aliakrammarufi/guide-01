"""
Central configuration for XAU Sentinel.

Everything is read from environment variables (or a .env file next to this
file).  Copy .env.example to .env and fill in the values.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name, "true" if default else "false").lower()
    return raw in {"1", "true", "yes", "on"}


def _env_list(name: str, default: list[str] | None = None) -> list[str]:
    raw = _env(name)
    if not raw:
        return list(default or [])
    return [item.strip() for item in raw.split(",") if item.strip()]


# Default news feeds.  Google News RSS queries are the most reliable free
# source (they aggregate Reuters, Bloomberg, CNBC, WSJ, FT, AP, MarketWatch…);
# the rest are direct publisher feeds.  All feeds are fetched concurrently and
# any that fail are skipped silently, so a dead URL never breaks the bot.
_GN = "https://news.google.com/rss/search?hl=en-US&gl=US&ceid=US:en&q="
DEFAULT_FEEDS: list[str] = [
    # --- Google News: gold itself --------------------------------------------
    _GN + "gold+price",
    _GN + "XAUUSD+OR+%22gold+futures%22+OR+%22spot+gold%22",
    _GN + "%22gold%22+%22record+high%22+OR+%22all-time+high%22",
    _GN + "gold+ETF+inflows+OR+outflows",
    _GN + "%22central+bank%22+gold+buying+OR+reserves+OR+PBOC",
    _GN + "%22World+Gold+Council%22+OR+%22gold+demand%22+India+China",
    _GN + "silver+price+OR+%22precious+metals%22",
    # --- Google News: the drivers -----------------------------------------
    _GN + "%22Federal+Reserve%22+rates+OR+FOMC+OR+Powell",
    _GN + "%22rate+cut%22+OR+%22rate+hike%22+Fed+expectations",
    _GN + "%22US+dollar%22+OR+DXY+OR+%22dollar+index%22",
    _GN + "%22Treasury+yields%22+OR+%2210-year%22+bonds",
    _GN + "inflation+CPI+OR+PCE+report",
    _GN + "%22nonfarm+payrolls%22+OR+%22jobs+report%22+OR+%22jobless+claims%22",
    _GN + "recession+OR+%22economic+slowdown%22+US+economy",
    _GN + "geopolitical+tensions+OR+%22safe+haven%22",
    _GN + "Middle+East+OR+Ukraine+OR+Taiwan+conflict+OR+ceasefire",
    _GN + "tariffs+OR+%22trade+war%22+OR+sanctions",
    _GN + "%22debt+ceiling%22+OR+%22government+shutdown%22+OR+%22US+deficit%22",
    # --- Google News: publisher-scoped (gets Reuters/Bloomberg without paid feeds)
    _GN + "site:reuters.com+gold+OR+Fed+OR+dollar",
    _GN + "site:bloomberg.com+gold+OR+Fed+OR+treasuries",
    _GN + "site:kitco.com",
    _GN + "site:cnbc.com+gold+OR+Fed+OR+inflation",
    _GN + "site:marketwatch.com+gold+OR+Fed",
    _GN + "site:fxstreet.com+gold",
    _GN + "site:dailyfx.com+gold",
    _GN + "site:bls.gov",
    _GN + "site:ft.com+gold+OR+%22Federal+Reserve%22",
    # --- Direct publisher feeds -------------------------------------------
    "https://feeds.bloomberg.com/markets/news.rss",
    "https://feeds.bloomberg.com/economics/news.rss",
    "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",              # WSJ markets
    "https://feeds.content.dowjones.io/public/rss/mw_topstories",  # MarketWatch
    "https://feeds.content.dowjones.io/public/rss/mw_marketpulse",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",      # CNBC top news
    "https://www.cnbc.com/id/20910258/device/rss/rss.html",       # CNBC economy
    "https://www.cnbc.com/id/10000664/device/rss/rss.html",       # CNBC finance
    "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
    "https://feeds.bbci.co.uk/news/business/rss.xml",
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://www.theguardian.com/uk/business/rss",
    "https://www.aljazeera.com/xml/rss/all.xml",                  # geopolitics
    "https://www.federalreserve.gov/feeds/press_all.xml",         # Fed statements, first-hand
    "https://www.forexlive.com/feed/news",
    "https://www.investing.com/rss/news_11.rss",                  # commodities
    "https://www.investing.com/rss/news_95.rss",                  # economic indicators
    "https://www.mining.com/feed/",
]

# Free economic calendar (ForexFactory's public weekly JSON). Used to warn before
# high-impact USD releases (CPI, FOMC, NFP…). Empty string disables it.
DEFAULT_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

# Headlines containing these terms are treated as "high impact" and can
# trigger an immediate Telegram alert (see Scheduler).
DEFAULT_HIGH_IMPACT_TERMS: list[str] = [
    "fomc", "fed decision", "rate decision", "rate cut", "rate hike",
    "cpi", "inflation data", "nonfarm", "non-farm", "nfp", "payrolls",
    "powell", "jackson hole", "gdp", "pce",
    "war", "missile", "strike", "invasion", "ceasefire", "sanction",
    "tariff", "trade war", "default", "shutdown", "bank collapse",
    "central bank buying", "record high", "all-time high", "plunge", "crash",
]


@dataclass
class Settings:
    # --- Telegram -----------------------------------------------------------
    telegram_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    # Comma-separated chat IDs allowed to use the bot.  Empty = anyone.
    allowed_chat_ids: list[int] = field(
        default_factory=lambda: [int(x) for x in _env_list("TELEGRAM_ALLOWED_CHAT_IDS")]
    )

    # --- Market data --------------------------------------------------------
    # "yfinance" (free, no key) or "twelvedata" (free key, true XAU/USD spot)
    data_provider: str = field(default_factory=lambda: _env("DATA_PROVIDER", "yfinance"))
    # yfinance symbols: GC=F = COMEX gold futures (very reliable),
    #                   XAUUSD=X = spot (sometimes thin / missing bars)
    yf_symbol: str = field(default_factory=lambda: _env("YF_SYMBOL", "GC=F"))
    twelvedata_api_key: str = field(default_factory=lambda: _env("TWELVEDATA_API_KEY"))
    twelvedata_symbol: str = field(default_factory=lambda: _env("TWELVEDATA_SYMBOL", "XAU/USD"))
    # Live tick source for the terminal: "goldapi" (free, no key, ~1 s), "twelvedata", or "yfinance"
    quote_provider: str = field(default_factory=lambda: _env("QUOTE_PROVIDER", "goldapi"))
    quote_interval_sec: float = field(default_factory=lambda: _env_float("QUOTE_INTERVAL_SEC", 1.0))
    # Rescale futures/ETF bars onto the spot XAU/USD basis of the live tick (levels & SL/TP match MT5)
    spot_basis: bool = field(default_factory=lambda: _env_bool("SPOT_BASIS", True))
    # Real-time tick sources, best available wins: finnhub (needs key), tradingview (no key), goldapi (no key)
    tick_sources: list[str] = field(default_factory=lambda: _env_list("TICK_SOURCES", ["finnhub", "tradingview", "goldapi"]))
    finnhub_api_key: str = field(default_factory=lambda: _env("FINNHUB_API_KEY"))
    finnhub_symbol: str = field(default_factory=lambda: _env("FINNHUB_SYMBOL", "OANDA:XAU_USD"))
    tv_symbols: list[str] = field(default_factory=lambda: _env_list("TV_SYMBOLS", ["OANDA:XAUUSD", "FX_IDC:XAUUSD", "TVC:GOLD"]))
    # Keep a free-tier host (Render) awake by pinging our own public URL every few minutes.
    keepalive: bool = field(default_factory=lambda: _env_bool("KEEPALIVE", False))
    keepalive_url: str = field(default_factory=lambda: _env("KEEPALIVE_URL") or _env("RENDER_EXTERNAL_URL"))
    keepalive_interval_min: int = field(default_factory=lambda: _env_int("KEEPALIVE_INTERVAL_MIN", 10))

    # --- AI (optional, Anthropic API) --------------------------------------
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    # Cheap + fast model for classifying headlines
    classifier_model: str = field(default_factory=lambda: _env("CLASSIFIER_MODEL", "claude-haiku-5-5"))
    # Stronger model for the written market briefing
    analyst_model: str = field(default_factory=lambda: _env("ANALYST_MODEL", "claude-sonnet-5-5"))

    # --- News ---------------------------------------------------------------
    feeds: list[str] = field(default_factory=lambda: _env_list("NEWS_FEEDS", DEFAULT_FEEDS))
    high_impact_terms: list[str] = field(
        default_factory=lambda: _env_list("HIGH_IMPACT_TERMS", DEFAULT_HIGH_IMPACT_TERMS)
    )
    news_lookback_hours: int = field(default_factory=lambda: _env_int("NEWS_LOOKBACK_HOURS", 36))
    news_half_life_hours: float = field(default_factory=lambda: _env_float("NEWS_HALF_LIFE_HOURS", 8.0))
    max_headlines_in_report: int = field(default_factory=lambda: _env_int("MAX_HEADLINES", 8))
    feed_workers: int = field(default_factory=lambda: _env_int("FEED_WORKERS", 16))
    feed_timeout_sec: float = field(default_factory=lambda: _env_float("FEED_TIMEOUT_SEC", 8.0))

    # --- Economic calendar --------------------------------------------------
    calendar_url: str = field(default_factory=lambda: _env("CALENDAR_URL", DEFAULT_CALENDAR_URL))
    calendar_currencies: list[str] = field(default_factory=lambda: _env_list("CALENDAR_CURRENCIES", ["USD"]))
    # minutes before a high-impact release to send the heads-up alert
    event_warn_min: int = field(default_factory=lambda: _env_int("EVENT_WARN_MIN", 30))

    # --- Signal engine ------------------------------------------------------
    tech_weight: float = field(default_factory=lambda: _env_float("TECH_WEIGHT", 0.6))
    news_weight: float = field(default_factory=lambda: _env_float("NEWS_WEIGHT", 0.4))
    atr_stop_mult: float = field(default_factory=lambda: _env_float("ATR_STOP_MULT", 1.5))
    atr_target_mult: float = field(default_factory=lambda: _env_float("ATR_TARGET_MULT", 2.5))

    # --- Trade signals (BUY / SELL calls with SL, TP1, TP2) -----------------
    signal_min_conf: int = field(default_factory=lambda: _env_int("SIGNAL_MIN_CONF", 45))
    signal_max_hours: float = field(default_factory=lambda: _env_float("SIGNAL_MAX_HOURS", 48.0))
    signal_breakeven_after_tp1: bool = field(default_factory=lambda: _env_bool("SIGNAL_BREAKEVEN_AFTER_TP1", True))
    # Entry timing: a call is a *setup* (zone) first and goes live only on a 15-minute confirmation
    signal_require_trigger: bool = field(default_factory=lambda: _env_bool("SIGNAL_REQUIRE_TRIGGER", True))
    signal_pending_max_hours: float = field(default_factory=lambda: _env_float("SIGNAL_PENDING_MAX_HOURS", 12.0))
    # Session filter: no new entries in the dead zone / weekend / Friday evening / Sunday open, and around high-impact news
    session_filter: bool = field(default_factory=lambda: _env_bool("SESSION_FILTER", True))
    asia_entries: bool = field(default_factory=lambda: _env_bool("ASIA_ENTRIES", True))
    news_blackout_before_min: int = field(default_factory=lambda: _env_int("NEWS_BLACKOUT_BEFORE_MIN", 30))
    news_blackout_after_min: int = field(default_factory=lambda: _env_int("NEWS_BLACKOUT_AFTER_MIN", 15))
    # Position sizing defaults for the terminal (Telegram users set their own with /risk)
    account_size: float = field(default_factory=lambda: _env_float("ACCOUNT_SIZE", 0.0))
    account_currency: str = field(default_factory=lambda: _env("ACCOUNT_CURRENCY", "USD").upper())
    risk_pct: float = field(default_factory=lambda: _env_float("RISK_PCT", 1.0))
    fixed_lots: float = field(default_factory=lambda: _env_float("FIXED_LOTS", 0.0))        # 0 = size by risk %
    contract_oz: float = field(default_factory=lambda: _env_float("CONTRACT_OZ", 100.0))   # 1 lot of XAU/USD = 100 oz

    # --- Scheduler ----------------------------------------------------------
    analysis_interval_min: int = field(default_factory=lambda: _env_int("ANALYSIS_INTERVAL_MIN", 15))
    news_scan_interval_min: int = field(default_factory=lambda: _env_int("NEWS_SCAN_INTERVAL_MIN", 3))
    # Alert when price moves more than this % within the last hour
    price_move_alert_pct: float = field(default_factory=lambda: _env_float("PRICE_MOVE_ALERT_PCT", 0.8))
    send_charts: bool = field(default_factory=lambda: _env_bool("SEND_CHARTS", True))

    # --- Storage ------------------------------------------------------------
    db_path: Path = field(default_factory=lambda: Path(_env("DB_PATH", str(BASE_DIR / "data" / "sentinel.db"))))
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO"))
    # Redis-compatible store (Render Key Value, Upstash…) that keeps a copy of the database so that
    # calls, history, subscribers and /risk settings survive a redeploy on a host without a disk.
    redis_url: str = field(default_factory=lambda: _env("REDIS_URL") or _env("KEY_VALUE_URL"))
    backup_interval_sec: int = field(default_factory=lambda: _env_int("BACKUP_INTERVAL_SEC", 120))

    # --- Self-check (historical replay of the signal rules) -----------------
    # Replays the last ~60 days of 15-minute bars through the engine on start-up (when the last
    # run is older than BACKTEST_MAX_AGE_DAYS) so /backtest and the terminal can show how the
    # rules would have done. Technical-only: news history is not available.
    backtest_on_start: bool = field(default_factory=lambda: _env_bool("BACKTEST_ON_START", False))
    backtest_max_age_days: float = field(default_factory=lambda: _env_float("BACKTEST_MAX_AGE_DAYS", 7.0))

    @property
    def ai_enabled(self) -> bool:
        return bool(self.anthropic_api_key)

    def validate(self) -> list[str]:
        """Return a list of human-readable configuration problems."""
        problems = []
        if not self.telegram_token:
            problems.append("TELEGRAM_BOT_TOKEN is missing (create a bot with @BotFather).")
        if self.data_provider not in {"yfinance", "twelvedata"}:
            problems.append("DATA_PROVIDER must be 'yfinance' or 'twelvedata'.")
        if self.data_provider == "twelvedata" and not self.twelvedata_api_key:
            problems.append("DATA_PROVIDER=twelvedata requires TWELVEDATA_API_KEY.")
        total = self.tech_weight + self.news_weight
        if total <= 0:
            problems.append("TECH_WEIGHT + NEWS_WEIGHT must be > 0.")
        return problems


settings = Settings()
