import json
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.urls import reverse
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import models, transaction
from django.db.models import Count, DecimalField, OuterRef, Prefetch, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone as django_timezone
from django.views.decorators.http import require_GET, require_POST

from apps.billing.receipt_escpos import build_invoice_receipt
from apps.billing.models import OrderPayment, SaleInvoice, SaleInvoiceLine
from apps.billing.tab_service import (
    apply_tab_payments_and_maybe_finalize,
    cart_line_rows_for_template,
    compute_order_totals,
    finalize_order_invoice,
    sum_tab_payments,
)
from apps.catalog.forms import PRODUCT_QUICK_FORM_PREFIX, ProductForm
from apps.catalog.models import Category, Product, ProductModifierGroup
from apps.contacts.customer_lookup import active_customers_search_qs, customer_search_result_row
from apps.contacts.forms import CustomerForm
from apps.contacts.services import resolve_or_create_active_customer_by_name
from apps.contacts.models import Customer
from apps.inventory.models import StockBalance
from apps.core.forms import TreasuryVoucherForm
from apps.core.models import get_pos_settings, log_audit
from apps.core.treasury_services import recent_treasury_voucher_logs
from apps.core.payment_methods import (
    credit_method_codes,
    get_payment_method_codes,
    method_codes_requiring_payer_details,
)
from apps.core.services import SessionService
from apps.purchasing.models import Supplier
from apps.purchasing.request_parsers import payment_rows as _payment_rows, purchase_form_state as _purchase_form_state
from apps.pos.models import DiningTable, Order, OrderLine, TableSession
from apps.pos.services import (
    add_or_update_line,
    adjust_line_quantity,
    create_order,
    delete_order_line,
    hold_order,
    open_orders_with_lines_queryset,
    set_line_note,
    set_line_quantity,
    set_line_unit_price,
)
from apps.pos.table_service import (
    floor_rows_for_session,
    open_or_resume_table_session,
    retire_ephemeral_dining_table_if_safe,
)

POS_CUSTOMER_FORM_PREFIX = "poscc"

def customers_search(request):
    q = (request.GET.get("q") or "").strip()[:80]
    data = [customer_search_result_row(c) for c in active_customers_search_qs(q, limit=20)]
    return JsonResponse({"results": data})
def products_search(request):
    q = (request.GET.get("q") or "").strip()[:80]
    if len(q) < 1:
        return JsonResponse({"results": []})
    qs = (
        Product.objects.filter(is_active=True)
        .exclude(product_type=Product.ProductType.RAW)
        .filter(Q(name_ar__icontains=q) | Q(name_en__icontains=q) | Q(barcode__icontains=q))
        .select_related("category")
        .annotate(modifier_group_count=Count("modifier_groups"))
        .order_by("name_ar")[:15]
    )
    data = [
        {
            "id": p.pk,
            "name_ar": p.name_ar,
            "price": str(p.selling_price),
            "category": p.category.name_ar if p.category else "",
            "has_modifiers": p.modifier_group_count > 0,
        }
        for p in qs
    ]
    return JsonResponse({"results": data})
def customer_quick_create(request):
    """إنشاء عميل سريع من الكاشير — لا يشترط وردية مفتوحة (كان يمنع الإنشاء عند غيابها)."""
    name = (request.POST.get("name_ar") or "").strip()[:200]
    phone = (request.POST.get("phone") or "").strip()[:32]
    c, reused = resolve_or_create_active_customer_by_name(name)
    if not c:
        return JsonResponse({"ok": False, "error": "name_required"}, status=400)
    if phone and not (c.phone or "").strip():
        c.phone = phone[:32]
        c.save(update_fields=["phone", "updated_at"])
    if not reused:
        log_audit(request.user, "contacts.customer.quick_create", "contacts.Customer", c.pk, {})
    row = customer_search_result_row(c)
    return JsonResponse(
        {
            "ok": True,
            "id": c.pk,
            "name_ar": c.name_ar,
            "reused": reused,
            "balance": row["balance"],
            "balance_hint": row["balance_hint"],
            "balance_kind": row["balance_kind"],
        }
    )
def payer_hints_search(request):
    """اقتراحات اسم/جوال المحوّل من دفعات سابقة (تبويب لاختيار أول نتيجة)."""
    from django.db.models import Q

    from apps.billing.models import InvoicePayment, OrderPayment, SaleInvoice

    q = (request.GET.get("q") or "").strip()
    if len(q) < 1:
        return JsonResponse({"results": []})
    seen = set()
    results = []
    inv_q = (
        InvoicePayment.objects.filter(Q(payer_name__icontains=q) | Q(payer_phone__icontains=q))
        .exclude(payer_name="", payer_phone="")
        .order_by("-id")[:40]
    )
    op_q = (
        OrderPayment.objects.filter(Q(payer_name__icontains=q) | Q(payer_phone__icontains=q))
        .exclude(payer_name="", payer_phone="")
        .order_by("-id")[:40]
    )
    for p in list(inv_q) + list(op_q):
        name = (p.payer_name or "").strip()
        phone = (p.payer_phone or "").strip()
        if not name and not phone:
            continue
        key = (name, phone)
        if key in seen:
            continue
        seen.add(key)
        results.append({"name_ar": name, "phone": phone})
        if len(results) >= 15:
            break
    return JsonResponse({"results": results})
