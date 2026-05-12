"""منطق مشترك لصفحة الإعدادات (التطبيق العام أو داخل كاشير POS)."""

from __future__ import annotations

from typing import Any, Dict, Union

from django.conf import settings as django_settings
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect

from apps.core.forms import (
    CafeInfoForm,
    CurrencyForm,
    OrderSettingsForm,
    PrinterForm,
    ReceiptForm,
    TaxServiceForm,
)
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
    }

    if request.method == "POST" and section in form_map:
        form = form_map[section](request.POST, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, "تم حفظ الإعدادات بنجاح.")
            return redirect(f"{redirect_after_save}?tab={section}")

    ctx = {k: cls(instance=obj) for k, cls in form_map.items()}
    ctx["active_tab"] = section or "cafe"
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
    return ctx
