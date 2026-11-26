// Ali Marufi — small, dependency-free behaviour: mobile menu, fade-in, count-up, placeholders.
(function () {
  var doc = document;
  doc.documentElement.classList.add('js');

  // Mobile menu
  var btn = doc.querySelector('.menu-btn');
  var links = doc.querySelector('.nav-links');
  function setMenu(open) {
    links.classList.toggle('open', open);
    btn.setAttribute('aria-expanded', String(open));
    btn.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
    doc.body.style.overflow = open ? 'hidden' : '';
  }
  if (btn && links) {
    btn.addEventListener('click', function () { setMenu(!links.classList.contains('open')); });
    links.addEventListener('click', function (e) { if (e.target.closest('a')) setMenu(false); });
    doc.addEventListener('keydown', function (e) { if (e.key === 'Escape') setMenu(false); });
    window.addEventListener('resize', function () { if (window.innerWidth > 760) setMenu(false); });
  }

  // Placeholder links do nothing until a real address is filled in.
  doc.addEventListener('click', function (e) {
    var a = e.target.closest('a[data-placeholder]');
    if (a) e.preventDefault();
  });

  // Contact form is not connected to a mail service yet.
  var form = doc.querySelector('form[data-placeholder]');
  if (form) {
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      form.querySelector('[role="status"]').textContent =
        'This form is a placeholder and is not connected yet. Nothing was sent.';
    });
  }

  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // Count up once. Works on any element with a numeric data-count, e.g.
  // <span data-count="12" data-prefix="$" data-suffix=" million">$12 million</span>
  function countUp(el) {
    var target = parseFloat(el.getAttribute('data-count'));
    if (isNaN(target)) return; // still a placeholder
    var decimals = (String(el.getAttribute('data-count')).split('.')[1] || '').length;
    var prefix = el.getAttribute('data-prefix') || '';
    var suffix = el.getAttribute('data-suffix') || '';
    function render(v) {
      el.textContent = prefix + v.toLocaleString('en-US', {
        minimumFractionDigits: decimals, maximumFractionDigits: decimals
      }) + suffix;
    }
    if (reduce) { render(target); return; }
    var start = null, dur = 1600;
    function step(t) {
      if (start === null) start = t;
      var p = Math.min((t - start) / dur, 1);
      render(target * (1 - Math.pow(1 - p, 3)));
      if (p < 1) requestAnimationFrame(step); else render(target);
    }
    requestAnimationFrame(step);
  }

  var reveals = doc.querySelectorAll('.reveal');
  var counters = doc.querySelectorAll('[data-count]');
  if (!('IntersectionObserver' in window)) {
    reveals.forEach(function (el) { el.classList.add('in'); });
    return;
  }
  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (en) {
      if (!en.isIntersecting) return;
      if (en.target.hasAttribute('data-count')) countUp(en.target);
      else en.target.classList.add('in');
      io.unobserve(en.target);
    });
  }, { threshold: 0.15, rootMargin: '0px 0px -40px 0px' });
  reveals.forEach(function (el) { io.observe(el); });
  counters.forEach(function (el) { io.observe(el); });

  var y = doc.querySelector('[data-year]');
  if (y) y.textContent = new Date().getFullYear();
})();
