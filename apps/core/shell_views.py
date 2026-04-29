from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render
from django.urls import reverse

from apps.core.settings_handlers import resolve_settings_request


@login_required
def settings_page(request):
    result = resolve_settings_request(
        request,
        redirect_after_save=reverse("shell:settings"),
        payment_method_url_namespace="shell",
    )
    if isinstance(result, HttpResponse):
        return result
    return render(request, "shell/settings.html", result)
