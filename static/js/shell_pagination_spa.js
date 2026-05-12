/**
 * ترقيم الغلاف: نقر «السابقة / التالية / لكل صفحة» يحدّث الجدول فقط دون إعادة تحميل الصفحة.
 * يتطلب أن يحيط الجدول + شريط الترقيم عنصر بـ class shell-spa-table-root.
 */
(function () {
  function sameDocumentPath(fullUrl) {
    try {
      var u = new URL(fullUrl, window.location.origin);
      return u.origin === window.location.origin && u.pathname === window.location.pathname;
    } catch (e) {
      return false;
    }
  }

  function isPaginationLink(anchor) {
    if (!anchor || anchor.tagName !== "A") return false;
    if (anchor.target && anchor.target !== "_self") return false;
    var href = anchor.getAttribute("href");
    if (!href || href.charAt(0) === "#" || href.indexOf("javascript:") === 0) return false;
    if (!anchor.closest(".ds-shell-pagination")) return false;
    if (!sameDocumentPath(anchor.href)) return false;
    return true;
  }

  function findSwapRoot(anchor) {
    return anchor.closest(".shell-spa-table-root");
  }

  function swapFromHtml(html, root) {
    var doc = new DOMParser().parseFromString(html, "text/html");
    var fresh = doc.querySelector(".shell-spa-table-root");
    if (!fresh) return false;
    root.innerHTML = fresh.innerHTML;
    return true;
  }

  function navigatePartial(url) {
    return fetch(url, {
      credentials: "same-origin",
      headers: {
        "X-Requested-With": "XMLHttpRequest",
        Accept: "text/html",
      },
    }).then(function (r) {
      if (!r.ok) throw new Error(String(r.status));
      return r.text();
    });
  }

  document.addEventListener("click", function (e) {
    var a = e.target.closest("a");
    if (!isPaginationLink(a)) return;
    var root = findSwapRoot(a);
    if (!root) return;
    e.preventDefault();
    var url = a.href;
    root.setAttribute("aria-busy", "true");
    navigatePartial(url)
      .then(function (html) {
        if (!swapFromHtml(html, root)) {
          window.location.href = url;
          return;
        }
        if (window.history && window.history.pushState) {
          window.history.pushState({ shellSpaPagination: true }, "", url);
        }
        try {
          var sc = root.closest(".pos-shell-content");
          if (sc) sc.scrollTop = 0;
        } catch (err) {}
      })
      .catch(function () {
        window.location.href = url;
      })
      .finally(function () {
        root.removeAttribute("aria-busy");
      });
  });

  window.addEventListener("popstate", function () {
    var root = document.querySelector(".shell-spa-table-root");
    if (!root) return;
    root.setAttribute("aria-busy", "true");
    navigatePartial(window.location.href)
      .then(function (html) {
        if (!swapFromHtml(html, root)) window.location.reload();
      })
      .catch(function () {
        window.location.reload();
      })
      .finally(function () {
        root.removeAttribute("aria-busy");
      });
  });
})();
