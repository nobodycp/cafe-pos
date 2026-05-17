from django import template
from django.urls import reverse

from apps.core.nav_back import append_return

register = template.Library()


@register.simple_tag(takes_context=True)
def shell_url(context, view_name, *args, **kwargs):
    """رابط مع return=المسار الحالي للرجوع الصحيح."""
    url = reverse(view_name, args=args, kwargs=kwargs)
    request = context.get("request")
    if request:
        url = append_return(url, request)
    return url
