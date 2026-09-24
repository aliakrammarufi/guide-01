/* Customer dashboard (/account/). Passwordless: e-mail plus a one-time code, then a 30-day session kept in
   localStorage. Talks to the Worker's /account/* routes. page.js loads the catalog first and announces it with
   the "marufi:ready" event; this script uses it for covers, titles, and the endpoint. */
(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const SESSION_KEY = 'marufi-session';
  const SAVED_KEY = 'marufi-saved';
  const state = { endpoint: '', currency: 'CAD', name: 'Marufi Digital', guides: [], session: null, account: null, subscribed: false, orders: null, gifts: null, requests: null, view: 'overview' };

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  const isEmail = (v) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(String(v || '').trim());
  const isHttps = (v) => { try { const u = new URL(v); return u.protocol === 'https:' || (u.protocol === 'http:' && u.hostname === 'localhost'); } catch { return false; } };
  const money = (amount, currency) => { try { return new Intl.NumberFormat(undefined, { style: 'currency', currency, minimumFractionDigits: Number.isInteger(amount) ? 0 : 2 }).format(amount); } catch { return `${currency} ${amount}`; } };
  const when = (iso, long) => { try { return new Date(iso).toLocaleDateString(undefined, long ? { year: 'numeric', month: 'long', day: 'numeric' } : { year: 'numeric', month: 'short', day: 'numeric' }); } catch { return iso; } };
  const initials = (name, email) => (name ? name.trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join('') : (email || '?')[0]).toUpperCase();
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
  const busy = (button, on) => { if (!button) return; button.disabled = on; button.classList.toggle('is-busy', on); };
  function readSession() { try { const s = JSON.parse(localStorage.getItem(SESSION_KEY) || 'null'); return s && s.token && s.exp > Date.now() ? s : null; } catch { return null; } }
  function writeSession(s) { try { if (s) localStorage.setItem(SESSION_KEY, JSON.stringify(s)); else localStorage.removeItem(SESSION_KEY); } catch { /* private mode */ } }
  async function api(path, options = {}) {
    const headers = { ...(options.headers || {}) };
    if (state.session) headers.Authorization = `Bearer ${state.session.token}`;
    if (options.json !== undefined) { headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(options.json); }
    const response = await fetch(`${state.endpoint}${path}`, { ...options, headers });
    const data = await response.json().catch(() => ({}));
    if (response.status === 401 && state.session) { signOut(true); throw new Error('Your session has ended. Please sign in again.'); }
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
    return data;
  }
  const guideByPrice = (priceId) => state.guides.find((g) => g.priceId === priceId || (g.variants || []).some((v) => v.priceId === priceId));
  const guideById = (id) => state.guides.find((g) => g.id === id);
  const coverUrl = (g) => (g && g.cover ? (isHttps(g.cover) ? g.cover : `../${g.cover}`) : '');

  /* ---------- Auth panel ---------- */
  let challenge = '';
  function setupAuth() {
    const panel = $('#auth-panel');
    const tabs = $$('.auth-tab', panel);
    const forms = { signup: $('#signup-form'), signin: $('#signin-form') };
    const codeForm = $('#code-form');
    for (const tab of tabs) tab.addEventListener('click', () => {
      for (const t of tabs) t.classList.toggle('is-active', t === tab);
      for (const [name, form] of Object.entries(forms)) form.hidden = name !== tab.dataset.tab;
      codeForm.hidden = true;
    });
    const send = async (form, signup) => {
      const f = form.elements;
      if (signup && !f.name.value.trim()) { toast('Please tell us your name', true); f.name.focus(); return; }
      if (!isEmail(f.email.value)) { toast('Please enter a valid e-mail address', true); f.email.focus(); return; }
      busy(f.send, true);
      try {
        const r = await api('/account/code', { method: 'POST', json: { email: f.email.value.trim(), name: signup ? f.name.value.trim() : '' } });
        challenge = r.challenge;
        forms.signup.hidden = forms.signin.hidden = true;
        codeForm.hidden = false;
        $('#code-note').textContent = `${r.existing ? 'Welcome back. ' : ''}We sent a six-digit code to ${f.email.value.trim()}. It works for ten minutes.`;
        codeForm.elements.code.value = '';
        codeForm.elements.code.focus();
      } catch (error) { toast(error.message, true); }
      busy(f.send, false);
    };
    forms.signup.addEventListener('submit', (e) => { e.preventDefault(); send(forms.signup, true); });
    forms.signin.addEventListener('submit', (e) => { e.preventDefault(); send(forms.signin, false); });
    codeForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const f = codeForm.elements;
      busy(f.verify, true);
      try {
        const r = await api('/account/verify', { method: 'POST', json: { challenge, code: f.code.value.trim() } });
        state.session = { token: r.token, exp: r.exp, email: r.account.email, name: r.account.name };
        writeSession(state.session);
        state.account = r.account;
        toast(r.created ? 'Your account is ready' : `Welcome back${r.account.name ? `, ${r.account.name.split(' ')[0]}` : ''}`);
        await enterDashboard();
      } catch (error) { toast(error.message, true); }
      busy(f.verify, false);
    });
    $('#code-restart').addEventListener('click', () => { codeForm.hidden = true; forms.signin.hidden = false; for (const t of tabs) t.classList.toggle('is-active', t.dataset.tab === 'signin'); challenge = ''; });
  }

  /* ---------- Dashboard ---------- */
  async function enterDashboard() {
    $('#auth-panel').hidden = true;
    const dash = $('#dashboard');
    dash.hidden = false;
    if (!state.account) { const me = await api('/account/me'); state.account = me.account; state.subscribed = me.subscribed; }
    else { try { const me = await api('/account/me'); state.subscribed = me.subscribed; state.account = me.account; } catch { /* keep */ } }
    // Merge the saved titles kept in this browser with the ones on the account, both ways.
    try {
      const local = JSON.parse(localStorage.getItem(SAVED_KEY) || '[]');
      const merged = [...new Set([...(state.account.saved || []), ...(Array.isArray(local) ? local : [])])];
      if (merged.length !== (state.account.saved || []).length) { const r = await api('/account/me', { method: 'PUT', json: { saved: merged } }); state.account = r.account; }
      localStorage.setItem(SAVED_KEY, JSON.stringify(merged));
    } catch { /* ignore */ }
    renderIdentity();
    showView(location.hash.replace('#', '') || 'overview');
    loadOrders().catch(() => {});
  }
  function renderIdentity() {
    const a = state.account;
    $('#dash-avatar').textContent = initials(a.name, a.email);
    $('#dash-name').textContent = a.name || 'Your account';
    $('#dash-email').textContent = a.email;
    $('#dash-greeting').textContent = `${greeting()}${a.name ? `, ${a.name.split(' ')[0]}` : ''}.`;
    $('#dash-since').textContent = a.createdAt ? `Member since ${when(a.createdAt, true)}` : '';
  }
  function greeting() { const h = new Date().getHours(); return h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening'; }
  function showView(view) {
    const known = ['overview', 'downloads', 'orders', 'saved', 'gifts', 'requests', 'profile'];
    state.view = known.includes(view) ? view : 'overview';
    for (const item of $$('.dash-nav button')) item.classList.toggle('is-active', item.dataset.view === state.view);
    for (const section of $$('.dash-view')) section.hidden = section.dataset.view !== state.view;
    if (history.replaceState) history.replaceState(null, '', `#${state.view}`);
    const render = { overview: renderOverview, downloads: renderDownloads, orders: renderOrders, saved: renderSaved, gifts: renderGifts, requests: renderRequests, profile: renderProfile }[state.view];
    render();
    window.scrollTo({ top: Math.min(window.scrollY, $('#dashboard').offsetTop - 20), behavior: 'auto' });
  }
  async function loadOrders() {
    try { const r = await api('/account/orders'); state.orders = r.orders; state.stripe = r.stripe; }
    catch (error) { state.orders = []; state.ordersError = error.message; }
    if (['overview', 'downloads', 'orders'].includes(state.view)) showView(state.view);
  }
  function downloads() {
    const out = [];
    for (const order of state.orders || []) {
      order.items.forEach((item, i) => {
        const line = order.lines[i] || order.lines.find((l) => l.name === item.name) || {};
        out.push({ ...item, priceId: line.priceId, order, guide: guideByPrice(line.priceId) });
      });
    }
    return out;
  }
  function statTile(label, value, note) {
    const tile = el('div', 'stat');
    tile.append(el('span', 'stat-label', label), el('strong', null, String(value)), el('span', 'stat-note', note || ''));
    return tile;
  }
  function emptyState(symbol, title, text, link, linkText) {
    const box = el('div', 'catalog-message dash-empty');
    const sym = el('div', 'message-symbol', symbol); sym.setAttribute('aria-hidden', 'true');
    const copy = el('div', 'message-copy');
    copy.append(el('h3', null, title), el('p', null, text));
    if (link) { const a = el('a', 'button button-sm', linkText); a.href = link; copy.append(a); }
    box.append(sym, copy);
    return box;
  }
  function renderOverview() {
    const box = $('[data-view="overview"] .dash-body');
    box.replaceChildren();
    const stats = el('div', 'stats');
    const d = downloads();
    const ready = d.filter((x) => x.url).length;
    const pending = d.filter((x) => x.preorder).length;
    const saved = (state.account.saved || []).length;
    stats.append(
      statTile('Purchases', state.orders ? state.orders.filter((o) => !o.giftCard).length : '…', state.orders ? 'orders placed' : 'loading'),
      statTile('Downloads', state.orders ? ready : '…', pending ? `${pending} pre-ordered` : 'ready now'),
      statTile('Saved', saved, saved === 1 ? 'title for later' : 'titles for later'),
      statTile('Newsletter', state.subscribed ? 'On' : 'Off', state.subscribed ? 'new titles and free help' : 'turn on in Profile')
    );
    box.append(stats);
    if (state.orders && d.length) {
      const head = el('div', 'dash-subhead'); head.append(el('h3', null, 'Your latest downloads'), linkButton('All downloads', 'downloads'));
      box.append(head, downloadList(d.slice(0, 3)));
    } else if (state.orders) {
      box.append(emptyState('Start', 'No purchases yet.', 'Titles you buy with this e-mail appear here with their download links.', '../#needs', 'Browse by need'));
    }
    if (saved) {
      const head = el('div', 'dash-subhead'); head.append(el('h3', null, 'Saved for later'), linkButton('All saved', 'saved'));
      box.append(head, savedGrid((state.account.saved || []).slice(0, 3)));
    }
  }
  function linkButton(text, view) { const b = el('button', 'text-link', `${text} →`); b.type = 'button'; b.addEventListener('click', () => showView(view)); return b; }
  function downloadList(items) {
    const list = el('div', 'download-list');
    for (const item of items) {
      const row = el('article', 'download');
      const cover = el('div', 'download-cover');
      if (coverUrl(item.guide)) { const img = document.createElement('img'); img.src = coverUrl(item.guide); img.alt = ''; img.loading = 'lazy'; cover.append(img); }
      else { const art = el('div', 'cover-art'); art.dataset.tone = 'pine'; art.append(el('strong', null, (item.name || '?')[0])); cover.append(art); }
      const body = el('div', 'download-body');
      const place = item.guide ? [item.guide.country, item.guide.state].filter(Boolean).join(' / ') : '';
      body.append(el('span', 'card-kicker', [place, `Bought ${when(item.order.created)}`].filter(Boolean).join(' · ')), el('h4', null, item.name));
      if (item.guide && item.guide.format) body.append(el('p', null, item.guide.format));
      const actions = el('div', 'download-actions');
      if (item.url) { const a = el('a', 'button button-sm', 'Download'); a.href = item.url; a.append(el('span', null, ' ↓')); actions.append(a, el('span', 'hint', `Link valid until ${when(item.expires, true)}. Refresh this page for a new one.`)); }
      else if (item.preorder) actions.append(el('span', 'pill-soft', 'Pre-order'), el('span', 'hint', item.releaseDate ? `We e-mail the download on ${when(item.releaseDate, true)}.` : 'We e-mail the download on release.'));
      else actions.append(el('span', 'hint', 'No file attached yet. Contact us and we will sort it out.'));
      body.append(actions);
      row.append(cover, body);
      list.append(row);
    }
    return list;
  }
  function renderDownloads() {
    const box = $('[data-view="downloads"] .dash-body');
    box.replaceChildren();
    if (!state.orders) { box.append(el('p', 'hint', 'Loading your downloads…')); return; }
    if (state.stripe === false) { box.append(emptyState('Soon', 'Downloads appear once the store is connected to Stripe.', 'Your Stripe receipt has your links in the meantime.')); return; }
    const d = downloads();
    if (!d.length) { box.append(emptyState('Start', 'Nothing to download yet.', 'Every title you buy with this e-mail lands here, with a fresh link each time you visit.', '../#guides', 'Open the library')); return; }
    box.append(downloadList(d));
  }
  function renderOrders() {
    const box = $('[data-view="orders"] .dash-body');
    box.replaceChildren();
    if (!state.orders) { box.append(el('p', 'hint', 'Loading your orders…')); return; }
    if (!state.orders.length) { box.append(emptyState('Start', 'No orders yet.', state.ordersError || 'Orders paid with this e-mail address appear here.', '../#needs', 'Browse by need')); return; }
    const table = el('table', 'orders-table');
    const head = el('tr'); for (const h of ['Date', 'Items', 'Total', 'Receipt']) head.append(el('th', null, h)); table.append(head);
    for (const o of state.orders) {
      const tr = el('tr');
      const items = el('td'); items.append(...o.lines.map((l) => el('div', null, l.name)));
      const ref = el('td', 'mono', o.id.slice(-8).toUpperCase());
      tr.append(el('td', null, when(o.created)), items, el('td', 'money', money(o.total, o.currency)), ref);
      table.append(tr);
    }
    box.append(table, el('p', 'hint', 'Receipts and invoices come from Stripe by e-mail. Quote the receipt code above when you contact us.'));
  }
  function savedGrid(ids) {
    const grid = el('div', 'saved-grid');
    for (const id of ids) {
      const g = guideById(id);
      const card = el('a', 'saved-card');
      card.href = g ? `../?open=${encodeURIComponent(g.id)}` : '../#guides';
      const cover = el('div', 'saved-cover');
      if (coverUrl(g)) { const img = document.createElement('img'); img.src = coverUrl(g); img.alt = ''; img.loading = 'lazy'; cover.append(img); }
      else { const art = el('div', 'cover-art'); art.dataset.tone = 'clay'; art.append(el('span', null, g ? g.country || '' : ''), el('strong', null, g ? g.state || g.title : 'Unavailable')); cover.append(art); }
      const body = el('div', 'saved-body');
      body.append(el('span', 'card-kicker', g ? [g.country, g.state].filter(Boolean).join(' / ') || 'Title' : 'No longer listed'), el('h4', null, g ? g.title : id), el('span', 'text-link', g ? 'Open →' : 'Browse →'));
      const remove = el('button', 'saved-remove', 'Remove'); remove.type = 'button'; remove.setAttribute('aria-label', `Remove ${g ? g.title : id} from saved`);
      remove.addEventListener('click', async (e) => {
        e.preventDefault();
        const next = (state.account.saved || []).filter((x) => x !== id);
        try { const r = await api('/account/me', { method: 'PUT', json: { saved: next } }); state.account = r.account; try { localStorage.setItem(SAVED_KEY, JSON.stringify(next)); } catch { /* ignore */ } showView(state.view); }
        catch (error) { toast(error.message, true); }
      });
      card.append(cover, body, remove);
      grid.append(card);
    }
    return grid;
  }
  function renderSaved() {
    const box = $('[data-view="saved"] .dash-body');
    box.replaceChildren();
    const ids = state.account.saved || [];
    if (!ids.length) { box.append(emptyState('Heart', 'Nothing saved yet.', 'Tap the heart on any title in the library and it will wait for you here, on every device.', '../#guides', 'Open the library')); return; }
    box.append(savedGrid(ids));
  }
  async function renderGifts() {
    const box = $('[data-view="gifts"] .dash-body');
    box.replaceChildren(el('p', 'hint', 'Loading…'));
    try { const r = await api('/account/gifts'); state.gifts = r.gifts; } catch (error) { state.gifts = []; }
    box.replaceChildren();
    if (!state.gifts.length) { box.append(emptyState('Gift', 'No gift cards yet.', 'Cards you buy or receive with this e-mail appear here with their codes.', '../gift/', 'Give a gift card')); return; }
    for (const g of state.gifts) {
      const card = el('article', 'gift-card');
      const top = el('div', 'gift-top');
      top.append(el('span', 'card-kicker', `${g.received ? `From ${g.from || g.buyer || 'a friend'}` : `Sent to ${g.to || 'you'}`} · ${when(g.at)}`), el('span', `pill-soft${g.redeemed ? ' is-used' : ''}`, g.redeemed ? 'Redeemed' : g.active === false ? 'Inactive' : 'Ready to use'));
      const code = el('div', 'gift-code-row');
      code.append(el('span', 'code', g.code), el('strong', null, money(g.amount, g.currency)));
      const copy = el('button', 'button-ghost', 'Copy code'); copy.type = 'button';
      copy.addEventListener('click', async () => { try { await navigator.clipboard.writeText(g.code); toast('Code copied'); } catch { toast('Select the code and copy it', true); } });
      card.append(top, code, copy, el('p', 'hint', 'Enter it in the promotion-code box at checkout. Single use.'));
      box.append(card);
    }
  }
  async function renderRequests() {
    const box = $('[data-view="requests"] .dash-body');
    box.replaceChildren(el('p', 'hint', 'Loading…'));
    try { const r = await api('/account/requests'); state.requests = r.requests; } catch { state.requests = []; }
    box.replaceChildren();
    if (!state.requests.length) { box.append(emptyState('Ask', 'No place requests yet.', 'Ask for a place or a topic in the atlas and we will write to you when it is covered.', '../#atlas', 'Ask for a place')); return; }
    const list = el('ul', 'request-list');
    for (const r of state.requests) { const li = el('li'); li.append(el('strong', null, r.place), el('span', null, `Asked ${when(r.at)} · we will e-mail you when it is ready`)); list.append(li); }
    box.append(list);
  }
  function renderProfile() {
    const box = $('[data-view="profile"] .dash-body');
    box.replaceChildren();
    const a = state.account;
    const form = el('form', 'form-card profile-form');
    form.noValidate = true;
    const grid = el('div', 'grid-2');
    grid.append(fieldNode('Name', 'name', a.name, 'text', 'name'), fieldNode('Country you live in (optional)', 'country', a.country, 'text', 'country-name'));
    form.append(grid, fieldNode('E-mail', 'email', a.email, 'email', 'email', true));
    const news = el('label', 'check');
    const box2 = document.createElement('input'); box2.type = 'checkbox'; box2.checked = state.subscribed;
    news.append(box2, document.createTextNode(' Send me one e-mail when there are new titles or free help'));
    form.append(news);
    const actions = el('div', 'form-actions');
    const save = el('button', 'button', 'Save changes'); save.type = 'submit';
    actions.append(save, el('span', 'hint', 'Your e-mail is how we recognise your purchases, so it cannot be changed here.'));
    form.append(actions);
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      busy(save, true);
      try {
        const r = await api('/account/me', { method: 'PUT', json: { name: form.elements.name.value, country: form.elements.country.value } });
        state.account = r.account;
        if (box2.checked !== state.subscribed) { const n = await api('/account/newsletter', { method: 'PUT', json: { subscribed: box2.checked } }); state.subscribed = n.subscribed; }
        state.session.name = state.account.name; writeSession(state.session);
        renderIdentity();
        toast('Saved');
      } catch (error) { toast(error.message, true); }
      busy(save, false);
    });
    box.append(form);
    const danger = el('div', 'danger-zone');
    danger.append(el('h3', null, 'Sign out or close your account'));
    danger.append(el('p', 'hint', 'Closing your account removes your profile, saved titles, newsletter subscription, and place requests. Your orders stay with Stripe so you can still get downloads with "Resend my download".'));
    const row = el('div', 'form-actions');
    const out = el('button', 'button-ghost', 'Sign out'); out.type = 'button'; out.addEventListener('click', () => signOut());
    const del = el('button', 'button-ghost danger', 'Close my account'); del.type = 'button';
    del.addEventListener('click', async () => {
      if (!del.dataset.armed) { del.dataset.armed = '1'; del.textContent = 'Click again to confirm'; setTimeout(() => { delete del.dataset.armed; del.textContent = 'Close my account'; }, 5000); return; }
      try { await api('/account/me', { method: 'DELETE' }); toast('Your account has been closed'); signOut(); } catch (error) { toast(error.message, true); }
    });
    row.append(out, del);
    danger.append(row);
    box.append(danger);
  }
  function fieldNode(label, name, value, type, autocomplete, readonly) {
    const wrap = el('label', 'field');
    const input = document.createElement('input');
    input.type = type; input.name = name; input.value = value || ''; input.autocomplete = autocomplete || 'off';
    if (readonly) input.readOnly = true;
    wrap.append(el('span', null, label), input);
    return wrap;
  }
  function signOut(silent) {
    state.session = null; state.account = null; state.orders = null; state.gifts = null; state.requests = null;
    writeSession(null);
    $('#dashboard').hidden = true;
    $('#auth-panel').hidden = false;
    if (history.replaceState) history.replaceState(null, '', location.pathname);
    if (!silent) toast('Signed out');
  }

  function init(store) {
    state.endpoint = store.endpoint;
    state.currency = store.currency;
    state.name = store.name;
    state.guides = store.guides;
    const offline = $('#account-offline');
    if (!state.endpoint) { $('#auth-panel').hidden = true; if (offline) offline.hidden = false; return; }
    setupAuth();
    for (const item of $$('.dash-nav button')) item.addEventListener('click', () => showView(item.dataset.view));
    $('#dash-signout').addEventListener('click', () => signOut());
    state.session = readSession();
    if (state.session) enterDashboard().catch(() => signOut(true));
  }
  if (window.marufiStore) init(window.marufiStore);
  else window.addEventListener('marufi:ready', (e) => init(e.detail), { once: true });
})();
