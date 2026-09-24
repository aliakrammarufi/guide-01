/**
 * Generates a static landing page per country (dist/<country-slug>/index.html), a page per kind of help
 * (dist/travel-guides/, books/, immigration/, food/, checklists/, ... filled at runtime by kind.js), plus sitemap.xml and robots.txt.
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
              <h3>The first ${esc(cat.name.toLowerCase())} are being written.</h3>
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

// Sitemap and robots
const base = siteUrl || 'https://YOUR-DOMAIN';
const urls = [`${base}/`, ...kindPages.map((c) => `${base}/${c.slug}/`), ...countries.map((c) => `${base}/${slug(c)}/`)];
await writeFile(path.join(dist, 'sitemap.xml'), `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls.map((u, i) => `  <url><loc>${esc(u)}</loc><changefreq>weekly</changefreq><priority>${i === 0 ? '1.0' : '0.8'}</priority></url>`).join('\n')}\n</urlset>\n`);
await writeFile(path.join(dist, 'robots.txt'), `User-agent: *\nAllow: /\nDisallow: /thank-you.html\nDisallow: /admin/\nSitemap: ${base}/sitemap.xml\n`);
console.log(`Built ${kindPages.length} kind pages (${kindPages.map((c) => c.slug).join(', ')}) and ${count} country page${count === 1 ? '' : 's'}${count ? ': ' + countries.map(slug).join(', ') : ''}. Sitemap has ${urls.length} URLs.`);
