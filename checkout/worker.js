/**
 * Marufi Digital — store backend (Cloudflare Worker, no dependencies).
 *
 * Public routes (JSON unless noted):
 *   POST /session   { items: ["price_..."], gift?: { email, message } }   → { url }   one Stripe Checkout for the cart
 *   GET  /order?session_id=cs_...                                          → { email, items: [{ name, url, expires }] }
 *   GET  /file?t=<signed token>                                            → streams a purchased file from R2
 *   GET  /media/<key>                                                      → serves public media (covers, samples, zones, free PDFs)
 *   GET  /catalog                                                          → the live catalog saved from the admin dashboard
 *   POST /notify    { email, place }                                       → { ok }
 *   POST /resend    { email }                                              → { ok }
 *   POST /contact   { name, email, message }                               → { ok }   stored, and e-mailed to CONTACT_EMAIL
 *   POST /subscribe { email }                                              → { ok, doubleOptIn }   newsletter; confirmation e-mail when e-mail is configured
 *   GET  /confirm?t=<token>, GET /unsubscribe?t=<token>                    → HTML pages
 *   POST /account/code   { email, name? }                                  → { ok, challenge }   e-mails a six-digit code (creates the account on verify)
 *   POST /account/verify { challenge, code }                               → { token, exp, account }   30-day customer session
 *   Customer routes (Bearer session token): GET/PUT/DELETE /account/me, GET /account/orders (with download links),
 *     GET /account/gifts, GET /account/requests, PUT /account/newsletter { subscribed }
 *   POST /gift-card/session { amount, to, from, message }                  → { url }   Stripe Checkout for a gift card; the code is created on payment
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
 *   POST /admin/stripe/coupon    { percent, minItems, name }  → { couponId }   multi-title cart discount
 *   POST /admin/login/verify     { challenge, code }          → { token, exp }  second step when two-step sign-in is on
 *   POST /admin/recover                                       → e-mails a reset code to CONTACT_EMAIL
 *   POST /admin/reset            { challenge, code, password } → { ok }
 *   POST /admin/password         { current, next }            → { ok, token }
 *   GET/PUT /admin/security      { twoStep }
 *   GET/POST/DELETE /admin/coupons[/id]                        Stripe coupons and promotion codes
 *   POST /admin/refund           { sessionId, amount? }        → { refund }
 *   GET  /admin/activity, GET/PUT /admin/templates, GET /admin/health
 *   POST /admin/requests/notify  { guideId, subject?, message?, dryRun? } → e-mails readers who asked for that place
 *   POST /admin/deliver          { priceId, subject?, message?, dryRun? } → { sent }  email download links to buyers (pre-orders)
 *   GET  /admin/messages, DELETE /admin/messages/<key>                     contact-form messages
 *   GET  /admin/subscribers, DELETE /admin/subscribers/<key>               newsletter list
 *   POST /admin/newsletter       { subject, message, dryRun? }             → { sent, recipients }
 *
 * Environment (wrangler.toml [vars] + `wrangler secret put`):
 *   STRIPE_SECRET_KEY   secret
 *   ADMIN_PASSWORD      secret; the dashboard password
 *   DOWNLOAD_SECRET     secret; signs download links and admin sessions
 *   SUCCESS_URL, CANCEL_URL, ALLOWED_ORIGINS, DOWNLOAD_HOURS, FROM_EMAIL, CONTACT_EMAIL, SITE_URL
 *   RESEND_API_KEY      secret, optional; e-mail through resend.com
 *   ADMIN_TOKEN         secret, optional; alternative bearer token for scripts
 * Bindings:
 *   FILES   R2 bucket, optional: media/... (public) and files/... (purchased downloads, private).
 *           Without it, files are kept in the STORE KV namespace (25 MB per file, 1 GB total on the free plan, no card needed).
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
      if (path === '/contact' && request.method === 'POST') return json(await contact(request, await readJson(request), env));
      if (path === '/subscribe' && request.method === 'POST') return json(await subscribe(request, await readJson(request), env, url));
      if (path === '/confirm' && request.method === 'GET') return confirmSubscription(url.searchParams.get('t'), env);
      if (path === '/unsubscribe' && request.method === 'GET') return unsubscribe(url.searchParams.get('t'), env);
      if (path === '/account/code' && request.method === 'POST') return json(await accountCode(request, await readJson(request), env));
      if (path === '/account/verify' && request.method === 'POST') return json(await accountVerify(request, await readJson(request), env));
      if (path.startsWith('/account/')) {
        const who = await requireCustomer(request, env);
        const sub = path.slice('/account'.length);
        if (sub === '/me' && request.method === 'GET') return json(await accountMe(who, env));
        if (sub === '/me' && request.method === 'PUT') return json(await accountUpdate(who, await readJson(request), env));
        if (sub === '/me' && request.method === 'DELETE') return json(await accountDelete(who, env));
        if (sub === '/orders' && request.method === 'GET') return json(await accountOrders(who, env, url));
        if (sub === '/gifts' && request.method === 'GET') return json(await accountGifts(who, env));
        if (sub === '/requests' && request.method === 'GET') return json(await accountRequests(who, env));
        if (sub === '/newsletter' && request.method === 'PUT') return json(await accountNewsletter(who, await readJson(request), env));
      }
      if (path === '/gift-card/session' && request.method === 'POST') return json(await giftCardSession(request, await readJson(request), env));

      // Admin
      if (path === '/admin/login' && request.method === 'POST') return json(await login(request, await readJson(request), env));
      if (path === '/admin/login/verify' && request.method === 'POST') return json(await verifyLogin(request, await readJson(request), env));
      if (path === '/admin/recover' && request.method === 'POST') return json(await recover(request, env));
      if (path === '/admin/reset' && request.method === 'POST') return json(await resetPassword(request, await readJson(request), env));
      if (path.startsWith('/admin/')) {
        await requireAdmin(request, env);
        const sub = path.slice('/admin'.length);
        if (sub === '/status' && request.method === 'GET') return json(status(env));
        if (sub === '/security' && request.method === 'GET') return json(await securityInfo(env));
        if (sub === '/security' && request.method === 'PUT') return json(await updateSecurity(request, await readJson(request), env));
        if (sub === '/password' && request.method === 'POST') return json(await changePassword(request, await readJson(request), env));
        if (sub === '/coupons' && request.method === 'GET') return json(await listCoupons(env));
        if (sub === '/coupons' && request.method === 'POST') return json(await createCoupon(request, await readJson(request), env));
        if (sub.startsWith('/coupons/') && request.method === 'DELETE') return json(await deleteCoupon(request, decodeURIComponent(sub.slice('/coupons/'.length)), env));
        if (sub === '/refund' && request.method === 'POST') return json(await refund(request, await readJson(request), env));
        if (sub === '/activity' && request.method === 'GET') return json(await activity(env));
        if (sub === '/templates' && request.method === 'GET') return json(await getTemplates(env));
        if (sub === '/templates' && request.method === 'PUT') return json(await putTemplates(request, await readJson(request), env));
        if (sub === '/requests/notify' && request.method === 'POST') return json(await notifyRequesters(request, await readJson(request), env));
        if (sub === '/health' && request.method === 'GET') return json(await health(env));
        if (sub === '/catalog' && request.method === 'GET') return json(await loadCatalog(env));
        if (sub === '/catalog' && request.method === 'PUT') return json(await saveCatalog(await readJson(request), env, request));
        if (sub === '/backups' && request.method === 'GET') return json(await listBackups(env));
        if (sub.startsWith('/backups/') && request.method === 'GET') return json(await getBackup(decodeURIComponent(sub.slice('/backups/'.length)), env));
        if (sub === '/upload' && request.method === 'PUT') return json(await upload(request, url, env));
        if (sub === '/upload' && request.method === 'DELETE') return json(await removeUpload(url.searchParams.get('key'), env));
        if (sub === '/files' && request.method === 'GET') return json(await listFiles(url.searchParams.get('kind'), env, url));
        if (sub === '/stripe/product' && request.method === 'POST') return json(await createStripeProduct(await readJson(request), env, url));
        if (sub === '/stripe/coupon' && request.method === 'POST') return json(await createStripeCoupon(await readJson(request), env));
        if (sub === '/deliver' && request.method === 'POST') return json(await deliver(await readJson(request), env, url));
        if (sub === '/orders' && request.method === 'GET') return json(await orders(Number(url.searchParams.get('days')) || 30, env));
        if (sub === '/requests' && request.method === 'GET') return json(await listRequests(env));
        if (sub.startsWith('/requests/') && request.method === 'DELETE') return json(await deleteRequest(decodeURIComponent(sub.slice('/requests/'.length)), env));
        if (sub === '/announce' && request.method === 'POST') return json(await announce(await readJson(request), env));
        if (sub === '/messages' && request.method === 'GET') return json(await listKeyed('message:', env));
        if (sub.startsWith('/messages/') && request.method === 'DELETE') return json(await deleteKeyed('message:', decodeURIComponent(sub.slice('/messages/'.length)), env));
        if (sub === '/subscribers' && request.method === 'GET') return json(await listSubscribers(env));
        if (sub.startsWith('/subscribers/') && request.method === 'DELETE') return json(await deleteKeyed('subscriber:', decodeURIComponent(sub.slice('/subscribers/'.length)), env));
        if (sub === '/newsletter' && request.method === 'POST') return json(await newsletter(request, await readJson(request), env, url));
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

/* ---------- Activity log ---------- */
async function logEvent(env, request, action, detail) {
  if (!env.STORE) return;
  try {
    await env.STORE.put(`log:${Date.now()}:${crypto.randomUUID().slice(0, 6)}`, JSON.stringify({ action, detail: String(detail || '').slice(0, 300), at: new Date().toISOString(), ip: request ? clientIp(request) : '' }), { expirationTtl: 60 * 60 * 24 * 180 });
  } catch { /* logging must never break the request */ }
}

async function activity(env) {
  if (!env.STORE) throw fail('The STORE KV namespace is not bound', 500);
  const list = await env.STORE.list({ prefix: 'log:', limit: 200 });
  const events = [];
  for (const k of list.keys.slice(-120)) { const raw = await env.STORE.get(k.name); if (raw) events.push(JSON.parse(raw)); }
  return { events: events.sort((a, b) => (a.at < b.at ? 1 : -1)) };
}

/* ---------- Passwords, sessions, two-step ---------- */
async function pbkdf2(password, saltB64, iterations = 120000) {
  const salt = saltB64 ? fromB64url(saltB64) : crypto.getRandomValues(new Uint8Array(16));
  const key = await crypto.subtle.importKey('raw', enc.encode(password), 'PBKDF2', false, ['deriveBits']);
  const bits = await crypto.subtle.deriveBits({ name: 'PBKDF2', hash: 'SHA-256', salt, iterations }, key, 256);
  return { salt: b64url(salt), hash: b64url(bits), iterations };
}

async function passwordMatches(password, env) {
  const stored = env.STORE ? await env.STORE.get('auth:password') : null;
  if (stored) {
    const record = JSON.parse(stored);
    const check = await pbkdf2(password, record.salt, record.iterations);
    return equalBytes(fromB64url(check.hash), fromB64url(record.hash));
  }
  if (!env.ADMIN_PASSWORD) throw fail('ADMIN_PASSWORD is not configured on the server', 500);
  return equalBytes(await sha256(String(password || '')), await sha256(env.ADMIN_PASSWORD));
}

async function passwordVersion(env) {
  return env.STORE ? Number(await env.STORE.get('auth:version')) || 1 : 1;
}

async function issueSession(env) {
  const exp = Date.now() + 12 * 3600 * 1000;
  return { token: await signToken(env, { role: 'admin', exp, v: await passwordVersion(env) }), exp };
}

function maskEmail(email) {
  if (!isEmail(email)) return '';
  const [user, domain] = email.split('@');
  return `${user.slice(0, 2)}${'•'.repeat(Math.max(2, user.length - 2))}@${domain}`;
}

function sixDigits() {
  return String(crypto.getRandomValues(new Uint32Array(1))[0] % 1000000).padStart(6, '0');
}

async function storeCode(env, kind, code, ttl = 600) {
  const id = crypto.randomUUID();
  await env.STORE.put(`${kind}:${id}`, JSON.stringify({ hash: b64url(await sha256(code)), attempts: 0 }), { expirationTtl: ttl });
  return id;
}

async function checkCode(env, kind, id, code) {
  if (!/^[0-9a-f-]{36}$/.test(String(id || ''))) throw fail('Invalid or expired code', 401);
  const raw = await env.STORE.get(`${kind}:${id}`);
  if (!raw) throw fail('Invalid or expired code', 401);
  const record = JSON.parse(raw);
  if (record.attempts >= 5) { await env.STORE.delete(`${kind}:${id}`); throw fail('Too many attempts. Start again.', 429); }
  const ok = equalBytes(fromB64url(record.hash), await sha256(String(code || '').trim()));
  if (!ok) { record.attempts += 1; await env.STORE.put(`${kind}:${id}`, JSON.stringify(record), { expirationTtl: 600 }); throw fail('Wrong code', 401); }
  await env.STORE.delete(`${kind}:${id}`);
}

async function securityInfo(env) {
  return {
    twoStep: env.STORE ? (await env.STORE.get('auth:twostep')) === '1' : false,
    customPassword: env.STORE ? Boolean(await env.STORE.get('auth:password')) : false,
    ownerEmail: maskEmail(env.CONTACT_EMAIL || ''),
    emailConfigured: Boolean(env.RESEND_API_KEY && env.FROM_EMAIL && isEmail(env.CONTACT_EMAIL))
  };
}

async function updateSecurity(request, body, env) {
  if (!env.STORE) throw fail('The STORE KV namespace is not bound', 500);
  if (body.twoStep && !(env.RESEND_API_KEY && env.FROM_EMAIL && isEmail(env.CONTACT_EMAIL))) throw fail('Two-step sign-in needs e-mail configured (RESEND_API_KEY, FROM_EMAIL, CONTACT_EMAIL).');
  await env.STORE.put('auth:twostep', body.twoStep ? '1' : '0');
  await logEvent(env, request, 'security', `two-step ${body.twoStep ? 'enabled' : 'disabled'}`);
  return { ok: true, twoStep: Boolean(body.twoStep) };
}

async function changePassword(request, body, env) {
  if (!env.STORE) throw fail('The STORE KV namespace is not bound', 500);
  if (!(await passwordMatches(String(body.current || ''), env))) throw fail('Current password is wrong', 401);
  const next = String(body.next || '');
  if (next.length < 12) throw fail('Use at least 12 characters');
  await env.STORE.put('auth:password', JSON.stringify(await pbkdf2(next)));
  await env.STORE.put('auth:version', String((await passwordVersion(env)) + 1));
  await logEvent(env, request, 'password', 'changed');
  return { ...(await issueSession(env)), ok: true };
}

async function recover(request, env) {
  if (!(env.STORE && env.RESEND_API_KEY && env.FROM_EMAIL && isEmail(env.CONTACT_EMAIL))) throw fail('Recovery by e-mail is not configured. Reset the password with: wrangler secret put ADMIN_PASSWORD, then clear the stored password with the KV key auth:password.', 500);
  const ip = clientIp(request);
  const key = `recover-throttle:${ip}`;
  if (Number(await env.STORE.get(key)) >= 3) throw fail('Too many recovery requests. Try again later.', 429);
  await env.STORE.put(key, String((Number(await env.STORE.get(key)) || 0) + 1), { expirationTtl: 3600 });
  const code = sixDigits();
  const id = await storeCode(env, 'reset', code, 900);
  await sendEmail(env, env.CONTACT_EMAIL, 'Reset your Marufi Digital admin password', `<p>Your reset code is <strong style="font-size:1.4em;letter-spacing:.1em">${code}</strong>. It expires in 15 minutes. If you did not ask for this, ignore it.</p>`);
  await logEvent(env, request, 'recover', 'reset code sent');
  return { ok: true, challenge: id, sentTo: maskEmail(env.CONTACT_EMAIL) };
}

async function resetPassword(request, body, env) {
  if (!env.STORE) throw fail('The STORE KV namespace is not bound', 500);
  await checkCode(env, 'reset', body.challenge, body.code);
  const next = String(body.password || '');
  if (next.length < 12) throw fail('Use at least 12 characters');
  await env.STORE.put('auth:password', JSON.stringify(await pbkdf2(next)));
  await env.STORE.put('auth:version', String((await passwordVersion(env)) + 1));
  await logEvent(env, request, 'password', 'reset by e-mail code');
  return { ok: true };
}

/* ---------- Admin auth ---------- */
async function login(request, body, env) {
  const ip = clientIp(request);
  const throttleKey = `login:${ip}`;
  if (env.STORE) {
    const attempts = Number(await env.STORE.get(throttleKey)) || 0;
    if (attempts >= 8) throw fail('Too many attempts. Try again in 15 minutes.', 429);
  }
  const ok = await passwordMatches(String(body.password || ''), env);
  if (!ok) {
    if (env.STORE) {
      const attempts = Number(await env.STORE.get(throttleKey)) || 0;
      await env.STORE.put(throttleKey, String(attempts + 1), { expirationTtl: 900 });
    }
    throw fail('Wrong password', 401);
  }
  if (env.STORE) await env.STORE.delete(throttleKey);
  const twoStep = env.STORE && (await env.STORE.get('auth:twostep')) === '1' && env.RESEND_API_KEY && env.FROM_EMAIL && isEmail(env.CONTACT_EMAIL);
  if (twoStep) {
    const code = sixDigits();
    const id = await storeCode(env, 'otp', code, 600);
    await sendEmail(env, env.CONTACT_EMAIL, 'Your Marufi Digital sign-in code', `<p>Your sign-in code is <strong style="font-size:1.4em;letter-spacing:.1em">${code}</strong>. It expires in 10 minutes.</p>`);
    return { challenge: id, sentTo: maskEmail(env.CONTACT_EMAIL) };
  }
  await logEvent(env, request, 'login', 'signed in');
  return issueSession(env);
}

async function verifyLogin(request, body, env) {
  if (!env.STORE) throw fail('The STORE KV namespace is not bound', 500);
  await checkCode(env, 'otp', body.challenge, body.code);
  await logEvent(env, request, 'login', 'signed in with two-step code');
  return issueSession(env);
}

async function requireAdmin(request, env) {
  const auth = request.headers.get('Authorization') || '';
  const token = auth.startsWith('Bearer ') ? auth.slice(7) : '';
  if (!token) throw fail('Sign in required', 401);
  if (env.ADMIN_TOKEN && token === env.ADMIN_TOKEN) return;
  const payload = await verifyToken(env, token);
  if (!payload || payload.role !== 'admin') throw fail('Session expired. Sign in again.', 401);
  if ((payload.v || 1) !== (await passwordVersion(env))) throw fail('Password changed. Sign in again.', 401);
}

function status(env) {
  return {
    stripe: Boolean(env.STRIPE_SECRET_KEY),
    stripeMode: env.STRIPE_SECRET_KEY ? (String(env.STRIPE_SECRET_KEY).startsWith('sk_live') ? 'live' : 'test') : '',
    files: Boolean(files(env)),
    fileStorage: files(env) ? files(env).kind : '',
    store: Boolean(env.STORE),
    email: Boolean(env.RESEND_API_KEY && env.FROM_EMAIL),
    downloads: Boolean(env.DOWNLOAD_SECRET),
    successUrl: env.SUCCESS_URL || '',
    siteUrl: env.SITE_URL || ''
  };
}

/* ---------- Catalog (KV) ---------- */
async function readCatalog(env) {
  if (!env.STORE) return null;
  const raw = await env.STORE.get('catalog');
  return raw ? JSON.parse(raw) : null;
}

const EMPTY_CATALOG = { storeName: 'Marufi Digital', currency: 'CAD', currencies: ['CAD'], guides: [], zones: [], resources: [], categories: [], reviews: [], regions: {}, author: {}, social: {} };

async function loadCatalog(env) {
  if (!env.STORE) throw fail('The STORE KV namespace is not bound', 500);
  const raw = await env.STORE.get('catalog');
  if (!raw) return { ...EMPTY_CATALOG, _empty: true };
  return JSON.parse(raw);
}

function isLive(guide) {
  if (!guide || guide.status === 'draft') return false;
  if (guide.status === 'scheduled' && guide.publishAt && !Number.isNaN(Date.parse(guide.publishAt)) && Date.parse(guide.publishAt) > Date.now()) return false;
  return true;
}

function publicCatalog(catalog) {
  const out = { ...catalog };
  out.guides = (catalog.guides || []).filter(isLive).map((g) => {
    const { file, productId, ...rest } = g;
    rest.variants = (g.variants || []).map(({ file: f, productId: pid, ...v }) => v);
    return rest;
  });
  if (out.cartDiscount) out.cartDiscount = { percent: out.cartDiscount.percent, minItems: out.cartDiscount.minItems };
  return out;
}

async function catalogResponse(env, json) {
  if (!env.STORE) return json({ error: 'No catalog' }, 404);
  const raw = await env.STORE.get('catalog');
  if (!raw) return json({ error: 'No catalog' }, 404, { 'Cache-Control': 'no-store' });
  return new Response(JSON.stringify(publicCatalog(JSON.parse(raw))), { headers: { 'Content-Type': 'application/json', 'Cache-Control': 'public, max-age=60', 'Access-Control-Allow-Origin': '*' } });
}

async function saveCatalog(catalog, env, request) {
  if (!env.STORE) throw fail('The STORE KV namespace is not bound', 500);
  if (!catalog || typeof catalog !== 'object' || !Array.isArray(catalog.guides)) throw fail('The catalog must be an object with a guides array');
  delete catalog._empty;
  catalog.updatedAt = new Date().toISOString();
  const text = JSON.stringify(catalog);
  if (text.length > 4 * 1024 * 1024) throw fail('The catalog is too large (4 MB limit)');
  const previous = await env.STORE.get('catalog');
  if (previous) await env.STORE.put(`backup:${Date.now()}`, previous, { expirationTtl: 60 * 60 * 24 * 90 });
  await env.STORE.put('catalog', text);
  await logEvent(env, request, 'publish', `${catalog.guides.length} titles`);
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
// Public media: images, the presenter video, and free PDFs for the "Free help" section. Paid product files use FILE_TYPES and stay private.
const MEDIA_TYPES = { jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png', webp: 'image/webp', avif: 'image/avif', gif: 'image/gif', svg: 'image/svg+xml', mp4: 'video/mp4', webm: 'video/webm', mov: 'video/quicktime', pdf: 'application/pdf' };
const FILE_TYPES = { pdf: 'application/pdf', epub: 'application/epub+zip', zip: 'application/zip', mobi: 'application/x-mobipocket-ebook', mp3: 'audio/mpeg', mp4: 'video/mp4', jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png', webp: 'image/webp' };

function safeName(name) {
  const base = String(name || 'file').toLowerCase().replace(/[^a-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 80);
  return base || 'file';
}

/* ---------- File storage: R2 when bound, otherwise the KV namespace (no card needed; 25 MB per file) ---------- */
const KV_FILE_LIMIT = 25 * 1024 * 1024 - 4096;
function files(env) {
  if (env.FILES) return {
    kind: 'r2',
    limit: Infinity,
    put: (key, body, contentType) => env.FILES.put(key, body, { httpMetadata: { contentType } }),
    get: async (key) => { const o = await env.FILES.get(key); return o ? { body: o.body, contentType: o.httpMetadata && o.httpMetadata.contentType } : null; },
    head: (key) => env.FILES.head(key),
    delete: (key) => env.FILES.delete(key),
    list: async (prefix) => (await env.FILES.list({ prefix, limit: 500 })).objects.map((o) => ({ key: o.key, size: o.size, uploaded: o.uploaded }))
  };
  if (env.STORE) return {
    kind: 'kv',
    limit: KV_FILE_LIMIT,
    put: async (key, body, contentType) => {
      const bytes = await new Response(body).arrayBuffer();
      if (bytes.byteLength > KV_FILE_LIMIT) throw fail('Files are limited to 25 MB each on KV storage. Enable R2 in Cloudflare for larger files.', 413);
      await env.STORE.put(`blob:${key}`, bytes);
      await env.STORE.put(`blobmeta:${key}`, JSON.stringify({ size: bytes.byteLength, uploaded: new Date().toISOString(), contentType }));
    },
    get: async (key) => { const meta = await env.STORE.get(`blobmeta:${key}`); if (!meta) return null; const body = await env.STORE.get(`blob:${key}`, 'stream'); return body ? { body, contentType: JSON.parse(meta).contentType } : null; },
    head: async (key) => { const meta = await env.STORE.get(`blobmeta:${key}`); return meta ? JSON.parse(meta) : null; },
    delete: async (key) => { await env.STORE.delete(`blob:${key}`); await env.STORE.delete(`blobmeta:${key}`); },
    list: async (prefix) => {
      const l = await env.STORE.list({ prefix: `blobmeta:${prefix}`, limit: 500 });
      const out = [];
      for (const k of l.keys) { const meta = JSON.parse((await env.STORE.get(k.name)) || '{}'); out.push({ key: k.name.slice('blobmeta:'.length), size: meta.size || 0, uploaded: meta.uploaded }); }
      return out;
    }
  };
  return null;
}

async function upload(request, url, env) {
  const store = files(env);
  if (!store) throw fail('File storage is not configured (bind FILES or STORE)', 500);
  const kind = url.searchParams.get('kind') === 'file' ? 'file' : 'media';
  const name = safeName(url.searchParams.get('name') || request.headers.get('X-File-Name'));
  const ext = name.includes('.') ? name.split('.').pop() : '';
  const types = kind === 'file' ? FILE_TYPES : MEDIA_TYPES;
  if (!types[ext]) throw fail(`Unsupported ${kind} type .${ext}`);
  const length = Number(request.headers.get('Content-Length')) || 0;
  const limit = Math.min(store.limit, kind === 'file' || /^(mp4|webm|mov)$/.test(ext) ? 95 * 1024 * 1024 : 12 * 1024 * 1024);
  if (length > limit) throw fail(`File too large (limit ${Math.round(limit / 1048576)} MB)`);
  const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, '');
  const key = `${kind === 'file' ? 'files' : 'media'}/${stamp}-${crypto.randomUUID().slice(0, 8)}-${name}`;
  await store.put(key, request.body, types[ext]);
  await logEvent(env, request, 'upload', key);
  return { key, url: kind === 'media' ? `${url.origin}/media/${key.slice('media/'.length)}` : '', kind, name, size: length };
}

async function removeUpload(key, env) {
  const store = files(env);
  if (!store) throw fail('File storage is not configured', 500);
  if (!/^(media|files)\/[a-z0-9._\/-]+$/i.test(String(key || ''))) throw fail('Invalid key');
  await store.delete(key);
  return { ok: true };
}

async function listFiles(kind, env, url) {
  const store = files(env);
  if (!store) throw fail('File storage is not configured', 500);
  const prefix = kind === 'file' ? 'files/' : 'media/';
  const objects = await store.list(prefix);
  return {
    storage: store.kind,
    limitMb: Number.isFinite(store.limit) ? Math.floor(store.limit / 1048576) : 0,
    objects: objects.map((o) => ({ key: o.key, size: o.size, uploaded: o.uploaded, url: prefix === 'media/' ? `${url.origin}/media/${o.key.slice(6)}` : '' }))
  };
}

async function serveMedia(key, env, cors) {
  const store = files(env);
  if (!store) return new Response('Not configured', { status: 500 });
  if (!/^[a-z0-9._\/-]+$/i.test(key) || key.includes('..')) return new Response('Not found', { status: 404 });
  const object = await store.get(`media/${key}`);
  if (!object) return new Response('Not found', { status: 404 });
  return new Response(object.body, {
    headers: {
      'Content-Type': object.contentType || 'application/octet-stream',
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
  await logEvent(env, null, 'stripe', `${name}: ${price.id}`);
  return { guide, productId: product.id, priceId: price.id, paymentLink: target.paymentLink };
}

/* ---------- Coupons and pre-order delivery ---------- */
async function createStripeCoupon(body, env) {
  const percent = Number(body.percent);
  if (!(percent > 0 && percent < 100)) throw fail('Percent must be between 1 and 99');
  const params = new URLSearchParams();
  params.set('percent_off', String(percent));
  params.set('duration', 'once');
  params.set('name', String(body.name || `${percent}% off ${body.minItems || 2}+ titles`).slice(0, 40));
  const coupon = await stripe(env, 'POST', '/coupons', params);
  const catalog = await loadCatalog(env);
  catalog.cartDiscount = { percent, minItems: Math.max(2, Number(body.minItems) || 2), couponId: coupon.id };
  await saveCatalog(catalog, env);
  return { couponId: coupon.id, cartDiscount: catalog.cartDiscount };
}

async function deliver(body, env, url) {
  // Email fresh download links to everyone who bought a given price (used when a pre-order ships or a file is replaced).
  if (!/^price_[A-Za-z0-9]+$/.test(String(body.priceId || ''))) throw fail('priceId is required');
  const sessions = [];
  let startingAfter = '';
  for (let page = 0; page < 20; page += 1) {
    const params = new URLSearchParams();
    params.set('status', 'complete');
    params.set('limit', '100');
    params.append('expand[]', 'data.line_items.data.price.product');
    if (startingAfter) params.set('starting_after', startingAfter);
    const result = await stripe(env, 'GET', '/checkout/sessions', params);
    for (const session of result.data || []) {
      if (session.payment_status !== 'paid') continue;
      if (((session.line_items && session.line_items.data) || []).some((line) => line.price && line.price.id === body.priceId)) sessions.push(session);
    }
    if (!result.has_more) break;
    startingAfter = result.data[result.data.length - 1].id;
  }
  if (body.dryRun) return { sent: 0, recipients: sessions.length };
  let sent = 0;
  const seen = new Set();
  for (const session of sessions) {
    const to = (session.metadata && session.metadata.gift_email) || (session.customer_details && session.customer_details.email);
    if (!isEmail(to) || seen.has(to)) continue;
    seen.add(to);
    const items = await downloadLinks(env, session, url.origin);
    if (!items.length || items.every((i) => i.preorder)) continue;
    const t = await getTemplates(env);
    await sendEmail(env, to, String(body.subject || t.release.subject).slice(0, 150), `<div style="font-family:sans-serif;line-height:1.6">${linksHtml(items, body.message ? String(body.message).slice(0, 4000) : t.release.intro)}</div>`);
    sent += 1;
  }
  return { sent, recipients: sessions.length };
}

/* ---------- Coupons ---------- */
async function listCoupons(env) {
  const [coupons, codes] = await Promise.all([
    stripe(env, 'GET', '/coupons', new URLSearchParams({ limit: '100' })),
    stripe(env, 'GET', '/promotion_codes', new URLSearchParams({ limit: '100' }))
  ]);
  const byCoupon = {};
  for (const c of codes.data || []) { const id = c.coupon && c.coupon.id; if (!id) continue; (byCoupon[id] = byCoupon[id] || []).push({ id: c.id, code: c.code, active: c.active, timesRedeemed: c.times_redeemed, maxRedemptions: c.max_redemptions, expiresAt: c.expires_at ? new Date(c.expires_at * 1000).toISOString() : '' }); }
  return { coupons: (coupons.data || []).map((c) => ({ id: c.id, name: c.name, percentOff: c.percent_off, amountOff: c.amount_off ? c.amount_off / 100 : null, currency: c.currency ? c.currency.toUpperCase() : '', duration: c.duration, valid: c.valid, timesRedeemed: c.times_redeemed, created: new Date(c.created * 1000).toISOString(), codes: byCoupon[c.id] || [] })) };
}

async function createCoupon(request, body, env) {
  const params = new URLSearchParams();
  const percent = Number(body.percent);
  const amount = Number(body.amount);
  if (percent > 0 && percent <= 100) params.set('percent_off', String(percent));
  else if (amount > 0) { params.set('amount_off', String(Math.round(amount * 100))); params.set('currency', String(body.currency || 'cad').toLowerCase()); }
  else throw fail('Give a percent or an amount');
  params.set('duration', 'once');
  if (body.name) params.set('name', String(body.name).slice(0, 40));
  if (Number(body.maxRedemptions) > 0) params.set('max_redemptions', String(Math.floor(Number(body.maxRedemptions))));
  if (body.expiresAt && !Number.isNaN(Date.parse(body.expiresAt))) params.set('redeem_by', String(Math.floor(Date.parse(body.expiresAt) / 1000)));
  const coupon = await stripe(env, 'POST', '/coupons', params);
  let promotionCode = null;
  const code = String(body.code || '').toUpperCase().replace(/[^A-Z0-9_-]/g, '').slice(0, 30);
  if (code) {
    const codeParams = new URLSearchParams();
    codeParams.set('coupon', coupon.id);
    codeParams.set('code', code);
    promotionCode = await stripe(env, 'POST', '/promotion_codes', codeParams);
  }
  await logEvent(env, request, 'coupon', `${coupon.id}${code ? ` code ${code}` : ''}`);
  return { coupon: { id: coupon.id, name: coupon.name }, promotionCode: promotionCode ? { id: promotionCode.id, code: promotionCode.code } : null };
}

async function deleteCoupon(request, id, env) {
  if (!/^[A-Za-z0-9_-]+$/.test(id)) throw fail('Invalid coupon id');
  await stripe(env, 'DELETE', `/coupons/${id}`);
  await logEvent(env, request, 'coupon', `deleted ${id}`);
  return { ok: true };
}

/* ---------- Refunds ---------- */
async function refund(request, body, env) {
  if (!/^cs_[A-Za-z0-9_]+$/.test(String(body.sessionId || ''))) throw fail('Invalid session');
  const session = await stripe(env, 'GET', `/checkout/sessions/${body.sessionId}`);
  if (!session.payment_intent) throw fail('This order has no payment to refund');
  const params = new URLSearchParams();
  params.set('payment_intent', session.payment_intent);
  if (Number(body.amount) > 0) params.set('amount', String(Math.round(Number(body.amount) * 100)));
  const result = await stripe(env, 'POST', '/refunds', params);
  await logEvent(env, request, 'refund', `${body.sessionId} ${result.amount / 100} ${String(result.currency).toUpperCase()}`);
  return { refund: { id: result.id, amount: result.amount / 100, currency: String(result.currency).toUpperCase(), status: result.status } };
}

/* ---------- E-mail templates ---------- */
const DEFAULT_TEMPLATES = {
  downloads: { subject: 'Your Marufi Digital downloads', intro: 'Here are your downloads from Marufi Digital:' },
  gift: { subject: 'You have been sent a gift from Marufi Digital', intro: 'Someone sent you a gift from Marufi Digital.' },
  release: { subject: 'Your Marufi Digital download is ready', intro: 'The title you pre-ordered has shipped. Your download links are below.' },
  request: { subject: 'The guide you asked for is ready', intro: 'You asked us to tell you when this place was covered. It is live now.' }
};
async function getTemplates(env) {
  const raw = env.STORE ? await env.STORE.get('templates') : null;
  const stored = raw ? JSON.parse(raw) : {};
  const out = {};
  for (const key of Object.keys(DEFAULT_TEMPLATES)) out[key] = { ...DEFAULT_TEMPLATES[key], ...(stored[key] || {}) };
  return out;
}
async function putTemplates(request, body, env) {
  if (!env.STORE) throw fail('The STORE KV namespace is not bound', 500);
  const out = {};
  for (const key of Object.keys(DEFAULT_TEMPLATES)) {
    const t = body[key] && typeof body[key] === 'object' ? body[key] : {};
    out[key] = { subject: String(t.subject || DEFAULT_TEMPLATES[key].subject).slice(0, 150), intro: String(t.intro || DEFAULT_TEMPLATES[key].intro).slice(0, 2000) };
  }
  await env.STORE.put('templates', JSON.stringify(out));
  await logEvent(env, request, 'templates', 'updated');
  return { ok: true, templates: out };
}

/* ---------- Notify-me matching ---------- */
function normalisePlace(text) {
  return String(text || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^a-z0-9]+/g, ' ').trim();
}
async function notifyRequesters(request, body, env) {
  const catalog = await loadCatalog(env);
  const guide = catalog.guides.find((g) => g.id === body.guideId);
  if (!guide) throw fail('Title not found');
  const targets = [normalisePlace(guide.state), normalisePlace(guide.country)].filter(Boolean);
  const { requests } = await listRequests(env);
  const matched = requests.filter((r) => { const p = normalisePlace(r.place); return targets.some((t) => t && (p.includes(t) || t.includes(p))); });
  if (body.dryRun) return { matched: matched.length, sent: 0, places: [...new Set(matched.map((r) => r.place))] };
  const templates = await getTemplates(env);
  const site = env.SITE_URL || catalog.siteUrl || '';
  let sent = 0;
  const seen = new Set();
  for (const r of matched) {
    if (seen.has(r.email)) { await env.STORE.delete(r.key); continue; }
    seen.add(r.email);
    const link = site ? `${site}/?country=${encodeURIComponent(guide.country || '')}&q=${encodeURIComponent(guide.title)}#guides` : '';
    await sendEmail(env, r.email, String(body.subject || `${guide.title} is ready`).slice(0, 150), `<div style="font-family:sans-serif;line-height:1.6"><p>${escapeHtml(body.message || templates.request.intro)}</p><p><strong>${escapeHtml(guide.title)}</strong>${guide.description ? ` — ${escapeHtml(guide.description)}` : ''}</p>${link ? `<p><a href="${link}">Open the listing</a></p>` : ''}</div>`);
    await env.STORE.delete(r.key);
    sent += 1;
  }
  await logEvent(env, request, 'notify', `${guide.title}: ${sent} e-mails`);
  return { matched: matched.length, sent };
}

/* ---------- Health check ---------- */
async function health(env) {
  const catalog = await loadCatalog(env);
  const issues = [];
  const push = (level, guide, message) => issues.push({ level, guideId: guide ? guide.id : '', title: guide ? guide.title : '', message });
  for (const g of catalog.guides || []) {
    if (!g.cover) push('warn', g, 'No cover image');
    if (!g.priceId && !g.paymentLink) push('error', g, 'Not sellable: no Stripe price or payment link');
    if (g.paymentLink && !g.priceId) push('info', g, 'Payment link only: cannot be bought together with other titles in the cart');
    if (!g.file && !isPreorderPending(g)) push('warn', g, 'No product file: buyers would get nothing to download');
    if (g.status === 'scheduled' && g.publishAt && Date.parse(g.publishAt) <= Date.now()) push('info', g, 'Scheduled date has passed; it is live now');
    if (isPreorderPending(g) && g.file) push('info', g, 'Pre-order has a file attached: ready to deliver');
    if (g.file && files(env)) { const head = await files(env).head(g.file); if (!head) push('error', g, `Product file missing from storage: ${g.file}`); }
    if (g.priceId && env.STRIPE_SECRET_KEY) {
      try {
        const price = await stripe(env, 'GET', `/prices/${g.priceId}`);
        const expected = Math.round(Number(g.salePrice && !(g.saleEnds && Date.parse(g.saleEnds) < Date.now()) ? g.salePrice : g.price) * 100);
        if (!price.active) push('error', g, 'Stripe price is archived');
        else if (price.unit_amount !== expected || price.currency !== String(catalog.currency || 'CAD').toLowerCase()) push('warn', g, `Stripe charges ${price.unit_amount / 100} ${price.currency.toUpperCase()} but the catalog shows ${expected / 100} ${catalog.currency}. Use "Sync with Stripe".`);
      } catch (error) { push('error', g, `Stripe price check failed: ${error.message}`); }
    }
  }
  if (!catalog.siteUrl) push('info', null, 'Site URL is not set (used in structured data and e-mails)');
  // E-mail deliverability: the sending domain needs SPF, DKIM, and DMARC records or receipts land in spam.
  const from = String(env.FROM_EMAIL || '');
  const domain = (from.match(/@([a-z0-9.-]+)/i) || [])[1];
  if (!env.RESEND_API_KEY || !from) push('warn', null, 'E-mail is not configured (RESEND_API_KEY and FROM_EMAIL): receipts, download links, codes, and the newsletter cannot be sent');
  else if (domain) {
    const txt = async (name) => { try { const r = await fetch(`https://cloudflare-dns.com/dns-query?name=${encodeURIComponent(name)}&type=TXT`, { headers: { Accept: 'application/dns-json' } }); const d = await r.json(); return (d.Answer || []).map((a) => String(a.data || '').replace(/"/g, '')); } catch { return null; } };
    const spfRoot = await txt(domain); const spfSend = await txt(`send.${domain}`); const dkim = await txt(`resend._domainkey.${domain}`); const dmarc = await txt(`_dmarc.${domain}`);
    if (spfRoot === null) push('info', null, `Could not check DNS for ${domain} right now`);
    else {
      const hasSpf = [...(spfRoot || []), ...(spfSend || [])].some((v) => /^v=spf1/i.test(v) && /amazonses|resend/i.test(v));
      if (!hasSpf) push('warn', null, `No SPF record for ${domain} that allows Resend (add the TXT record from the Resend dashboard, usually on send.${domain})`);
      if (!(dkim || []).some((v) => /^p=|v=DKIM1/i.test(v))) push('warn', null, `No DKIM record at resend._domainkey.${domain} (copy it from the Resend dashboard)`);
      if (!(dmarc || []).some((v) => /^v=DMARC1/i.test(v))) push('info', null, `No DMARC record at _dmarc.${domain} (add TXT "v=DMARC1; p=none; rua=mailto:${env.CONTACT_EMAIL || `postmaster@${domain}`}")`);
      if (hasSpf && (dkim || []).length && (dmarc || []).length) push('ok', null, `E-mail domain ${domain} has SPF, DKIM, and DMARC`);
    }
  }
  if (!catalog.contactEmail) push('info', null, 'Contact e-mail is not set');
  return { issues, checkedAt: new Date().toISOString(), titles: (catalog.guides || []).length };
}
function isPreorderPending(g) {
  return Boolean(g.preorder) || (g.releaseDate && !Number.isNaN(Date.parse(g.releaseDate)) && Date.parse(g.releaseDate) > Date.now());
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
        paymentIntent: typeof session.payment_intent === 'string' ? session.payment_intent : (session.payment_intent && session.payment_intent.id) || '',
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
  // Mark refunded orders.
  try {
    const refunds = await stripe(env, 'GET', '/refunds', new URLSearchParams({ limit: '100', 'created[gte]': String(since) }));
    const refunded = new Set((refunds.data || []).map((r) => r.payment_intent));
    for (const order of result) order.refunded = refunded.has(order.paymentIntent);
  } catch { /* refunds are optional */ }
  const byTitle = {};
  const byCurrency = {};
  for (const order of result) {
    if (order.refunded) continue;
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
  params.set('billing_address_collection', 'auto');
  items.forEach((price, index) => {
    params.set(`line_items[${index}][price]`, price);
    params.set(`line_items[${index}][quantity]`, '1');
  });
  // Multi-title discount: a Stripe coupon applied automatically once the cart reaches the minimum.
  // Stripe does not allow promotion codes and automatic discounts on the same session, so one or the other.
  const catalog = await readCatalog(env);
  const discount = catalog && catalog.cartDiscount && typeof catalog.cartDiscount === 'object' ? catalog.cartDiscount : null;
  const couponId = (discount && discount.couponId) || env.CART_COUPON || '';
  const minItems = Number((discount && discount.minItems) || env.CART_COUPON_MIN || 2);
  if (couponId && items.length >= minItems) params.set('discounts[0][coupon]', couponId);
  else params.set('allow_promotion_codes', 'true');
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

function preorderInfo(guide) {
  const release = guide.releaseDate && !Number.isNaN(Date.parse(guide.releaseDate)) ? Date.parse(guide.releaseDate) : 0;
  const pending = Boolean(guide.preorder) || release > Date.now();
  return pending ? { preorder: true, releaseDate: release ? new Date(release).toISOString() : '' } : null;
}

async function fileForLine(line, env) {
  const product = line.price && line.price.product;
  const catalog = line.price ? await readCatalog(env) : null;
  let entry = null;
  if (catalog) {
    for (const guide of catalog.guides || []) {
      if (guide.priceId === line.price.id) { entry = { key: guide.file || '', name: guide.title, pre: preorderInfo(guide) }; break; }
      const variant = (guide.variants || []).find((v) => v.priceId === line.price.id);
      if (variant) { entry = { key: variant.file || guide.file || '', name: `${guide.title} · ${variant.label}`, pre: preorderInfo(guide) }; break; }
    }
  }
  if (entry && entry.pre) return { name: entry.name, key: '', ...entry.pre };
  if (product && product.metadata && product.metadata.file) return { key: product.metadata.file, name: product.name };
  if (entry && entry.key) return { key: entry.key, name: entry.name };
  return null;
}

async function downloadLinks(env, session, base) {
  const hours = Number(env.DOWNLOAD_HOURS) || 72;
  const exp = Date.now() + hours * 3600 * 1000;
  const items = [];
  for (const line of session.line_items.data) {
    const file = await fileForLine(line, env);
    if (!file) continue;
    if (file.preorder) { items.push({ name: file.name, preorder: true, releaseDate: file.releaseDate }); continue; }
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
      const t = await getTemplates(env);
      await sendEmail(env, giftEmail, t.gift.subject, giftHtml(items, session.metadata.gift_message, email, t.gift.intro));
      await env.STORE.put(flag, '1');
    }
    giftSent = true;
  }
  const giftCard = session.metadata && session.metadata.gift_card ? await issueGiftCard(session, env) : null;
  return { email, items, gift: giftEmail ? { email: giftEmail, sent: giftSent } : null, giftCard };
}

async function serveFile(token, env) {
  const payload = await verifyToken(env, token);
  if (!payload || !payload.key) return new Response('This download link has expired. Use "Resend my download" on the store to get a new one.', { status: 403 });
  const store = files(env);
  if (!store) return new Response('File storage is not configured', { status: 500 });
  const object = await store.get(payload.key);
  if (!object) return new Response('File not found', { status: 404 });
  const filename = payload.key.split('/').pop();
  return new Response(object.body, {
    headers: {
      'Content-Type': object.contentType || 'application/octet-stream',
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
    if (items.length) { const t = await getTemplates(env); await sendEmail(env, body.email, t.downloads.subject, linksHtml(items, t.downloads.intro)); }
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

/* ---------- Rate limiting (approximate, per KV counter) ---------- */
async function throttle(env, bucket, request, max, ttl = 3600) {
  if (!env.STORE) return;
  const key = `rl:${bucket}:${clientIp(request)}`;
  const count = Number(await env.STORE.get(key)) || 0;
  if (count >= max) throw fail('Too many requests. Please try again later.', 429);
  await env.STORE.put(key, String(count + 1), { expirationTtl: ttl });
}
async function listKeyed(prefix, env) {
  if (!env.STORE) throw fail('The STORE KV namespace is not bound', 500);
  const list = await env.STORE.list({ prefix, limit: 1000 });
  const items = [];
  for (const k of list.keys) { const raw = await env.STORE.get(k.name); if (raw) items.push({ key: k.name, ...JSON.parse(raw) }); }
  return { items: items.sort((a, b) => (a.at < b.at ? 1 : -1)) };
}
async function deleteKeyed(prefix, key, env) {
  if (!env.STORE) throw fail('The STORE KV namespace is not bound', 500);
  if (!key.startsWith(prefix) || !/^[a-z]+:[A-Za-z0-9_:-]+$/i.test(key)) throw fail('Invalid key');
  await env.STORE.delete(key);
  return { ok: true };
}
function htmlPage(title, body, env) {
  const home = env.SITE_URL || '/';
  return new Response(`<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${escapeHtml(title)}</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f5f0e6;color:#171b1a;font:16px/1.6 -apple-system,Segoe UI,sans-serif}main{max-width:32rem;padding:2rem;text-align:center}h1{font:500 2rem/1.1 Georgia,serif;margin:0 0 1rem}a{color:#9a3a20}</style></head><body><main><h1>${escapeHtml(title)}</h1><p>${body}</p><p><a href="${escapeHtml(home)}">Back to the store</a></p></main></body></html>`, { headers: { 'Content-Type': 'text/html; charset=utf-8' } });
}

/* ---------- Contact form ---------- */
async function contact(request, body, env) {
  const name = String(body.name || '').trim().slice(0, 120);
  const message = String(body.message || '').trim().slice(0, 4000);
  const topic = String(body.topic || '').trim().slice(0, 60);
  if (!isEmail(body.email)) throw fail('Please enter a valid e-mail address');
  if (!message) throw fail('Please write a message');
  if (!env.STORE && !(env.CONTACT_EMAIL && env.RESEND_API_KEY)) throw fail('The contact form is not configured yet', 500);
  await throttle(env, 'contact', request, 5);
  const record = { name, email: body.email.trim(), topic, message, at: new Date().toISOString() };
  if (env.STORE) await env.STORE.put(`message:${Date.now()}:${crypto.randomUUID().slice(0, 8)}`, JSON.stringify(record));
  if (env.CONTACT_EMAIL && env.RESEND_API_KEY && env.FROM_EMAIL) {
    try { await sendEmail(env, env.CONTACT_EMAIL, `Message from ${name || record.email}${topic ? ` · ${topic}` : ''}`, `<div style="font-family:sans-serif;line-height:1.6"><p><strong>${escapeHtml(name || 'No name')}</strong> &lt;${escapeHtml(record.email)}&gt;${topic ? ` · ${escapeHtml(topic)}` : ''}</p><p style="white-space:pre-wrap">${escapeHtml(message)}</p></div>`); }
    catch { /* stored anyway; the admin shows it */ }
  }
  return { ok: true };
}

/* ---------- Newsletter ---------- */
async function subscriberKey(email) { return `subscriber:${b64url(await sha256(String(email).trim().toLowerCase()))}`; }
async function subscribe(request, body, env, url) {
  if (!isEmail(body.email)) throw fail('Please enter a valid e-mail address');
  if (!env.STORE) throw fail('The newsletter is not configured yet', 500);
  await throttle(env, 'subscribe', request, 10);
  const key = await subscriberKey(body.email);
  const existing = await env.STORE.get(key);
  if (existing && JSON.parse(existing).confirmed) return { ok: true, doubleOptIn: false };
  const canMail = Boolean(env.RESEND_API_KEY && env.FROM_EMAIL);
  const record = { email: body.email.trim(), source: String(body.source || '').slice(0, 60), at: new Date().toISOString(), confirmed: !canMail };
  await env.STORE.put(key, JSON.stringify(record));
  if (canMail) {
    const token = await signToken(env, { email: record.email, c: 1, exp: Date.now() + 7 * 86400000 });
    const link = `${url.origin}/confirm?t=${token}`;
    await sendEmail(env, record.email, 'Please confirm your subscription', `<div style="font-family:sans-serif;line-height:1.6"><p>One click and you are on the list for new titles and free help.</p><p><a href="${link}">Confirm my subscription</a></p><p style="color:#666">If you did not ask for this, ignore this e-mail and nothing happens.</p></div>`);
  }
  return { ok: true, doubleOptIn: canMail };
}
async function confirmSubscription(token, env) {
  const payload = await verifyToken(env, token);
  if (!payload || !payload.c || !isEmail(payload.email)) return htmlPage('This link has expired', 'Subscribe again from the store and we will send a fresh one.', env);
  const key = await subscriberKey(payload.email);
  const raw = await env.STORE.get(key);
  const record = raw ? JSON.parse(raw) : { email: payload.email, at: new Date().toISOString() };
  record.confirmed = true;
  record.confirmedAt = new Date().toISOString();
  await env.STORE.put(key, JSON.stringify(record));
  return htmlPage('You are on the list', 'Thank you. We write only when there is something new worth your time, and every e-mail has an unsubscribe link.', env);
}
async function unsubscribe(token, env) {
  const payload = await verifyToken(env, token);
  if (!payload || !payload.u || !isEmail(payload.email)) return htmlPage('This link has expired', 'Reply to any of our e-mails and we will remove you by hand.', env);
  await env.STORE.delete(await subscriberKey(payload.email));
  return htmlPage('Unsubscribed', 'You will not hear from us again unless you subscribe once more.', env);
}
async function listSubscribers(env) {
  const { items } = await listKeyed('subscriber:', env);
  return { subscribers: items, confirmed: items.filter((s) => s.confirmed).length, pending: items.filter((s) => !s.confirmed).length };
}
async function newsletter(request, body, env, url) {
  const subject = String(body.subject || '').trim().slice(0, 150);
  const message = String(body.message || '').trim().slice(0, 8000);
  if (!body.dryRun && (!subject || !message)) throw fail('subject and message are required');
  const { subscribers } = await listSubscribers(env);
  const recipients = subscribers.filter((s) => s.confirmed && isEmail(s.email));
  if (body.dryRun) return { sent: 0, recipients: recipients.length };
  let sent = 0;
  for (const s of recipients) {
    const token = await signToken(env, { email: s.email, u: 1, exp: Date.now() + 365 * 86400000 });
    const html = `<div style="font-family:sans-serif;line-height:1.6">${escapeHtml(message).replace(/\n/g, '<br>')}<p style="margin-top:2em;color:#666;font-size:.9em"><a href="${url.origin}/unsubscribe?t=${token}" style="color:#666">Unsubscribe</a></p></div>`;
    try { await sendEmail(env, s.email, subject, html); sent += 1; } catch { /* keep going */ }
  }
  await logEvent(env, request, 'newsletter', `${subject} → ${sent}`);
  return { sent, recipients: recipients.length };
}

/* ---------- Customer accounts (passwordless: e-mail + one-time code, 30-day signed session) ---------- */
async function accountKey(email) { return `acct:${b64url(await sha256(String(email).trim().toLowerCase()))}`; }
function publicAccount(record) {
  return { email: record.email, name: record.name || '', country: record.country || '', saved: Array.isArray(record.saved) ? record.saved : [], createdAt: record.createdAt, lastSeen: record.lastSeen || '' };
}
async function accountCode(request, body, env) {
  if (!isEmail(body.email)) throw fail('Please enter a valid e-mail address');
  if (!env.STORE) throw fail('Accounts are not configured yet', 500);
  const canMail = Boolean(env.RESEND_API_KEY && env.FROM_EMAIL);
  if (!canMail && env.DEV_MODE !== '1') throw fail('E-mail is not configured on the server', 500);
  await throttle(env, 'account', request, 6, 900);
  const email = body.email.trim();
  const name = String(body.name || '').trim().slice(0, 80);
  const code = sixDigits();
  const id = crypto.randomUUID();
  const exists = Boolean(await env.STORE.get(await accountKey(email)));
  await env.STORE.put(`account:${id}`, JSON.stringify({ hash: b64url(await sha256(code)), attempts: 0, email, name }), { expirationTtl: 600 });
  const intro = exists ? 'Your sign-in code:' : 'Welcome. Your code to finish creating your account:';
  if (canMail) await sendEmail(env, email, exists ? 'Your sign-in code' : 'Your account code', `<div style="font-family:sans-serif;line-height:1.6"><p>${intro}</p><p style="font-size:2em;letter-spacing:.2em"><strong>${code}</strong></p><p style="color:#666">It works for ten minutes. If you did not ask for it, ignore this e-mail.</p></div>`);
  else console.log(`[dev] account code for ${email}: ${code}`);
  return { ok: true, challenge: id, existing: exists };
}
async function accountVerify(request, body, env) {
  const raw = env.STORE ? await env.STORE.get(`account:${String(body.challenge || '')}`) : null;
  const pending = raw ? JSON.parse(raw) : null;
  await checkCode(env, 'account', body.challenge, body.code);
  if (!pending || !isEmail(pending.email)) throw fail('Invalid or expired code', 401);
  const key = await accountKey(pending.email);
  const existing = await env.STORE.get(key);
  const now = new Date().toISOString();
  const record = existing ? JSON.parse(existing) : { email: pending.email, name: pending.name || '', country: '', saved: [], createdAt: now };
  if (!record.name && pending.name) record.name = pending.name;
  record.lastSeen = now;
  await env.STORE.put(key, JSON.stringify(record));
  const exp = Date.now() + 30 * 86400000;
  const token = await signToken(env, { email: record.email, a: 1, exp });
  await logEvent(env, request, existing ? 'customer-signin' : 'customer-signup', maskEmail(record.email));
  return { token, exp, account: publicAccount(record), created: !existing };
}
async function requireCustomer(request, env) {
  const header = request.headers.get('Authorization') || '';
  const token = header.startsWith('Bearer ') ? header.slice(7) : '';
  const payload = await verifyToken(env, token);
  if (!payload || payload.a !== 1 || !isEmail(payload.email)) throw fail('Please sign in again', 401);
  const key = await accountKey(payload.email);
  const raw = env.STORE ? await env.STORE.get(key) : null;
  if (!raw) throw fail('Please sign in again', 401);
  return { email: payload.email, key, record: JSON.parse(raw) };
}
async function accountMe(who, env) {
  const sub = await env.STORE.get(await subscriberKey(who.email));
  return { account: publicAccount(who.record), subscribed: Boolean(sub && JSON.parse(sub).confirmed) };
}
async function accountUpdate(who, body, env) {
  const record = who.record;
  if (typeof body.name === 'string') record.name = body.name.trim().slice(0, 80);
  if (typeof body.country === 'string') record.country = body.country.trim().slice(0, 60);
  if (Array.isArray(body.saved)) record.saved = [...new Set(body.saved.filter((id) => typeof id === 'string' && id.length < 120))].slice(0, 200);
  record.updatedAt = new Date().toISOString();
  await env.STORE.put(who.key, JSON.stringify(record));
  return { ok: true, account: publicAccount(record) };
}
async function accountDelete(who, env) {
  await env.STORE.delete(who.key);
  await env.STORE.delete(await subscriberKey(who.email));
  const list = await env.STORE.list({ prefix: 'notify:', limit: 1000 });
  for (const k of list.keys) { const raw = await env.STORE.get(k.name); if (raw && JSON.parse(raw).email === who.email) await env.STORE.delete(k.name); }
  return { ok: true };
}
async function ordersForEmail(email, env, base) {
  const params = new URLSearchParams();
  params.set('customer_details[email]', email);
  params.set('status', 'complete');
  params.set('limit', '50');
  params.append('expand[]', 'data.line_items.data.price.product');
  const result = await stripe(env, 'GET', '/checkout/sessions', params);
  const orders = [];
  for (const session of result.data || []) {
    if (session.payment_status !== 'paid') continue;
    const lines = (session.line_items && session.line_items.data) || [];
    orders.push({
      id: session.id,
      created: new Date(session.created * 1000).toISOString(),
      total: (session.amount_total || 0) / 100,
      currency: String(session.currency || '').toUpperCase(),
      giftCard: session.metadata && session.metadata.gift_card ? Number(session.metadata.gift_card) : 0,
      items: await downloadLinks(env, session, base),
      lines: lines.map((l) => ({ name: l.description, priceId: l.price && l.price.id, amount: (l.amount_total || 0) / 100 }))
    });
  }
  return orders.sort((a, b) => (a.created < b.created ? 1 : -1));
}
async function accountOrders(who, env, url) {
  if (!env.STRIPE_SECRET_KEY) return { email: who.email, orders: [], stripe: false };
  return { email: who.email, orders: await ordersForEmail(who.email, env, url.origin), stripe: true };
}
async function accountGifts(who, env) {
  const { items } = await listKeyed('giftcard:', env);
  const mine = items.filter((g) => g.to === who.email || g.buyer === who.email);
  for (const g of mine) {
    g.received = g.to === who.email;
    if (env.STRIPE_SECRET_KEY) {
      try { const r = await stripe(env, 'GET', '/promotion_codes', new URLSearchParams({ code: g.code, limit: '1' })); const p = (r.data || [])[0]; if (p) { g.redeemed = p.times_redeemed > 0; g.active = p.active; } } catch { /* leave unknown */ }
    }
    delete g.key;
  }
  return { gifts: mine };
}
async function accountRequests(who, env) {
  const { items } = await listKeyed('notify:', env);
  return { requests: items.filter((r) => r.email === who.email).map(({ key, ...r }) => r) };
}
async function accountNewsletter(who, body, env) {
  const key = await subscriberKey(who.email);
  if (body.subscribed) await env.STORE.put(key, JSON.stringify({ email: who.email, source: 'account', at: new Date().toISOString(), confirmed: true, confirmedAt: new Date().toISOString() }));
  else await env.STORE.delete(key);
  return { ok: true, subscribed: Boolean(body.subscribed) };
}

/* ---------- Gift cards ---------- */
async function giftCardSession(request, body, env) {
  if (!env.SUCCESS_URL || !env.CANCEL_URL) throw fail('Checkout is not configured', 500);
  const catalog = (await readCatalog(env)) || {};
  const settings = catalog.giftCards && typeof catalog.giftCards === 'object' ? catalog.giftCards : {};
  if (settings.enabled === false) throw fail('Gift cards are not available right now');
  const amounts = (Array.isArray(settings.amounts) && settings.amounts.length ? settings.amounts : [25, 50, 100]).map(Number).filter((n) => n > 0);
  const amount = Number(body.amount);
  if (!amounts.includes(amount)) throw fail('Choose one of the offered amounts');
  const to = isEmail(body.to) ? body.to.trim() : '';
  const from = String(body.from || '').trim().slice(0, 80);
  const message = String(body.message || '').trim().slice(0, 400);
  await throttle(env, 'gift', request, 10);
  const currency = String(catalog.currency || 'CAD').toLowerCase();
  const params = new URLSearchParams();
  params.set('mode', 'payment');
  params.set('success_url', env.SUCCESS_URL);
  params.set('cancel_url', env.CANCEL_URL);
  params.set('line_items[0][quantity]', '1');
  params.set('line_items[0][price_data][currency]', currency);
  params.set('line_items[0][price_data][unit_amount]', String(Math.round(amount * 100)));
  params.set('line_items[0][price_data][product_data][name]', `${catalog.storeName || 'Marufi Digital'} gift card`);
  params.set('line_items[0][price_data][product_data][description]', `A ${amount} ${currency.toUpperCase()} code, redeemable at checkout${to ? `, sent to ${to}` : ''}`);
  params.set('metadata[gift_card]', String(amount));
  if (to) params.set('metadata[gift_to]', to);
  if (from) params.set('metadata[gift_from]', from);
  if (message) params.set('metadata[gift_message]', message);
  const session = await stripe(env, 'POST', '/checkout/sessions', params);
  if (!session.url) throw fail('Stripe could not create the session', 502);
  return { url: session.url };
}
function giftCode() {
  const alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  const bytes = crypto.getRandomValues(new Uint8Array(8));
  const chars = [...bytes].map((b) => alphabet[b % alphabet.length]).join('');
  return `GIFT-${chars.slice(0, 4)}-${chars.slice(4)}`;
}
async function issueGiftCard(session, env) {
  const amount = Number(session.metadata.gift_card);
  if (!(amount > 0)) return null;
  const flag = `giftcard:${session.id}`;
  const existing = env.STORE ? await env.STORE.get(flag) : null;
  if (existing) return JSON.parse(existing);
  const currency = String(session.currency || 'cad').toLowerCase();
  const coupon = await stripe(env, 'POST', '/coupons', new URLSearchParams({ amount_off: String(Math.round(amount * 100)), currency, duration: 'once', max_redemptions: '1', name: `Gift card ${amount} ${currency.toUpperCase()}` }));
  const code = giftCode();
  await stripe(env, 'POST', '/promotion_codes', new URLSearchParams({ coupon: coupon.id, code, max_redemptions: '1' }));
  const to = session.metadata.gift_to || '';
  const buyer = session.customer_details && session.customer_details.email;
  const record = { code, amount, currency: currency.toUpperCase(), to, buyer: buyer || '', from: session.metadata.gift_from || '', sentTo: [], at: new Date().toISOString() };
  if (env.RESEND_API_KEY && env.FROM_EMAIL) {
    const note = session.metadata.gift_message ? `<blockquote style="border-left:3px solid #9a3a20;margin:1em 0;padding:.25em 1em;color:#333">${escapeHtml(session.metadata.gift_message)}</blockquote>` : '';
    const body = (intro) => `<div style="font-family:sans-serif;line-height:1.6"><p>${intro}</p>${note}<p style="font-size:1.6em;letter-spacing:.15em"><strong>${code}</strong></p><p>Worth ${amount} ${currency.toUpperCase()}. Enter it in the promotion code box at checkout${env.SITE_URL ? ` on <a href="${env.SITE_URL}">${env.SITE_URL}</a>` : ''}. It can be used once, so choose a cart worth at least the full amount.</p></div>`;
    for (const [addr, intro] of [[to, `${escapeHtml(session.metadata.gift_from || buyer || 'Someone')} sent you a gift card.`], [buyer, to ? `Your gift card has been sent to ${escapeHtml(to)}. Here is the code for your records.` : 'Here is your gift card code.']]) {
      if (!isEmail(addr)) continue;
      try { await sendEmail(env, addr, to && addr === to ? 'You have received a gift card' : 'Your gift card', body(intro)); record.sentTo.push(addr); } catch { /* shown on the thank-you page anyway */ }
    }
  }
  if (env.STORE) await env.STORE.put(flag, JSON.stringify(record));
  return record;
}

/* ---------- E-mail templates ---------- */
function linksHtml(items, intro) {
  const rows = items.map((item) => item.preorder
    ? `<li style="margin:.5em 0"><strong>${escapeHtml(item.name)}</strong> <span style="color:#666">(pre-order${item.releaseDate ? `, ships ${new Date(item.releaseDate).toDateString()}` : ''}; we will email the download on release)</span></li>`
    : `<li style="margin:.5em 0"><a href="${item.url}">${escapeHtml(item.name)}</a> <span style="color:#666">(link valid until ${new Date(item.expires).toUTCString()})</span></li>`).join('');
  return `<div style="font-family:sans-serif;line-height:1.6"><p>${escapeHtml(intro || DEFAULT_TEMPLATES.downloads.intro).replace(/\n/g, '<br>')}</p><ul>${rows}</ul><p>Save the files somewhere you will find them again. If a link has expired, use "Resend my download" on the store to request a fresh one.</p></div>`;
}
function giftHtml(items, message, from, intro) {
  const note = message ? `<blockquote style="border-left:3px solid #9a3a20;margin:1em 0;padding:.25em 1em;color:#333">${escapeHtml(message)}</blockquote>` : '';
  return `<div style="font-family:sans-serif;line-height:1.6"><p>${escapeHtml(intro || DEFAULT_TEMPLATES.gift.intro)}${from ? ` (from ${escapeHtml(from)})` : ''}</p>${note}${linksHtml(items, 'Your downloads:')}</div>`;
}
