/**
 * تحليل وتنسيق مبالغ في الواجهات (كاشير، نماذج) — نقطة عشرية إنجليزية.
 * يُحمّل كـ global للصفحات التي لا تستخدم bundler.
 */
(function (w) {
  'use strict';
  function parseMoneyStr(s) {
    if (s == null || s === '') return NaN;
    var t = String(s).trim().replace(/\u00a0/g, '').replace(/\s/g, '');
    t = t.replace(/٬/g, '').replace(/'/g, '');
    var ar = '٠١٢٣٤٥٦٧٨٩';
    var en = '0123456789';
    for (var i = 0; i < 10; i++) t = t.split(ar[i]).join(en[i]);
    t = t.replace(/٫/g, '.').replace(/,/g, '.');
    var n = parseFloat(t);
    return isNaN(n) ? NaN : n;
  }
  function formatPayAmt2(n) {
    if (isNaN(n) || n < 0) return '0.00';
    return (Math.round(n * 100) / 100).toFixed(2);
  }
  w.CafeMoney = { parse: parseMoneyStr, format2: formatPayAmt2 };
})(typeof window !== 'undefined' ? window : this);
