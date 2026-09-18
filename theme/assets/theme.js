/* ============================================================
   SIDEGLASS — theme scripts
   Vanilla JS, no dependencies. Progressive: every form posts
   normally if JS fails, so the store still sells without it.
   ============================================================ */
(function () {
  'use strict';

  var T = window.SideglassTheme || {};
  var routes = T.routes || {};
  var FREE_SHIPPING_THRESHOLD = 6000; // pence. Mirror of settings.free_shipping_threshold.

  /* ---------- money ---------- */
  function formatMoney(cents) {
    var fmt = T.moneyFormat || '£{{amount}}';
    var value = (cents / 100).toFixed(2);
    var parts = value.split('.');
    var whole = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ',');

    return fmt.replace(/\{\{\s*(\w+)\s*\}\}/g, function (_, name) {
      if (name === 'amount_no_decimals') return whole;
      if (name === 'amount_with_comma_separator') return whole + ',' + parts[1];
      if (name === 'amount_no_decimals_with_comma_separator') return whole;
      return whole + '.' + parts[1];
    });
  }

  /* ---------- tiny helpers ---------- */
  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  function announce(msg) {
    var region = $('#CartLiveRegion');
    if (region) region.textContent = msg;
  }

  /* ---------- cart drawer ---------- */
  var drawer = $('[data-drawer]');
  var scrim = $('[data-drawer-scrim]');
  var lastFocused = null;

  function openDrawer() {
    if (!drawer) return;
    lastFocused = document.activeElement;
    drawer.setAttribute('data-open', 'true');
    if (scrim) scrim.setAttribute('data-open', 'true');
    document.body.style.overflow = 'hidden';
    var close = $('[data-drawer-close]', drawer);
    if (close) close.focus();
  }

  function closeDrawer() {
    if (!drawer) return;
    drawer.setAttribute('data-open', 'false');
    if (scrim) scrim.setAttribute('data-open', 'false');
    document.body.style.overflow = '';
    if (lastFocused && lastFocused.focus) lastFocused.focus();
  }

  document.addEventListener('click', function (e) {
    if (e.target.closest('[data-drawer-open]')) { e.preventDefault(); openDrawer(); refreshCart(); }
    if (e.target.closest('[data-drawer-close]') || e.target.closest('[data-drawer-scrim]')) closeDrawer();
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && drawer && drawer.getAttribute('data-open') === 'true') closeDrawer();
  });

  /* keep focus inside the open drawer */
  if (drawer) {
    drawer.addEventListener('keydown', function (e) {
      if (e.key !== 'Tab') return;
      var focusables = $$('a[href], button:not([disabled]), input, select, textarea', drawer)
        .filter(function (el) { return el.offsetParent !== null; });
      if (!focusables.length) return;
      var first = focusables[0];
      var last = focusables[focusables.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    });
  }

  /* ---------- render cart ---------- */
  function renderCart(cart) {
    var body = $('[data-cart-body]');
    var foot = $('[data-cart-foot]');
    var counts = $$('[data-cart-count]');

    counts.forEach(function (el) {
      el.textContent = cart.item_count;
      el.hidden = cart.item_count === 0;
    });

    if (!body) return;

    if (!cart.items.length) {
      body.innerHTML =
        '<div class="drawer__empty">' +
        '<p>Your bag is empty.</p>' +
        '<p><a class="btn btn--ghost btn--sm" href="/collections/all">Browse the kit</a></p>' +
        '</div>';
      if (foot) foot.hidden = true;
      return;
    }

    if (foot) foot.hidden = false;

    body.innerHTML = cart.items.map(function (item) {
      var img = item.image
        ? '<img src="' + item.image.replace(/(\.[a-z]+)(\?|$)/i, '_160x$1$2') + '" alt="" loading="lazy" width="62" height="62">'
        : '';
      var variant = (item.variant_title && item.variant_title !== 'Default Title')
        ? '<span class="line__v">' + item.variant_title + '</span>' : '';
      return '' +
        '<div class="line">' +
          '<div class="line__img">' + img + '</div>' +
          '<div>' +
            '<a class="line__t" href="' + item.url + '">' + item.product_title + '</a>' +
            variant +
            '<span class="line__v">Qty ' + item.quantity + '</span>' +
            '<button class="line__rm" type="button" data-remove="' + item.key + '">Remove</button>' +
          '</div>' +
          '<div class="line__p">' + formatMoney(item.final_line_price) + '</div>' +
        '</div>';
    }).join('');

    var sub = $('[data-cart-subtotal]');
    if (sub) sub.textContent = formatMoney(cart.total_price);

    renderShippingBar(cart.total_price);
  }

  function renderShippingBar(total) {
    var bar = $('[data-ship-bar]');
    if (!bar) return;
    var remaining = FREE_SHIPPING_THRESHOLD - total;
    var text = $('[data-ship-text]', bar);
    var fill = $('[data-ship-fill]', bar);

    if (remaining <= 0) {
      if (text) text.innerHTML = '<strong>Free UK delivery unlocked.</strong>';
      if (fill) fill.style.width = '100%';
    } else {
      if (text) text.innerHTML = 'You’re <strong>' + formatMoney(remaining) + '</strong> from free UK delivery.';
      if (fill) fill.style.width = Math.min(100, (total / FREE_SHIPPING_THRESHOLD) * 100) + '%';
    }
  }

  function refreshCart() {
    return fetch(routes.cart_url + '.js', { headers: { 'Accept': 'application/json' } })
      .then(function (r) { return r.json(); })
      .then(renderCart)
      .catch(function () { /* drawer keeps its server-rendered state */ });
  }

  /* ---------- add to cart ---------- */
  document.addEventListener('submit', function (e) {
    var form = e.target.closest('[data-add-form]');
    if (!form) return;
    e.preventDefault();

    var btn = $('[data-add-btn]', form);
    var original = btn ? btn.textContent : '';
    if (btn) { btn.disabled = true; btn.textContent = 'Adding…'; }

    fetch(routes.cart_add_url + '.js', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
      body: JSON.stringify({
        items: [{
          id: Number(new FormData(form).get('id')),
          quantity: Number(new FormData(form).get('quantity') || 1)
        }]
      })
    })
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (res.status) throw new Error(res.description || res.message);
        announce('Added to bag.');
        return refreshCart().then(openDrawer);
      })
      .catch(function (err) {
        announce('Could not add to bag.');
        var msg = $('[data-add-error]', form);
        if (msg) { msg.textContent = err.message || 'Something went wrong. Please try again.'; msg.hidden = false; }
      })
      .finally(function () {
        if (btn) { btn.disabled = false; btn.textContent = original; }
      });
  });

  /* ---------- remove line ---------- */
  document.addEventListener('click', function (e) {
    var rm = e.target.closest('[data-remove]');
    if (!rm) return;
    rm.disabled = true;

    fetch(routes.cart_change_url + '.js', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
      body: JSON.stringify({ id: rm.getAttribute('data-remove'), quantity: 0 })
    })
      .then(function (r) { return r.json(); })
      .then(function (cart) { renderCart(cart); announce('Removed from bag.'); })
      .catch(function () { rm.disabled = false; });
  });

  /* ---------- bundle / variant picker ---------- */
  $$('[data-picker]').forEach(function (picker) {
    var form = picker.closest('form') || document;

    picker.addEventListener('change', function (e) {
      var input = e.target.closest('input[name="id"]');
      if (!input) return;

      var idField = $('[data-variant-id]', form);
      if (idField) idField.value = input.value;

      var price = $('[data-price]', form);
      if (price && input.dataset.price) price.textContent = input.dataset.price;

      var was = $('[data-price-was]', form);
      if (was) {
        if (input.dataset.compare) { was.textContent = input.dataset.compare; was.hidden = false; }
        else { was.hidden = true; }
      }

      var note = $('[data-price-note]', form);
      if (note) note.textContent = input.dataset.note || '';

      var btn = $('[data-add-btn]', form);
      if (btn) {
        var available = input.dataset.available !== 'false';
        btn.disabled = !available;
        btn.textContent = available ? (input.dataset.cta || T.strings.addToCart) : T.strings.soldOut;
      }
    });
  });

  /* ---------- quantity ---------- */
  document.addEventListener('click', function (e) {
    var step = e.target.closest('[data-qty-step]');
    if (!step) return;
    var input = $('input', step.parentElement);
    if (!input) return;
    var next = (parseInt(input.value, 10) || 1) + parseInt(step.getAttribute('data-qty-step'), 10);
    input.value = Math.max(1, next);
  });

  /* ---------- accordion ---------- */
  document.addEventListener('click', function (e) {
    var q = e.target.closest('.acc__q');
    if (!q) return;
    var expanded = q.getAttribute('aria-expanded') === 'true';
    q.setAttribute('aria-expanded', expanded ? 'false' : 'true');
    var panel = document.getElementById(q.getAttribute('aria-controls'));
    if (panel) panel.hidden = expanded;
  });

  /* ---------- gallery ---------- */
  document.addEventListener('click', function (e) {
    var thumb = e.target.closest('.product__thumb');
    if (!thumb) return;
    var gallery = thumb.closest('.product__gallery');
    var main = $('.product__main img', gallery);
    var img = $('img', thumb);
    if (main && img) {
      main.src = img.getAttribute('data-full') || img.src;
      main.alt = img.alt;
    }
    $$('.product__thumb', gallery).forEach(function (t) { t.setAttribute('aria-current', String(t === thumb)); });
  });

  /* ---------- mobile nav ---------- */
  document.addEventListener('click', function (e) {
    var toggle = e.target.closest('[data-nav-toggle]');
    if (!toggle) return;
    var nav = $('[data-nav]');
    if (!nav) return;
    var open = nav.getAttribute('data-open') === 'true';
    nav.setAttribute('data-open', open ? 'false' : 'true');
    toggle.setAttribute('aria-expanded', open ? 'false' : 'true');
  });

  /* ---------- boot ---------- */
  if ($('[data-cart-body]')) refreshCart();
})();
