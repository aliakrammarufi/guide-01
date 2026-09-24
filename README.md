# Marufi Digital guide site

A static storefront for Marufi Digital's digital guides and books, organized by country and state, for every country. Payments are taken with Stripe. The public site is in `dist/`; the optional backend (cart checkout, secure downloads, e-mail) is one Cloudflare Worker in `checkout/`.

## What the site does

Marufi Digital is a place-first library: travel guides, books, immigration and settling-in help, restaurant and food lists, checklists, and bundles, every one filed by country and state. The home page opens with **Browse by need** (one tile per kind of help, with counts, or "coming soon" and a link to the request form), then the library with filter chips per kind, the atlas, zones, and a **Free help** section for resources given away without checkout.

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
| `categories` | Optional overrides for the kinds of help: `[{ "key", "name", "single", "format", "blurb", "icon", "hidden" }]`. Built-in keys: `Guide`, `Book`, `Immigration`, `Food`, `Checklist`, `Bundle`. Add a new key to create a kind; icons: compass, book, passport, fork, check, layers. |
| `articles` | Journal entries: `[{ "title", "slug", "date", "status": "draft" \| "published", "category", "place", "summary", "cover", "body" }]`. Body supports `## `, `- `, `> `, `**bold**`, `*italic*`, and links. |
| `giftCards` | `{ "enabled": true, "amounts": [25, 50, 100] }`. |
| `analytics` | `plausibleDomain`, `ga4`, or `cloudflareToken`. |
| `resources` | Free help shown without checkout: `[{ "title", "summary", "category", "place", "kind": "Read" \| "Download" \| "Link", "url" }]`. Section shows "coming soon" when empty. |
| `regions` | `{ "Canada": ["Alberta", ...] }`. Full state lists so the atlas can show "Coming soon" entries. Canada, US, Australia, Germany, Italy, Spain, France, UK, and Japan are pre-filled; add others as you publish. |
| `analytics.plausibleDomain` | Your domain in Plausible to enable analytics. |

Per title (required: `title`, `description`, numeric `price`, and `paymentLink` or `priceId`):

| Field | Purpose |
|---|---|
| `id` | Stable id used by bundles, zones, saved items, and the cart. |
| `type` | The kind of help: `Guide`, `Book`, `Immigration`, `Food`, `Checklist`, `Bundle`, or a custom category key. Drives the kicker, the format label, the filter chips, and "Browse by need". |
| `tags` | Optional search words, e.g. `["work permit", "PR"]`. |
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

Open `https://your-domain.com/admin/` (locally, `http://localhost:8765/admin/`). Two modes:

- **Connected.** Sign in with your Worker URL and the admin password (plus a six-digit e-mail code when two-step sign-in is on). Everything is stored by the Worker: the live catalog (KV), images and product files (R2), orders and coupons (Stripe), reader requests, backups, the activity log. Publishing makes changes live within a minute; the storefront reads `<worker>/catalog` and falls back to `guides.json` when the Worker is unreachable.
- **Offline.** "Work offline with guides.json" edits the catalog in your browser and downloads a new `guides.json` to commit. Uploads and Stripe actions are not available offline.

| Section | What you can do |
|---|---|
| Overview | Stats, setup checklist, store health check (prices out of sync with Stripe, missing files or covers), recent orders. |
| Titles | Add, edit, duplicate, delete. Sort, filter by type and status, paged at 25. Select rows for bulk actions (publish, draft, change price by %, set a currency price, badge, delete). CSV export and import. The editor has a live card preview, every storefront field, status (published, draft, scheduled with a go-live time), pre-order with release date, cover upload with 4:5 crop and resize, sample pages, the private product file, editions, bundle contents, per-title reviews, one-click Stripe product creation, and "email download links to buyers" for shipping pre-orders. `Cmd/Ctrl+S` saves. |
| Zones, Reviews | Searchable lists; drag zones to reorder. |
| Media & files | Upload with a progress bar; images are resized to WebP in the browser first. Copy URLs and keys, delete. |
| Orders | 7 to 365 days, revenue by currency, best sellers, refund with one click, refunded orders flagged, CSV export. |
| Coupons | Create Stripe coupons with a promotion code, percent or amount off, max redemptions, expiry. List and delete. |
| Requests | "Notify me" requests. Pick a title and e-mail everyone who asked for that state or country; sent requests clear themselves. |
| Announce | E-mail every buyer of a title, optionally with fresh download links. |
| Store settings | Name, site URL, currencies, contact, refund policy, analytics, multi-title cart discount (creates the Stripe coupon), country pages, social links, author, security (change password, two-step sign-in), e-mail templates, state lists per country. |
| Backups & tools | Import/export `guides.json`, restore any published version, activity log, discard the local draft. |

**Password recovery.** "Forgot password?" on the sign-in screen e-mails a reset code to the Worker's `CONTACT_EMAIL` when e-mail is configured. Without e-mail, reset by running `wrangler secret put ADMIN_PASSWORD` and deleting the KV key `auth:password`.

## Kind pages

Every kind of help has its own page: `/travel-guides/`, `/books/`, `/immigration/`, `/food/`, `/checklists/` (and `/bundles/` once a bundle exists; custom kinds get a slug from their name). The "Browse by need" tiles link to them. Each page is a small static shell written by `node scripts/build-pages.mjs` and filled in the browser by `dist/kind.js` from the same catalog as the home page, so publishing from the admin updates them instantly with no rebuild. They have a search box, a country filter, Buy and Add to cart (the cart is shared with the home page), free help filed under that kind, and links to the other kinds. Rebuild only when you rename a kind or add one in Store settings.

## Secondary pages

`node scripts/build-pages.mjs` also writes these, all filled in the browser by `dist/page.js` from the same catalog:

| Page | What it does |
|---|---|
| `/privacy/`, `/terms/` | Privacy policy and terms of sale written for how this store works (Stripe, downloads, newsletter, gift cards, Canada and EU rights). Review them with a lawyer before launch; the refund paragraph comes from `refundPolicy` when set. |
| `/contact/` | Contact form. With the Worker it stores the message (Requests → Messages in the admin) and e-mails CONTACT_EMAIL; without it, it opens the visitor's mail app with `contactEmail`. |
| `/account/` | Customer account and dashboard. Passwordless: create an account or sign in with a six-digit e-mail code, then a 30-day session. Overview, downloads (fresh signed links each visit), orders, saved titles (synced with the heart button on the home page), gift cards bought or received, place requests, and profile (name, country, newsletter, close account). Needs the Worker and e-mail; locally, `dev-server.mjs` prints codes to its console. |
| `/gift/` | Gift cards. Stripe Checkout for a chosen amount; on payment the Worker creates a single-use promotion code, e-mails it to the recipient (and the buyer), and shows it on the thank-you page. Amounts and on/off live in Store settings. |
| `/articles/` | The journal. Articles are written in the admin (Journal view) with a small Markdown subset; the latest three appear on the home page. `/articles/?a=<slug>` works immediately; rerun the build for clean `/articles/<slug>/` URLs. |
| `404.html` | On-brand not-found page (served automatically by GitHub Pages and Cloudflare Pages). Its links are absolute, based on `siteUrl`, because the host serves it at any missing path. |

Every page (home, kind pages, country pages, and the secondary pages) shares one footer with the newsletter form (double opt-in when e-mail is configured; Newsletter view in the admin lists subscribers, exports CSV, and sends a plain-text newsletter with unsubscribe links).

## Analytics

Store settings → Analytics takes any of: a Plausible domain (cookieless), a Google Analytics 4 measurement id (loaded with IP anonymisation), or a Cloudflare Web Analytics token (cookieless). Leave all empty for no analytics. The privacy policy describes both options.

## E-mail deliverability

The Worker sends through Resend from `FROM_EMAIL`. For receipts and download links to reach inboxes, the sending domain needs three DNS records, all shown in the Resend dashboard after you add the domain there:

1. **SPF**: a TXT record on `send.<your-domain>` with `v=spf1 include:amazonses.com ~all`, plus the MX record Resend lists next to it.
2. **DKIM**: a TXT record at `resend._domainkey.<your-domain>` with the key Resend gives you.
3. **DMARC**: a TXT record at `_dmarc.<your-domain>` with `v=DMARC1; p=none; rua=mailto:<your contact address>`.

Overview → Health in the admin checks these records live and tells you which are missing. Use a subdomain such as `orders@mail.your-domain.com` if you prefer to keep your main domain's records untouched.

## Social share image

`dist/assets/share.jpg` (1200×630) is referenced from every page's Open Graph and Twitter tags. Replace it with your own artwork at the same size; keep the file name or update `og:image` in `index.html` and `scripts/build-pages.mjs`.

## Country landing pages

`node scripts/build-pages.mjs` writes one static page per country (`dist/canada/index.html` and so on) with the state list, the titles, and Product structured data, plus `sitemap.xml` and `robots.txt` using the `siteUrl` from the catalog. It reads the live catalog from the Worker when `checkoutEndpoint` is set. Run it after publishing new countries and push the result. Turn on "Link to country landing pages" in Store settings so the storefront links to them. Drafts and unreleased scheduled titles are excluded.

## Home page video (UGC presenter)

Set `heroVideo` in the catalog (Store settings → Home page video in the admin, or by hand in `guides.json`) and the hero photo becomes a framed video player:

```json
"heroVideo": {
  "src": "assets/hero.mp4",
  "poster": "assets/hero-poster.webp",
  "aspect": "9 / 16",
  "tag": "Watch",
  "playLabel": "Play with sound",
  "captionLeft": "Film 01",
  "captionRight": "Meet the guides, 45 seconds",
  "autoplay": true,
  "loop": true
}
```

The clip plays silently as a living poster (never when the visitor prefers reduced motion), and the big button restarts it with sound. Controls cover pause, mute, and elapsed time. Export from zeely.app as MP4 (H.264, AAC), portrait, ideally under 30 MB; put it in `dist/assets/` or upload it from the admin, which stores it in R2 and serves it from the Worker. Remove `heroVideo` (or clear it in the admin) to show the photo again.

## Selling features

- **Related titles** appear in every quick view: bundles that include the title, then other titles from the same country, then the same format.
- **Multi-title discount.** Set a percent and minimum in Store settings and click "Create the coupon in Stripe". The cart shows "add one more for 15% off" and the Worker applies the coupon automatically at checkout. Stripe does not allow promotion codes on the same session when an automatic discount applies.
- **Pre-orders.** Tick "Pre-order" and set a release date. The card and quick view say Pre-order, the thank-you page shows "Reserved" instead of a download, and no file is served until you attach one and click "Email download links to everyone who bought this" in the editor (or Announce with links).
- **Deep links.** `/?country=Canada&state=Alberta`, `/?type=Immigration`, and `/?q=tokyo` open the library pre-filtered.
- **Kinds of help.** Store settings → Kinds of help renames the categories, edits the card wording, hides one, or adds a new one. Titles pick their kind in the editor.
- **Free help.** The Free help view manages resources: a title, a kind, an optional place, and a URL. PDFs upload as public media (up to 12 MB) so they open without a signed link.

## Scroll animation

The home page uses GSAP 3.15 with ScrollTrigger and Lenis smooth scrolling, loaded from jsDelivr with Subresource Integrity, no build step. Everything is progressive enhancement: with JavaScript off, a blocked CDN, or `prefers-reduced-motion`, every element is visible and static.

- **Everywhere:** an arrival curtain once per session, masked word reveals, staggered card entrances with depth, icon draws, the atlas land wave and pins, dark-section wipes, a reading-progress line.
- **Large screens with a mouse only:** the hero pins while the film grows to fill the screen, Browse by need becomes a horizontal gallery, the statement fills word by word, steps draw in sequence, ghost numerals drift behind sections, the header hides while you read down and returns when you scroll up, buttons are gently magnetic, cards tilt a few degrees, and a thin cursor ring follows the pointer.
- **Turn parts off:** delete the `#arrival` block in `index.html` to drop the curtain; set `SMOOTH_SCROLL = false` in `site.js` to keep native scrolling; the cinematic functions are called one per line at the top of `setupScrollAnimation` in `site.js`, so remove any you do not want.

## The deployed Worker (done on September 24, 2026)

- Address: `https://marufi-checkout.marufidigital.workers.dev` (account subdomain `marufidigital`), set as `checkoutEndpoint` in `dist/guides.json`.
- Storage: the `STORE` KV namespace (id in `checkout/wrangler.toml`). Files live in KV too (25 MB per file, 1 GB total free) until R2 is enabled; see the comment in `wrangler.toml` to switch.
- Secrets set: `DOWNLOAD_SECRET`, `ADMIN_PASSWORD`. Still to set: `STRIPE_SECRET_KEY` (checkout, gift cards, orders), `RESEND_API_KEY` plus a real `FROM_EMAIL` and `CONTACT_EMAIL` in `wrangler.toml` (receipts, download links, sign-in codes, newsletter). Run `cd checkout && npx wrangler secret put STRIPE_SECRET_KEY`, then `npx wrangler deploy` after editing `wrangler.toml`.
- Public `GET /status` tells the storefront which of these are connected, so pages show an honest notice instead of a form that cannot succeed.

## Deploy the Worker

1. `npm i -g wrangler`, then `wrangler login`.
2. Create storage: `wrangler r2 bucket create marufi-files` and `wrangler kv namespace create STORE`. Paste the KV id into `checkout/wrangler.toml`.
3. In `checkout/wrangler.toml` set `SITE_URL`, `SUCCESS_URL`, `CANCEL_URL`, `ALLOWED_ORIGINS`, `FROM_EMAIL`, `CONTACT_EMAIL`.
4. From `checkout/`, set secrets: `wrangler secret put STRIPE_SECRET_KEY`, `wrangler secret put ADMIN_PASSWORD`, `wrangler secret put DOWNLOAD_SECRET` (any long random string), and optionally `RESEND_API_KEY` (e-mail via resend.com) and `ADMIN_TOKEN` (for scripts).
5. `wrangler deploy`, then put the Worker URL in `dist/guides.json` as `checkoutEndpoint` and push. Sign in at `/admin/` with that URL and the password.

To try the Worker locally without deploying: `node checkout/dev-server.mjs` starts it on `http://localhost:8787` with in-memory storage (password `letmein`, override with `ADMIN_PASSWORD=...`). Stripe and e-mail calls need real keys in the environment; everything else works offline.

## Live site on Vercel

The site is served from **https://marufi-digital.vercel.app** (Vercel project `marufi-digital`, static output from `dist/`, config in `vercel.json`). Deploy with `npx vercel deploy --prod` from the repo root, or connect the GitHub repository in the Vercel dashboard so every push to `main` deploys itself. `siteUrl` in `guides.json` is set to that address; change it (and `checkout/wrangler.toml`) when you attach your own domain. Vercel's free Hobby plan is for non-commercial use, so move to Pro before real sales.

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
