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



from apps.catalog.views._helpers import (
    _catalog_ctx,
    _catalog_redirect,
    _catalog_reverse,
    _is_volume_unit,
    _is_weight_unit,
    _recipe_form_rows,
    _save_recipe_lines_from_post,
    _unit_code,
)

def _recipe_list_panel_context(request, pk):
    from decimal import Decimal
    from apps.inventory.models import StockBalance

    product = get_object_or_404(
        Product, pk=pk, product_type=Product.ProductType.MANUFACTURED
    )
    lines = product.recipe_lines.select_related("component", "component__unit")
    bom_cost = get_unit_cost(product)
    enriched = []
    for line in lines:
        comp = line.component
        unit_cost = get_unit_cost(comp)
        qty = line.quantity_per_unit
        line_cost = (unit_cost * qty).quantize(Decimal("0.01"))
        try:
            on_hand = comp.stock_balance.quantity_on_hand
        except StockBalance.DoesNotExist:
            on_hand = Decimal("0")
        max_units = (
            (on_hand / qty).quantize(Decimal("1"), rounding="ROUND_DOWN")
            if qty > 0
            else Decimal("0")
        )
        if _is_weight_unit(comp) and qty < 1:
            display_qty = (qty * 1000).quantize(Decimal("0.1"))
            display_unit = "غرام"
        elif _is_volume_unit(comp) and qty < 1:
            display_qty = (qty * 1000).quantize(Decimal("0.1"))
            display_unit = "مل"
        else:
            display_qty = qty
            display_unit = comp.unit.name_ar if comp.unit else ""
        enriched.append({
            "line": line,
            "component": comp,
            "unit_cost": unit_cost,
            "line_cost": line_cost,
            "on_hand": on_hand,
            "max_units": max_units,
            "display_qty": display_qty,
            "display_unit": display_unit,
        })
    profit = (
        (product.selling_price - bom_cost).quantize(Decimal("0.01"))
        if bom_cost
        else None
    )
    max_producible = (
        min((e["max_units"] for e in enriched), default=Decimal("0"))
        if enriched
        else Decimal("0")
    )
    return {
        "product": product,
        "enriched": enriched,
        "bom_cost": bom_cost,
        "profit": profit,
        "max_producible": max_producible,
        "recipe_add_panel_url": reverse("shell:recipe_add_panel", args=[pk]),
    }


def category_create_panel(request):
    tpl = "shell/panels/category_create_panel.html"

    def build_context():
        form = CategoryForm(request.POST or None)
        panelize_form(form)
        return {"form": form, "form_action": reverse("shell:category_create_panel"), "panel_title": "إضافة تصنيف"}

    def on_valid():
        form = CategoryForm(request.POST)
        if not form.is_valid():
            raise PanelFormInvalid("راجع البيانات")
        form.save()

    return handle_panel_form(request, template_name=tpl, build_context=build_context, on_valid=on_valid)


def category_edit_panel(request, pk):
    category = get_object_or_404(Category, pk=pk)
    tpl = "shell/panels/category_edit_panel.html"

    def build_context():
        form = CategoryForm(request.POST or None, instance=category)
        panelize_form(form)
        return {
            "form": form,
            "form_action": reverse("shell:category_edit_panel", args=[pk]),
            "panel_title": "تعديل تصنيف",
        }

    def on_valid():
        form = CategoryForm(request.POST, instance=category)
        if not form.is_valid():
            raise PanelFormInvalid("راجع البيانات")
        form.save()

    return handle_panel_form(request, template_name=tpl, build_context=build_context, on_valid=on_valid)


def manufactured_product_create_panel(request):
    from decimal import Decimal, InvalidOperation

    categories = Category.objects.filter(is_active=True).order_by("name_ar")
    units = Unit.objects.all()
    components = Product.objects.filter(
        is_active=True,
        product_type=Product.ProductType.RAW,
    ).select_related("unit").order_by("name_ar")
    errors: list[str] = []
    tpl = "shell/panels/manufactured_product_create_panel.html"

    def build_context():
        return _catalog_ctx(
            request,
            categories=categories,
            units=units,
            components=components,
            errors=errors,
            range10=range(10),
            form_action=reverse("shell:manufactured_product_create_panel"),
            panel_title="تصنيع منتج",
        )

    @transaction.atomic
    def on_valid():
        nonlocal errors
        errors = []
        name_ar = (request.POST.get("name_ar") or "").strip()
        selling_price_raw = request.POST.get("selling_price") or "0"
        category_id = request.POST.get("category") or ""
        unit_id = request.POST.get("unit") or ""
        barcode = (request.POST.get("barcode") or "").strip()

        if not name_ar:
            errors.append("أدخل اسم المنتج المصنع.")
        try:
            selling_price = Decimal(str(selling_price_raw or "0")).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError):
            selling_price = Decimal("0")
            errors.append("سعر البيع غير صالح.")

        category = None
        if category_id:
            try:
                category = Category.objects.get(pk=int(category_id))
            except (Category.DoesNotExist, ValueError):
                errors.append("التصنيف غير صالح.")

        unit = None
        if unit_id:
            try:
                unit = Unit.objects.get(pk=int(unit_id))
            except (Unit.DoesNotExist, ValueError):
                errors.append("الوحدة غير صالحة.")

        recipe_rows = []
        for i in range(10):
            comp_id = request.POST.get(f"component_{i}")
            qty_raw = (request.POST.get(f"qty_{i}") or "").strip()
            if not comp_id and not qty_raw:
                continue
            try:
                comp = Product.objects.get(pk=int(comp_id), product_type=Product.ProductType.RAW)
                qty = Decimal(qty_raw)
                unit_mode = request.POST.get(f"unit_mode_{i}", "base")
                if qty <= 0:
                    errors.append(f"سطر {i + 1}: الكمية يجب أن تكون أكبر من صفر.")
                    continue
                if unit_mode == "gram" and _is_weight_unit(comp):
                    qty = qty / Decimal("1000")
                elif unit_mode == "ml" and _is_volume_unit(comp):
                    qty = qty / Decimal("1000")
                recipe_rows.append((comp, qty))
            except (Product.DoesNotExist, InvalidOperation, ValueError, TypeError):
                errors.append(f"سطر {i + 1}: بيانات المكوّن غير صالحة.")

        if not recipe_rows:
            errors.append("أدخل مكوّن واحد على الأقل لمعادلة التصنيع.")

        if errors:
            raise PanelFormInvalid(errors[0])

        product = Product.objects.create(
            name_ar=name_ar,
            name_en="",
            category=category,
            unit=unit,
            selling_price=selling_price,
            product_type=Product.ProductType.MANUFACTURED,
            is_stock_tracked=False,
            is_active=True,
            barcode=barcode,
        )
        for comp, qty in recipe_rows:
            RecipeLine.objects.create(
                manufactured_product=product,
                component=comp,
                quantity_per_unit=qty,
            )

    return handle_panel_form(request, template_name=tpl, build_context=build_context, on_valid=on_valid, wide=True)


def product_create_panel(request):
    """نفس نموذج «منتج جديد» في الكاشير (تصنيف/وحدة سريعة + كل الحقول)."""
    tpl = "shell/panels/product_create_panel.html"

    def build_context():
        form = ProductForm(request.POST or None, prefix=PRODUCT_QUICK_FORM_PREFIX)
        panelize_form(form)
        return {
            "pos_product_quick_form": form,
            "form_action": reverse("shell:product_create_panel"),
            "panel_title": "منتج جديد",
            "product_quick_shell": True,
        }

    def on_valid():
        form = ProductForm(request.POST, prefix=PRODUCT_QUICK_FORM_PREFIX)
        if not form.is_valid():
            raise PanelFormInvalid("راجع البيانات")
        form.save()

    return handle_panel_form(request, template_name=tpl, build_context=build_context, on_valid=on_valid, wide=True)


def product_edit_panel(request, pk):
    product = get_object_or_404(Product, pk=pk)
    tpl = "shell/panels/product_edit_panel.html"

    def build_context():
        form = ProductForm(request.POST or None, instance=product)
        panelize_form(form)
        return {
            "form": form,
            "product": product,
            "form_action": reverse("shell:product_edit_panel", args=[pk]),
            "panel_title": "تعديل منتج",
        }

    def on_valid():
        form = ProductForm(request.POST, instance=product)
        if not form.is_valid():
            raise PanelFormInvalid("راجع البيانات")
        form.save()

    return handle_panel_form(request, template_name=tpl, build_context=build_context, on_valid=on_valid, wide=True)


def product_manufacture_panel(request, pk):
    from decimal import Decimal, InvalidOperation
    from apps.core.panel import PanelFormInvalid, handle_panel_form, panel_json_ok, render_panel
    from apps.core.services import SessionService
    from apps.inventory.services import record_manufacturing_batch

    product = get_object_or_404(
        Product, pk=pk, product_type=Product.ProductType.MANUFACTURED
    )
    tpl = "shell/panels/product_manufacture_panel.html"

    def build_context():
        return {
            "product": product,
            "form_action": reverse("shell:product_manufacture_panel", args=[pk]),
            "panel_title": f"تصنيع — {product.name_ar}",
            "recipe_count": RecipeLine.objects.filter(manufactured_product=product).count(),
        }

    def on_valid():
        raw = (request.POST.get("quantity") or "").strip()
        note = (request.POST.get("note") or "").strip()[:500]
        try:
            qty = Decimal(raw)
        except (InvalidOperation, ValueError, TypeError):
            raise PanelFormInvalid("أدخل كمية صالحة")
        if qty <= 0:
            raise PanelFormInvalid("الكمية يجب أن تكون أكبر من صفر")
        try:
            session = SessionService.get_open_session()
            record_manufacturing_batch(
                product=product, quantity=qty, session=session, note=note
            )
        except ValueError as exc:
            code = str(exc)
            if code.startswith("INSUFFICIENT_STOCK:"):
                raise PanelFormInvalid("المواد غير كافية في المخزون")
            if code == "NO_RECIPE":
                raise PanelFormInvalid("أضف معادلة تصنيع أولاً")
            raise PanelFormInvalid(code)
        jr = panel_json_ok(request, reload=True, message="تم تسجيل دفعة التصنيع")
        if jr:
            return jr
        messages.success(request, "تم تسجيل دفعة التصنيع")
        return redirect(reverse("shell:product_card", args=[pk]))

    if request.method == "POST":
        return handle_panel_form(
            request,
            template_name=tpl,
            build_context=build_context,
            on_valid=on_valid,
        )
    return render_panel(request, tpl, build_context())


def recipe_add_panel(request, pk):
    from apps.core.panel import PanelFormInvalid, handle_panel_form, panel_json_ok, render_panel

    product = get_object_or_404(
        Product, pk=pk, product_type=Product.ProductType.MANUFACTURED
    )
    tpl = "shell/panels/recipe_add_panel.html"

    def build_context():
        return {
            "product": product,
            "form_action": reverse("shell:recipe_add_panel", args=[pk]),
            "panel_title": f"معادلة — {product.name_ar}",
            "recipe_rows": _recipe_form_rows(product),
            "raw_materials_search_url": reverse("shell:raw_materials_search"),
        }

    def on_valid():
        added, errors = _save_recipe_lines_from_post(request, product)
        if not added and not errors:
            raise PanelFormInvalid("أضف مكوّناً واحداً على الأقل")
        if errors:
            raise PanelFormInvalid("؛ ".join(errors[:3]))
        jr = panel_json_ok(request, reload=True, message="تم حفظ معادلة التصنيع")
        if jr:
            return jr
        messages.success(request, "تم حفظ معادلة التصنيع")
        return redirect(reverse("shell:product_card", args=[pk]))

    if request.method == "POST":
        return handle_panel_form(
            request,
            template_name=tpl,
            build_context=build_context,
            on_valid=on_valid,
            wide=True,
        )
    return render_panel(request, tpl, build_context(), wide=True)


def recipe_list_panel(request, pk):
    from apps.core.panel import render_panel

    ctx = _recipe_list_panel_context(request, pk)
    ctx["panel_title"] = f"معادلة — {ctx['product'].name_ar}"
    return render_panel(
        request,
        "shell/panels/recipe_list_panel.html",
        ctx,
        wide=True,
    )


def unit_create_panel(request):
    tpl = "shell/panels/unit_create_panel.html"

    def build_context():
        form = UnitForm(request.POST or None)
        panelize_form(form)
        return {"form": form, "form_action": reverse("shell:unit_create_panel"), "panel_title": "إضافة وحدة"}

    def on_valid():
        form = UnitForm(request.POST)
        if not form.is_valid():
            raise PanelFormInvalid("راجع البيانات")
        form.save()

    return handle_panel_form(request, template_name=tpl, build_context=build_context, on_valid=on_valid)


def unit_edit_panel(request, pk):
    unit = get_object_or_404(Unit, pk=pk)
    tpl = "shell/panels/unit_edit_panel.html"

    def build_context():
        form = UnitForm(request.POST or None, instance=unit)
        panelize_form(form)
        return {
            "form": form,
            "form_action": reverse("shell:unit_edit_panel", args=[pk]),
            "panel_title": "تعديل وحدة",
        }

    def on_valid():
        form = UnitForm(request.POST, instance=unit)
        if not form.is_valid():
            raise PanelFormInvalid("راجع البيانات")
        form.save()

    return handle_panel_form(request, template_name=tpl, build_context=build_context, on_valid=on_valid)
