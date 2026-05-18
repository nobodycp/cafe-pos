import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Max, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import Resolver404, resolve, reverse
from django.views.decorators.http import require_GET, require_POST

from apps.catalog.models import Product, Unit
from apps.core.models import log_audit
from apps.core.ledger_pagination import paginate_amount_ledger
from apps.core.list_filters import get_search_q, parse_date_range
from apps.core.pagination import paginate_queryset
from apps.core.panel import PanelFormInvalid, handle_panel_form, panelize_form
from apps.core.payment_methods import credit_method_codes, load_payment_method_rows
from apps.purchasing.forms import SupplierForm, SupplierPaymentForm
from apps.purchasing.supplier_list_filters import (
    COMMISSION_FILTER_CHOICES,
    LINKED_FILTER_CHOICES,
    NET_SIDE_CHOICES,
    SUPPLIER_SORT_CHOICES,
    apply_supplier_filters,
    parse_supplier_filters,
    supplier_filters_open,
    supplier_list_base_queryset,
)
from apps.purchasing.models import (
    PurchaseInvoice,
    PurchaseLine,
    PurchaseReturn,
    PurchaseReturnLine,
    Supplier,
    SupplierCafePurchase,
    SupplierLedgerEntry,
    SupplierPayment,
)
from apps.purchasing.purge_service import purge_purchase_invoice
from apps.purchasing.request_parsers import (
    payment_rows as _payment_rows,
    purchase_form_state as _purchase_form_state,
    purchase_lines_from_request as _purchase_lines_from_request,
    purchase_payments_from_request as _purchase_payments_from_request,
)
from apps.purchasing.services import post_purchase_invoice, record_supplier_payment
from apps.billing.models import SaleInvoiceLine


OPENING_BALANCE_LEDGER_NOTE = "رصيد افتتاحي"

def _supplier_opening_ledger_qs(supplier: Supplier):
    """قيود التسوية المعنونة «رصيد افتتاحي» (يُفترض أنها من إنشاء/تصحيح الرصيد الافتتاحي)."""
    return SupplierLedgerEntry.objects.filter(
        supplier=supplier,
        entry_type=SupplierLedgerEntry.EntryType.ADJUSTMENT,
        note=OPENING_BALANCE_LEDGER_NOTE,
    )
def _supplier_opening_balance_from_ledger(supplier: Supplier) -> Decimal:
    agg = _supplier_opening_ledger_qs(supplier).aggregate(s=Sum("amount"))
    return (agg["s"] or Decimal("0")).quantize(Decimal("0.01"))
def _apply_supplier_opening_balance(*, supplier: Supplier, amount: Decimal) -> None:
    """يستبدل قيود الرصيد الافتتاحي بقيد واحد بالمبلغ الجديد (أو يحذفها إن كان الصفر) ويُحدّث حقل الرصيد."""
    amount = (amount or Decimal("0")).quantize(Decimal("0.01"))
    if amount < 0:
        amount = Decimal("0")
    _supplier_opening_ledger_qs(supplier).delete()
    if amount > 0:
        SupplierLedgerEntry.objects.create(
            supplier=supplier,
            entry_type=SupplierLedgerEntry.EntryType.ADJUSTMENT,
            amount=amount,
            note=OPENING_BALANCE_LEDGER_NOTE,
        )
    supplier.balance = supplier.computed_balance
    supplier.save(update_fields=["balance", "updated_at"])
def _safe_purchase_redirect_next(request) -> Optional[str]:
    """إعادة توجيه آمنة إلى الكاشير بعد حفظ فاتورة شراء (حقل next في النموذج)."""
    raw = (request.POST.get("next") or "").strip()
    if not raw or "\n" in raw or "\r" in raw or ".." in raw or raw.startswith("//"):
        return None
    if not raw.startswith("/"):
        return None
    path_only = raw.split("?", 1)[0]
    if not path_only.startswith("/pos"):
        return None
    try:
        resolve(path_only)
    except Resolver404:
        return None
    return raw
def _apply_general_discount(request, lines: list, errors: list) -> list:
    raw = request.POST.get("general_discount", "0")
    try:
        discount = Decimal(str(raw or "0")).quantize(Decimal("0.01"))
    except InvalidOperation:
        errors.append("الخصم العام غير صالح.")
        return lines
    if discount <= 0:
        return lines
    total = sum((q * c for _, q, c in lines), Decimal("0")).quantize(Decimal("0.01"))
    if discount > total:
        errors.append("الخصم العام أكبر من إجمالي الفاتورة.")
        return lines
    remaining = discount
    adjusted = []
    for idx, (product, qty, cost) in enumerate(lines):
        line_total = (qty * cost).quantize(Decimal("0.01"))
        if idx == len(lines) - 1:
            line_discount = remaining
        else:
            line_discount = ((line_total / total) * discount).quantize(Decimal("0.01"))
            remaining -= line_discount
        net_total = max(line_total - line_discount, Decimal("0"))
        adjusted.append((product, qty, (net_total / qty).quantize(Decimal("0.000001"))))
    return adjusted
def _purchasing_ctx(request, **kwargs):
    ctx = {"purchasing_ns": "shell"}
    ctx.update(kwargs)
    return ctx
def _purchasing_reverse(request, viewname, *args, **kwargs):
    return reverse(f"shell:{viewname}", args=args, kwargs=kwargs)
def _purchasing_redirect(request, viewname, *args, **kwargs):
    return redirect(_purchasing_reverse(request, viewname, *args, **kwargs))
def _safe_return_path(raw: str) -> str:
    from apps.core.nav_back import safe_return_path

    return safe_return_path(raw)
def _purchase_invoice_detail_queryset():
    return PurchaseInvoice.objects.select_related("supplier", "work_session")
def _purchase_detail_back_url(request, invoice: PurchaseInvoice) -> str:
    dest = _safe_return_path(request.GET.get("return", ""))
    if dest:
        return dest
    if invoice.supplier_id:
        return reverse("shell:supplier_detail", args=[invoice.supplier_id])
    return reverse("shell:invoice_list")
def _redirect_open_purchase_invoice_to(url: str, pk: int):
    sep = "&" if "?" in url else "?"
    return redirect(f"{url}{sep}view_purchase_invoice={pk}")
def _redirect_open_purchase_invoice(request, pk: int):
    invoice = get_object_or_404(_purchase_invoice_detail_queryset(), pk=pk)
    dest = _safe_return_path(request.GET.get("return", ""))
    if not dest and request.method == "POST":
        dest = _safe_return_path(request.POST.get("next", ""))
    if not dest:
        dest = _safe_return_path(request.META.get("HTTP_REFERER", ""))
    if not dest:
        dest = _purchase_detail_back_url(request, invoice)
    return _redirect_open_purchase_invoice_to(dest, pk)
def _purchase_invoice_detail_context(request, invoice: PurchaseInvoice) -> dict:
    return {
        "invoice": invoice,
        "lines": invoice.lines.select_related("product").order_by("pk"),
        "has_purchase_returns": invoice.returns.exists(),
        "purchase_returns": list(invoice.returns.order_by("-created_at")),
    }
def _purchasing_tpl(request, shell_tpl, classic_tpl):
    return shell_tpl
