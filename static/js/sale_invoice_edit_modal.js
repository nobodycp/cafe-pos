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

  function openModal(url, title) {
    var ov = getOverlay();
    var mount = getMount();
    if (!ov || !mount || !url) return;
    setTitle(title || "تعديل الفاتورة");
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
        if (window.initSaleInvoiceReceiptEdit) {
          window.initSaleInvoiceReceiptEdit(mount);
        }
      })
      .catch(function () {
        mount.innerHTML =
          '<p class="p-4 text-center text-xs text-danger" dir="rtl">تعذر تحميل التعديل.</p>';
        toast("تعذر تحميل التعديل", "error");
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
      var btn =
        e.target.closest &&
        (e.target.closest(".shell-invoice-load-edit") ||
          e.target.closest(".pos-invoice-load-edit"));
      if (!btn) return;
      var url = btn.getAttribute("data-edit-panel-url");
      if (!url) return;
      e.preventDefault();
      var title = btn.getAttribute("data-edit-title") || "تعديل الفاتورة";
      openModal(url, title);
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
  window.closeSaleInvoiceEditModal = closeModal;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", setup);
  } else {
    setup();
  }
})();
