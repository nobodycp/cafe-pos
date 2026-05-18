/**
 * واجهة تقسيم الدفع المشتركة (مصروفات، شراء، خزينة، POS، …).
 * يُصدّر PaymentSplitsUI.bind(opts)
 */
(function (global) {
  'use strict';

  function money(v) {
    var n = parseFloat(v);
    return isNaN(n) ? 0 : n;
  }

  function defaultToast(m, type) {
    (global.shellToast || function (x) { alert(x); })(m, type || 'error');
  }

  /**
   * @param {Object} opts
   * @param {HTMLFormElement} [opts.form]
   * @param {HTMLInputElement} [opts.amountInput]
   * @param {Function} [opts.getTargetAmount]
   * @param {HTMLInputElement} [opts.splitToggle]
   * @param {HTMLElement} [opts.paySingleWrap]
   * @param {HTMLElement} [opts.splitsWrap]
   * @param {HTMLTableSectionElement} [opts.splitsTbody]
   * @param {HTMLInputElement} [opts.splitsJson]
   * @param {HTMLButtonElement} [opts.splitAddBtn]
   * @param {HTMLElement} [opts.splitSumHint]
   * @param {HTMLInputElement} [opts.methodHidden]
   * @param {NodeList|Array} [opts.payBtns]
   * @param {HTMLElement} [opts.stateNode]
   * @param {Array<{code,label_ar,ledger}>} opts.pmRows
   * @param {boolean} [opts.requireExactSum=true]
   * @param {boolean} [opts.rejectOverTarget=false]
   * @param {boolean} [opts.disableSubmitValidation=false]
   * @param {number} [opts.minRows=0]
   * @param {Function} [opts.splitEligible] — false يعطّل التقسيم (خزينة)
   * @param {Function} [opts.onChange]
   * @param {Function} [opts.syncHintCustom]
   * @param {Function} [opts.toastErr]
   */
  function bind(opts) {
    var form = opts.form;
    if (!form && !opts.disableSubmitValidation && !opts.splitsTbody) return null;

    var toastErr = opts.toastErr || function (m) { defaultToast(m, 'error'); };
    var pmRows = opts.pmRows || [];
    var amountInput = opts.amountInput;
    var splitToggle = opts.splitToggle;
    var paySingleWrap = opts.paySingleWrap;
    var splitsWrap = opts.splitsWrap;
    var splitsTbody = opts.splitsTbody;
    var splitsJson = opts.splitsJson;
    var splitAddBtn = opts.splitAddBtn;
    var splitSumHint = opts.splitSumHint;
    var methodHidden = opts.methodHidden;
    var payBtns = opts.payBtns || [];
    var stateNode = opts.stateNode;
    var selCls = opts.splitMethodSelectClass || 'expense-split-m';
    var amtCls = opts.splitAmountInputClass || 'expense-split-amt';
    var requireExactSum = opts.requireExactSum !== false;
    var rejectOverTarget = !!opts.rejectOverTarget;
    var minRows = opts.minRows || 0;
    var splitEligible = opts.splitEligible;
    var onChange = opts.onChange || function () {};

    function targetAmount() {
      if (typeof opts.getTargetAmount === 'function') return money(opts.getTargetAmount());
      return money(amountInput && amountInput.value);
    }

    function splitMode() {
      if (splitEligible && !splitEligible()) return false;
      return !!(splitToggle && splitToggle.checked);
    }

    function rowsSum() {
      if (!splitsTbody) return 0;
      var s = 0;
      splitsTbody.querySelectorAll('input.' + amtCls).forEach(function (inp) {
        s += money(inp.value);
      });
      return Math.round((s + Number.EPSILON) * 100) / 100;
    }

    function serialize() {
      if (!splitsJson) return;
      if (!splitMode()) {
        splitsJson.value = '';
        return;
      }
      var pairs = [];
      if (splitsTbody) {
        splitsTbody.querySelectorAll('tr').forEach(function (tr) {
          var sel = tr.querySelector('select.' + selCls);
          var am = tr.querySelector('input.' + amtCls);
          if (!sel || !am) return;
          var c = (sel.value || '').trim().toLowerCase();
          var v = money(am.value);
          if (c && v > 0) pairs.push([c, v]);
        });
      }
      splitsJson.value = pairs.length ? JSON.stringify(pairs) : '';
      onChange();
    }

    function syncHint() {
      if (typeof opts.syncHintCustom === 'function') {
        opts.syncHintCustom({ target: targetAmount(), sum: rowsSum(), splitMode: splitMode() });
        return;
      }
      if (!splitSumHint) return;
      var target = targetAmount();
      var sum = rowsSum();
      var r = target - sum;
      var tail = '';
      if (target <= 0) tail = ' — أدخل المبلغ أولاً';
      else if (Math.abs(r) > 0.005) tail = r > 0 ? (' — نقص: ' + r.toFixed(2)) : (' — زيادة: ' + (-r).toFixed(2));
      else tail = ' — متطابق';
      splitSumHint.textContent = 'المجموع: ' + sum.toFixed(2) + ' — المبلغ: ' + target.toFixed(2) + tail;
    }

    function addRow(methodCode, amountStr) {
      if (!splitsTbody || !pmRows.length) return;
      var tr = document.createElement('tr');
      tr.className = 'border-b border-gray-100 dark:border-gray-700';
      var tdM = document.createElement('td');
      tdM.className = 'p-1';
      var sel = document.createElement('select');
      sel.className =
        selCls +
        ' w-full min-w-0 rounded border border-gray-300 bg-white px-1 py-0.5 dark:border-gray-600 dark:bg-gray-900';
      pmRows.forEach(function (r) {
        var o = document.createElement('option');
        o.value = r.code;
        o.textContent = r.label_ar;
        if (r.code === (methodCode || '').toLowerCase()) o.selected = true;
        sel.appendChild(o);
      });
      tdM.appendChild(sel);
      var tdA = document.createElement('td');
      tdA.className = 'p-1 text-end';
      var inp = document.createElement('input');
      inp.type = 'number';
      inp.step = '0.01';
      inp.min = '0';
      inp.className =
        amtCls +
        ' w-full rounded border border-gray-300 px-1 py-0.5 text-end tabular-nums dark:border-gray-600 dark:bg-gray-900';
      inp.placeholder = '0.00';
      if (amountStr != null && amountStr !== '') inp.value = String(amountStr);
      tdA.appendChild(inp);
      var tdX = document.createElement('td');
      tdX.className = 'p-1 text-center';
      var rm = document.createElement('button');
      rm.type = 'button';
      rm.className = 'cursor-pointer border-0 bg-transparent text-[11px] font-bold text-danger';
      rm.textContent = '×';
      rm.addEventListener('click', function () {
        if (minRows > 0 && splitsTbody.querySelectorAll('tr').length <= minRows) return;
        tr.remove();
        serialize();
        syncHint();
      });
      tdX.appendChild(rm);
      tr.appendChild(tdM);
      tr.appendChild(tdA);
      tr.appendChild(tdX);
      splitsTbody.appendChild(tr);
      sel.addEventListener('change', function () {
        serialize();
        syncHint();
      });
      inp.addEventListener('input', function () {
        serialize();
        syncHint();
      });
      serialize();
      syncHint();
    }

    function selectPay(btn) {
      if (!btn || !methodHidden) return;
      methodHidden.value = btn.dataset.code || '';
      payBtns.forEach(function (b) {
        b.classList.toggle('pay-on', b === btn);
      });
      if (typeof opts.onPayMethodSelect === 'function') opts.onPayMethodSelect(btn);
      onChange();
    }

    payBtns.forEach(function (btn) {
      btn.addEventListener('click', function () {
        selectPay(btn);
      });
    });

    if (splitToggle) {
      splitToggle.addEventListener('change', function () {
        if (splitEligible && !splitEligible()) {
          splitToggle.checked = false;
          return;
        }
        if (splitToggle.checked) {
          if (paySingleWrap) paySingleWrap.classList.add('hidden');
          if (splitsWrap) splitsWrap.classList.remove('hidden');
          if (splitsTbody && !splitsTbody.children.length) {
            var m =
              (methodHidden && methodHidden.value) ||
              (pmRows[0] && pmRows[0].code) ||
              'cash';
            var t = targetAmount();
            addRow(m, t > 0 ? t.toFixed(2) : '');
          }
        } else {
          if (splitsWrap) splitsWrap.classList.add('hidden');
          if (paySingleWrap) paySingleWrap.classList.remove('hidden');
          if (splitsTbody) splitsTbody.innerHTML = '';
          if (splitsJson) splitsJson.value = '';
        }
        serialize();
        syncHint();
        onChange();
      });
    }

    if (splitAddBtn) {
      splitAddBtn.addEventListener('click', function () {
        if (!splitMode()) return;
        var m =
          (methodHidden && methodHidden.value) ||
          (pmRows[0] && pmRows[0].code) ||
          'cash';
        addRow(m, '');
        serialize();
        syncHint();
      });
    }

    if (amountInput) {
      amountInput.addEventListener('input', function () {
        serialize();
        syncHint();
      });
    }

    function hydrate() {
      var initialState = {};
      if (stateNode) {
        try {
          initialState = JSON.parse(stateNode.textContent.trim() || '{}') || {};
        } catch (e) {
          initialState = {};
        }
      }
      var useSp = initialState.use_payment_splits;
      if (useSp === '1' || useSp === 1 || useSp === true) {
        if (splitToggle) splitToggle.checked = true;
        if (paySingleWrap) paySingleWrap.classList.add('hidden');
        if (splitsWrap) splitsWrap.classList.remove('hidden');
        if (splitsTbody && initialState.payment_splits_json) {
          splitsTbody.innerHTML = '';
          try {
            var sp = JSON.parse(initialState.payment_splits_json);
            if (Array.isArray(sp)) {
              sp.forEach(function (item) {
                var c = Array.isArray(item) ? item[0] : item && item.method;
                var a = Array.isArray(item) ? item[1] : item && item.amount;
                if (c) addRow(String(c).toLowerCase(), a != null && a !== '' ? String(a) : '');
              });
            }
          } catch (e3) {}
        }
      } else if (initialState.payment_method) {
        var btn = Array.prototype.find.call(payBtns, function (b) {
          return b.dataset.code === initialState.payment_method;
        });
        if (btn) selectPay(btn);
        else if (methodHidden) methodHidden.value = initialState.payment_method;
      }
      if (!splitMode() && payBtns.length && (!methodHidden || !methodHidden.value)) {
        selectPay(payBtns[0]);
      }
      serialize();
      syncHint();
    }

    if (form && !opts.disableSubmitValidation) {
      form.addEventListener('submit', function (ev) {
        serialize();
        if (splitMode()) {
          var target = targetAmount();
          var sum = rowsSum();
          if (target <= 0 && requireExactSum) {
            ev.preventDefault();
            toastErr(opts.amountRequiredMsg || 'أدخل المبلغ.');
            return;
          }
          if (rejectOverTarget && sum - target > 0.005) {
            ev.preventDefault();
            toastErr(opts.sumOverMsg || 'مجموع أسطر الدفع أكبر من المبلغ المستهدف.');
            return;
          }
          if (requireExactSum && Math.abs(sum - target) > 0.005) {
            ev.preventDefault();
            toastErr(opts.sumMismatchMsg || 'مجموع أسطر الدفع يجب أن يساوي المبلغ.');
            return;
          }
          if (!splitsTbody || !splitsTbody.querySelector('tr')) {
            ev.preventDefault();
            toastErr(opts.emptySplitsMsg || 'أضف سطر دفع واحداً على الأقل.');
            return;
          }
        } else {
          if (splitsJson) splitsJson.value = '';
          if (methodHidden && !methodHidden.value) {
            ev.preventDefault();
            toastErr(opts.methodRequiredMsg || 'اختر طريقة الدفع.');
            return;
          }
        }
      });
    }

    function readPairs() {
      var pairs = [];
      if (!splitsTbody) return pairs;
      splitsTbody.querySelectorAll('tr').forEach(function (tr) {
        var sel = tr.querySelector('select.' + selCls);
        var am = tr.querySelector('input.' + amtCls);
        if (!sel || !am) return;
        var code = sel.value;
        var raw = String(am.value || '').replace(',', '.').trim();
        if (!raw) return;
        var v = parseFloat(raw);
        if (!(v > 0)) return;
        pairs.push([code, v]);
      });
      return pairs;
    }

    hydrate();

    return {
      splitMode: splitMode,
      serialize: serialize,
      syncHint: syncHint,
      rowsSum: rowsSum,
      targetAmount: targetAmount,
      addRow: addRow,
      readPairs: readPairs,
    };
  }

  global.PaymentSplitsUI = { bind: bind, money: money };
})(typeof window !== 'undefined' ? window : this);
