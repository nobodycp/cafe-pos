"""منطق مشترك لصفحة الإعدادات (التطبيق العام أو داخل كاشير POS)."""

from __future__ import annotations

from typing import Any, Dict, Union

from django.conf import settings as django_settings
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect

from apps.core.forms import (
    BalanceAdjustmentForm,
    CafeInfoForm,
    CurrencyForm,
    OperationModeForm,
    OrderSettingsForm,
    PrinterForm,
    ReceiptForm,
    TaxServiceForm,
)
from apps.core.operation_mode import MODE_CONTINUOUS, MODE_SHIFTS, uses_shifts
from apps.core.models import PosSettings


def resolve_settings_request(
    request,
    *,
    redirect_after_save: str,
    payment_method_url_namespace: str = "pos",
) -> Union[HttpResponse, Dict[str, Any]]:
    """
    يعالج GET/POST للإعدادات.
    redirect_after_save: مسار إعادة التوجيه بعد حفظ ناجح (مثل reverse('shell:settings')).
    payment_method_url_namespace: «shell» فقط — طرق الدفع من /app/settings/.
    يُرجع Redirect أو سياق القالب.
    """
    from apps.core.payment_method_pages import build_payment_methods_list_context

    obj, _ = PosSettings.objects.get_or_create(pk=1)
    section = request.POST.get("section", request.GET.get("tab", ""))

    form_map = {
        "cafe": CafeInfoForm,
        "currency": CurrencyForm,
        "tax": TaxServiceForm,
        "order": OrderSettingsForm,
        "printer": PrinterForm,
        "receipt": ReceiptForm,
        "operation-mode": OperationModeForm,
    }

    if request.method == "POST" and section == "channel-balance-adjust":
        if uses_shifts():
            messages.error(request, "سند تسوية الرصيد متاح في نمط المحاسبة المستمرة فقط.")
            return redirect(f"{redirect_after_save}?tab=operation-mode")
        if not request.user.is_superuser:
            messages.error(request, "تسجيل تسوية الرصيد متاح لمدير النظام فقط.")
            return redirect(f"{redirect_after_save}?tab=channel-balances")
        adj_form = BalanceAdjustmentForm(request.POST)
        if adj_form.is_valid():
            from apps.core.balance_adjustment_service import post_balance_adjustment

            try:
                post_balance_adjustment(
                    method=adj_form.cleaned_data["method"],
                    amount_delta=adj_form.cleaned_data["amount_delta"],
                    reason=adj_form.cleaned_data["reason"],
                    effective_date=adj_form.cleaned_data["effective_date"],
                    user=request.user,
                )
                messages.success(request, "تم تسجيل سند التسوية والقيد المحاسبي.")
            except ValueError as e:
                messages.error(request, str(e))
        else:
            messages.error(request, "تحقق من حقول التسوية.")
        return redirect(f"{redirect_after_save}?tab=channel-balances")

    if request.method == "POST" and section in form_map:
        form = form_map[section](request.POST, instance=obj)
        if form.is_valid():
            if section == "operation-mode":
                old_mode = obj.operation_mode
                new_mode = form.cleaned_data.get("operation_mode")
                if old_mode != new_mode:
                    from apps.core.services import SessionService

                    if new_mode == MODE_CONTINUOUS and SessionService.get_open_session():
                        messages.warning(
                            request,
                            "تم التبديل إلى محاسبة مستمرة. يُفضّل إغلاق الوردية المفتوحة يدوياً إن وُجدت.",
                        )
                    elif new_mode == MODE_SHIFTS:
                        messages.warning(
                            request,
                            "تم التبديل إلى ورديات. الفواتير السابقة بلا وردية قد لا تظهر في مطابقة الوردية.",
                        )
            form.save()
            messages.success(request, "تم حفظ الإعدادات بنجاح.")
            return redirect(f"{redirect_after_save}?tab={section}")

    ctx = {k: cls(instance=obj) for k, cls in form_map.items()}
    ctx["operation_mode_form"] = OperationModeForm(instance=obj)
    ctx["uses_shifts_mode"] = uses_shifts()
    from apps.core.operation_mode import uses_continuous

    ctx["uses_continuous_mode"] = uses_continuous()
    from apps.core.payment_channel_balance import channel_balance_rows_for_settings

    ctx["channel_balance_rows"] = (
        channel_balance_rows_for_settings() if uses_continuous() else []
    )
    ctx["balance_adjust_form"] = BalanceAdjustmentForm() if uses_continuous() else None
    active_tab = section or "cafe"
    if active_tab == "channel-balances" and uses_shifts():
        active_tab = "operation-mode"
    ctx["active_tab"] = active_tab
    ctx["allow_test_database_wipe"] = getattr(
        django_settings, "ALLOW_TEST_DATABASE_WIPE", django_settings.DEBUG
    )
    ctx["payment_method_url_namespace"] = payment_method_url_namespace
    ctx.update(build_payment_methods_list_context(request))
    try:
        from apps.billing.models import SaleInvoice

        ctx["receipt_preview_invoice_id"] = SaleInvoice.objects.order_by("-pk").values_list("pk", flat=True).first()
    except Exception:
        ctx["receipt_preview_invoice_id"] = None
    from apps.core.database_wipe import PRESERVE_LABELS_AR

    ctx["wipe_preserve_options"] = sorted(PRESERVE_LABELS_AR.items(), key=lambda x: x[0])
    from apps.core.database_backup import database_vendor_label, sqlite_backend_enabled

    ctx["database_backup_sqlite"] = sqlite_backend_enabled()
    ctx["database_backup_vendor"] = database_vendor_label()
    return ctx
