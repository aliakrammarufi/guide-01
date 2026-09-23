/**
 * Marufi Digital — Stripe Checkout session endpoint.
 *
 * A dependency-free Cloudflare Worker (also runs unchanged on any platform that
 * supports the standard fetch handler signature). The storefront POSTs the Stripe
 * Price IDs in the visitor's cart; this creates one Stripe Checkout Session for
 * all of them and returns the hosted checkout URL.
 *
 * Environment variables (set as Worker secrets / vars):
 *   STRIPE_SECRET_KEY   sk_live_... or sk_test_...   (secret)
 *   SUCCESS_URL         https://your-domain.com/thank-you.html
 *   CANCEL_URL          https://your-domain.com/#guides
 *   ALLOWED_ORIGINS     https://your-domain.com,https://www.your-domain.com
 *   ALLOWED_PRICES      optional comma-separated list of price_... IDs that may be sold
 */
export default {
  async fetch(request, env) {
    const origin = request.headers.get('Origin') || '';
    const allowedOrigins = String(env.ALLOWED_ORIGINS || '').split(',').map((s) => s.trim()).filter(Boolean);
    const cors = {
      'Access-Control-Allow-Origin': allowedOrigins.includes(origin) ? origin : (allowedOrigins[0] || ''),
      'Access-Control-Allow-Methods': 'POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
      'Vary': 'Origin'
    };
    const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', ...cors } });

    if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors });
    if (request.method !== 'POST') return json({ error: 'Method not allowed' }, 405);
    if (allowedOrigins.length && !allowedOrigins.includes(origin)) return json({ error: 'Origin not allowed' }, 403);
    if (!env.STRIPE_SECRET_KEY || !env.SUCCESS_URL || !env.CANCEL_URL) return json({ error: 'Checkout is not configured' }, 500);

    let body;
    try {
      body = await request.json();
    } catch {
      return json({ error: 'Invalid request body' }, 400);
    }

    const allowedPrices = String(env.ALLOWED_PRICES || '').split(',').map((s) => s.trim()).filter(Boolean);
    const items = [...new Set(Array.isArray(body.items) ? body.items : [])]
      .filter((id) => typeof id === 'string' && /^price_[A-Za-z0-9]+$/.test(id))
      .filter((id) => !allowedPrices.length || allowedPrices.includes(id))
      .slice(0, 20);
    if (!items.length) return json({ error: 'No purchasable items in the request' }, 400);

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

    const response = await fetch('https://api.stripe.com/v1/checkout/sessions', {
      method: 'POST',
      headers: { Authorization: `Bearer ${env.STRIPE_SECRET_KEY}`, 'Content-Type': 'application/x-www-form-urlencoded' },
      body: params
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.url) return json({ error: (data.error && data.error.message) || 'Stripe could not create the session' }, 502);
    return json({ url: data.url });
  }
};
