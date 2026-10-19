"""
Build a single-file, server-less demo of the terminal.

It takes web/static/index.html, embeds a snapshot of the demo API responses,
and replaces the /api/* calls with an in-page simulator (random-walk ticks,
a moving tape, live countdowns).  The result runs anywhere a browser does:
open it from disk, drop it on GitHub Pages, or publish it as a hosted page.

    python tools/build_static_demo.py                       # uses a running demo server on :8080
    python tools/build_static_demo.py --from snapshots/     # uses saved report.json / tape.json / calendar.json
    python tools/build_static_demo.py --out dist/demo.html --bare   # --bare strips <html>/<head>/<body> for hosts that wrap pages
"""
from __future__ import annotations

import argparse
import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "web" / "static" / "index.html"
CDN = "https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"

MOCK = r"""
<script>
// ---- in-page simulator: replaces the FastAPI backend for the static demo ----
(() => {
  const SNAP = __SNAP__; const SNAPSHOT = __SNAPSHOT__;
  const real = window.fetch.bind(window);
  const now = () => Date.now() / 1000;
  let seed = 7; const rnd = () => { seed = (seed * 1664525 + 1013904223) % 4294967296; return seed / 4294967296; };
  const gauss = () => { let u = 0, v = 0; while (!u) u = rnd(); while (!v) v = rnd(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(6.283 * v); };

  // shift the candles so the last bar is the current hour
  const rep = SNAP.report, c = rep.candles, n = c.time.length;
  const lastHour = Math.floor(now() / 3600) * 3600, shift = SNAPSHOT ? 0 : lastHour - c.time[n - 1];
  c.time = c.time.map(t => t + shift);
  if (!SNAPSHOT) rep.bar_time = new Date(lastHour * 1000).toISOString();
  let generatedAt = new Date().toISOString();
  rep.demo = !SNAPSHOT;

  // bias history: a plausible walk ending at the current combined score
  const hist = []; let v = rep.signal.combined - 25 + rnd() * 10;
  for (let i = 0; i < 30; i++) { v += gauss() * 6 + (rep.signal.combined - v) * 0.12; hist.push({ combined: Math.round(Math.max(-100, Math.min(100, v))) }); }
  hist[hist.length - 1].combined = rep.signal.combined; rep.history = hist;

  // quotes: mean-reverting random walk around the snapshot price
  const anchor = rep.price.last; let price = anchor, prev = anchor * 0.9948, lastQ = 0;
  if (SNAPSHOT) { prev = c.close[n - 2] ?? anchor; }
  // live price pushed into the artifact's database (see tools/live_snapshot.py --push / the hourly refresh task)
  let liveQuote = null;
  if (SNAPSHOT && window.claude && window.claude.use) (async () => {
    try {
      const db = await window.claude.use('db'); if (!db) return;
      db.doc('live/quote').onSnapshot(snap => {
        const d = snap.data(); if (!d || typeof d.price !== 'number') return;
        liveQuote = d;
        const badge = document.getElementById('demo-badge');
        if (badge && d.updated_at) badge.textContent = 'LIVE · price ' + new Date(d.updated_at).toUTCString().slice(17, 22) + ' UTC · chart snapshot ' + new Date(SNAP.report.snapshot_at).toUTCString().slice(5, 22) + ' UTC';
      }, () => {});
    } catch (_) {}
  })();
  const quote = () => { if (SNAPSHOT) { const p = liveQuote ? liveQuote.price : anchor; return { price: p, prev_close: +prev.toFixed(2), change_pct: +((p / prev - 1) * 100).toFixed(3), ts: c.time[n - 1], stale: false, demo: false, snapshot: true }; } if (now() - lastQ > 0.8) { price = +(price + gauss() * 0.45 + (anchor - price) * 0.02).toFixed(2); lastQ = now(); } return { price, prev_close: +prev.toFixed(2), change_pct: +((price / prev - 1) * 100).toFixed(3), ts: now(), stale: false, demo: true }; };

  // tape: slow drift around the snapshot values
  const tapeBase = SNAP.tape.items.map(t => ({ ...t, base: t.price }));
  const tape = () => SNAPSHOT ? { items: SNAP.tape.items, demo: false } : ({ items: tapeBase.map(t => { t.price = +(t.price * (1 + gauss() * 0.0005)).toFixed(t.price < 10 ? 4 : 2); return { name: t.name, kind: t.kind, price: t.price, change_pct: +((t.price / t.base - 1) * 100 + t.change_pct).toFixed(2) }; }), demo: true });

  // calendar: keep the same offsets from "now" as when the snapshot was taken
  const t0 = Date.now();
  const evs = SNAP.calendar.events.map(e => ({ ...e, _off: e.minutes_away * 60000 }));
  const calendar = () => SNAPSHOT ? { events: SNAP.calendar.events.map(e => ({ ...e, minutes_away: (new Date(e.when).getTime() - Date.now()) / 60000 })), demo: false } : ({ events: evs.map(e => ({ ...e, when: new Date(t0 + e._off).toISOString(), minutes_away: (t0 + e._off - Date.now()) / 60000 })), demo: true });

  const json = (b) => new Response(JSON.stringify(b), { status: 200, headers: { 'Content-Type': 'application/json' } });
  window.fetch = async (url, opts) => {
    if (typeof url === 'string' && url.startsWith('/api/')) {
      const path = url.split('?')[0];
      if (path === '/api/refresh') { generatedAt = new Date().toISOString(); }
      if (path === '/api/report' || path === '/api/refresh') return json({ ...rep, generated_at: generatedAt, events: calendar().events.slice(0, 10), signals: SNAP.signals });
      if (path === '/api/quote') return json(quote());
      if (path === '/api/tape') return json(tape());
      if (path === '/api/calendar') return json(calendar());
      if (path === '/api/signals') return json({ ...SNAP.signals, demo: !SNAPSHOT });
      if (path === '/api/health') return json({ ok: true, demo: true });
    }
    return real(url, opts);
  };
  if (SNAPSHOT) document.addEventListener('DOMContentLoaded', () => {
    const at = new Date(SNAP.report.snapshot_at || Date.now());
    const badge = document.getElementById('demo-badge');
    if (badge) { badge.textContent = 'LIVE SNAPSHOT · ' + at.toUTCString().slice(5, 22) + ' UTC'; badge.classList.remove('hidden'); badge.title = 'Real market data captured at this time. Run the server for continuous live updates.'; }
    document.querySelectorAll('.live-tag').forEach(el => { el.innerHTML = '<i></i>SNAPSHOT'; });
    const h2 = document.querySelector('.chart-wrap') && document.querySelector('.chart-wrap').parentElement.querySelector('h2');
    if (h2) h2.firstChild.textContent = 'XAU/USD · D1 ';
    const map = { '1 hour': 'Today', '24 hours': '1 week', 'About a week': '1 month' };
    document.querySelectorAll('.delta .k').forEach(el => { if (map[el.textContent]) el.textContent = map[el.textContent]; });
    const tt = document.getElementById('ticktape'); if (tt) tt.innerHTML = '<div class="empty">Snapshot mode: live ticks appear when the server is running.</div>';
    const fix = () => document.querySelectorAll('.levels .row span:first-child').forEach(el => { el.textContent = el.textContent.replace('(H1)', '(D1)'); });
    setTimeout(fix, 1500); setTimeout(fix, 5000);
    const sp = document.getElementById('splash-msg'); if (sp) sp.textContent = 'loading live snapshot…';
  });
})();
</script>
"""


def fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310 - local demo server
        return json.load(r)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://localhost:8080", help="running demo server to snapshot")
    ap.add_argument("--from", dest="from_dir", help="folder with report.json, tape.json, calendar.json instead of a server")
    ap.add_argument("--out", default=str(ROOT / "dist" / "xau-sentinel-demo.html"))
    ap.add_argument("--bare", action="store_true", help="strip <!doctype>/<html>/<head>/<body> (for hosts that wrap the page)")
    ap.add_argument("--snapshot", action="store_true", help="real-data snapshot from tools/live_snapshot.py: no simulated ticks, real dates")
    args = ap.parse_args()

    if args.from_dir:
        d = Path(args.from_dir)
        snap = {k: json.loads((d / f"{k}.json").read_text()) for k in ("report", "tape", "calendar", "signals")}
    else:
        snap = {k: fetch_json(f"{args.server}/api/{k}") for k in ("report", "tape", "calendar", "signals")}

    html = SRC.read_text(encoding="utf-8")
    html = html.replace('<script src="/static/vendor/lightweight-charts.standalone.production.js"></script>', f'<script src="{CDN}"></script>')
    # favicons come from a third-party host; the demo has no real links anyway
    html = re.sub(r"const favicon = \(link\) => \{.*?\};\n", "const favicon = () => '';\n", html, flags=re.S)
    mock = MOCK.replace("__SNAP__", json.dumps(snap, separators=(",", ":"))).replace("__SNAPSHOT__", "true" if args.snapshot else "false")
    html = html.replace("<script>\n(() => {\n  const $ = ", mock + "<script>\n(() => {\n  const $ = ", 1)
    # safe-area aware gutters for phone hosts
    html = html.replace(".wrap { position: relative; z-index: 1; max-width: 1360px; margin: 0 auto; padding: 0 16px 60px; }",
                        ".wrap { position: relative; z-index: 1; max-width: 1360px; margin: 0 auto; padding-block: 0 60px; padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); }")
    html = html.replace(".toasts { position: fixed; right: 18px; top: 56px;", ".toasts { position: fixed; right: calc(18px + env(safe-area-inset-right, 0px)); top: calc(56px + env(safe-area-inset-top, 0px));")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if not args.snapshot:
        html = html.replace('<span id="demo-badge" class="badge demo hidden">Demo data</span>',
                            f'<span id="demo-badge" class="badge demo" title="Static demo built {stamp}: simulated ticks, sample headlines">Demo data</span>')

    if args.bare:
        html = re.sub(r"^\s*<!doctype html>\s*<html[^>]*>\s*<head>\s*", "", html, flags=re.I)
        html = html.replace('<meta charset="utf-8">\n', "").replace('<meta name="viewport" content="width=device-width, initial-scale=1">\n', "")
        html = html.replace("<title>XAU Sentinel</title>", '<title>XAU Sentinel</title>\n<meta name="claude:viewport-fit-sides" content="cover">')
        html = re.sub(r"</head>\s*<body>", "", html)
        html = re.sub(r"</body>\s*</html>\s*$", "", html)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
