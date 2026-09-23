const list = document.querySelector('#guide-list');
const message = document.querySelector('#catalog-message');
const controls = document.querySelector('#catalog-controls');
const search = document.querySelector('#guide-search');
const countryFilter = document.querySelector('#country-filter');
const guideCount = document.querySelector('#guide-count');
const navShop = document.querySelector('#nav-shop');

function renderGuides(guides) {
  list.replaceChildren();
  guideCount.textContent = `${guides.length} ${guides.length === 1 ? 'guide' : 'guides'} shown`;
  message.hidden = guides.length > 0;
  if (!guides.length) {
    const title = message.querySelector('h3');
    const detail = message.querySelector('p');
    if (search.value || countryFilter.value) {
      title.textContent = 'No guides match your search.';
      detail.textContent = 'Try a different country, state, or search term.';
    } else {
      title.textContent = 'The library is taking shape.';
      detail.textContent = 'Guides will appear here as they are added to the collection.';
    }
    return;
  }

  for (const guide of guides) {
    const card = document.createElement('a');
    card.className = 'guide-card';
    card.href = guide.stanUrl;
    card.target = '_blank';
    card.rel = 'noopener noreferrer';
    const kicker = document.createElement('span');
    kicker.className = 'card-kicker';
    kicker.textContent = `${guide.country} / ${guide.state}`;
    const title = document.createElement('h3');
    title.textContent = guide.title;
    const description = document.createElement('p');
    description.textContent = guide.description;
    const link = document.createElement('span');
    link.className = 'card-link';
    link.textContent = 'View guide on Stan ↗';
    const newTabNotice = document.createElement('span');
    newTabNotice.className = 'sr-only';
    newTabNotice.textContent = ' (opens in a new tab)';
    link.append(newTabNotice);
    card.append(kicker, title, description, link);
    list.append(card);
  }
}

fetch('guides.json')
  .then((response) => {
    if (!response.ok) throw new Error('Could not load the guide catalog');
    return response.json();
  })
  .then((data) => {
    if (/^https:\/\//.test(data.stanUrl || '')) {
      navShop.href = data.stanUrl;
      navShop.target = '_blank';
      navShop.rel = 'noopener noreferrer';
      const arrow = document.createElement('span');
      arrow.setAttribute('aria-hidden', 'true');
      arrow.textContent = '↗';
      const newTabNotice = document.createElement('span');
      newTabNotice.className = 'sr-only';
      newTabNotice.textContent = ' (opens in a new tab)';
      navShop.replaceChildren('Shop on Stan ', arrow, newTabNotice);
    }
    const guides = Array.isArray(data.guides)
      ? data.guides.filter((guide) => guide.title && guide.description && guide.country && guide.state && /^https:\/\//.test(guide.stanUrl || ''))
      : [];
    const countries = [...new Set(guides.map((guide) => guide.country))].sort();
    for (const country of countries) {
      const option = document.createElement('option');
      option.value = country;
      option.textContent = country;
      countryFilter.append(option);
    }
    controls.hidden = guides.length === 0;
    const update = () => {
      const query = search.value.trim().toLocaleLowerCase();
      renderGuides(guides.filter((guide) =>
        (!countryFilter.value || guide.country === countryFilter.value) &&
        (!query || `${guide.country} ${guide.state} ${guide.title}`.toLocaleLowerCase().includes(query))
      ));
    };
    search.addEventListener('input', update);
    countryFilter.addEventListener('change', update);
    update();
  })
  .catch(() => {
    message.querySelector('h3').textContent = 'The catalog could not load.';
    message.querySelector('p').textContent = 'Please refresh the page and try again.';
  });
