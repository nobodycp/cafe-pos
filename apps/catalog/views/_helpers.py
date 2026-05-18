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



def _catalog_ctx(request, **kwargs):
    ctx = {"catalog_ns": "shell"}
    ctx.update(kwargs)
    return ctx


def _catalog_redirect(request, viewname, *args, **kwargs):
    return redirect(_catalog_reverse(request, viewname, *args, **kwargs))


def _catalog_reverse(request, viewname, *args, **kwargs):
    return reverse(f"shell:{viewname}", args=args, kwargs=kwargs)


def _is_volume_unit(product: Product) -> bool:
    return _unit_code(product) in VOLUME_UNIT_CODES


def _is_weight_unit(product: Product) -> bool:
    return _unit_code(product) in WEIGHT_UNIT_CODES


def _recipe_form_rows(product: Product, *, max_rows: int = 10) -> list[dict]:
    """صفوف نموذج المعادلة — معبّأة من الوصفة الحالية + صف فارغ للإضافة."""
    from decimal import Decimal

    filled = []
    for line in product.recipe_lines.select_related("component", "component__unit").order_by("id"):
        comp = line.component
        qty = line.quantity_per_unit
        unit_mode = "base"
        display_qty = qty
        if _is_weight_unit(comp) and qty < Decimal("1"):
            unit_mode = "gram"
            display_qty = (qty * Decimal("1000")).quantize(Decimal("0.0001"))
        elif _is_volume_unit(comp) and qty < Decimal("1"):
            unit_mode = "ml"
            display_qty = (qty * Decimal("1000")).quantize(Decimal("0.0001"))
        label = comp.name_ar
        if comp.unit:
            label += f" — {comp.unit.name_ar}"
        filled.append({
            "component_id": comp.pk,
            "component_label": label,
            "qty": display_qty,
            "unit_mode": unit_mode,
        })
    rows = []
    for i in range(max_rows):
        if i < len(filled):
            rows.append({**filled[i], "show": True})
        elif i == len(filled):
            rows.append({"show": True})
        else:
            rows.append({"show": False})
    return rows


def _save_recipe_lines_from_post(request, product: Product) -> tuple[int, list[str]]:
    from decimal import Decimal, InvalidOperation

    errors = []
    added = 0
    for i in range(10):
        comp_id = (request.POST.get(f"component_{i}") or "").strip()
        qty_str = (request.POST.get(f"qty_{i}") or "").strip()
        unit_mode = request.POST.get(f"unit_mode_{i}", "base")
        if not comp_id and not qty_str:
            continue
        if not comp_id:
            errors.append(f"سطر {i + 1}: اختر مكوّناً من نتائج البحث")
            continue
        if not qty_str:
            errors.append(f"سطر {i + 1}: أدخل الكمية")
            continue
        try:
            comp = Product.objects.get(
                pk=int(comp_id),
                is_active=True,
                product_type=Product.ProductType.RAW,
            )
            qty = Decimal(qty_str)
            if qty <= 0:
                errors.append(f"سطر {i + 1}: الكمية يجب أن تكون أكبر من صفر")
                continue
            if unit_mode == "gram" and _is_weight_unit(comp):
                qty = qty / Decimal("1000")
            elif unit_mode == "ml" and _is_volume_unit(comp):
                qty = qty / Decimal("1000")
            RecipeLine.objects.update_or_create(
                manufactured_product=product,
                component=comp,
                defaults={"quantity_per_unit": qty},
            )
            added += 1
        except (Product.DoesNotExist, InvalidOperation, ValueError):
            errors.append(f"سطر {i + 1}: بيانات غير صالحة")
    return added, errors


def _unit_code(product: Product) -> str:
    return (product.unit.code if product.unit else "").strip().lower()
