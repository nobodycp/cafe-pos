/**
 * بحث عملاء (POS) — نفس الـ API لـ customers_search.
 * الاستخدام: CafeCustomerAutocomplete.bind({ searchUrl, input, hits, hidden, ... })
 */
(function (w) {
  'use strict';

  function defaultLabel(c, nameOnly) {
    if (nameOnly) return c.name_ar || '';
    return (c.name_ar || '') + (c.phone ? ' — ' + c.phone : '');
  }

  function defaultOnPick(c, el) {
    el.hidden.value = c.id;
    el.input.value = c.name_ar || '';
    el.hits.style.display = 'none';
  }

  /**
   * @param {object} opts
   * @param {string} opts.searchUrl — URL كامل أو نسبي لـ pos:customers_search
   * @param {HTMLElement} opts.input
   * @param {HTMLElement} opts.hits
   * @param {HTMLElement} opts.hidden — حقل مخفي لـ customer_id
   * @param {number} [opts.debounce=200]
   * @param {boolean} [opts.nameOnlyHits=false]
   * @param {string} [opts.hitClass='cust-ac-hit']
   * @param {string} [opts.noResultsHtml] — HTML عند عدم وجود نتائج (اختياري)
   * @param {string} [opts.quickCreateUrl] — POST لإنشاء عميل سريع (نفس pos:customer_quick_create) مع name_ar
   * @param {string} [opts.quickCreateCsrf] — قيمة X-CSRFToken
   * @param {boolean} [opts.arrowNav=true] — أسهم + Enter
   * @param {function} [opts.onPick] — (customer, {input,hits,hidden})
   * @param {function} [opts.buildLabel] — (customer) => string
   * @param {boolean} [opts.useDisplayNone=true] — إخفاء hits بـ display:none عند الإغلاق
   */
  function bind(opts) {
    if (!opts || !opts.searchUrl || !opts.input || !opts.hits || !opts.hidden) return;
    var inp = opts.input;
    var hits = opts.hits;
    var hidden = opts.hidden;
    var url = opts.searchUrl;
    var debounce = opts.debounce != null ? opts.debounce : 200;
    var nameOnly = !!opts.nameOnlyHits;
    var hitClass = opts.hitClass || 'cust-ac-hit';
    var noResults = opts.noResultsHtml;
    var quickCreateUrl = opts.quickCreateUrl || '';
    var quickCreateCsrf = opts.quickCreateCsrf || '';
    var arrowNav = opts.arrowNav !== false;
    var useDisplay = opts.useDisplayNone !== false;
    var onPick = typeof opts.onPick === 'function' ? opts.onPick : defaultOnPick;
    var buildLabel =
      typeof opts.buildLabel === 'function'
        ? opts.buildLabel
        : function (c) {
            return defaultLabel(c, nameOnly);
          };

    var tmo;
    var el = { input: inp, hits: hits, hidden: hidden };

    inp.addEventListener('input', function () {
      clearTimeout(tmo);
      var q = inp.value.trim();
      if (q.length < 1) {
        hits.innerHTML = '';
        if (useDisplay) hits.style.display = 'none';
        return;
      }
      tmo = setTimeout(function () {
        fetch(url + (url.indexOf('?') >= 0 ? '&' : '?') + 'q=' + encodeURIComponent(q))
          .then(function (r) {
            return r.json();
          })
          .then(function (data) {
            hits.innerHTML = '';
            var list = data.results || [];
            if (!list.length) {
              if (quickCreateUrl && q.length >= 2) {
                hits.innerHTML = '';
                var row = document.createElement('div');
                row.style.padding = '8px 10px';
                row.style.fontSize = '.8rem';
                row.style.lineHeight = '1.35';
                var t1 = document.createTextNode('لا توجد نتائج — ');
                row.appendChild(t1);
                var qBtn = document.createElement('button');
                qBtn.type = 'button';
                qBtn.setAttribute('data-cust-quick-create', '1');
                qBtn.className = hitClass;
                qBtn.textContent = '+ عميل';
                qBtn.addEventListener('mousedown', function (ev) {
                  ev.preventDefault();
                });
                qBtn.onclick = function (ev) {
                  ev.preventDefault();
                  ev.stopPropagation();
                  if (!quickCreateUrl) return;
                  qBtn.disabled = true;
                  fetch(quickCreateUrl, {
                    method: 'POST',
                    headers: {
                      'Content-Type': 'application/x-www-form-urlencoded',
                      'X-CSRFToken': quickCreateCsrf,
                    },
                    body:
                      'name_ar=' +
                      encodeURIComponent(q) +
                      '&phone=' +
                      encodeURIComponent(''),
                  })
                    .then(function (r) {
                      return r.json().then(function (j) {
                        return { ok: r.ok, j: j };
                      });
                    })
                    .then(function (x) {
                      qBtn.disabled = false;
                      if (!x.ok || !x.j || !x.j.ok) {
                        window.alert((x.j && x.j.error) || 'تعذّر إنشاء العميل');
                        return;
                      }
                      onPick({ id: x.j.id, name_ar: x.j.name_ar || q, phone: '' }, el);
                    })
                    .catch(function () {
                      qBtn.disabled = false;
                      window.alert('تعذّر الاتصال بالخادم');
                    });
                };
                row.appendChild(qBtn);
                hits.appendChild(row);
                if (useDisplay) hits.style.display = 'block';
                return;
              }
              if (noResults) {
                hits.innerHTML = noResults;
                if (useDisplay) hits.style.display = 'block';
              }
              return;
            }
            list.forEach(function (c) {
              var btn = document.createElement('button');
              btn.type = 'button';
              btn.textContent = buildLabel(c);
              btn.className = hitClass;
              btn.setAttribute('data-cust-hit', '1');
              btn.onclick = function () {
                onPick(c, el);
              };
              hits.appendChild(btn);
            });
            if (useDisplay) hits.style.display = 'block';
          });
      }, debounce);
    });

    function hitsHasChoices() {
      return !!hits.querySelector('[data-cust-hit],[data-cust-quick-create]');
    }
    function hitsPanelOpen() {
      if (!useDisplay) return hitsHasChoices();
      return hits.style.display !== 'none' && hitsHasChoices();
    }

    inp.addEventListener('keydown', function (e) {
      /* Tab: اختيار أول عميل (كاشير + خريطة الطاولات) — لا يعتمد على display:none */
      if (e.key === 'Tab') {
        var first = hits.querySelector('[data-cust-hit]');
        if (first && !String(first.textContent || '').includes('لا توجد')) {
          e.preventDefault();
          first.click();
        }
        return;
      }
      if (arrowNav && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
        e.preventDefault();
        var items = hits.querySelectorAll('[data-cust-hit]');
        if (!items.length) return;
        var cur = -1;
        items.forEach(function (it, i) {
          if (it.classList.contains('ac-active')) cur = i;
        });
        items.forEach(function (it) {
          it.classList.remove('ac-active');
          it.style.background = '';
        });
        if (e.key === 'ArrowDown') cur = cur < items.length - 1 ? cur + 1 : 0;
        else cur = cur > 0 ? cur - 1 : items.length - 1;
        items[cur].classList.add('ac-active');
        items[cur].style.background = 'rgba(108,92,231,0.1)';
        items[cur].scrollIntoView({ block: 'nearest' });
        return;
      }
      if (e.key === 'Enter' && hitsPanelOpen()) {
        var active = hits.querySelector('.ac-active');
        var pick =
          active && active.getAttribute('data-cust-quick-create')
            ? active
            : active && active.matches('[data-cust-hit]')
              ? active
              : hits.querySelector('[data-cust-hit]') || hits.querySelector('[data-cust-quick-create]');
        if (pick) {
          e.preventDefault();
          pick.click();
        }
      }
    });

    inp.addEventListener('blur', function () {
      setTimeout(function () {
        if (useDisplay) hits.style.display = 'none';
      }, 200);
    });
    inp.addEventListener('focus', function () {
      if (hits.childElementCount > 0 && useDisplay) hits.style.display = 'block';
    });
  }

  w.CafeCustomerAutocomplete = { bind: bind };
})(typeof window !== 'undefined' ? window : this);
