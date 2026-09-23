/* Marufi Digital storefront — catalog, cart, Stripe checkout, and interface behaviour. */
const $ = (selector) => document.querySelector(selector);
const list = $('#guide-list');
const featuredBox = $('#featured');
const message = $('#catalog-message');
const controls = $('#catalog-controls');
const search = $('#guide-search');
const countryFilter = $('#country-filter');
const typeFilter = $('#type-filter');
const guideCount = $('#guide-count');
const CART_KEY = 'marufi-cart';
const TONES = ['clay', 'pine', 'ink', 'gold'];
const store = { currency: 'CAD', checkoutEndpoint: '' };
let catalog = [];
let activeType = '';

/* ---------- Helpers ---------- */
function isHttps(value) {
  try {
    return new URL(value).protocol === 'https:';
  } catch {
    return false;
  }
}

function isSafeImage(value) {
  if (typeof value !== 'string' || !value.trim()) return false;
  if (/^(assets|covers)\//.test(value) && !value.includes('..')) return true;
  return isHttps(value);
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function money(amount) {
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency: store.currency, minimumFractionDigits: Number.isInteger(amount) ? 0 : 2 }).format(amount);
  } catch {
    return `${store.currency} ${amount}`;
  }
}

function placeOf(guide) {
  return [guide.country, guide.state].filter(Boolean).join(' / ');
}

function kickerOf(guide) {
  const place = placeOf(guide);
  return place ? `${guide.type} · ${place}` : guide.type;
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

function canBuy(guide) {
  return Boolean(guide.paymentLink || (guide.priceId && store.checkoutEndpoint));
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

/* ---------- Stripe checkout ---------- */
let checkoutBusy = false;

async function startCheckout(guides, button) {
  if (checkoutBusy) return;
  const ids = guides.map((guide) => guide.priceId).filter(Boolean);
  if (!store.checkoutEndpoint || !ids.length) {
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
    const response = await fetch(store.checkoutEndpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items: ids })
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

function buyNow(guide, button) {
  if (guide.paymentLink) {
    window.location.assign(guide.paymentLink);
    return;
  }
  startCheckout([guide], button);
}

/* ---------- Cart state ---------- */
function readCart() {
  try {
    const stored = JSON.parse(localStorage.getItem(CART_KEY) || '[]');
    return Array.isArray(stored) ? stored.filter((id) => typeof id === 'string') : [];
  } catch {
    return [];
  }
}

function writeCart(ids) {
  try {
    localStorage.setItem(CART_KEY, JSON.stringify(ids));
  } catch {
    /* Private mode or storage disabled: the cart lives for this page view only. */
  }
}

let cart = readCart();
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
let lastFocus;

function inCart(id) {
  return cart.includes(id);
}

function addToCart(guide) {
  if (inCart(guide.id)) {
    openCart();
    return;
  }
  cart = [...cart, guide.id];
  writeCart(cart);
  renderCart();
  showToast(`Added “${guide.title}” to your cart`);
  cartCount.classList.remove('is-bumped');
  void cartCount.offsetWidth;
  cartCount.classList.add('is-bumped');
}

function removeFromCart(id) {
  cart = cart.filter((item) => item !== id);
  writeCart(cart);
  renderCart();
}

function renderCart() {
  const guides = cart.map((id) => catalog.find((guide) => guide.id === id)).filter(Boolean);
  if (guides.length !== cart.length) {
    cart = guides.map((guide) => guide.id);
    writeCart(cart);
  }
  cartCount.textContent = String(guides.length);
  cartCount.hidden = guides.length === 0;
  cartOpenButton.setAttribute('aria-label', guides.length ? `Cart, ${guides.length} ${guides.length === 1 ? 'title' : 'titles'}` : 'Cart, empty');
  cartEmpty.hidden = guides.length > 0;
  cartFoot.hidden = guides.length === 0;
  cartItems.replaceChildren();

  const singleCheckout = Boolean(store.checkoutEndpoint) && guides.every((guide) => guide.priceId);
  for (const guide of guides) {
    const item = el('li', 'cart-item');
    const thumb = el('div', 'cart-thumb');
    thumb.append(coverFor(guide));
    const body = el('div', 'cart-item-body');
    body.append(el('span', 'card-kicker', kickerOf(guide)), el('h3', null, guide.title), el('span', 'cart-item-price', money(guide.price)));
    const actions = el('div', 'cart-item-actions');
    if (!singleCheckout && canBuy(guide)) {
      const buy = el('button', 'button', 'Buy with Stripe');
      buy.type = 'button';
      buy.addEventListener('click', () => buyNow(guide, buy));
      actions.append(buy);
    }
    const remove = el('button', 'cart-remove', 'Remove');
    remove.type = 'button';
    remove.setAttribute('aria-label', `Remove ${guide.title} from cart`);
    remove.addEventListener('click', () => removeFromCart(guide.id));
    actions.append(remove);
    body.append(actions);
    item.append(thumb, body);
    cartItems.append(item);
  }

  cartSummaryLabel.textContent = `${guides.length} ${guides.length === 1 ? 'title' : 'titles'}`;
  cartTotal.textContent = money(guides.reduce((total, guide) => total + guide.price, 0));
  cartCheckout.hidden = !singleCheckout;
  cartNote.textContent = singleCheckout
    ? 'One secure Stripe payment for everything in your cart. Your cart is saved on this device only.'
    : 'Each title has its own secure Stripe checkout. Your cart is saved on this device only.';

  for (const button of document.querySelectorAll('[data-add]')) {
    const added = inCart(button.dataset.add);
    button.classList.toggle('is-added', added);
    button.textContent = added ? 'In cart ✓' : 'Add to cart';
  }
}

function openCart() {
  closeQuick();
  setMenu(false);
  lastFocus = document.activeElement;
  cartPanel.hidden = false;
  cartOverlay.hidden = false;
  void cartPanel.offsetWidth; // commit display change before the transition starts
  cartPanel.classList.add('is-open');
  cartOverlay.classList.add('is-open');
  document.body.classList.add('cart-open');
  cartOpenButton.setAttribute('aria-expanded', 'true');
  $('#cart-close').focus();
}

function closeCart() {
  if (cartPanel.hidden) return;
  cartPanel.classList.remove('is-open');
  cartOverlay.classList.remove('is-open');
  document.body.classList.remove('cart-open');
  cartOpenButton.setAttribute('aria-expanded', 'false');
  const finish = () => {
    cartPanel.hidden = true;
    cartOverlay.hidden = true;
  };
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) finish(); else setTimeout(finish, 450);
  if (lastFocus && typeof lastFocus.focus === 'function') lastFocus.focus();
}

cartOpenButton.addEventListener('click', openCart);
$('#footer-cart').addEventListener('click', openCart);
$('#cart-close').addEventListener('click', closeCart);
cartOverlay.addEventListener('click', closeCart);
$('#cart-browse').addEventListener('click', closeCart);
cartCheckout.addEventListener('click', () => {
  const guides = cart.map((id) => catalog.find((guide) => guide.id === id)).filter(Boolean);
  startCheckout(guides, cartCheckout);
});
$('#cart-clear').addEventListener('click', () => {
  cart = [];
  writeCart(cart);
  renderCart();
  showToast('Cart cleared');
});

/* ---------- Quick view ---------- */
const quick = $('#quick');
const quickOverlay = $('#quick-overlay');
let quickGuide = null;
let quickLastFocus;

function openQuick(guide) {
  quickGuide = guide;
  quickLastFocus = document.activeElement;
  const cover = $('#quick-cover');
  cover.replaceChildren(coverFor(guide));
  if (guide.badge) cover.append(el('span', 'card-badge', guide.badge));
  $('#quick-kicker').textContent = kickerOf(guide);
  $('#quick-title').textContent = guide.title;
  $('#quick-desc').textContent = guide.longDescription || guide.description;
  const highlights = $('#quick-highlights');
  highlights.replaceChildren(...guide.highlights.map((text) => el('li', null, text)));
  highlights.hidden = guide.highlights.length === 0;
  $('#quick-format').textContent = guide.format || (guide.type === 'Book' ? 'Digital book' : 'Digital guide');
  $('#quick-price').textContent = money(guide.price);
  const add = $('#quick-add');
  add.dataset.add = guide.id;
  const buy = $('#quick-buy');
  buy.hidden = !canBuy(guide);
  renderCart();
  quick.hidden = false;
  quickOverlay.hidden = false;
  void quick.offsetWidth; // commit display change before the transition starts
  quick.classList.add('is-open');
  quickOverlay.classList.add('is-open');
  document.body.classList.add('cart-open');
  $('#quick-close').focus();
}

function closeQuick() {
  if (quick.hidden) return;
  quick.classList.remove('is-open');
  quickOverlay.classList.remove('is-open');
  document.body.classList.remove('cart-open');
  const finish = () => {
    quick.hidden = true;
    quickOverlay.hidden = true;
  };
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) finish(); else setTimeout(finish, 350);
  if (quickLastFocus && typeof quickLastFocus.focus === 'function') quickLastFocus.focus();
}

$('#quick-close').addEventListener('click', closeQuick);
quickOverlay.addEventListener('click', closeQuick);
$('#quick-buy').addEventListener('click', (event) => { if (quickGuide) buyNow(quickGuide, event.currentTarget); });
$('#quick-add').addEventListener('click', () => { if (quickGuide) addToCart(quickGuide); });

document.addEventListener('keydown', (event) => {
  const dialog = !cartPanel.hidden ? cartPanel : (!quick.hidden ? quick : null);
  if (!dialog) return;
  if (event.key === 'Escape') {
    closeCart();
    closeQuick();
    return;
  }
  if (event.key === 'Tab') {
    const focusable = [...dialog.querySelectorAll('a[href], button:not([disabled])')].filter((node) => !node.hidden && node.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
});

/* ---------- Catalog ---------- */
function buildActions(guide) {
  const actions = el('div', 'card-actions');
  const buy = el('button', 'button button-sm', 'Buy now');
  buy.type = 'button';
  buy.append(' ');
  const arrow = el('span', null, '→');
  arrow.setAttribute('aria-hidden', 'true');
  buy.append(arrow);
  buy.hidden = !canBuy(guide);
  buy.addEventListener('click', () => buyNow(guide, buy));
  const add = el('button', 'button-ghost', 'Add to cart');
  add.type = 'button';
  add.dataset.add = guide.id;
  add.addEventListener('click', () => addToCart(guide));
  actions.append(buy, add);
  return actions;
}

function buildCard(guide) {
  const card = el('article', 'guide-card');
  card.setAttribute('aria-label', guide.title);
  const cover = el('button', 'card-cover');
  cover.type = 'button';
  cover.setAttribute('aria-label', `Quick view: ${guide.title}`);
  cover.append(coverFor(guide));
  if (guide.badge) cover.append(el('span', 'card-badge', guide.badge));
  cover.append(el('span', 'card-peek', 'Quick view'));
  cover.addEventListener('click', () => openQuick(guide));

  const body = el('div', 'card-body');
  const meta = el('div', 'card-meta');
  meta.append(el('span', 'card-kicker', kickerOf(guide)), el('span', 'card-price', money(guide.price)));
  const title = el('h3');
  const titleButton = el('button', 'title-button', guide.title);
  titleButton.type = 'button';
  titleButton.addEventListener('click', () => openQuick(guide));
  title.append(titleButton);
  body.append(meta, title, el('p', null, guide.description), buildActions(guide));
  card.append(cover, body);
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
  meta.append(el('span', null, guide.format || (guide.type === 'Book' ? 'Digital book' : 'Digital guide')), el('strong', null, money(guide.price)));
  body.append(meta, buildActions(guide));
  block.append(cover, body);
  return block;
}

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
    if (filtering) {
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
}

function applyFooterLinks(data) {
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
}

function normalise(guide, index) {
  const type = /^book$/i.test(guide.type) ? 'Book' : 'Guide';
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
    price: Number(guide.price),
    priceId: typeof guide.priceId === 'string' && /^price_[A-Za-z0-9]+$/.test(guide.priceId) ? guide.priceId : '',
    paymentLink: isHttps(guide.paymentLink) ? guide.paymentLink : '',
    cover: isSafeImage(guide.cover) ? guide.cover : '',
    coverAlt: typeof guide.coverAlt === 'string' ? guide.coverAlt : '',
    badge: typeof guide.badge === 'string' ? guide.badge.trim().slice(0, 24) : '',
    featured: guide.featured === true
  };
}

fetch('guides.json', { cache: 'no-cache' })
  .then((response) => {
    if (!response.ok) throw new Error('Could not load the catalog');
    return response.json();
  })
  .then((data) => {
    if (typeof data.currency === 'string' && /^[A-Z]{3}$/.test(data.currency)) store.currency = data.currency;
    if (isHttps(data.checkoutEndpoint)) store.checkoutEndpoint = data.checkoutEndpoint;
    applyFooterLinks(data);
    catalog = Array.isArray(data.guides)
      ? data.guides.filter((guide, index) => {
          const valid = guide && guide.title && guide.description && Number.isFinite(Number(guide.price)) && Number(guide.price) >= 0 &&
            (isHttps(guide.paymentLink) || (typeof guide.priceId === 'string' && /^price_[A-Za-z0-9]+$/.test(guide.priceId)));
          if (!valid) console.warn(`Skipping incomplete guide at index ${index}: needs title, description, price, and a paymentLink or priceId`);
          return valid;
        }).map(normalise)
      : [];
    const countries = [...new Set(catalog.map((guide) => guide.country).filter(Boolean))].sort((a, b) => a.localeCompare(b));
    for (const country of countries) {
      const option = document.createElement('option');
      option.value = country;
      option.textContent = country;
      countryFilter.append(option);
    }
    const types = new Set(catalog.map((guide) => guide.type));
    typeFilter.hidden = types.size < 2;
    controls.hidden = catalog.length === 0;
    // Sample listings and their links are a placeholder for the real catalog only.
    for (const element of document.querySelectorAll('[data-sample-only]')) {
      element.hidden = catalog.length > 0;
    }
    const update = () => {
      const query = search.value.trim().toLocaleLowerCase();
      const filtering = Boolean(query || countryFilter.value || activeType);
      renderGuides(catalog.filter((guide) =>
        (!countryFilter.value || guide.country === countryFilter.value) &&
        (!activeType || guide.type === activeType) &&
        (!query || `${guide.country} ${guide.state} ${guide.title} ${guide.type}`.toLocaleLowerCase().includes(query))
      ), filtering);
    };
    search.addEventListener('input', update);
    countryFilter.addEventListener('change', update);
    typeFilter.addEventListener('click', (event) => {
      const chip = event.target.closest('.chip');
      if (!chip) return;
      activeType = chip.dataset.type;
      for (const other of typeFilter.querySelectorAll('.chip')) other.classList.toggle('is-active', other === chip);
      update();
    });
    update();
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
  for (const node of document.querySelectorAll('.reveal')) revealer.observe(node);
} else {
  for (const node of document.querySelectorAll('.reveal')) node.classList.add('is-visible');
}
