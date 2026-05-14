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

  function fmt2(n) {
    var x = Math.round(n * 100) / 100;
    return x.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function hideAc(tr) {
    var d = tr.querySelector(".sale-edit-ac");
    if (d) {
      d.innerHTML = "";
      d.classList.add("hidden");
    }
  }

  function wireProductRow(tr, searchUrl) {
    var hid = tr.querySelector(".sale-edit-line-product-id");
    var searchInp = tr.querySelector(".sale-edit-product-search");
    var ac = tr.querySelector(".sale-edit-ac");
    var priceInp = tr.querySelector(".sale-edit-price");
    var tmr;
    if (!searchInp || !ac || !hid) return;
    searchInp.addEventListener("input", function () {
      hid.value = "";
      clearTimeout(tmr);
      var q = searchInp.value.trim();
      if (q.length < 1) {
        hideAc(tr);
        return;
      }
      tmr = setTimeout(function () {
        fetch(searchUrl + "?q=" + encodeURIComponent(q), { credentials: "same-origin" })
          .then(function (r) {
            return r.json();
          })
          .then(function (data) {
            var items = data.results || [];
            ac.innerHTML = "";
            if (!items.length) {
              ac.classList.add("hidden");
              return;
            }
            items.forEach(function (it) {
              var b = document.createElement("button");
              b.type = "button";
              b.className =
                "block w-full text-start px-2 py-1.5 hover:bg-gray-100 dark:hover:bg-gray-700 border-0 bg-transparent cursor-pointer text-gray-900 dark:text-gray-100";
              b.textContent = it.name_ar + (it.category ? " — " + it.category : "");
              b.addEventListener("mousedown", function (e) {
                e.preventDefault();
                hid.value = String(it.id);
                searchInp.value = it.name_ar || "";
                if (it.price != null && priceInp) {
                  var pr = parseFloat(String(it.price).replace(",", "."));
                  if (!isNaN(pr)) priceInp.value = pr.toFixed(2);
                }
                ac.classList.add("hidden");
                tr.dispatchEvent(new Event("input", { bubbles: true }));
              });
              ac.appendChild(b);
            });
            ac.classList.remove("hidden");
          })
          .catch(function () {
            hideAc(tr);
          });
      }, 220);
    });
    document.addEventListener("mousedown", function (ev) {
      if (!tr.contains(ev.target)) hideAc(tr);
    });
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
    var disc = parseNum(form.getAttribute("data-discount"));
    var svc = parseNum(form.getAttribute("data-service"));
    var tax = parseNum(form.getAttribute("data-tax"));
    var total = sum - disc + svc + tax;
    var subEl = form.querySelector("#sale-edit-live-subtotal");
    var totalEl = form.querySelector("#sale-edit-live-total");
    if (subEl) subEl.textContent = fmt2(sum) + (cur ? " " + cur : "");
    if (totalEl) totalEl.textContent = fmt2(total) + (cur ? " " + cur : "");

    var paySum = 0;
    form.querySelectorAll(".sale-edit-pay-amt").forEach(function (inp) {
      paySum += parseNum(inp && inp.value);
    });
    var hint = form.querySelector("#sale-edit-pay-hint");
    if (hint) {
      var diff = Math.round((paySum - total) * 100) / 100;
      if (Math.abs(diff) < 0.02) {
        hint.textContent = "مجموع الدفعات يساوي الإجمالي ✓";
        hint.className = "text-[11px] mt-1.5 font-semibold tabular-nums text-success";
      } else {
        hint.textContent =
          "مجموع الدفعات: " +
          fmt2(paySum) +
          " — الإجمالي: " +
          fmt2(total) +
          " — فرق: " +
          fmt2(diff);
        hint.className = "text-[11px] mt-1.5 font-semibold tabular-nums text-danger";
      }
    }
  }

  function initFull(form) {
    var searchUrl = form.getAttribute("data-pos-products-search") || "";
    form.querySelectorAll("[data-sale-edit-line-full]").forEach(function (tr) {
      wireProductRow(tr, searchUrl);
    });
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
  };
})();
