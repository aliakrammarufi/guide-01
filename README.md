# Marufi Digital guide site

This project is a static storefront for Marufi Digital's digital guides and books, organized by country and state. Payments are taken with Stripe. The public site files are in `dist/`; the optional cart-checkout endpoint is in `checkout/`.

## How selling works

- **Buy now** on a card opens that title's Stripe checkout. The simplest setup is a Stripe **Payment Link** per title: no server is needed, and the visitor lands back on `thank-you.html` if you set the link's confirmation page to redirect there.
- **Add to cart** keeps titles in the visitor's browser. **Checkout with Stripe** in the cart pays for everything in one Stripe Checkout session. That needs the small endpoint in `checkout/worker.js` deployed (see below) and a Stripe **Price ID** on each title. Without the endpoint, the cart still works and shows a Buy with Stripe button per title.
- **Delivery** is up to your Stripe setup. Stripe emails the receipt; add your download link to the Payment Link's confirmation page or send it with a Stripe post-purchase email or webhook. The thank-you page tells buyers to check their inbox.

## Add real guides and books

Edit `dist/guides.json`:

```json
{
  "storeName": "Marufi Digital",
  "currency": "CAD",
  "checkoutEndpoint": "https://marufi-checkout.your-account.workers.dev",
  "contactEmail": "hello@example.com",
  "social": { "instagram": "https://instagram.com/your-handle", "tiktok": "", "youtube": "" },
  "guides": [
    {
      "id": "bc-vancouver",
      "type": "Guide",
      "country": "Canada",
      "state": "British Columbia",
      "title": "Vancouver and the Sea-to-Sky",
      "description": "One or two sentences that say what the buyer receives.",
      "longDescription": "A fuller paragraph shown in the quick view and the featured spotlight.",
      "highlights": ["Neighbourhood-by-neighbourhood map", "Day-by-day Whistler drive", "Where to eat late"],
      "format": "PDF · 84 pages",
      "price": 19,
      "badge": "New",
      "featured": true,
      "cover": "assets/covers/bc-vancouver.webp",
      "coverAlt": "Cover of Vancouver and the Sea-to-Sky",
      "priceId": "price_1ABC...",
      "paymentLink": "https://buy.stripe.com/..."
    }
  ]
}
```

Required for each title: `title`, `description`, a numeric `price`, and at least one of `paymentLink` (a Stripe Payment Link URL) or `priceId` (a Stripe Price ID, which only works once `checkoutEndpoint` is set). Titles missing any of these are skipped.

Optional fields:

- `type`: `Guide` or `Book`. Defaults to `Guide`. The Guides / Books filter appears once both types exist.
- `country` and `state`: recommended. Books that are not tied to a place can leave them out.
- `cover`: a file inside `dist/assets/` (for example `assets/covers/name.webp`) or an HTTPS URL. Portrait 4:5 around 800 x 1000 pixels looks best. Titles without a cover get a typographic cover generated from the country and state.
- `coverAlt`, `longDescription`, `highlights` (up to six short lines), `format` (shown in the quick view), `badge` (short label on the cover such as `New` or `Bestseller`).
- `featured: true` on one title shows it as the Editor's pick spotlight above the grid.

Store-wide: `currency` is the ISO code used to format prices (Stripe charges whatever the Price or Payment Link is set to, so keep them consistent). `contactEmail` adds an Email us link to the footer and thank-you page. `social` adds Follow links; leave a value empty to hide it.

## Deploy the cart checkout endpoint (optional)

`checkout/worker.js` is a dependency-free Cloudflare Worker that creates one Stripe Checkout Session for the Price IDs in the cart.

1. Install Wrangler (`npm i -g wrangler`) and sign in with `wrangler login`.
2. In `checkout/wrangler.toml`, set `SUCCESS_URL`, `CANCEL_URL`, and `ALLOWED_ORIGINS` to your real domain. Optionally list the Price IDs you sell in `ALLOWED_PRICES`.
3. From the `checkout/` folder run `wrangler secret put STRIPE_SECRET_KEY` and paste your Stripe secret key, then `wrangler deploy`.
4. Put the Worker URL in `dist/guides.json` as `checkoutEndpoint`.

The secret key never leaves the Worker. The site only ever sends Price IDs to it.

## Preview locally

Open the site through an HTTP server; opening `index.html` as a file prevents the catalog from loading. For example, run `python3 -m http.server 8765 -d dist` and visit `http://localhost:8765/`.

## Let Codex and Claude work together

In a normal Terminal tab, run:

```bash
cd /Users/alimarufi/Documents/marufidigital-web
./collab.sh "Describe the project goal you want completed"
```

Codex creates a plan of up to five tasks. For each task, Codex implements, Claude reviews and fixes, and Codex verifies. When a task is complete, the runner starts the next one. It stops when all tasks are done, an agent needs your input, a command fails, or an individual agent call takes longer than one hour. The wrapper keeps a Mac awake while it runs.

Use `./collab.sh --max-tasks 8 "..."` for a larger objective, or `./collab.sh --resume --note "Here is the missing information"` after addressing a stopped run. Each run's plan, handoffs, logs, and status are saved in `.ai-handoff/`. The runner permits only one active run in this project at a time.

The runner launches new non-interactive Codex and Claude sessions. It does not type into the two interactive chats you already have open. Avoid editing the same project in those chats while the runner is active.

The agents can edit this project, but the runner does not give Codex user-configured browser/desktop tools or network access, and Claude has file tools only. Changes from each completed task are committed locally; nothing is pushed or deployed. A new run requires a clean Git working tree. Review the result before publishing. The Claude review phase has a $10 per-call budget cap; reaching it stops the run. This automation uses your existing CLI sign-ins and their usage limits. Run one real objective while you are present before relying on the unattended watcher.

### Keep a queue running while you are away

Leave this command running in a normal Terminal tab on the Mac Mini:

```bash
./collab.sh --watch
```

Add a new project objective from another normal Terminal tab:

```bash
./collab.sh --queue "Add the real guide listings and check the catalog"
```

The watcher picks up queued objectives in order. Each objective gets its own Codex plan and the same Codex → Claude → Codex phases. Finished tasks move to `automation/completed/`; tasks that need attention move to `automation/paused/`. If an objective pauses or fails, the watcher stops rather than starting the next one on a half-edited project. Logs and detailed handoffs remain in `.ai-handoff/`. Press Ctrl+C in the watcher tab to stop it. The watcher does not invent new objectives after the queued work is finished; it waits for another task.

Use `./collab.sh --status` to see whether the watcher is running, or `./collab.sh --stop-watch` to ask it to stop after its current objective. Keep the Terminal tab open while you are away. macOS does not allow a login service to read this project from Documents without extra privacy access, so the watcher is intentionally run from Terminal.
