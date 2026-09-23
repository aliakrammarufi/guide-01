/**
 * Marufi Digital — store backend (Cloudflare Worker, no dependencies).
 *
 * Routes (all JSON):
 *   POST /session   { items: ["price_..."], gift?: { email, message } }   → { url }   one Stripe Checkout for the cart
 *   GET  /order?session_id=cs_...                                          → { email, items: [{ name, url, expires }] }
 *   GET  /file?t=<signed token>                                            → streams the purchased file from R2
 *   POST /notify    { email, place }                                       → { ok }    "tell me when this place is covered"
 *   POST /resend    { email }                                              → { ok }    re-sends download links to a buyer
 *   POST /announce  { priceId, subject, message }  (Bearer ADMIN_TOKEN)    → { sent }  emails every buyer of a title
 *
 * Environment (wrangler.toml [vars] + `wrangler secret put`):
 *   STRIPE_SECRET_KEY   secret. sk_live_... or sk_test_...
 *   SUCCESS_URL         https://your-domain.com/thank-you.html?session_id={CHECKOUT_SESSION_ID}
 *   CANCEL_URL          https://your-domain.com/#guides
 *   ALLOWED_ORIGINS     https://your-domain.com,https://www.your-domain.com
 *   ALLOWED_PRICES      optional comma-separated price_... IDs that may be sold
 *   DOWNLOAD_SECRET     secret. any long random string; signs download links
 *   DOWNLOAD_HOURS      optional, default 72; how long a download link stays valid
 *   RESEND_API_KEY      secret, optional; enables e-mails through https://resend.com
 *   FROM_EMAIL          e.g. "Marufi Digital <orders@your-domain.com>" (a domain verified in Resend)
 *   CONTACT_EMAIL       where "notify me" requests go when no KV store is bound
 *   ADMIN_TOKEN         secret; required for /announce
 * Bindings:
 *   FILES   R2 bucket holding the purchased files. Each Stripe product needs metadata `file` = object key in the bucket.
 *   NOTIFY  optional KV namespace that stores "notify me" requests.
 */
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const origin = request.headers.get('Origin') || '';
    const allowedOrigins = String(env.ALLOWED_ORIGINS || '').split(',').map((s) => s.trim()).filter(Boolean);
    const cors = {
      'Access-Control-Allow-Origin': allowedOrigins.includes(origin) ? origin : (allowedOrigins[0] || ''),
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type, Authorization',
      'Vary': 'Origin'
    };
    const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', ...cors } });

    if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors });
    const path = url.pathname.replace(/\/+$/, '') || '/session';
    const browserRoute = path === '/file';
    if (!browserRoute && allowedOrigins.length && origin && !allowedOrigins.includes(origin)) return json({ error: 'Origin not allowed' }, 403);

    try {
      if (path === '/session' && request.method === 'POST') return json(await createSession(await readJson(request), env));
      if (path === '/order' && request.method === 'GET') return json(await orderDetails(url.searchParams.get('session_id'), env, url));
      if (path === '/file' && request.method === 'GET') return serveFile(url.searchParams.get('t'), env);
      if (path === '/notify' && request.method === 'POST') return json(await notifyRequest(await readJson(request), env));
      if (path === '/resend' && request.method === 'POST') return json(await resendLinks(await readJson(request), env, url));
      if (path === '/announce' && request.method === 'POST') return json(await announce(request, await readJson(request), env));
      return json({ error: 'Not found' }, 404);
    } catch (error) {
      const status = error.status || 500;
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

async function stripe(env, method, endpoint, params) {
  if (!env.STRIPE_SECRET_KEY) throw fail('Checkout is not configured', 500);
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
  if (!env.RESEND_API_KEY || !env.FROM_EMAIL) throw fail('E-mail is not configured', 500);
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

/* ---------- Signed download links ---------- */
const enc = new TextEncoder();
function b64url(bytes) {
  return btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
function fromB64url(text) {
  const padded = text.replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((text.length + 3) % 4);
  return Uint8Array.from(atob(padded), (c) => c.charCodeAt(0));
}
async function hmacKey(env) {
  if (!env.DOWNLOAD_SECRET) throw fail('Downloads are not configured', 500);
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
  const ok = await crypto.subtle.verify('HMAC', await hmacKey(env), fromB64url(sig), enc.encode(body));
  if (!ok) return null;
  const payload = JSON.parse(new TextDecoder().decode(fromB64url(body)));
  if (!payload.exp || Date.now() > payload.exp) return null;
  return payload;
}

async function downloadLinks(env, session, base) {
  const hours = Number(env.DOWNLOAD_HOURS) || 72;
  const exp = Date.now() + hours * 3600 * 1000;
  const items = [];
  for (const line of session.line_items.data) {
    const product = line.price && line.price.product;
    const file = product && product.metadata && product.metadata.file;
    if (!file) continue;
    const token = await signToken(env, { key: file, name: product.name, exp, s: session.id });
    items.push({ name: product.name, url: `${base}/file?t=${token}`, expires: new Date(exp).toISOString() });
  }
  return items;
}

/* ---------- Routes ---------- */
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

async function orderDetails(sessionId, env, url) {
  const session = await fetchPaidSession(env, sessionId);
  const items = await downloadLinks(env, session, url.origin);
  const email = session.customer_details && session.customer_details.email;
  const giftEmail = session.metadata && session.metadata.gift_email;
  let giftSent = false;
  if (giftEmail && env.RESEND_API_KEY && env.NOTIFY) {
    const flag = `gift:${session.id}`;
    if (!(await env.NOTIFY.get(flag))) {
      await sendEmail(env, giftEmail, 'You have been sent a gift from Marufi Digital', giftHtml(items, session.metadata.gift_message, email));
      await env.NOTIFY.put(flag, '1');
    }
    giftSent = true;
  }
  return { email, items, gift: giftEmail ? { email: giftEmail, sent: giftSent } : null };
}

async function serveFile(token, env) {
  const payload = await verifyToken(env, token);
  if (!payload) return new Response('This download link has expired. Use "Lost your download?" on the store to get a new one.', { status: 403 });
  if (!env.FILES) return new Response('File storage is not configured', { status: 500 });
  const object = await env.FILES.get(payload.key);
  if (!object) return new Response('File not found', { status: 404 });
  const filename = payload.key.split('/').pop();
  return new Response(object.body, {
    headers: {
      'Content-Type': object.httpMetadata && object.httpMetadata.contentType ? object.httpMetadata.contentType : 'application/octet-stream',
      'Content-Disposition': `attachment; filename="${filename.replace(/"/g, '')}"`,
      'Cache-Control': 'private, no-store'
    }
  });
}

async function notifyRequest(body, env) {
  if (!isEmail(body.email)) throw fail('Please enter a valid e-mail address');
  const place = String(body.place || '').trim().slice(0, 120);
  if (!place) throw fail('Please tell us the place');
  if (env.NOTIFY) {
    await env.NOTIFY.put(`notify:${Date.now()}:${crypto.randomUUID()}`, JSON.stringify({ email: body.email, place, at: new Date().toISOString() }));
  } else if (env.CONTACT_EMAIL && env.RESEND_API_KEY) {
    await sendEmail(env, env.CONTACT_EMAIL, `Guide request: ${place}`, `<p>${escapeHtml(body.email)} wants a guide for <strong>${escapeHtml(place)}</strong>.</p>`);
  } else {
    throw fail('Requests are not configured yet', 500);
  }
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

async function announce(request, body, env) {
  const auth = request.headers.get('Authorization') || '';
  if (!env.ADMIN_TOKEN || auth !== `Bearer ${env.ADMIN_TOKEN}`) throw fail('Unauthorised', 401);
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
      const bought = (session.line_items && session.line_items.data || []).some((line) => line.price && line.price.id === body.priceId);
      const email = session.customer_details && session.customer_details.email;
      if (bought && isEmail(email)) recipients.add(email);
    }
    if (!result.has_more) break;
    startingAfter = result.data[result.data.length - 1].id;
  }
  let sent = 0;
  for (const email of recipients) {
    await sendEmail(env, email, subject, `<div style="font-family:sans-serif;line-height:1.6">${escapeHtml(message).replace(/\n/g, '<br>')}</div>`);
    sent += 1;
  }
  return { sent };
}

/* ---------- E-mail templates ---------- */
function linksHtml(items) {
  const rows = items.map((item) => `<li style="margin:.5em 0"><a href="${item.url}">${escapeHtml(item.name)}</a> <span style="color:#666">(link valid until ${new Date(item.expires).toUTCString()})</span></li>`).join('');
  return `<div style="font-family:sans-serif;line-height:1.6"><p>Here are your downloads from Marufi Digital:</p><ul>${rows}</ul><p>Save the files somewhere you will find them again. If a link has expired, use "Lost your download?" on the store to request a fresh one.</p></div>`;
}
function giftHtml(items, message, from) {
  const note = message ? `<blockquote style="border-left:3px solid #9a3a20;margin:1em 0;padding:.25em 1em;color:#333">${escapeHtml(message)}</blockquote>` : '';
  return `<div style="font-family:sans-serif;line-height:1.6"><p>Someone${from ? ` (${escapeHtml(from)})` : ''} sent you a gift from Marufi Digital.</p>${note}${linksHtml(items).replace('Here are your downloads from Marufi Digital:', 'Your downloads:')}</div>`;
}
