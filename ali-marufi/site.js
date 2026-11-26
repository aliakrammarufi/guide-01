// Ali Marufi — small, dependency-free behaviour.
(function () {
  var doc = document, root = doc.documentElement;
  root.classList.add('js');
  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // Mobile menu
  var nav = doc.querySelector('.nav');
  var btn = doc.querySelector('.menu-btn');
  var links = doc.querySelector('.nav-links');
  function setMenu(open) {
    links.classList.toggle('open', open);
    nav.classList.toggle('menu-open', open);
    btn.setAttribute('aria-expanded', String(open));
    btn.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
    doc.body.style.overflow = open ? 'hidden' : '';
  }
  btn.addEventListener('click', function () { setMenu(!links.classList.contains('open')); });
  links.addEventListener('click', function (e) { if (e.target.closest('a')) setMenu(false); });
  doc.addEventListener('keydown', function (e) { if (e.key === 'Escape') setMenu(false); });
  window.addEventListener('resize', function () { if (window.innerWidth > 820) setMenu(false); });

  // Links whose address has not been supplied yet do nothing.
  doc.addEventListener('click', function (e) {
    if (e.target.closest('a[data-pending]')) e.preventDefault();
  });

  // Count up once: <span data-count="12" data-prefix="$" data-suffix=" million">$12 million</span>
  function countUp(el) {
    var raw = el.getAttribute('data-count'), target = parseFloat(raw);
    if (isNaN(target)) return;
    var decimals = (raw.split('.')[1] || '').length;
    var prefix = el.getAttribute('data-prefix') || '', suffix = el.getAttribute('data-suffix') || '';
    function render(v) {
      el.textContent = prefix + v.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals }) + suffix;
    }
    if (reduce) { render(target); return; }
    var start = null, dur = 1800;
    function step(t) {
      if (start === null) start = t;
      var p = Math.min((t - start) / dur, 1);
      render(target * (1 - Math.pow(1 - p, 4)));
      if (p < 1) requestAnimationFrame(step); else render(target);
    }
    render(0); requestAnimationFrame(step);
  }

  // Stagger: children of [data-stagger] arrive one after another.
  doc.querySelectorAll('[data-stagger]').forEach(function (group) {
    group.querySelectorAll(':scope > .reveal').forEach(function (el, i) { el.style.transitionDelay = (i * 0.14) + 's'; });
  });

  var reveals = doc.querySelectorAll('.reveal');
  var counters = doc.querySelectorAll('[data-count]');
  function shown(el) {
    el.classList.add('in');
    setTimeout(function () { el.style.transitionDelay = ''; el.classList.add('done'); }, 2000);
  }
  if (!('IntersectionObserver' in window)) {
    reveals.forEach(shown);
  } else {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) return;
        if (en.target.hasAttribute('data-count')) countUp(en.target); else shown(en.target);
        io.unobserve(en.target);
      });
    }, { threshold: 0.12, rootMargin: '0px 0px -40px 0px' });
    reveals.forEach(function (el) { io.observe(el); });
    counters.forEach(function (el) { io.observe(el); });
  }

  var y = doc.querySelector('[data-year]');
  if (y) y.textContent = new Date().getFullYear();

  // Statement: split into words that light up as the reader scrolls through.
  var words = [];
  doc.querySelectorAll('.words').forEach(function (p) {
    (function split(node) {
      Array.prototype.slice.call(node.childNodes).forEach(function (n) {
        if (n.nodeType === 3) {
          var frag = doc.createDocumentFragment();
          n.textContent.split(/(\s+)/).forEach(function (part) {
            if (!part.trim()) { frag.appendChild(doc.createTextNode(part)); return; }
            var s = doc.createElement('span'); s.className = 'w'; s.textContent = part;
            frag.appendChild(s); words.push(s);
          });
          node.replaceChild(frag, n);
        } else if (n.nodeType === 1) split(n);
      });
    })(p);
    p._words = p.querySelectorAll('.w');
  });

  var bar = doc.querySelector('.progress');
  var hero = doc.querySelector('.hero');
  var drift = doc.querySelectorAll('[data-drift]');
  var fill = doc.querySelector('.timeline .fill');
  var lastY = window.scrollY, ticking = false;

  function onScroll() {
    ticking = false;
    var sy = window.scrollY, vh = window.innerHeight;
    var max = root.scrollHeight - vh;
    if (bar) bar.style.transform = 'scaleX(' + (max > 0 ? Math.min(sy / max, 1) : 0) + ')';

    // Nav: clear over the green hero, ivory afterwards; steps aside on the way down.
    var heroEnd = hero ? hero.offsetHeight - 80 : 0;
    nav.classList.toggle('solid', sy > heroEnd);
    if (!links.classList.contains('open') && !reduce) {
      if (sy > lastY + 4 && sy > 300) nav.classList.add('away');
      else if (sy < lastY - 4 || sy < 300) nav.classList.remove('away');
    }
    lastY = sy;

    doc.querySelectorAll('.words').forEach(function (p) {
      var r = p.getBoundingClientRect();
      var prog = (vh * 0.88 - r.top) / (r.height + vh * 0.42);
      var n = Math.round(Math.max(0, Math.min(1, prog)) * p._words.length);
      for (var i = 0; i < p._words.length; i++) p._words[i].classList.toggle('lit', reduce || i < n);
    });

    if (fill) {
      var tr = fill.parentNode.getBoundingClientRect();
      fill.style.transform = 'scaleY(' + Math.max(0, Math.min(1, (vh * 0.7 - tr.top) / tr.height)) + ')';
    }

    if (reduce) return;
    drift.forEach(function (el) {
      var r = el.getBoundingClientRect();
      if (r.bottom < 0 || r.top > vh) return;
      var mid = (r.top + r.height / 2 - vh / 2) / vh;
      el.style.transform = 'translate3d(0,' + (mid * parseFloat(el.getAttribute('data-drift'))).toFixed(1) + 'px,0)';
    });
  }
  window.addEventListener('scroll', function () {
    if (!ticking) { ticking = true; requestAnimationFrame(onScroll); }
  }, { passive: true });
  window.addEventListener('resize', onScroll);
  onScroll();
})();
