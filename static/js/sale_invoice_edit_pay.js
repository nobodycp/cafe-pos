/**
 * تعديل فاتورة — قسم الدفع (نفس منطق إتمام الدفع في السلة).
 */
(function () {
  "use strict";

  var AR_CODES = null;

  function readPayBoot(root) {
    var scopes = [];
    if (root && root.querySelector) scopes.push(root);
    if (root && root.closest) {
      var form = root.closest("[data-sale-edit-form]");
      if (form) scopes.push(form);
    }
    scopes.push(document);
    var el = null;
    for (var i = 0; i < scopes.length; i++) {
      el = scopes[i].querySelector && scopes[i].querySelector("#sale-edit-pay-boot-data");
      if (el && el.value) break;
      el = null;
    }
    if (!el) {
      el = document.getElementById("sale-edit-pay-boot-data");
    }
    if (!el || !el.value) {
      var scr = document.getElementById("sale-edit-pay-boot");
      if (scr && scr.textContent) {
        try {
          return JSON.parse(scr.textContent);
        } catch (e2) {
          return null;
        }
      }
      return null;
    }
    try {
      return JSON.parse(el.value);
    } catch (e) {
      return null;
    }
  }

  function pmRowsFromButtons(root) {
    var host = root || document;
    var out = [];
    host.querySelectorAll("#sale-edit-co-methods .sale-edit-co-pm-btn").forEach(function (btn) {
      var code = (btn.getAttribute("data-m") || "").trim();
      if (!code) return;
      out.push({
        code: code,
        label_ar: (btn.textContent || "").trim() || code,
        ledger: btn.getAttribute("data-ledger") || "",
        needsPayer: btn.getAttribute("data-needs-payer") === "1",
      });
    });
    return out;
  }

  function pmRows(root) {
    var boot = readPayBoot(root);
    if (boot && boot.pmRows && boot.pmRows.length) return boot.pmRows;
    var scraped = pmRowsFromButtons(root);
    if (scraped.length) return scraped;
    return typeof SALE_EDIT_PM_ROWS !== "undefined" ? SALE_EDIT_PM_ROWS : [];
  }

  function arCodes(root) {
    if (AR_CODES) return AR_CODES;
    AR_CODES = {};
    pmRows(root).forEach(function (r) {
      if ((r.ledger || "").toLowerCase() === "ar") AR_CODES[r.code] = true;
    });
    if (!Object.keys(AR_CODES).length) AR_CODES.credit = true;
    return AR_CODES;
  }

  function parseNum(v) {
    if (v == null || v === "") return 0;
    var n = parseFloat(String(v).replace(/,/g, ".").replace(/\s/g, ""));
    return isNaN(n) ? 0 : n;
  }

  function fmt2(n) {
    var x = Math.round(n * 100) / 100;
    return x.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function pmMeta(code, root) {
    var c = String(code || "").toLowerCase();
    var rows = pmRows(root);
    for (var i = 0; i < rows.length; i++) {
      if (rows[i].code === c) return rows[i];
    }
    return { code: c, label_ar: c, ledger: "", needsPayer: false };
  }

  function coSplitModeOn(root) {
    var chk = root.querySelector("#sale-edit-co-split-chk");
    return !!(chk && chk.checked);
  }

  function coSingleNeedsPayer(root) {
    if (coSplitModeOn(root)) return false;
    var b = root.querySelector("#sale-edit-co-methods .sale-edit-co-pm-btn.on");
    return !!(b && b.getAttribute("data-needs-payer") === "1");
  }

  function coAnySplitNeedsPayer(root) {
    if (!coSplitModeOn(root)) return false;
    var need = false;
    root.querySelectorAll("#sale-edit-co-split-tbody select.sale-edit-split-m").forEach(function (sel) {
      if (pmMeta(sel.value, root).needsPayer) need = true;
    });
    return need;
  }

  function coSingleIsAr(root) {
    if (coSplitModeOn(root)) return false;
    var b = root.querySelector("#sale-edit-co-methods .sale-edit-co-pm-btn.on");
    return !!(b && (b.getAttribute("data-ledger") || "") === "ar");
  }

  function coAnySplitIsAr(root) {
    if (!coSplitModeOn(root)) return false;
    var ar = false;
    root.querySelectorAll("#sale-edit-co-split-tbody select.sale-edit-split-m").forEach(function (sel) {
      if ((pmMeta(sel.value, root).ledger || "") === "ar") ar = true;
    });
    return ar;
  }

  function syncPayerPanel(root) {
    var p = root.querySelector("#sale-edit-co-payer-panel");
    if (!p) return;
    p.classList.toggle("hidden", !coSingleNeedsPayer(root) && !coAnySplitNeedsPayer(root));
  }

  function splitRowsSum(root) {
    var sum = 0;
    root.querySelectorAll("#sale-edit-co-split-tbody input.sale-edit-split-amt, #sale-edit-co-split-tbody input[type='number']").forEach(function (inp) {
      sum += parseNum(inp.value);
    });
    return Math.round(sum * 100) / 100;
  }

  function syncSplitHint(root, total) {
    var hint = root.querySelector("#sale-edit-co-split-hint");
    if (!hint) return;
    var sum = splitRowsSum(root);
    var diff = Math.round((sum - total) * 100) / 100;
    if (Math.abs(diff) < 0.02) {
      hint.textContent = "مجموع الأسطر يساوي الإجمالي ✓";
      hint.className = "mt-0.5 text-[10px] tabular-nums text-success min-h-[1rem]";
    } else {
      hint.textContent = "المجموع: " + fmt2(sum) + " — الإجمالي: " + fmt2(total) + " — فرق: " + fmt2(diff);
      hint.className = "mt-0.5 text-[10px] tabular-nums text-danger min-h-[1rem]";
    }
  }

  function syncPayHint(root, total) {
    var hint = root.querySelector("#sale-edit-pay-hint");
    if (!hint) return;
    if (coSplitModeOn(root)) {
      syncSplitHint(root, total);
      return;
    }
    hint.textContent = total > 0 ? "طريقة دفع واحدة بمبلغ " + fmt2(total) : "";
    hint.className = "text-[10px] mt-0.5 font-semibold tabular-nums text-muted min-h-[1rem]";
  }

  function setHeroTotal(root, total) {
    var el = root.querySelector("#sale-edit-co-hero-amt");
    if (el) el.textContent = fmt2(total);
    var hid = root.querySelector("#sale-edit-pay-amt-hid");
    if (hid) hid.value = total.toFixed(2);
  }

  function addSplitRow(root, code, amtStr) {
    var tbody = root.querySelector("#sale-edit-co-split-tbody");
    if (!tbody) return;
    var rows = pmRows(root);
    if (!rows.length) {
      if (typeof window.shellToast === "function") {
        window.shellToast("تعذر تحميل طرق الدفع — حدّث الصفحة", "error");
      }
      return;
    }
    var tr = document.createElement("tr");
    var sel = document.createElement("select");
    sel.className =
      "sale-edit-split-m w-full rounded border border-gray-300 bg-white text-[10px] dark:border-gray-600 dark:bg-gray-800";
    rows.forEach(function (r) {
      var o = document.createElement("option");
      o.value = r.code;
      o.textContent = r.label_ar;
      if (code && r.code === code) o.selected = true;
      sel.appendChild(o);
    });
    var am = document.createElement("input");
    am.type = "number";
    am.step = "0.01";
    am.min = "0";
    am.className =
      "sale-edit-split-amt w-full rounded border border-gray-300 bg-white px-1 py-0.5 text-end text-[10px] tabular-nums dark:border-gray-600 dark:bg-gray-800";
    am.dir = "ltr";
    am.value = amtStr || "";
    var rm = document.createElement("button");
    rm.type = "button";
    rm.className = "sale-edit-split-rm text-rose-600 font-bold text-sm leading-none border-0 bg-transparent cursor-pointer";
    rm.textContent = "×";
    rm.title = "حذف";
    var td1 = document.createElement("td");
    td1.className = "p-1";
    td1.appendChild(sel);
    var td2 = document.createElement("td");
    td2.className = "p-1";
    td2.appendChild(am);
    var td3 = document.createElement("td");
    td3.className = "p-1 text-center";
    td3.appendChild(rm);
    tr.appendChild(td1);
    tr.appendChild(td2);
    tr.appendChild(td3);
    tbody.appendChild(tr);
    sel.addEventListener("change", function () {
      syncPayerPanel(root);
      root.dispatchEvent(new CustomEvent("sale-edit-pay-change"));
    });
    rm.addEventListener("click", function () {
      tr.remove();
      root.dispatchEvent(new CustomEvent("sale-edit-pay-change"));
    });
    am.addEventListener("input", function () {
      root.dispatchEvent(new CustomEvent("sale-edit-pay-change"));
    });
  }

  function syncSplitMode(root, on, opts) {
    opts = opts || {};
    var sw = root.querySelector("#sale-edit-co-single-wrap");
    var wrap = root.querySelector("#sale-edit-co-split-wrap");
    if (sw) sw.classList.toggle("hidden", !!on);
    if (wrap) wrap.classList.toggle("hidden", !on);
    if (on && !opts.skipDefaultRow) {
      var tbody = root.querySelector("#sale-edit-co-split-tbody");
      if (tbody && !tbody.children.length) {
        var pr = pmRows(root);
        var def = pr[0] ? pr[0].code : "";
        addSplitRow(root, def, "");
      }
    } else if (!on) {
      var tbodyOff = root.querySelector("#sale-edit-co-split-tbody");
      if (tbodyOff) tbodyOff.innerHTML = "";
    }
    syncPayerPanel(root);
  }

  function readFormInvoiceTotal(form, init) {
    var totalEl = form && form.querySelector("#sale-edit-live-total");
    if (totalEl) {
      var fromLive = parseNum(String(totalEl.textContent || "").replace(/[^\d.-]/g, ""));
      if (fromLive > 0) return fromLive;
    }
    return parseNum((init && init.total) || 0);
  }

  function selectMethodBtn(root, code) {
    root.querySelectorAll("#sale-edit-co-methods .sale-edit-co-pm-btn").forEach(function (b) {
      var on = (b.getAttribute("data-m") || "") === code;
      b.classList.toggle("on", on);
    });
    var hid = root.querySelector("#sale-edit-pay-mode");
    if (hid) hid.value = code || "";
    syncPayerPanel(root);
  }

  function applyToHidden(root) {
    var useSplits = root.querySelector("#sale-edit-use-splits");
    var splitsJson = root.querySelector("#sale-edit-splits-json");
    var modeHid = root.querySelector("#sale-edit-pay-mode");
    var custId = root.querySelector("#sale-edit-cust-id");
    var custDraft = root.querySelector("#sale-edit-cust-draft");
    var pnHid = root.querySelector("#sale-edit-payer-name-hid");
    var phHid = root.querySelector("#sale-edit-payer-phone-hid");
    var pn = root.querySelector("#sale-edit-co-payer-name");
    var ph = root.querySelector("#sale-edit-co-payer-phone");
    var search = root.querySelector("#sale-edit-co-cust-search");

    if (pnHid) pnHid.value = pn ? String(pn.value || "").trim() : "";
    if (phHid)
      phHid.value = ph
        ? String(ph.value || "")
            .replace(/\D/g, "")
            .trim()
        : "";
    if (custDraft) custDraft.value = search ? String(search.value || "").trim() : "";

    if (coSplitModeOn(root)) {
      if (useSplits) useSplits.value = "1";
      var pairs = [];
      root.querySelectorAll("#sale-edit-co-split-tbody tr").forEach(function (tr) {
        var sel = tr.querySelector("select.sale-edit-split-m") || tr.querySelector("select");
        var inp = tr.querySelector("input.sale-edit-split-amt") || tr.querySelector("input[type='number']");
        if (!sel || !inp) return;
        var m = String(sel.value || "").trim().toLowerCase();
        var a = parseNum(inp.value);
        if (m && a > 0) pairs.push([m, Math.round(a * 100) / 100]);
      });
      if (splitsJson) splitsJson.value = pairs.length ? JSON.stringify(pairs) : "";
      if (modeHid) modeHid.value = "";
    } else {
      if (useSplits) useSplits.value = "";
      if (splitsJson) splitsJson.value = "";
      var onBtn = root.querySelector("#sale-edit-co-methods .sale-edit-co-pm-btn.on");
      if (modeHid) modeHid.value = onBtn ? onBtn.getAttribute("data-m") || "" : "";
    }
  }

  function validatePay(root, total) {
    if (total <= 0) {
      return "الإجمالي يجب أن يكون أكبر من صفر.";
    }
    var custId = root.querySelector("#sale-edit-cust-id");
    var search = root.querySelector("#sale-edit-co-cust-search");
    var hasId = custId && String(custId.value || "").trim();
    var draftName = search ? String(search.value || "").trim() : "";

    if (coSplitModeOn(root)) {
      var sum = splitRowsSum(root);
      if (Math.abs(sum - total) > 0.02) {
        return "مجموع أسطر الدفع المختلط يجب أن يساوي إجمالي الفاتورة.";
      }
      if (coAnySplitIsAr(root) && !hasId && draftName.length < 2) {
        return "للآجل: اختر عميلاً موجوداً أو اكتب اسماً (حرفان على الأقل).";
      }
      var anyRow = false;
      root.querySelectorAll("#sale-edit-co-split-tbody tr").forEach(function (tr) {
        var sel = tr.querySelector("select");
        var inp = tr.querySelector("input[type='number']");
        if (sel && inp && sel.value && parseNum(inp.value) > 0) anyRow = true;
      });
      if (!anyRow) return "أضف بند دفع واحداً على الأقل.";
    } else {
      if (!root.querySelector("#sale-edit-co-methods .sale-edit-co-pm-btn.on")) {
        return "اختر طريقة الدفع.";
      }
      if (coSingleIsAr(root) && !hasId && draftName.length < 2) {
        return "للآجل: اختر عميلاً موجوداً أو اكتب اسماً (حرفان على الأقل).";
      }
    }

    if (coSingleNeedsPayer(root) || coAnySplitNeedsPayer(root)) {
      var pn = root.querySelector("#sale-edit-co-payer-name");
      var ph = root.querySelector("#sale-edit-co-payer-phone");
      var n = pn ? String(pn.value || "").trim() : "";
      var p = ph ? String(ph.value || "").replace(/\D/g, "").trim() : "";
      if (n.length < 2 || p.length < 8) {
        return "أدخل اسم المحوّل ورقم الجوال (للتتبع) مع بنك / شبكة.";
      }
    }
    return null;
  }

  function setupPayerHints(root) {
    var inp = root.querySelector("#sale-edit-co-payer-name");
    var hits = root.querySelector("#sale-edit-co-payer-hits");
    var url = typeof SALE_EDIT_PAYER_HINTS_URL !== "undefined" ? SALE_EDIT_PAYER_HINTS_URL : "";
    if (!inp || !hits || !url || inp.getAttribute("data-hints") === "1") return;
    inp.setAttribute("data-hints", "1");
    var tmr;
    inp.addEventListener("input", function () {
      clearTimeout(tmr);
      var q = inp.value.trim();
      if (q.length < 1) {
        hits.classList.add("hidden");
        hits.innerHTML = "";
        return;
      }
      tmr = setTimeout(function () {
        fetch(url + "?q=" + encodeURIComponent(q), { credentials: "same-origin" })
          .then(function (r) {
            return r.json();
          })
          .then(function (data) {
            var items = data.results || data.hints || [];
            hits.innerHTML = "";
            if (!items.length) {
              hits.classList.add("hidden");
              return;
            }
            items.forEach(function (it) {
              var b = document.createElement("button");
              b.type = "button";
              b.className =
                "block w-full text-start px-2 py-1 hover:bg-gray-100 dark:hover:bg-gray-700 border-0 bg-transparent cursor-pointer text-[10px]";
              b.textContent = (it.name || it.payer_name || "") + (it.phone || it.payer_phone ? " — " + (it.phone || it.payer_phone) : "");
              b.addEventListener("mousedown", function (e) {
                e.preventDefault();
                inp.value = it.name || it.payer_name || "";
                var ph = root.querySelector("#sale-edit-co-payer-phone");
                if (ph) ph.value = it.phone || it.payer_phone || "";
                hits.classList.add("hidden");
              });
              hits.appendChild(b);
            });
            hits.classList.remove("hidden");
          })
          .catch(function () {
            hits.classList.add("hidden");
          });
      }, 220);
    });
    document.addEventListener("mousedown", function (ev) {
      if (!root.contains(ev.target)) hits.classList.add("hidden");
    });
  }

  function initFromPayments(root, form, init, total) {
    var pays = (init && init.payments) || [];
    var positive = pays.filter(function (p) {
      return parseNum(p.amount) > 0;
    });
    var payerFrom = positive.find(function (p) {
      return (p.payer_name || "").trim() || (p.payer_phone || "").trim();
    });
    if (payerFrom) {
      var pn = root.querySelector("#sale-edit-co-payer-name");
      var ph = root.querySelector("#sale-edit-co-payer-phone");
      if (pn) pn.value = payerFrom.payer_name || "";
      if (ph) ph.value = payerFrom.payer_phone || "";
    }

    if (positive.length > 1) {
      var chk = root.querySelector("#sale-edit-co-split-chk");
      if (chk) chk.checked = true;
      syncSplitMode(root, true, { skipDefaultRow: true });
      positive.forEach(function (p) {
        addSplitRow(root, p.method, parseNum(p.amount).toFixed(2));
      });
    } else if (positive.length === 1) {
      selectMethodBtn(root, positive[0].method);
    } else {
      var def = pmRows(root)[0] ? pmRows(root)[0].code : "cash";
      selectMethodBtn(root, def);
    }
    setHeroTotal(root, total);
    syncPayHint(root, total);
    syncPayerPanel(root);
  }

  function wire(root, form) {
    if (root.getAttribute("data-pay-wired") === "1") return;
    root.setAttribute("data-pay-wired", "1");
    AR_CODES = null;

    var boot = readPayBoot(form) || readPayBoot(root);
    var init =
      (boot && boot.payInit) ||
      (typeof SALE_EDIT_PAY_INIT !== "undefined" ? SALE_EDIT_PAY_INIT : {});
    var cur = init.currency || "";

    root.querySelectorAll("#sale-edit-co-methods .sale-edit-co-pm-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        selectMethodBtn(root, btn.getAttribute("data-m") || "");
        root.dispatchEvent(new CustomEvent("sale-edit-pay-change"));
      });
    });

    var splitChk = root.querySelector("#sale-edit-co-split-chk");
    if (splitChk) {
      splitChk.addEventListener("change", function () {
        syncSplitMode(root, splitChk.checked);
        root.dispatchEvent(new CustomEvent("sale-edit-pay-change"));
      });
    }
    var splitAdd = root.querySelector("#sale-edit-co-split-add");
    if (splitAdd) {
      splitAdd.addEventListener("click", function () {
        var def = pmRows(root)[0] ? pmRows(root)[0].code : "";
        addSplitRow(root, def, "");
        root.dispatchEvent(new CustomEvent("sale-edit-pay-change"));
      });
    }

    setupPayerHints(root);

    if (typeof CafeCustomerAutocomplete !== "undefined") {
      var searchUrl =
        (boot && boot.customersSearchUrl) ||
        (typeof SALE_EDIT_CUSTOMERS_SEARCH_URL !== "undefined" ? SALE_EDIT_CUSTOMERS_SEARCH_URL : "");
      var createUrl =
        (boot && boot.customerCreateUrl) ||
        (typeof SALE_EDIT_CUSTOMER_CREATE_URL !== "undefined" ? SALE_EDIT_CUSTOMER_CREATE_URL : "");
      var csrfInp = form.querySelector("[name=csrfmiddlewaretoken]");
      var csrf = csrfInp ? csrfInp.value : "";
      var custInp = root.querySelector("#sale-edit-co-cust-search");
      var custHits = root.querySelector("#sale-edit-co-cust-hits");
      var custHid = root.querySelector("#sale-edit-cust-id");
      if (searchUrl && custInp && custHits && custHid) {
        CafeCustomerAutocomplete.bind({
          searchUrl: searchUrl,
          input: custInp,
          hits: custHits,
          hidden: custHid,
          balanceEl: root.querySelector("#sale-edit-co-cust-balance"),
          currencySymbol: cur,
          showBalance: true,
          quickCreateUrl: createUrl,
          quickCreateCsrf: csrf,
          hitClass: "cust-ac-hit",
          useDisplayNone: true,
          useFixedPanel: true,
        });
      }
    }

    root.addEventListener("sale-edit-pay-change", function () {
      if (window.refreshSaleInvoiceEditPayTotal) window.refreshSaleInvoiceEditPayTotal(form);
    });

    form.addEventListener(
      "submit",
      function (e) {
        var totalEl = form.querySelector("#sale-edit-live-total");
        var total = parseNum(String((totalEl && totalEl.textContent) || "").replace(/[^\d.-]/g, ""));
        var err = validatePay(root, total);
        if (err) {
          e.preventDefault();
          e.stopImmediatePropagation();
          if (typeof window.shellToast === "function") window.shellToast(err, "error");
          else if (typeof window.showToast === "function") window.showToast(err, "error");
          else alert(err);
          return;
        }
        applyToHidden(root);
      },
      true
    );

    initFromPayments(root, form, init, readFormInvoiceTotal(form, init));
  }

  window.refreshSaleInvoiceEditPayTotal = function (form) {
    form = form || document.querySelector("[data-sale-edit-form]");
    if (!form) return;
    var sec = form.querySelector("[data-sale-edit-pay-section]");
    if (!sec) return;
    var init = typeof SALE_EDIT_PAY_INIT !== "undefined" ? SALE_EDIT_PAY_INIT : {};
    var total = readFormInvoiceTotal(form, init);
    setHeroTotal(sec, total);
    syncPayHint(sec, total);
  };

  window.initSaleInvoiceEditPay = function (root) {
    root = root || document;
    var form = root.querySelector ? root.querySelector("[data-sale-edit-form]") : null;
    if (!form) form = root.closest && root.closest("[data-sale-edit-form]");
    if (!form) return;
    var sec = form.querySelector("[data-sale-edit-pay-section]");
    if (!sec) return;
    wire(sec, form);
  };

  window.validateSaleInvoiceEditPay = function (form) {
    form = form || document.querySelector("[data-sale-edit-form]");
    if (!form) return null;
    var sec = form.querySelector("[data-sale-edit-pay-section]");
    if (!sec) return null;
    var totalEl = form.querySelector("#sale-edit-live-total");
    var total = parseNum(String((totalEl && totalEl.textContent) || "").replace(/[^\d.-]/g, ""));
    return validatePay(sec, total);
  };

  window.applySaleInvoiceEditPayHidden = function (form) {
    form = form || document.querySelector("[data-sale-edit-form]");
    if (!form) return;
    var sec = form.querySelector("[data-sale-edit-pay-section]");
    if (sec) applyToHidden(sec);
  };
})();
