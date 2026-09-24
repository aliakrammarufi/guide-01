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
const store = { name: 'Marufi Digital', currency: 'CAD', currencies: [], endpoint: '', regions: {}, siteUrl: '', discount: null, countryPages: false };
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
  return place ? `${guide.type} · ${place}` : guide.type;
}

function formatOf(guide) {
  if (guide.format) return guide.format;
  if (guide.type === 'Bundle') return `${guide.includes.length} titles`;
  return guide.type === 'Book' ? 'Digital book' : 'Digital guide';
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
function toggleSaved(guide) {
  saved = isSaved(guide) ? saved.filter((id) => id !== guide.id) : [...saved, guide.id];
  writeStore(SAVED_KEY, saved);
  showToast(isSaved(guide) ? `Saved “${guide.title}” for later` : `Removed “${guide.title}” from saved`);
  renderSaved();
  if (activeType === 'saved') update();
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
  $('#guides').scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
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
    item.append(cover, el('strong', 'related-title', g.title), el('span', 'related-price', `${g.type} · ${formatMoney(priceInfo(g).amount, priceInfo(g).currency)}`));
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
  const block = el('article', 'featured-card reveal is-visible');
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
      detail.textContent = 'Try a different country, state, title, or format.';
    } else {
      title.textContent = 'The library is taking shape.';
      detail.textContent = 'Guides and books will appear here as they are added to the collection.';
    }
    return;
  }
  for (const guide of rest) list.append(buildCard(guide));
  renderCart();
  renderSaved();
  renderCompareBar();
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
    (!query || `${guide.country} ${guide.state} ${guide.title} ${guide.type}`.toLocaleLowerCase().includes(query))
  );
  renderGuides(shown, filtering);
  const bar = $('#active-filter');
  const parts = [];
  if (countryFilter.value) parts.push(countryFilter.value);
  if (activeRegion) parts.push(activeRegion);
  if (activeType) parts.push(activeType === 'saved' ? 'Saved' : `${activeType}s`);
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
        item.append(button, el('span', 'region-type', ` ${guide.type}`), priceNode(guide, null, 'region-price'));
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
        $('#guides').scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
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
      const link = el('button', 'text-link', `Read the ${guide.type.toLowerCase()} `);
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
        $('#guides').scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
      });
      body.append(link);
    }
    card.append(visual, body);
    track.append(card);
  });
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
        category: guide.type,
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
  const type = /^book$/i.test(guide.type) ? 'Book' : /^bundle$/i.test(guide.type) ? 'Bundle' : 'Guide';
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
    const countries = [...new Set(catalog.map((guide) => guide.country).filter(Boolean))].sort((a, b) => a.localeCompare(b));
    for (const country of countries) {
      const option = document.createElement('option');
      option.value = country;
      option.textContent = country;
      countryFilter.append(option);
    }
    const types = new Set(catalog.map((guide) => guide.type));
    typeFilter.querySelector('[data-type="Bundle"]').hidden = !types.has('Bundle');
    typeFilter.querySelector('[data-type="Book"]').hidden = !types.has('Book');
    controls.hidden = catalog.length === 0;
    // Sample listings are a placeholder for the real catalog only.
    for (const element of $$('[data-sample-only]')) element.hidden = catalog.length > 0;
    // Deep links: ?country=Canada&state=Alberta&type=Book, or ?q=tokyo
    const params = new URLSearchParams(location.search);
    if (params.get('country') && countries.includes(params.get('country'))) countryFilter.value = params.get('country');
    if (params.get('state')) activeRegion = params.get('state');
    if (params.get('type') && ['Guide', 'Book', 'Bundle'].includes(params.get('type'))) activeType = params.get('type');
    if (params.get('q')) search.value = params.get('q');
    if (activeType) for (const chip of typeFilter.querySelectorAll('.chip')) chip.classList.toggle('is-active', chip.dataset.type === activeType);
    update();
    if ([...params.keys()].some((k) => ['country', 'state', 'type', 'q'].includes(k))) setTimeout(() => $('#guides').scrollIntoView(), 50);
    renderAtlas();
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

  const revealer = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (entry.isIntersecting) {
        entry.target.classList.add('is-visible');
        revealer.unobserve(entry.target);
      }
    }
  }, { rootMargin: '0px 0px -8% 0px', threshold: 0.1 });
  document.documentElement.classList.add('reveal-ready');
  for (const node of $$('.reveal')) revealer.observe(node);
} else {
  for (const node of $$('.reveal')) node.classList.add('is-visible');
}
