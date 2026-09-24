/* Marufi Digital storefront — catalog, atlas, zones, cart, Stripe checkout, and interface behaviour. */
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const list = $('#guide-list');
const featuredBox = $('#featured');
const message = $('#catalog-message');
const controls = $('#catalog-controls');
const search = $('#guide-search');
const countryFilter = $('#country-filter');
const typeFilter = $('#type-filter');
const guideCount = $('#guide-count');
const CART_KEY = 'marufi-cart';
const SAVED_KEY = 'marufi-saved';
const CURRENCY_KEY = 'marufi-currency';
const TONES = ['clay', 'pine', 'ink', 'gold'];
const store = { name: 'Marufi Digital', currency: 'CAD', currencies: [], endpoint: '', regions: {}, siteUrl: '', discount: null, countryPages: false, categories: [] };

/* ---------- Kinds of help ----------
   Every title belongs to one category. The defaults below cover what the store sells today; guides.json may
   add or rename categories through a `categories` array of { key, name, single, format, blurb, icon }. */
const DEFAULT_CATEGORIES = [
  { key: 'Guide', slug: 'travel-guides', name: 'Travel guides', single: 'Travel guide', format: 'Digital guide', icon: 'compass', blurb: 'One place, read in one sitting: where to stay, what to skip, and how to move around.' },
  { key: 'Book', slug: 'books', name: 'Books', single: 'Book', format: 'Digital book', icon: 'book', blurb: 'Longer reads on a country or a region, for the flight over or the year after.' },
  { key: 'Immigration', slug: 'immigration', name: 'Immigration and settling in', single: 'Immigration help', format: 'Digital help guide', icon: 'passport', blurb: 'Permits, paperwork, housing, banking, and the first weeks, in the order you will meet them.' },
  { key: 'Food', slug: 'food', name: 'Restaurant and food lists', single: 'Restaurant list', format: 'Digital list', icon: 'fork', blurb: 'Short, curated lists of where to eat, what to order, and when to go.' },
  { key: 'Checklist', slug: 'checklists', name: 'Checklists and templates', single: 'Checklist', format: 'Digital checklist', icon: 'check', blurb: 'Packing lists, moving timelines, budget sheets, and the templates that save a week.' },
  { key: 'Bundle', slug: 'bundles', name: 'Bundles', single: 'Bundle', format: '', icon: 'layers', blurb: 'Several titles for one place, priced together.' }
];
const CATEGORY_ICONS = {
  compass: '<circle cx="12" cy="12" r="9.5"/><path d="M15.5 8.5 13.6 13.6 8.5 15.5l1.9-5.1z"/>',
  book: '<path d="M4 5.5A1.5 1.5 0 0 1 5.5 4H11a2 2 0 0 1 2 2v13.5a1.5 1.5 0 0 0-1.5-1.5H4z"/><path d="M20 5.5A1.5 1.5 0 0 0 18.5 4H13a2 2 0 0 0-2 2v13.5a1.5 1.5 0 0 1 1.5-1.5H20z"/>',
  passport: '<rect x="5" y="3" width="14" height="18" rx="2"/><circle cx="12" cy="10" r="3"/><path d="M8.5 17h7"/>',
  fork: '<path d="M7 3v7a2.5 2.5 0 0 0 5 0V3M9.5 3v18"/><path d="M17 3c-2 1.5-2.5 4-2.5 7.5H17V21"/>',
  check: '<rect x="4" y="3.5" width="16" height="17" rx="2"/><path d="m8 12 2.5 2.5L16 9"/>',
  layers: '<path d="m12 4 8 4.5-8 4.5-8-4.5z"/><path d="m4 13 8 4.5 8-4.5"/><path d="m4 16.5 8 4.5 8-4.5"/>'
};
function setCategories(list) {
  const custom = Array.isArray(list) ? list.filter((c) => c && typeof c.key === 'string' && /^[A-Za-z][\w-]{0,30}$/.test(c.key) && typeof c.name === 'string' && c.name.trim()) : [];
  const merged = DEFAULT_CATEGORIES.map((d) => ({ ...d, ...(custom.find((c) => c.key.toLowerCase() === d.key.toLowerCase()) || {}), key: d.key, slug: d.slug }));
  for (const c of custom) if (!merged.some((m) => m.key.toLowerCase() === c.key.toLowerCase())) merged.push({ single: c.name.replace(/s$/, ''), format: 'Digital download', icon: 'layers', blurb: '', slug: slugify(c.name), ...c });
  store.categories = merged.map((c) => ({ ...c, name: String(c.name).trim(), single: String(c.single || c.name).trim(), format: String(c.format || ''), blurb: String(c.blurb || ''), icon: CATEGORY_ICONS[c.icon] ? c.icon : 'layers', hidden: c.hidden === true }));
}
setCategories([]);
function categoryOf(type) {
  const key = String(type || '').toLowerCase();
  return store.categories.find((c) => c.key.toLowerCase() === key || c.name.toLowerCase() === key || c.single.toLowerCase() === key) || store.categories[0];
}
function categoryIcon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('aria-hidden', 'true');
  svg.innerHTML = CATEGORY_ICONS[name] || CATEGORY_ICONS.layers;
  return svg;
}
let catalog = [];
let activeType = '';
let activeRegion = '';
let displayCurrency = '';

/* ---------- Helpers ---------- */
function isHttps(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' || (url.protocol === 'http:' && url.hostname === 'localhost');
  } catch {
    return false;
  }
}

function isSafeAsset(value) {
  if (typeof value !== 'string' || !value.trim()) return false;
  if (/^(assets|covers|samples)\//.test(value) && !value.includes('..')) return true;
  return isHttps(value);
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function readStore(key) {
  try {
    const stored = JSON.parse(localStorage.getItem(key) || '[]');
    return Array.isArray(stored) ? stored.filter((id) => typeof id === 'string') : [];
  } catch {
    return [];
  }
}

function writeStore(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* Private mode or storage disabled: state lives for this page view only. */
  }
}

function currentCurrency() {
  return displayCurrency || store.currency;
}

function formatMoney(amount, currency) {
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency, minimumFractionDigits: Number.isInteger(amount) ? 0 : 2 }).format(amount);
  } catch {
    return `${currency} ${amount}`;
  }
}

function saleActive(guide) {
  return Number.isFinite(guide.salePrice) && guide.salePrice < guide.price && (!guide.saleEnds || guide.saleEnds > Date.now());
}

function priceInfo(guide, variant) {
  const cur = currentCurrency();
  const base = variant || guide;
  let amount = Number.isFinite(base.prices[cur]) ? base.prices[cur] : null;
  let currency = cur;
  if (amount === null) {
    amount = base.price;
    currency = store.currency;
  }
  if (!variant && saleActive(guide)) {
    const sale = Number.isFinite(guide.salePrices[currency]) ? guide.salePrices[currency]
      : currency === store.currency ? guide.salePrice : Math.round(amount * (guide.salePrice / guide.price) * 100) / 100;
    return { amount: sale, was: amount, currency };
  }
  return { amount, was: null, currency };
}

function priceNode(guide, variant, className) {
  const info = priceInfo(guide, variant);
  const node = el('span', className || 'card-price');
  if (info.was !== null) {
    const was = el('s', null, formatMoney(info.was, info.currency));
    node.append(was, ' ', el('span', 'price-now', formatMoney(info.amount, info.currency)));
  } else {
    node.textContent = formatMoney(info.amount, info.currency);
  }
  return node;
}

function saleLabel(guide) {
  if (!saleActive(guide)) return '';
  if (!guide.saleEnds) return 'Sale';
  const days = Math.max(0, Math.ceil((guide.saleEnds - Date.now()) / 86400000));
  return days <= 1 ? 'Sale · ends today' : `Sale · ${days} days left`;
}

function slugify(text) {
  return String(text || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
}

function isPreorder(guide) {
  return Boolean(guide.preorder) || (guide.releaseDate && guide.releaseDate > Date.now());
}

function releaseLabel(guide) {
  if (!guide.releaseDate) return 'Pre-order';
  return `Pre-order · ships ${new Date(guide.releaseDate).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })}`;
}

function placeOf(guide) {
  return [guide.country, guide.state].filter(Boolean).join(' / ');
}

function kickerOf(guide) {
  const place = placeOf(guide);
  const kind = categoryOf(guide.type).single;
  return place ? `${kind} · ${place}` : kind;
}

function formatOf(guide) {
  if (guide.format) return guide.format;
  if (guide.type === 'Bundle') return `${guide.includes.length} titles`;
  return categoryOf(guide.type).format || 'Digital download';
}

function coverFor(guide) {
  if (guide.cover) {
    const img = document.createElement('img');
    img.src = guide.cover;
    img.alt = guide.coverAlt || `Cover of ${guide.title}`;
    img.loading = 'lazy';
    img.decoding = 'async';
    return img;
  }
  const art = el('div', 'cover-art');
  art.dataset.tone = TONES[Math.max(0, catalog.indexOf(guide)) % TONES.length];
  art.append(el('span', null, guide.country || guide.type), el('strong', null, guide.state || guide.title));
  return art;
}

function canBuy(guide, variant) {
  const target = variant || guide;
  return Boolean(target.paymentLink || (target.priceId && store.endpoint));
}

function byId(id) {
  return catalog.find((guide) => guide.id === id);
}

/* ---------- Toast ---------- */
const toast = $('#toast');
let toastTimer;
function showToast(text) {
  toast.textContent = text;
  toast.classList.add('is-visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('is-visible'), 2800);
}

/* ---------- Dialog plumbing ---------- */
const openDialogs = [];
function openDialog(panel, overlay, focusTarget) {
  panel.hidden = false;
  overlay.hidden = false;
  void panel.offsetWidth; // commit display change before the transition starts
  panel.classList.add('is-open');
  overlay.classList.add('is-open');
  document.body.classList.add('cart-open');
  openDialogs.push({ panel, overlay, lastFocus: document.activeElement });
  if (focusTarget) focusTarget.focus();
}

function closeDialog(panel, overlay) {
  const index = openDialogs.findIndex((d) => d.panel === panel);
  if (panel.hidden || index === -1) return;
  const [entry] = openDialogs.splice(index, 1);
  panel.classList.remove('is-open');
  overlay.classList.remove('is-open');
  if (!openDialogs.length) document.body.classList.remove('cart-open');
  const finish = () => {
    panel.hidden = true;
    overlay.hidden = true;
  };
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) finish(); else setTimeout(finish, 450);
  if (entry.lastFocus && typeof entry.lastFocus.focus === 'function') entry.lastFocus.focus();
}

document.addEventListener('keydown', (event) => {
  if (!openDialogs.length) return;
  const top = openDialogs[openDialogs.length - 1];
  if (event.key === 'Escape') {
    closeDialog(top.panel, top.overlay);
    return;
  }
  if (event.key === 'Tab') {
    const focusable = [...top.panel.querySelectorAll('a[href], button:not([disabled]), input, textarea, select')].filter((node) => !node.hidden && node.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
});

/* ---------- Stripe checkout ---------- */
let checkoutBusy = false;

async function startCheckout(entries, button, gift) {
  if (checkoutBusy) return;
  const ids = entries.map(({ guide, variant }) => (variant || guide).priceId).filter(Boolean);
  if (!store.endpoint || !ids.length) {
    showToast('Checkout is not available for this selection yet.');
    return;
  }
  checkoutBusy = true;
  const originalText = button ? button.textContent : '';
  if (button) {
    button.disabled = true;
    button.textContent = 'Opening Stripe…';
  }
  try {
    const response = await fetch(`${store.endpoint}/session`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items: ids, gift: gift || undefined })
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !isHttps(data.url)) throw new Error(data.error || 'Checkout could not start');
    window.location.assign(data.url);
  } catch (error) {
    showToast('Stripe checkout could not start. Please try again.');
    console.warn(error);
    checkoutBusy = false;
    if (button) {
      button.disabled = false;
      button.textContent = originalText;
    }
  }
}

function buyNow(guide, variant, button) {
  const target = variant || guide;
  if (target.paymentLink) {
    window.location.assign(target.paymentLink);
    return;
  }
  startCheckout([{ guide, variant }], button);
}

/* ---------- Cart ---------- */
let cart = readStore(CART_KEY);
const cartItems = $('#cart-items');
const cartEmpty = $('#cart-empty');
const cartFoot = $('#cart-foot');
const cartCount = $('#cart-count');
const cartTotal = $('#cart-total');
const cartSummaryLabel = $('#cart-summary-label');
const cartCheckout = $('#cart-checkout');
const cartNote = $('#cart-note');
const cartPanel = $('#cart');
const cartOverlay = $('#cart-overlay');
const cartOpenButton = $('#cart-open');

function cartKey(guide, variant) {
  return variant ? `${guide.id}::${guide.variants.indexOf(variant)}` : guide.id;
}

function resolveKey(key) {
  const [id, index] = key.split('::');
  const guide = byId(id);
  if (!guide) return null;
  const variant = index !== undefined ? guide.variants[Number(index)] : null;
  if (index !== undefined && !variant) return null;
  return { key, guide, variant };
}

function cartEntries() {
  return cart.map(resolveKey).filter(Boolean);
}

function singleCheckoutPossible(entries) {
  return Boolean(store.endpoint) && entries.length > 0 && entries.every(({ guide, variant }) => (variant || guide).priceId);
}

function inCart(guide, variant) {
  return cart.includes(cartKey(guide, variant));
}

function addToCart(guide, variant) {
  const key = cartKey(guide, variant);
  if (cart.includes(key)) {
    openCart();
    return;
  }
  cart = [...cart, key];
  writeStore(CART_KEY, cart);
  renderCart();
  showToast(`Added “${guide.title}${variant ? ` · ${variant.label}` : ''}” to your cart`);
  cartCount.classList.remove('is-bumped');
  void cartCount.offsetWidth;
  cartCount.classList.add('is-bumped');
}

function removeFromCart(key) {
  cart = cart.filter((item) => item !== key);
  writeStore(CART_KEY, cart);
  renderCart();
}

function renderCart() {
  const entries = cartEntries();
  if (entries.length !== cart.length) {
    cart = entries.map((entry) => entry.key);
    writeStore(CART_KEY, cart);
  }
  cartCount.textContent = String(entries.length);
  cartCount.hidden = entries.length === 0;
  cartOpenButton.setAttribute('aria-label', entries.length ? `Cart, ${entries.length} ${entries.length === 1 ? 'title' : 'titles'}` : 'Cart, empty');
  cartEmpty.hidden = entries.length > 0;
  cartFoot.hidden = entries.length === 0;
  cartItems.replaceChildren();

  const singleCheckout = Boolean(store.endpoint) && entries.every(({ guide, variant }) => (variant || guide).priceId);
  for (const { key, guide, variant } of entries) {
    const item = el('li', 'cart-item');
    const thumb = el('div', 'cart-thumb');
    thumb.append(coverFor(guide));
    const body = el('div', 'cart-item-body');
    body.append(el('span', 'card-kicker', kickerOf(guide)), el('h3', null, variant ? `${guide.title} · ${variant.label}` : guide.title), priceNode(guide, variant, 'cart-item-price'));
    const actions = el('div', 'cart-item-actions');
    if (!singleCheckout && canBuy(guide, variant)) {
      const buy = el('button', 'button', 'Buy with Stripe');
      buy.type = 'button';
      buy.addEventListener('click', () => buyNow(guide, variant, buy));
      actions.append(buy);
    }
    const remove = el('button', 'cart-remove', 'Remove');
    remove.type = 'button';
    remove.setAttribute('aria-label', `Remove ${guide.title} from cart`);
    remove.addEventListener('click', () => removeFromCart(key));
    actions.append(remove);
    body.append(actions);
    item.append(thumb, body);
    cartItems.append(item);
  }

  cartSummaryLabel.textContent = `${entries.length} ${entries.length === 1 ? 'title' : 'titles'}`;
  const totals = entries.map(({ guide, variant }) => priceInfo(guide, variant));
  const sameCurrency = totals.every((info) => info.currency === (totals[0] && totals[0].currency));
  const subtotal = totals.reduce((sum, info) => sum + info.amount, 0);
  const discount = store.discount;
  const discountApplies = Boolean(discount && singleCheckoutPossible(entries) && entries.length >= discount.minItems);
  const total = discountApplies ? subtotal * (1 - discount.percent / 100) : subtotal;
  cartTotal.textContent = totals.length && sameCurrency ? formatMoney(Math.round(total * 100) / 100, totals[0].currency) : '';
  const note = $('#cart-discount');
  if (discount && entries.length) {
    note.hidden = false;
    note.classList.toggle('is-applied', discountApplies);
    if (discountApplies) note.textContent = `${discount.percent}% off applied at checkout for ${discount.minItems}+ titles. You save ${sameCurrency ? formatMoney(Math.round((subtotal - total) * 100) / 100, totals[0].currency) : `${discount.percent}%`}.`;
    else { const more = discount.minItems - entries.length; note.textContent = `Add ${more} more ${more === 1 ? 'title' : 'titles'} for ${discount.percent}% off the whole cart.`; }
  } else note.hidden = true;
  cartCheckout.hidden = !singleCheckout;
  $('#gift').hidden = !singleCheckout;
  cartNote.textContent = singleCheckout
    ? 'One secure Stripe payment for everything in your cart. Your cart is saved on this device only.'
    : 'Each title has its own secure Stripe checkout. Your cart is saved on this device only.';

  for (const button of $$('[data-add]')) {
    const entry = resolveKey(button.dataset.add);
    const added = entry ? inCart(entry.guide, entry.variant) : false;
    button.classList.toggle('is-added', added);
    button.textContent = added ? 'In cart ✓' : 'Add to cart';
  }
}

function openCart() {
  setMenu(false);
  openDialog(cartPanel, cartOverlay, $('#cart-close'));
}
function closeCart() {
  closeDialog(cartPanel, cartOverlay);
}

cartOpenButton.addEventListener('click', openCart);
$('#footer-cart').addEventListener('click', openCart);
$('#cart-close').addEventListener('click', closeCart);
cartOverlay.addEventListener('click', closeCart);
$('#cart-browse').addEventListener('click', closeCart);
cartCheckout.addEventListener('click', () => {
  const giftEmail = $('#gift-email').value.trim();
  let gift = null;
  if ($('#gift').open && giftEmail) {
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(giftEmail)) {
      showToast('Please enter a valid email for the gift recipient.');
      $('#gift-email').focus();
      return;
    }
    gift = { email: giftEmail, message: $('#gift-message').value.trim() };
  }
  startCheckout(cartEntries(), cartCheckout, gift);
});
$('#cart-clear').addEventListener('click', () => {
  cart = [];
  writeStore(CART_KEY, cart);
  renderCart();
  showToast('Cart cleared');
});

/* ---------- Saved titles ---------- */
let saved = readStore(SAVED_KEY);
function isSaved(guide) {
  return saved.includes(guide.id);
}
/** When the visitor has an account session in this browser, saved titles are written to it too. */
function syncSavedToAccount() {
  let session = null;
  try { session = JSON.parse(localStorage.getItem('marufi-session') || 'null'); } catch { session = null; }
  if (!session || !session.token || session.exp < Date.now() || !store.endpoint) return;
  fetch(`${store.endpoint}/account/me`, { method: 'PUT', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${session.token}` }, body: JSON.stringify({ saved }) }).catch(() => {});
}

function toggleSaved(guide) {
  saved = isSaved(guide) ? saved.filter((id) => id !== guide.id) : [...saved, guide.id];
  writeStore(SAVED_KEY, saved);
  showToast(isSaved(guide) ? `Saved “${guide.title}” for later` : `Removed “${guide.title}” from saved`);
  renderSaved();
  if (activeType === 'saved') update();
  syncSavedToAccount();
}
function renderSaved() {
  saved = saved.filter(byId);
  const count = $('#saved-count');
  count.textContent = String(saved.length);
  count.hidden = saved.length === 0;
  const chipCount = $('#saved-chip-count');
  chipCount.textContent = saved.length ? String(saved.length) : '';
  chipCount.hidden = saved.length === 0;
  for (const button of $$('[data-save]')) {
    const guide = byId(button.dataset.save);
    const on = guide ? isSaved(guide) : false;
    button.classList.toggle('is-on', on);
    button.setAttribute('aria-pressed', String(on));
    if (button.classList.contains('text-link')) button.textContent = on ? 'Saved ✓' : 'Save for later';
    else button.setAttribute('aria-label', on ? `Remove ${guide.title} from saved` : `Save ${guide.title} for later`);
  }
}
$('#saved-open').addEventListener('click', () => {
  setMenu(false);
  setType('saved');
  scrollToSection('#guides');
});

/* ---------- Compare ---------- */
let compareIds = [];
const compareBar = $('#compare-bar');
function toggleCompare(guide) {
  if (compareIds.includes(guide.id)) compareIds = compareIds.filter((id) => id !== guide.id);
  else if (compareIds.length >= 3) { showToast('Compare up to three titles at a time.'); return; }
  else compareIds = [...compareIds, guide.id];
  renderCompareBar();
}
function renderCompareBar() {
  compareIds = compareIds.filter(byId);
  compareBar.hidden = compareIds.length === 0;
  $('#compare-count').textContent = String(compareIds.length);
  const items = $('#compare-bar-items');
  items.replaceChildren(...compareIds.map((id) => {
    const guide = byId(id);
    const chip = el('button', 'compare-chip');
    chip.type = 'button';
    chip.setAttribute('aria-label', `Remove ${guide.title} from compare`);
    chip.append(el('span', null, guide.title), el('i', null, '×'));
    chip.addEventListener('click', () => toggleCompare(guide));
    return chip;
  }));
  for (const button of $$('[data-compare]')) {
    const on = compareIds.includes(button.dataset.compare);
    button.classList.toggle('is-on', on);
    button.setAttribute('aria-pressed', String(on));
    if (button.classList.contains('text-link')) button.textContent = on ? 'In compare ✓' : 'Add to compare';
  }
}
function openCompare() {
  const guides = compareIds.map(byId).filter(Boolean);
  if (guides.length < 2) { showToast('Add at least two titles to compare.'); return; }
  const table = $('#compare-table');
  table.replaceChildren();
  const head = el('tr');
  head.append(el('th', null, ''));
  for (const guide of guides) {
    const th = el('th');
    const cover = el('div', 'compare-cover');
    cover.append(coverFor(guide));
    th.append(cover, el('strong', null, guide.title));
    head.append(th);
  }
  table.append(head);
  const rows = [
    ['Type', (g) => g.type],
    ['Place', (g) => placeOf(g) || '—'],
    ['Format', (g) => formatOf(g)],
    ['Pages', (g) => (g.pages ? String(g.pages) : '—')],
    ['Price', (g) => priceNode(g, null, 'compare-price')],
    ['What is inside', (g) => { const ul = el('ul'); ul.append(...g.highlights.map((h) => el('li', null, h))); return g.highlights.length ? ul : '—'; }],
    ['Sample', (g) => (g.sample || g.samplePages.length ? 'Yes' : '—')],
    ['Last updated', (g) => g.updated || '—']
  ];
  for (const [label, render] of rows) {
    const tr = el('tr');
    tr.append(el('th', null, label));
    for (const guide of guides) {
      const td = el('td');
      const value = render(guide);
      td.append(value);
      tr.append(td);
    }
    table.append(tr);
  }
  const actions = el('tr', 'compare-actions');
  actions.append(el('th', null, ''));
  for (const guide of guides) {
    const td = el('td');
    td.append(buildActions(guide));
    actions.append(td);
  }
  table.append(actions);
  renderCart();
  openDialog($('#compare'), $('#compare-overlay'), $('#compare-close'));
}
$('#compare-open').addEventListener('click', openCompare);
$('#compare-clear').addEventListener('click', () => { compareIds = []; renderCompareBar(); });
$('#compare-close').addEventListener('click', () => closeDialog($('#compare'), $('#compare-overlay')));
$('#compare-overlay').addEventListener('click', () => closeDialog($('#compare'), $('#compare-overlay')));

/* ---------- Sample viewer ---------- */
let sampleGuide = null;
let samplePage = 0;
function renderSample() {
  const stage = $('#sample-stage');
  stage.replaceChildren();
  const counter = $('#sample-counter');
  if (sampleGuide.samplePages.length) {
    const img = document.createElement('img');
    img.src = sampleGuide.samplePages[samplePage];
    img.alt = `Sample page ${samplePage + 1} of ${sampleGuide.title}`;
    stage.append(img);
    counter.textContent = `Page ${samplePage + 1} of ${sampleGuide.samplePages.length}`;
    $('#sample-prev').disabled = samplePage === 0;
    $('#sample-next').disabled = samplePage >= sampleGuide.samplePages.length - 1;
    $('#sample-prev').hidden = false;
    $('#sample-next').hidden = false;
  } else {
    const frame = document.createElement('iframe');
    frame.src = sampleGuide.sample;
    frame.title = `Sample of ${sampleGuide.title}`;
    stage.append(frame);
    counter.textContent = 'Sample pages';
    $('#sample-prev').hidden = true;
    $('#sample-next').hidden = true;
  }
}
function openSample(guide) {
  sampleGuide = guide;
  samplePage = 0;
  $('#sample-title').textContent = guide.title;
  $('#sample-buy').hidden = !canBuy(guide);
  renderSample();
  openDialog($('#sample-viewer'), $('#sample-overlay'), $('#sample-close'));
}
$('#sample-prev').addEventListener('click', () => { samplePage = Math.max(0, samplePage - 1); renderSample(); });
$('#sample-next').addEventListener('click', () => { samplePage = Math.min(sampleGuide.samplePages.length - 1, samplePage + 1); renderSample(); });
$('#sample-buy').addEventListener('click', (event) => { if (sampleGuide) buyNow(sampleGuide, null, event.currentTarget); });
$('#sample-close').addEventListener('click', () => closeDialog($('#sample-viewer'), $('#sample-overlay')));
$('#sample-overlay').addEventListener('click', () => closeDialog($('#sample-viewer'), $('#sample-overlay')));

/* ---------- Quick view ---------- */
const quick = $('#quick');
const quickOverlay = $('#quick-overlay');
let quickGuide = null;
let quickVariant = null;

function renderQuickPrice() {
  $('#quick-price').replaceChildren(priceNode(quickGuide, quickVariant, 'quick-price-value'));
  $('#quick-format').textContent = quickVariant ? (quickVariant.format || quickVariant.label) : formatOf(quickGuide);
  $('#quick-add').dataset.add = cartKey(quickGuide, quickVariant);
  $('#quick-buy').hidden = !canBuy(quickGuide, quickVariant);
  renderCart();
}

function openQuick(guide) {
  quickGuide = guide;
  quickVariant = null;
  const cover = $('#quick-cover');
  cover.replaceChildren(coverFor(guide));
  const badge = saleLabel(guide) || (isPreorder(guide) ? releaseLabel(guide) : guide.badge);
  if (badge) cover.append(el('span', `card-badge${isPreorder(guide) ? ' is-preorder' : ''}`, badge));
  $('#quick-kicker').textContent = kickerOf(guide);
  $('#quick-title').textContent = guide.title;
  $('#quick-desc').textContent = guide.longDescription || guide.description;
  const highlights = $('#quick-highlights');
  highlights.replaceChildren(...guide.highlights.map((text) => el('li', null, text)));
  highlights.hidden = guide.highlights.length === 0;
  const includes = $('#quick-includes');
  includes.replaceChildren();
  const included = guide.includes.map(byId).filter(Boolean);
  includes.hidden = included.length === 0;
  if (included.length) {
    includes.append(el('span', 'message-overline', `Includes ${included.length} titles`));
    const ul = el('ul');
    for (const item of included) {
      const li = el('li');
      const button = el('button', 'link-button', item.title);
      button.type = 'button';
      button.addEventListener('click', () => openQuick(item));
      li.append(button, el('span', null, ` · ${placeOf(item)}`));
      ul.append(li);
    }
    includes.append(ul);
  }
  const variants = $('#quick-variants');
  variants.replaceChildren();
  variants.hidden = guide.variants.length === 0;
  if (guide.variants.length) {
    const options = [{ label: guide.variantLabel || 'Standard', format: guide.format, self: true }, ...guide.variants];
    options.forEach((option, index) => {
      const label = el('label', 'variant');
      const input = document.createElement('input');
      input.type = 'radio';
      input.name = 'variant';
      input.checked = index === 0;
      input.addEventListener('change', () => { quickVariant = option.self ? null : option; renderQuickPrice(); });
      const text = el('span');
      text.append(el('strong', null, option.label), el('small', null, option.format || ''));
      label.append(input, text, priceNode(guide, option.self ? null : option, 'variant-price'));
      variants.append(label);
    });
  }
  $('#quick-sample').hidden = !(guide.sample || guide.samplePages.length);
  $('#quick-save').dataset.save = guide.id;
  $('#quick-compare').dataset.compare = guide.id;
  const reviews = $('#quick-reviews');
  reviews.replaceChildren();
  reviews.hidden = guide.reviews.length === 0;
  for (const review of guide.reviews.slice(0, 2)) {
    const block = el('blockquote', 'mini-review');
    block.append(el('p', null, `“${review.quote}”`), el('cite', null, [review.name, review.place].filter(Boolean).join(' · ')));
    reviews.append(block);
  }
  $('#quick-buy').firstChild.textContent = isPreorder(guide) ? 'Pre-order ' : 'Buy now ';
  $('#quick-note').textContent = isPreorder(guide)
    ? `Pre-order: pay now and we email your download the day it ships${guide.releaseDate ? ` (${new Date(guide.releaseDate).toLocaleDateString(undefined, { day: 'numeric', month: 'long' })})` : ''}.`
    : 'Secure checkout with Stripe. Instant download after payment.';
  renderRelated(guide);
  renderQuickPrice();
  renderSaved();
  renderCompareBar();
  openDialog(quick, quickOverlay, $('#quick-close'));
}

function relatedTo(guide) {
  const seen = new Set([guide.id]);
  const picks = [];
  const add = (g) => { if (g && !seen.has(g.id) && picks.length < 3) { seen.add(g.id); picks.push(g); } };
  // Bundles that include this title, then other titles in the same country, then the same type elsewhere.
  for (const g of catalog) if (g.type === 'Bundle' && g.includes.includes(guide.id)) add(g);
  for (const id of guide.includes) add(byId(id));
  for (const g of catalog) if (g.country && g.country === guide.country && g.type !== 'Bundle') add(g);
  for (const g of catalog) if (g.type === guide.type) add(g);
  return picks;
}

function renderRelated(guide) {
  const box = $('#quick-related');
  const list = $('#quick-related-list');
  list.replaceChildren();
  const picks = relatedTo(guide);
  box.hidden = picks.length === 0;
  for (const g of picks) {
    const item = el('button', 'related-item');
    item.type = 'button';
    const cover = el('div', 'related-cover');
    cover.append(coverFor(g));
    item.append(cover, el('strong', 'related-title', g.title), el('span', 'related-price', `${categoryOf(g.type).single} · ${formatMoney(priceInfo(g).amount, priceInfo(g).currency)}`));
    item.addEventListener('click', () => openQuick(g));
    list.append(item);
  }
}
function closeQuick() {
  closeDialog(quick, quickOverlay);
}
$('#quick-close').addEventListener('click', closeQuick);
quickOverlay.addEventListener('click', closeQuick);
$('#quick-buy').addEventListener('click', (event) => { if (quickGuide) buyNow(quickGuide, quickVariant, event.currentTarget); });
$('#quick-add').addEventListener('click', () => { if (quickGuide) addToCart(quickGuide, quickVariant); });
$('#quick-sample').addEventListener('click', () => { if (quickGuide) openSample(quickGuide); });
$('#quick-save').addEventListener('click', () => { if (quickGuide) toggleSaved(quickGuide); });
$('#quick-compare').addEventListener('click', () => { if (quickGuide) toggleCompare(quickGuide); });

/* ---------- Cards ---------- */
function buildActions(guide) {
  const actions = el('div', 'card-actions');
  const buy = el('button', 'button button-sm', isPreorder(guide) ? 'Pre-order' : 'Buy now');
  buy.type = 'button';
  buy.append(' ');
  const arrow = el('span', null, '→');
  arrow.setAttribute('aria-hidden', 'true');
  buy.append(arrow);
  buy.hidden = !canBuy(guide);
  buy.addEventListener('click', () => (guide.variants.length ? openQuick(guide) : buyNow(guide, null, buy)));
  const add = el('button', 'button-ghost', 'Add to cart');
  add.type = 'button';
  add.dataset.add = guide.id;
  add.addEventListener('click', () => (guide.variants.length ? openQuick(guide) : addToCart(guide, null)));
  actions.append(buy, add);
  return actions;
}

function iconButton(className, svg, label) {
  const button = el('button', className);
  button.type = 'button';
  button.innerHTML = svg;
  button.setAttribute('aria-label', label);
  return button;
}
const HEART = '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M12 20s-7-4.6-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.4-7 10-7 10z"/></svg>';
const COMPARE = '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M4 6h7v12H4zM13 6h7v12h-7z"/></svg>';

function buildCard(guide) {
  const card = el('article', 'guide-card');
  card.setAttribute('aria-label', guide.title);
  const cover = el('button', 'card-cover');
  cover.type = 'button';
  cover.setAttribute('aria-label', `Quick view: ${guide.title}`);
  cover.append(coverFor(guide));
  const badge = saleLabel(guide) || (isPreorder(guide) ? releaseLabel(guide) : guide.badge);
  if (badge) cover.append(el('span', `card-badge${saleActive(guide) ? ' is-sale' : isPreorder(guide) ? ' is-preorder' : ''}`, badge));
  cover.append(el('span', 'card-peek', 'Quick view'));
  cover.addEventListener('click', () => openQuick(guide));

  const tools = el('div', 'card-tools');
  const save = iconButton('card-tool', HEART, `Save ${guide.title} for later`);
  save.dataset.save = guide.id;
  save.addEventListener('click', () => toggleSaved(guide));
  const compare = iconButton('card-tool', COMPARE, `Add ${guide.title} to compare`);
  compare.dataset.compare = guide.id;
  compare.addEventListener('click', () => toggleCompare(guide));
  tools.append(save, compare);

  const body = el('div', 'card-body');
  const meta = el('div', 'card-meta');
  meta.append(el('span', 'card-kicker', kickerOf(guide)), priceNode(guide, null, 'card-price'));
  const title = el('h3');
  const titleButton = el('button', 'title-button', guide.title);
  titleButton.type = 'button';
  titleButton.addEventListener('click', () => openQuick(guide));
  title.append(titleButton);
  body.append(meta, title, el('p', null, guide.description));
  if (guide.sample || guide.samplePages.length) {
    const sample = el('button', 'text-link card-sample', 'Read a sample ');
    sample.type = 'button';
    const arrow = el('span', null, '→');
    arrow.setAttribute('aria-hidden', 'true');
    sample.append(arrow);
    sample.addEventListener('click', () => openSample(guide));
    body.append(sample);
  }
  body.append(buildActions(guide));
  card.append(cover, tools, body);
  return card;
}

function buildFeatured(guide) {
  const block = el('article', 'featured-card');
  block.setAttribute('aria-label', `Featured: ${guide.title}`);
  const cover = el('button', 'featured-cover');
  cover.type = 'button';
  cover.setAttribute('aria-label', `Quick view: ${guide.title}`);
  cover.append(coverFor(guide));
  cover.addEventListener('click', () => openQuick(guide));
  const body = el('div', 'featured-body');
  body.append(el('span', 'message-overline', "Editor's pick"), el('span', 'card-kicker', kickerOf(guide)));
  const title = el('h3');
  const titleButton = el('button', 'title-button', guide.title);
  titleButton.type = 'button';
  titleButton.addEventListener('click', () => openQuick(guide));
  title.append(titleButton);
  body.append(title, el('p', null, guide.longDescription || guide.description));
  if (guide.highlights.length) {
    const ul = el('ul', 'featured-highlights');
    ul.append(...guide.highlights.slice(0, 4).map((text) => el('li', null, text)));
    body.append(ul);
  }
  const meta = el('div', 'featured-meta');
  meta.append(el('span', null, formatOf(guide)), priceNode(guide, null, 'featured-price'));
  body.append(meta, buildActions(guide));
  block.append(cover, body);
  return block;
}

/* ---------- Catalog rendering and filters ---------- */
function renderGuides(guides, filtering) {
  list.replaceChildren();
  featuredBox.replaceChildren();
  const featured = !filtering ? guides.find((guide) => guide.featured) : null;
  featuredBox.hidden = !featured;
  if (featured) featuredBox.append(buildFeatured(featured));
  const rest = featured ? guides.filter((guide) => guide !== featured) : guides;
  guideCount.textContent = `${guides.length} ${guides.length === 1 ? 'title' : 'titles'} shown`;
  message.hidden = guides.length > 0;
  if (!guides.length) {
    const title = message.querySelector('h3');
    const detail = message.querySelector('p');
    if (activeType === 'saved') {
      title.textContent = 'Nothing saved yet.';
      detail.textContent = 'Tap the heart on any title to keep it here.';
    } else if (filtering) {
      title.textContent = 'Nothing matches your search.';
      detail.textContent = 'Try a different place, topic, or kind of title.';
    } else {
      title.textContent = 'The library is taking shape.';
      detail.textContent = 'Guides, books, help, and lists will appear here as they are added.';
    }
    return;
  }
  for (const guide of rest) list.append(buildCard(guide));
  // Entrance for the first render only; re-renders from search and filters stay instant so typing never flickers.
  if (!catalogAnimated) {
    catalogAnimated = true;
    if (featured) revealOnView(featuredBox.firstElementChild, { y: 40, scale: 0.98 });
    revealBatch($$('.guide-card', list));
  }
  if (motionOn) window.ScrollTrigger.refresh();
  renderCart();
  renderSaved();
  renderCompareBar();
}
let catalogAnimated = false;

function scrollToSection(target) {
  const node = typeof target === 'string' ? $(target) : target;
  if (!node) return;
  if (lenis) lenis.scrollTo(node, { offset: -80 });
  else node.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
}

function setType(type) {
  activeType = type;
  for (const chip of typeFilter.querySelectorAll('.chip')) chip.classList.toggle('is-active', chip.dataset.type === type);
  update();
}

function setCountry(country, region) {
  countryFilter.value = country || '';
  activeRegion = region || '';
  update();
}

function update() {
  const query = search.value.trim().toLocaleLowerCase();
  const filtering = Boolean(query || countryFilter.value || activeType || activeRegion);
  const shown = catalog.filter((guide) =>
    (!countryFilter.value || guide.country === countryFilter.value) &&
    (!activeRegion || guide.state === activeRegion) &&
    (!activeType || (activeType === 'saved' ? isSaved(guide) : guide.type === activeType)) &&
    (!query || `${guide.country} ${guide.state} ${guide.title} ${categoryOf(guide.type).name} ${categoryOf(guide.type).single} ${guide.tags.join(' ')}`.toLocaleLowerCase().includes(query))
  );
  renderGuides(shown, filtering);
  const bar = $('#active-filter');
  const parts = [];
  if (countryFilter.value) parts.push(countryFilter.value);
  if (activeRegion) parts.push(activeRegion);
  if (activeType) parts.push(activeType === 'saved' ? 'Saved' : categoryOf(activeType).name);
  if (query) parts.push(`“${search.value.trim()}”`);
  bar.hidden = parts.length === 0;
  $('#active-filter-text').textContent = parts.length ? `Showing: ${parts.join(' · ')}` : '';
  const link = $('#country-page-link');
  link.hidden = !(store.countryPages && countryFilter.value);
  if (!link.hidden) { link.href = `${slugify(countryFilter.value)}/`; link.textContent = `Open the ${countryFilter.value} page →`; }
}

search.addEventListener('input', update);
countryFilter.addEventListener('change', () => { activeRegion = ''; update(); });
typeFilter.addEventListener('click', (event) => {
  const chip = event.target.closest('.chip');
  if (chip) setType(chip.dataset.type);
});
$('#clear-filters').addEventListener('click', () => {
  search.value = '';
  countryFilter.value = '';
  activeRegion = '';
  setType('');
});

/* ---------- Atlas: map, countries, regions ---------- */
const MAP = { width: 950, height: 620 };
// Reference islands and small regions with ids in the map SVG, used to calibrate the projection at runtime.
const MAP_REFS = [
  ['iceland', 64.9, -18.6], ['sri lanka', 7.8, 80.7], ['tasmania', -42.0, 146.6], ['madagascar', -19.4, 46.7], ['hokkaido', 43.2, 142.8],
  ['taiwan', 23.7, 121.0], ['sardinia', 40.0, 9.0], ['corsica', 42.1, 9.1], ['crete', 35.2, 24.9], ['hainan', 19.2, 109.7], ['jamaica', 18.1, -77.3],
  ['newfoundland', 48.8, -56.0], ['vancouver', 49.7, -125.8], ['haida gwaii', 53.3, -132.3], ['puerto rico', 18.2, -66.5], ['new caledonia', -21.3, 165.5],
  ['kerguelen', -49.3, 69.5], ['falklands east', -51.7, -58.5], ['oahu', 21.5, -158.0], ['tahiti', -17.65, -149.4], ['mauritius', -20.3, 57.6],
  ['malta', 35.9, 14.4], ['cyprus', 35.1, 33.4], ['bali', -8.4, 115.2], ['cuba', 21.8, -79.0], ['south island', -43.9, 170.5], ['galapagos', -0.6, -90.7],
  ['sao miguel', 37.8, -25.5], ['grand bahama', 26.6, -78.4], ['kauai', 22.1, -159.5], ['sicily', 37.6, 14.0], ['honshu', 36.5, 138.5], ['ireland', 53.4, -8.0]
];
let mapModel = null;

function linearFit(xs, ys) {
  const n = xs.length;
  const mx = xs.reduce((a, b) => a + b, 0) / n;
  const my = ys.reduce((a, b) => a + b, 0) / n;
  let num = 0;
  let den = 0;
  for (let i = 0; i < n; i += 1) { num += (xs[i] - mx) * (ys[i] - my); den += (xs[i] - mx) ** 2; }
  const m = num / den;
  return { m, c: my - m * mx };
}

function calibrateMap(svg) {
  const points = [];
  for (const [id, lat, lng] of MAP_REFS) {
    const node = svg.querySelector(`#g-${CSS.escape(id)}`);
    if (!node) continue;
    const box = node.getBBox();
    points.push({ lat, lng, x: box.x + box.width / 2, y: box.y + box.height / 2 });
  }
  if (points.length < 6) { mapModel = { fx: { m: 2.678, c: 455.5 }, fy: { m: -3.447, c: 340.6 }, points: [] }; return; }
  const fx = linearFit(points.map((p) => p.lng), points.map((p) => p.x));
  const fy = linearFit(points.map((p) => p.lat), points.map((p) => p.y));
  for (const p of points) {
    p.dx = p.x - (fx.m * p.lng + fx.c);
    p.dy = p.y - (fy.m * p.lat + fy.c);
  }
  mapModel = { fx, fy, points };
}

function project([lat, lng]) {
  if (!mapModel) return [((lng + 180) / 360) * MAP.width, ((90 - lat) / 180) * MAP.height];
  let x = mapModel.fx.m * lng + mapModel.fx.c;
  let y = mapModel.fy.m * lat + mapModel.fy.c;
  // Inverse-distance weighted correction from the nearest reference points smooths the hand-drawn map's distortions.
  let wsum = 0;
  let dx = 0;
  let dy = 0;
  for (const p of mapModel.points) {
    const dlat = p.lat - lat;
    const dlng = Math.min(Math.abs(p.lng - lng), 360 - Math.abs(p.lng - lng)) * Math.cos(((p.lat + lat) / 2) * Math.PI / 180);
    const dist = Math.sqrt(dlat * dlat + dlng * dlng) + 0.5;
    if (dist > 60) continue;
    const w = 1 / (dist * dist);
    wsum += w;
    dx += p.dx * w;
    dy += p.dy * w;
  }
  if (wsum) { x += dx / wsum; y += dy / wsum; }
  return [x, y];
}

async function renderAtlas() {
  const countries = [...new Set(catalog.map((guide) => guide.country).filter(Boolean))].sort((a, b) => a.localeCompare(b));
  const countryBox = $('#atlas-countries');
  countryBox.replaceChildren();
  for (const country of countries) {
    const count = catalog.filter((guide) => guide.country === country).length;
    const regions = new Set(catalog.filter((guide) => guide.country === country).map((guide) => guide.state).filter(Boolean));
    const button = el('button', 'country-tile');
    button.type = 'button';
    button.setAttribute('role', 'listitem');
    button.append(el('strong', null, country), el('span', null, `${count} ${count === 1 ? 'title' : 'titles'} · ${regions.size} ${regions.size === 1 ? 'state' : 'states'}`));
    button.addEventListener('click', () => showRegions(country));
    countryBox.append(button);
  }
  $('#atlas-empty').hidden = countries.length === 0 ? false : false;
  $('#atlas-empty').querySelector('p').textContent = countries.length ? 'Pick a country to see its states and the titles filed under each one.' : 'Countries appear here as the first guides are published.';

  const mapBox = $('#atlas-map-inner');
  try {
    const response = await fetch('assets/world-map.svg');
    if (!response.ok) throw new Error('map');
    mapBox.innerHTML = await response.text();
    const svg = mapBox.querySelector('svg');
    calibrateMap(svg);
    const layer = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    layer.setAttribute('class', 'pins');
    svg.append(layer);
    const grouped = new Map();
    for (const guide of catalog) {
      if (!guide.coordinates) continue;
      const key = `${guide.country}|${guide.state}`;
      if (!grouped.has(key)) grouped.set(key, { guide, count: 0 });
      grouped.get(key).count += 1;
    }
    const tip = $('#atlas-tip');
    for (const { guide, count } of grouped.values()) {
      const [x, y] = project(guide.coordinates);
      const pin = document.createElementNS('http://www.w3.org/2000/svg', 'g');
      pin.setAttribute('class', 'pin');
      pin.setAttribute('transform', `translate(${x.toFixed(1)} ${y.toFixed(1)})`);
      pin.setAttribute('tabindex', '0');
      pin.setAttribute('role', 'button');
      pin.setAttribute('aria-label', `${placeOf(guide)}: ${count} ${count === 1 ? 'title' : 'titles'}`);
      const halo = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      halo.setAttribute('r', '9');
      halo.setAttribute('class', 'pin-halo');
      const dot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      dot.setAttribute('r', '3.2');
      dot.setAttribute('class', 'pin-dot');
      pin.append(halo, dot);
      const show = () => {
        tip.hidden = false;
        tip.textContent = `${placeOf(guide)} · ${count} ${count === 1 ? 'title' : 'titles'}`;
        const rect = mapBox.getBoundingClientRect();
        const svgRect = svg.getBoundingClientRect();
        tip.style.left = `${svgRect.left - rect.left + (x / MAP.width) * svgRect.width}px`;
        tip.style.top = `${svgRect.top - rect.top + (y / MAP.height) * svgRect.height}px`;
      };
      pin.addEventListener('mouseenter', show);
      pin.addEventListener('focus', show);
      pin.addEventListener('mouseleave', () => { tip.hidden = true; });
      pin.addEventListener('blur', () => { tip.hidden = true; });
      const go = () => showRegions(guide.country, guide.state);
      pin.addEventListener('click', go);
      pin.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); go(); } });
      layer.append(pin);
    }
    if (motionOn) {
      const land = [...svg.querySelectorAll('.land path')];
      const pins = [...layer.querySelectorAll('.pin')];
      const tl = gsapLib.timeline({ scrollTrigger: { trigger: mapBox, start: 'top 78%', once: true } });
      tl.from(land, { opacity: 0, duration: 0.8, stagger: { each: 0.004, from: 'start' }, ease: 'power1.out', clearProps: 'opacity' }, 0)
        .from(pins, { scale: 0, transformOrigin: 'center', duration: 0.7, ease: 'back.out(2.2)', stagger: 0.08 }, 0.9);
      revealBatch([...countryBox.querySelectorAll('.country-tile')], { y: 30, rotate: 0, scale: 0.96, step: 0.07, duration: 0.8 });
      window.ScrollTrigger.refresh();
    }
  } catch {
    mapBox.replaceChildren(el('p', 'atlas-fallback', 'The map could not load. Use the country list instead.'));
  }
}

function showRegions(country, highlight) {
  const panel = $('#atlas-regions');
  $('#atlas-countries').hidden = true;
  $('#atlas-empty').hidden = true;
  panel.hidden = false;
  $('#atlas-country-title').textContent = country;
  const head = $('.atlas-regions-head');
  let pageLink = head.querySelector('.country-page');
  if (store.countryPages) {
    if (!pageLink) { pageLink = el('a', 'link-button country-page'); head.insertBefore(pageLink, head.querySelector('#atlas-back')); }
    pageLink.href = `${slugify(country)}/`;
    pageLink.textContent = `${country} page →`;
  } else if (pageLink) pageLink.remove();
  const regionList = $('#region-list');
  regionList.replaceChildren();
  const inCountry = catalog.filter((guide) => guide.country === country);
  const known = Array.isArray(store.regions[country]) ? store.regions[country] : [];
  const names = [...new Set([...known, ...inCountry.map((guide) => guide.state).filter(Boolean)])].sort((a, b) => a.localeCompare(b));
  for (const name of names) {
    const titles = inCountry.filter((guide) => guide.state === name);
    const li = el('li', `region${titles.length ? '' : ' is-empty'}${highlight === name ? ' is-highlight' : ''}`);
    const head = el('div', 'region-head');
    head.append(el('strong', null, name), el('span', null, titles.length ? `${titles.length} ${titles.length === 1 ? 'title' : 'titles'}` : 'Coming soon'));
    li.append(head);
    if (titles.length) {
      const ul = el('ul', 'region-titles');
      for (const guide of titles) {
        const item = el('li');
        const button = el('button', 'link-button', guide.title);
        button.type = 'button';
        button.addEventListener('click', () => openQuick(guide));
        item.append(button, el('span', 'region-type', ` ${categoryOf(guide.type).single}`), priceNode(guide, null, 'region-price'));
        ul.append(item);
      }
      li.append(ul);
      const all = el('button', 'text-link', 'Show in the collection ');
      all.type = 'button';
      const arrow = el('span', null, '↑');
      arrow.setAttribute('aria-hidden', 'true');
      all.append(arrow);
      all.addEventListener('click', () => {
        setCountry(country, name);
        scrollToSection('#guides');
      });
      li.append(all);
    } else {
      const ask = el('button', 'text-link', 'Notify me when it is ready ');
      ask.type = 'button';
      const arrow = el('span', null, '↓');
      arrow.setAttribute('aria-hidden', 'true');
      ask.append(arrow);
      ask.addEventListener('click', () => {
        $('#notify-place').value = `${name}, ${country}`;
        $('#notify-email').focus();
      });
      li.append(ask);
    }
    regionList.append(li);
  }
  if (highlight) {
    const target = regionList.querySelector('.is-highlight');
    if (target) target.scrollIntoView({ block: 'nearest', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
  }
}
$('#atlas-back').addEventListener('click', () => {
  $('#atlas-regions').hidden = true;
  $('#atlas-countries').hidden = false;
  $('#atlas-empty').hidden = false;
});

/* ---------- Notify me and resend ---------- */
async function postJson(path, body) {
  const response = await fetch(`${store.endpoint}${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || 'Request failed');
  return data;
}

$('#notify-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const place = $('#notify-place').value.trim();
  const email = $('#notify-email').value.trim();
  const note = $('#notify-note');
  if (!place || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    note.textContent = 'Please add the place and a valid email.';
    return;
  }
  if (!store.endpoint) {
    const contact = $('#footer-contact');
    if (!contact.hidden) {
      window.location.href = `${contact.href}?subject=${encodeURIComponent(`Guide request: ${place}`)}&body=${encodeURIComponent(`Please let me know when a guide for ${place} is ready. ${email}`)}`;
      return;
    }
    note.textContent = 'Requests are not open yet. Check back soon.';
    return;
  }
  const button = $('#notify-submit');
  button.disabled = true;
  try {
    await postJson('/notify', { place, email });
    note.textContent = `Noted. We will write to ${email} when ${place} is ready.`;
    $('#notify-form').reset();
  } catch (error) {
    note.textContent = error.message || 'Something went wrong. Please try again.';
  } finally {
    button.disabled = false;
  }
});

$('#resend-open').addEventListener('click', () => {
  setMenu(false);
  $('#resend-note').textContent = store.endpoint ? '' : 'Automatic resending is not switched on yet. Email us and we will send your files by hand.';
  openDialog($('#resend'), $('#resend-overlay'), $('#resend-email'));
});
$('#resend-close').addEventListener('click', () => closeDialog($('#resend'), $('#resend-overlay')));
$('#resend-overlay').addEventListener('click', () => closeDialog($('#resend'), $('#resend-overlay')));
$('#resend-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const email = $('#resend-email').value.trim();
  const note = $('#resend-note');
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) { note.textContent = 'Please enter a valid email.'; return; }
  if (!store.endpoint) return;
  const button = $('#resend-submit');
  button.disabled = true;
  try {
    await postJson('/resend', { email });
    note.textContent = `If ${email} has orders, fresh links are on their way. Check your spam folder if nothing arrives in a few minutes.`;
  } catch (error) {
    note.textContent = error.message || 'Something went wrong. Please try again.';
  } finally {
    button.disabled = false;
  }
});

/* ---------- Store-level sections ---------- */
function applyStore(data) {
  if (typeof data.storeName === 'string' && data.storeName.trim()) store.name = data.storeName.trim();
  if (typeof data.currency === 'string' && /^[A-Z]{3}$/.test(data.currency)) store.currency = data.currency;
  store.currencies = Array.isArray(data.currencies) ? data.currencies.filter((c) => /^[A-Z]{3}$/.test(c)) : [];
  if (!store.currencies.includes(store.currency)) store.currencies.unshift(store.currency);
  if (isHttps(data.checkoutEndpoint)) store.endpoint = data.checkoutEndpoint.replace(/\/session\/?$/, '').replace(/\/+$/, '');
  store.regions = data.regions && typeof data.regions === 'object' ? data.regions : {};
  if (isHttps(data.siteUrl)) store.siteUrl = data.siteUrl.replace(/\/+$/, '');
  const d = data.cartDiscount && typeof data.cartDiscount === 'object' ? data.cartDiscount : null;
  store.discount = d && Number(d.percent) > 0 && Number(d.percent) < 100 && Number(d.minItems) >= 2 ? { percent: Number(d.percent), minItems: Number(d.minItems) } : null;
  store.countryPages = data.countryPages === true;

  // Currency switcher
  try { displayCurrency = localStorage.getItem(CURRENCY_KEY) || ''; } catch { displayCurrency = ''; }
  if (!store.currencies.includes(displayCurrency)) displayCurrency = store.currency;
  const switcher = $('#currency-switch');
  const select = $('#currency-select');
  select.replaceChildren(...store.currencies.map((code) => { const option = el('option', null, code); option.value = code; return option; }));
  select.value = displayCurrency;
  switcher.hidden = store.currencies.length < 2;
  select.addEventListener('change', () => {
    displayCurrency = select.value;
    try { localStorage.setItem(CURRENCY_KEY, displayCurrency); } catch { /* ignore */ }
    update();
    renderCart();
    if (!quick.hidden && quickGuide) renderQuickPrice();
  });
  $('#footer-payments-note').textContent = `All major cards and wallets, processed securely by Stripe. Prices are charged in ${store.currency}${store.currencies.length > 1 ? '; other currencies are shown for reference' : ''}.`;

  // Footer contact and social
  const contact = $('#footer-contact');
  if (typeof data.contactEmail === 'string' && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(data.contactEmail)) {
    contact.href = `mailto:${data.contactEmail}`;
    contact.hidden = false;
  }
  const follow = $('#footer-follow-col');
  const social = data.social && typeof data.social === 'object' ? data.social : {};
  const labels = { instagram: 'Instagram', tiktok: 'TikTok', youtube: 'YouTube', pinterest: 'Pinterest', x: 'X', facebook: 'Facebook', threads: 'Threads' };
  let added = 0;
  for (const [key, label] of Object.entries(labels)) {
    if (isHttps(social[key])) {
      const link = el('a', null, label + ' ');
      link.href = social[key];
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      const arrow = el('span', null, '↗');
      arrow.setAttribute('aria-hidden', 'true');
      link.append(arrow, el('span', 'sr-only', ' (opens in a new tab)'));
      follow.append(link);
      added += 1;
    }
  }
  follow.hidden = added === 0;

  // Reviews
  const reviews = Array.isArray(data.reviews) ? data.reviews.filter((r) => r && typeof r.quote === 'string' && r.quote.trim()) : [];
  const reviewsSection = $('#reviews');
  reviewsSection.hidden = reviews.length === 0;
  if (reviews.length) {
    const grid = $('#reviews-grid');
    grid.replaceChildren();
    const rated = reviews.filter((r) => Number.isFinite(Number(r.rating)));
    const average = rated.length ? rated.reduce((sum, r) => sum + Number(r.rating), 0) / rated.length : 0;
    $('#reviews-summary').textContent = rated.length ? `${average.toFixed(1)} / 5 · ${reviews.length} ${reviews.length === 1 ? 'review' : 'reviews'}` : `${reviews.length} ${reviews.length === 1 ? 'review' : 'reviews'}`;
    for (const review of reviews.slice(0, 6)) {
      const card = el('blockquote', 'review');
      const rating = Math.max(0, Math.min(5, Math.round(Number(review.rating) || 0)));
      if (rating) {
        const stars = el('span', 'stars', '★'.repeat(rating) + '☆'.repeat(5 - rating));
        stars.setAttribute('aria-label', `${rating} out of 5`);
        card.append(stars);
      }
      card.append(el('p', null, `“${review.quote.trim()}”`));
      const cite = el('cite');
      cite.append(el('strong', null, review.name || 'A reader'));
      if (review.place) cite.append(el('span', null, review.place));
      card.append(cite);
      grid.append(card);
    }
    revealBatch([...grid.children], { y: 36, step: 0.12 });
  }

  // Author
  const author = data.author && typeof data.author === 'object' ? data.author : {};
  const authorSection = $('#author');
  authorSection.hidden = !(author.name && author.bio);
  if (!authorSection.hidden) {
    $('#author-role').textContent = author.role || 'Writer and publisher';
    $('#author-title').textContent = author.name;
    const bio = $('#author-bio');
    bio.replaceChildren(...String(author.bio).split(/\n+/).filter(Boolean).map((text) => el('p', null, text)));
    $('#author-note').textContent = author.note || '';
    const photo = $('#author-photo');
    photo.replaceChildren();
    if (isSafeAsset(author.photo)) {
      const img = document.createElement('img');
      img.src = author.photo;
      img.alt = author.photoAlt || author.name;
      img.loading = 'lazy';
      photo.append(img);
    } else {
      const art = el('div', 'cover-art');
      art.dataset.tone = 'pine';
      art.append(el('span', null, 'The author'), el('strong', null, author.name));
      photo.append(art);
    }
  }

  // Refund policy FAQ
  if (typeof data.refundPolicy === 'string' && data.refundPolicy.trim()) {
    $('#faq-refund-text').textContent = data.refundPolicy.trim();
    $('#faq-refund').hidden = false;
  }

  // Zones
  renderZones(Array.isArray(data.zones) ? data.zones : []);

  // Hero video (for a UGC or explainer clip). Falls back to the photo when not set.
  setupHeroVideo(data.heroVideo && typeof data.heroVideo === 'object' ? data.heroVideo : null);

  // Analytics (Plausible, cookie-free)
  const analytics = data.analytics && typeof data.analytics === 'object' ? data.analytics : {};
  if (typeof analytics.plausibleDomain === 'string' && /^[a-z0-9.-]+$/i.test(analytics.plausibleDomain)) {
    const script = document.createElement('script');
    script.defer = true;
    script.dataset.domain = analytics.plausibleDomain;
    script.src = 'https://plausible.io/js/script.js';
    document.head.append(script);
  }
  // Google Analytics 4, when a measurement id is set. IP anonymisation is on by default in GA4.
  if (typeof analytics.ga4 === 'string' && /^G-[A-Z0-9]{4,14}$/.test(analytics.ga4)) {
    const script = document.createElement('script');
    script.async = true;
    script.src = `https://www.googletagmanager.com/gtag/js?id=${analytics.ga4}`;
    document.head.append(script);
    window.dataLayer = window.dataLayer || [];
    window.gtag = function gtag() { window.dataLayer.push(arguments); };
    window.gtag('js', new Date());
    window.gtag('config', analytics.ga4, { anonymize_ip: true });
  }
  // Cloudflare Web Analytics, when a beacon token is set (cookieless).
  if (typeof analytics.cloudflareToken === 'string' && /^[a-f0-9]{32}$/i.test(analytics.cloudflareToken)) {
    const script = document.createElement('script');
    script.defer = true;
    script.src = 'https://static.cloudflareinsights.com/beacon.min.js';
    script.dataset.cfBeacon = JSON.stringify({ token: analytics.cloudflareToken });
    document.head.append(script);
  }
  setupSubscribe();
  renderJournal(Array.isArray(data.articles) ? data.articles : []);
}

/* ---------- Newsletter (footer) ---------- */
function setupSubscribe() {
  const form = $('#subscribe-form');
  if (!form) return;
  form.hidden = !store.endpoint;
  if (!store.endpoint) return;
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const input = $('#subscribe-email');
    const button = $('#subscribe-submit');
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(input.value.trim())) { showToast('Please enter a valid e-mail address'); input.focus(); return; }
    button.disabled = true;
    try {
      const response = await fetch(`${store.endpoint}/subscribe`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: input.value.trim(), source: 'home' }) });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.error || 'Could not subscribe right now');
      form.reset();
      $('#subscribe-note').textContent = result.doubleOptIn ? 'Almost there: check your inbox and confirm.' : 'You are on the list.';
      showToast(result.doubleOptIn ? 'Check your inbox to confirm' : 'You are on the list');
    } catch (error) { showToast(error.message); }
    button.disabled = false;
  });
}

/* ---------- Journal (latest three articles on the home page) ---------- */
function renderJournal(articles) {
  const section = $('#journal');
  const list = $('#journal-list');
  if (!section || !list) return;
  const published = articles
    .filter((a) => a && typeof a.title === 'string' && a.title.trim() && a.status !== 'draft' && !(a.date && Date.parse(a.date) > Date.now()))
    .map((a) => ({ ...a, slug: a.slug || slugify(a.title) }))
    .sort((a, b) => String(b.date || '').localeCompare(String(a.date || '')))
    .slice(0, 3);
  section.hidden = published.length === 0;
  if (!published.length) return;
  list.replaceChildren(...published.map((a) => {
    const card = el('a', 'article-card');
    card.href = `articles/?a=${encodeURIComponent(a.slug)}`;
    if (isSafeAsset(a.cover)) { const img = document.createElement('img'); img.src = a.cover; img.alt = a.coverAlt || ''; img.loading = 'lazy'; card.append(img); }
    const body = el('div', 'article-card-body');
    let when = '';
    try { when = a.date ? new Date(/^\d{4}-\d{2}-\d{2}$/.test(a.date) ? `${a.date}T12:00:00` : a.date).toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' }) : ''; } catch { when = ''; }
    body.append(el('span', 'card-kicker', [a.category, a.place, when].filter(Boolean).join(' · ')), el('h3', null, a.title), el('p', null, a.summary || ''), el('span', 'text-link', 'Read →'));
    card.append(body);
    return card;
  }));
  revealHeading(section.querySelector('.section-head'));
  revealBatch($$('.article-card', list), { y: 50, step: 0.08 });
  renumberGhosts();
}

function setupHeroVideo(config) {
  if (!config || !isSafeAsset(config.src)) return;
  const box = $('#plate-img');
  const video = $('#plate-video');
  const play = $('#plate-play');
  const controls = $('#plate-controls');
  const toggle = $('#plate-toggle');
  const mute = $('#plate-mute');
  const time = $('#plate-time');
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  video.src = config.src;
  if (isSafeAsset(config.poster)) video.poster = config.poster;
  video.loop = config.loop !== false;
  video.muted = true;
  video.setAttribute('aria-label', config.title || 'Introduction video');
  if (typeof config.aspect === 'string' && /^\d+\s*\/\s*\d+$/.test(config.aspect)) box.style.setProperty('--plate-aspect', config.aspect);
  $('#plate-picture').hidden = true;
  video.hidden = false;
  box.classList.add('has-video');
  $('#plate-tag').textContent = config.tag || 'Watch';
  $('#plate-caption-a').textContent = config.captionLeft || 'Film 01';
  $('#plate-caption-b').textContent = config.captionRight || config.title || 'A short introduction';
  $('#plate-play-label').textContent = config.playLabel || 'Play with sound';
  play.hidden = false;
  mute.classList.add('is-muted');

  const setPlaying = (on) => {
    box.classList.toggle('is-playing', on);
    controls.hidden = !on;
    toggle.setAttribute('aria-label', on ? 'Pause' : 'Play');
    toggle.innerHTML = on
      ? '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M7 5h4v14H7zM13 5h4v14h-4z"/></svg>'
      : '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>';
  };
  const format = (n) => `${Math.floor(n / 60)}:${String(Math.floor(n % 60)).padStart(2, '0')}`;
  video.addEventListener('timeupdate', () => { if (Number.isFinite(video.duration)) time.textContent = `${format(video.currentTime)} / ${format(video.duration)}`; });
  video.addEventListener('play', () => setPlaying(true));
  video.addEventListener('pause', () => setPlaying(false));
  video.addEventListener('ended', () => { if (!video.loop) { setPlaying(false); play.hidden = false; } });

  // Tapping the big button plays with sound; the ambient loop stays muted until then.
  const setSound = (on) => { video.muted = !on; box.classList.toggle('is-sound', on); mute.classList.toggle('is-muted', !on); mute.setAttribute('aria-label', on ? 'Mute' : 'Unmute'); };
  play.addEventListener('click', () => { setSound(true); video.currentTime = 0; video.play().catch(() => {}); });
  toggle.addEventListener('click', () => { if (video.paused) video.play().catch(() => {}); else video.pause(); });
  mute.addEventListener('click', () => setSound(video.muted));
  video.addEventListener('click', () => { if (video.paused) video.play().catch(() => {}); else video.pause(); });

  // Autoplay silently as a living poster unless the visitor prefers reduced motion or turned it off.
  if (!reduced && config.autoplay !== false) {
    video.autoplay = true;
    video.play().then(() => { play.hidden = false; controls.hidden = false; }).catch(() => { play.hidden = false; });
  }
}

function renderNeeds() {
  const grid = $('#needs-grid');
  grid.replaceChildren();
  const visible = store.categories.filter((c) => !c.hidden && (c.key !== 'Bundle' || catalog.some((g) => g.type === 'Bundle')));
  for (const c of visible) {
    const count = catalog.filter((g) => g.type === c.key).length;
    const places = new Set(catalog.filter((g) => g.type === c.key).map((g) => g.country).filter(Boolean));
    const card = el('a', `need${count ? '' : ' is-empty'}`);
    card.href = `${c.slug}/`;
    card.dataset.category = c.key;
    const top = el('div', 'need-top');
    top.append(categoryIcon(c.icon), el('span', 'need-count', count ? `${count} ${count === 1 ? 'title' : 'titles'}${places.size ? ` · ${places.size} ${places.size === 1 ? 'country' : 'countries'}` : ''}` : 'Coming soon'));
    const foot = el('div', 'need-foot');
    foot.append(el('span', null, `Open ${c.name.toLowerCase()} →`), el('span', 'pill-soft', c.format || 'Digital'));
    card.append(top, el('h3', null, c.name), el('p', null, c.blurb), foot);
    card.setAttribute('aria-label', count ? `Open ${c.name}` : `${c.name}: coming soon`);
    grid.append(card);
  }
  if (!setupNeedsScene(grid)) revealBatch($$('.need', grid), { y: 60, rotate: 4, step: 0.08 });
  renumberGhosts();
}

function renderResources(resources) {
  const grid = $('#help-grid');
  const empty = $('#help-empty');
  grid.replaceChildren();
  const valid = resources.filter((r) => r && typeof r.title === 'string' && r.title.trim() && (isHttps(r.url) || /^[\w./-]+$/.test(String(r.url || ''))));
  empty.hidden = valid.length > 0;
  for (const r of valid) {
    const card = el('a', 'help-card');
    card.href = r.url;
    if (isHttps(r.url) && !r.url.startsWith(location.origin)) { card.target = '_blank'; card.rel = 'noopener'; }
    const kicker = el('span', 'card-kicker');
    const kind = typeof r.kind === 'string' && r.kind.trim() ? r.kind.trim() : 'Read';
    kicker.append(el('span', null, [r.category, r.place].filter((v) => typeof v === 'string' && v.trim()).join(' · ') || 'Free'), el('span', null, kind));
    card.append(kicker, el('h3', null, r.title.trim()), el('p', null, typeof r.summary === 'string' ? r.summary : ''), el('span', 'text-link', `${kind === 'Download' ? 'Download' : kind === 'Link' ? 'Open' : 'Read'} →`));
    grid.append(card);
  }
  revealBatch($$('.help-card', grid), { y: 50, rotate: 3, step: 0.08 });
}

function renderZones(zones) {
  const section = $('#zones');
  const valid = zones.filter((zone) => zone && typeof zone.city === 'string' && zone.city.trim() && typeof zone.country === 'string');
  section.hidden = valid.length === 0;
  if (!valid.length) return;
  const track = $('#zones-track');
  track.replaceChildren();
  valid.slice(0, 12).forEach((zone, index) => {
    const card = el('article', 'zone');
    const visual = el('div', 'zone-visual');
    if (isSafeAsset(zone.image)) {
      const img = document.createElement('img');
      img.src = zone.image;
      img.alt = zone.imageAlt || `${zone.city}, ${zone.country}`;
      img.loading = 'lazy';
      visual.append(img);
    } else {
      const art = el('div', 'cover-art');
      art.dataset.tone = TONES[index % TONES.length];
      art.append(el('span', null, zone.country), el('strong', null, zone.city));
      visual.append(art);
    }
    visual.append(el('span', 'zone-index', String(index + 1).padStart(2, '0')));
    const body = el('div', 'zone-body');
    body.append(el('span', 'card-kicker', [zone.country, zone.state].filter(Boolean).join(' / ')), el('h3', null, zone.city));
    if (zone.tagline) body.append(el('p', 'zone-tagline', zone.tagline));
    if (zone.why) body.append(el('p', 'zone-why', zone.why));
    const facts = el('dl', 'zone-facts');
    if (zone.bestTime) { facts.append(el('dt', null, 'Best time'), el('dd', null, zone.bestTime)); }
    if (zone.knownFor) { facts.append(el('dt', null, 'Known for'), el('dd', null, Array.isArray(zone.knownFor) ? zone.knownFor.join(' · ') : String(zone.knownFor))); }
    if (facts.childElementCount) body.append(facts);
    const guide = zone.guideId ? byId(zone.guideId) : null;
    if (guide) {
      const link = el('button', 'text-link', `Open the ${categoryOf(guide.type).single.toLowerCase()} `);
      link.type = 'button';
      const arrow = el('span', null, '→');
      arrow.setAttribute('aria-hidden', 'true');
      link.append(arrow);
      link.addEventListener('click', () => openQuick(guide));
      body.append(link);
    } else if (zone.country) {
      const link = el('button', 'text-link', `Guides for ${zone.country} `);
      link.type = 'button';
      const arrow = el('span', null, '→');
      arrow.setAttribute('aria-hidden', 'true');
      link.append(arrow);
      link.addEventListener('click', () => {
        setCountry(zone.country, '');
        scrollToSection('#guides');
      });
      body.append(link);
    }
    card.append(visual, body);
    track.append(card);
  });
  revealBatch($$('.zone', track), { y: 60, rotate: 0, scale: 0.96, step: 0.12 });
  if (motionOn) gsapLib.from(track, { x: 120, duration: 1.4, ease: 'power3.out', clearProps: 'transform', scrollTrigger: { trigger: track, start: 'top 90%', once: true } });
  const scroller = $('#zones-track');
  $('#zones-prev').addEventListener('click', () => scroller.scrollBy({ left: -scroller.clientWidth * 0.8, behavior: 'smooth' }));
  $('#zones-next').addEventListener('click', () => scroller.scrollBy({ left: scroller.clientWidth * 0.8, behavior: 'smooth' }));
}

function injectStructuredData() {
  if (!catalog.length) return;
  const base = store.siteUrl || '';
  const data = {
    '@context': 'https://schema.org',
    '@type': 'ItemList',
    name: `${store.name} collection`,
    itemListElement: catalog.map((guide, index) => ({
      '@type': 'ListItem',
      position: index + 1,
      item: {
        '@type': 'Product',
        name: guide.title,
        description: guide.description,
        image: guide.cover ? (base && !isHttps(guide.cover) ? `${base}/${guide.cover}` : guide.cover) : undefined,
        brand: { '@type': 'Brand', name: store.name },
        category: categoryOf(guide.type).single,
        offers: { '@type': 'Offer', price: saleActive(guide) ? guide.salePrice : guide.price, priceCurrency: store.currency, availability: 'https://schema.org/InStock', url: base ? `${base}/#guides` : undefined }
      }
    }))
  };
  const script = document.createElement('script');
  script.type = 'application/ld+json';
  script.textContent = JSON.stringify(data);
  document.head.append(script);
}

/* ---------- Catalog load ---------- */
function moneyMap(value) {
  const out = {};
  if (value && typeof value === 'object') for (const [code, amount] of Object.entries(value)) if (/^[A-Z]{3}$/.test(code) && Number.isFinite(Number(amount))) out[code] = Number(amount);
  return out;
}

function normalise(guide, index) {
  const type = categoryOf(guide.type).key;
  const tags = Array.isArray(guide.tags) ? guide.tags.filter((t) => typeof t === 'string' && t.trim()).map((t) => t.trim()).slice(0, 12) : [];
  const variants = Array.isArray(guide.variants) ? guide.variants.filter((v) => v && typeof v.label === 'string' && Number.isFinite(Number(v.price)) && (isHttps(v.paymentLink) || /^price_[A-Za-z0-9]+$/.test(String(v.priceId || '')))).map((v) => ({
    label: v.label.trim(),
    format: typeof v.format === 'string' ? v.format.trim() : '',
    price: Number(v.price),
    prices: moneyMap(v.prices),
    priceId: /^price_[A-Za-z0-9]+$/.test(String(v.priceId || '')) ? v.priceId : '',
    paymentLink: isHttps(v.paymentLink) ? v.paymentLink : ''
  })) : [];
  const coords = Array.isArray(guide.coordinates) && guide.coordinates.length === 2 && guide.coordinates.every((n) => Number.isFinite(Number(n))) ? [Number(guide.coordinates[0]), Number(guide.coordinates[1])] : null;
  return {
    id: String(guide.id || guide.priceId || guide.paymentLink || `item-${index}`),
    type,
    tags,
    country: typeof guide.country === 'string' ? guide.country.trim() : '',
    state: typeof guide.state === 'string' ? guide.state.trim() : '',
    title: String(guide.title).trim(),
    description: String(guide.description).trim(),
    longDescription: typeof guide.longDescription === 'string' ? guide.longDescription.trim() : '',
    highlights: Array.isArray(guide.highlights) ? guide.highlights.filter((h) => typeof h === 'string' && h.trim()).slice(0, 6) : [],
    format: typeof guide.format === 'string' ? guide.format.trim() : '',
    pages: Number.isFinite(Number(guide.pages)) ? Number(guide.pages) : 0,
    updated: typeof guide.updated === 'string' ? guide.updated.trim() : '',
    price: Number(guide.price),
    prices: moneyMap(guide.prices),
    salePrice: Number.isFinite(Number(guide.salePrice)) ? Number(guide.salePrice) : NaN,
    salePrices: moneyMap(guide.salePrices),
    saleEnds: typeof guide.saleEnds === 'string' && !Number.isNaN(Date.parse(guide.saleEnds)) ? Date.parse(guide.saleEnds) : 0,
    variantLabel: typeof guide.variantLabel === 'string' ? guide.variantLabel.trim() : '',
    variants,
    includes: Array.isArray(guide.includes) ? guide.includes.filter((id) => typeof id === 'string') : [],
    sample: isSafeAsset(guide.sample) && !/\.(png|jpe?g|webp|gif|avif)(\?|$)/i.test(guide.sample) ? guide.sample : '',
    samplePages: [
      ...(isSafeAsset(guide.sample) && /\.(png|jpe?g|webp|gif|avif)(\?|$)/i.test(guide.sample) ? [guide.sample] : []),
      ...(Array.isArray(guide.samplePages) ? guide.samplePages.filter(isSafeAsset) : [])
    ].slice(0, 12),
    reviews: Array.isArray(guide.reviews) ? guide.reviews.filter((r) => r && typeof r.quote === 'string' && r.quote.trim()).slice(0, 4) : [],
    coordinates: coords,
    priceId: typeof guide.priceId === 'string' && /^price_[A-Za-z0-9]+$/.test(guide.priceId) ? guide.priceId : '',
    paymentLink: isHttps(guide.paymentLink) ? guide.paymentLink : '',
    cover: isSafeAsset(guide.cover) ? guide.cover : '',
    coverAlt: typeof guide.coverAlt === 'string' ? guide.coverAlt : '',
    badge: typeof guide.badge === 'string' ? guide.badge.trim().slice(0, 24) : '',
    featured: guide.featured === true,
    preorder: guide.preorder === true,
    releaseDate: typeof guide.releaseDate === 'string' && !Number.isNaN(Date.parse(guide.releaseDate)) ? Date.parse(guide.releaseDate) : 0
  };
}

fetch('guides.json', { cache: 'no-cache' })
  .then((response) => {
    if (!response.ok) throw new Error('Could not load the catalog');
    return response.json();
  })
  .then(async (local) => {
    // When the Worker is deployed, the catalog published from the admin dashboard takes over.
    // guides.json stays as the bootstrap config and the fallback.
    const endpoint = isHttps(local.checkoutEndpoint) ? local.checkoutEndpoint.replace(/\/session\/?$/, '').replace(/\/+$/, '') : '';
    if (!endpoint) return local;
    try {
      const response = await fetch(`${endpoint}/catalog`, { cache: 'no-cache' });
      if (!response.ok) return local;
      const live = await response.json();
      if (!live || !Array.isArray(live.guides)) return local;
      return { ...local, ...live, checkoutEndpoint: local.checkoutEndpoint };
    } catch {
      return local;
    }
  })
  .then((data) => {
    setCategories(data.categories);
    catalog = Array.isArray(data.guides)
      ? data.guides.filter((guide, index) => {
          if (!guide) return false;
          if (guide.status === 'draft') return false;
          if (guide.status === 'scheduled' && guide.publishAt && !Number.isNaN(Date.parse(guide.publishAt)) && Date.parse(guide.publishAt) > Date.now()) return false;
          const valid = guide.title && guide.description && Number.isFinite(Number(guide.price)) && Number(guide.price) >= 0 &&
            (isHttps(guide.paymentLink) || (typeof guide.priceId === 'string' && /^price_[A-Za-z0-9]+$/.test(guide.priceId)));
          if (!valid) console.warn(`Skipping incomplete guide at index ${index}: needs title, description, price, and a paymentLink or priceId`);
          return valid;
        }).map(normalise)
      : [];
    applyStore(data);
    renderNeeds();
    renderResources(Array.isArray(data.resources) ? data.resources : []);
    const countries = [...new Set(catalog.map((guide) => guide.country).filter(Boolean))].sort((a, b) => a.localeCompare(b));
    for (const country of countries) {
      const option = document.createElement('option');
      option.value = country;
      option.textContent = country;
      countryFilter.append(option);
    }
    const types = new Set(catalog.map((guide) => guide.type));
    const savedChip = typeFilter.querySelector('[data-type="saved"]');
    for (const c of store.categories) if (types.has(c.key)) {
      const chip = el('button', 'chip', c.name);
      chip.type = 'button';
      chip.dataset.type = c.key;
      typeFilter.insertBefore(chip, savedChip);
    }
    controls.hidden = catalog.length === 0;
    // Sample listings are a placeholder for the real catalog only.
    for (const element of $$('[data-sample-only]')) element.hidden = catalog.length > 0;
    // Deep links: ?country=Canada&state=Alberta&type=Book, or ?q=tokyo
    const params = new URLSearchParams(location.search);
    if (params.get('country') && countries.includes(params.get('country'))) countryFilter.value = params.get('country');
    if (params.get('state')) activeRegion = params.get('state');
    if (params.get('type') && store.categories.some((c) => c.key === params.get('type'))) activeType = params.get('type');
    if (params.get('q')) search.value = params.get('q');
    if (activeType) for (const chip of typeFilter.querySelectorAll('.chip')) chip.classList.toggle('is-active', chip.dataset.type === activeType);
    update();
    if ([...params.keys()].some((k) => ['country', 'state', 'type', 'q'].includes(k))) setTimeout(() => $('#guides').scrollIntoView(), 50);
    // Kind pages link back here for the quick view (?open=<id>) and the cart (#cart).
    const wanted = params.get('open') ? catalog.find((guide) => guide.id === params.get('open')) : null;
    if (wanted) setTimeout(() => { $('#guides').scrollIntoView(); openQuick(wanted); }, 80);
    if (location.hash === '#cart') setTimeout(openCart, 80);
    renderAtlas();
    renumberGhosts();
    injectStructuredData();
    // Refresh sale countdowns once a minute.
    setInterval(() => { if (catalog.some(saleActive)) update(); }, 60000);
  })
  .catch(() => {
    message.querySelector('h3').textContent = 'The catalog could not load.';
    message.querySelector('p').textContent = 'Please refresh the page and try again.';
    guideCount.textContent = 'The catalog could not load.';
    renderCart();
  });

/* ---------- Mobile menu ---------- */
const navToggle = $('#nav-toggle');
const siteNav = $('#site-nav');
function setMenu(open) {
  document.body.classList.toggle('nav-open', open);
  navToggle.setAttribute('aria-expanded', String(open));
  navToggle.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
  navToggle.querySelector('.nav-toggle-text').textContent = open ? 'Close' : 'Menu';
}
navToggle.addEventListener('click', () => setMenu(!document.body.classList.contains('nav-open')));
siteNav.addEventListener('click', (event) => { if (event.target.closest('a')) setMenu(false); });
document.addEventListener('keydown', (event) => { if (event.key === 'Escape') setMenu(false); });
matchMedia('(min-width: 821px)').addEventListener('change', (event) => { if (event.matches) setMenu(false); });

/* ---------- Interface ---------- */
const masthead = $('#masthead');
const topbar = $('#topbar');
if ('IntersectionObserver' in window) {
  new IntersectionObserver(([entry]) => {
    masthead.classList.toggle('is-stuck', !entry.isIntersecting);
  }, { threshold: 0 }).observe(topbar);
}

/* ---------- Scroll animation (GSAP + ScrollTrigger + Lenis) ----------
   Progressive enhancement: nothing is hidden by CSS. This only runs when the pinned bundles loaded and the visitor
   has not asked for reduced motion. Entrances play once, on first entry into view, using opacity, transform, and
   clip-path only, and hand control back to the stylesheet when they finish. Smooth scrolling is native scroll with
   eased wheel input (Lenis); it pauses while a dialog is open and is skipped entirely under reduced motion. */
const gsapLib = window.gsap && window.ScrollTrigger ? window.gsap : null;
const motionOn = Boolean(gsapLib) && !matchMedia('(prefers-reduced-motion: reduce)').matches;
const SMOOTH_SCROLL = true;
let lenis = null;
if (motionOn) {
  gsapLib.registerPlugin(window.ScrollTrigger);
  gsapLib.defaults({ ease: 'power3.out', overwrite: 'auto' });
}
const CLEAR = 'opacity,transform,clipPath';
const markDone = (node) => { node.dataset.motion = 'done'; };

/** Wraps every word of a heading in a masked box so words can slide up into view. Keeps <em> styling. */
function splitWords(node) {
  if (!node || node.dataset.split) return [];
  node.dataset.split = '1';
  const words = [];
  const wrap = (textNode) => {
    const parts = textNode.textContent.split(/(\s+)/);
    const frag = document.createDocumentFragment();
    for (const part of parts) {
      if (!part) continue;
      if (/^\s+$/.test(part)) { frag.append(document.createTextNode(' ')); continue; }
      const box = el('span', 'w');
      const inner = el('span', 'wi', part);
      box.append(inner);
      frag.append(box);
      words.push(inner);
    }
    textNode.replaceWith(frag);
  };
  for (const child of [...node.childNodes]) {
    if (child.nodeType === Node.TEXT_NODE) wrap(child);
    else if (child.nodeType === Node.ELEMENT_NODE) for (const grand of [...child.childNodes]) if (grand.nodeType === Node.TEXT_NODE) wrap(grand);
  }
  return words;
}

/** One block rises and fades in when it scrolls into view. */
function revealOnView(node, { y = 44, scale = 1, delay = 0, duration = 1.3, start = 'top 88%' } = {}) {
  if (!motionOn || !node || node.dataset.motion) return;
  node.dataset.motion = 'pending';
  gsapLib.from(node, { opacity: 0, y, scale, duration, delay, ease: 'expo.out', clearProps: CLEAR, scrollTrigger: { trigger: node, start, once: true, onEnter: () => { node.dataset.motion = 'playing'; } }, onComplete: () => markDone(node) });
}

/** Cards and tiles enter with depth: a slight tilt, a rise, and a stagger as each one reaches the viewport. */
function revealBatch(nodes, { y = 48, scale = 0.985, rotate = 0, step = 0.08, duration = 1.3, start = 'top 92%' } = {}) {
  if (!motionOn || !nodes.length) return;
  const fresh = nodes.filter((n) => !n.dataset.motion);
  if (!fresh.length) return;
  for (const n of fresh) { n.dataset.motion = 'pending'; if (n.parentElement) n.parentElement.classList.add('depth'); }
  gsapLib.set(fresh, { opacity: 0, y, scale, rotateX: rotate, transformOrigin: '50% 100%' });
  // Cover images settle from a slight zoom as their card arrives.
  const covers = fresh.flatMap((n) => [...n.querySelectorAll('.card-cover img, .cover-art')]);
  if (covers.length) gsapLib.set(covers, { scale: 1.12 });
  window.ScrollTrigger.batch(fresh, {
    start,
    once: true,
    onEnter: (batch) => {
      for (const n of batch) n.dataset.motion = 'playing';
      gsapLib.to(batch, { opacity: 1, y: 0, scale: 1, rotateX: 0, duration, stagger: step, ease: 'expo.out', clearProps: CLEAR, onComplete: () => batch.forEach(markDone) });
      const imgs = batch.flatMap((n) => [...n.querySelectorAll('.card-cover img, .cover-art')]);
      if (imgs.length) gsapLib.to(imgs, { scale: 1, duration: duration + 0.4, stagger: step, ease: 'expo.out', clearProps: 'transform' });
    }
  });
}

/** Headings: the rule draws across, the words slide up out of their masks, and the intro text follows. */
function revealHeading(head, { start = 'top 82%' } = {}) {
  if (!motionOn || !head || head.dataset.motion) return;
  head.dataset.motion = 'pending';
  const index = head.previousElementSibling && head.previousElementSibling.classList.contains('section-index') ? head.previousElementSibling : null;
  const h = head.querySelector('h1, h2, h3');
  const words = h ? splitWords(h) : [];
  const rest = [...head.children].filter((c) => c !== h);
  const tl = gsapLib.timeline({ scrollTrigger: { trigger: head, start, once: true, onEnter: () => { head.dataset.motion = 'playing'; } }, onComplete: () => markDone(head) });
  if (index) tl.from(index, { clipPath: 'inset(0 100% 0 0)', duration: 1.2, ease: 'power4.out', clearProps: CLEAR }, 0);
  if (words.length) tl.from(words, { yPercent: 115, rotate: 2, duration: 1.15, stagger: 0.04, ease: 'expo.out', clearProps: CLEAR }, 0.1);
  else if (h) tl.from(h, { opacity: 0, y: 48, duration: 1, clearProps: CLEAR }, 0.1);
  if (rest.length) tl.from(rest, { opacity: 0, y: 32, duration: 0.9, stagger: 0.1, clearProps: CLEAR }, 0.5);
}

/** Stroked SVG icons draw themselves in. */
function drawIcons(paths, trigger) {
  if (!motionOn || !paths.length) return;
  for (const path of paths) {
    let length = 100;
    try { length = path.getTotalLength(); } catch { /* not a path-like element */ }
    path.style.strokeDasharray = String(length);
    path.style.strokeDashoffset = String(length);
  }
  gsapLib.to(paths, { strokeDashoffset: 0, duration: 1.4, ease: 'power2.inOut', stagger: 0.06, clearProps: 'strokeDasharray,strokeDashoffset', scrollTrigger: { trigger, start: 'top 85%', once: true } });
}

function setupHeroIntro(delay = 0) {
  const copy = $('.hero-copy');
  const plate = $('#hero-plate');
  if (!copy || !plate) return;
  copy.dataset.motion = plate.dataset.motion = 'playing';
  const media = $('#plate-img > picture, #plate-img > video');
  const words = splitWords(copy.querySelector('h1'));
  const tl = gsapLib.timeline({ delay, defaults: { ease: 'power3.out' }, onComplete: () => { markDone(copy); markDone(plate); } });
  tl.from(copy.querySelector('.eyebrow'), { opacity: 0, x: -24, duration: 0.8, clearProps: CLEAR }, 0.1)
    .from(words, { yPercent: 115, rotate: 4, duration: 1.1, stagger: 0.07, ease: 'power4.out', clearProps: CLEAR }, 0.2)
    .from(copy.querySelector('.deck'), { opacity: 0, y: 34, duration: 1, clearProps: CLEAR }, 0.75)
    .from(copy.querySelectorAll('.hero-actions > *'), { opacity: 0, y: 26, duration: 0.8, stagger: 0.12, clearProps: CLEAR }, 0.95)
    .from(copy.querySelectorAll('.hero-facts li'), { opacity: 0, y: 20, duration: 0.7, stagger: 0.1, clearProps: CLEAR }, 1.15)
    // Only clipPath and opacity are cleared here: the scroll scenes own the transforms of the plate and its image.
    .from(plate, { opacity: 0, y: 60, scale: 0.94, rotate: -1.5, duration: 1.3, clearProps: 'opacity' }, 0.35)
    .from($('#plate-img'), { clipPath: 'inset(100% 0 0 0)', duration: 1.5, ease: 'power4.out', clearProps: 'clipPath' }, 0.45)
    .from(plate.querySelector('figcaption'), { opacity: 0, y: 12, duration: 0.7, clearProps: CLEAR }, 1.4);
  if (media) {
    gsapLib.set(media, { scale: 1.14, transformOrigin: 'center' });
    tl.from(media, { scale: 1.4, duration: 1.8, ease: 'power3.out' }, 0.45);
  }
  // Small screens and touch: depth as you leave the hero, the copy recedes faster than the plate.
  // Large screens get the pinned scene in setupHeroScene instead.
  mm.add(SMALL_MOTION, () => {
    if (media) gsapLib.to(media, { yPercent: 9, ease: 'none', scrollTrigger: { trigger: '.hero', start: 'top top', end: 'bottom top', scrub: 0.5 } });
    gsapLib.to(copy, { y: -110, opacity: 0.25, ease: 'none', scrollTrigger: { trigger: '.hero', start: 'top top', end: 'bottom top', scrub: 0.4 } });
    gsapLib.to(plate, { y: -50, ease: 'none', scrollTrigger: { trigger: '.hero', start: 'top top', end: 'bottom top', scrub: 0.6 } });
    const side = $('.hero-side');
    if (side) gsapLib.to(side, { y: -140, ease: 'none', scrollTrigger: { trigger: '.hero', start: 'top top', end: 'bottom top', scrub: true } });
  });
}

/* ---------- Cinematic layer ----------
   Arrival curtain, a pinned hero where the film grows to fill the screen, a horizontal gallery for Browse by need,
   scroll-filled statement text, drawing step lines, ghost numerals, velocity skew, magnetic buttons, a cursor ring,
   and card tilt. Pinned scenes and pointer effects run only on large screens with a mouse; touch and small
   screens keep the lighter entrances. Nothing here changes markup that the page needs without JavaScript. */
const DESKTOP_MOTION = '(min-width: 1000px) and (pointer: fine)';
const SMALL_MOTION = '(max-width: 999px), (pointer: coarse)';
const mm = motionOn ? gsapLib.matchMedia() : null;
const arriving = document.documentElement.classList.contains('arriving');

/** The curtain: wordmark rises, a rule draws, then two panels part to reveal the page. Once per session. */
function setupArrival() {
  const overlay = $('#arrival');
  const finish = () => { document.documentElement.classList.remove('arriving'); if (overlay) overlay.remove(); if (lenis) lenis.start(); };
  if (!motionOn || !arriving || !overlay) { finish(); return 0; }
  try { sessionStorage.setItem('arrived', '1'); } catch { /* private mode */ }
  const words = splitWords(overlay.querySelector('.arrival-word'));
  const small = overlay.querySelector('small');
  const line = overlay.querySelector('.arrival-line');
  const tl = gsapLib.timeline({ defaults: { ease: 'power4.out' }, onComplete: finish });
  tl.from(words, { yPercent: 115, rotate: 3, duration: 1, stagger: 0.08, ease: 'expo.out' }, 0.05)
    .fromTo(line, { scaleX: 0, transformOrigin: 'left' }, { scaleX: 1, duration: 0.9, ease: 'power3.inOut' }, 0.25)
    .from(small, { opacity: 0, y: 10, duration: 0.6 }, 0.6)
    .to([overlay.querySelector('.arrival-mark'), line], { opacity: 0, y: -30, duration: 0.45, ease: 'power2.in' }, 1.25)
    .to(overlay.querySelector('.arrival-top'), { yPercent: -100, duration: 0.95, ease: 'expo.inOut' }, 1.4)
    .to(overlay.querySelector('.arrival-bottom'), { yPercent: 100, duration: 0.95, ease: 'expo.inOut' }, 1.4);
  return 1.5;
}

/** Large screens: the hero pins and the film plate grows until it fills the viewport, then the page moves on. */
function setupHeroScene() {
  const hero = $('.hero');
  const plateImg = $('#plate-img');
  const copy = $('.hero-copy');
  const plate = $('#hero-plate');
  if (!hero || !plateImg || !copy || !plate) return;
  mm.add(DESKTOP_MOTION, () => {
    hero.classList.add('is-scene');
    const hint = el('div', 'scroll-hint');
    hint.setAttribute('aria-hidden', 'true');
    hint.append(el('span', null, 'Scroll'), el('i'));
    hero.append(hint);
    const chrome = $$('.plate-tag, .plate-controls', plateImg);
    const play = plateImg.querySelector('.plate-play');
    const fit = () => Math.max(innerWidth / plateImg.offsetWidth, innerHeight / plateImg.offsetHeight) * 1.02;
    // Layout offsets ignore transforms, so the target is stable however far the scrub has run.
    const within = () => { let x = 0, y = 0, node = plateImg; while (node && node !== hero) { x += node.offsetLeft; y += node.offsetTop; node = node.offsetParent; } return { x, y }; };
    const shiftX = () => innerWidth / 2 - (within().x + plateImg.offsetWidth / 2);
    const shiftY = () => innerHeight / 2 - (within().y + plateImg.offsetHeight / 2);
    const tl = gsapLib.timeline({ defaults: { ease: 'none' }, scrollTrigger: { trigger: hero, start: 'top top', end: '+=120%', pin: true, scrub: 0.6, anticipatePin: 1, invalidateOnRefresh: true } });
    tl.to(copy, { y: -90, opacity: 0, scale: 0.96, duration: 0.45, ease: 'power2.in' }, 0)
      .to([$('.hero-side'), plate.querySelector('figcaption'), hint].filter(Boolean), { opacity: 0, duration: 0.25 }, 0)
      .to(plateImg, { scale: fit, x: shiftX, y: shiftY, duration: 0.7, ease: 'power2.inOut' }, 0.05)
      .to(chrome, { scale: () => 1 / fit(), duration: 0.7, ease: 'power2.inOut' }, 0.05)
      .to({}, { duration: 0.25 });
    // The play button is hidden until the video is ready, so its CSS centering transform is set here explicitly.
    if (play) tl.fromTo(play, { xPercent: -50, yPercent: -50, scale: 1 }, { xPercent: -50, yPercent: -50, scale: () => 1 / fit(), duration: 0.7, ease: 'power2.inOut' }, 0.05);
    return () => { hero.classList.remove('is-scene'); hint.remove(); };
  });
}

/** Large screens: Browse by need becomes a pinned horizontal gallery that scrolls sideways with the wheel. */
function setupNeedsScene(grid) {
  if (!mm) return false;
  const section = $('#needs');
  const cards = $$('.need', grid);
  if (!section || cards.length < 4) return false;
  let active = false;
  mm.add(DESKTOP_MOTION, () => {
    active = true;
    grid.classList.add('is-track');
    section.classList.add('is-scene');
    const progress = el('div', 'needs-progress');
    progress.append(el('i'));
    grid.after(progress);
    const distance = () => Math.max(0, grid.scrollWidth - grid.parentElement.clientWidth);
    const tween = gsapLib.to(grid, { x: () => -distance(), ease: 'none', scrollTrigger: { trigger: section, start: 'top top', end: () => `+=${distance() + innerHeight * 0.3}`, pin: true, scrub: 0.5, anticipatePin: 1, invalidateOnRefresh: true, onUpdate: (self) => gsapLib.set(progress.firstElementChild, { scaleX: self.progress }) } });
    const visibleWidth = grid.parentElement.clientWidth;
    cards.forEach((card, i) => {
      const onScreenAtStart = card.offsetLeft < visibleWidth * 0.95;
      gsapLib.from(card, { y: 90, rotate: 2.5, opacity: 0, duration: 1.1, ease: 'power4.out', delay: onScreenAtStart ? i * 0.1 : 0, clearProps: CLEAR,
        scrollTrigger: onScreenAtStart ? { trigger: section, start: 'top 80%', once: true } : { trigger: card, containerAnimation: tween, start: 'left 95%', once: true } });
    });
    return () => { grid.classList.remove('is-track'); section.classList.remove('is-scene'); progress.remove(); active = false; };
  });
  return active;
}

/** The statement fills in word by word as it crosses the middle of the screen. */
function setupStatement() {
  const p = $('.statement');
  if (!p) return;
  const words = splitWords(p);
  gsapLib.set(words, { opacity: 0.14 });
  gsapLib.to(words, { opacity: 1, stagger: 0.04, ease: 'none', scrollTrigger: { trigger: p, start: 'top 78%', end: 'bottom 42%', scrub: 0.4 } });
}

/** The three steps light up in turn while their rule draws across. */
function setupSteps() {
  const steps = $$('.steps li');
  if (!steps.length) return;
  const tl = gsapLib.timeline({ scrollTrigger: { trigger: '.steps', start: 'top 78%', end: 'bottom 50%', scrub: 0.5 } });
  steps.forEach((li, i) => {
    const line = el('i', 'step-line');
    li.prepend(line);
    tl.from(line, { scaleX: 0, ease: 'none', duration: 1 }, i * 0.85)
      .from(li, { opacity: 0.28, y: 24, ease: 'none', duration: 0.7 }, i * 0.85);
  });
}

/** Ghost numerals sit behind every section and drift the other way as you scroll. */
function setupGhostIndex() {
  for (const section of $$('.section')) {
    if (!section.querySelector('.section-index')) continue;
    const ghost = el('span', 'ghost-index');
    ghost.setAttribute('aria-hidden', 'true');
    section.prepend(ghost);
    gsapLib.fromTo(ghost, { yPercent: 45 }, { yPercent: -45, ease: 'none', scrollTrigger: { trigger: section, start: 'top bottom', end: 'bottom top', scrub: 0.6 } });
  }
  renumberGhosts();
}
function renumberGhosts() {
  let n = 0;
  for (const section of $$('.section')) {
    const ghost = section.querySelector(':scope > .ghost-index');
    if (!ghost) continue;
    if (section.hidden) { ghost.textContent = ''; continue; }
    n += 1;
    ghost.textContent = String(n).padStart(2, '0');
  }
}

/** The header slips away while you read downward and returns the moment you scroll up. */
function setupMasthead() {
  const head = $('#masthead');
  if (!head) return;
  let hidden = false;
  const show = () => { if (!hidden) return; hidden = false; gsapLib.to(head, { yPercent: 0, duration: 0.5, ease: 'expo.out', overwrite: true }); };
  const hide = () => { if (hidden || document.body.classList.contains('nav-open')) return; hidden = true; gsapLib.to(head, { yPercent: -100, duration: 0.5, ease: 'power3.in', overwrite: true }); };
  window.ScrollTrigger.create({ start: 0, end: 'max', onUpdate: (self) => { if (self.direction === 1 && self.scroll() > innerHeight * 0.6) hide(); else show(); } });
  new MutationObserver(() => { if (document.body.classList.contains('nav-open')) show(); }).observe(document.body, { attributes: true, attributeFilter: ['class'] });
}

/** Buttons lean toward the pointer and spring back. */
function setupMagnetic() {
  mm.add(DESKTOP_MOTION, () => {
    const bound = [];
    for (const target of $$('.button, .nav-shop, .plate-control')) {
      const xTo = gsapLib.quickTo(target, 'x', { duration: 0.5, ease: 'power3' });
      const yTo = gsapLib.quickTo(target, 'y', { duration: 0.5, ease: 'power3' });
      const move = (e) => { const r = target.getBoundingClientRect(); xTo((e.clientX - (r.left + r.width / 2)) * 0.16); yTo((e.clientY - (r.top + r.height / 2)) * 0.16); };
      const leave = () => { xTo(0); yTo(0); };
      target.addEventListener('mousemove', move);
      target.addEventListener('mouseleave', leave);
      bound.push([target, move, leave]);
    }
    return () => { for (const [t, move, leave] of bound) { t.removeEventListener('mousemove', move); t.removeEventListener('mouseleave', leave); gsapLib.set(t, { clearProps: 'x,y' }); } };
  });
}

/** A ring follows the pointer with a little lag, opens over links and buttons, and hides over text fields. */
function setupCursor() {
  mm.add(DESKTOP_MOTION, () => {
    const cursor = el('div', 'cursor');
    cursor.setAttribute('aria-hidden', 'true');
    const ring = el('i', 'cursor-ring'); ring.append(el('b'));
    const dot = el('i', 'cursor-dot'); dot.append(el('b'));
    cursor.append(ring, dot);
    document.body.append(cursor);
    const rx = gsapLib.quickTo(ring, 'x', { duration: 0.45, ease: 'power3' }), ry = gsapLib.quickTo(ring, 'y', { duration: 0.45, ease: 'power3' });
    const dx = gsapLib.quickTo(dot, 'x', { duration: 0.12, ease: 'power3' }), dy = gsapLib.quickTo(dot, 'y', { duration: 0.12, ease: 'power3' });
    const move = (e) => { rx(e.clientX); ry(e.clientY); dx(e.clientX); dy(e.clientY); cursor.classList.add('is-on'); };
    const over = (e) => {
      const t = e.target instanceof Element ? e.target : null;
      cursor.classList.toggle('is-hover', Boolean(t && t.closest('a, button, label, select, summary, [role="button"], .need, .guide-card')));
      cursor.classList.toggle('is-text', Boolean(t && t.closest('input:not([type="checkbox"]):not([type="radio"]), textarea')));
    };
    const down = () => cursor.classList.add('is-down');
    const up = () => cursor.classList.remove('is-down');
    const out = () => cursor.classList.remove('is-on');
    window.addEventListener('mousemove', move, { passive: true });
    document.addEventListener('mouseover', over);
    document.addEventListener('mousedown', down);
    document.addEventListener('mouseup', up);
    document.documentElement.addEventListener('mouseleave', out);
    return () => { window.removeEventListener('mousemove', move); document.removeEventListener('mouseover', over); document.removeEventListener('mousedown', down); document.removeEventListener('mouseup', up); document.documentElement.removeEventListener('mouseleave', out); cursor.remove(); };
  });
}

/** Cards tilt toward the pointer in three dimensions and ease back when it leaves. */
function setupTilt() {
  mm.add(DESKTOP_MOTION, () => {
    const SEL = '.need, .guide-card, .zone-card, .help-card, .review';
    let current = null;
    const reset = (node) => gsapLib.to(node, { rotateX: 0, rotateY: 0, y: 0, duration: 0.8, ease: 'power3.out', clearProps: 'transform' });
    const move = (e) => {
      const t = e.target instanceof Element ? e.target.closest(SEL) : null;
      if (t !== current) {
        if (current) reset(current);
        current = t && (!t.dataset.motion || t.dataset.motion === 'done') ? t : null;
        if (current && current.parentElement) current.parentElement.classList.add('depth');
      }
      if (!current) return;
      const r = current.getBoundingClientRect();
      const px = (e.clientX - r.left) / r.width - 0.5;
      const py = (e.clientY - r.top) / r.height - 0.5;
      gsapLib.to(current, { rotateY: px * 4.5, rotateX: -py * 4.5, y: -5, duration: 0.6, ease: 'power2.out', transformPerspective: 1100 });
    };
    document.addEventListener('mousemove', move, { passive: true });
    return () => { document.removeEventListener('mousemove', move); if (current) reset(current); };
  });
}

function setupSmoothScroll() {
  if (!SMOOTH_SCROLL || typeof window.Lenis !== 'function' || matchMedia('(pointer: coarse)').matches) return;
  try {
    lenis = new window.Lenis({ lerp: 0.09, wheelMultiplier: 1, anchors: true, autoRaf: false });
    lenis.on('scroll', window.ScrollTrigger.update);
    if (arriving) lenis.stop();
    gsapLib.ticker.add((time) => lenis.raf(time * 1000));
    gsapLib.ticker.lagSmoothing(0);
    // Dialogs and the mobile menu lock the page; pause smooth scrolling while they are open.
    new MutationObserver(() => {
      const locked = document.body.classList.contains('cart-open') || document.body.classList.contains('nav-open');
      if (locked) lenis.stop(); else lenis.start();
    }).observe(document.body, { attributes: true, attributeFilter: ['class'] });
    for (const panel of $$('.cart, .quick, .compare, .sample-viewer, .resend, .region-list, .zones-track')) panel.setAttribute('data-lenis-prevent', '');
  } catch { lenis = null; }
}

function setupScrollAnimation() {
  // The arrival curtain is cleared first so a failed library load never leaves it on screen.
  const introDelay = setupArrival();
  if (!motionOn) return;
  setupSmoothScroll();
  const bar = $('#scroll-progress');
  if (bar) { bar.classList.add('is-on'); gsapLib.to(bar, { scaleX: 1, ease: 'none', scrollTrigger: { start: 0, end: 'max', scrub: 0.3 } }); }
  setupHeroIntro(introDelay);
  setupHeroScene();
  setupGhostIndex();
  setupStatement();
  setupSteps();
  setupMasthead();
  setupMagnetic();
  setupCursor();
  setupTilt();
  for (const head of $$('.section-head, .footer-cta-copy')) revealHeading(head);
  // Containers whose children animate individually (zones, reviews) are skipped here.
  for (const node of $$('.reveal')) if (!node.dataset.motion && !node.matches('.zones-track, .reviews-grid')) revealOnView(node, { y: 56 });
  revealBatch($$('.sample-grid .guide-card'));
  revealBatch($$('.features li'), { y: 48, rotate: 8, step: 0.12 });
  drawIcons($$('.features svg path, .features svg rect'), '.features');
  revealBatch($$('.faq-list details'), { y: 28, rotate: 0, scale: 1, step: 0.08, duration: 0.8 });
  revealBatch($$('.footer-grid > *'), { y: 30, rotate: 0, scale: 1, step: 0.1, duration: 0.9, start: 'top 95%' });
  revealBatch($$('.contents li'), { y: 24, rotate: 0, scale: 1, step: 0.08, duration: 0.7, start: 'top 95%' });
  // The marquee band slides in as the hero ends.
  const band = $('.marquee');
  if (band) gsapLib.from(band, { opacity: 0, y: 30, duration: 0.9, clearProps: CLEAR, scrollTrigger: { trigger: band, start: 'top 96%', once: true } });
  // Dark sections wipe in from the bottom edge as they arrive.
  for (const section of $$('.approach-section, .site-footer')) {
    gsapLib.from(section, { clipPath: 'inset(0 0 100% 0)', ease: 'none', scrollTrigger: { trigger: section, start: 'top 95%', end: 'top 35%', scrub: 0.4, once: true, onLeave: () => { section.style.clipPath = ''; } } });
  }
  // The giant footer wordmark drifts sideways as the footer scrolls.
  const footer = $('.site-footer');
  if (footer) gsapLib.fromTo([...footer.children].filter((c) => !c.classList.contains('footer-mark')), { yPercent: -14 }, { yPercent: 0, ease: 'none', scrollTrigger: { trigger: footer, start: 'top bottom', end: 'top 25%', scrub: 0.5 } });
  const mark = $('.footer-mark');
  if (mark) gsapLib.fromTo(mark, { xPercent: -60 }, { xPercent: -40, ease: 'none', scrollTrigger: { trigger: '.site-footer', start: 'top bottom', end: 'bottom bottom', scrub: 0.5 } });
  window.addEventListener('load', () => window.ScrollTrigger.refresh());
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => window.ScrollTrigger.refresh());
}
setupScrollAnimation();
