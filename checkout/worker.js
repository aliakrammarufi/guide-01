/**
 * Marufi Digital — store backend (Cloudflare Worker, no dependencies).
 *
 * Public routes (JSON unless noted):
 *   POST /session   { items: ["price_..."], gift?: { email, message } }   → { url }   one Stripe Checkout for the cart
 *   GET  /order?session_id=cs_...                                          → { email, items: [{ name, url, expires }] }
 *   GET  /file?t=<signed token>                                            → streams a purchased file from R2
 *   GET  /media/<key>                                                      → serves an uploaded image (covers, samples, zones)
 *   GET  /catalog                                                          → the live catalog saved from the admin dashboard
 *   POST /notify    { email, place }                                       → { ok }
 *   POST /resend    { email }                                              → { ok }
 *
 * Admin routes (Bearer session token from /admin/login, or Bearer ADMIN_TOKEN):
 *   POST /admin/login            { password }                → { token, exp }
 *   GET  /admin/status                                       → what is configured
 *   GET  /admin/catalog                                      → catalog JSON
 *   PUT  /admin/catalog          <catalog JSON>              → { ok, savedAt }
 *   GET  /admin/backups                                      → recent catalog backups
 *   GET  /admin/backups/<key>                                → one backup
 *   PUT  /admin/upload?kind=media|file&name=<filename>  body → { key, url }
 *   DELETE /admin/upload?key=<key>                           → { ok }
 *   GET  /admin/files?kind=media|file                        → { objects: [...] }
 *   POST /admin/stripe/product   { guideId, variantIndex? }  → { guide }   creates Product + Price + Payment Link
 *   GET  /admin/orders?days=30                               → { orders, summary }
 *   GET  /admin/requests                                     → { requests }
 *   DELETE /admin/requests/<key>                             → { ok }
 *   POST /admin/announce         { priceId, subject, message } → { sent }
 *
 * Environment (wrangler.toml [vars] + `wrangler secret put`):
 *   STRIPE_SECRET_KEY   secret
 *   ADMIN_PASSWORD      secret; the dashboard password
 *   DOWNLOAD_SECRET     secret; signs download links and admin sessions
 *   SUCCESS_URL, CANCEL_URL, ALLOWED_ORIGINS, DOWNLOAD_HOURS, FROM_EMAIL, CONTACT_EMAIL, SITE_URL
 *   RESEND_API_KEY      secret, optional; e-mail through resend.com
 *   ADMIN_TOKEN         secret, optional; alternative bearer token for scripts
 * Bindings:
 *   FILES   R2 bucket: media/... (public) and files/... (purchased downloads, private)
 *   STORE   KV namespace: catalog, backups, notify requests, gift bookkeeping, login throttling
 */
export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const origin = request.headers.get('Origin') || '';
    const allowedOrigins = String(env.ALLOWED_ORIGINS || '').split(',').map((s) => s.trim()).filter(Boolean);
    const originAllowed = !allowedOrigins.length || !origin || allowedOrigins.includes(origin);
    const cors = {
      'Access-Control-Allow-Origin': allowedOrigins.includes(origin) ? origin : (allowedOrigins[0] || '*'),
      'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type, Authorization, X-File-Name',
      'Access-Control-Max-Age': '86400',
      'Vary': 'Origin'
    };
    const json = (body, status = 200, extra = {}) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', ...cors, ...extra } });

    if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors });
    const path = url.pathname.replace(/\/+$/, '') || '/';
    const publicFile = path === '/file' || path.startsWith('/media/');
    if (!publicFile && !originAllowed) return json({ error: 'Origin not allowed' }, 403);

    try {
      // Public
      if (path === '/session' && request.method === 'POST') return json(await createSession(await readJson(request), env));
      if (path === '/order' && request.method === 'GET') return json(await orderDetails(url.searchParams.get('session_id'), env, url));
      if (path === '/file' && request.method === 'GET') return serveFile(url.searchParams.get('t'), env);
      if (path.startsWith('/media/') && request.method === 'GET') return serveMedia(path.slice('/media/'.length), env, cors);
      if (path === '/catalog' && request.method === 'GET') return catalogResponse(env, json);
      if (path === '/notify' && request.method === 'POST') return json(await notifyRequest(await readJson(request), env));
      if (path === '/resend' && request.method === 'POST') return json(await resendLinks(await readJson(request), env, url));
      if (path === '/announce' && request.method === 'POST') { await requireAdmin(request, env); return json(await announce(await readJson(request), env)); }

      // Admin
      if (path === '/admin/login' && request.method === 'POST') return json(await login(request, await readJson(request), env));
      if (path.startsWith('/admin/')) {
        await requireAdmin(request, env);
        const sub = path.slice('/admin'.length);
        if (sub === '/status' && request.method === 'GET') return json(status(env));
        if (sub === '/catalog' && request.method === 'GET') return json(await loadCatalog(env));
        if (sub === '/catalog' && request.method === 'PUT') return json(await saveCatalog(await readJson(request), env));
        if (sub === '/backups' && request.method === 'GET') return json(await listBackups(env));
        if (sub.startsWith('/backups/') && request.method === 'GET') return json(await getBackup(decodeURIComponent(sub.slice('/backups/'.length)), env));
        if (sub === '/upload' && request.method === 'PUT') return json(await upload(request, url, env));
        if (sub === '/upload' && request.method === 'DELETE') return json(await removeUpload(url.searchParams.get('key'), env));
        if (sub === '/files' && request.method === 'GET') return json(await listFiles(url.searchParams.get('kind'), env, url));
        if (sub === '/stripe/product' && request.method === 'POST') return json(await createStripeProduct(await readJson(request), env, url));
        if (sub === '/orders' && request.method === 'GET') return json(await orders(Number(url.searchParams.get('days')) || 30, env));
        if (sub === '/requests' && request.method === 'GET') return json(await listRequests(env));
        if (sub.startsWith('/requests/') && request.method === 'DELETE') return json(await deleteRequest(decodeURIComponent(sub.slice('/requests/'.length)), env));
        if (sub === '/announce' && request.method === 'POST') return json(await announce(await readJson(request), env));
      }
      return json({ error: 'Not found' }, 404);
    } catch (error) {
      const status = error.status || 500;
      if (status === 500) console.error(error);
      return json({ error: error.message || 'Something went wrong' }, status);
    }
  }
};

/* ---------- Helpers ---------- */
function fail(message, status = 400) {
  const error = new Error(message);
  error.status = status;
  return error;
}

async function readJson(request) {
  try {
    return await request.json();
  } catch {
    throw fail('Invalid request body');
  }
}

function isEmail(value) {
  return typeof value === 'string' && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value) && value.length < 200;
}

function clientIp(request) {
  return request.headers.get('CF-Connecting-IP') || request.headers.get('X-Forwarded-For') || 'unknown';
}

async function stripe(env, method, endpoint, params) {
  if (!env.STRIPE_SECRET_KEY) throw fail('Stripe is not configured on the server', 500);
  const init = { method, headers: { Authorization: `Bearer ${env.STRIPE_SECRET_KEY}` } };
  let target = `https://api.stripe.com/v1${endpoint}`;
  if (params) {
    if (method === 'GET') target += (target.includes('?') ? '&' : '?') + params.toString();
    else {
      init.headers['Content-Type'] = 'application/x-www-form-urlencoded';
      init.body = params;
    }
  }
  const response = await fetch(target, init);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw fail((data.error && data.error.message) || 'Stripe request failed', 502);
  return data;
}

async function sendEmail(env, to, subject, html) {
  if (!env.RESEND_API_KEY || !env.FROM_EMAIL) throw fail('E-mail is not configured on the server', 500);
  const response = await fetch('https://api.resend.com/emails', {
    method: 'POST',
    headers: { Authorization: `Bearer ${env.RESEND_API_KEY}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ from: env.FROM_EMAIL, to: [to], subject, html })
  });
  if (!response.ok) throw fail('E-mail could not be sent', 502);
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/* ---------- Signing (download links and admin sessions) ---------- */
const enc = new TextEncoder();
function b64url(bytes) {
  return btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
function fromB64url(text) {
  const padded = text.replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((text.length + 3) % 4);
  return Uint8Array.from(atob(padded), (c) => c.charCodeAt(0));
}
async function hmacKey(env) {
  if (!env.DOWNLOAD_SECRET) throw fail('DOWNLOAD_SECRET is not configured on the server', 500);
  return crypto.subtle.importKey('raw', enc.encode(env.DOWNLOAD_SECRET), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign', 'verify']);
}
async function signToken(env, payload) {
  const body = b64url(enc.encode(JSON.stringify(payload)));
  const sig = await crypto.subtle.sign('HMAC', await hmacKey(env), enc.encode(body));
  return `${body}.${b64url(sig)}`;
}
async function verifyToken(env, token) {
  const [body, sig] = String(token || '').split('.');
  if (!body || !sig) return null;
  let ok = false;
  try {
    ok = await crypto.subtle.verify('HMAC', await hmacKey(env), fromB64url(sig), enc.encode(body));
  } catch {
    return null;
  }
  if (!ok) return null;
  const payload = JSON.parse(new TextDecoder().decode(fromB64url(body)));
  if (!payload.exp || Date.now() > payload.exp) return null;
  return payload;
}

async function sha256(text) {
  return new Uint8Array(await crypto.subtle.digest('SHA-256', enc.encode(text)));
}
function equalBytes(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i += 1) diff |= a[i] ^ b[i];
  return diff === 0;
}

/* ---------- Admin auth ---------- */
async function login(request, body, env) {
  if (!env.ADMIN_PASSWORD) throw fail('ADMIN_PASSWORD is not configured on the server', 500);
  const ip = clientIp(request);
  const throttleKey = `login:${ip}`;
  if (env.STORE) {
    const attempts = Number(await env.STORE.get(throttleKey)) || 0;
    if (attempts >= 8) throw fail('Too many attempts. Try again in 15 minutes.', 429);
  }
  const ok = equalBytes(await sha256(String(body.password || '')), await sha256(env.ADMIN_PASSWORD));
  if (!ok) {
    if (env.STORE) {
      const attempts = Number(await env.STORE.get(throttleKey)) || 0;
      await env.STORE.put(throttleKey, String(attempts + 1), { expirationTtl: 900 });
    }
    throw fail('Wrong password', 401);
  }
  if (env.STORE) await env.STORE.delete(throttleKey);
  const exp = Date.now() + 12 * 3600 * 1000;
  return { token: await signToken(env, { role: 'admin', exp }), exp };
}

async function requireAdmin(request, env) {
  const auth = request.headers.get('Authorization') || '';
  const token = auth.startsWith('Bearer ') ? auth.slice(7) : '';
  if (!token) throw fail('Sign in required', 401);
  if (env.ADMIN_TOKEN && token === env.ADMIN_TOKEN) return;
  const payload = await verifyToken(env, token);
  if (!payload || payload.role !== 'admin') throw fail('Session expired. Sign in again.', 401);
}

function status(env) {
  return {
    stripe: Boolean(env.STRIPE_SECRET_KEY),
    stripeMode: env.STRIPE_SECRET_KEY ? (String(env.STRIPE_SECRET_KEY).startsWith('sk_live') ? 'live' : 'test') : '',
    files: Boolean(env.FILES),
    store: Boolean(env.STORE),
    email: Boolean(env.RESEND_API_KEY && env.FROM_EMAIL),
    downloads: Boolean(env.DOWNLOAD_SECRET),
    successUrl: env.SUCCESS_URL || '',
    siteUrl: env.SITE_URL || ''
  };
}

/* ---------- Catalog (KV) ---------- */
const EMPTY_CATALOG = { storeName: 'Marufi Digital', currency: 'CAD', currencies: ['CAD'], guides: [], zones: [], reviews: [], regions: {}, author: {}, social: {} };

async function loadCatalog(env) {
  if (!env.STORE) throw fail('The STORE KV namespace is not bound', 500);
  const raw = await env.STORE.get('catalog');
  if (!raw) return { ...EMPTY_CATALOG, _empty: true };
  return JSON.parse(raw);
}

async function catalogResponse(env, json) {
  if (!env.STORE) return json({ error: 'No catalog' }, 404);
  const raw = await env.STORE.get('catalog');
  if (!raw) return json({ error: 'No catalog' }, 404, { 'Cache-Control': 'no-store' });
  return new Response(raw, { headers: { 'Content-Type': 'application/json', 'Cache-Control': 'public, max-age=60', 'Access-Control-Allow-Origin': '*' } });
}

async function saveCatalog(catalog, env) {
  if (!env.STORE) throw fail('The STORE KV namespace is not bound', 500);
  if (!catalog || typeof catalog !== 'object' || !Array.isArray(catalog.guides)) throw fail('The catalog must be an object with a guides array');
  delete catalog._empty;
  catalog.updatedAt = new Date().toISOString();
  const text = JSON.stringify(catalog);
  if (text.length > 4 * 1024 * 1024) throw fail('The catalog is too large (4 MB limit)');
  const previous = await env.STORE.get('catalog');
  if (previous) await env.STORE.put(`backup:${Date.now()}`, previous, { expirationTtl: 60 * 60 * 24 * 90 });
  await env.STORE.put('catalog', text);
  return { ok: true, savedAt: catalog.updatedAt, guides: catalog.guides.length };
}

async function listBackups(env) {
  if (!env.STORE) throw fail('The STORE KV namespace is not bound', 500);
  const list = await env.STORE.list({ prefix: 'backup:', limit: 50 });
  return { backups: list.keys.map((k) => ({ key: k.name, at: new Date(Number(k.name.slice(7))).toISOString() })).sort((a, b) => (a.key < b.key ? 1 : -1)) };
}

async function getBackup(key, env) {
  if (!/^backup:\d+$/.test(key)) throw fail('Invalid backup key');
  const raw = await env.STORE.get(key);
  if (!raw) throw fail('Backup not found', 404);
  return JSON.parse(raw);
}

/* ---------- Uploads (R2) ---------- */
const MEDIA_TYPES = { jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png', webp: 'image/webp', avif: 'image/avif', gif: 'image/gif', svg: 'image/svg+xml' };
const FILE_TYPES = { pdf: 'application/pdf', epub: 'application/epub+zip', zip: 'application/zip', mobi: 'application/x-mobipocket-ebook', mp3: 'audio/mpeg', mp4: 'video/mp4', jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png', webp: 'image/webp' };

function safeName(name) {
  const base = String(name || 'file').toLowerCase().replace(/[^a-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 80);
  return base || 'file';
}

async function upload(request, url, env) {
  if (!env.FILES) throw fail('The FILES R2 bucket is not bound', 500);
  const kind = url.searchParams.get('kind') === 'file' ? 'file' : 'media';
  const name = safeName(url.searchParams.get('name') || request.headers.get('X-File-Name'));
  const ext = name.includes('.') ? name.split('.').pop() : '';
  const types = kind === 'file' ? FILE_TYPES : MEDIA_TYPES;
  if (!types[ext]) throw fail(`Unsupported ${kind} type .${ext}`);
  const length = Number(request.headers.get('Content-Length')) || 0;
  const limit = kind === 'file' ? 95 * 1024 * 1024 : 12 * 1024 * 1024;
  if (length > limit) throw fail(`File too large (limit ${Math.round(limit / 1048576)} MB)`);
  const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, '');
  const key = `${kind === 'file' ? 'files' : 'media'}/${stamp}-${crypto.randomUUID().slice(0, 8)}-${name}`;
  await env.FILES.put(key, request.body, { httpMetadata: { contentType: types[ext] } });
  return { key, url: kind === 'media' ? `${url.origin}/media/${key.slice('media/'.length)}` : '', kind, name, size: length };
}

async function removeUpload(key, env) {
  if (!env.FILES) throw fail('The FILES R2 bucket is not bound', 500);
  if (!/^(media|files)\/[a-z0-9._\/-]+$/i.test(String(key || ''))) throw fail('Invalid key');
  await env.FILES.delete(key);
  return { ok: true };
}

async function listFiles(kind, env, url) {
  if (!env.FILES) throw fail('The FILES R2 bucket is not bound', 500);
  const prefix = kind === 'file' ? 'files/' : 'media/';
  const list = await env.FILES.list({ prefix, limit: 500 });
  return {
    objects: list.objects.map((o) => ({ key: o.key, size: o.size, uploaded: o.uploaded, url: prefix === 'media/' ? `${url.origin}/media/${o.key.slice(6)}` : '' }))
  };
}

async function serveMedia(key, env, cors) {
  if (!env.FILES) return new Response('Not configured', { status: 500 });
  if (!/^[a-z0-9._\/-]+$/i.test(key) || key.includes('..')) return new Response('Not found', { status: 404 });
  const object = await env.FILES.get(`media/${key}`);
  if (!object) return new Response('Not found', { status: 404 });
  return new Response(object.body, {
    headers: {
      'Content-Type': (object.httpMetadata && object.httpMetadata.contentType) || 'application/octet-stream',
      'Cache-Control': 'public, max-age=31536000, immutable',
      'Access-Control-Allow-Origin': '*'
    }
  });
}

/* ---------- Stripe: create product, price, and payment link from a catalog entry ---------- */
async function createStripeProduct(body, env, url) {
  const catalog = await loadCatalog(env);
  const guide = catalog.guides.find((g) => g.id === body.guideId);
  if (!guide) throw fail('Title not found in the catalog');
  const variantIndex = Number.isInteger(body.variantIndex) ? body.variantIndex : -1;
  const target = variantIndex >= 0 ? (guide.variants || [])[variantIndex] : guide;
  if (!target) throw fail('Edition not found');
  const amount = Number(target.price);
  if (!Number.isFinite(amount) || amount < 0) throw fail('The title needs a numeric price first');
  const currency = String(catalog.currency || 'CAD').toLowerCase();
  const name = variantIndex >= 0 ? `${guide.title} · ${target.label}` : guide.title;

  const productParams = new URLSearchParams();
  productParams.set('name', name);
  if (guide.description) productParams.set('description', String(guide.description).slice(0, 500));
  if (guide.cover && /^https:\/\//.test(guide.cover)) productParams.append('images[]', guide.cover);
  if (target.file || guide.file) productParams.set('metadata[file]', target.file || guide.file);
  productParams.set('metadata[guideId]', guide.id);
  let product;
  if (target.productId) {
    product = await stripe(env, 'POST', `/products/${target.productId}`, productParams);
  } else {
    product = await stripe(env, 'POST', '/products', productParams);
  }

  let price;
  if (target.priceId) {
    const existing = await stripe(env, 'GET', `/prices/${target.priceId}`);
    if (existing.unit_amount === Math.round(amount * 100) && existing.currency === currency && existing.active) price = existing;
    else {
      await stripe(env, 'POST', `/prices/${target.priceId}`, new URLSearchParams({ active: 'false' }));
    }
  }
  if (!price) {
    const priceParams = new URLSearchParams();
    priceParams.set('product', product.id);
    priceParams.set('unit_amount', String(Math.round(amount * 100)));
    priceParams.set('currency', currency);
    price = await stripe(env, 'POST', '/prices', priceParams);
  }

  let link = null;
  if (!target.paymentLink || price.id !== target.priceId) {
    const linkParams = new URLSearchParams();
    linkParams.set('line_items[0][price]', price.id);
    linkParams.set('line_items[0][quantity]', '1');
    linkParams.set('allow_promotion_codes', 'true');
    const success = env.SUCCESS_URL || (env.SITE_URL ? `${env.SITE_URL}/thank-you.html?session_id={CHECKOUT_SESSION_ID}` : '');
    if (success) {
      linkParams.set('after_completion[type]', 'redirect');
      linkParams.set('after_completion[redirect][url]', success);
    }
    link = await stripe(env, 'POST', '/payment_links', linkParams);
  }

  target.productId = product.id;
  target.priceId = price.id;
  if (link) target.paymentLink = link.url;
  await saveCatalog(catalog, env);
  return { guide, productId: product.id, priceId: price.id, paymentLink: target.paymentLink };
}

/* ---------- Orders and stats ---------- */
async function orders(days, env) {
  const since = Math.floor(Date.now() / 1000) - Math.min(365, Math.max(1, days)) * 86400;
  const result = [];
  let startingAfter = '';
  for (let page = 0; page < 10; page += 1) {
    const params = new URLSearchParams();
    params.set('limit', '100');
    params.set('created[gte]', String(since));
    params.append('expand[]', 'data.line_items');
    if (startingAfter) params.set('starting_after', startingAfter);
    const data = await stripe(env, 'GET', '/checkout/sessions', params);
    for (const session of data.data || []) {
      if (session.payment_status !== 'paid') continue;
      result.push({
        id: session.id,
        created: new Date(session.created * 1000).toISOString(),
        email: session.customer_details && session.customer_details.email,
        name: session.customer_details && session.customer_details.name,
        country: session.customer_details && session.customer_details.address && session.customer_details.address.country,
        total: (session.amount_total || 0) / 100,
        currency: String(session.currency || '').toUpperCase(),
        gift: session.metadata && session.metadata.gift_email ? session.metadata.gift_email : '',
        items: ((session.line_items && session.line_items.data) || []).map((line) => ({ name: line.description, priceId: line.price && line.price.id, amount: (line.amount_total || 0) / 100 }))
      });
    }
    if (!data.has_more) break;
    startingAfter = data.data[data.data.length - 1].id;
  }
  const byTitle = {};
  const byCurrency = {};
  for (const order of result) {
    byCurrency[order.currency] = (byCurrency[order.currency] || 0) + order.total;
    for (const item of order.items) {
      byTitle[item.name] = byTitle[item.name] || { name: item.name, count: 0, revenue: 0 };
      byTitle[item.name].count += 1;
      byTitle[item.name].revenue += item.amount;
    }
  }
  return {
    orders: result.sort((a, b) => (a.created < b.created ? 1 : -1)),
    summary: { count: result.length, days, byCurrency, byTitle: Object.values(byTitle).sort((a, b) => b.revenue - a.revenue) }
  };
}

/* ---------- Public routes ---------- */
async function createSession(body, env) {
  if (!env.SUCCESS_URL || !env.CANCEL_URL) throw fail('Checkout is not configured', 500);
  const allowedPrices = String(env.ALLOWED_PRICES || '').split(',').map((s) => s.trim()).filter(Boolean);
  const items = [...new Set(Array.isArray(body.items) ? body.items : [])]
    .filter((id) => typeof id === 'string' && /^price_[A-Za-z0-9]+$/.test(id))
    .filter((id) => !allowedPrices.length || allowedPrices.includes(id))
    .slice(0, 20);
  if (!items.length) throw fail('No purchasable items in the request');

  const params = new URLSearchParams();
  params.set('mode', 'payment');
  params.set('success_url', env.SUCCESS_URL);
  params.set('cancel_url', env.CANCEL_URL);
  params.set('allow_promotion_codes', 'true');
  params.set('billing_address_collection', 'auto');
  items.forEach((price, index) => {
    params.set(`line_items[${index}][price]`, price);
    params.set(`line_items[${index}][quantity]`, '1');
  });
  const gift = body.gift && typeof body.gift === 'object' ? body.gift : null;
  if (gift && isEmail(gift.email)) {
    params.set('metadata[gift_email]', gift.email);
    params.set('metadata[gift_message]', String(gift.message || '').slice(0, 400));
  }
  const session = await stripe(env, 'POST', '/checkout/sessions', params);
  if (!session.url) throw fail('Stripe could not create the session', 502);
  return { url: session.url };
}

async function fetchPaidSession(env, sessionId) {
  if (!/^cs_[A-Za-z0-9_]+$/.test(String(sessionId || ''))) throw fail('Invalid session');
  const params = new URLSearchParams();
  params.append('expand[]', 'line_items.data.price.product');
  const session = await stripe(env, 'GET', `/checkout/sessions/${sessionId}`, params);
  if (session.payment_status !== 'paid') throw fail('This order has not been paid', 402);
  return session;
}

async function fileForLine(line, env) {
  const product = line.price && line.price.product;
  if (product && product.metadata && product.metadata.file) return { key: product.metadata.file, name: product.name };
  // Fall back to the catalog when the Stripe product has no file metadata.
  if (env.STORE && line.price) {
    const raw = await env.STORE.get('catalog');
    if (raw) {
      const catalog = JSON.parse(raw);
      for (const guide of catalog.guides || []) {
        if (guide.priceId === line.price.id && guide.file) return { key: guide.file, name: guide.title };
        for (const variant of guide.variants || []) if (variant.priceId === line.price.id && (variant.file || guide.file)) return { key: variant.file || guide.file, name: `${guide.title} · ${variant.label}` };
      }
    }
  }
  return null;
}

async function downloadLinks(env, session, base) {
  const hours = Number(env.DOWNLOAD_HOURS) || 72;
  const exp = Date.now() + hours * 3600 * 1000;
  const items = [];
  for (const line of session.line_items.data) {
    const file = await fileForLine(line, env);
    if (!file) continue;
    const token = await signToken(env, { key: file.key, name: file.name, exp, s: session.id });
    items.push({ name: file.name, url: `${base}/file?t=${token}`, expires: new Date(exp).toISOString() });
  }
  return items;
}

async function orderDetails(sessionId, env, url) {
  const session = await fetchPaidSession(env, sessionId);
  const items = await downloadLinks(env, session, url.origin);
  const email = session.customer_details && session.customer_details.email;
  const giftEmail = session.metadata && session.metadata.gift_email;
  let giftSent = false;
  if (giftEmail && env.RESEND_API_KEY && env.STORE) {
    const flag = `gift:${session.id}`;
    if (!(await env.STORE.get(flag))) {
      await sendEmail(env, giftEmail, 'You have been sent a gift from Marufi Digital', giftHtml(items, session.metadata.gift_message, email));
      await env.STORE.put(flag, '1');
    }
    giftSent = true;
  }
  return { email, items, gift: giftEmail ? { email: giftEmail, sent: giftSent } : null };
}

async function serveFile(token, env) {
  const payload = await verifyToken(env, token);
  if (!payload || !payload.key) return new Response('This download link has expired. Use "Resend my download" on the store to get a new one.', { status: 403 });
  if (!env.FILES) return new Response('File storage is not configured', { status: 500 });
  const object = await env.FILES.get(payload.key);
  if (!object) return new Response('File not found', { status: 404 });
  const filename = payload.key.split('/').pop();
  return new Response(object.body, {
    headers: {
      'Content-Type': (object.httpMetadata && object.httpMetadata.contentType) || 'application/octet-stream',
      'Content-Disposition': `attachment; filename="${filename.replace(/"/g, '')}"`,
      'Cache-Control': 'private, no-store'
    }
  });
}

async function notifyRequest(body, env) {
  if (!isEmail(body.email)) throw fail('Please enter a valid e-mail address');
  const place = String(body.place || '').trim().slice(0, 120);
  if (!place) throw fail('Please tell us the place');
  if (env.STORE) {
    await env.STORE.put(`notify:${Date.now()}:${crypto.randomUUID().slice(0, 8)}`, JSON.stringify({ email: body.email, place, at: new Date().toISOString() }));
  } else if (env.CONTACT_EMAIL && env.RESEND_API_KEY) {
    await sendEmail(env, env.CONTACT_EMAIL, `Guide request: ${place}`, `<p>${escapeHtml(body.email)} wants a guide for <strong>${escapeHtml(place)}</strong>.</p>`);
  } else {
    throw fail('Requests are not configured yet', 500);
  }
  return { ok: true };
}

async function listRequests(env) {
  if (!env.STORE) throw fail('The STORE KV namespace is not bound', 500);
  const list = await env.STORE.list({ prefix: 'notify:', limit: 1000 });
  const requests = [];
  for (const k of list.keys) {
    const raw = await env.STORE.get(k.name);
    if (raw) requests.push({ key: k.name, ...JSON.parse(raw) });
  }
  return { requests: requests.sort((a, b) => (a.at < b.at ? 1 : -1)) };
}

async function deleteRequest(key, env) {
  if (!/^notify:\d+:[a-z0-9-]+$/i.test(key)) throw fail('Invalid key');
  await env.STORE.delete(key);
  return { ok: true };
}

async function resendLinks(body, env, url) {
  if (!isEmail(body.email)) throw fail('Please enter a valid e-mail address');
  const params = new URLSearchParams();
  params.set('customer_details[email]', body.email);
  params.set('status', 'complete');
  params.set('limit', '20');
  params.append('expand[]', 'data.line_items.data.price.product');
  const result = await stripe(env, 'GET', '/checkout/sessions', params);
  const paid = (result.data || []).filter((s) => s.payment_status === 'paid');
  // Always answer the same way so the endpoint cannot be used to test whether an e-mail has orders.
  if (paid.length) {
    const items = [];
    for (const session of paid) items.push(...await downloadLinks(env, session, url.origin));
    if (items.length) await sendEmail(env, body.email, 'Your Marufi Digital downloads', linksHtml(items));
  }
  return { ok: true };
}

async function announce(body, env) {
  if (!/^price_[A-Za-z0-9]+$/.test(String(body.priceId || ''))) throw fail('priceId is required');
  const subject = String(body.subject || '').trim().slice(0, 150);
  const message = String(body.message || '').trim().slice(0, 4000);
  if (!subject || !message) throw fail('subject and message are required');
  const recipients = new Set();
  let startingAfter = '';
  for (let page = 0; page < 20; page += 1) {
    const params = new URLSearchParams();
    params.set('status', 'complete');
    params.set('limit', '100');
    params.append('expand[]', 'data.line_items');
    if (startingAfter) params.set('starting_after', startingAfter);
    const result = await stripe(env, 'GET', '/checkout/sessions', params);
    for (const session of result.data || []) {
      if (session.payment_status !== 'paid') continue;
      const bought = ((session.line_items && session.line_items.data) || []).some((line) => line.price && line.price.id === body.priceId);
      const email = session.customer_details && session.customer_details.email;
      if (bought && isEmail(email)) recipients.add(email);
    }
    if (!result.has_more) break;
    startingAfter = result.data[result.data.length - 1].id;
  }
  if (body.dryRun) return { sent: 0, recipients: recipients.size };
  let sent = 0;
  for (const email of recipients) {
    await sendEmail(env, email, subject, `<div style="font-family:sans-serif;line-height:1.6">${escapeHtml(message).replace(/\n/g, '<br>')}</div>`);
    sent += 1;
  }
  return { sent, recipients: recipients.size };
}

/* ---------- E-mail templates ---------- */
function linksHtml(items) {
  const rows = items.map((item) => `<li style="margin:.5em 0"><a href="${item.url}">${escapeHtml(item.name)}</a> <span style="color:#666">(link valid until ${new Date(item.expires).toUTCString()})</span></li>`).join('');
  return `<div style="font-family:sans-serif;line-height:1.6"><p>Here are your downloads from Marufi Digital:</p><ul>${rows}</ul><p>Save the files somewhere you will find them again. If a link has expired, use "Resend my download" on the store to request a fresh one.</p></div>`;
}
function giftHtml(items, message, from) {
  const note = message ? `<blockquote style="border-left:3px solid #9a3a20;margin:1em 0;padding:.25em 1em;color:#333">${escapeHtml(message)}</blockquote>` : '';
  return `<div style="font-family:sans-serif;line-height:1.6"><p>Someone${from ? ` (${escapeHtml(from)})` : ''} sent you a gift from Marufi Digital.</p>${note}${linksHtml(items).replace('Here are your downloads from Marufi Digital:', 'Your downloads:')}</div>`;
}
