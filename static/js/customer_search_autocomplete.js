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
    el.hits.classList.add('hidden');
    el.hits.style.display = 'none';
    el.hits.style.position = '';
    el.hits.style.left = '';
    el.hits.style.right = '';
    el.hits.style.top = '';
    el.hits.style.width = '';
    el.hits.style.zIndex = '';
    el.hits.style.maxHeight = '';
    el.hits._custAcPinInput = null;
  }

  function formatBalanceLine(c, currencySymbol) {
    if (c == null || c.balance == null || c.balance === '') return '';
    var n = parseFloat(String(c.balance).replace(',', '.'));
    if (isNaN(n)) return '';
    var cur = currencySymbol ? ' ' + currencySymbol : '';
    var abs = Math.abs(n).toFixed(2);
    var hint = c.balance_hint || (n > 0 ? 'عليه' : n < 0 ? 'له' : 'متوازن');
    if (n > 0) return hint + ': ' + abs + cur;
    if (n < 0) return hint + ': ' + abs + cur;
    return hint + ': 0.00' + cur;
  }

  function balanceColorClass(kind, n) {
    if (kind === 'debit' || n > 0) return 'text-red-600 dark:text-red-400';
    if (kind === 'credit' || n < 0) return 'text-emerald-600 dark:text-emerald-400';
    return 'text-muted';
  }

  function updateBalanceEl(el, c, currencySymbol) {
    if (!el || !el.balanceEl) return;
    var line = formatBalanceLine(c, currencySymbol);
    if (!line) {
      el.balanceEl.classList.add('hidden');
      el.balanceEl.textContent = '';
      return;
    }
    var n = parseFloat(String(c.balance).replace(',', '.'));
    el.balanceEl.textContent = line;
    el.balanceEl.className =
      'mt-0.5 text-[10px] font-extrabold tabular-nums leading-snug ' +
      balanceColorClass(c.balance_kind, n);
    el.balanceEl.classList.remove('hidden');
  }

  function buildHitButton(c, hitClass, onPick, el, currencySymbol, showBalance, nameOnly, buildLabel) {
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = hitClass;
    btn.setAttribute('data-cust-hit', '1');
    if (showBalance && c.balance != null && c.balance !== '') {
      var wrap = document.createElement('div');
      wrap.className = 'text-start leading-snug w-full';
      var t1 = document.createElement('div');
      t1.className = 'font-semibold text-[0.85rem]';
      t1.textContent = buildLabel(c);
      wrap.appendChild(t1);
      var t2 = document.createElement('div');
      var n = parseFloat(String(c.balance).replace(',', '.'));
      t2.className =
        'text-[10px] font-bold tabular-nums ' + balanceColorClass(c.balance_kind, isNaN(n) ? 0 : n);
      t2.textContent = formatBalanceLine(c, currencySymbol);
      if (t2.textContent) wrap.appendChild(t2);
      btn.appendChild(wrap);
    } else {
      btn.textContent = buildLabel(c);
    }
    btn.onclick = function () {
      onPick(c, el);
    };
    return btn;
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
   * @param {boolean} [opts.showBalance=false] — سطر الرصيد تحت الاسم في الاقتراحات
   * @param {string} [opts.currencySymbol=''] — رمز العملة بجانب الرصيد
   * @param {HTMLElement} [opts.balanceEl] — عنصر يعرض رصيد العميل بعد الاختيار
   * @param {boolean} [opts.useFixedPanel=false] — تثبيت القائمة فوق المودال (يتجاوز overflow)
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
    var showBalance = !!opts.showBalance;
    var currencySymbol = opts.currencySymbol || '';
    var balanceEl = opts.balanceEl || null;
    var useFixedPanel = !!opts.useFixedPanel;
    var userOnPick = typeof opts.onPick === 'function' ? opts.onPick : null;
    var onPick = function (c, elCtx) {
      defaultOnPick(c, elCtx);
      updateBalanceEl(elCtx, c, currencySymbol);
      if (userOnPick) userOnPick(c, elCtx);
    };
    var buildLabel =
      typeof opts.buildLabel === 'function'
        ? opts.buildLabel
        : function (c) {
            return defaultLabel(c, nameOnly);
          };

    var tmo;
    var el = { input: inp, hits: hits, hidden: hidden, balanceEl: balanceEl };

    function clampNum(n, lo, hi) {
      if (n < lo) return lo;
      if (n > hi) return hi;
      return n;
    }

    function pinHitsToInput() {
      if (!useFixedPanel || !inp.getBoundingClientRect) return;
      var rect = inp.getBoundingClientRect();
      var vw = window.innerWidth || document.documentElement.clientWidth || 640;
      var vh = window.innerHeight || document.documentElement.clientHeight || 480;
      var pad = 8;
      var minW = 220;
      var w = Math.max(rect.width, minW);
      w = clampNum(w, minW, vw - 2 * pad);
      var left = clampNum(rect.left, pad, vw - w - pad);
      var top = rect.bottom + 2;
      var maxH = Math.min(280, vh - top - pad);
      hits.style.position = 'fixed';
      hits.style.left = left + 'px';
      hits.style.right = 'auto';
      hits.style.top = top + 'px';
      hits.style.width = w + 'px';
      hits.style.zIndex = '50000';
      hits.style.maxHeight = (maxH > 72 ? maxH : Math.floor(vh * 0.35)) + 'px';
      hits._custAcPinInput = inp;
    }

    function unpinHits() {
      hits.style.position = '';
      hits.style.left = '';
      hits.style.right = '';
      hits.style.top = '';
      hits.style.width = '';
      hits.style.zIndex = '';
      hits.style.maxHeight = '';
      hits._custAcPinInput = null;
    }

    function showHitsPanel() {
      hits.classList.remove('hidden');
      if (useDisplay) hits.style.display = 'block';
      if (useFixedPanel) pinHitsToInput();
    }

    function hideHitsPanel() {
      hits.classList.add('hidden');
      if (useDisplay) hits.style.display = 'none';
      if (useFixedPanel) unpinHits();
    }

    inp.addEventListener('input', function () {
      clearTimeout(tmo);
      var q = inp.value.trim();
      if (q.length < 1) {
        hits.innerHTML = '';
        hideHitsPanel();
        hidden.value = '';
        updateBalanceEl(el, null, currencySymbol);
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
                /* قائمة اقتراحات POS — صفوف مثل نتائج البحث، وليس زر السندات (خارج اللوحة / أخضر) */
                var emptyMsg = document.createElement('div');
                emptyMsg.setAttribute('aria-hidden', 'true');
                emptyMsg.style.cssText =
                  'display:block;width:100%;padding:8px 12px;text-align:right;font-size:.85rem;border:none;color:var(--c-muted,#64748b);cursor:default;font-family:inherit';
                emptyMsg.textContent = 'لا توجد نتائج';
                hits.appendChild(emptyMsg);
                var qBtn = document.createElement('button');
                qBtn.type = 'button';
                qBtn.setAttribute('data-cust-quick-create', '1');
                qBtn.className = hitClass;
                qBtn.style.cssText =
                  'font-weight:700;color:var(--c-primary,#1e3a5f);border-top:1px solid rgba(148,163,184,.35)';
                qBtn.textContent = '+ إضافة عميل جديد';
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
                      onPick(
                        {
                          id: x.j.id,
                          name_ar: x.j.name_ar || q,
                          phone: '',
                          balance: x.j.balance != null ? x.j.balance : '0.00',
                          balance_hint: x.j.balance_hint || 'متوازن',
                          balance_kind: x.j.balance_kind || 'zero',
                        },
                        el
                      );
                    })
                    .catch(function () {
                      qBtn.disabled = false;
                      window.alert('تعذّر الاتصال بالخادم');
                    });
                };
                hits.appendChild(qBtn);
                showHitsPanel();
                return;
              }
              if (noResults) {
                hits.innerHTML = noResults;
                showHitsPanel();
              }
              return;
            }
            list.forEach(function (c) {
              hits.appendChild(
                buildHitButton(c, hitClass, onPick, el, currencySymbol, showBalance, nameOnly, buildLabel)
              );
            });
            showHitsPanel();
          })
          .catch(function () {
            hideHitsPanel();
          });
      }, debounce);
    });

    if (useFixedPanel) {
      var reposition = function () {
        if (!hits.classList.contains('hidden') && hits._custAcPinInput === inp) pinHitsToInput();
      };
      window.addEventListener('scroll', reposition, true);
      window.addEventListener('resize', reposition);
    }

    function hitsHasChoices() {
      return !!hits.querySelector('[data-cust-hit],[data-cust-quick-create]');
    }
    function hitsPanelOpen() {
      if (!useDisplay) return hitsHasChoices();
      return !hits.classList.contains('hidden') && hitsHasChoices();
    }

    inp.addEventListener('keydown', function (e) {
      /* Tab: اختيار أول عميل (كاشير + خريطة الطاولات) — لا يعتمد على display:none */
      if (e.key === 'Tab') {
        var first = hits.querySelector('[data-cust-hit]');
        if (!first) first = hits.querySelector('[data-cust-quick-create]');
        if (first) {
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
        hideHitsPanel();
      }, 200);
    });
    inp.addEventListener('focus', function () {
      if (hits.childElementCount > 0) showHitsPanel();
    });
  }

  w.CafeCustomerAutocomplete = { bind: bind };
})(typeof window !== 'undefined' ? window : this);
