from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q
from django.http import JsonResponse
import json
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify
from django.views.decorators.http import require_GET, require_POST

from apps.catalog.forms import (
    PRODUCT_QUICK_FORM_PREFIX,
    CategoryForm,
    ProductForm,
    QuickCategoryForm,
    QuickUnitForm,
    RecipeLineForm,
    UnitForm,
)
from apps.catalog.models import Category, Product, RecipeLine, Unit
from apps.catalog.product_list_filters import (
    CATEGORY_SORT_CHOICES,
    ACTIVE_FILTER_CHOICES,
    PARENT_FILTER_CHOICES,
    PRODUCT_SORT_CHOICES,
    STOCK_FILTER_CHOICES,
    UNIT_SORT_CHOICES,
    apply_category_filters,
    apply_product_filters,
    apply_unit_filters,
    categories_filters_open,
    category_filter_options,
    parent_category_options,
    parse_category_filters,
    parse_product_filters,
    parse_unit_filters,
    products_filters_open,
    units_filters_open,
)
from apps.core.models import log_audit
from apps.core.panel import PanelFormInvalid, handle_panel_form, panelize_form
from apps.core.services import SessionService
from apps.inventory.models import ManufacturingBatch, StockBalance, StockMovement, StockTakeLine
from apps.inventory.services import get_unit_cost, record_manufacturing_batch, void_manufacturing_batch
from apps.billing.models import SaleInvoiceLine
from apps.core.pagination import paginate_queryset
from apps.pos.models import Order, OrderLine
from apps.purchasing.models import PurchaseLine


WEIGHT_UNIT_CODES = {"kg", "kilo", "kilogram", "كيلو", "كيلوغرام"}
VOLUME_UNIT_CODES = {"l", "lt", "ltr", "liter", "litre", "lter", "لتر"}



from apps.catalog.views._helpers import _catalog_reverse

def category_quick_create(request):
    try:
        body = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "JSON غير صالح"}, status=400)
    name_ar = (body.get("name_ar") or "").strip()[:160]
    if len(name_ar) < 2:
        return JsonResponse({"error": "أدخل اسم التصنيف بحرفين على الأقل"}, status=400)
    existing = Category.objects.filter(name_ar__iexact=name_ar, is_active=True).first()
    if existing:
        return JsonResponse({"id": existing.pk, "name_ar": existing.name_ar, "reused": True})
    obj = Category.objects.create(name_ar=name_ar, name_en="", is_active=True, sort_order=0)
    return JsonResponse({"id": obj.pk, "name_ar": obj.name_ar, "reused": False})


def category_search(request):
    q = (request.GET.get("q") or "").strip()
    if len(q) < 1:
        return JsonResponse({"results": []})
    rows = Category.objects.filter(is_active=True, name_ar__icontains=q).order_by("name_ar")[:24]
    return JsonResponse({"results": [{"id": c.pk, "name_ar": c.name_ar} for c in rows]})


def component_info(request, pk):
    """Return unit, cost, and stock info for a component product (JSON)."""
    from decimal import Decimal
    from apps.inventory.models import StockBalance

    product = get_object_or_404(Product, pk=pk)
    unit_code = product.unit.code if product.unit else ""
    unit_name = product.unit.name_ar if product.unit else ""
    cost = float(get_unit_cost(product))
    try:
        on_hand = float(product.stock_balance.quantity_on_hand)
    except StockBalance.DoesNotExist:
        on_hand = 0
    has_sub_unit = _is_weight_unit(product) or _is_volume_unit(product)
    sub_unit = ""
    if _is_weight_unit(product):
        sub_unit = "غرام"
    elif _is_volume_unit(product):
        sub_unit = "مل"
    return JsonResponse({
        "unit_code": unit_code,
        "unit_name": unit_name,
        "cost": cost,
        "on_hand": on_hand,
        "has_sub_unit": has_sub_unit,
        "sub_unit": sub_unit,
    })


def raw_materials_search(request):
    """بحث مواد خام لمعادلة التصنيع (autocomplete)."""
    q = (request.GET.get("q") or "").strip()
    if len(q) < 1:
        return JsonResponse({"results": []})
    name_q = Q(name_ar__icontains=q) | Q(name_en__icontains=q) | Q(barcode__icontains=q)
    qs = (
        Product.objects.select_related("unit")
        .filter(is_active=True, product_type=Product.ProductType.RAW)
        .filter(name_q)
        .order_by("name_ar")[:30]
    )
    return JsonResponse({
        "results": [
            {
                "id": p.pk,
                "name_ar": p.name_ar,
                "unit_name": p.unit.name_ar if p.unit else "",
            }
            for p in qs
        ],
    })


def unit_quick_create(request):
    try:
        body = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "JSON غير صالح"}, status=400)
    name_ar = (body.get("name_ar") or "").strip()[:128]
    name_en = (body.get("name_en") or "").strip()[:128]
    code_raw = (body.get("code") or "").strip()[:32]
    if len(name_ar) < 2:
        return JsonResponse({"error": "أدخل اسم الوحدة بحرفين على الأقل"}, status=400)
    code = slugify(code_raw) if code_raw else ""
    if not code:
        n = Unit.objects.count() + 1
        code = f"u{n}"
        while Unit.objects.filter(code=code).exists():
            n += 1
            code = f"u{n}"
    if len(code) > 32:
        code = code[:32]
    existing = Unit.objects.filter(code__iexact=code).first()
    if existing:
        return JsonResponse(
            {"id": existing.pk, "code": existing.code, "name_ar": existing.name_ar, "reused": True},
        )
    unit = Unit.objects.create(code=code, name_ar=name_ar, name_en=name_en)
    log_audit(request.user, "catalog.unit.quick_create", "catalog.Unit", str(unit.pk), {"code": unit.code})
    return JsonResponse({"id": unit.pk, "code": unit.code, "name_ar": unit.name_ar, "reused": False})
