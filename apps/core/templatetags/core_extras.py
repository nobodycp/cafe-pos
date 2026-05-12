from django import template

from apps.core.formatting import decimal_plain_2
from apps.core.payment_methods import payment_method_label_map

register = template.Library()

_VOUCHER_TYPE_AR = {"receipt": "قبض", "disbursement": "صرف"}
_PARTY_TYPE_AR = {
    "customer": "عميل",
    "supplier": "مورد",
    "employee": "موظف",
    "expense": "مصاريف",
}


@register.filter
def dec_plain(value):
    """مبلغ بنقطتين عشريتين ونقطة إنجليزية (لـ data-* و input number)."""
    return decimal_plain_2(value)


@register.filter
def dict_get(mapping, key):
    if not mapping:
        return key
    return mapping.get(str(key), key)


@register.filter
def payment_method_label(code):
    """تسمية طريقة الدفع من جدول طرق الدفع (نشط أو سجل قديم)."""
    if code is None or str(code).strip() == "":
        return "—"
    c = str(code).strip().lower()
    mp = payment_method_label_map()
    return mp.get(c, c)


@register.filter
def treasury_voucher_type_ar(value):
    if not value:
        return "—"
    return _VOUCHER_TYPE_AR.get(str(value).strip().lower(), str(value))


@register.filter
def treasury_party_type_ar(value):
    if not value:
        return "—"
    return _PARTY_TYPE_AR.get(str(value).strip().lower(), str(value))
