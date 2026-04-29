"""تحويل GET من /customers/… إلى /app/customers/… مع الحفاظ على الاستعلام؛ تمرير POST إلى العروض الحقيقية مع سلوك الغلاف."""

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect

from apps.contacts import views as contacts_views


def _redirect(request, tail: str):
    path = "/app/customers/" + tail
    if request.GET:
        path += "?" + request.GET.urlencode()
    return redirect(path)


def _mark_shell_effective(request):
    request._contacts_effective_ns = "shell"


@login_required
def redirect_legacy_customers_root(request):
    return _redirect(request, "")


@login_required
def redirect_legacy_customers_balances(request):
    return _redirect(request, "balances/")


@login_required
def legacy_customer_create(request):
    if request.method == "POST":
        _mark_shell_effective(request)
        return contacts_views.customer_create(request)
    return _redirect(request, "create/")


@login_required
def redirect_legacy_customer_detail(request, pk: int):
    return _redirect(request, f"{pk}/")


@login_required
def legacy_customer_edit(request, pk: int):
    if request.method == "POST":
        _mark_shell_effective(request)
        return contacts_views.customer_edit(request, pk)
    return _redirect(request, f"{pk}/edit/")


@login_required
def legacy_customer_delete(request, pk: int):
    if request.method == "POST":
        _mark_shell_effective(request)
        return contacts_views.customer_delete(request, pk)
    return _redirect(request, f"{pk}/")


@login_required
def legacy_customer_payment(request, pk: int):
    if request.method == "POST":
        _mark_shell_effective(request)
        return contacts_views.customer_payment(request, pk)
    return _redirect(request, f"{pk}/payment/")


@login_required
def legacy_customer_statement(request, pk: int):
    if request.method == "POST":
        _mark_shell_effective(request)
        return contacts_views.customer_statement(request, pk)
    return _redirect(request, f"{pk}/statement/")
