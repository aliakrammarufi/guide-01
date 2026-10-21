# XAU SENTINEL — iOS app · Claude Design master prompt

Paste everything below the line into Claude Design (template: Mobile app design). Attach 2–3 screenshots of the web terminal (https://xau-sentinel-pro.onrender.com) as references.

---

Design a native iOS app called **XAU Sentinel** — a professional gold (XAU/USD) decision-support terminal with a paid subscription. It is the mobile version of an existing web terminal and must look like it: same colours, same fonts, same "holographic HUD" feel, adapted to iPhone.

## 1. What the product is (and is not)

- XAU Sentinel analyses gold in real time and tells the trader **what the market is doing and where a trade would be**: bias and confidence, an entry zone, stop, TP1, TP2, and a 15-minute confirmation. It posts **setups**, then **live calls**, then **TP1 / TP2 / stop-out** updates, plus alerts (bias change, big move, breaking news, pre-release warnings) and an AI-written briefing.
- It is **analysis only**. It never connects to a broker, never places, modifies or closes orders, and never asks for broker credentials. Every screen that shows levels carries a short line: *"Decision support only — you place the trade."* Never use words like "guaranteed", "profit machine", "auto-trade".
- The user is a retail gold trader on MT5 who checks the phone between trades. He wants the decision first, the numbers he acts on second, and nothing he has to decode.

## 2. Visual identity — copy the web terminal exactly

Dark mode only. Treat these as the design tokens:

- Backgrounds: `#070A14` (base), `#0B1026` (raised). Panels are glass: `rgba(11,16,38,.9)` with a 1 px hairline `rgba(34,211,238,.16)`; stronger hairline `rgba(34,211,238,.32)`.
- Primary accent (electric cyan): `#22D3EE`, light `#A5F3FC`, deep `#0891B2`, glow `rgba(34,211,238,.5)`.
- Secondary accent (magenta): `#E040FB`; violet `#A78BFA`.
- Up / bullish: mint `#19F0B0`. Down / bearish: rose `#FF4D6D`. Each with a soft glow of the same colour at 50 %.
- Text: `#EAF4FF` primary, `#93A8CF` secondary, `#4E5F8C` muted.
- Corner radius **4 px** (sharp, instrument-like), never pill-shaped cards; chips may be pills.
- Fonts: **Orbitron** (display: titles, letter-spacing 0.18–0.28 em, uppercase, cyan-to-magenta gradient on the brand name), **Exo 2** (body), **IBM Plex Mono** (every number, tabular figures). Bundle these fonts; do not substitute SF Pro.
- Signature elements to reuse: the rotating two-ring logo mark (cyan top / magenta bottom, counter-rotating inner ring); panel titles with a small rotated-square diamond bullet and a short cyan gradient rule at the top-left of each panel; a thin ticker tape; a blinking red "LIVE" dot; LED status dots; subtle scanlines and a faint grid on the hero area; a 270° bias dial (arc gauge from bearish rose on the left to bullish mint on the right, needle at the combined score); odometer-style rolling price digits; small ring meters for technical / news scores; a session timeline with a glowing "now" marker.
- Motion: springy (`cubic-bezier(.34,1.56,.64,1)`) reveals on panels, 1-second smooth transitions on the dial and timeline, price digits roll, the LIVE dot pulses. Respect Reduce Motion.

## 3. Information architecture

Tab bar (5): **Terminal · Signals · Analysis · News · Settings**. Plus: onboarding + paywall, a Live Activity, two widgets, notifications.

### Terminal (home)
1. Ticker tape at the top: XAU/USD, DXY, US10Y, S&P 500, Brent — mono, scrolling.
2. Hero: live price in large rolling digits, 1h / 24h / 1w change chips, bid · ask · spread line, the bias dial with the combined score and the label (STRONG BUY / BUY / NEUTRAL / SELL / STRONG SELL) and a confidence % with a word (weak / fair / moderate / strong).
3. Session strip: current session (Asia / London / London-NY overlap / New York / Weekend), quality dot (best / good / ok / thin / closed), next change countdown, "no new entries" state when blocked.
4. **Signal card** — the most important element on the app:
   - No call: "No call — bias too weak for a call. Waiting." + Support / Resistance line + the *lean plan* (zone, stop, TP1, TP2) labelled "the plan if it strengthens".
   - Setup (pending): entry zone, SL, TP1, TP2, R:R, confidence, "wait for a pullback into the zone and a 15-minute confirmation — don't chase beyond X".
   - Zone reached: same, with "watching the 15-minute chart for a confirmation".
   - Live call: entry, current P/L in R and in account currency, SL (with points), TP1 (R), TP2 (R), lots & risk, the plan line ("book half at TP1, keep the stop at …, let the rest run to TP2"), a progress bar from SL through entry to TP2.
   - Closed: result in R with ✅ / ❌ / ➖.
5. Structure line: H4 trend · H1 trend · regime (trending / ranging) · ADX · macro label.
6. Next high-impact release countdown.
7. Mini chart (H1 candles with EMA 20/50/200, zone band, stop and target lines) that opens full-screen in landscape.

### Signals
- Active card (same as above) at the top.
- History list: date · side · entry → exit · result R · outcome icon; "no trade" rows greyed (missed / invalidated / cancelled / expired).
- Stats header: closed, win rate, total R, average R, profit factor, max drawdown; "Calls you took" when the user has marked calls (Took / Skipped buttons on every closed call).
- Weekly scorecard sheet.
- Self-check (backtest) screen: 60-day and 3-year replay headline numbers, by-year table, "other managements" comparison, with a plain disclaimer: "past replay results never predict future results".

### Analysis
- AI briefing (3–4 sentences) with a timestamp.
- Price action: H4 / H1 structure with BOS / CHoCH levels, regime, volatility percentile, divergence flag, last impulse with fib 50 / 61.8.
- Key levels ladder: previous-day high/low, previous-week high/low, Asia high/low, daily VWAP, round numbers, pivots — relative to current price with ± points.
- Technicals: EMA 20/50/200 (H1), EMA 50/200 (D1), RSI, MACD histogram, Bollinger %B, ATR; score ring meters.
- Macro: dollar index, US 10-year yield, S&P 500 with 5-day change and "+gold / −gold" tags.

### News
- Score header (−100…+100, label, bullish / bearish / neutral counts, top themes).
- Headlines: impact icon (🟢🟢 / 🟢 / ⚪ / 🔴 / 🔴🔴), title, source, age, and the one-line AI reason underneath.
- Calendar: next 48 h of USD releases with impact flame, time in the user's zone, countdown, forecast / previous / actual.

### Settings
- Account & risk: account size, currency (CAD default), risk % or fixed lots (0.05 default) → the app shows lot size and money risk on every call.
- Alerts: toggles for setups & live calls, TP/stop updates, bias changes, big moves, breaking news, pre-release warnings; quiet hours.
- Live Activity on/off; widgets explainer.
- Subscription: current plan, renewal date, Manage subscription (opens App Store), Restore purchases.
- Telegram link (optional): connect the same account to the Telegram bot.
- Legal: disclaimer, privacy, terms. About: version, data sources (OANDA spot ticks, COMEX bars rescaled to spot, Dukascopy history).

### Onboarding + paywall
- 3 short screens: what it does (decision support, not auto-trading), how a call works (setup → zone → 15-min confirmation → live call → TP1 / TP2 / stop), what the subscription includes.
- Paywall (StoreKit 2, Apple in-app purchase, with Restore Purchases and the mandatory links):
  - **Free**: bias and confidence delayed 15 minutes, key levels, news score, no calls, no push.
  - **Pro — $29.99 / month** or **$199 / year** (save 45 %), 7-day free trial: real-time price and dial, every setup and live call with push notifications, Live Activity, AI briefing, alerts, scorecard, backtest.
  - Show prices as placeholders the owner can change; include "cancel anytime", trial terms, and the analysis-only disclaimer.

### Notifications, Live Activity, widgets
- Push categories mirror the Telegram messages: NEW SETUP, ZONE REACHED, LIVE CALL, TP1 HIT, TP2 HIT, STOPPED OUT, BIAS CHANGED, BIG MOVE, HIGH-IMPACT NEWS, RELEASE IN 30 MIN. Each notification is one line of numbers, e.g. "📡 LIVE CALL · SELL 4,123.7 · SL 4,150.1 · TP1 4,116.8 · TP2 4,101.7".
- Live Activity (Dynamic Island + Lock Screen) while a call is live: side, entry, current price, P/L in R, a progress bar SL → entry → TP1 → TP2.
- Widgets: small (price + bias dial), medium (price, bias, next release, active call line).

## 4. Data the screens must display (real shapes from the backend)

The app talks to the existing backend over HTTPS; design with these fields, not invented ones:

- `GET /api/quote` → price, bid, ask, spread, change_1h_pct, change_24h_pct, change_7d_pct, source, age_sec.
- `GET /api/stream` (SSE) → ticks ~1 per second.
- `GET /api/report` → bias, combined, tech_score, news_score, confidence, label, zone_lo, zone_hi, chase_limit, stop, target1, target2, confluence[], lean_side, lean_plan{}, session{name,label,quality,allow_entries,next_change}, price_action{h4,h1,regime,adx,atr_pct,levels{PDH,PDL,PWH,PWL,ASIA_H,ASIA_L,VWAP}}, macro{label,score,parts}, briefing, briefing_at, news{score,label,n_bullish,n_bearish,n_neutral,top_themes,drivers[]}, events[].
- `GET /api/signals` → active{status: pending|active|tp1|tp2|stopped|flipped|expired|missed|invalidated|cancelled|stale, side, entry, sl, sl_current, tp1, tp2, zone_lo, zone_hi, chase, confidence, trigger_note, opened_at, triggered_at, result_r, exit_price}, history[], stats{count,wins,losses,breakeven,win_rate,avg_r,total_r,profit_factor,max_drawdown_r}, events[], sizing{account,currency,risk_pct,fixed_lots}.
- `GET /api/backtest` → summary, by_year, by_session, by_trigger, variants[], deep{…}.
- `GET /api/calendar` → events[{title,when,impact,forecast,previous,actual,minutes_away}].
- `GET /api/health` → data sources, tick rate, last bar time, backup state.

Numbers: prices with one decimal and thousands separators (4,123.7), R with two decimals and sign (+0.26R), money in the account currency (C$88), percentages without decimals for confidence (46 %). Times in the user's time zone with the UTC equivalent available on long-press.

## 5. States every screen needs

Loading (skeleton in the same glass panels), empty (first run, no calls yet), error (backend unreachable: show last known data with an "as of" stamp and a red LED), weekend (market closed banner, no new entries), free-tier locked content (blurred panel with a lock and a one-tap upgrade), Reduce Motion variants, Dynamic Type at the largest accessibility size, VoiceOver labels for the dial and every number.

## 6. Deliverables

1. A component sheet: tokens, type scale, logo mark, panel, chips, LED dots, bias dial, ring meter, ticker tape, timeline, signal card in its 5 states, level ladder row, headline row, calendar row, buttons (primary cyan→magenta gradient on hover/press, secondary hairline).
2. Screens for iPhone 15 Pro (393 × 852) and iPhone 15 Pro Max: Terminal (no call / setup / live call), Signals, Signal detail, Scorecard, Self-check, Analysis, News, Calendar, Settings, Risk, Alerts, Subscription, Paywall, Onboarding ×3, Live Activity (compact, expanded, Lock Screen), widgets (small, medium), notification examples.
3. Three user flows: first launch → trial → first live call; a call from setup to TP2 with the notifications along the way; subscription lapsed → paywall → restore.
4. App Store screenshot set (6) with short headlines in the same type system.

Tone of all copy: short, professional, numbers first, no hype, no exclamation marks. Every levels screen ends with the one-line disclaimer.
