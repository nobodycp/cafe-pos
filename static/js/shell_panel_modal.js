/**
 * نوافذ منبثقة عامة للنماذج (إضافة/تعديل) — يكمّل sale_invoice_edit_modal.js
 */
(function () {
  "use strict";

  function getDialog() {
    var ov = document.getElementById("shell-invoice-edit-overlay");
    if (!ov) return null;
    return ov.querySelector(".shell-panel-dialog");
  }

  function setPanelWide(wide) {
    var dlg = getDialog();
    if (!dlg) return;
    if (wide) {
      dlg.classList.remove("max-w-[min(32rem,96vw)]");
      dlg.classList.add("max-w-[min(48rem,96vw)]");
    } else {
      dlg.classList.add("max-w-[min(32rem,96vw)]");
      dlg.classList.remove("max-w-[min(48rem,96vw)]");
    }
  }

  function activatePanelScripts(mount) {
    if (!mount) return;
    mount.querySelectorAll("script").forEach(function (oldScript) {
      var script = document.createElement("script");
      for (var i = 0; i < oldScript.attributes.length; i++) {
        var attr = oldScript.attributes[i];
        script.setAttribute(attr.name, attr.value);
      }
      script.text = oldScript.text;
      oldScript.parentNode.replaceChild(script, oldScript);
    });
    if (typeof window.initShellPanel === "function") {
      try {
        window.initShellPanel(mount);
      } catch (e) {
        /* ignore */
      }
    }
  }

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
    }
  }

  function submitPanelForm(form) {
    var fd = new FormData(form);
    if (!fd.has("panel_embed")) fd.append("panel_embed", "1");
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
          if (res.json.message) toast(res.json.message, "success");
          if (window.closeSaleInvoiceEditModal) window.closeSaleInvoiceEditModal();
          if (res.json.redirect) {
            window.location.href = res.json.redirect;
            return;
          }
          window.location.reload();
          return;
        }
        if (res.json && res.json.html) {
          var mount = document.getElementById("shell-invoice-edit-mount");
          if (mount) {
            mount.innerHTML = res.json.html;
            activatePanelScripts(mount);
          }
          toast((res.json && res.json.error) || "راجع البيانات", "error");
          return;
        }
        toast((res.json && res.json.error) || "تعذر الحفظ", "error");
      })
      .catch(function () {
        toast("تعذر الحفظ", "error");
      });
  }

  function openPanelUrl(url, title, wide) {
    if (typeof window.openSaleInvoiceEditModal === "function") {
      window.openSaleInvoiceEditModal(url, title || "", {
        mode: "panel",
        kind: "panel",
        wide: !!wide,
      });
      return;
    }
  }

  function patchOpenModal() {
    if (window.__shellPanelModalPatched) return;
    var orig = window.openSaleInvoiceEditModal;
    if (typeof orig !== "function") return;
    window.openSaleInvoiceEditModal = function (url, title, opts) {
      opts = opts || {};
      setPanelWide(!!opts.wide);
      var isPanel = opts.mode === "panel" || opts.kind === "panel";
      var ov = document.getElementById("shell-invoice-edit-overlay");
      var mount = document.getElementById("shell-invoice-edit-mount");
      if (isPanel && ov && mount && url) {
        var titleEl = document.getElementById("shell-invoice-edit-title");
        if (titleEl) titleEl.textContent = title || "نموذج";
        mount.innerHTML =
          '<p class="p-4 text-center text-xs text-muted" dir="rtl">جاري التحميل…</p>';
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
            activatePanelScripts(mount);
          })
          .catch(function () {
            mount.innerHTML =
              '<p class="p-4 text-center text-xs text-danger" dir="rtl">تعذر التحميل.</p>';
            toast("تعذر تحميل النموذج", "error");
          });
        return;
      }
      setPanelWide(false);
      return orig(url, title, opts);
    };
    window.__shellPanelModalPatched = true;
  }

  function setup() {
    patchOpenModal();

    document.addEventListener("click", function (e) {
      if (!e.target.closest) return;
      var btn = e.target.closest(".shell-panel-open");
      if (!btn) return;
      var url = btn.getAttribute("data-panel-url");
      if (!url) return;
      e.preventDefault();
      openPanelUrl(
        url,
        btn.getAttribute("data-panel-title") || "",
        btn.getAttribute("data-panel-wide") === "1"
      );
    });

    var ov = document.getElementById("shell-invoice-edit-overlay");
    if (ov) {
      ov.addEventListener("submit", function (e) {
        var form = e.target;
        if (!form || form.tagName !== "FORM") return;
        if (form.getAttribute("data-shell-panel-form") !== "1") return;
        if (e.defaultPrevented) return;
        e.preventDefault();
        submitPanelForm(form);
      });
    }

    try {
      var params = new URLSearchParams(window.location.search);
      var panelUrl = params.get("shell_panel");
      if (panelUrl) {
        openPanelUrl(decodeURIComponent(panelUrl), "", false);
        params.delete("shell_panel");
        var qs = params.toString();
        window.history.replaceState(
          {},
          "",
          window.location.pathname + (qs ? "?" + qs : "") + window.location.hash
        );
      }
    } catch (err) {
      /* ignore */
    }
  }

  window.openShellPanel = openPanelUrl;
  window.initShellPanel = window.initShellPanel || function () {};

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", setup);
  } else {
    setup();
  }
})();
