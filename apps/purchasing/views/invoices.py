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

from apps.purchasing.views._helpers import (
    _apply_general_discount,
    _purchase_detail_back_url,
    _purchase_invoice_detail_queryset,
    _purchasing_ctx,
    _purchasing_redirect,
    _purchasing_tpl,
    _redirect_open_purchase_invoice,
    _redirect_open_purchase_invoice_to,
    _safe_purchase_redirect_next,
)


OPENING_BALANCE_LEDGER_NOTE = "رصيد افتتاحي"

def purchase_invoice_create(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)
    errors = []

    if request.method == "POST":
        lines = _purchase_lines_from_request(request, errors)
        if not errors:
            lines = _apply_general_discount(request, lines, errors)

        if not errors:
            total = sum((q * c for _, q, c in lines), Decimal("0")).quantize(Decimal("0.01"))
            payments = _purchase_payments_from_request(request, total, errors)

            if not errors:
                try:
                    inv = post_purchase_invoice(
                        supplier=supplier,
                        lines=lines,
                        user=request.user,
                        payments=payments,
                    )
                    messages.success(request, f"تم إنشاء فاتورة الشراء {inv.invoice_number} بنجاح")
                    nu = _safe_purchase_redirect_next(request)
                    if nu:
                        return redirect(nu)
                    return _purchasing_redirect(request, "supplier_detail", pk=supplier.pk)
                except Exception as e:
                    errors.append(f"حدث خطأ: {e}")

    if errors:
        nu = _safe_purchase_redirect_next(request)
        if nu:
            for err in errors:
                messages.error(request, err)
            return redirect(nu)

    return render(
        request,
        _purchasing_tpl(request, "shell/purchase_form.html", "purchasing/purchase_form.html"),
        _purchasing_ctx(
            request,
            supplier=supplier,
            errors=errors,
            range10=range(10),
            range20=range(20),
            payment_method_rows=_payment_rows(),
            purchase_form_state=_purchase_form_state(request),
        ),
    )
def purchase_invoice_new(request):
    suppliers = Supplier.objects.filter(is_active=True).order_by("name_ar")
    errors = []

    if request.method == "POST":
        sup_id = request.POST.get("supplier_id")
        if not sup_id:
            errors.append("يرجى اختيار مورد")
        else:
            try:
                supplier = Supplier.objects.get(pk=int(sup_id))
            except (Supplier.DoesNotExist, ValueError):
                errors.append("مورد غير صالح")
                supplier = None

            if supplier and not errors:
                lines = _purchase_lines_from_request(request, errors)
                if not errors:
                    lines = _apply_general_discount(request, lines, errors)

                if not errors:
                    total = sum((q * c for _, q, c in lines), Decimal("0")).quantize(Decimal("0.01"))
                    payments = _purchase_payments_from_request(request, total, errors)

                    if not errors:
                        try:
                            inv = post_purchase_invoice(
                                supplier=supplier,
                                lines=lines,
                                user=request.user,
                                payments=payments,
                            )
                            messages.success(request, f"تم إنشاء فاتورة الشراء {inv.invoice_number} بنجاح")
                            nu = _safe_purchase_redirect_next(request)
                            if nu:
                                return redirect(nu)
                            return _purchasing_redirect(request, "supplier_detail", pk=supplier.pk)
                        except Exception as e:
                            errors.append(f"حدث خطأ: {e}")

    if errors:
        nu = _safe_purchase_redirect_next(request)
        if nu:
            for err in errors:
                messages.error(request, err)
            return redirect(nu)

    return render(
        request,
        _purchasing_tpl(request, "shell/purchase_new.html", "purchasing/purchase_new.html"),
        _purchasing_ctx(
            request,
            suppliers=suppliers,
            errors=errors,
            range10=range(10),
            range20=range(20),
            payment_method_rows=_payment_rows(),
            purchase_form_state=_purchase_form_state(request),
        ),
    )
def purchase_invoice_list(request):
    from apps.core.list_filters import iso_date_str

    qs = PurchaseInvoice.objects.select_related("supplier", "work_session").order_by("-created_at", "-pk")

    q = get_search_q(request)
    if q:
        qs = qs.filter(
            Q(invoice_number__icontains=q)
            | Q(supplier__name_ar__icontains=q)
            | Q(supplier__name_en__icontains=q)
        )

    date_from, date_to = parse_date_range(request)
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)

    status = (request.GET.get("status") or "").strip().lower()
    if status == "cancelled":
        qs = qs.filter(is_cancelled=True)
    elif status == "active":
        qs = qs.filter(is_cancelled=False)
    elif status in ("paid", "partial", "unpaid"):
        qs = qs.filter(is_cancelled=False, payment_status=status)

    ctx = _purchasing_ctx(
        request,
        q=q,
        date_from=iso_date_str(date_from),
        date_to=iso_date_str(date_to),
        status=status,
        filters_open=bool(q or date_from or date_to or status),
    )
    ctx.update(paginate_queryset(request, qs))
    tpl = _purchasing_tpl(request, "shell/purchase_list.html", "purchasing/purchase_list.html")
    return render(request, tpl, ctx)
def purchase_invoice_detail(request, pk):
    """الرابط القديم — يعيد التوجيه لفتح النافذة المنبثقة."""
    invoice = get_object_or_404(_purchase_invoice_detail_queryset(), pk=pk)
    dest = _purchase_detail_back_url(request, invoice)
    return _redirect_open_purchase_invoice_to(dest, pk)
def purchase_invoice_delete(request, pk):
    invoice = get_object_or_404(PurchaseInvoice.objects.select_related("supplier"), pk=pk)
    supplier_pk = invoice.supplier_id
    inv_number = invoice.invoice_number
    try:
        purge_purchase_invoice(
            invoice=invoice,
            reason=(request.POST.get("reason") or "حذف نهائي من شاشة فاتورة الشراء").strip(),
            user=request.user,
        )
        messages.success(request, f"تم حذف فاتورة الشراء {inv_number} نهائياً مع كل آثارها.")
        success_next = (request.POST.get("next_success") or "").strip()
        if success_next.startswith("/") and not success_next.startswith("//") and "\n" not in success_next and "\r" not in success_next:
            return redirect(success_next)
        return _purchasing_redirect(request, "supplier_detail", pk=supplier_pk)
    except Exception as e:
        messages.error(request, f"تعذر حذف فاتورة الشراء: {e}")
        fallback = (request.POST.get("next") or "").strip()
        if fallback.startswith("/") and not fallback.startswith("//") and "\n" not in fallback and "\r" not in fallback:
            return redirect(fallback)
        return _redirect_open_purchase_invoice(request, pk)
