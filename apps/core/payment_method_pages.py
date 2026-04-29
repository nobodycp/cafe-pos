"""طرق الدفع: نماذج منفصلة للإنشاء/التعديل، والقائمة مدمجة في تبويب الإعدادات."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.core.forms import PaymentMethodForm
from apps.core.models import PaymentMethod
from apps.core.payment_methods import assert_active_cash_payment_method

PAYMENT_METHOD_PER_PAGE = (10, 25, 50, 100)


def build_payment_methods_list_context(request) -> dict:
    qs = PaymentMethod.objects.order_by("sort_order", "pk")
    try:
        per_page = int(request.GET.get("per_page", "25"))
    except ValueError:
        per_page = 25
    if per_page not in PAYMENT_METHOD_PER_PAGE:
        per_page = 25
    page = Paginator(qs, per_page).get_page(request.GET.get("page"))
    return {
        "page_obj": page,
        "per_page": per_page,
        "per_page_choices": PAYMENT_METHOD_PER_PAGE,
    }


def payment_methods_settings_return_url(request) -> str:
    ns = getattr(request.resolver_match, "namespace", "") or ""
    if ns == "shell":
        return f"{reverse('shell:settings')}?tab=payment"
    return f"{reverse('core:settings')}?tab=payment"


@login_required
def payment_method_list_page(request):
    """يوجّه قائمة طرق الدفع المنفصلة إلى تبويب الإعدادات."""
    q = request.GET.copy()
    q["tab"] = "payment"
    ns = getattr(request.resolver_match, "namespace", "") or ""
    base = reverse("shell:settings") if ns == "shell" else reverse("core:settings")
    return redirect(f"{base}?{q.urlencode()}")


@login_required
def payment_method_create_page(request):
    return_url = payment_methods_settings_return_url(request)
    ns = getattr(request.resolver_match, "namespace", "") or ""
    template_name = "shell/payment_method_form.html" if ns == "shell" else "pos/cashier_payment_method_form.html"
    if request.method == "POST":
        form = PaymentMethodForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "تمت إضافة طريقة الدفع.")
            return redirect(return_url)
    else:
        form = PaymentMethodForm()
    return render(
        request,
        template_name,
        {"form": form, "title": "طريقة دفع جديدة", "is_create": True, "payment_methods_return_url": return_url},
    )


@login_required
def payment_method_update_page(request, pk: int):
    return_url = payment_methods_settings_return_url(request)
    ns = getattr(request.resolver_match, "namespace", "") or ""
    template_name = "shell/payment_method_form.html" if ns == "shell" else "pos/cashier_payment_method_form.html"
    obj = get_object_or_404(PaymentMethod, pk=pk)
    if request.method == "POST":
        form = PaymentMethodForm(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, "تم حفظ التعديلات.")
            return redirect(return_url)
    else:
        form = PaymentMethodForm(instance=obj)
    return render(
        request,
        template_name,
        {
            "form": form,
            "title": "تعديل طريقة الدفع",
            "is_create": False,
            "object": obj,
            "payment_methods_return_url": return_url,
        },
    )


@login_required
@require_POST
def payment_method_delete_page(request, pk: int):
    return_url = payment_methods_settings_return_url(request)
    obj = get_object_or_404(PaymentMethod, pk=pk)
    if obj.code == "cash":
        messages.error(request, "لا يمكن حذف طريقة الكاش الأساسية (يمكنك تعديل الاسم أو التعطيل).")
        return redirect(return_url)
    obj.delete()
    try:
        assert_active_cash_payment_method()
    except ValidationError as e:
        messages.error(request, e.messages[0] if e.messages else str(e))
    else:
        messages.success(request, "تم حذف طريقة الدفع.")
    return redirect(return_url)
