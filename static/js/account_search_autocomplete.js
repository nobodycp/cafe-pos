/**
 * بحث حسابات دليل الحسابات — shell API accounts/search.
 * AccountSearchAutocomplete.bind({ searchUrl, input, hits, hidden })
 */
(function (w) {
  'use strict';

  function accountLabel(a) {
    return (a.code || '') + ' — ' + (a.name_ar || '');
  }

  function parseResults(data) {
    if (!data) return [];
    if (Array.isArray(data.results)) return data.results;
    if (data.data && Array.isArray(data.data.results)) return data.data.results;
    return [];
  }

  function bind(opts) {
    if (!opts || !opts.searchUrl || !opts.input || !opts.hits || !opts.hidden) return;
    var inp = opts.input;
    var hits = opts.hits;
    var hidden = opts.hidden;
    var url = opts.searchUrl;
    var debounce = opts.debounce != null ? opts.debounce : 200;
    var hitClass = opts.hitClass || 'cust-ac-hit';
    var tmo;

    function showHitsPanel() {
      hits.classList.remove('hidden');
      hits.style.display = 'block';
    }

    function hideHitsPanel() {
      hits.classList.add('hidden');
      hits.style.display = 'none';
    }

    inp.addEventListener('input', function () {
      clearTimeout(tmo);
      var q = inp.value.trim();
      if (q.length < 1) {
        hits.innerHTML = '';
        hideHitsPanel();
        hidden.value = '';
        return;
      }
      tmo = setTimeout(function () {
        fetch(url + (url.indexOf('?') >= 0 ? '&' : '?') + 'q=' + encodeURIComponent(q))
          .then(function (r) {
            return r.json();
          })
          .then(function (data) {
            hits.innerHTML = '';
            var list = parseResults(data);
            if (!list.length) {
              var empty = document.createElement('div');
              empty.className = 'px-3 py-2 text-xs text-muted text-right';
              empty.textContent = 'لا توجد نتائج';
              hits.appendChild(empty);
              showHitsPanel();
              return;
            }
            list.forEach(function (a) {
              var btn = document.createElement('button');
              btn.type = 'button';
              btn.className = hitClass;
              btn.textContent = accountLabel(a);
              btn.addEventListener('mousedown', function (ev) {
                ev.preventDefault();
              });
              btn.onclick = function () {
                hidden.value = a.id;
                inp.value = accountLabel(a);
                hideHitsPanel();
              };
              hits.appendChild(btn);
            });
            showHitsPanel();
          })
          .catch(function () {
            hits.innerHTML = '';
            hideHitsPanel();
          });
      }, debounce);
    });

    document.addEventListener('click', function (ev) {
      if (ev.target === inp || hits.contains(ev.target)) return;
      hideHitsPanel();
    });
  }

  w.AccountSearchAutocomplete = { bind: bind, accountLabel: accountLabel };
})(window);
