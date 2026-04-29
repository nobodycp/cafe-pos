from django import template

from apps.core.formatting import decimal_plain_2

register = template.Library()


@register.filter
def dec_plain(value):
    """مبلغ بنقطتين عشريتين ونقطة إنجليزية (لـ data-* و input number)."""
    return decimal_plain_2(value)


@register.filter
def dict_get(mapping, key):
    if not mapping:
        return key
    return mapping.get(str(key), key)
