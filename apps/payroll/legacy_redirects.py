"""تحويل مسارات الموظفين القديمة /payroll/... إلى الغلاف /app/payroll/..."""

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect


def _redirect(request, tail: str = ""):
    path = "/app/payroll/" + tail
    if request.GET:
        path += "?" + request.GET.urlencode()
    return redirect(path)


@login_required
def employees(request):
    return _redirect(request)


@login_required
def employee_create(request):
    return _redirect(request, "create/")


@login_required
def employee_detail(request, pk: int):
    return _redirect(request, f"{pk}/")


@login_required
def employee_edit(request, pk: int):
    return _redirect(request, f"{pk}/edit/")


@login_required
def employee_delete(request, pk: int):
    return _redirect(request, f"{pk}/")


@login_required
def employee_advance(request, pk: int):
    return _redirect(request, f"{pk}/advance/")


@login_required
def employee_advance_delete(request, pk: int, advance_id: int):
    return _redirect(request, f"{pk}/")


@login_required
def employee_payout(request, pk: int):
    return _redirect(request, f"{pk}/payout/")


@login_required
def employee_payout_delete(request, pk: int, payout_id: int):
    return _redirect(request, f"{pk}/")


@login_required
def employee_add_days(request, pk: int):
    return _redirect(request, f"{pk}/add-days/")


@login_required
def employee_add_hours(request, pk: int):
    return _redirect(request, f"{pk}/add-hours/")


@login_required
def employee_cafe_purchase(request, pk: int):
    return _redirect(request, f"{pk}/cafe-purchase/")


@login_required
def employee_cafe_purchase_delete(request, pk: int, purchase_id: int):
    return _redirect(request, f"{pk}/")
