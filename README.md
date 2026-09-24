# Marufi Digital guide site

A static storefront for Marufi Digital's digital guides and books, organized by country and state, for every country. Payments are taken with Stripe. The public site is in `dist/`; the optional backend (cart checkout, secure downloads, e-mail) is one Cloudflare Worker in `checkout/`.

## What the site does

- **Collection** with search, country filter, Guides / Books / Bundles / Saved chips, an Editor's pick spotlight, sale pricing with countdown, a quick-view dialog with editions (variants), sample pages, save-for-later hearts, and a compare tool (up to three titles side by side).
- **Atlas**: a world map with a pin for every covered state, plus a country list. Pick a country to see its states with the titles under each; states without a title show "Coming soon" and a notify-me button.
- **Zones**: a horizontal strip of the cities people are heading to, what makes each special, best time to go, and the guide that covers it.
- **Reviews**, **About the author**, FAQ with an optional refund policy, and a footer with accepted payment marks and "Resend my download".
- **Cart** with one-payment Stripe Checkout, gift orders, and a currency switcher for display prices.
- **Thank-you page** that shows secure, time-limited download links when the Worker is deployed.
- SEO: Product structured data is injected for every title; `robots.txt` and `sitemap.xml` are included (replace `YOUR-DOMAIN`). Optional cookie-free analytics via Plausible.

## Selling with Stripe

- **Buy now** opens that title's Stripe checkout. Simplest setup: a Stripe **Payment Link** per title, no server needed. Set the link's confirmation page to redirect to `https://your-domain.com/thank-you.html?session_id={CHECKOUT_SESSION_ID}`.
- **Checkout with Stripe** in the cart pays for everything at once. It needs the Worker deployed and a Stripe **Price ID** per title. Without the Worker, the cart shows a Buy with Stripe button per title instead.
- **Secure delivery**: with the Worker, buyers get time-limited download links on the thank-you page and by e-mail. Files live in a private R2 bucket; each Stripe product needs metadata `file` set to the object key (for example `vancouver.pdf`). Nothing is served from a public URL.

## Edit the catalog: `dist/guides.json`

Store-wide fields:

| Field | Purpose |
|---|---|
| `currency` | Base currency code, the one your Stripe prices use. |
| `currencies` | Codes offered in the display switcher, e.g. `["CAD","USD","EUR"]`. Add `prices` per title for each; missing ones fall back to the base price. |
| `checkoutEndpoint` | The Worker URL once deployed. Enables cart checkout, notify-me, resend, and secure downloads. |
| `siteUrl` | Your live URL, used in structured data. |
| `contactEmail`, `social` | Footer links. Leave empty to hide. |
| `refundPolicy` | Text for the "What if a guide is not what I expected?" FAQ. Empty hides it. |
| `author` | `name`, `role`, `photo`, `bio` (blank line = new paragraph), `note`. Section hides until `name` and `bio` are set. |
| `reviews` | `[{ "quote", "name", "place", "rating" }]`. Section hides when empty. |
| `zones` | `[{ "city", "country", "state", "tagline", "why", "bestTime", "knownFor": [], "image", "guideId" }]`. Section hides when empty. |
| `regions` | `{ "Canada": ["Alberta", ...] }`. Full state lists so the atlas can show "Coming soon" entries. Canada, US, Australia, Germany, Italy, Spain, France, UK, and Japan are pre-filled; add others as you publish. |
| `analytics.plausibleDomain` | Your domain in Plausible to enable analytics. |

Per title (required: `title`, `description`, numeric `price`, and `paymentLink` or `priceId`):

| Field | Purpose |
|---|---|
| `id` | Stable id used by bundles, zones, saved items, and the cart. |
| `type` | `Guide`, `Book`, or `Bundle`. |
| `country`, `state` | Filing place. `coordinates: [lat, lng]` places the pin on the atlas. |
| `cover`, `coverAlt`, `badge`, `featured` | Presentation. Portrait 4:5 covers look best. |
| `longDescription`, `highlights`, `format`, `pages`, `updated` | Shown in quick view and compare. |
| `prices` | `{ "USD": 14 }` display prices per currency. |
| `salePrice`, `saleEnds`, `salePrices` | Launch or sale pricing with a countdown badge. `saleEnds` is an ISO date. |
| `variants` | Editions: `[{ "label", "format", "price", "prices", "priceId", "paymentLink" }]`. `variantLabel` names the base edition. |
| `sample` or `samplePages` | A PDF (or single image) URL, or a list of page images, for "Read a sample". Put them in `dist/assets/samples/`. |
| `reviews` | Per-title quotes shown in quick view. |
| `includes` | For bundles: ids of the included titles. |

## The admin dashboard: `/admin/`

Open `https://your-domain.com/admin/` (locally, `http://localhost:8765/admin/`). It has two modes:

- **Connected.** Sign in with your Worker URL and the admin password. Everything is stored by the Worker: the live catalog (KV), images and product files (R2), orders (Stripe), reader requests, and backups. Publishing makes changes live within a minute; the storefront reads `<worker>/catalog` and falls back to `guides.json` when the Worker is unreachable.
- **Offline.** Click "Work offline with guides.json". The dashboard edits the catalog in your browser and downloads a new `guides.json` to commit. Uploads are not available offline; put files in `dist/assets/` and paste the path.

What it does:

| Section | What you can do |
|---|---|
| Overview | Stats, setup checklist, recent orders, quick actions. |
| Titles | Add, edit, duplicate, delete guides, books, and bundles. Every field the storefront uses, plus cover upload, sample pages, the private product file, editions, bundle contents, and per-title reviews. **Create product, price, and payment link in Stripe** fills the Stripe ids in one click. |
| Zones | Cities people are heading to. Drag to reorder. |
| Reviews | Home-page reader quotes with ratings. |
| Media & files | Upload images (public) and product files (private), copy URLs and keys, delete. |
| Orders | Paid checkouts for 7 to 365 days, revenue by currency, best sellers, CSV export. |
| Requests | "Notify me" requests by place, CSV export. |
| Announce | Email every buyer of a title (dry-run count first). |
| Store settings | Name, site URL, currencies, contact, social links, refund policy, analytics, author, and the state lists per country. |
| Backups & tools | Import or export `guides.json`, restore any earlier published version, discard the local draft. |

Unpublished edits are kept in the browser until you publish, so you can stop and continue later. A backup is stored every time you publish.

## Deploy the Worker

1. `npm i -g wrangler`, then `wrangler login`.
2. Create storage: `wrangler r2 bucket create marufi-files` and `wrangler kv namespace create STORE`. Paste the KV id into `checkout/wrangler.toml`.
3. In `checkout/wrangler.toml` set `SITE_URL`, `SUCCESS_URL`, `CANCEL_URL`, `ALLOWED_ORIGINS`, `FROM_EMAIL`, `CONTACT_EMAIL`.
4. From `checkout/`, set secrets: `wrangler secret put STRIPE_SECRET_KEY`, `wrangler secret put ADMIN_PASSWORD`, `wrangler secret put DOWNLOAD_SECRET` (any long random string), and optionally `RESEND_API_KEY` (e-mail via resend.com) and `ADMIN_TOKEN` (for scripts).
5. `wrangler deploy`, then put the Worker URL in `dist/guides.json` as `checkoutEndpoint` and push. Sign in at `/admin/` with that URL and the password.

To try the Worker locally without deploying: `node checkout/dev-server.mjs` starts it on `http://localhost:8787` with in-memory storage (password `letmein`, override with `ADMIN_PASSWORD=...`). Stripe and e-mail calls need real keys in the environment; everything else works offline.

## Preview locally

Serve the site over HTTP; opening `index.html` as a file blocks the catalog. For example `python3 -m http.server 8765 -d dist` then open `http://localhost:8765/`.

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
