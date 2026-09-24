/* Shared script for the secondary pages: privacy, terms, contact, purchase history, gift cards, articles, 404.
   Reads the same catalog as the home page (guides.json, or the Worker when connected) for the store name,
   contact e-mail, and the endpoint used by the forms. Which page is running is set by <body data-page="...">. */
(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const page = document.body.dataset.page || '';
  // Path back to the site root, taken from this script's own src (../ on most pages, ../../ on article pages).
  const BASE = ((document.querySelector('script[src$="page.js"]') || {}).getAttribute || (() => ''))?.call(document.querySelector('script[src$="page.js"]'), 'src')?.replace(/page\.js$/, '') || '../';
  const store = { name: 'Marufi Digital', endpoint: '', status: {}, contactEmail: '', currency: 'CAD', giftCards: { enabled: true, amounts: [25, 50, 100] }, articles: [] };
  const isHttps = (v) => { try { const u = new URL(v); return u.protocol === 'https:' || (u.protocol === 'http:' && u.hostname === 'localhost'); } catch { return false; } };
  const isEmail = (v) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(String(v || '').trim());
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function formatMoney(amount, currency) {
    try { return new Intl.NumberFormat(undefined, { style: 'currency', currency, minimumFractionDigits: Number.isInteger(amount) ? 0 : 2 }).format(amount); }
    catch { return `${currency} ${amount}`; }
  }
  function formatDate(iso) {
    // Date-only strings are read as local noon so the day never shifts with the time zone.
    try { return new Date(/^\d{4}-\d{2}-\d{2}$/.test(iso) ? `${iso}T12:00:00` : iso).toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' }); } catch { return iso; }
  }
  let toastTimer = 0;
  function toast(text, error) {
    let node = $('#page-toast');
    if (!node) { node = el('div', 'toast'); node.id = 'page-toast'; node.setAttribute('role', 'status'); document.body.append(node); }
    node.textContent = text;
    node.classList.toggle('is-error', Boolean(error));
    node.classList.add('is-visible');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => node.classList.remove('is-visible'), 3200);
  }
  async function post(path, body) {
    if (!store.endpoint) throw new Error('offline');
    const response = await fetch(`${store.endpoint}${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
    return data;
  }
  const busy = (button, on) => { if (!button) return; button.disabled = on; button.classList.toggle('is-busy', on); };

  /* ---------- Shared chrome ---------- */
  function fillChrome() {
    for (const node of $$('[data-store-name]')) node.textContent = store.name;
    for (const node of $$('[data-contact-email]')) {
      if (store.contactEmail) { node.textContent = store.contactEmail; if (node.tagName === 'A') node.href = `mailto:${store.contactEmail}`; node.hidden = false; }
      else node.hidden = true;
    }
    for (const node of $$('[data-needs-email]')) node.hidden = !store.contactEmail;
    const count = $('#kind-cart-count');
    if (count) { try { const cart = JSON.parse(localStorage.getItem('marufi-cart') || '[]'); count.textContent = String(cart.length); count.hidden = !cart.length; } catch { /* ignore */ } }
    for (const node of $$('[data-year]')) node.textContent = String(new Date().getFullYear());
  }

  /* ---------- Newsletter forms (footer of every page) ---------- */
  function setupSubscribe() {
    for (const form of $$('.subscribe-form')) {
      const note = form.querySelector('.subscribe-note');
      if (!store.endpoint) { form.hidden = true; continue; }
      form.hidden = false;
      form.addEventListener('submit', async (event) => {
        event.preventDefault();
        const input = form.querySelector('input[type="email"]');
        const button = form.querySelector('button');
        if (!isEmail(input.value)) { toast('Please enter a valid e-mail address', true); input.focus(); return; }
        busy(button, true);
        try {
          const r = await post('/subscribe', { email: input.value.trim(), source: page || 'home' });
          form.reset();
          if (note) note.textContent = r.doubleOptIn ? 'Almost there: check your inbox and confirm.' : 'You are on the list.';
          toast(r.doubleOptIn ? 'Check your inbox to confirm' : 'You are on the list');
        } catch (error) { toast(error.message === 'offline' ? 'The newsletter is not connected yet' : error.message, true); }
        busy(button, false);
      });
    }
  }

  /* ---------- Contact ---------- */
  function setupContact() {
    const form = $('#contact-form');
    if (!form) return;
    const done = $('#contact-done');
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const f = form.elements;
      if (!isEmail(f.email.value)) { toast('Please enter a valid e-mail address', true); f.email.focus(); return; }
      if (!f.message.value.trim()) { toast('Please write a message', true); f.message.focus(); return; }
      if (f.website && f.website.value) return; // honeypot
      busy(f.send, true);
      try {
        if (store.endpoint) {
          await post('/contact', { name: f.name.value, email: f.email.value, topic: f.topic.value, message: f.message.value });
        } else if (store.contactEmail) {
          location.href = `mailto:${store.contactEmail}?subject=${encodeURIComponent(`${f.topic.value || 'Message'} from ${f.name.value || f.email.value}`)}&body=${encodeURIComponent(`${f.message.value}\n\n${f.name.value}\n${f.email.value}`)}`;
        } else throw new Error('The contact form is not connected yet. Please try again later.');
        form.hidden = true;
        if (done) done.hidden = false;
      } catch (error) { toast(error.message, true); }
      busy(f.send, false);
    });
  }

  /* ---------- Gift cards ---------- */
  function setupGift() {
    const form = $('#gift-form');
    if (!form) return;
    const offline = $('#gift-offline');
    const amounts = (Array.isArray(store.giftCards.amounts) && store.giftCards.amounts.length ? store.giftCards.amounts : [25, 50, 100]).map(Number).filter((n) => n > 0);
    const box = $('#gift-amounts');
    box.replaceChildren(...amounts.map((amount, i) => {
      const label = el('label', 'gift-amount');
      const input = document.createElement('input');
      input.type = 'radio'; input.name = 'amount'; input.value = String(amount); input.checked = i === 1 || amounts.length === 1;
      label.append(input, el('span', null, formatMoney(amount, store.currency)));
      return label;
    }));
    if (!store.endpoint || store.status.stripe === false || store.giftCards.enabled === false) { form.hidden = true; if (offline) offline.hidden = false; return; }
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const f = form.elements;
      const amount = Number((form.querySelector('input[name="amount"]:checked') || {}).value);
      if (f.to.value && !isEmail(f.to.value)) { toast("Please check the recipient's e-mail address", true); f.to.focus(); return; }
      busy(f.pay, true);
      try {
        const r = await post('/gift-card/session', { amount, to: f.to.value.trim(), from: f.from.value.trim(), message: f.message.value.trim() });
        location.href = r.url;
      } catch (error) { toast(error.message, true); busy(f.pay, false); }
    });
  }

  /* ---------- Articles ---------- */
  function slugify(t) { return String(t || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, ''); }
  function inline(text) {
    // Escapes HTML, then allows **bold**, *italic*, and [text](https://link).
    const safe = String(text).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
    return safe
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+|\.\.\/[^\s)]+|#[^\s)]*)\)/g, '<a href="$2">$1</a>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/\*([^*]+)\*/g, '<em>$1</em>');
  }
  function renderBody(text) {
    // A small, safe subset of Markdown: ## headings, - lists, > quotes, blank-line paragraphs.
    const out = [];
    let list = null;
    const flush = () => { if (list) { out.push(`<ul>${list.join('')}</ul>`); list = null; } };
    for (const block of String(text || '').replace(/\r/g, '').split(/\n{2,}/)) {
      const lines = block.split('\n').filter((l) => l.trim());
      if (!lines.length) continue;
      if (lines.every((l) => /^\s*-\s+/.test(l))) { out.push(`<ul>${lines.map((l) => `<li>${inline(l.replace(/^\s*-\s+/, ''))}</li>`).join('')}</ul>`); continue; }
      flush();
      const first = lines[0];
      if (/^###\s+/.test(first)) out.push(`<h3>${inline(first.replace(/^###\s+/, ''))}</h3>`);
      else if (/^##\s+/.test(first)) out.push(`<h2>${inline(first.replace(/^##\s+/, ''))}</h2>`);
      else if (/^>\s?/.test(first)) out.push(`<blockquote>${lines.map((l) => inline(l.replace(/^>\s?/, ''))).join('<br>')}</blockquote>`);
      else out.push(`<p>${lines.map(inline).join('<br>')}</p>`);
    }
    flush();
    return out.join('');
  }
  function publishedArticles() {
    return store.articles
      .filter((a) => a && typeof a.title === 'string' && a.title.trim() && a.status !== 'draft' && !(a.date && Date.parse(a.date) > Date.now()))
      .map((a) => ({ ...a, slug: a.slug || slugify(a.title) }))
      .sort((a, b) => String(b.date || '').localeCompare(String(a.date || '')));
  }
  function setupArticles() {
    const list = $('#articles-list');
    const view = $('#article-view');
    if (!list || !view) return;
    const articles = publishedArticles();
    const wanted = new URLSearchParams(location.search).get('a') || document.body.dataset.article || '';
    const article = wanted ? articles.find((a) => a.slug === wanted) : null;
    const empty = $('#articles-empty');
    if (article) {
      $('#articles-index').hidden = true;
      view.hidden = false;
      document.title = `${article.title} · ${store.name}`;
      const meta = $('meta[name="description"]'); if (meta && article.summary) meta.content = article.summary;
      $('#article-kicker').textContent = [article.category, article.place, article.date ? formatDate(article.date) : ''].filter(Boolean).join(' · ');
      $('#article-title').textContent = article.title;
      $('#article-summary').textContent = article.summary || '';
      const cover = $('#article-cover');
      if (article.cover) { cover.src = isHttps(article.cover) ? article.cover : `${BASE}${article.cover}`; cover.alt = article.coverAlt || ''; cover.hidden = false; }
      $('#article-body').innerHTML = renderBody(article.body);
      const more = $('#article-more');
      const others = articles.filter((a) => a.slug !== article.slug).slice(0, 3);
      more.hidden = !others.length;
      $('#article-more-list').replaceChildren(...others.map(card));
      return;
    }
    if (!articles.length) { if (empty) empty.hidden = false; return; }
    list.replaceChildren(...articles.map(card));
    function card(a) {
      const item = el('a', 'article-card');
      item.href = `${BASE}articles/?a=${encodeURIComponent(a.slug)}`;
      if (a.cover) { const img = document.createElement('img'); img.src = isHttps(a.cover) ? a.cover : `${BASE}${a.cover}`; img.alt = a.coverAlt || ''; img.loading = 'lazy'; item.append(img); }
      const body = el('div', 'article-card-body');
      body.append(el('span', 'card-kicker', [a.category, a.place, a.date ? formatDate(a.date) : ''].filter(Boolean).join(' · ')), el('h2', null, a.title), el('p', null, a.summary || ''), el('span', 'text-link', 'Read →'));
      item.append(body);
      return item;
    }
  }

  async function load() {
    let data = {};
    try { data = await fetch(`${BASE}guides.json`, { cache: 'no-cache' }).then((r) => (r.ok ? r.json() : {})); } catch { data = {}; }
    if (isHttps(data.checkoutEndpoint)) {
      store.endpoint = data.checkoutEndpoint.replace(/\/session\/?$/, '').replace(/\/+$/, '');
      try {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 4000);
        const live = await fetch(`${store.endpoint}/catalog`, { signal: controller.signal }).then((r) => (r.ok ? r.json() : null));
        clearTimeout(timer);
        if (live && typeof live === 'object') data = { ...data, ...live };
      } catch { /* keep guides.json */ }
      try { store.status = await fetch(`${store.endpoint}/status`).then((r) => (r.ok ? r.json() : {})); } catch { store.status = {}; }
    }
    if (typeof data.storeName === 'string' && data.storeName.trim()) store.name = data.storeName.trim();
    if (isEmail(data.contactEmail)) store.contactEmail = data.contactEmail.trim();
    if (typeof data.currency === 'string' && /^[A-Z]{3}$/.test(data.currency)) store.currency = data.currency;
    if (data.giftCards && typeof data.giftCards === 'object') store.giftCards = { ...store.giftCards, ...data.giftCards };
    store.articles = Array.isArray(data.articles) ? data.articles : [];
    fillChrome();
    setupSubscribe();
    // Hand the catalog to page-specific scripts (account.js) that load after this one.
    window.marufiStore = { endpoint: store.endpoint, status: store.status, currency: store.currency, name: store.name, contactEmail: store.contactEmail, guides: (Array.isArray(data.guides) ? data.guides : []).filter((g) => g && g.title && g.status !== 'draft').map((g) => ({ id: String(g.id || g.priceId || ''), title: g.title, country: g.country || '', state: g.state || '', cover: g.cover || '', format: g.format || '', priceId: g.priceId || '', variants: Array.isArray(g.variants) ? g.variants.map((v) => ({ priceId: v.priceId || '', label: v.label })) : [] })) };
    window.dispatchEvent(new CustomEvent('marufi:ready', { detail: window.marufiStore }));
    if (page === 'contact') setupContact();
    if (page === 'gift') setupGift();
    if (page === 'articles') setupArticles();
  }
  load();
})();
