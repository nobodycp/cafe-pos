from django import template

register = template.Library()


@register.filter
def dict_get(mapping, key):
    if not mapping:
        return key
    return mapping.get(str(key), key)
