/**
 * تعديل فاتورة البيع: معاينة المجاميع؛ النمط الكامل يضيف بحث أصناف ودفعات.
 */
(function () {
  function parseNum(v) {
    if (v == null || v === "") return 0;
    var s = String(v).replace(/,/g, ".").replace(/\s/g, "").trim();
    var n = parseFloat(s);
    return isNaN(n) ? 0 : n;
  }

  /* خصم: نسبة إن انتهت بـ % أو ٪، وإلا مبلغ ثابت — نفس آلية السلة. */
  function parseDiscountInput(raw, subtotal) {
    var t = String(raw == null ? "" : raw).replace(/\u066a/g, "%").replace(/٪/g, "%").trim();
    if (!t) return 0;
    var isPct = t.indexOf("%") >= 0;
    var core = t.replace(/%/g, "").trim();
    if (!core) return 0;
    var val = parseNum(core);
    if (val < 0) val = 0;
    if (isPct) {
      if (val > 100) val = 100;
      var d = (subtotal * val) / 100;
      return d > subtotal ? subtotal : d;
    }
    return val > subtotal ? subtotal : val;
  }

  function fmt2(n) {
    var x = Math.round(n * 100) / 100;
    return x.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function clampNum(n, lo, hi) {
    if (n < lo) return lo;
    if (n > hi) return hi;
    return n;
  }

  function unpinPanel(ac) {
    if (!ac) return;
    ac.style.position = "";
    ac.style.left = "";
    ac.style.right = "";
    ac.style.top = "";
    ac.style.width = "";
    ac.style.minWidth = "";
    ac.style.maxWidth = "";
    ac.style.zIndex = "";
    ac.style.maxHeight = "";
  }

  function hidePanel(ac) {
    if (!ac) return;
    ac.innerHTML = "";
    ac.classList.add("hidden");
    ac.style.display = "none";
    unpinPanel(ac);
  }

  function hideAc(tr) {
    hidePanel(tr && (tr._saleEditAc || tr.querySelector(".sale-edit-ac")));
  }

  function pinPanel(ac, inp) {
    if (!ac || !inp || !inp.getBoundingClientRect) return;
    var rect = inp.getBoundingClientRect();
    var vw = window.innerWidth || document.documentElement.clientWidth || 640;
    var vh = window.innerHeight || document.documentElement.clientHeight || 480;
    var pad = 8;
    var minW = Math.max(rect.width, 220);
    var w = clampNum(minW, 220, vw - 2 * pad);
    var left = clampNum(rect.left, pad, vw - w - pad);
    var top = rect.bottom + 2;
    var maxH = Math.min(240, vh - top - pad);
    ac.style.setProperty("position", "fixed", "important");
    ac.style.setProperty("left", left + "px", "important");
    ac.style.setProperty("right", "auto", "important");
    ac.style.setProperty("top", top + "px", "important");
    ac.style.setProperty("width", w + "px", "important");
    ac.style.setProperty("min-width", w + "px", "important");
    ac.style.setProperty("max-width", w + "px", "important");
    ac.style.setProperty("z-index", "50000", "important");
    ac.style.setProperty("max-height", (maxH > 72 ? maxH : Math.floor(vh * 0.35)) + "px", "important");
    ac.style.setProperty("display", "block", "important");
  }

  function showPanel(ac, inp) {
    ac.classList.remove("hidden");
    pinPanel(ac, inp);
  }

  function pickItem(it, ctx) {
    if (!it || !ctx) return;
    ctx.hid.value = String(it.id);
    ctx.searchInp.value = it.name_ar || "";
    if (it.price != null && ctx.priceInp) {
      var pr = parseFloat(String(it.price).replace(",", "."));
      if (!isNaN(pr)) ctx.priceInp.value = pr.toFixed(2);
    }
    hidePanel(ctx.ac);
    ctx.tr.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function wireProductRow(tr, searchUrl) {
    var hid = tr.querySelector(".sale-edit-line-product-id");
    var searchInp = tr.querySelector(".sale-edit-product-search");
    var ac = tr.querySelector(".sale-edit-ac") || tr._saleEditAc;
    var priceInp = tr.querySelector(".sale-edit-price");
    var tmr;
    if (!searchInp || !ac || !hid) return;
    if (tr._saleEditAcWired) return;
    tr._saleEditAcWired = true;
    tr._saleEditAc = ac;

    var ctx = { tr: tr, ac: ac, searchInp: searchInp, hid: hid, priceInp: priceInp };

    /* منع blur عند الضغط داخل اللوحة قبل أن تصل النقرة لزر الاختيار. */
    ac.addEventListener("mousedown", function (e) {
      e.preventDefault();
    });
    ac.addEventListener("pointerdown", function (e) {
      if (e.pointerType !== "mouse") e.preventDefault();
    });

    searchInp.addEventListener("input", function () {
      hid.value = "";
      clearTimeout(tmr);
      var q = searchInp.value.trim();
      if (q.length < 1) {
        hidePanel(ac);
        return;
      }
      tmr = setTimeout(function () {
        fetch(searchUrl + (searchUrl.indexOf("?") >= 0 ? "&" : "?") + "q=" + encodeURIComponent(q), {
          credentials: "same-origin",
        })
          .then(function (r) {
            return r.json();
          })
          .then(function (data) {
            var items = data.results || [];
            ac.innerHTML = "";
            if (!items.length) {
              hidePanel(ac);
              return;
            }
            items.forEach(function (it) {
              var b = document.createElement("button");
              b.type = "button";
              b.setAttribute("data-prod-hit", "1");
              b.className =
                "block w-full text-start px-2 py-1.5 hover:bg-gray-100 dark:hover:bg-gray-700 border-0 bg-transparent cursor-pointer text-gray-900 dark:text-gray-100 text-[11px]";
              b.textContent = it.name_ar + (it.category ? " — " + it.category : "");
              b.addEventListener("mousedown", function (e) {
                e.preventDefault();
              });
              b.addEventListener("click", function (e) {
                e.preventDefault();
                pickItem(it, ctx);
              });
              ac.appendChild(b);
            });
            requestAnimationFrame(function () {
              showPanel(ac, searchInp);
            });
          })
          .catch(function () {
            hidePanel(ac);
          });
      }, 180);
    });

    searchInp.addEventListener("keydown", function (e) {
      if (e.key === "Tab") {
        var first = ac.querySelector("[data-prod-hit]");
        if (first) {
          e.preventDefault();
          first.click();
        }
        return;
      }
      if (e.key === "Enter") {
        var firstE = ac.querySelector("[data-prod-hit]");
        if (firstE && !ac.classList.contains("hidden")) {
          e.preventDefault();
          firstE.click();
        }
        return;
      }
      if (e.key === "Escape") {
        hidePanel(ac);
      }
    });

    searchInp.addEventListener("blur", function () {
      setTimeout(function () {
        hidePanel(ac);
      }, 200);
    });
    searchInp.addEventListener("focus", function () {
      if (ac.childElementCount > 0) showPanel(ac, searchInp);
    });

    var reposition = function () {
      if (!ac.classList.contains("hidden") && ac.childElementCount > 0) pinPanel(ac, searchInp);
    };
    window.addEventListener("scroll", reposition, true);
    window.addEventListener("resize", reposition);
  }

  function recalcFull(form) {
    var cur = form.getAttribute("data-currency") || "";
    var rows = form.querySelectorAll("[data-sale-edit-line-full]");
    var sum = 0;
    rows.forEach(function (tr) {
      var hid = tr.querySelector(".sale-edit-line-product-id");
      var qi = tr.querySelector(".sale-edit-qty");
      var pi = tr.querySelector(".sale-edit-price");
      var pid = hid && hid.value.trim();
      var q = parseNum(qi && qi.value);
      var p = parseNum(pi && pi.value);
      var gross = pid ? q * p : 0;
      sum += gross;
      var totEl = tr.querySelector(".sale-edit-line-total");
      if (totEl) totEl.textContent = pid ? fmt2(gross) + (cur ? " " + cur : "") : "—";
    });
    var discInp = form.querySelector("#sale-edit-discount-input");
    var disc = discInp
      ? parseDiscountInput(discInp.value, sum)
      : parseNum(form.getAttribute("data-discount"));
    var svc = parseNum(form.getAttribute("data-service"));
    var tax = parseNum(form.getAttribute("data-tax"));
    var total = sum - disc + svc + tax;
    if (total < 0) total = 0;
    var discEl = form.querySelector("#sale-edit-live-discount");
    if (discEl) discEl.textContent = fmt2(disc);
    form.setAttribute("data-discount", String(disc));
    var totalEl = form.querySelector("#sale-edit-live-total");
    if (totalEl) totalEl.textContent = fmt2(total) + (cur ? " " + cur : "");

    /* تحديث البطاقة الخضراء + تلميح أسطر الدفع/المختلط بالإجمالي الجديد. */
    if (window.refreshSaleInvoiceEditPayTotal) {
      window.refreshSaleInvoiceEditPayTotal(form);
    }
    var sec = form.querySelector("[data-sale-edit-pay-section]");
    if (sec) sec.dispatchEvent(new CustomEvent("sale-edit-pay-change"));
  }

  function renumberLineRows(tbody) {
    if (!tbody) return;
    tbody.querySelectorAll("[data-sale-edit-line-full]").forEach(function (tr, i) {
      var num = tr.querySelector(".sale-edit-line-num");
      if (num) num.textContent = String(i + 1);
    });
  }

  function addLineRow(form) {
    var tbody = form.querySelector("#sale-edit-lines-tbody");
    var tpl = form.querySelector("#sale-edit-line-row-template");
    if (!tbody || !tpl || !tpl.content) return;
    var nextIdx = parseInt(tbody.getAttribute("data-next-idx") || "0", 10);
    if (isNaN(nextIdx) || nextIdx < 0) nextIdx = tbody.querySelectorAll("[data-sale-edit-line-full]").length;
    if (nextIdx >= 50) {
      if (typeof window.shellToast === "function") window.shellToast("الحد الأقصى 50 سطراً", "info");
      else if (typeof window.showToast === "function") window.showToast("الحد الأقصى 50 سطراً", "info");
      return;
    }
    var tr = tpl.content.firstElementChild.cloneNode(true);
    tr.setAttribute("data-idx", String(nextIdx));
    var hid = tr.querySelector(".sale-edit-line-product-id");
    var searchInp = tr.querySelector(".sale-edit-product-search");
    var qtyInp = tr.querySelector(".sale-edit-qty");
    var priceInp = tr.querySelector(".sale-edit-price");
    if (hid) hid.name = "line_" + nextIdx + "_product";
    if (searchInp) searchInp.name = "line_" + nextIdx + "_search";
    if (qtyInp) qtyInp.name = "line_" + nextIdx + "_qty";
    if (priceInp) priceInp.name = "line_" + nextIdx + "_price";
    tbody.appendChild(tr);
    tbody.setAttribute("data-next-idx", String(nextIdx + 1));
    renumberLineRows(tbody);
    wireProductRow(tr, form.getAttribute("data-pos-products-search") || "");
    form.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function cleanupOrphanAcPanels() {
    document.querySelectorAll("body > .sale-edit-ac").forEach(function (el) {
      el.parentNode.removeChild(el);
    });
  }

  function initFull(form) {
    var searchUrl = form.getAttribute("data-pos-products-search") || "";
    cleanupOrphanAcPanels();
    form.querySelectorAll("[data-sale-edit-line-full]").forEach(function (tr) {
      wireProductRow(tr, searchUrl);
    });
    renumberLineRows(form.querySelector("#sale-edit-lines-tbody"));
    var addBtn = form.querySelector("#sale-edit-add-line");
    if (addBtn && !addBtn.getAttribute("data-wired")) {
      addBtn.setAttribute("data-wired", "1");
      addBtn.addEventListener("click", function () {
        addLineRow(form);
      });
    }
    var recalc = function () {
      recalcFull(form);
    };
    if (form._saleEditRecalc) {
      form.removeEventListener("input", form._saleEditRecalc);
      form.removeEventListener("change", form._saleEditRecalc);
    }
    form._saleEditRecalc = recalc;
    form.addEventListener("input", recalc);
    form.addEventListener("change", recalc);
    recalc();
  }

  function initLegacy(form) {
    var cur = form.getAttribute("data-currency") || "";
    function recalc() {
      var rows = form.querySelectorAll("[data-sale-edit-line]");
      var sum = 0;
      rows.forEach(function (row) {
        var qi = row.querySelector('input[name^="qty_"]');
        var pi = row.querySelector('input[name^="price_"]');
        var q = parseNum(qi && qi.value);
        var p = parseNum(pi && pi.value);
        var gross = q * p;
        sum += gross;
        var totEl = row.querySelector(".sale-edit-line-total");
        if (totEl) totEl.textContent = fmt2(gross) + (cur ? " " + cur : "");
      });
      var disc = parseNum(form.getAttribute("data-discount"));
      var svc = parseNum(form.getAttribute("data-service"));
      var tax = parseNum(form.getAttribute("data-tax"));
      var subEl = form.querySelector("#sale-edit-live-subtotal");
      var totalEl = form.querySelector("#sale-edit-live-total");
      if (subEl) subEl.textContent = fmt2(sum) + (cur ? " " + cur : "");
      if (totalEl) totalEl.textContent = fmt2(sum - disc + svc + tax) + (cur ? " " + cur : "");
    }
    if (form._saleEditRecalc) {
      form.removeEventListener("input", form._saleEditRecalc);
      form.removeEventListener("change", form._saleEditRecalc);
    }
    form._saleEditRecalc = recalc;
    form.addEventListener("input", recalc);
    form.addEventListener("change", recalc);
    recalc();
  }

  window.initSaleInvoiceReceiptEdit = function (root) {
    root = root || document;
    var form = root.querySelector("[data-sale-edit-form]");
    if (!form) return;
    if (form.getAttribute("data-sale-edit-full") === "1") initFull(form);
    else initLegacy(form);
    if (window.initSaleInvoiceEditPay) window.initSaleInvoiceEditPay(form);
    if (window.refreshSaleInvoiceEditPayTotal) window.refreshSaleInvoiceEditPayTotal(form);
  };
})();
