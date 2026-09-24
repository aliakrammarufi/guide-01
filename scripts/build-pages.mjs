/**
 * Generates a static landing page per country (dist/<country-slug>/index.html), a page per kind of help
 * (dist/travel-guides/, books/, immigration/, food/, checklists/, ... filled at runtime by kind.js), the secondary pages
 * (privacy, terms, contact, account, gift, articles, 404.html, filled by page.js), plus sitemap.xml and robots.txt.
 * Reads dist/guides.json, and when checkoutEndpoint is set, the live catalog published from the admin dashboard.
 * Run: node scripts/build-pages.mjs
 */
import { readFile, writeFile, mkdir, readdir, rm } from 'node:fs/promises';
import path from 'node:path';

const root = path.resolve(new URL('..', import.meta.url).pathname);
const dist = process.env.DIST ? path.resolve(process.env.DIST) : path.join(root, 'dist');
const local = JSON.parse(await readFile(path.join(dist, 'guides.json'), 'utf8'));
let catalog = local;
// Mirrors DEFAULT_CATEGORIES in site.js and kind.js; guides.json's `categories` array overrides wording or adds kinds.
const DEFAULT_CATEGORIES = [
  { key: 'Guide', slug: 'travel-guides', name: 'Travel guides', single: 'Travel guide', format: 'Digital guide', icon: 'compass', blurb: 'One place, read in one sitting: where to stay, what to skip, and how to move around.' },
  { key: 'Book', slug: 'books', name: 'Books', single: 'Book', format: 'Digital book', icon: 'book', blurb: 'Longer reads on a country or a region, for the flight over or the year after.' },
  { key: 'Immigration', slug: 'immigration', name: 'Immigration and settling in', single: 'Immigration help', format: 'Digital help guide', icon: 'passport', blurb: 'Permits, paperwork, housing, banking, and the first weeks, in the order you will meet them.' },
  { key: 'Food', slug: 'food', name: 'Restaurant and food lists', single: 'Restaurant list', format: 'Digital list', icon: 'fork', blurb: 'Short, curated lists of where to eat, what to order, and when to go.' },
  { key: 'Checklist', slug: 'checklists', name: 'Checklists and templates', single: 'Checklist', format: 'Digital checklist', icon: 'check', blurb: 'Packing lists, moving timelines, budget sheets, and the templates that save a week.' },
  { key: 'Bundle', slug: 'bundles', name: 'Bundles', single: 'Bundle', format: '', icon: 'layers', blurb: 'Several titles for one place, priced together.' }
];
const ICONS = {
  compass: '<circle cx="12" cy="12" r="9.5"/><path d="M15.5 8.5 13.6 13.6 8.5 15.5l1.9-5.1z"/>',
  book: '<path d="M4 5.5A1.5 1.5 0 0 1 5.5 4H11a2 2 0 0 1 2 2v13.5a1.5 1.5 0 0 0-1.5-1.5H4z"/><path d="M20 5.5A1.5 1.5 0 0 0 18.5 4H13a2 2 0 0 0-2 2v13.5a1.5 1.5 0 0 1 1.5-1.5H20z"/>',
  passport: '<rect x="5" y="3" width="14" height="18" rx="2"/><circle cx="12" cy="10" r="3"/><path d="M8.5 17h7"/>',
  fork: '<path d="M7 3v7a2.5 2.5 0 0 0 5 0V3M9.5 3v18"/><path d="M17 3c-2 1.5-2.5 4-2.5 7.5H17V21"/>',
  check: '<rect x="4" y="3.5" width="16" height="17" rx="2"/><path d="m8 12 2.5 2.5L16 9"/>',
  layers: '<path d="m12 4 8 4.5-8 4.5-8-4.5z"/><path d="m4 13 8 4.5 8-4.5"/><path d="m4 16.5 8 4.5 8-4.5"/>'
};
const slugOf = (t) => String(t || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
const custom = (Array.isArray(local.categories) ? local.categories : []).filter((c) => c && typeof c.key === 'string' && typeof c.name === 'string');
const categories = DEFAULT_CATEGORIES.map((d) => ({ ...d, ...(custom.find((c) => c.key.toLowerCase() === d.key.toLowerCase()) || {}), key: d.key, slug: d.slug }));
for (const c of custom) if (!categories.some((m) => m.key.toLowerCase() === c.key.toLowerCase())) categories.push({ single: c.name, format: 'Digital download', icon: 'layers', blurb: '', slug: slugOf(c.name), ...c });
const kindOf = (g) => (categories.find((c) => c.key.toLowerCase() === String(g.type || 'Guide').toLowerCase()) || categories[0]).single;
const endpoint = typeof local.checkoutEndpoint === 'string' ? local.checkoutEndpoint.replace(/\/session\/?$/, '').replace(/\/+$/, '') : '';
if (endpoint) {
  try {
    const live = await fetch(`${endpoint}/catalog`).then((r) => (r.ok ? r.json() : null));
    if (live && Array.isArray(live.guides)) { catalog = { ...local, ...live }; console.log('Using the live catalog from the Worker.'); }
  } catch { console.log('Worker unreachable, using dist/guides.json.'); }
}

const siteUrl = String(catalog.siteUrl || '').replace(/\/+$/, '');
const storeName = catalog.storeName || 'Marufi Digital';
const currency = catalog.currency || 'CAD';
const slug = (t) => String(t || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
const esc = (t) => String(t ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const money = (n) => { try { return new Intl.NumberFormat('en', { style: 'currency', currency, minimumFractionDigits: Number.isInteger(n) ? 0 : 2 }).format(n); } catch { return `${currency} ${n}`; } };
const visible = (g) => g && g.title && Number.isFinite(Number(g.price)) && (g.paymentLink || g.priceId) && g.status !== 'draft' && !(g.status === 'scheduled' && g.publishAt && Date.parse(g.publishAt) > Date.now());
const guides = (catalog.guides || []).filter(visible);
const countries = [...new Set(guides.map((g) => g.country).filter(Boolean))].sort();

// Remove previously generated country folders (marked by a generator comment) so renamed countries do not linger.
for (const entry of await readdir(dist, { withFileTypes: true })) {
  if (!entry.isDirectory() || ['assets', 'admin'].includes(entry.name)) continue;
  try {
    const html = await readFile(path.join(dist, entry.name, 'index.html'), 'utf8');
    if (html.includes('<!-- generated by scripts/build-pages.mjs -->')) await rm(path.join(dist, entry.name), { recursive: true });
  } catch { /* not a generated page */ }
}

function cover(g) {
  if (g.cover) return `<img src="${esc(/^https?:/.test(g.cover) ? g.cover : `../${g.cover}`)}" alt="${esc(g.coverAlt || `Cover of ${g.title}`)}" loading="lazy" />`;
  return `<div class="cover-art" data-tone="${['clay', 'pine', 'ink', 'gold'][Math.abs(g.title.length) % 4]}"><span>${esc(g.country || kindOf(g))}</span><strong>${esc(g.state || g.title)}</strong></div>`;
}

function page(country) {
  const inCountry = guides.filter((g) => g.country === country);
  const known = Array.isArray((catalog.regions || {})[country]) ? catalog.regions[country] : [];
  const states = [...new Set([...known, ...inCountry.map((g) => g.state).filter(Boolean)])].sort();
  const covered = states.filter((s) => inCountry.some((g) => g.state === s));
  const title = `${country} travel guides and books by state · ${storeName}`;
  const description = `${inCountry.length} digital ${inCountry.length === 1 ? 'title' : 'titles'} for ${country}, organized by state: ${covered.slice(0, 6).join(', ')}${covered.length > 6 ? ' and more' : ''}. Secure checkout with Stripe, instant download.`;
  const url = siteUrl ? `${siteUrl}/${slug(country)}/` : '';
  const jsonld = { '@context': 'https://schema.org', '@type': 'CollectionPage', name: title, description, url: url || undefined, isPartOf: siteUrl ? { '@type': 'WebSite', name: storeName, url: siteUrl } : undefined,
    mainEntity: { '@type': 'ItemList', itemListElement: inCountry.map((g, i) => ({ '@type': 'ListItem', position: i + 1, item: { '@type': 'Product', name: g.title, description: g.description, offers: { '@type': 'Offer', price: g.salePrice || g.price, priceCurrency: currency, availability: 'https://schema.org/InStock', url: g.paymentLink || undefined } } })) } };
  const stateBlocks = states.map((s) => {
    const titles = inCountry.filter((g) => g.state === s);
    if (!titles.length) return `<li class="region is-empty"><div class="region-head"><strong>${esc(s)}</strong><span>Coming soon</span></div><a class="text-link" href="../?country=${encodeURIComponent(country)}#atlas">Ask to be notified <span aria-hidden="true">→</span></a></li>`;
    return `<li class="region" id="${slug(s)}"><div class="region-head"><strong>${esc(s)}</strong><span>${titles.length} ${titles.length === 1 ? 'title' : 'titles'}</span></div><ul class="region-titles">${titles.map((g) => `<li><a class="link-button" href="#${slug(g.id)}">${esc(g.title)}</a><span class="region-type"> ${esc(kindOf(g))}</span><span class="region-price">${money(Number(g.salePrice || g.price))}</span></li>`).join('')}</ul></li>`;
  }).join('');
  const cards = inCountry.map((g) => `
        <article class="guide-card" id="${slug(g.id)}">
          <a class="card-cover" href="../?country=${encodeURIComponent(country)}&q=${encodeURIComponent(g.title)}#guides">${cover(g)}${g.badge ? `<span class="card-badge">${esc(g.badge)}</span>` : ''}</a>
          <div class="card-body">
            <div class="card-meta"><span class="card-kicker">${esc(kindOf(g))} · ${esc([g.country, g.state].filter(Boolean).join(' / '))}</span><span class="card-price">${money(Number(g.salePrice || g.price))}</span></div>
            <h3>${esc(g.title)}</h3>
            <p>${esc(g.description)}</p>
            ${g.highlights && g.highlights.length ? `<ul class="quick-highlights">${g.highlights.slice(0, 4).map((h) => `<li>${esc(h)}</li>`).join('')}</ul>` : ''}
            <div class="card-actions">
              ${g.paymentLink ? `<a class="button button-sm" href="${esc(g.paymentLink)}">${g.preorder ? 'Pre-order' : 'Buy now'} <span aria-hidden="true">→</span></a>` : `<a class="button button-sm" href="../?country=${encodeURIComponent(country)}#guides">Buy now <span aria-hidden="true">→</span></a>`}
              <a class="button-ghost" href="../?country=${encodeURIComponent(country)}&q=${encodeURIComponent(g.title)}#guides">Details</a>
            </div>
          </div>
        </article>`).join('');
  return `<!doctype html>
<!-- generated by scripts/build-pages.mjs -->
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta name="theme-color" content="#14201c" />
    <title>${esc(title)}</title>
    <meta name="description" content="${esc(description)}" />
    ${url ? `<link rel="canonical" href="${esc(url)}" />` : ''}
    <meta property="og:type" content="website" />
    <meta property="og:title" content="${esc(title)}" />
    <meta property="og:description" content="${esc(description)}" />
    ${siteUrl ? `<meta property="og:image" content="${esc(siteUrl)}/assets/travel-planning.webp" />` : ''}
    <link rel="icon" href="../favicon.svg" type="image/svg+xml" />
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,400;0,500;0,600;1,400;1,500&family=DM+Sans:opsz,wght@9..40,400;9..40,500;9..40,600;9..40,700&display=swap" rel="stylesheet" />
    <link rel="stylesheet" href="../styles.css" />
    <script type="application/ld+json">${JSON.stringify(jsonld)}</script>
    <style>
      .country-hero { padding: clamp(3rem, 6vw, 5rem) var(--gutter) clamp(2rem, 4vw, 3rem); background: var(--paper); background-image: var(--contour); background-size: 420px 420px; }
      .country-hero h1 { max-width: 14ch; margin: 1.2rem 0 1rem; font-size: clamp(2.6rem, 1.4rem + 4.5vw, 5.5rem); line-height: .98; font-weight: 300; }
      .country-hero h1 em { color: var(--clay); }
      .country-grid { display: grid; grid-template-columns: minmax(0, 8fr) minmax(0, 4fr); gap: 2.5rem; align-items: start; }
      .country-grid .region-list { max-height: none; margin: 0; }
      .country-grid .guide-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); padding-top: 0; }
      .country-grid .guide-card .quick-highlights { margin: 0 0 1rem; }
      .crumbs { display: flex; flex-wrap: wrap; gap: .5rem; color: var(--ink-3); font-family: var(--mono); font-size: .62rem; letter-spacing: .12em; text-transform: uppercase; }
      .crumbs a { color: inherit; text-decoration: none; }
      .crumbs a:hover { color: var(--clay); }
      @media (max-width: 820px) { .country-grid { grid-template-columns: 1fr; } }
      @media (max-width: 600px) { .country-grid .guide-grid { grid-template-columns: 1fr; } }
    </style>
  </head>
  <body>
    <a class="skip-link" href="#main">Skip to content</a>
    <header class="masthead">
      <div class="masthead-row wrap">
        <a class="wordmark" href="../" aria-label="${esc(storeName)} home">Marufi <em>Digital</em><small aria-hidden="true">Guides by place</small></a>
        <nav class="site-nav" aria-label="Main navigation">
          <a href="../#guides">Collection</a>
          <a href="../#atlas">Atlas</a>
          <a href="../#faq">Questions</a>
          <a class="nav-shop" href="../?country=${encodeURIComponent(country)}#guides">Shop ${esc(country)} <span aria-hidden="true">→</span></a>
        </nav>
      </div>
    </header>
    <main id="main" tabindex="-1">
      <section class="country-hero" aria-labelledby="country-title">
        <div class="wrap">
          <nav class="crumbs" aria-label="Breadcrumb"><a href="../">${esc(storeName)}</a><span>/</span><a href="../#atlas">Atlas</a><span>/</span><span>${esc(country)}</span></nav>
          <h1 id="country-title">${esc(country)}, <em>state by state.</em></h1>
          <p class="deck">${esc(description)}</p>
        </div>
      </section>
      <section class="section" aria-label="${esc(country)} titles and states">
        <div class="wrap country-grid">
          <div>
            <div class="section-index"><span>${esc(country)}</span><span>${inCountry.length} ${inCountry.length === 1 ? 'title' : 'titles'}</span></div>
            <div class="guide-grid" style="padding-top:1.5rem">${cards}
            </div>
          </div>
          <aside>
            <div class="section-index"><span>States</span><span>${covered.length} of ${states.length} covered</span></div>
            <ul class="region-list" style="margin-top:1rem">${stateBlocks}</ul>
          </aside>
        </div>
      </section>
    </main>
    <footer class="site-footer">
      <div class="footer-bottom wrap">
        <span>© ${new Date().getFullYear()} ${esc(storeName)}</span>
        <a href="../">Back to the collection <span aria-hidden="true">→</span></a>
      </div>
    </footer>
  </body>
</html>
`;
}

let count = 0;
for (const country of countries) {
  const dir = path.join(dist, slug(country));
  await mkdir(dir, { recursive: true });
  await writeFile(path.join(dir, 'index.html'), page(country));
  count += 1;
}

// Kind pages: static shells that kind.js fills from the live catalog, so they never need a rebuild for content.
function kindPage(cat) {
  const inKind = guides.filter((g) => String(g.type || 'Guide').toLowerCase() === cat.key.toLowerCase());
  const title = `${cat.name} by country and state · ${storeName}`;
  const description = `${cat.blurb} ${inKind.length ? `${inKind.length} ${inKind.length === 1 ? 'title' : 'titles'} so far, filed by country and state.` : 'Filed by country and state as each one is published.'} Secure checkout with Stripe, instant download.`;
  const url = siteUrl ? `${siteUrl}/${cat.slug}/` : '';
  const jsonld = { '@context': 'https://schema.org', '@type': 'CollectionPage', name: title, description, url: url || undefined, isPartOf: siteUrl ? { '@type': 'WebSite', name: storeName, url: siteUrl } : undefined };
  const others = categories.filter((c) => c.key !== cat.key && c.key !== 'Bundle' && !c.hidden).map((c) => `<a href="../${esc(c.slug)}/">${esc(c.name)}</a>`).join('');
  return `<!doctype html>
<!-- generated by scripts/build-pages.mjs -->
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta name="theme-color" content="#14201c" />
    <meta name="kind" content="${esc(cat.key)}" />
    <title>${esc(title)}</title>
    <meta name="description" content="${esc(description)}" />
    ${url ? `<link rel="canonical" href="${esc(url)}" />` : ''}
    <meta property="og:type" content="website" />
    <meta property="og:title" content="${esc(title)}" />
    <meta property="og:description" content="${esc(description)}" />
    ${siteUrl ? `<meta property="og:image" content="${esc(siteUrl)}/assets/travel-planning.webp" />` : ''}
    <link rel="icon" href="../favicon.svg" type="image/svg+xml" />
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,400;0,500;0,600;1,400;1,500&family=DM+Sans:opsz,wght@9..40,400;9..40,500;9..40,600;9..40,700&display=swap" rel="stylesheet" />
    <link rel="stylesheet" href="../styles.css" />
    <script type="application/ld+json">${JSON.stringify(jsonld)}</script>
    <script src="../kind.js" defer></script>
  </head>
  <body class="kind-page">
    <a class="skip-link" href="#main">Skip to content</a>
    <header class="masthead" id="masthead">
      <div class="masthead-row wrap">
        <a class="wordmark" href="../" aria-label="${esc(storeName)} home">Marufi <em>Digital</em><small aria-hidden="true">Help by place</small></a>
        <nav class="site-nav" aria-label="Main navigation">
          <a href="../#needs">Browse</a>
          <a href="../#atlas">Atlas</a>
          <a href="../#help">Free help</a>
          <a href="../#faq">Questions</a>
          <a class="nav-shop" href="../#cart">Cart <span class="cart-count" id="kind-cart-count" hidden>0</span></a>
        </nav>
      </div>
    </header>
    <main id="main" tabindex="-1">
      <section class="kind-hero" aria-labelledby="kind-title">
        <div class="wrap">
          <div>
            <nav class="crumbs" aria-label="Breadcrumb"><a href="../">${esc(storeName)}</a><span>/</span><a href="../#needs">Browse by need</a><span>/</span><span>${esc(cat.name)}</span></nav>
            <svg aria-hidden="true" viewBox="0 0 24 24">${ICONS[cat.icon] || ICONS.layers}</svg>
            <h1 id="kind-title">${esc(cat.name)}, <em>filed by place.</em></h1>
            <p class="deck" id="kind-blurb">${esc(cat.blurb)}</p>
            <span class="kind-format" id="kind-format">${esc(cat.format || 'Digital download')}</span>
          </div>
          <ul class="kind-stats" id="kind-stats" aria-label="At a glance">
            <li><span>Titles</span><strong>${inKind.length || 'Coming soon'}</strong></li>
          </ul>
        </div>
      </section>
      <section class="section" aria-label="${esc(cat.name)}">
        <div class="wrap">
          <div class="section-index"><span>${esc(cat.name)}</span><span>By country and state</span></div>
          <div class="kind-toolbar" id="kind-toolbar" hidden>
            <label class="sr-only" for="kind-search">Search</label>
            <input id="kind-search" type="search" placeholder="Search a place or a title" autocomplete="off" />
            <label class="sr-only" for="kind-country">Country</label>
            <select id="kind-country"><option value="">All countries</option></select>
            <span class="kind-count" id="kind-count"></span>
          </div>
          <div class="guide-grid kind-grid" id="kind-grid"></div>
          <div class="catalog-message" id="kind-empty">
            <div class="message-symbol" aria-hidden="true">Coming<br />soon</div>
            <div class="message-copy">
              <span class="message-overline">${esc(cat.name)} / in progress</span>
              <h2>The first ${esc(cat.name.toLowerCase())} are being written.</h2>
              <p>Tell us the place you need and we will write to you when it is ready.</p>
              <a class="button" href="../#atlas">Ask for a place <span aria-hidden="true">→</span></a>
            </div>
          </div>
          <noscript><p class="hint">Titles load with JavaScript. <a href="../?type=${esc(cat.key)}#guides">Browse ${esc(cat.name.toLowerCase())} on the home page</a>.</p></noscript>
        </div>
      </section>
      <section class="section help-section" id="kind-help" hidden aria-label="Free help">
        <div class="wrap">
          <div class="section-index"><span>Free help</span><span>No purchase needed</span></div>
          <div class="help-grid" id="kind-help-list"></div>
        </div>
      </section>
      <section class="section" aria-label="Other kinds of help">
        <div class="wrap">
          <div class="section-index"><span>Looking for something else?</span><span>Every kind, filed by place</span></div>
          <div class="kind-others" id="kind-others">${others}</div>
        </div>
      </section>
    </main>
    <footer class="site-footer">
      <div class="footer-bottom wrap">
        <span>© ${new Date().getFullYear()} ${esc(storeName)}</span>
        <span class="footer-legal"><a href="../contact/">Contact</a> · <a href="../privacy/">Privacy</a> · <a href="../terms/">Terms</a></span>
        <a href="../">Back to the library <span aria-hidden="true">→</span></a>
      </div>
    </footer>
  </body>
</html>
`;
}
const kindPages = categories.filter((c) => !c.hidden && (c.key !== 'Bundle' || guides.some((g) => String(g.type || '').toLowerCase() === 'bundle')));
for (const cat of kindPages) {
  const dir = path.join(dist, cat.slug);
  await mkdir(dir, { recursive: true });
  await writeFile(path.join(dir, 'index.html'), kindPage(cat));
}

// Secondary pages: privacy, terms, contact, purchase history, gift cards, articles, 404. All share one shell and
// are filled by page.js at runtime (store name, contact e-mail, forms, articles), so they never go stale.
const contactEmail = String(catalog.contactEmail || '').trim();
function shell({ base, page, title, description, body, article = '', canonicalPath = '' }) {
  const url = siteUrl && canonicalPath ? `${siteUrl}/${canonicalPath}` : '';
  const nav = [['#needs', 'Browse'], ['#atlas', 'Atlas'], ['articles/', 'Journal'], ['contact/', 'Contact']];
  return `<!doctype html>
<!-- generated by scripts/build-pages.mjs -->
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta name="theme-color" content="#14201c" />
    <title>${esc(title)} · ${esc(storeName)}</title>
    <meta name="description" content="${esc(description)}" />
    ${url ? `<link rel="canonical" href="${esc(url)}" />` : ''}
    ${page === '404' ? '<meta name="robots" content="noindex" />' : ''}
    <meta property="og:type" content="website" />
    <meta property="og:title" content="${esc(title)} · ${esc(storeName)}" />
    <meta property="og:description" content="${esc(description)}" />
    <meta property="og:image" content="${siteUrl ? `${esc(siteUrl)}/assets/share.jpg` : `${base}assets/share.jpg`}" />
    <meta name="twitter:card" content="summary_large_image" />
    <link rel="icon" href="${base}favicon.svg" type="image/svg+xml" />
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,400;0,500;0,600;1,400;1,500&family=DM+Sans:opsz,wght@9..40,400;9..40,500;9..40,600;9..40,700&display=swap" rel="stylesheet" />
    <link rel="stylesheet" href="${base}styles.css" />
    <script src="${base}page.js" defer></script>
  </head>
  <body class="sub-page" data-page="${esc(page)}"${article ? ` data-article="${esc(article)}"` : ''}>
    <a class="skip-link" href="#main">Skip to content</a>
    <header class="masthead" id="masthead">
      <div class="masthead-row wrap">
        <a class="wordmark" href="${base}" aria-label="${esc(storeName)} home">Marufi <em>Digital</em><small aria-hidden="true">Help by place</small></a>
        <nav class="site-nav" aria-label="Main navigation">
          ${nav.map(([href, label]) => `<a href="${base}${href}">${label}</a>`).join('\n          ')}
          <a class="nav-shop" href="${base}#cart">Cart <span class="cart-count" id="kind-cart-count" hidden>0</span></a>
        </nav>
      </div>
    </header>
    <main id="main" tabindex="-1">
${body}
    </main>
    <footer class="site-footer sub-footer">
      <div class="footer-grid wrap">
        <div class="footer-brand">
          <div class="footer-wordmark">Marufi <em>Digital</em></div>
          <p>Guides, books, immigration help, restaurant lists, and checklists, organized by country and state. Payments are processed securely by Stripe.</p>
          <form class="subscribe-form" aria-label="Newsletter" hidden>
            <label class="sr-only" for="subscribe-email-${esc(page)}">E-mail for new titles and free help</label>
            <div class="subscribe-row"><input id="subscribe-email-${esc(page)}" type="email" placeholder="Your e-mail for new titles and free help" autocomplete="email" required /><button type="submit" class="button button-sm">Subscribe</button></div>
            <p class="subscribe-note hint">One e-mail when something new is worth your time. Unsubscribe in one click.</p>
          </form>
        </div>
        <nav class="footer-col" aria-label="Explore">
          <h2>Explore</h2>
          <a href="${base}#needs">Browse by need</a>
          <a href="${base}#guides">The library</a>
          <a href="${base}#atlas">The atlas</a>
          <a href="${base}articles/">Journal</a>
          <a href="${base}gift/">Gift cards</a>
        </nav>
        <nav class="footer-col" aria-label="Support">
          <h2>Support</h2>
          <a href="${base}contact/">Contact</a>
          <a href="${base}account/">My purchases</a>
          <a href="${base}#faq">Questions</a>
          <a href="${base}#footer">Resend my download</a>
        </nav>
        <nav class="footer-col" aria-label="Legal">
          <h2>Legal</h2>
          <a href="${base}privacy/">Privacy policy</a>
          <a href="${base}terms/">Terms of sale</a>
        </nav>
      </div>
      <div class="footer-bottom wrap">
        <span>© <span data-year>${new Date().getFullYear()}</span> <span data-store-name>${esc(storeName)}</span> · Independent publisher</span>
        <a href="${base}">Back to the library <span aria-hidden="true">→</span></a>
      </div>
    </footer>
  </body>
</html>
`;
}
const hero = (kicker, h1, deck) => `      <section class="sub-hero">
        <div class="wrap">
          <nav class="crumbs" aria-label="Breadcrumb"><a href="../">${esc(storeName)}</a><span>/</span><span>${esc(kicker)}</span></nav>
          <h1>${h1}</h1>
          ${deck ? `<p class="deck">${deck}</p>` : ''}
        </div>
      </section>`;
const updated = 'September 24, 2026';
const legalNote = `<div class="notice"><strong>A note on this text.</strong> It was prepared as a starting point for ${esc(storeName)} and reflects how the store actually works. Have it reviewed by a lawyer for your province and the countries you sell to before relying on it.</div>`;
const privacyBody = `${hero('Privacy policy', 'Privacy <em>policy.</em>', `How ${esc(storeName)} handles your information. Last updated ${updated}.`)}
      <section class="section"><div class="wrap prose">
        ${legalNote}
        <h2>Who we are</h2>
        <p><span data-store-name>${esc(storeName)}</span> is an independent publisher of digital guides, books, immigration and settling-in help, restaurant lists, and checklists, sold as downloads. ${contactEmail ? `You can reach us at <a data-contact-email href="mailto:${esc(contactEmail)}">${esc(contactEmail)}</a>` : 'You can reach us through the <a href="../contact/">contact page</a>'}.</p>
        <h2>What we collect, and why</h2>
        <ul>
          <li><strong>When you buy.</strong> Checkout is run by Stripe. Stripe collects your name, e-mail address, billing country, and payment details. We never see your card number. We receive your name, e-mail, country, and what you bought so that we can deliver your files, send receipts and download links, answer support questions, and keep the accounting records the law requires.</li>
          <li><strong>When you download.</strong> Download links are signed and time-limited. We log which files were served, when, and from which address, to prevent abuse.</li>
          <li><strong>When you ask to be told about a place.</strong> We keep your e-mail and the place you asked for, and write to you once when it is covered.</li>
          <li><strong>When you subscribe to the newsletter.</strong> We keep your e-mail and the date. Where e-mail is configured you confirm by clicking a link first (double opt-in). Every newsletter has an unsubscribe link.</li>
          <li><strong>When you contact us.</strong> We keep your name, e-mail, and message so that we can reply.</li>
          <li><strong>When you look up your purchases.</strong> We e-mail a one-time code to the address you enter and show the orders placed with that address. Codes expire after ten minutes.</li>
          <li><strong>When you send a gift.</strong> We use the recipient's e-mail only to deliver the gift and keep it with the order record.</li>
        </ul>
        <p>The legal bases, where they apply, are performance of a contract (orders, downloads, support), your consent (newsletter, notify-me requests), and our legitimate interest in running the store securely and keeping accounts.</p>
        <h2>Cookies and local storage</h2>
        <p>The store sets no advertising or tracking cookies. Your browser's local storage holds your cart, saved titles, compare list, chosen currency, and a flag so the opening animation plays once per visit. That data stays on your device. Stripe sets its own cookies on the checkout page for fraud prevention; see Stripe's privacy policy.</p>
        <p>If analytics are enabled, we use either Plausible (cookieless, no personal data) or Google Analytics 4 with IP anonymisation. The current setting is shown in the site's source and in this policy's contact section on request.</p>
        <h2>Who processes data for us</h2>
        <ul>
          <li><strong>Stripe</strong> for payments, receipts, and refunds.</li>
          <li><strong>Cloudflare</strong> for hosting the store's server, file storage, and delivery.</li>
          <li><strong>Resend</strong> for sending receipts, download links, codes, and newsletters.</li>
          <li><strong>GitHub Pages</strong> or our own web host for serving the site itself.</li>
        </ul>
        <p>Some of these providers are outside Canada and the European Economic Area. They rely on standard contractual clauses or equivalent safeguards.</p>
        <h2>How long we keep it</h2>
        <p>Order records for seven years, as tax law requires. Notify-me requests until we write to you or you ask us to delete them. Newsletter subscriptions until you unsubscribe. Contact messages for up to two years. Server logs for thirty days.</p>
        <h2>Your rights</h2>
        <p>Under Canada's PIPEDA and provincial privacy laws, and under the GDPR and UK GDPR for readers in the EU, EEA, and UK, you can ask for a copy of the personal information we hold, ask us to correct or delete it, object to or restrict certain processing, take your data elsewhere, and withdraw consent at any time. E-mail us and we will answer within thirty days. You can also complain to the Office of the Privacy Commissioner of Canada or to your local data-protection authority.</p>
        <h2>Children</h2>
        <p>The store is not aimed at children under sixteen and we do not knowingly collect their information.</p>
        <h2>Changes</h2>
        <p>If this policy changes in a way that matters, we update the date at the top and, for material changes, tell newsletter subscribers.</p>
        <h2>Contact</h2>
        <p>Questions about privacy: ${contactEmail ? `<a data-contact-email href="mailto:${esc(contactEmail)}">${esc(contactEmail)}</a>` : '<a href="../contact/">use the contact page</a>'}.</p>
      </div></section>`;
const refund = String(catalog.refundPolicy || '').trim();
const termsBody = `${hero('Terms of sale', 'Terms <em>of sale.</em>', `The agreement between you and ${esc(storeName)} when you buy a download. Last updated ${updated}.`)}
      <section class="section"><div class="wrap prose">
        ${legalNote}
        <div class="notice notice-plain"><strong>In plain words.</strong> You buy a digital file, you get a personal licence to read and use it, we deliver it straight away, and we will make it right if the file is faulty. Our guides are general information, not legal or immigration advice.</div>
        <h2>1. Who you are dealing with</h2>
        <p>These terms apply to every purchase from <span data-store-name>${esc(storeName)}</span> ("we", "us"). By placing an order you accept them. ${contactEmail ? `Contact: <a data-contact-email href="mailto:${esc(contactEmail)}">${esc(contactEmail)}</a>.` : 'Contact us through the <a href="../contact/">contact page</a>.'}</p>
        <h2>2. What you are buying</h2>
        <p>Every title is a digital product delivered as a download: a guide, a book, a help guide, a list, a checklist, or a bundle of these. The listing says what is inside, the format, and, where relevant, the number of pages and the date it was last updated. Nothing is shipped physically.</p>
        <h2>3. Prices and payment</h2>
        <p>Prices are shown in the store's base currency. Prices shown in other currencies are for convenience; Stripe charges you in the base currency and your bank sets the exchange rate. Payment is processed by Stripe; we never see your card details. Taxes are shown at checkout where they apply.</p>
        <h2>4. Delivery</h2>
        <p>After payment you receive download links on the confirmation page and by e-mail. Links are valid for a limited time (typically three days) and can be requested again at any time using "Resend my download" or "My purchases". Pre-orders are delivered by e-mail on the release date shown on the listing.</p>
        <h2>5. Your licence</h2>
        <p>You get a personal, non-exclusive, non-transferable licence to download, store, and read the title on devices you own. You may not resell, share, publish, or distribute it, remove notices from it, or use it to train automated systems. Gifts are licensed to the recipient in the same way. All rights remain with ${esc(storeName)} and its authors.</p>
        <h2>6. Refunds and your right of withdrawal</h2>
        ${refund ? `<p data-refund-policy>${esc(refund)}</p>` : `<p data-refund-policy>If a file is faulty, incomplete, or clearly not what the listing describes, tell us within fourteen days of purchase and we will fix it or refund you in full. Because a download is delivered instantly, we cannot refund a title simply because you changed your mind after opening it.</p>`}
        <p>If you are a consumer in the European Union or the United Kingdom, you normally have a fourteen-day right to withdraw from a distance purchase. For digital content this right ends once delivery begins with your express consent, which you give at checkout by choosing to receive the download immediately. Your statutory rights regarding faulty digital content are not affected.</p>
        <h2>7. Gift cards and coupons</h2>
        <p>Gift cards are delivered as a single-use code worth the amount paid, redeemable at checkout on this store. They have no cash value, cannot be reloaded or exchanged for money, and any unused balance on a single redemption is not carried forward, so choose a cart worth at least the code's value. Coupons and promotion codes may have their own conditions and can be withdrawn at any time.</p>
        <h2>8. Information, not advice</h2>
        <p>Our titles, including immigration and settling-in help, are general information based on public sources and personal experience at the time of writing. Rules, prices, opening hours, and procedures change. They are not legal, immigration, financial, or medical advice. For decisions about your immigration status, consult a licensed immigration consultant or lawyer in the country concerned.</p>
        <h2>9. Our liability</h2>
        <p>To the extent the law allows, our liability for any claim connected with a purchase is limited to the amount you paid for the title concerned. Nothing in these terms limits liability that cannot be limited by law, including for fraud or for consumer rights that apply where you live.</p>
        <h2>10. Accounts, codes, and security</h2>
        <p>There is no password account. Purchase history is shown after a one-time code sent to your e-mail. Keep that mailbox secure; anyone with access to it can see your orders and download links.</p>
        <h2>11. Changes to these terms</h2>
        <p>We may update these terms for future purchases. The terms in force when you order apply to that order.</p>
        <h2>12. Governing law</h2>
        <p>These terms are governed by the laws of Canada and of the province in which ${esc(storeName)} is established, without affecting the mandatory consumer protections of the country where you live. Disputes go first to a friendly e-mail; failing that, to the courts of that province, or to your local courts where consumer law gives you that right.</p>
      </div></section>`;
const contactBody = `${hero('Contact', 'Say <em>hello.</em>', 'Questions about an order, a title, or a place you want covered. We reply within two working days.')}
      <section class="section"><div class="wrap contact-grid">
        <form class="form-card" id="contact-form" novalidate>
          <div class="grid-2">
            <label class="field"><span>Your name</span><input type="text" name="name" autocomplete="name" maxlength="120" /></label>
            <label class="field"><span>E-mail</span><input type="email" name="email" autocomplete="email" required /></label>
          </div>
          <label class="field"><span>What is it about?</span><select name="topic"><option value="Order or download">An order or a download</option><option value="Question about a title">A question about a title</option><option value="Immigration help">Immigration and settling in</option><option value="Restaurants and food">Restaurants and food</option><option value="Request a place">A place you want covered</option><option value="Press or partnership">Press or partnership</option><option value="Other">Something else</option></select></label>
          <label class="field"><span>Message</span><textarea name="message" rows="6" maxlength="4000" required></textarea></label>
          <label class="hp" aria-hidden="true"><input type="text" name="website" tabindex="-1" autocomplete="off" /></label>
          <div class="form-actions"><button type="submit" class="button" name="send">Send message <span aria-hidden="true">→</span></button><span class="hint">We keep your message only to reply to it. <a href="../privacy/">Privacy</a>.</span></div>
        </form>
        <div class="catalog-message" id="contact-done" hidden>
          <div class="message-symbol" aria-hidden="true">Sent</div>
          <div class="message-copy"><span class="message-overline">Thank you</span><h2>Your message is on its way.</h2><p>We reply within two working days. If it is about a download, "Resend my download" in the footer may be faster.</p></div>
        </div>
        <aside class="side-notes">
          <div class="side-note"><span class="message-overline">Faster answers</span><ul class="plain-links"><li><a href="../#footer">Resend my download</a></li><li><a href="../account/">See my purchases</a></li><li><a href="../#faq">Questions before you buy</a></li><li><a href="../#atlas">Ask for a place</a></li></ul></div>
          <div class="side-note" data-needs-email hidden><span class="message-overline">E-mail</span><p><a data-contact-email href="#"></a></p></div>
          <div class="side-note"><span class="message-overline">Response time</span><p>Two working days, usually sooner. Weekends are for the road.</p></div>
        </aside>
      </div></section>`;
const accountBody = `${hero('My purchases', 'Your <em>purchases.</em>', 'Enter the e-mail you used at checkout. We send a one-time code, then show every order with fresh download links.')}
      <section class="section"><div class="wrap narrow">
        <form class="form-card" id="account-email-form" novalidate>
          <label class="field"><span>E-mail used at checkout</span><input type="email" name="email" autocomplete="email" required /></label>
          <div class="form-actions"><button type="submit" class="button" name="send">Send me a code <span aria-hidden="true">→</span></button><span class="hint">No password. The code works for ten minutes.</span></div>
        </form>
        <form class="form-card" id="account-code-form" hidden novalidate>
          <p class="hint" id="account-code-note"></p>
          <label class="field"><span>Six-digit code</span><input type="text" name="code" inputmode="numeric" pattern="[0-9]*" maxlength="6" autocomplete="one-time-code" required /></label>
          <div class="form-actions"><button type="submit" class="button" name="verify">Show my purchases <span aria-hidden="true">→</span></button><button type="button" class="button-ghost" id="account-restart">Use another e-mail</button></div>
        </form>
        <div class="catalog-message" id="account-offline" hidden>
          <div class="message-symbol" aria-hidden="true">Soon</div>
          <div class="message-copy"><span class="message-overline">Not connected yet</span><h2>Purchase history needs the store's server.</h2><p>Until it is connected, your Stripe receipt has your download links, and "Resend my download" in the footer of the home page sends fresh ones.</p></div>
        </div>
        <div id="account-orders" hidden></div>
      </div></section>`;
const giftBody = `${hero('Gift cards', 'Give the <em>place.</em>', 'A gift card is a single-use code, delivered by e-mail, redeemable on any title in the store.')}
      <section class="section"><div class="wrap contact-grid">
        <form class="form-card" id="gift-form" novalidate>
          <fieldset class="gift-fieldset"><legend>Amount</legend><div class="gift-amounts" id="gift-amounts"></div></fieldset>
          <div class="grid-2">
            <label class="field"><span>Send to (e-mail, optional)</span><input type="email" name="to" autocomplete="off" /></label>
            <label class="field"><span>From</span><input type="text" name="from" maxlength="80" autocomplete="name" /></label>
          </div>
          <label class="field"><span>Message (optional)</span><textarea name="message" rows="3" maxlength="400"></textarea></label>
          <div class="form-actions"><button type="submit" class="button" name="pay">Pay with Stripe <span aria-hidden="true">→</span></button><span class="hint">Leave "Send to" empty to receive the code yourself.</span></div>
        </form>
        <div class="catalog-message" id="gift-offline" hidden>
          <div class="message-symbol" aria-hidden="true">Soon</div>
          <div class="message-copy"><span class="message-overline">Not available yet</span><h2>Gift cards open once the store is connected to Stripe.</h2><p>Meanwhile, any title can be sent as a gift from the cart.</p></div>
        </div>
        <aside class="side-notes">
          <div class="side-note"><span class="message-overline">How it works</span><ol class="plain-steps"><li>Choose an amount and pay with Stripe.</li><li>The recipient gets a code by e-mail (or you do).</li><li>They enter it in the promotion-code box at checkout.</li></ol></div>
          <div class="side-note"><span class="message-overline">Good to know</span><p>Codes are single use and never expire. Any unused balance is not carried forward, so a cart worth at least the code's value makes the most of it. No cash value. <a href="../terms/">Terms</a>.</p></div>
        </aside>
      </div></section>`;
const articlesBody = (slug) => `      <div id="articles-index">
${hero('Journal', 'Notes from <em>the road.</em>', 'Short pieces on places, paperwork, and where to eat. Free to read.')}
        <section class="section"><div class="wrap">
          <div class="articles-list" id="articles-list"></div>
          <div class="catalog-message" id="articles-empty" hidden>
            <div class="message-symbol" aria-hidden="true">Coming<br />soon</div>
            <div class="message-copy"><span class="message-overline">Journal / in progress</span><h2>The first pieces are being written.</h2><p>Subscribe in the footer and we will tell you when they are up.</p></div>
          </div>
        </div></section>
      </div>
      <article id="article-view" hidden>
        <section class="sub-hero"><div class="wrap">
          <nav class="crumbs" aria-label="Breadcrumb"><a href="../">${esc(storeName)}</a><span>/</span><a href="${slug ? '../' : './'}">Journal</a></nav>
          <p class="card-kicker" id="article-kicker"></p>
          <h1 id="article-title"></h1>
          <p class="deck" id="article-summary"></p>
        </div></section>
        <section class="section"><div class="wrap narrow">
          <img class="article-cover" id="article-cover" alt="" hidden />
          <div class="prose" id="article-body"></div>
        </div></section>
        <section class="section" id="article-more" hidden><div class="wrap">
          <div class="section-index"><span>More from the journal</span><span>Free to read</span></div>
          <div class="articles-list" id="article-more-list"></div>
        </div></section>
      </article>`;
const notFoundBody = `      <section class="sub-hero"><div class="wrap">
        <p class="eyebrow">404 · Not on the map</p>
        <h1>This page <em>is not in the atlas.</em></h1>
        <p class="deck">The link may be old, or the page has moved. Try one of these instead.</p>
        <div class="hero-actions"><a class="button" href="./#needs">Browse by need <span aria-hidden="true">→</span></a><a class="text-link" href="./contact/">Tell us what you were looking for <span aria-hidden="true">→</span></a></div>
        <form class="search-row" action="./" method="get"><label class="sr-only" for="lost-search">Search the library</label><input id="lost-search" type="search" name="q" placeholder="Search a place or a title" /><button type="submit" class="button-ghost">Search</button></form>
      </div></section>`;

const staticPages = [
  ['privacy', { page: 'privacy', title: 'Privacy policy', description: `How ${storeName} collects, uses, and protects your information when you buy, download, subscribe, or get in touch.`, body: privacyBody }],
  ['terms', { page: 'terms', title: 'Terms of sale', description: `The terms that apply when you buy a digital title from ${storeName}: delivery, licence, refunds, gift cards, and your rights.`, body: termsBody }],
  ['contact', { page: 'contact', title: 'Contact', description: `Questions about an order, a title, or a place you want covered. ${storeName} replies within two working days.`, body: contactBody }],
  ['account', { page: 'account', title: 'My purchases', description: `See every order and get fresh download links with a one-time code sent to your e-mail.`, body: accountBody }],
  ['gift', { page: 'gift', title: 'Gift cards', description: `Give a ${storeName} gift card: a single-use code, delivered by e-mail, redeemable on any title.`, body: giftBody }],
  ['articles', { page: 'articles', title: 'Journal', description: `Short free pieces from ${storeName} on places, paperwork, and where to eat.`, body: articlesBody('') }]
];
for (const [dir, opts] of staticPages) {
  await mkdir(path.join(dist, dir), { recursive: true });
  await writeFile(path.join(dist, dir, 'index.html'), shell({ base: '../', canonicalPath: `${dir}/`, ...opts }));
}
await writeFile(path.join(dist, '404.html'), shell({ base: './', page: '404', title: 'Page not found', description: 'That page is not in the atlas.', body: notFoundBody }));
// Static article pages for articles already in guides.json (clean URLs); page.js also serves ?a=<slug> for new ones.
const articles = (Array.isArray(catalog.articles) ? catalog.articles : []).filter((a) => a && a.title && a.status !== 'draft' && !(a.date && Date.parse(a.date) > Date.now()));
const articleSlugs = [];
for (const a of articles) {
  const s = a.slug || slug(a.title);
  if (!s) continue;
  articleSlugs.push(s);
  await mkdir(path.join(dist, 'articles', s), { recursive: true });
  await writeFile(path.join(dist, 'articles', s, 'index.html'), shell({ base: '../../', canonicalPath: `articles/${s}/`, page: 'articles', article: s, title: a.title, description: a.summary || `${a.title}, from the ${storeName} journal.`, body: articlesBody(s) }));
}

// Sitemap and robots
const base = siteUrl || 'https://YOUR-DOMAIN';
const urls = [`${base}/`, ...kindPages.map((c) => `${base}/${c.slug}/`), ...countries.map((c) => `${base}/${slug(c)}/`), ...['articles', 'gift', 'contact', 'account', 'privacy', 'terms'].map((d) => `${base}/${d}/`), ...articleSlugs.map((a) => `${base}/articles/${a}/`)];
await writeFile(path.join(dist, 'sitemap.xml'), `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls.map((u, i) => `  <url><loc>${esc(u)}</loc><changefreq>weekly</changefreq><priority>${i === 0 ? '1.0' : '0.8'}</priority></url>`).join('\n')}\n</urlset>\n`);
await writeFile(path.join(dist, 'robots.txt'), `User-agent: *\nAllow: /\nDisallow: /thank-you.html\nDisallow: /admin/\nSitemap: ${base}/sitemap.xml\n`);
console.log(`Built ${staticPages.length + 1} static pages, ${articleSlugs.length} article pages, ${kindPages.length} kind pages (${kindPages.map((c) => c.slug).join(', ')}) and ${count} country page${count === 1 ? '' : 's'}${count ? ': ' + countries.map(slug).join(', ') : ''}. Sitemap has ${urls.length} URLs.`);
