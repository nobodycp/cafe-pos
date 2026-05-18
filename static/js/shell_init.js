/**
 * تهيئة Shell الموحّدة.
 * - النوافذ المنبثقة للألواح: shell_panel_modal.js (يُحمَّل من base.html)
 * - ترقيم الصفحات بدون إعادة تحميل كاملة: shell_pagination_spa.js
 * - هذا الملف: ربط اختياري بعد حقن محتوى AJAX (استدعِ ShellInit.afterPanelInject(container))
 */
(function (global) {
  'use strict';

  function afterPanelInject(container) {
    if (!container) return;
    if (global.CafeCustomerAutocomplete && typeof global.CafeCustomerAutocomplete.bind === 'function') {
      container.querySelectorAll('[data-customer-autocomplete]').forEach(function (el) {
        try {
          global.CafeCustomerAutocomplete.bind(el);
        } catch (e) {}
      });
    }
    if (global.PaymentSplitsUI && typeof global.PaymentSplitsUI.bind === 'function') {
      var form = container.querySelector('form[data-expense-payment-splits]');
      if (form && form._paymentSplitsBound) return;
      if (form) form._paymentSplitsBound = true;
    }
  }

  global.ShellInit = {
    afterPanelInject: afterPanelInject,
    note:
      'حمّل shell_panel_modal.js و shell_pagination_spa.js من templates/shell/base.html قبل هذا الملف.',
  };
})(typeof window !== 'undefined' ? window : this);
