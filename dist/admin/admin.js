/* Marufi Digital admin dashboard. Talks to the Worker (online) or edits guides.json in the browser (offline). */
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const KEYS = { endpoint: 'marufi-admin-endpoint', token: 'marufi-admin-token', draft: 'marufi-admin-draft', remember: 'marufi-admin-remember' };
const state = { mode: 'offline', endpoint: '', token: '', catalog: null, dirty: false, status: {}, orders: null, ordersError: '', requests: [], media: [], files: [], view: 'overview', editing: null, backups: [], titlesSort: { key: 'title', dir: 'asc' }, titlesPage: 1, selected: new Set(), health: null, templates: null, coupons: null, loginChallenge: '', resetChallenge: '' };

/* ---------- Utilities ---------- */
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function slug(text) {
  return String(text || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60);
}
function assetUrl(value) {
  // Site-relative paths such as assets/covers/x.webp live one level above /admin/.
  if (!value) return '';
  return /^(https?:)?\/\//.test(value) || value.startsWith('data:') ? value : `../${value.replace(/^\/+/, '')}`;
}
function isHttps(value) {
  try { return new URL(value).protocol === 'https:' || new URL(value).hostname === 'localhost'; } catch { return false; }
}
function money(amount, currency) {
  try { return new Intl.NumberFormat(undefined, { style: 'currency', currency: currency || (state.catalog && state.catalog.currency) || 'CAD', minimumFractionDigits: Number.isInteger(amount) ? 0 : 2 }).format(amount); } catch { return `${currency} ${amount}`; }
}
function formatDate(iso) {
  try { return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }); } catch { return iso; }
}
function formatBytes(n) {
  if (!n) return '';
  if (n > 1048576) return `${(n / 1048576).toFixed(1)} MB`;
  if (n > 1024) return `${Math.round(n / 1024)} KB`;
  return `${n} B`;
}
let toastTimer;
function toast(text, error) {
  const node = $('#toast');
  node.textContent = text;
  node.classList.toggle('is-error', Boolean(error));
  node.classList.add('is-visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove('is-visible'), error ? 5000 : 2800);
}
function download(name, text, type = 'application/json') {
  const blob = new Blob([text], { type });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
function csv(rows) {
  return rows.map((row) => row.map((cell) => `"${String(cell ?? '').replace(/"/g, '""')}"`).join(',')).join('\n');
}

/* ---------- API ---------- */
async function api(path, options = {}) {
  if (state.mode !== 'online') throw new Error('This needs the Worker. Sign in with your Worker URL to use it.');
  const headers = { ...(options.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  if (options.json !== undefined) { headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(options.json); }
  const response = await fetch(`${state.endpoint}${path}`, { ...options, headers });
  const data = await response.json().catch(() => ({}));
  if (response.status === 401 && path !== '/admin/login') { signOut(true); throw new Error(data.error || 'Session expired'); }
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

function setProgress(fraction) {
  const bar = $('#progress');
  if (fraction === null) { bar.style.transform = 'scaleX(0)'; bar.classList.remove('is-active'); return; }
  bar.classList.add('is-active');
  bar.style.transform = `scaleX(${Math.max(0.03, Math.min(1, fraction))})`;
}

/** Crops (optional) and resizes an image in the browser before upload, returning a WebP file. */
async function processImage(file, { aspect = 0, maxWidth = 1400, quality = 0.86 } = {}) {
  if (!/^image\/(jpeg|png|webp|avif|gif)$/.test(file.type)) return file;
  const bitmap = await createImageBitmap(file).catch(() => null);
  if (!bitmap) return file;
  let sx = 0; let sy = 0; let sw = bitmap.width; let sh = bitmap.height;
  if (aspect > 0) {
    const current = sw / sh;
    if (current > aspect) { sw = Math.round(sh * aspect); sx = Math.round((bitmap.width - sw) / 2); }
    else if (current < aspect) { sh = Math.round(sw / aspect); sy = Math.round((bitmap.height - sh) / 2); }
  }
  const scale = Math.min(1, maxWidth / sw);
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(sw * scale);
  canvas.height = Math.round(sh * scale);
  canvas.getContext('2d').drawImage(bitmap, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height);
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/webp', quality));
  if (!blob) return file;
  const name = file.name.replace(/\.[^.]+$/, '') + '.webp';
  return new File([blob], name, { type: 'image/webp' });
}

function uploadFile(file, kind, onProgress) {
  if (state.mode !== 'online') return Promise.reject(new Error('Uploads need the Worker. Offline, put the file in dist/assets/ and paste its path instead.'));
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', `${state.endpoint}/admin/upload?kind=${kind}&name=${encodeURIComponent(file.name)}`);
    xhr.setRequestHeader('Authorization', `Bearer ${state.token}`);
    xhr.setRequestHeader('Content-Type', file.type || 'application/octet-stream');
    xhr.upload.onprogress = (event) => { if (event.lengthComputable) { setProgress(event.loaded / event.total); if (onProgress) onProgress(event.loaded / event.total); } };
    xhr.onload = () => {
      setProgress(null);
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch { /* ignore */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data); else reject(new Error(data.error || `Upload failed (${xhr.status})`));
    };
    xhr.onerror = () => { setProgress(null); reject(new Error('Upload failed: network error')); };
    setProgress(0.02);
    xhr.send(file);
  });
}

/* ---------- Catalog state ---------- */
const EMPTY = { storeName: 'Marufi Digital', siteUrl: '', currency: 'CAD', currencies: ['CAD'], checkoutEndpoint: '', contactEmail: '', social: {}, analytics: {}, refundPolicy: '', author: {}, reviews: [], zones: [], regions: {}, guides: [] };

function normaliseCatalog(raw) {
  const c = { ...EMPTY, ...(raw || {}) };
  c.guides = Array.isArray(c.guides) ? c.guides : [];
  c.zones = Array.isArray(c.zones) ? c.zones : [];
  c.reviews = Array.isArray(c.reviews) ? c.reviews : [];
  c.regions = c.regions && typeof c.regions === 'object' ? c.regions : {};
  c.social = c.social && typeof c.social === 'object' ? c.social : {};
  c.author = c.author && typeof c.author === 'object' ? c.author : {};
  c.analytics = c.analytics && typeof c.analytics === 'object' ? c.analytics : {};
  c.currencies = Array.isArray(c.currencies) && c.currencies.length ? c.currencies : [c.currency || 'CAD'];
  for (const g of c.guides) {
    g.highlights = Array.isArray(g.highlights) ? g.highlights : [];
    g.variants = Array.isArray(g.variants) ? g.variants : [];
    g.reviews = Array.isArray(g.reviews) ? g.reviews : [];
    g.includes = Array.isArray(g.includes) ? g.includes : [];
    g.samplePages = Array.isArray(g.samplePages) ? g.samplePages : [];
    g.prices = g.prices && typeof g.prices === 'object' ? g.prices : {};
    if (!g.id) g.id = slug(g.title) || `title-${Date.now()}`;
    if (!['draft', 'scheduled', 'published'].includes(g.status)) g.status = 'published';
  }
  c.cartDiscount = c.cartDiscount && typeof c.cartDiscount === 'object' ? c.cartDiscount : {};
  delete c._empty;
  return c;
}

function markDirty() {
  state.dirty = true;
  $('#unsaved').hidden = false;
  try { localStorage.setItem(KEYS.draft, JSON.stringify({ at: Date.now(), catalog: state.catalog })); } catch { /* ignore */ }
  renderCounts();
}
function markClean() {
  state.dirty = false;
  $('#unsaved').hidden = true;
  try { localStorage.removeItem(KEYS.draft); } catch { /* ignore */ }
}

async function publish() {
  const button = $('#save');
  button.disabled = true;
  try {
    if (state.mode === 'online') {
      const result = await api('/admin/catalog', { method: 'PUT', json: state.catalog });
      markClean();
      toast(`Published. ${result.guides} titles live.`);
    } else {
      download('guides.json', JSON.stringify(state.catalog, null, 2));
      markClean();
      toast('guides.json downloaded. Replace dist/guides.json and push to GitHub to go live.');
    }
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

/* ---------- Auth and boot ---------- */
async function signIn(endpoint, password, remember, code) {
  const base = endpoint.replace(/\/+$/, '');
  const response = state.loginChallenge && code
    ? await fetch(`${base}/admin/login/verify`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ challenge: state.loginChallenge, code }) })
    : await fetch(`${base}/admin/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password }) });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || 'Sign in failed');
  if (data.challenge) {
    state.loginChallenge = data.challenge;
    $('#login-step-code').hidden = false;
    $('#login-sent-to').textContent = data.sentTo || 'your e-mail';
    $('#login-code').focus();
    const pending = new Error('Enter the code we just e-mailed you.');
    pending.pending = true;
    throw pending;
  }
  state.loginChallenge = '';
  state.endpoint = base;
  state.token = data.token;
  state.mode = 'online';
  const storage = remember ? localStorage : sessionStorage;
  try { storage.setItem(KEYS.endpoint, base); storage.setItem(KEYS.token, data.token); localStorage.setItem(KEYS.remember, remember ? '1' : '0'); } catch { /* ignore */ }
}

function signOut(silent) {
  try { for (const s of [localStorage, sessionStorage]) { s.removeItem(KEYS.token); } } catch { /* ignore */ }
  state.token = '';
  state.mode = 'offline';
  $('#app').hidden = true;
  $('#login').hidden = false;
  if (!silent) toast('Signed out');
}

async function loadCatalogOnline() {
  const live = await api('/admin/catalog');
  if (live._empty) {
    // First run: seed from the site's guides.json so nothing is lost.
    const local = await fetch('../guides.json', { cache: 'no-cache' }).then((r) => r.json()).catch(() => ({}));
    state.catalog = normaliseCatalog(local);
    state.catalog.checkoutEndpoint = state.endpoint;
    markDirty();
    toast('No published catalog yet. Loaded guides.json as a starting point. Publish when ready.');
  } else {
    state.catalog = normaliseCatalog(live);
    markClean();
  }
}

async function loadCatalogOffline() {
  const local = await fetch('../guides.json', { cache: 'no-cache' }).then((r) => r.json()).catch(() => ({}));
  state.catalog = normaliseCatalog(local);
  markClean();
}

function restoreDraft() {
  try {
    const raw = localStorage.getItem(KEYS.draft);
    if (!raw) return false;
    const draft = JSON.parse(raw);
    if (!draft || !draft.catalog) return false;
    state.catalog = normaliseCatalog(draft.catalog);
    state.dirty = true;
    $('#unsaved').hidden = false;
    toast(`Restored unpublished edits from ${formatDate(draft.at)}.`);
    return true;
  } catch {
    return false;
  }
}

async function enterApp() {
  // Deep link to a section: /admin/#view=titles
  try { const wanted = new URLSearchParams(location.hash.slice(1)).get('view'); if (wanted && VIEW_TITLES[wanted]) state.view = wanted; } catch { /* ignore */ }
  $('#login').hidden = true;
  $('#app').hidden = false;
  $('#mode-pill').textContent = state.mode === 'online' ? 'Connected' : 'Offline · guides.json';
  $('#save').textContent = state.mode === 'online' ? 'Publish changes' : 'Download guides.json';
  if (state.mode === 'online') {
    try { state.status = await api('/admin/status'); } catch { state.status = {}; }
  } else state.status = {};
  renderStatus();
  renderCounts();
  showView(state.view);
  if (state.mode === 'online') { loadRequests().catch(() => {}); loadOrders().catch(() => {}); }
}

async function boot() {
  const remembered = (localStorage.getItem(KEYS.remember) || '1') === '1';
  const storage = remembered ? localStorage : sessionStorage;
  const endpoint = storage.getItem(KEYS.endpoint) || localStorage.getItem(KEYS.endpoint) || '';
  const token = storage.getItem(KEYS.token) || '';
  $('#login-endpoint').value = endpoint;
  if (endpoint && token) {
    state.endpoint = endpoint.replace(/\/+$/, '');
    state.token = token;
    state.mode = 'online';
    try {
      await api('/admin/status');
      if (!restoreDraft()) await loadCatalogOnline();
      await enterApp();
      return;
    } catch {
      state.mode = 'offline';
      state.token = '';
    }
  }
  $('#login').hidden = false;
}

$('#login-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const note = $('#login-note');
  const endpoint = $('#login-endpoint').value.trim();
  const password = $('#login-password').value;
  if (!isHttps(endpoint)) { note.textContent = 'Enter your Worker URL (https://…workers.dev), or work offline below.'; return; }
  if (!password && !state.loginChallenge) { note.textContent = 'Enter the admin password.'; return; }
  $('#login-submit').disabled = true;
  note.textContent = '';
  try {
    await signIn(endpoint, password, $('#login-remember').checked, $('#login-code').value.trim());
    $('#login-step-code').hidden = true;
    if (!restoreDraft()) await loadCatalogOnline();
    await enterApp();
  } catch (error) {
    note.textContent = error.message;
  } finally {
    $('#login-submit').disabled = false;
  }
});
$('#login-forgot').addEventListener('click', async () => {
  const endpoint = $('#login-endpoint').value.trim().replace(/\/+$/, '');
  const note = $('#login-note');
  if (!isHttps(endpoint)) { note.textContent = 'Enter your Worker URL first.'; return; }
  try {
    const response = await fetch(`${endpoint}/admin/recover`, { method: 'POST' });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'Could not start recovery');
    state.resetChallenge = data.challenge;
    $('#reset-hint').textContent = `A six-digit code has been sent to ${data.sentTo}. It expires in 15 minutes.`;
    $('#login-form').hidden = true;
    $('#reset-form').hidden = false;
    $('#reset-code').focus();
  } catch (error) { note.textContent = error.message; }
});
$('#reset-back').addEventListener('click', () => { $('#reset-form').hidden = true; $('#login-form').hidden = false; });
$('#reset-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const endpoint = $('#login-endpoint').value.trim().replace(/\/+$/, '');
  const note = $('#reset-note');
  try {
    const response = await fetch(`${endpoint}/admin/reset`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ challenge: state.resetChallenge, code: $('#reset-code').value.trim(), password: $('#reset-password').value }) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'Reset failed');
    $('#reset-form').hidden = true; $('#login-form').hidden = false;
    $('#login-password').value = '';
    $('#login-note').textContent = 'Password changed. Sign in with the new one.';
  } catch (error) { note.textContent = error.message; }
});
$('#login-offline').addEventListener('click', async () => {
  state.mode = 'offline';
  if (!restoreDraft()) await loadCatalogOffline();
  await enterApp();
});
$('#sign-out').addEventListener('click', () => signOut(false));
$('#save').addEventListener('click', publish);
window.addEventListener('beforeunload', (event) => { if (state.dirty && state.mode === 'online') { event.preventDefault(); event.returnValue = ''; } });

/* ---------- Views ---------- */
const VIEW_TITLES = {
  overview: ['Overview', 'How the store is doing and what to do next.'],
  titles: ['Titles', 'Guides, books, and bundles in the collection.'],
  zones: ['Zones', 'Cities people are heading to.'],
  reviews: ['Reviews', 'Reader quotes shown on the home page.'],
  media: ['Media & files', 'Images for the site and the products buyers download.'],
  orders: ['Orders', 'Paid Stripe checkouts.'],
  coupons: ['Coupons', 'Discount codes buyers enter at checkout.'],
  requests: ['Requests', 'Places readers asked for.'],
  announce: ['Announce', 'Email buyers about an updated title.'],
  settings: ['Store settings', 'Name, currencies, contact, author, and regions.'],
  tools: ['Backups & tools', 'Import, export, restore.']
};
function showView(view) {
  state.view = view;
  for (const section of $$('.view')) section.hidden = section.dataset.view !== view;
  for (const item of $$('.nav-item')) item.classList.toggle('is-active', item.dataset.view === view);
  const [title, sub] = VIEW_TITLES[view] || [view, ''];
  $('#view-title').textContent = title;
  $('#view-sub').textContent = sub;
  const renderers = { overview: renderOverview, titles: renderTitles, zones: renderZones, reviews: renderReviews, media: renderMedia, orders: renderOrders, coupons: renderCoupons, requests: renderRequests, announce: renderAnnounce, settings: renderSettings, tools: renderTools };
  if (renderers[view]) renderers[view]();
  window.scrollTo({ top: 0 });
}
$('#nav').addEventListener('click', (event) => { const item = event.target.closest('.nav-item'); if (item) showView(item.dataset.view); });
document.addEventListener('click', (event) => {
  const link = event.target.closest('[data-view-link]');
  if (link) showView(link.dataset.viewLink);
  const action = event.target.closest('[data-action]');
  if (!action) return;
  if (action.dataset.action === 'new-title') openTitleEditor(null);
  if (action.dataset.action === 'new-zone') openZoneEditor(null);
  if (action.dataset.action === 'new-review') openReviewEditor(null);
  if (action.dataset.action === 'go-media') showView('media');
});

function renderCounts() {
  const c = state.catalog || EMPTY;
  $('#nav-titles').textContent = c.guides.length || '';
  $('#nav-zones').textContent = c.zones.length || '';
  $('#nav-reviews').textContent = c.reviews.length || '';
  $('#nav-requests').textContent = state.requests.length || '';
}

function renderStatus() {
  const row = $('#status-row');
  row.replaceChildren();
  const s = state.status;
  const items = state.mode === 'online' ? [
    ['Worker', true, 'connected'],
    ['Stripe', s.stripe, s.stripe ? `${s.stripeMode} keys` : 'not configured'],
    ['Files (R2)', s.files, s.files ? 'ready' : 'no bucket'],
    ['Catalog (KV)', s.store, s.store ? 'ready' : 'no namespace'],
    ['Downloads', s.downloads, s.downloads ? 'signed links' : 'no secret'],
    ['Email', s.email, s.email ? 'ready' : 'off']
  ] : [['Offline mode', false, 'editing guides.json in this browser']];
  for (const [label, ok, detail] of items) {
    const pill = el('span', `pill ${ok ? 'ok' : 'warn'}`);
    pill.textContent = `${label} · ${detail}`;
    row.append(pill);
  }
}

/* ---------- Overview ---------- */
function renderOverview() {
  const c = state.catalog;
  const stats = $('#stats');
  stats.replaceChildren();
  const withStripe = c.guides.filter((g) => g.priceId || g.paymentLink).length;
  const revenue = state.orders ? Object.entries(state.orders.summary.byCurrency).map(([cur, v]) => money(v, cur)).join(' · ') : '—';
  const tiles = [
    ['Titles', c.guides.length, `${withStripe} sellable on Stripe`],
    ['Zones', c.zones.length, 'cities featured'],
    ['Reviews', c.reviews.length, 'on the home page'],
    ['Countries', new Set(c.guides.map((g) => g.country).filter(Boolean)).size, 'covered'],
    ['Requests', state.requests.length, 'places asked for'],
    [`Revenue · ${state.orders ? state.orders.summary.days : 30}d`, revenue, state.orders ? `${state.orders.summary.count} orders` : (state.ordersError ? 'connect Stripe to see sales' : (state.mode === 'online' ? 'loading…' : 'needs the Worker'))]
  ];
  for (const [label, value, detail] of tiles) {
    const tile = el('div', 'stat');
    tile.append(el('span', null, label), el('strong', null, String(value)), el('small', null, detail));
    stats.append(tile);
  }
  const list = $('#checklist');
  list.replaceChildren();
  const checks = [
    ['Add your first title', c.guides.length > 0],
    ['Upload a cover for every title', c.guides.length > 0 && c.guides.every((g) => g.cover)],
    ['Create Stripe products for every title', c.guides.length > 0 && c.guides.every((g) => g.priceId || g.paymentLink)],
    ['Attach a product file to every title', c.guides.length > 0 && c.guides.every((g) => g.file || g.paymentLink)],
    ['Fill in the author section', Boolean(c.author && c.author.name && c.author.bio)],
    ['Add a contact email', Boolean(c.contactEmail)],
    ['Set the site URL', Boolean(c.siteUrl)],
    ['Connect the Worker', state.mode === 'online']
  ];
  for (const [label, done] of checks) list.append(el('li', done ? 'done' : '', label));
  renderOrdersTable($('#overview-orders'), state.orders ? state.orders.orders.slice(0, 6) : [], true);
  renderHealth();
}

function renderHealth() {
  const box = $('#health-list');
  box.replaceChildren();
  if (state.mode !== 'online') { box.append(el('div', 'empty', 'Health checks compare the catalog with Stripe and the file bucket. Connect the Worker to run them.')); return; }
  if (!state.health) { box.append(el('div', 'empty', 'Not checked yet. Run the check to compare prices with Stripe and confirm every product file exists.')); return; }
  if (!state.health.issues.length) { box.append(el('div', 'empty', `All ${state.health.titles} titles look healthy. Checked ${formatDate(state.health.checkedAt)}.`)); return; }
  for (const issue of state.health.issues) {
    const row = el('div', `health-item ${issue.level}`);
    row.append(el('span', 'level'));
    const text = el('span');
    if (issue.title) text.append(el('strong', null, issue.title + ' · '));
    text.append(document.createTextNode(issue.message));
    row.append(text);
    if (issue.guideId) { const open = el('button', 'link-button', 'Open'); open.type = 'button'; open.addEventListener('click', () => { const g = state.catalog.guides.find((x) => x.id === issue.guideId); if (g) openTitleEditor(g); }); row.append(open); } else row.append(el('span'));
    box.append(row);
  }
}
$('#health-run').addEventListener('click', async () => {
  const button = $('#health-run');
  button.textContent = 'Checking…';
  try { state.health = await api('/admin/health'); renderHealth(); }
  catch (error) { toast(error.message, true); }
  finally { button.textContent = 'Run check'; }
});

/* ---------- Titles ---------- */
function coverThumb(guide) {
  if (guide.cover) { const img = el('img', 'thumb'); img.src = assetUrl(guide.cover); img.alt = ''; return img; }
  return el('div', 'thumb-art', (guide.state || guide.title || '').slice(0, 10));
}
function statusOf(g) {
  if (g.status === 'draft') return ['draft', 'off'];
  if (g.status === 'scheduled' && g.publishAt && Date.parse(g.publishAt) > Date.now()) return [`scheduled · ${new Date(g.publishAt).toLocaleDateString()}`, 'warn'];
  if (g.preorder || (g.releaseDate && Date.parse(g.releaseDate) > Date.now())) return ['pre-order', 'warn'];
  return ['live', 'ok'];
}
function filteredTitles() {
  const query = $('#titles-search').value.trim().toLowerCase();
  const type = $('#titles-type').value;
  const status = $('#titles-status').value;
  let rows = state.catalog.guides.filter((g) => (!type || g.type === type) && (!query || `${g.title} ${g.country} ${g.state} ${g.type} ${g.id}`.toLowerCase().includes(query)));
  if (status === 'preorder') rows = rows.filter((g) => statusOf(g)[0] === 'pre-order');
  else if (status === 'published') rows = rows.filter((g) => statusOf(g)[1] === 'ok');
  else if (status) rows = rows.filter((g) => g.status === status);
  const { key, dir } = state.titlesSort;
  const value = (g) => key === 'price' ? Number(g.price) || 0 : key === 'place' ? `${g.country || ''} ${g.state || ''}` : key === 'status' ? statusOf(g)[0] : String(g[key] || '');
  rows.sort((a, b) => { const va = value(a); const vb = value(b); const c = typeof va === 'number' ? va - vb : String(va).localeCompare(String(vb)); return dir === 'asc' ? c : -c; });
  return rows;
}
function renderTitles() {
  const wrap = $('#titles-table');
  const all = filteredTitles();
  const pageSize = 25;
  const pages = Math.max(1, Math.ceil(all.length / pageSize));
  if (state.titlesPage > pages) state.titlesPage = pages;
  const rows = all.slice((state.titlesPage - 1) * pageSize, state.titlesPage * pageSize);
  wrap.replaceChildren();
  renderBulkBar();
  if (!rows.length) { wrap.append(el('div', 'empty', state.catalog.guides.length ? 'Nothing matches.' : 'No titles yet. Add your first guide or book.')); $('#titles-pager').replaceChildren(); return; }
  const table = el('table');
  const head = el('tr');
  const selectAll = document.createElement('input'); selectAll.type = 'checkbox'; selectAll.setAttribute('aria-label', 'Select all on this page');
  selectAll.checked = rows.every((g) => state.selected.has(g.id));
  selectAll.addEventListener('change', () => { for (const g of rows) { if (selectAll.checked) state.selected.add(g.id); else state.selected.delete(g.id); } renderTitles(); });
  const th0 = el('th', 'select-cell'); th0.append(selectAll); head.append(th0, el('th', null, ''));
  for (const [label, key] of [['Title', 'title'], ['Type', 'type'], ['Place', 'place'], ['Price', 'price'], ['Status', 'status'], ['Stripe', ''], ['Flags', ''], ['', '']]) {
    const th = el('th', key ? `sortable${state.titlesSort.key === key ? ' ' + state.titlesSort.dir : ''}` : '', label);
    if (key) th.addEventListener('click', () => { state.titlesSort = { key, dir: state.titlesSort.key === key && state.titlesSort.dir === 'asc' ? 'desc' : 'asc' }; renderTitles(); });
    head.append(th);
  }
  table.append(head);
  for (const g of rows) {
    const tr = el('tr');
    const c0 = el('td', 'select-cell'); const box = document.createElement('input'); box.type = 'checkbox'; box.checked = state.selected.has(g.id); box.setAttribute('aria-label', `Select ${g.title}`);
    box.addEventListener('change', () => { if (box.checked) state.selected.add(g.id); else state.selected.delete(g.id); renderBulkBar(); }); c0.append(box);
    const c1 = el('td'); c1.append(coverThumb(g));
    const c2 = el('td'); c2.append(el('div', 'row-title', g.title), el('span', 'row-sub', g.id));
    const c5 = el('td'); c5.textContent = Number.isFinite(Number(g.price)) ? money(Number(g.price)) : '—';
    const [statusText, statusClass] = statusOf(g);
    const cs = el('td'); cs.append(Object.assign(el('span', `pill ${statusClass}`), { textContent: statusText }));
    const stripeOk = Boolean(g.priceId || g.paymentLink);
    const c6 = el('td'); c6.append(Object.assign(el('span', `pill ${stripeOk ? 'ok' : 'warn'}`), { textContent: stripeOk ? 'ready' : 'not linked' }));
    const flags = [g.featured ? 'Featured' : '', g.badge, g.salePrice ? 'Sale' : '', g.file ? 'File' : '', (g.samplePages || []).length || g.sample ? 'Sample' : ''].filter(Boolean).join(' · ');
    const c7 = el('td'); c7.append(el('span', 'row-sub', flags));
    const c8 = el('td', 'row-actions');
    const edit = el('button', 'link-button', 'Edit'); edit.type = 'button'; edit.addEventListener('click', () => openTitleEditor(g));
    const dup = el('button', 'link-button', 'Duplicate'); dup.type = 'button'; dup.addEventListener('click', () => { const copy = JSON.parse(JSON.stringify(g)); copy.id = `${g.id}-copy`; copy.title = `${g.title} (copy)`; copy.status = 'draft'; delete copy.priceId; delete copy.productId; delete copy.paymentLink; copy.featured = false; state.catalog.guides.push(copy); markDirty(); renderTitles(); toast('Duplicated as a draft'); });
    c8.append(edit, dup);
    tr.append(c0, c1, c2, el('td', null, g.type || 'Guide'), el('td', null, [g.country, g.state].filter(Boolean).join(' / ') || '—'), c5, cs, c6, c7, c8);
    table.append(tr);
  }
  wrap.append(table);
  const pager = $('#titles-pager');
  pager.replaceChildren();
  pager.append(el('span', null, `${all.length} ${all.length === 1 ? 'title' : 'titles'} · page ${state.titlesPage} of ${pages}`));
  const buttons = el('div', 'pages');
  for (let i = 1; i <= pages; i += 1) { const b = el('button', i === state.titlesPage ? 'is-active' : '', String(i)); b.type = 'button'; b.addEventListener('click', () => { state.titlesPage = i; renderTitles(); }); buttons.append(b); }
  if (pages > 1) pager.append(buttons);
}
function renderBulkBar() {
  const bar = $('#bulk-bar');
  state.selected = new Set([...state.selected].filter((id) => state.catalog.guides.some((g) => g.id === id)));
  bar.hidden = state.selected.size === 0;
  $('#bulk-count').textContent = `${state.selected.size} selected`;
}
$('#bulk-action').addEventListener('change', () => {
  const action = $('#bulk-action').value;
  const value = $('#bulk-value');
  value.hidden = !['price-pct', 'price-cur', 'badge'].includes(action);
  value.placeholder = action === 'price-pct' ? 'e.g. -10 or 15' : action === 'price-cur' ? 'e.g. USD 14' : 'Badge text';
});
$('#bulk-apply').addEventListener('click', () => {
  const action = $('#bulk-action').value;
  const value = $('#bulk-value').value.trim();
  const targets = state.catalog.guides.filter((g) => state.selected.has(g.id));
  if (!action || !targets.length) return;
  if (action === 'delete') { const button = $('#bulk-apply'); if (!button.dataset.armed) { button.dataset.armed = '1'; button.textContent = 'Confirm delete'; setTimeout(() => { delete button.dataset.armed; button.textContent = 'Apply'; }, 4000); return; } state.catalog.guides = state.catalog.guides.filter((g) => !state.selected.has(g.id)); state.selected.clear(); }
  else if (action === 'publish') for (const g of targets) g.status = 'published';
  else if (action === 'draft') for (const g of targets) g.status = 'draft';
  else if (action === 'badge') for (const g of targets) g.badge = value.slice(0, 24);
  else if (action === 'price-pct') { const pct = Number(value); if (!Number.isFinite(pct)) { toast('Enter a percentage such as -10 or 15.', true); return; } for (const g of targets) g.price = Math.round(Number(g.price) * (1 + pct / 100) * 100) / 100; }
  else if (action === 'price-cur') { const m = value.toUpperCase().match(/^([A-Z]{3})\s+([\d.]+)$/); if (!m) { toast('Use the form "USD 14".', true); return; } for (const g of targets) { g.prices = g.prices || {}; g.prices[m[1]] = Number(m[2]); } }
  const button = $('#bulk-apply'); delete button.dataset.armed; button.textContent = 'Apply';
  markDirty(); renderTitles(); toast(`Applied to ${targets.length} ${targets.length === 1 ? 'title' : 'titles'}. Publish to make it live.`);
});
$('#bulk-clear').addEventListener('click', () => { state.selected.clear(); renderTitles(); });
const CSV_COLUMNS = ['id', 'type', 'status', 'title', 'country', 'state', 'price', 'salePrice', 'saleEnds', 'badge', 'featured', 'format', 'pages', 'updated', 'description', 'longDescription', 'highlights', 'cover', 'coverAlt', 'file', 'priceId', 'paymentLink', 'lat', 'lng', 'preorder', 'releaseDate', 'publishAt'];
$('#titles-export').addEventListener('click', () => {
  const rows = state.catalog.guides.map((g) => CSV_COLUMNS.map((c) => c === 'highlights' ? (g.highlights || []).join(' | ') : c === 'lat' ? (g.coordinates ? g.coordinates[0] : '') : c === 'lng' ? (g.coordinates ? g.coordinates[1] : '') : g[c] ?? ''));
  download('titles.csv', csv([CSV_COLUMNS, ...rows]), 'text/csv');
});
function parseCsv(text) {
  const rows = []; let row = []; let cell = ''; let quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (quoted) { if (ch === '"' && text[i + 1] === '"') { cell += '"'; i += 1; } else if (ch === '"') quoted = false; else cell += ch; }
    else if (ch === '"') quoted = true;
    else if (ch === ',') { row.push(cell); cell = ''; }
    else if (ch === '\n' || ch === '\r') { if (ch === '\r' && text[i + 1] === '\n') i += 1; row.push(cell); rows.push(row); row = []; cell = ''; }
    else cell += ch;
  }
  if (cell || row.length) { row.push(cell); rows.push(row); }
  return rows.filter((r) => r.some((c) => c !== ''));
}
$('#titles-import').addEventListener('change', async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    const rows = parseCsv(await file.text());
    const header = rows.shift().map((h) => h.trim());
    let added = 0; let updated = 0;
    for (const r of rows) {
      const rec = {}; header.forEach((h, i) => { rec[h] = (r[i] || '').trim(); });
      if (!rec.title) continue;
      const id = slug(rec.id || rec.title);
      const existing = state.catalog.guides.find((g) => g.id === id);
      const g = existing || { id, highlights: [], variants: [], reviews: [], includes: [], samplePages: [], prices: {} };
      for (const key of ['type', 'status', 'title', 'country', 'state', 'saleEnds', 'badge', 'format', 'updated', 'description', 'longDescription', 'cover', 'coverAlt', 'file', 'priceId', 'paymentLink', 'releaseDate', 'publishAt']) if (rec[key] !== undefined && rec[key] !== '') g[key] = rec[key];
      if (rec.price) g.price = Number(rec.price);
      if (rec.salePrice) g.salePrice = Number(rec.salePrice);
      if (rec.pages) g.pages = Number(rec.pages);
      if (rec.highlights) g.highlights = rec.highlights.split('|').map((h) => h.trim()).filter(Boolean);
      if (rec.featured) g.featured = /^(true|yes|1)$/i.test(rec.featured);
      if (rec.preorder) g.preorder = /^(true|yes|1)$/i.test(rec.preorder);
      if (rec.lat && rec.lng) g.coordinates = [Number(rec.lat), Number(rec.lng)];
      if (!g.type) g.type = 'Guide';
      if (!g.status) g.status = 'published';
      if (existing) updated += 1; else { state.catalog.guides.push(g); added += 1; }
    }
    markDirty(); renderTitles(); toast(`Imported: ${added} added, ${updated} updated. Publish to make it live.`);
  } catch (error) { toast(`Import failed: ${error.message}`, true); }
  event.target.value = '';
});
$('#titles-search').addEventListener('input', () => { state.titlesPage = 1; renderTitles(); });
$('#titles-type').addEventListener('change', () => { state.titlesPage = 1; renderTitles(); });
$('#titles-status').addEventListener('change', () => { state.titlesPage = 1; renderTitles(); });

/* ---------- Drawer editor infrastructure ---------- */
function openDrawer(kicker, title, buildForm, onSave, onDelete) {
  const drawer = $('#drawer');
  $('#drawer-kicker').textContent = kicker;
  $('#drawer-title').textContent = title;
  const form = $('#drawer-form');
  form.replaceChildren();
  $('#drawer-preview').hidden = true;
  buildForm(form);
  const del = $('#drawer-delete');
  del.hidden = !onDelete;
  del.textContent = 'Delete';
  del.onclick = () => {
    if (del.dataset.armed) { onDelete(); closeDrawer(); }
    else { del.dataset.armed = '1'; del.textContent = 'Click again to confirm delete'; setTimeout(() => { delete del.dataset.armed; del.textContent = 'Delete'; }, 4000); }
  };
  $('#drawer-save').onclick = () => { if (onSave(form) !== false) closeDrawer(); };
  drawer.hidden = false;
  $('#drawer-overlay').hidden = false;
  void drawer.offsetWidth;
  drawer.classList.add('is-open');
  $('#drawer-overlay').classList.add('is-open');
  document.body.classList.add('drawer-open');
  form.querySelector('input, textarea, select')?.focus();
}
function closeDrawer() {
  const drawer = $('#drawer');
  drawer.classList.remove('is-open');
  $('#drawer-overlay').classList.remove('is-open');
  document.body.classList.remove('drawer-open');
  setTimeout(() => { drawer.hidden = true; $('#drawer-overlay').hidden = true; }, 450);
}
$('#drawer-close').addEventListener('click', closeDrawer);
$('#drawer-cancel').addEventListener('click', closeDrawer);
$('#drawer-overlay').addEventListener('click', closeDrawer);
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && !$('#drawer').hidden) closeDrawer();
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
    event.preventDefault();
    if (!$('#drawer').hidden) $('#drawer-save').click(); else if (!$('#app').hidden) publish();
  }
});

// Field builders
function field(label, input, hint) {
  const wrap = el('label', 'field');
  wrap.append(el('span', null, label), input);
  if (hint) wrap.append(el('small', 'hint', hint));
  return wrap;
}
function input(name, value, attrs = {}) {
  const node = document.createElement(attrs.type === 'textarea' ? 'textarea' : 'input');
  if (attrs.type && attrs.type !== 'textarea') node.type = attrs.type;
  node.name = name;
  node.value = value ?? '';
  for (const [k, v] of Object.entries(attrs)) if (!['type', 'value'].includes(k)) node.setAttribute(k, v);
  return node;
}
function select(name, value, options) {
  const node = document.createElement('select');
  node.name = name;
  for (const [v, label] of options) { const o = el('option', null, label); o.value = v; node.append(o); }
  node.value = value ?? '';
  return node;
}
function check(name, checked, label) {
  const wrap = el('label', 'check');
  const box = document.createElement('input');
  box.type = 'checkbox'; box.name = name; box.checked = Boolean(checked);
  wrap.append(box, document.createTextNode(label));
  return wrap;
}
function imagePicker(name, value, label, options = {}) {
  const wrap = el('div', 'field');
  wrap.append(el('span', null, label));
  const picker = el('div', 'image-picker');
  const img = el('img'); img.alt = ''; img.hidden = !value; if (value) img.src = assetUrl(value);
  const url = input(name, value, { placeholder: 'Image URL or assets/… path, or upload' });
  url.addEventListener('input', () => { img.hidden = !url.value; if (url.value) img.src = assetUrl(url.value); });
  const button = el('label', 'button-ghost upload-button', 'Upload');
  const file = document.createElement('input'); file.type = 'file'; file.accept = 'image/*'; file.hidden = true;
  const crop = el('label', 'check');
  const cropBox = document.createElement('input'); cropBox.type = 'checkbox'; cropBox.checked = Boolean(options.aspect);
  crop.append(cropBox, document.createTextNode(options.aspect ? ` Crop to ${options.aspectLabel || '4:5'} and resize before upload` : ' Resize before upload'));
  file.addEventListener('change', async () => {
    if (!file.files[0]) return;
    button.textContent = 'Uploading…';
    try {
      const prepared = await processImage(file.files[0], { aspect: cropBox.checked ? (options.aspect || 0) : 0, maxWidth: options.maxWidth || 1400 });
      const result = await uploadFile(prepared, 'media');
      url.value = result.url; img.src = result.url; img.hidden = false;
      url.dispatchEvent(new Event('input', { bubbles: true }));
      toast(`Image uploaded (${formatBytes(prepared.size)})`);
    }
    catch (error) { toast(error.message, true); }
    finally { button.textContent = 'Upload'; file.value = ''; }
  });
  button.append(file);
  picker.append(img, url, button);
  const row = el('div', 'crop-row');
  row.append(crop);
  wrap.append(picker, row);
  return wrap;
}
function filePicker(name, value, label, hint) {
  const wrap = el('div', 'field');
  wrap.append(el('span', null, label));
  const picker = el('div', 'file-picker');
  const key = input(name, value, { placeholder: 'files/… key in the bucket, or upload' });
  const button = el('label', 'button-ghost upload-button', 'Upload file');
  const file = document.createElement('input'); file.type = 'file'; file.accept = '.pdf,.epub,.zip,.mobi,.mp3,.mp4'; file.hidden = true;
  file.addEventListener('change', async () => {
    if (!file.files[0]) return;
    button.textContent = 'Uploading…';
    try { const result = await uploadFile(file.files[0], 'file'); key.value = result.key; toast('File uploaded'); }
    catch (error) { toast(error.message, true); }
    finally { button.textContent = 'Upload file'; file.value = ''; }
  });
  button.append(file);
  picker.append(key, button);
  wrap.append(picker);
  if (hint) wrap.append(el('small', 'hint', hint));
  return wrap;
}
function pagesPicker(name, values, label) {
  const wrap = el('div', 'field');
  wrap.dataset.pages = name;
  wrap.append(el('span', null, label));
  const list = el('div', 'pages-list');
  const items = [...values];
  const render = () => {
    list.replaceChildren();
    items.forEach((src, index) => {
      const page = el('div', 'page');
      const img = el('img'); img.src = assetUrl(src); img.alt = '';
      const remove = el('button', null, '×'); remove.type = 'button'; remove.setAttribute('aria-label', 'Remove page');
      remove.addEventListener('click', () => { items.splice(index, 1); render(); });
      page.append(img, remove);
      list.append(page);
    });
    wrap.dataset.value = JSON.stringify(items);
  };
  const button = el('label', 'button-ghost upload-button', 'Add pages');
  const file = document.createElement('input'); file.type = 'file'; file.accept = 'image/*'; file.multiple = true; file.hidden = true;
  file.addEventListener('change', async () => {
    for (const f of file.files) {
      try { const result = await uploadFile(await processImage(f, { maxWidth: 1600 }), 'media'); items.push(result.url); render(); }
      catch (error) { toast(error.message, true); break; }
    }
    file.value = '';
  });
  button.append(file);
  const manual = input(`${name}-manual`, '', { placeholder: 'Or paste an image URL and press Enter' });
  manual.addEventListener('keydown', (event) => { if (event.key === 'Enter') { event.preventDefault(); if (manual.value.trim()) { items.push(manual.value.trim()); manual.value = ''; render(); } } });
  render();
  wrap.append(list, button, manual);
  return wrap;
}
function repeater(name, label, items, buildRow, addLabel) {
  const wrap = el('div', 'fieldset');
  wrap.dataset.repeat = name;
  const head = el('div', 'fieldset-title');
  head.append(el('span', null, label));
  const add = el('button', 'link-button', addLabel || 'Add'); add.type = 'button';
  head.append(add);
  const list = el('div', 'repeat');
  const addRow = (item) => {
    const row = el('div', 'repeat-item');
    buildRow(row, item || {});
    const actions = el('div', 'item-actions');
    const remove = el('button', 'link-button danger-link', 'Remove'); remove.type = 'button';
    remove.addEventListener('click', () => row.remove());
    actions.append(el('span'), remove);
    row.append(actions);
    list.append(row);
  };
  items.forEach(addRow);
  add.addEventListener('click', () => addRow());
  wrap.append(head, list);
  return wrap;
}
function readRepeater(form, name, fields) {
  return $$(`[data-repeat="${name}"] .repeat-item`, form).map((row) => {
    const out = {};
    for (const f of fields) {
      const node = row.querySelector(`[data-field="${f}"]`);
      if (!node) continue;
      out[f] = node.type === 'checkbox' ? node.checked : node.value.trim();
    }
    return out;
  });
}
function fieldIn(row, label, f, value, attrs) {
  const node = input(`${f}`, value, attrs || {});
  node.dataset.field = f;
  node.removeAttribute('name');
  return field(label, node);
}
function num(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : undefined;
}
function lines(text) {
  return String(text || '').split('\n').map((s) => s.trim()).filter(Boolean);
}

/* ---------- Title editor ---------- */
function openTitleEditor(guide) {
  const c = state.catalog;
  const g = guide ? JSON.parse(JSON.stringify(guide)) : { type: 'Guide', highlights: [], variants: [], reviews: [], includes: [], samplePages: [], prices: {} };
  const isNew = !guide;
  const currencies = c.currencies.filter((code) => code !== c.currency);
  openDrawer(isNew ? 'New title' : 'Edit title', g.title || 'Untitled', (form) => {
    // Basics
    const basics = el('div', 'fieldset');
    basics.append(el('div', 'fieldset-title', 'Basics'));
    const g2 = el('div', 'grid-2');
    g2.append(field('Title', input('title', g.title, { required: 'true' })), field('Type', select('type', g.type || 'Guide', [['Guide', 'Guide'], ['Book', 'Book'], ['Bundle', 'Bundle']])));
    g2.append(field('Country', input('country', g.country, { placeholder: 'Canada' })), field('State or province', input('state', g.state, { placeholder: 'British Columbia' })));
    g2.append(field('Id (used in links; keep stable)', input('id', g.id, { placeholder: 'auto from title' })), field('Badge', input('badge', g.badge, { placeholder: 'New, Bestseller…', maxlength: '24' })));
    basics.append(g2);
    basics.append(field('Short description (card)', input('description', g.description, { type: 'textarea', rows: '2' })));
    basics.append(field('Long description (quick view and spotlight)', input('longDescription', g.longDescription, { type: 'textarea', rows: '4' })));
    basics.append(field('What is inside, one per line (up to six)', input('highlights', (g.highlights || []).join('\n'), { type: 'textarea', rows: '4' })));
    const g3 = el('div', 'grid-3');
    g3.append(field('Format', input('format', g.format, { placeholder: 'PDF · 84 pages' })), field('Pages', input('pages', g.pages, { type: 'number', min: '0' })), field('Last updated', input('updated', g.updated, { placeholder: 'September 2026' })));
    basics.append(g3);
    const coords = el('div', 'grid-3');
    coords.append(field('Latitude (atlas pin)', input('lat', g.coordinates ? g.coordinates[0] : '', { type: 'number', step: 'any', placeholder: '49.28' })), field('Longitude', input('lng', g.coordinates ? g.coordinates[1] : '', { type: 'number', step: 'any', placeholder: '-123.12' })), field('Options', check('featured', g.featured, "Editor's pick spotlight")));
    basics.append(coords);
    form.append(basics);

    // Visibility and pre-orders
    const vis = el('div', 'fieldset');
    vis.append(el('div', 'fieldset-title', 'Visibility and launch'));
    const v1 = el('div', 'grid-3');
    v1.append(field('Status', select('status', g.status || 'published', [['published', 'Published (visible)'], ['draft', 'Draft (hidden)'], ['scheduled', 'Scheduled (goes live at a date)']])), field('Goes live at (for scheduled)', input('publishAt', g.publishAt ? String(g.publishAt).slice(0, 16) : '', { type: 'datetime-local' })), field('Pre-order', check('preorder', g.preorder, 'Sell before the file is ready')));
    vis.append(v1, field('Release date (shown to buyers; delivery is sent by you from Announce)', input('releaseDate', g.releaseDate ? String(g.releaseDate).slice(0, 10) : '', { type: 'date' })));
    if (g.priceId && g.file && state.mode === 'online') {
      const deliverRow = el('div', 'form-actions');
      const deliver = el('button', 'button-ghost', 'Email download links to everyone who bought this'); deliver.type = 'button';
      deliver.addEventListener('click', () => armDelete(deliver, async () => { const r = await api('/admin/deliver', { method: 'POST', json: { priceId: g.priceId } }); toast(`Sent to ${r.sent} buyer${r.sent === 1 ? '' : 's'}.`); }));
      deliverRow.append(deliver, el('span', 'hint', 'Use when a pre-order ships or you replaced the file.'));
      vis.append(deliverRow);
    }
    form.append(vis);

    // Pricing
    const pricing = el('div', 'fieldset');
    pricing.append(el('div', 'fieldset-title', `Pricing (${c.currency})`));
    const p1 = el('div', 'grid-3');
    p1.append(field(`Price (${c.currency})`, input('price', g.price, { type: 'number', step: '0.01', min: '0', required: 'true' })), field('Sale price', input('salePrice', g.salePrice, { type: 'number', step: '0.01', min: '0' })), field('Sale ends (date)', input('saleEnds', g.saleEnds ? String(g.saleEnds).slice(0, 10) : '', { type: 'date' })));
    pricing.append(p1);
    if (currencies.length) {
      const p2 = el('div', 'grid-3');
      for (const code of currencies) p2.append(field(`Display price ${code}`, input(`prices.${code}`, g.prices[code], { type: 'number', step: '0.01', min: '0' })));
      pricing.append(p2, el('small', 'hint', `Display prices only. Stripe charges the ${c.currency} price.`));
    }
    form.append(pricing);

    // Images and files
    const media = el('div', 'fieldset');
    media.append(el('div', 'fieldset-title', 'Cover, samples, and the product file'));
    media.append(imagePicker('cover', g.cover, 'Cover image (portrait 4:5 looks best)', { aspect: 0.8, aspectLabel: '4:5', maxWidth: 1200 }), field('Cover alt text', input('coverAlt', g.coverAlt)));
    media.append(pagesPicker('samplePages', g.samplePages || [], 'Sample pages (images, shown in "Read a sample")'));
    media.append(field('Or a sample PDF URL', input('sample', g.sample, { placeholder: 'https://… or assets/samples/name.pdf' })));
    media.append(filePicker('file', g.file, 'Product file buyers download', 'Uploaded privately. Delivered through signed, time-limited links after payment. Also attached to the Stripe product.'));
    form.append(media);

    // Stripe
    const stripe = el('div', 'stripe-box');
    stripe.append(el('span', 'eyebrow', 'Stripe'));
    const s1 = el('div', 'grid-2');
    s1.append(field('Price ID', input('priceId', g.priceId, { placeholder: 'price_…' })), field('Payment link', input('paymentLink', g.paymentLink, { placeholder: 'https://buy.stripe.com/…' })));
    stripe.append(s1, field('Product ID', input('productId', g.productId, { placeholder: 'prod_… (filled automatically)' })));
    const actions = el('div', 'form-actions');
    const create = el('button', 'button button-sm', g.priceId ? 'Sync with Stripe' : 'Create product, price, and payment link in Stripe'); create.type = 'button';
    create.disabled = !(state.mode === 'online' && state.status.stripe);
    create.addEventListener('click', () => stripeCreate(form, g, -1, create));
    actions.append(create);
    stripe.append(actions, el('p', 'hint', state.mode === 'online' && state.status.stripe ? 'Creates the Stripe product with this title, price, cover, and product file, then fills the ids above. Run it again after changing the price.' : 'Connect the Worker with a Stripe key to create products from here. Until then, paste a Payment Link from your Stripe dashboard.'));
    form.append(stripe);

    // Variants
    form.append(field('Name of the base edition (when editions exist)', input('variantLabel', g.variantLabel, { placeholder: 'Guide' })));
    form.append(repeater('variants', 'Editions (e.g. Guide + maps pack)', g.variants || [], (row, v) => {
      const r1 = el('div', 'row-3 row');
      r1.append(fieldIn(row, 'Label', 'label', v.label), fieldIn(row, 'Format', 'format', v.format), fieldIn(row, `Price (${c.currency})`, 'price', v.price, { type: 'number', step: '0.01' }));
      const r2 = el('div', 'row-3 row');
      r2.append(fieldIn(row, 'Product file key', 'file', v.file, { placeholder: 'files/…' }), fieldIn(row, 'Price ID', 'priceId', v.priceId, { placeholder: 'price_…' }), fieldIn(row, 'Payment link', 'paymentLink', v.paymentLink));
      row.append(r1, r2);
    }, 'Add an edition'));

    // Bundle includes
    if (c.guides.length) {
      const inc = el('div', 'fieldset');
      inc.append(el('div', 'fieldset-title', 'Bundle contents (for bundles)'));
      const grid = el('div', 'grid-2');
      for (const other of c.guides) {
        if (other.id === g.id) continue;
        const box = check(`includes.${other.id}`, (g.includes || []).includes(other.id), `${other.title} (${other.type})`);
        grid.append(box);
      }
      inc.append(grid);
      form.append(inc);
    }

    // Reviews
    form.append(repeater('reviews', 'Reviews for this title (quick view)', g.reviews || [], (row, r) => {
      row.append(fieldIn(row, 'Quote', 'quote', r.quote, { type: 'textarea', rows: '2' }));
      const r1 = el('div', 'row-3 row');
      r1.append(fieldIn(row, 'Name', 'name', r.name), fieldIn(row, 'Place', 'place', r.place), fieldIn(row, 'Rating 1–5', 'rating', r.rating, { type: 'number', min: '1', max: '5' }));
      row.append(r1);
    }, 'Add a review'));
    // Live preview of the storefront card, updated as you type.
    const preview = $('#drawer-preview');
    preview.hidden = false;
    const refresh = () => renderPreview(preview, form, c);
    form.addEventListener('input', refresh);
    form.addEventListener('change', refresh);
    refresh();
  }, (form) => {
    const data = readTitleForm(form, g, c);
    if (!data) return false;
    const list = state.catalog.guides;
    const index = guide ? list.findIndex((x) => x.id === guide.id) : -1;
    if (index >= 0) list[index] = data; else list.push(data);
    markDirty();
    renderTitles();
    toast(isNew ? 'Title added. Publish to make it live.' : 'Title updated. Publish to make it live.');
  }, guide ? () => { state.catalog.guides = state.catalog.guides.filter((x) => x.id !== guide.id); markDirty(); renderTitles(); toast('Title deleted'); } : null);
}

function renderPreview(box, form, c) {
  const f = form.elements;
  box.replaceChildren();
  box.append(el('span', 'preview-label', 'Card preview'));
  const cover = el('div', 'preview-cover');
  if (f.cover.value.trim()) { const img = el('img'); img.src = assetUrl(f.cover.value.trim()); img.alt = ''; cover.append(img); }
  else cover.append(el('div', 'thumb-art', f.state.value.trim() || f.title.value.trim() || 'Cover'));
  const sale = num(f.salePrice.value);
  const badge = sale !== undefined && sale < num(f.price.value) ? 'Sale' : f.preorder.checked ? 'Pre-order' : f.badge.value.trim();
  if (badge) cover.append(el('span', 'preview-badge', badge));
  const body = el('div', 'preview-body');
  body.append(el('span', 'kicker', [f.type.value, [f.country.value.trim(), f.state.value.trim()].filter(Boolean).join(' / ')].filter(Boolean).join(' · ')));
  body.append(el('h3', null, f.title.value.trim() || 'Untitled'));
  const price = el('span', 'price');
  if (sale !== undefined && sale < num(f.price.value)) { price.append(el('s', null, money(num(f.price.value) || 0)), document.createTextNode(' ' + money(sale))); }
  else price.textContent = money(num(f.price.value) || 0);
  body.append(price, el('p', null, f.description.value.trim() || 'Short description appears here.'));
  const status = f.status.value;
  if (status !== 'published') body.append(Object.assign(el('span', 'pill warn'), { textContent: status === 'draft' ? 'hidden from the site' : 'scheduled' }));
  box.append(cover, body);
}

function readTitleForm(form, base, c) {
  const f = form.elements;
  const title = f.title.value.trim();
  if (!title) { toast('The title needs a name.', true); return null; }
  const price = num(f.price.value);
  if (price === undefined) { toast('Enter a numeric price.', true); return null; }
  const id = slug(f.id.value) || slug(title);
  const data = { ...base, id, title, type: f.type.value, country: f.country.value.trim(), state: f.state.value.trim(), badge: f.badge.value.trim(),
    description: f.description.value.trim(), longDescription: f.longDescription.value.trim(), highlights: lines(f.highlights.value).slice(0, 6),
    format: f.format.value.trim(), pages: num(f.pages.value) || 0, updated: f.updated.value.trim(), featured: f.featured.checked,
    price, salePrice: num(f.salePrice.value), saleEnds: f.saleEnds.value ? `${f.saleEnds.value}T23:59:59Z` : '',
    cover: f.cover.value.trim(), coverAlt: f.coverAlt.value.trim(), sample: f.sample.value.trim(), file: f.file.value.trim(),
    priceId: f.priceId.value.trim(), paymentLink: f.paymentLink.value.trim(), productId: f.productId.value.trim(), variantLabel: f.variantLabel.value.trim(),
    status: f.status.value, publishAt: f.status.value === 'scheduled' && f.publishAt.value ? new Date(f.publishAt.value).toISOString() : '',
    preorder: f.preorder.checked, releaseDate: f.releaseDate.value ? `${f.releaseDate.value}T00:00:00Z` : '',
    prices: {} };
  const lat = num(f.lat.value); const lng = num(f.lng.value);
  data.coordinates = lat !== undefined && lng !== undefined ? [lat, lng] : undefined;
  for (const code of c.currencies) { const node = f[`prices.${code}`]; if (node && node.value !== '') data.prices[code] = num(node.value); }
  const pages = form.querySelector('[data-pages="samplePages"]');
  data.samplePages = pages ? JSON.parse(pages.dataset.value || '[]') : [];
  data.variants = readRepeater(form, 'variants', ['label', 'format', 'price', 'file', 'priceId', 'paymentLink']).filter((v) => v.label).map((v) => ({ ...v, price: num(v.price) }));
  data.reviews = readRepeater(form, 'reviews', ['quote', 'name', 'place', 'rating']).filter((r) => r.quote).map((r) => ({ ...r, rating: num(r.rating) }));
  data.includes = c.guides.filter((o) => f[`includes.${o.id}`] && f[`includes.${o.id}`].checked).map((o) => o.id);
  if (data.salePrice === undefined) delete data.salePrice;
  if (!data.coordinates) delete data.coordinates;
  if (!data.preorder) delete data.preorder;
  for (const k of Object.keys(data)) if (data[k] === '' || data[k] === undefined) delete data[k];
  return data;
}

async function stripeCreate(form, base, variantIndex, button) {
  const data = readTitleForm(form, base, state.catalog);
  if (!data) return;
  button.disabled = true;
  const original = button.textContent;
  button.textContent = 'Talking to Stripe…';
  try {
    const list = state.catalog.guides;
    const index = list.findIndex((x) => x.id === (base.id || data.id));
    if (index >= 0) list[index] = data; else list.push(data);
    await api('/admin/catalog', { method: 'PUT', json: state.catalog });
    const result = await api('/admin/stripe/product', { method: 'POST', json: { guideId: data.id, variantIndex } });
    const updated = result.guide;
    const i2 = list.findIndex((x) => x.id === updated.id);
    if (i2 >= 0) list[i2] = updated;
    markClean();
    form.elements.priceId.value = updated.priceId || '';
    form.elements.paymentLink.value = updated.paymentLink || '';
    form.elements.productId.value = updated.productId || '';
    toast('Stripe product, price, and payment link are ready. Catalog published.');
    button.textContent = 'Sync with Stripe';
  } catch (error) {
    toast(error.message, true);
    button.textContent = original;
  } finally {
    button.disabled = false;
  }
}

/* ---------- Zones ---------- */
function renderZones() {
  const list = $('#zones-list');
  list.replaceChildren();
  const zones = state.catalog.zones;
  const q = $('#zones-search').value.trim().toLowerCase();
  if (!zones.length) { list.append(el('div', 'empty', 'No zones yet. Add the cities people are heading to.')); return; }
  zones.forEach((z, index) => {
    if (q && !`${z.city} ${z.country} ${z.state} ${z.tagline}`.toLowerCase().includes(q)) return;
    const card = el('div', 'card');
    card.draggable = true;
    card.dataset.index = index;
    if (z.image) { const img = el('img', 'cover'); img.src = assetUrl(z.image); img.alt = ''; card.append(img); }
    card.append(el('span', 'kicker', [z.country, z.state].filter(Boolean).join(' / ')), el('h3', null, z.city), el('p', null, z.tagline || z.why || ''));
    const foot = el('div', 'card-foot');
    const handle = el('span', 'handle', `⋮⋮ ${String(index + 1).padStart(2, '0')}`);
    const edit = el('button', 'link-button', 'Edit'); edit.type = 'button'; edit.addEventListener('click', () => openZoneEditor(z, index));
    foot.append(handle, edit);
    card.append(foot);
    card.addEventListener('dragstart', (e) => e.dataTransfer.setData('text/plain', String(index)));
    card.addEventListener('dragover', (e) => e.preventDefault());
    card.addEventListener('drop', (e) => { e.preventDefault(); const from = Number(e.dataTransfer.getData('text/plain')); if (from === index) return; const [moved] = zones.splice(from, 1); zones.splice(index, 0, moved); markDirty(); renderZones(); });
    list.append(card);
  });
}
function openZoneEditor(zone, index) {
  const z = zone ? { ...zone } : {};
  const guides = state.catalog.guides;
  openDrawer(zone ? 'Edit zone' : 'New zone', z.city || 'New zone', (form) => {
    const g2 = el('div', 'grid-2');
    g2.append(field('City', input('city', z.city, { required: 'true' })), field('Country', input('country', z.country)), field('State or province', input('state', z.state)), field('Best time to go', input('bestTime', z.bestTime, { placeholder: 'June to September' })));
    form.append(g2);
    form.append(field('Tagline (one line, italic)', input('tagline', z.tagline, { placeholder: 'Mountains before breakfast, ocean by lunch.' })));
    form.append(field('Why now / what is special', input('why', z.why, { type: 'textarea', rows: '3' })));
    form.append(field('Known for, one per line', input('knownFor', Array.isArray(z.knownFor) ? z.knownFor.join('\n') : (z.knownFor || ''), { type: 'textarea', rows: '3' })));
    form.append(imagePicker('image', z.image, 'Photo (4:3 looks best)'));
    form.append(field('Linked title', select('guideId', z.guideId || '', [['', 'None (link to the country instead)'], ...guides.map((g) => [g.id, `${g.title} · ${g.type}`])])));
  }, (form) => {
    const f = form.elements;
    if (!f.city.value.trim()) { toast('The zone needs a city.', true); return false; }
    const data = { city: f.city.value.trim(), country: f.country.value.trim(), state: f.state.value.trim(), tagline: f.tagline.value.trim(), why: f.why.value.trim(), bestTime: f.bestTime.value.trim(), knownFor: lines(f.knownFor.value), image: f.image.value.trim(), guideId: f.guideId.value };
    if (zone) state.catalog.zones[index] = data; else state.catalog.zones.push(data);
    markDirty(); renderZones(); toast('Zone saved. Publish to make it live.');
  }, zone ? () => { state.catalog.zones.splice(index, 1); markDirty(); renderZones(); toast('Zone deleted'); } : null);
}

/* ---------- Reviews ---------- */
function renderReviews() {
  const list = $('#reviews-list');
  list.replaceChildren();
  const reviews = state.catalog.reviews;
  const q = $('#reviews-search').value.trim().toLowerCase();
  if (!reviews.length) { list.append(el('div', 'empty', 'No reviews yet. Add a reader quote to show the section.')); return; }
  reviews.forEach((r, index) => {
    if (q && !`${r.quote} ${r.name} ${r.place}`.toLowerCase().includes(q)) return;
    const card = el('div', 'card');
    card.append(el('p', null, `“${r.quote}”`));
    const foot = el('div', 'card-foot');
    const who = el('span'); who.append(el('strong', null, r.name || 'A reader'), document.createTextNode(r.place ? ` · ${r.place}` : ''));
    const right = el('span');
    if (r.rating) right.append(Object.assign(el('span', 'stars'), { textContent: '★'.repeat(Math.round(r.rating)) }), document.createTextNode(' '));
    const edit = el('button', 'link-button', 'Edit'); edit.type = 'button'; edit.addEventListener('click', () => openReviewEditor(r, index));
    right.append(edit);
    foot.append(who, right);
    card.append(foot);
    list.append(card);
  });
}
function openReviewEditor(review, index) {
  const r = review ? { ...review } : {};
  openDrawer(review ? 'Edit review' : 'New review', review ? (r.name || 'Review') : 'New review', (form) => {
    form.append(field('Quote', input('quote', r.quote, { type: 'textarea', rows: '3', required: 'true' })));
    const g3 = el('div', 'grid-3');
    g3.append(field('Name', input('name', r.name, { placeholder: 'Priya S.' })), field('Place used for', input('place', r.place)), field('Rating 1–5', input('rating', r.rating, { type: 'number', min: '1', max: '5' })));
    form.append(g3);
  }, (form) => {
    const f = form.elements;
    if (!f.quote.value.trim()) { toast('The review needs a quote.', true); return false; }
    const data = { quote: f.quote.value.trim(), name: f.name.value.trim(), place: f.place.value.trim(), rating: num(f.rating.value) };
    if (review) state.catalog.reviews[index] = data; else state.catalog.reviews.push(data);
    markDirty(); renderReviews(); toast('Review saved. Publish to make it live.');
  }, review ? () => { state.catalog.reviews.splice(index, 1); markDirty(); renderReviews(); toast('Review deleted'); } : null);
}

$('#zones-search').addEventListener('input', renderZones);
$('#reviews-search').addEventListener('input', renderReviews);

/* ---------- Coupons ---------- */
function renderCoupons() {
  const list = $('#coupons-list');
  list.replaceChildren();
  if (state.mode !== 'online' || !state.status.stripe) { list.append(el('div', 'empty', 'Coupons live in Stripe. Connect the Worker with a Stripe key.')); return; }
  list.append(el('div', 'empty', 'Loading…'));
  api('/admin/coupons').then((data) => {
    state.coupons = data.coupons;
    list.replaceChildren();
    if (!data.coupons.length) list.append(el('div', 'empty', 'No coupons yet.'));
    for (const c of data.coupons) {
      const row = el('div', 'file-row');
      const name = el('div');
      name.append(el('strong', null, c.name || c.id), el('span', 'row-sub', `${c.percentOff ? `${c.percentOff}% off` : `${money(c.amountOff, c.currency)} off`} · ${c.timesRedeemed} used${c.codes.length ? ` · code${c.codes.length > 1 ? 's' : ''} ${c.codes.map((x) => x.code).join(', ')}` : ' · no code (automatic use only)'}`));
      const meta = el('div', 'meta', c.id);
      const del = el('button', 'link-button danger-link', 'Delete'); del.type = 'button';
      del.addEventListener('click', () => armDelete(del, async () => { await api(`/admin/coupons/${encodeURIComponent(c.id)}`, { method: 'DELETE' }); renderCoupons(); toast('Coupon deleted'); }));
      row.append(name, meta, del);
      list.append(row);
    }
  }).catch((error) => list.replaceChildren(el('div', 'empty', error.message)));
}
$('#coupons-refresh').addEventListener('click', renderCoupons);
$('#coupon-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const r = await api('/admin/coupons', { method: 'POST', json: { name: $('#coupon-name').value, code: $('#coupon-code').value, percent: $('#coupon-percent').value, amount: $('#coupon-amount').value, currency: state.catalog.currency, maxRedemptions: $('#coupon-max').value, expiresAt: $('#coupon-expires').value } });
    toast(r.promotionCode ? `Coupon created. Buyers can enter ${r.promotionCode.code}.` : `Coupon ${r.coupon.id} created.`);
    $('#coupon-form').reset();
    renderCoupons();
  } catch (error) { toast(error.message, true); }
});

/* ---------- Media ---------- */
async function loadMedia() {
  const [media, files] = await Promise.all([api('/admin/files?kind=media'), api('/admin/files?kind=file')]);
  state.media = media.objects.sort((a, b) => (a.uploaded < b.uploaded ? 1 : -1));
  state.files = files.objects.sort((a, b) => (a.uploaded < b.uploaded ? 1 : -1));
}
function renderMedia() {
  const grid = $('#media-grid');
  const list = $('#file-list');
  grid.replaceChildren();
  list.replaceChildren();
  if (state.mode !== 'online') {
    grid.append(el('div', 'empty', 'Uploads need the Worker. Offline, put files in dist/assets/ and reference them by path.'));
    list.append(el('div', 'empty', 'Product files are stored privately in R2 once the Worker is deployed.'));
    return;
  }
  grid.append(el('div', 'empty', 'Loading…'));
  loadMedia().then(() => {
    grid.replaceChildren();
    if (!state.media.length) grid.append(el('div', 'empty', 'No images yet.'));
    for (const m of state.media) {
      const item = el('div', 'media-item');
      const img = el('img'); img.src = m.url; img.alt = ''; img.loading = 'lazy';
      const name = el('div', 'name', m.key.split('/').pop());
      const actions = el('div', 'media-actions');
      const copy = el('button', 'link-button', 'Copy URL'); copy.type = 'button';
      copy.addEventListener('click', async () => { try { await navigator.clipboard.writeText(m.url); toast('URL copied'); } catch { toast(m.url); } });
      const del = el('button', 'link-button danger-link', 'Delete'); del.type = 'button';
      del.addEventListener('click', () => armDelete(del, async () => { await api(`/admin/upload?key=${encodeURIComponent(m.key)}`, { method: 'DELETE' }); item.remove(); toast('Deleted'); }));
      actions.append(copy, del);
      item.append(img, name, actions);
      grid.append(item);
    }
    list.replaceChildren();
    if (!state.files.length) list.append(el('div', 'empty', 'No product files yet.'));
    for (const f of state.files) {
      const row = el('div', 'file-row');
      const name = el('div', 'name', f.key);
      const meta = el('div', 'meta', `${formatBytes(f.size)} · ${formatDate(f.uploaded)}`);
      const actions = el('div', 'row-actions');
      const copy = el('button', 'link-button', 'Copy key'); copy.type = 'button';
      copy.addEventListener('click', async () => { try { await navigator.clipboard.writeText(f.key); toast('Key copied'); } catch { toast(f.key); } });
      const del = el('button', 'link-button danger-link', 'Delete'); del.type = 'button';
      del.addEventListener('click', () => armDelete(del, async () => { await api(`/admin/upload?key=${encodeURIComponent(f.key)}`, { method: 'DELETE' }); row.remove(); toast('Deleted'); }));
      actions.append(copy, del);
      row.append(name, meta, actions);
      list.append(row);
    }
  }).catch((error) => { grid.replaceChildren(el('div', 'empty', error.message)); });
}
function armDelete(button, run) {
  if (button.dataset.armed) { run().catch((e) => toast(e.message, true)); return; }
  button.dataset.armed = '1';
  const text = button.textContent;
  button.textContent = 'Confirm?';
  setTimeout(() => { delete button.dataset.armed; button.textContent = text; }, 4000);
}
async function bulkUpload(inputNode, kind) {
  const files = [...inputNode.files];
  if (!files.length) return;
  let done = 0;
  for (const file of files) {
    try { await uploadFile(file, kind); done += 1; toast(`Uploaded ${done} of ${files.length}`); }
    catch (error) { toast(`${file.name}: ${error.message}`, true); }
  }
  inputNode.value = '';
  renderMedia();
}
$('#media-upload').addEventListener('change', (e) => bulkUpload(e.target, 'media'));
$('#file-upload').addEventListener('change', (e) => bulkUpload(e.target, 'file'));

/* ---------- Orders ---------- */
async function loadOrders() {
  const days = Number($('#orders-days').value) || 30;
  try {
    state.orders = await api(`/admin/orders?days=${days}`);
    state.ordersError = '';
  } catch (error) {
    state.orders = null;
    state.ordersError = error.message;
    throw error;
  } finally {
    if (state.view === 'orders') renderOrders();
    if (state.view === 'overview') renderOverview();
  }
}
function renderOrdersTable(wrap, orders, compact) {
  wrap.replaceChildren();
  if (state.mode !== 'online') { wrap.append(el('div', 'empty', 'Orders appear here once the Worker is connected to Stripe.')); return; }
  if (!orders.length) { wrap.append(el('div', 'empty', state.orders ? 'No paid orders in this period.' : (state.ordersError ? `Orders unavailable: ${state.ordersError}` : 'Loading orders…'))); return; }
  const table = el('table');
  const head = el('tr');
  for (const h of compact ? ['Date', 'Buyer', 'Items', 'Total'] : ['Date', 'Buyer', 'Country', 'Items', 'Gift', 'Total', '']) head.append(el('th', h === 'Total' ? 'num' : '', h));
  table.append(head);
  for (const o of orders) {
    const tr = el('tr');
    tr.append(el('td', null, formatDate(o.created)), el('td', null, o.email || o.name || '—'));
    if (!compact) tr.append(el('td', null, o.country || '—'));
    tr.append(el('td', null, o.items.map((i) => i.name).join(', ')));
    if (!compact) tr.append(el('td', null, o.gift || '—'));
    const total = el('td', 'num', money(o.total, o.currency));
    if (o.refunded) total.append(' ', Object.assign(el('span', 'pill warn'), { textContent: 'refunded' }));
    tr.append(total);
    if (!compact) {
      const actions = el('td', 'row-actions');
      if (!o.refunded) { const refund = el('button', 'link-button danger-link', 'Refund'); refund.type = 'button'; refund.addEventListener('click', () => armDelete(refund, async () => { const r = await api('/admin/refund', { method: 'POST', json: { sessionId: o.id } }); toast(`Refunded ${money(r.refund.amount, r.refund.currency)}.`); loadOrders().catch(() => {}); })); actions.append(refund); }
      tr.append(actions);
    }
    table.append(tr);
  }
  wrap.append(table);
}
function renderOrders() {
  const stats = $('#orders-stats');
  stats.replaceChildren();
  if (state.orders) {
    const s = state.orders.summary;
    const tiles = [['Orders', s.count, `last ${s.days} days`], ['Revenue', Object.entries(s.byCurrency).map(([c, v]) => money(v, c)).join(' · ') || '—', 'paid through Stripe'], ['Average order', s.count ? Object.entries(s.byCurrency).map(([c, v]) => money(v / s.count, c)).join(' · ') : '—', ''], ['Best seller', s.byTitle[0] ? s.byTitle[0].name : '—', s.byTitle[0] ? `${s.byTitle[0].count} sold` : '']];
    for (const [label, value, detail] of tiles) { const t = el('div', 'stat'); t.append(el('span', null, label), el('strong', null, String(value)), el('small', null, detail)); stats.append(t); }
    const by = $('#orders-by-title');
    by.replaceChildren();
    const table = el('table');
    const head = el('tr'); for (const h of ['Title', 'Sold', 'Revenue']) head.append(el('th', h !== 'Title' ? 'num' : '', h)); table.append(head);
    for (const t of s.byTitle) { const tr = el('tr'); tr.append(el('td', null, t.name), el('td', 'num', String(t.count)), el('td', 'num', money(t.revenue, Object.keys(s.byCurrency)[0]))); table.append(tr); }
    by.append(s.byTitle.length ? table : el('div', 'empty', 'No sales yet.'));
  }
  renderOrdersTable($('#orders-table'), state.orders ? state.orders.orders : [], false);
}
$('#orders-days').addEventListener('change', () => loadOrders().catch((e) => toast(e.message, true)));
$('#orders-refresh').addEventListener('click', () => loadOrders().then(() => toast('Orders refreshed')).catch((e) => toast(e.message, true)));
$('#orders-export').addEventListener('click', () => {
  if (!state.orders) return;
  download('orders.csv', csv([['Date', 'Email', 'Name', 'Country', 'Items', 'Gift', 'Total', 'Currency', 'Session'], ...state.orders.orders.map((o) => [o.created, o.email, o.name, o.country, o.items.map((i) => i.name).join('; '), o.gift, o.total, o.currency, o.id])]), 'text/csv');
});

/* ---------- Requests ---------- */
async function loadRequests() {
  const data = await api('/admin/requests');
  state.requests = data.requests;
  renderCounts();
  if (state.view === 'requests') renderRequests();
}
function renderRequests() {
  const wrap = $('#requests-table');
  fillTitleSelect($('#notify-title'));
  wrap.replaceChildren();
  if (state.mode !== 'online') { wrap.append(el('div', 'empty', 'Requests are stored by the Worker.')); return; }
  if (!state.requests.length) { wrap.append(el('div', 'empty', 'No requests yet.')); return; }
  const table = el('table');
  const head = el('tr'); for (const h of ['Place', 'Email', 'Asked', '']) head.append(el('th', null, h)); table.append(head);
  for (const r of state.requests) {
    const tr = el('tr');
    const actions = el('td', 'row-actions');
    const del = el('button', 'link-button danger-link', 'Clear'); del.type = 'button';
    del.addEventListener('click', () => armDelete(del, async () => { await api(`/admin/requests/${encodeURIComponent(r.key)}`, { method: 'DELETE' }); state.requests = state.requests.filter((x) => x.key !== r.key); renderCounts(); renderRequests(); }));
    actions.append(del);
    tr.append(el('td', null, r.place), el('td', null, r.email), el('td', null, formatDate(r.at)), actions);
    table.append(tr);
  }
  wrap.append(table);
}
$('#requests-export').addEventListener('click', () => download('requests.csv', csv([['Place', 'Email', 'Asked'], ...state.requests.map((r) => [r.place, r.email, r.at])]), 'text/csv'));
function fillTitleSelect(select) {
  select.replaceChildren(...state.catalog.guides.map((g) => { const o = el('option', null, `${g.title} · ${[g.country, g.state].filter(Boolean).join(' / ')}`); o.value = g.id; return o; }));
}
$('#notify-count').addEventListener('click', async () => {
  try { const r = await api('/admin/requests/notify', { method: 'POST', json: { guideId: $('#notify-title').value, dryRun: true } }); $('#notify-note').textContent = r.matched ? `${r.matched} request${r.matched === 1 ? '' : 's'} match: ${r.places.join(', ')}.` : 'No requests match this title yet.'; }
  catch (error) { toast(error.message, true); }
});
$('#notify-send').addEventListener('click', () => armDelete($('#notify-send'), async () => {
  const r = await api('/admin/requests/notify', { method: 'POST', json: { guideId: $('#notify-title').value, subject: $('#notify-subject').value, message: $('#notify-message').value } });
  toast(`Emailed ${r.sent} reader${r.sent === 1 ? '' : 's'}.`);
  loadRequests().catch(() => {});
}));

/* ---------- Announce ---------- */
function renderAnnounce() {
  const sel = $('#announce-title');
  sel.replaceChildren();
  const options = [];
  for (const g of state.catalog.guides) {
    if (g.priceId) options.push([g.priceId, g.title]);
    for (const v of g.variants || []) if (v.priceId) options.push([v.priceId, `${g.title} · ${v.label}`]);
  }
  if (!options.length) sel.append(Object.assign(el('option', null, 'No titles with a Stripe price yet'), { value: '' }));
  for (const [v, label] of options) { const o = el('option', null, label); o.value = v; sel.append(o); }
  $('#announce-note').textContent = state.mode === 'online' ? (state.status.email ? '' : 'E-mail is not configured on the Worker (RESEND_API_KEY and FROM_EMAIL).') : 'Announcements need the Worker.';
}
$('#announce-count').addEventListener('click', async () => {
  try { const r = await api('/admin/announce', { method: 'POST', json: { priceId: $('#announce-title').value, subject: 'x', message: 'x', dryRun: true } }); $('#announce-note').textContent = `${r.recipients} buyer${r.recipients === 1 ? '' : 's'} would receive this.`; }
  catch (e) { toast(e.message, true); }
});
$('#announce-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = $('#announce-send');
  if (!button.dataset.armed) { button.dataset.armed = '1'; button.textContent = 'Click again to send'; setTimeout(() => { delete button.dataset.armed; button.textContent = 'Send'; }, 5000); return; }
  button.disabled = true;
  try {
    const payload = { priceId: $('#announce-title').value, subject: $('#announce-subject').value, message: $('#announce-message').value };
    const r = await api($('#announce-links').checked ? '/admin/deliver' : '/admin/announce', { method: 'POST', json: payload });
    toast(`Sent to ${r.sent} buyer${r.sent === 1 ? '' : 's'}.`); $('#announce-form').reset(); renderAnnounce();
  }
  catch (e) { toast(e.message, true); }
  finally { button.disabled = false; delete button.dataset.armed; button.textContent = 'Send'; }
});

/* ---------- Settings ---------- */
function renderSettings() {
  const c = state.catalog;
  const f = $('#settings-form').elements;
  f.storeName.value = c.storeName || '';
  f.siteUrl.value = c.siteUrl || '';
  f.currency.value = c.currency || 'CAD';
  f.currencies.value = (c.currencies || []).join(', ');
  f.contactEmail.value = c.contactEmail || '';
  f.plausibleDomain.value = (c.analytics && c.analytics.plausibleDomain) || '';
  f.refundPolicy.value = c.refundPolicy || '';
  f['cartDiscount.percent'].value = c.cartDiscount.percent || '';
  f['cartDiscount.minItems'].value = c.cartDiscount.minItems || '';
  f['cartDiscount.couponId'].value = c.cartDiscount.couponId || '';
  f.countryPages.checked = c.countryPages === true;
  const hv = c.heroVideo && typeof c.heroVideo === 'object' ? c.heroVideo : {};
  f['heroVideo.src'].value = hv.src || '';
  f['heroVideo.poster'].value = hv.poster || '';
  f['heroVideo.aspect'].value = hv.aspect || '4 / 5';
  f['heroVideo.tag'].value = hv.tag || '';
  f['heroVideo.playLabel'].value = hv.playLabel || '';
  f['heroVideo.captionLeft'].value = hv.captionLeft || '';
  f['heroVideo.captionRight'].value = hv.captionRight || '';
  f['heroVideo.autoplay'].checked = hv.autoplay !== false;
  f['heroVideo.loop'].checked = hv.loop !== false;
  const posterImg = $('.image-picker[data-target="heroVideo.poster"] img');
  posterImg.hidden = !hv.poster; if (hv.poster) posterImg.src = assetUrl(hv.poster);
  for (const k of ['instagram', 'tiktok', 'youtube', 'pinterest', 'x', 'facebook']) f[`social.${k}`].value = (c.social && c.social[k]) || '';
  renderSecurity();
  renderTemplates();
  for (const k of ['name', 'role', 'photo', 'bio', 'note']) f[`author.${k}`].value = (c.author && c.author[k]) || '';
  const picker = $('.image-picker[data-target="author.photo"]');
  const img = picker.querySelector('img');
  img.hidden = !f['author.photo'].value; if (f['author.photo'].value) img.src = assetUrl(f['author.photo'].value);
  renderRegions();
}
function renderRegions() {
  const box = $('#regions-editor');
  box.replaceChildren();
  const regions = state.catalog.regions;
  const countries = [...new Set([...Object.keys(regions), ...state.catalog.guides.map((g) => g.country).filter(Boolean)])].sort();
  for (const country of countries) {
    const wrap = el('div', 'region-box');
    const head = el('div', 'fieldset-title');
    head.append(el('span', null, country));
    const remove = el('button', 'link-button danger-link', 'Remove'); remove.type = 'button';
    remove.addEventListener('click', () => { delete regions[country]; markDirty(); renderRegions(); });
    head.append(remove);
    const area = document.createElement('textarea');
    area.value = (regions[country] || []).join('\n');
    area.placeholder = 'One state or province per line';
    area.addEventListener('change', () => { regions[country] = lines(area.value); markDirty(); });
    wrap.append(head, area);
    box.append(wrap);
  }
}
$('#region-add').addEventListener('click', () => {
  const box = $('#regions-editor');
  if (box.querySelector('.region-new')) return;
  const wrap = el('div', 'region-box region-new');
  const name = input('newCountry', '', { placeholder: 'Country name' });
  const add = el('button', 'button button-sm', 'Add'); add.type = 'button';
  add.addEventListener('click', () => { const n = name.value.trim(); if (!n) return; state.catalog.regions[n] = state.catalog.regions[n] || []; markDirty(); renderRegions(); });
  wrap.append(field('New country', name), add);
  box.prepend(wrap);
  name.focus();
});
$('#settings-form').addEventListener('change', (event) => {
  if (!event.target.name || /^tpl\./.test(event.target.name) || /^(pw-|twostep)/.test(event.target.id || '')) return;
  const c = state.catalog;
  const f = $('#settings-form').elements;
  c.storeName = f.storeName.value.trim();
  c.siteUrl = f.siteUrl.value.trim();
  c.currency = f.currency.value.trim().toUpperCase() || 'CAD';
  c.currencies = [...new Set([c.currency, ...f.currencies.value.split(',').map((s) => s.trim().toUpperCase()).filter((s) => /^[A-Z]{3}$/.test(s))])];
  c.contactEmail = f.contactEmail.value.trim();
  c.analytics = { plausibleDomain: f.plausibleDomain.value.trim() };
  c.refundPolicy = f.refundPolicy.value.trim();
  const pct = Number(f['cartDiscount.percent'].value); const min = Number(f['cartDiscount.minItems'].value);
  c.cartDiscount = pct > 0 ? { percent: pct, minItems: Math.max(2, min || 2), couponId: f['cartDiscount.couponId'].value.trim() } : {};
  c.countryPages = f.countryPages.checked;
  const src = f['heroVideo.src'].value.trim();
  c.heroVideo = src ? { src, poster: f['heroVideo.poster'].value.trim(), aspect: f['heroVideo.aspect'].value, tag: f['heroVideo.tag'].value.trim(), playLabel: f['heroVideo.playLabel'].value.trim(), captionLeft: f['heroVideo.captionLeft'].value.trim(), captionRight: f['heroVideo.captionRight'].value.trim(), autoplay: f['heroVideo.autoplay'].checked, loop: f['heroVideo.loop'].checked } : null;
  if (event.target.name === 'heroVideo.poster') { const img = $('.image-picker[data-target="heroVideo.poster"] img'); img.hidden = !c.heroVideo || !c.heroVideo.poster; if (c.heroVideo && c.heroVideo.poster) img.src = assetUrl(c.heroVideo.poster); }
  c.social = {}; for (const k of ['instagram', 'tiktok', 'youtube', 'pinterest', 'x', 'facebook']) c.social[k] = f[`social.${k}`].value.trim();
  c.author = {}; for (const k of ['name', 'role', 'photo', 'bio', 'note']) c.author[k] = f[`author.${k}`].value.trim();
  if (event.target.name === 'author.photo') { const img = $('.image-picker[data-target="author.photo"] img'); img.hidden = !c.author.photo; if (c.author.photo) img.src = assetUrl(c.author.photo); }
  markDirty();
});
$('#hero-video-upload').addEventListener('change', async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  const label = event.target.parentElement;
  label.firstChild.textContent = 'Uploading…';
  try { const r = await uploadFile(file, 'media'); const f = $('#settings-form').elements; f['heroVideo.src'].value = r.url; f['heroVideo.src'].dispatchEvent(new Event('change', { bubbles: true })); toast(`Video uploaded (${formatBytes(file.size)}).`); }
  catch (e) { toast(e.message, true); }
  finally { label.firstChild.textContent = 'Upload video'; event.target.value = ''; }
});
$('.image-picker[data-target="heroVideo.poster"] input[type="file"]').addEventListener('change', async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  try { const aspect = $('#settings-form').elements['heroVideo.aspect'].value.split('/').map(Number); const r = await uploadFile(await processImage(file, { aspect: aspect[0] / aspect[1], maxWidth: 1200 }), 'media'); const f = $('#settings-form').elements; f['heroVideo.poster'].value = r.url; f['heroVideo.poster'].dispatchEvent(new Event('change', { bubbles: true })); toast('Poster uploaded'); }
  catch (e) { toast(e.message, true); }
  event.target.value = '';
});
$('#hero-video-clear').addEventListener('click', () => { const f = $('#settings-form').elements; f['heroVideo.src'].value = ''; f['heroVideo.poster'].value = ''; f['heroVideo.src'].dispatchEvent(new Event('change', { bubbles: true })); toast('Video removed. The photo shows again after you publish.'); });
$('.image-picker[data-target="author.photo"] input[type="file"]').addEventListener('change', async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  try { const r = await uploadFile(file, 'media'); const f = $('#settings-form').elements; f['author.photo'].value = r.url; f['author.photo'].dispatchEvent(new Event('change', { bubbles: true })); toast('Photo uploaded'); }
  catch (e) { toast(e.message, true); }
  event.target.value = '';
});

$('#discount-create').addEventListener('click', async () => {
  const f = $('#settings-form').elements;
  const percent = Number(f['cartDiscount.percent'].value); const minItems = Number(f['cartDiscount.minItems'].value) || 2;
  if (!(percent > 0 && percent < 100)) { toast('Enter the percent off first.', true); return; }
  try {
    if (state.dirty) await api('/admin/catalog', { method: 'PUT', json: state.catalog });
    const r = await api('/admin/stripe/coupon', { method: 'POST', json: { percent, minItems, name: `${percent}% off ${minItems}+ titles` } });
    state.catalog.cartDiscount = r.cartDiscount; f['cartDiscount.couponId'].value = r.couponId; markClean();
    toast(`Coupon ${r.couponId} created and published.`);
  } catch (error) { toast(error.message, true); }
});

async function renderSecurity() {
  const panel = $('#security-panel');
  panel.hidden = state.mode !== 'online';
  if (panel.hidden) return;
  try {
    const info = await api('/admin/security');
    $('#twostep-toggle').checked = info.twoStep;
    $('#twostep-toggle').disabled = !info.emailConfigured;
    $('#twostep-hint').textContent = info.emailConfigured ? `Codes go to ${info.ownerEmail}.` : 'Set RESEND_API_KEY, FROM_EMAIL, and CONTACT_EMAIL on the Worker to enable two-step sign-in and password recovery.';
  } catch (error) { $('#twostep-hint').textContent = error.message; }
}
$('#twostep-toggle').addEventListener('change', async (event) => {
  try { await api('/admin/security', { method: 'PUT', json: { twoStep: event.target.checked } }); toast(event.target.checked ? 'Two-step sign-in is on.' : 'Two-step sign-in is off.'); }
  catch (error) { event.target.checked = !event.target.checked; toast(error.message, true); }
});
$('#pw-change').addEventListener('click', async () => {
  try {
    const r = await api('/admin/password', { method: 'POST', json: { current: $('#pw-current').value, next: $('#pw-next').value } });
    state.token = r.token;
    try { (localStorage.getItem(KEYS.remember) === '0' ? sessionStorage : localStorage).setItem(KEYS.token, r.token); } catch { /* ignore */ }
    $('#pw-current').value = ''; $('#pw-next').value = '';
    toast('Password changed. Other devices will need to sign in again.');
  } catch (error) { toast(error.message, true); }
});

async function renderTemplates() {
  const panel = $('#templates-panel');
  panel.hidden = state.mode !== 'online';
  if (panel.hidden) return;
  const grid = $('#templates-grid');
  grid.replaceChildren();
  try {
    state.templates = await api('/admin/templates');
    const labels = { downloads: 'Download links (resend)', gift: 'Gift delivery', release: 'Pre-order release', request: 'Requested place is ready' };
    for (const [key, label] of Object.entries(labels)) {
      const box = el('div', 'templates-item');
      box.append(el('strong', null, label));
      const subject = input(`tpl.${key}.subject`, state.templates[key].subject, { placeholder: 'Subject' });
      const intro = input(`tpl.${key}.intro`, state.templates[key].intro, { type: 'textarea', rows: '2', placeholder: 'Opening line' });
      box.append(field('Subject', subject), field('Opening line', intro));
      grid.append(box);
    }
  } catch (error) { grid.append(el('div', 'empty', error.message)); }
}
$('#templates-save').addEventListener('click', async () => {
  const f = $('#settings-form').elements;
  const body = {};
  for (const key of ['downloads', 'gift', 'release', 'request']) body[key] = { subject: f[`tpl.${key}.subject`].value, intro: f[`tpl.${key}.intro`].value };
  try { await api('/admin/templates', { method: 'PUT', json: body }); toast('Templates saved.'); }
  catch (error) { toast(error.message, true); }
});

/* ---------- Tools ---------- */
function renderTools() {
  renderActivity();
  const list = $('#backups-list');
  list.replaceChildren();
  if (state.mode !== 'online') { list.append(el('div', 'empty', 'Backups are kept by the Worker every time you publish.')); return; }
  api('/admin/backups').then((data) => {
    list.replaceChildren();
    if (!data.backups.length) list.append(el('div', 'empty', 'No backups yet.'));
    for (const b of data.backups) {
      const row = el('div', 'file-row');
      const restore = el('button', 'link-button', 'Restore'); restore.type = 'button';
      restore.addEventListener('click', async () => { try { const cat = await api(`/admin/backups/${encodeURIComponent(b.key)}`); state.catalog = normaliseCatalog(cat); markDirty(); toast('Backup restored into the editor. Publish to make it live.'); showView('titles'); } catch (e) { toast(e.message, true); } });
      row.append(el('div', 'name', formatDate(b.at)), el('div', 'meta', 'catalog'), restore);
      list.append(row);
    }
  }).catch((e) => list.replaceChildren(el('div', 'empty', e.message)));
}
$('#export-json').addEventListener('click', () => download('guides.json', JSON.stringify(state.catalog, null, 2)));
$('#import-json').addEventListener('change', async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  try { const data = JSON.parse(await file.text()); if (!Array.isArray(data.guides)) throw new Error('That file has no guides array.'); state.catalog = normaliseCatalog(data); markDirty(); toast('Imported. Review, then publish.'); showView('titles'); }
  catch (e) { toast(e.message, true); }
  event.target.value = '';
});
$('#backups-refresh').addEventListener('click', renderTools);
function renderActivity() {
  const box = $('#activity-list');
  box.replaceChildren();
  if (state.mode !== 'online') { box.append(el('div', 'empty', 'The activity log is kept by the Worker.')); return; }
  api('/admin/activity').then((data) => {
    box.replaceChildren();
    if (!data.events.length) { box.append(el('div', 'empty', 'Nothing logged yet.')); return; }
    const table = el('table');
    const head = el('tr'); for (const h of ['When', 'Action', 'Detail', 'From']) head.append(el('th', null, h)); table.append(head);
    for (const e of data.events) { const tr = el('tr'); tr.append(el('td', null, formatDate(e.at)), el('td', null, e.action), el('td', null, e.detail || ''), el('td', null, e.ip || '')); table.append(tr); }
    box.append(table);
  }).catch((error) => box.replaceChildren(el('div', 'empty', error.message)));
}
$('#activity-refresh').addEventListener('click', renderActivity);
$('#discard-draft').addEventListener('click', async () => {
  try { localStorage.removeItem(KEYS.draft); } catch { /* ignore */ }
  if (state.mode === 'online') await loadCatalogOnline(); else await loadCatalogOffline();
  showView('overview');
  toast('Local draft discarded.');
});

boot();
