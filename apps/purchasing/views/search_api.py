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

def purchase_products_search(request):
    q = request.GET.get("q", "").strip()
    if len(q) < 1:
        return JsonResponse({"results": []})
    purchasable = [Product.ProductType.RAW, Product.ProductType.READY]
    name_q = Q(name_ar__icontains=q) | Q(name_en__icontains=q) | Q(barcode__icontains=q)
    qs = (
        Product.objects.select_related("unit")
        .filter(is_active=True, product_type__in=purchasable)
        .filter(name_q)
        .order_by("name_ar")[:30]
    )
    return JsonResponse(
        {"results": [{"id": p.pk, "name_ar": p.name_ar, "type": p.product_type, "unit_id": p.unit_id, "unit_name": p.unit.name_ar if p.unit else ""} for p in qs]},
    )
def purchase_units_search(request):
    q = request.GET.get("q", "").strip()
    if len(q) < 1:
        return JsonResponse({"results": []})
    qs = Unit.objects.filter(name_ar__icontains=q).order_by("name_ar")[:30]
    return JsonResponse({"results": [{"id": u.pk, "name_ar": u.name_ar, "code": u.code} for u in qs]})
def purchase_unit_quick_create(request):
    try:
        body = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "JSON غير صالح"}, status=400)
    name_ar = (body.get("name_ar") or "").strip()[:128]
    if len(name_ar) < 1:
        return JsonResponse({"error": "أدخل اسم الوحدة"}, status=400)
    existing = Unit.objects.filter(name_ar__iexact=name_ar).first()
    if existing:
        return JsonResponse({"id": existing.pk, "name_ar": existing.name_ar, "code": existing.code, "reused": True})
    n = Unit.objects.count() + 1
    code = f"unit_{n}"
    while Unit.objects.filter(code=code).exists():
        n += 1
        code = f"unit_{n}"
    unit = Unit.objects.create(code=code, name_ar=name_ar, name_en="")
    log_audit(request.user, "catalog.unit.quick_create_purchase", "catalog.Unit", unit.pk, {})
    return JsonResponse({"id": unit.pk, "name_ar": unit.name_ar, "code": unit.code, "reused": False})
def purchase_suppliers_search(request):
    q = request.GET.get("q", "").strip()
    if len(q) < 1:
        return JsonResponse({"results": []})
    qs = (
        Supplier.objects.filter(is_active=True)
        .filter(name_ar__icontains=q)
        .order_by("name_ar")[:30]
    )
    return JsonResponse({"results": [{"id": s.pk, "name_ar": s.name_ar, "phone": s.phone} for s in qs]})
def purchase_supplier_quick_create(request):
    from apps.core.views import _supplier_net_balance_for_party

    try:
        body = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "JSON غير صالح"}, status=400)
    name_ar = (body.get("name_ar") or "").strip()[:200]
    if len(name_ar) < 2:
        return JsonResponse({"error": "أدخل اسم مورد بحرفين على الأقل"}, status=400)
    existing = Supplier.objects.filter(name_ar__iexact=name_ar, is_active=True).first()
    if existing:
        ex = Supplier.objects.select_related("linked_customer").get(pk=existing.pk)
        return JsonResponse(
            {
                "id": ex.pk,
                "name_ar": ex.name_ar,
                "balance": str(_supplier_net_balance_for_party(ex)),
                "reused": True,
            }
        )
    supplier = Supplier.objects.create(name_ar=name_ar, name_en="", phone="", email="")
    log_audit(request.user, "purchasing.supplier.quick_create", "purchasing.Supplier", supplier.pk, {})
    sup = Supplier.objects.select_related("linked_customer").get(pk=supplier.pk)
    return JsonResponse(
        {
            "id": sup.pk,
            "name_ar": sup.name_ar,
            "balance": str(_supplier_net_balance_for_party(sup)),
            "reused": False,
        }
    )
def purchase_product_quick_create(request):
    try:
        body = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "JSON غير صالح"}, status=400)
    name_ar = (body.get("name_ar") or "").strip()[:200]
    if len(name_ar) < 2:
        return JsonResponse({"error": "أدخل اسماً بحرفين على الأقل"}, status=400)
    ptype = body.get("product_type") or Product.ProductType.RAW
    unit_id = body.get("unit_id")
    unit = None
    if unit_id:
        try:
            unit = Unit.objects.get(pk=int(unit_id))
        except (Unit.DoesNotExist, ValueError):
            return JsonResponse({"error": "وحدة غير صالحة"}, status=400)
    if ptype not in (Product.ProductType.RAW, Product.ProductType.READY):
        ptype = Product.ProductType.RAW
    existing = Product.objects.filter(
        name_ar__iexact=name_ar,
        is_active=True,
        product_type__in=[Product.ProductType.RAW, Product.ProductType.READY],
    ).first()
    if existing:
        return JsonResponse({"id": existing.pk, "name_ar": existing.name_ar, "reused": True})
    with transaction.atomic():
        prod = Product.objects.create(
            name_ar=name_ar,
            name_en="",
            unit=unit,
            product_type=ptype,
            selling_price=Decimal("0"),
            is_stock_tracked=True,
            is_active=True,
        )
    log_audit(request.user, "catalog.product.quick_create_purchase", "catalog.Product", prod.pk, {"type": ptype})
    return JsonResponse({"id": prod.pk, "name_ar": prod.name_ar, "unit_id": prod.unit_id, "unit_name": prod.unit.name_ar if prod.unit else "", "reused": False})
