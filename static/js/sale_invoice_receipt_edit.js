/**
 * تعديل فاتورة البيع بمظهر الإيصال: إعادة حساب مجموع السطر والإجمالي معاينة.
 * يُستدعى بعد تحميل DOM أو بعد حقن HTML في طبقة الكاشير.
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

  window.initSaleInvoiceReceiptEdit = function (root) {
    root = root || document;
    var form = root.querySelector("[data-sale-edit-form]");
    if (!form) return;
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
  };
})();
