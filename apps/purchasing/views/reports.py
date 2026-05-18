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
    _purchasing_ctx,
    _purchasing_tpl,
)


OPENING_BALANCE_LEDGER_NOTE = "رصيد افتتاحي"

def supplier_statement(request, pk):
    supplier = get_object_or_404(Supplier.objects.select_related("linked_customer"), pk=pk)

    date_from = request.GET.get("from")
    date_to = request.GET.get("to")

    try:
        date_from = datetime.strptime(date_from, "%Y-%m-%d").date() if date_from else None
    except ValueError:
        date_from = None
    try:
        date_to = datetime.strptime(date_to, "%Y-%m-%d").date() if date_to else None
    except ValueError:
        date_to = None

    all_entries = supplier.ledger_entries.order_by("created_at")

    if date_from:
        opening_agg = all_entries.filter(created_at__date__lt=date_from).aggregate(s=Sum("amount"))
        opening_balance = (opening_agg["s"] or Decimal("0")).quantize(Decimal("0.01"))
        entries = all_entries.filter(created_at__date__gte=date_from)
    else:
        opening_balance = Decimal("0.00")
        entries = all_entries

    if date_to:
        entries = entries.filter(created_at__date__lte=date_to)

    def _statement_row(e, running):
        return {
            "date": e.created_at,
            "type": e.get_entry_type_display(),
            "entry_type": e.entry_type,
            "amount": e.amount,
            "running": running,
            "reference": e.note or e.reference_model,
            "reference_model": e.reference_model,
            "reference_pk": e.reference_pk,
        }

    stmt_pag = paginate_amount_ledger(
        request,
        entries,
        opening_balance=opening_balance,
        build_row=_statement_row,
    )
    closing_balance = stmt_pag["closing_balance"]

    net_balance = closing_balance
    if supplier.linked_customer:
        net_balance = (closing_balance - supplier.linked_customer.balance).quantize(Decimal("0.01"))

    tpl = _purchasing_tpl(request, "shell/suppliers_statement.html", "purchasing/supplier_statement.html")
    ctx = _purchasing_ctx(
        request,
        supplier=supplier,
        rows=stmt_pag["rows"],
        opening_balance=opening_balance,
        closing_balance=closing_balance,
        page_opening_balance=stmt_pag["page_opening_balance"],
        net_balance=net_balance,
        date_from=date_from,
        date_to=date_to,
    )
    ctx.update(stmt_pag)
    return render(request, tpl, ctx)
def supplier_balances(request):
    from django.db.models import DecimalField, ExpressionWrapper, F, Value
    from django.db.models.functions import Coalesce

    zero = Value(Decimal("0"), output_field=DecimalField(max_digits=24, decimal_places=6))
    qs = (
        Supplier.objects.filter(is_active=True)
        .select_related("linked_customer")
        .annotate(
            last_txn=Max("ledger_entries__created_at"),
            _cust_bal=Coalesce(F("linked_customer__balance"), zero),
            net_balance=ExpressionWrapper(
                F("balance") - Coalesce(F("linked_customer__balance"), zero),
                output_field=DecimalField(max_digits=24, decimal_places=6),
            ),
        )
        .filter(Q(balance__ne=Decimal("0")) | ~Q(_cust_bal=Decimal("0")))
        .order_by("name_ar")
    )
    q = get_search_q(request)
    if q:
        qs = qs.filter(Q(name_ar__icontains=q) | Q(name_en__icontains=q) | Q(phone__icontains=q))

    grand_agg = qs.aggregate(s=Sum("net_balance"))
    grand_total = (grand_agg["s"] or Decimal("0")).quantize(Decimal("0.01"))

    tpl = _purchasing_tpl(request, "shell/suppliers_balances.html", "purchasing/supplier_balances.html")
    pag = paginate_queryset(request, qs)
    results = [
        {
            "supplier": s,
            "balance": s.balance,
            "customer_balance": s._cust_bal,
            "net_balance": s.net_balance.quantize(Decimal("0.01")),
            "last_txn": s.last_txn,
        }
        for s in pag["page_obj"]
    ]
    ctx = _purchasing_ctx(request, q=q, grand_total=grand_total, results=results)
    ctx.update(pag)
    return render(request, tpl, ctx)
def commission_vendor_report(request):
    vendors = Supplier.objects.filter(
        commission_products__isnull=False,
        is_active=True,
    ).distinct().order_by("name_ar")
    q = get_search_q(request)
    if q:
        vendors = vendors.filter(Q(name_ar__icontains=q) | Q(name_en__icontains=q))

    rows = []
    grand_sales = Decimal("0")
    grand_commission = Decimal("0")
    grand_vendor_due = Decimal("0")
    grand_paid = Decimal("0")

    for vendor in vendors:
        products = vendor.commission_products.filter(is_active=True)
        product_names = [p.name_ar for p in products]

        sold_lines = SaleInvoiceLine.objects.filter(
            product__in=products,
            invoice__is_cancelled=False,
        )
        total_sales = Decimal("0")
        total_commission = Decimal("0")
        for sl in sold_lines:
            total_sales += sl.line_subtotal or Decimal("0")
            total_commission += sl.recognized_revenue or Decimal("0")

        vendor_due_total = (total_sales - total_commission).quantize(Decimal("0.01"))

        paid = Decimal("0")
        for entry in vendor.ledger_entries.filter(entry_type=SupplierLedgerEntry.EntryType.PAYMENT):
            paid += abs(entry.amount)

        remaining = vendor.balance

        rows.append({
            "vendor": vendor,
            "products": product_names,
            "total_sales": total_sales.quantize(Decimal("0.01")),
            "total_commission": total_commission.quantize(Decimal("0.01")),
            "vendor_due_total": vendor_due_total,
            "paid": paid.quantize(Decimal("0.01")),
            "remaining": remaining,
        })
        grand_sales += total_sales
        grand_commission += total_commission
        grand_vendor_due += vendor_due_total
        grand_paid += paid

    ctx = {
        "q": q,
        "grand_sales": grand_sales.quantize(Decimal("0.01")),
        "grand_commission": grand_commission.quantize(Decimal("0.01")),
        "grand_vendor_due": grand_vendor_due.quantize(Decimal("0.01")),
        "grand_paid": grand_paid.quantize(Decimal("0.01")),
    }
    ctx.update(paginate_queryset(request, rows))
    ctx["rows"] = list(ctx["page_obj"])
    return render(request, "purchasing/commission_vendors.html", ctx)
