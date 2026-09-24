/**
 * Local development server for the Worker: runs worker.js in Node with in-memory KV and R2 stubs.
 * Usage: node checkout/dev-server.mjs   (then set checkoutEndpoint to http://localhost:8787 in a local copy of guides.json)
 * Stripe and e-mail calls need real keys; everything else (login, catalog, uploads, media) works offline.
 */
import http from 'node:http';
import { Readable } from 'node:stream';
import worker from './worker.js';

const memoryKV = new Map();
const KV = {
  async get(key) { const v = memoryKV.get(key); if (!v) return null; if (v.exp && Date.now() > v.exp) { memoryKV.delete(key); return null; } return v.value; },
  async put(key, value, opts = {}) { memoryKV.set(key, { value, exp: opts.expirationTtl ? Date.now() + opts.expirationTtl * 1000 : 0 }); },
  async delete(key) { memoryKV.delete(key); },
  async list({ prefix = '', limit = 1000 } = {}) { return { keys: [...memoryKV.keys()].filter((k) => k.startsWith(prefix)).slice(0, limit).map((name) => ({ name })) }; }
};
const memoryR2 = new Map();
const R2 = {
  async put(key, body, opts = {}) {
    const chunks = [];
    if (body && typeof body.getReader === 'function') { const reader = body.getReader(); for (;;) { const { done, value } = await reader.read(); if (done) break; chunks.push(value); } }
    else if (body) chunks.push(new Uint8Array(await new Response(body).arrayBuffer()));
    const bytes = Buffer.concat(chunks.map((c) => Buffer.from(c)));
    memoryR2.set(key, { bytes, contentType: opts.httpMetadata && opts.httpMetadata.contentType, uploaded: new Date() });
  },
  async get(key) { const o = memoryR2.get(key); if (!o) return null; return { body: o.bytes, httpMetadata: { contentType: o.contentType } }; },
  async delete(key) { memoryR2.delete(key); },
  async list({ prefix = '' } = {}) { return { objects: [...memoryR2.entries()].filter(([k]) => k.startsWith(prefix)).map(([key, o]) => ({ key, size: o.bytes.length, uploaded: o.uploaded })) }; }
};

const env = {
  ADMIN_PASSWORD: process.env.ADMIN_PASSWORD || 'letmein',
  DOWNLOAD_SECRET: process.env.DOWNLOAD_SECRET || 'dev-secret-change-me',
  STRIPE_SECRET_KEY: process.env.STRIPE_SECRET_KEY || '',
  RESEND_API_KEY: process.env.RESEND_API_KEY || '',
  FROM_EMAIL: process.env.FROM_EMAIL || '',
  SUCCESS_URL: 'http://localhost:8766/thank-you.html?session_id={CHECKOUT_SESSION_ID}',
  CANCEL_URL: 'http://localhost:8766/#guides',
  ALLOWED_ORIGINS: '',
  SITE_URL: 'http://localhost:8766',
  DEV_MODE: '1', // prints one-time codes to the console instead of e-mailing them
  FILES: R2,
  STORE: KV
};

const port = Number(process.env.PORT) || 8787;
http.createServer(async (req, res) => {
  const url = `http://localhost:${port}${req.url}`;
  const headers = new Headers();
  for (const [k, v] of Object.entries(req.headers)) if (typeof v === 'string') headers.set(k, v);
  const hasBody = !['GET', 'HEAD', 'OPTIONS'].includes(req.method);
  const request = new Request(url, { method: req.method, headers, body: hasBody ? Readable.toWeb(req) : undefined, duplex: hasBody ? 'half' : undefined });
  try {
    const response = await worker.fetch(request, env, {});
    res.writeHead(response.status, Object.fromEntries(response.headers.entries()));
    if (response.body) {
      const buffer = Buffer.from(await response.arrayBuffer());
      res.end(buffer);
    } else res.end();
  } catch (error) {
    res.writeHead(500, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: error.message }));
  }
}).listen(port, () => console.log(`Worker dev server on http://localhost:${port} (admin password: ${env.ADMIN_PASSWORD})`));
