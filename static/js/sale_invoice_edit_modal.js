/**
 * تعديل فاتورة البيع في نافذة منبثقة (غلاف التشغيل + الكاشير).
 */
(function () {
  "use strict";

  function csrfToken() {
    var inp = document.querySelector("[name=csrfmiddlewaretoken]");
    if (inp && inp.value) return inp.value;
    var m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  function toast(msg, type) {
    if (typeof window.shellToast === "function") {
      window.shellToast(msg, type || "info");
      return;
    }
    if (typeof window.showToast === "function") {
      window.showToast(msg, type || "info");
      return;
    }
  }

  function getOverlay() {
    return document.getElementById("shell-invoice-edit-overlay");
  }

  function getMount() {
    return document.getElementById("shell-invoice-edit-mount");
  }

  function closeModal() {
    var ov = getOverlay();
    var mount = getMount();
    if (ov) ov.style.display = "none";
    if (mount) mount.innerHTML = '<p class="p-4 text-center text-xs text-muted">جاري التحميل…</p>';
    document.body.classList.remove("overflow-hidden");
  }

  function setTitle(text) {
    var el = document.getElementById("shell-invoice-edit-title");
    if (el) el.textContent = text || "تعديل الفاتورة";
  }

  function panelRoutes() {
    var el = document.getElementById("shell-invoice-panel-routes");
    if (!el) return null;
    try {
      return JSON.parse(el.textContent);
    } catch (e) {
      return null;
    }
  }

  function panelUrl(kind, pk) {
    var routes = panelRoutes();
    if (!routes || !routes[kind]) return "";
    return routes[kind].replace("/0/", "/" + String(pk) + "/");
  }

  function stripInvoiceQueryParams() {
    try {
      var u = new URL(window.location.href);
      if (
        !u.searchParams.has("view_invoice") &&
        !u.searchParams.has("edit_invoice") &&
        !u.searchParams.has("view_purchase_invoice") &&
        !u.searchParams.has("view_journal_entry")
      ) {
        return;
      }
      u.searchParams.delete("view_invoice");
      u.searchParams.delete("edit_invoice");
      u.searchParams.delete("view_purchase_invoice");
      u.searchParams.delete("view_journal_entry");
      var qs = u.searchParams.toString();
      window.history.replaceState({}, "", u.pathname + (qs ? "?" + qs : "") + u.hash);
    } catch (e) {
      /* ignore */
    }
  }

  function openFromQueryParams() {
    var params;
    try {
      params = new URLSearchParams(window.location.search);
    } catch (e) {
      return;
    }
    var editPk = params.get("edit_invoice");
    var viewPk = params.get("view_invoice");
    var viewPurchasePk = params.get("view_purchase_invoice");
    var viewJournalPk = params.get("view_journal_entry");
    if (editPk) {
      openModal(panelUrl("edit", editPk), "تعديل الفاتورة", { mode: "edit", kind: "sale" });
      stripInvoiceQueryParams();
      return;
    }
    if (viewPk) {
      openModal(panelUrl("detail", viewPk), "عرض الفاتورة", { mode: "view", kind: "sale" });
      stripInvoiceQueryParams();
      return;
    }
    if (viewPurchasePk) {
      openModal(panelUrl("purchase_detail", viewPurchasePk), "عرض فاتورة الشراء", {
        mode: "view",
        kind: "purchase",
      });
      stripInvoiceQueryParams();
      return;
    }
    if (viewJournalPk) {
      openModal(panelUrl("journal_detail", viewJournalPk), "عرض القيد", {
        mode: "view",
        kind: "journal",
      });
      stripInvoiceQueryParams();
    }
  }

  function openModal(url, title, opts) {
    opts = opts || {};
    var isView = opts.mode === "view";
    var isPurchase = opts.kind === "purchase";
    var isJournal = opts.kind === "journal";
    var ov = getOverlay();
    var mount = getMount();
    if (!ov || !mount || !url) return;
    var defaultTitle = isView
      ? isJournal
        ? "عرض القيد"
        : isPurchase
          ? "عرض فاتورة الشراء"
          : "عرض الفاتورة"
      : "تعديل الفاتورة";
    setTitle(title || defaultTitle);
    mount.innerHTML = '<p class="p-4 text-center text-xs text-muted" dir="rtl">جاري التحميل…</p>';
    ov.style.display = "flex";
    document.body.classList.add("overflow-hidden");
    fetch(url, {
      headers: { "X-Requested-With": "XMLHttpRequest" },
      credentials: "same-origin",
    })
      .then(function (r) {
        if (!r.ok) throw new Error("http");
        return r.text();
      })
      .then(function (html) {
        mount.innerHTML = html;
        if (mount.querySelector("[data-sale-edit-form]") && window.initSaleInvoiceReceiptEdit) {
          window.initSaleInvoiceReceiptEdit(mount);
        }
      })
      .catch(function () {
        mount.innerHTML =
          '<p class="p-4 text-center text-xs text-danger" dir="rtl">تعذر تحميل المحتوى.</p>';
        toast(
          isView
            ? isJournal
              ? "تعذر تحميل القيد"
              : isPurchase
                ? "تعذر تحميل فاتورة الشراء"
                : "تعذر تحميل العرض"
            : "تعذر تحميل التعديل",
          "error"
        );
      });
  }

  function submitEmbedForm(form) {
    var fd = new FormData(form);
    fetch(form.action, {
      method: "POST",
      body: fd,
      headers: {
        "X-Requested-With": "XMLHttpRequest",
        "X-CSRFToken": csrfToken(),
      },
      credentials: "same-origin",
    })
      .then(function (r) {
        var ct = (r.headers.get("content-type") || "").toLowerCase();
        if (ct.indexOf("application/json") >= 0) {
          return r.json().then(function (j) {
            return { ok: r.ok, json: j };
          });
        }
        return { ok: r.ok, json: null };
      })
      .then(function (res) {
        if (res.json && res.json.ok) {
          toast("تم حفظ التعديلات", "success");
          closeModal();
          window.location.reload();
          return;
        }
        var err = (res.json && res.json.error) || "تعذر الحفظ";
        toast(err, "error");
      })
      .catch(function () {
        toast("تعذر الحفظ", "error");
      });
  }

  function setup() {
    var ov = getOverlay();
    if (!ov || ov.getAttribute("data-sale-edit-modal-ready") === "1") return;
    ov.setAttribute("data-sale-edit-modal-ready", "1");

    var bd = document.getElementById("shell-invoice-edit-backdrop");
    var cl = document.getElementById("shell-invoice-edit-close");
    if (bd) bd.addEventListener("click", closeModal);
    if (cl) cl.addEventListener("click", closeModal);

    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape" || ov.style.display === "none") return;
      closeModal();
    });

    document.addEventListener("click", function (e) {
      if (!e.target.closest) return;
      var journalViewBtn = e.target.closest(".shell-journal-load-view");
      if (journalViewBtn) {
        var journalUrl = journalViewBtn.getAttribute("data-journal-detail-panel-url");
        if (!journalUrl) return;
        e.preventDefault();
        openModal(
          journalUrl,
          journalViewBtn.getAttribute("data-journal-detail-title") || "عرض القيد",
          { mode: "view", kind: "journal" }
        );
        return;
      }
      var purchaseViewBtn = e.target.closest(".shell-purchase-load-view");
      if (purchaseViewBtn) {
        var purchaseUrl = purchaseViewBtn.getAttribute("data-purchase-detail-panel-url");
        if (!purchaseUrl) return;
        e.preventDefault();
        openModal(
          purchaseUrl,
          purchaseViewBtn.getAttribute("data-purchase-detail-title") || "عرض فاتورة الشراء",
          { mode: "view", kind: "purchase" }
        );
        return;
      }
      var viewBtn = e.target.closest(".shell-invoice-load-view");
      if (viewBtn) {
        var viewUrl = viewBtn.getAttribute("data-detail-panel-url");
        if (!viewUrl) return;
        e.preventDefault();
        openModal(
          viewUrl,
          viewBtn.getAttribute("data-detail-title") || "عرض الفاتورة",
          { mode: "view", kind: "sale" }
        );
        return;
      }
      var btn =
        e.target.closest(".shell-invoice-load-edit") ||
        e.target.closest(".pos-invoice-load-edit");
      if (!btn) return;
      var url = btn.getAttribute("data-edit-panel-url");
      if (!url) return;
      e.preventDefault();
      var title = btn.getAttribute("data-edit-title") || "تعديل الفاتورة";
      openModal(url, title, { mode: "edit" });
    });

    ov.addEventListener("click", function (e) {
      if (e.target.closest && e.target.closest(".shell-invoice-edit-close")) {
        e.preventDefault();
        closeModal();
      }
      if (e.target.closest && e.target.closest(".pos-invoice-back-preview")) {
        e.preventDefault();
        closeModal();
      }
    });

    ov.addEventListener(
      "submit",
      function (e) {
        var form = e.target;
        if (!form || form.tagName !== "FORM") return;
        if (form.getAttribute("data-pos-sale-edit") !== "1") return;
        e.preventDefault();
        if (window.validateSaleInvoiceEditPay) {
          var err = window.validateSaleInvoiceEditPay(form);
          if (err) {
            toast(err, "error");
            return;
          }
        }
        if (window.applySaleInvoiceEditPayHidden) window.applySaleInvoiceEditPayHidden(form);
        submitEmbedForm(form);
      },
      true
    );
  }

  window.openSaleInvoiceEditModal = openModal;
  window.openSaleInvoiceDetailModal = function (url, title) {
    openModal(url, title, { mode: "view", kind: "sale" });
  };
  window.openPurchaseInvoiceDetailModal = function (url, title) {
    openModal(url, title, { mode: "view", kind: "purchase" });
  };
  window.openJournalEntryDetailModal = function (url, title) {
    openModal(url, title, { mode: "view", kind: "journal" });
  };
  window.closeSaleInvoiceEditModal = closeModal;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      setup();
      openFromQueryParams();
    });
  } else {
    setup();
    openFromQueryParams();
  }
})();
