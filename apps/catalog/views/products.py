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

def manufactured_product_create(request):
    from decimal import Decimal, InvalidOperation

    categories = Category.objects.filter(is_active=True).order_by("name_ar")
    units = Unit.objects.all()
    components = Product.objects.filter(
        is_active=True,
        product_type=Product.ProductType.RAW,
    ).select_related("unit").order_by("name_ar")
    errors = []

    if request.method == "POST":
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

        if not errors:
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
            messages.success(request, f"تم إنشاء المنتج المصنع «{product.name_ar}» ومعادلة التصنيع.")
            return _catalog_redirect(request, "recipe_list", pk=product.pk)

    return render(
        request,
        "shell/manufactured_product_form.html",
        _catalog_ctx(
            request,
            categories=categories,
            units=units,
            components=components,
            errors=errors,
            range10=range(10),
        ),
    )


def product_card(request, pk):
    from datetime import datetime
    from decimal import Decimal
    from django.db.models import Sum, Count

    product = get_object_or_404(Product, pk=pk)

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

    try:
        sb = product.stock_balance
        on_hand = sb.quantity_on_hand
        avg_cost = sb.average_cost
        stock_value = (on_hand * avg_cost).quantize(Decimal("0.01"))
    except StockBalance.DoesNotExist:
        on_hand = Decimal("0")
        avg_cost = Decimal("0")
        stock_value = Decimal("0")

    movements_qs = product.stock_movements.order_by("-created_at")
    if date_from:
        movements_qs = movements_qs.filter(created_at__date__gte=date_from)
    if date_to:
        movements_qs = movements_qs.filter(created_at__date__lte=date_to)
    movements = movements_qs[:100]

    sales_qs = SaleInvoiceLine.objects.filter(product=product, invoice__is_cancelled=False)
    if date_from:
        sales_qs = sales_qs.filter(invoice__created_at__date__gte=date_from)
    if date_to:
        sales_qs = sales_qs.filter(invoice__created_at__date__lte=date_to)
    sales_stats = sales_qs.aggregate(
        total_qty=Sum("quantity"),
        total_revenue=Sum("line_subtotal"),
        total_profit=Sum("line_profit"),
        sale_count=Count("id"),
    )

    purchase_lines = (
        PurchaseLine.objects.filter(product=product)
        .select_related("purchase__supplier")
        .order_by("-purchase__created_at")
    )
    suppliers_set = {}
    last_purchase_cost = None
    for pl in purchase_lines[:50]:
        sup = pl.purchase.supplier
        if sup.pk not in suppliers_set:
            suppliers_set[sup.pk] = {
                "supplier": sup,
                "last_cost": pl.unit_cost,
                "last_date": pl.purchase.created_at,
            }
        if last_purchase_cost is None:
            last_purchase_cost = pl.unit_cost
    suppliers_list = list(suppliers_set.values())

    used_in_recipes = RecipeLine.objects.filter(component=product).select_related("manufactured_product")

    bom_cost = None
    recipe_count = 0
    manufacturing_batches = []
    if product.product_type == Product.ProductType.MANUFACTURED:
        bom_cost = get_unit_cost(product)
        recipe_count = RecipeLine.objects.filter(manufactured_product=product).count()
        manufacturing_batches = list(
            ManufacturingBatch.objects.filter(product=product).order_by("-created_at")[:50]
        )

    return render(request, "catalog/product_card.html", {
        "product": product,
        "on_hand": on_hand,
        "avg_cost": avg_cost,
        "stock_value": stock_value,
        "movements": movements,
        "sales_stats": sales_stats,
        "suppliers_list": suppliers_list,
        "last_purchase_cost": last_purchase_cost,
        "used_in_recipes": used_in_recipes,
        "bom_cost": bom_cost,
        "recipe_count": recipe_count,
        "date_from": date_from,
        "date_to": date_to,
        "manufacturing_batches": manufacturing_batches,
    })


def product_create(request):
    """يُحوّل لمساحة الإعداد الموحّدة (تصنيف + وحدة + منتج في صفحة واحدة)."""
    return _catalog_redirect(request, "product_workspace")


def product_delete(request, pk):
    product = get_object_or_404(Product, pk=pk)
    if OrderLine.objects.filter(
        product=product,
        order__status=Order.Status.OPEN,
        order__is_cancelled=False,
    ).exists():
        messages.error(
            request,
            "لا يمكن الحذف: المنتج في طلب نقطة بيع لم يُغلق بعد. أزل السطر أو أكمل الطلب، أو عطّل المنتج من «تعطيل».",
        )
        return _catalog_redirect(request, "product_list")
    if SaleInvoiceLine.objects.filter(product=product).exists():
        messages.error(request, "لا يمكن الحذف: المنتج مسجّل في فواتير بيع.")
        return _catalog_redirect(request, "product_list")
    if PurchaseLine.objects.filter(product=product).exists():
        messages.error(request, "لا يمكن الحذف: المنتج في فواتير شراء.")
        return _catalog_redirect(request, "product_list")
    if StockTakeLine.objects.filter(product=product).exists():
        messages.error(request, "لا يمكن الحذف: المنتج مستخدم في جرد.")
        return _catalog_redirect(request, "product_list")
    OrderLine.objects.filter(product=product).delete()
    RecipeLine.objects.filter(Q(manufactured_product=product) | Q(component=product)).delete()
    StockMovement.objects.filter(product=product).delete()
    name = product.name_ar
    product.delete()
    messages.success(request, f"تم حذف المنتج «{name}» نهائياً.")
    return _catalog_redirect(request, "product_list")


def product_edit(request, pk):
    product = get_object_or_404(Product, pk=pk)
    if request.method == "POST":
        form = ProductForm(request.POST, instance=product)
        if form.is_valid():
            form.save()
            messages.success(request, "تم تعديل المنتج بنجاح")
            return _catalog_redirect(request, "product_list")
    else:
        form = ProductForm(instance=product)
    tpl = "shell/products_form.html"
    return render(request, tpl, _catalog_ctx(request, form=form))


def product_list(request):
    active_tab = request.GET.get("tab", "products")
    if active_tab not in ("products", "units", "categories"):
        active_tab = "products"

    product_filters = parse_product_filters(request.GET)
    unit_filters = parse_unit_filters(request.GET)
    category_filters = parse_category_filters(request.GET)

    q = (
        product_filters["q"]
        if active_tab == "products"
        else unit_filters["q"]
        if active_tab == "units"
        else category_filters["q"]
    )

    ctx = _catalog_ctx(
        request,
        q=q,
        active_tab=active_tab,
        product_filters=product_filters,
        unit_filters=unit_filters,
        category_filters=category_filters,
        product_sort_choices=PRODUCT_SORT_CHOICES,
        unit_sort_choices=UNIT_SORT_CHOICES,
        category_sort_choices=CATEGORY_SORT_CHOICES,
        active_filter_choices=ACTIVE_FILTER_CHOICES,
        stock_filter_choices=STOCK_FILTER_CHOICES,
        parent_filter_choices=PARENT_FILTER_CHOICES,
        product_type_choices=Product.ProductType.choices,
        filter_category_options=category_filter_options(),
        filter_parent_options=parent_category_options(),
        filters_open=(
            products_filters_open(product_filters)
            if active_tab == "products"
            else units_filters_open(unit_filters)
            if active_tab == "units"
            else categories_filters_open(category_filters)
        ),
    )

    if active_tab == "products":
        qs = (
            Product.objects.select_related("category", "unit")
            .exclude(product_type=Product.ProductType.RAW)
            .annotate(recipe_line_count=Count("recipe_lines"))
        )
        qs = apply_product_filters(qs, product_filters)
        ctx.update(paginate_queryset(request, qs))
        ctx["products"] = ctx["page_obj"]
    elif active_tab == "units":
        uqs = Unit.objects.all()
        uqs = apply_unit_filters(uqs, unit_filters)
        ctx.update(paginate_queryset(request, uqs))
        ctx["units"] = ctx["page_obj"]
    else:
        cqs = Category.objects.select_related("parent").all()
        cqs = apply_category_filters(cqs, category_filters)
        ctx.update(paginate_queryset(request, cqs))
        ctx["categories"] = ctx["page_obj"]

    tab_queries = {}
    for tab_name in ("products", "units", "categories"):
        qd = request.GET.copy()
        qd["tab"] = tab_name
        qd.pop("page", None)
        tab_queries[tab_name] = qd.urlencode()
    ctx["tab_queries"] = tab_queries

    tpl = "shell/products_list.html"
    return render(request, tpl, ctx)


def product_manufacture_batch(request, pk):
    from decimal import Decimal, InvalidOperation

    product = get_object_or_404(Product, pk=pk, product_type=Product.ProductType.MANUFACTURED)
    raw = (request.POST.get("quantity") or "").strip()
    note = (request.POST.get("note") or "").strip()[:500]
    try:
        qty = Decimal(raw)
    except (InvalidOperation, ValueError, TypeError):
        messages.error(request, "أدخل كمية صالحة.")
        return redirect(reverse("shell:product_card", args=[pk]))
    if qty <= 0:
        messages.error(request, "الكمية يجب أن تكون أكبر من صفر.")
        return redirect(reverse("shell:product_card", args=[pk]))
    try:
        session = SessionService.get_open_session()
        batch = record_manufacturing_batch(product=product, quantity=qty, session=session, note=note)
        log_audit(
            request.user,
            "catalog.product.manufacture_batch",
            "inventory.ManufacturingBatch",
            batch.pk,
            {"qty": str(qty), "product_id": product.pk},
        )
        messages.success(request, f"تم تسجيل دفعة تصنيع: {qty} وحدة.")
    except ValueError as exc:
        code = str(exc)
        if code.startswith("INSUFFICIENT_STOCK:"):
            messages.error(request, "المواد غير كافية في المخزون لتنفيذ هذه الدفعة.")
        elif code == "NO_RECIPE":
            messages.error(request, "أضف معادلة تصنيع للمنتج أولاً.")
        else:
            messages.error(request, code)
    return redirect(reverse("shell:product_card", args=[pk]))


def product_manufacture_batch_void(request, pk):
    product = get_object_or_404(Product, pk=pk, product_type=Product.ProductType.MANUFACTURED)
    try:
        batch_id = int((request.POST.get("batch_id") or "").strip())
    except (TypeError, ValueError):
        batch_id = 0
    batch = get_object_or_404(ManufacturingBatch, pk=batch_id, product=product)
    try:
        void_manufacturing_batch(batch=batch)
        log_audit(
            request.user,
            "catalog.product.manufacture_batch_void",
            "inventory.ManufacturingBatch",
            batch_id,
            {"product_id": product.pk},
        )
        messages.success(request, "تم حذف دفعة التصنيع وإرجاع أثرها على المخزون.")
    except ValueError as exc:
        code = str(exc)
        if code == "INSUFFICIENT_STOCK_TO_VOID_BATCH":
            messages.error(
                request,
                "لا يمكن إلغاء الدفعة: رصيد المنتج المصنع أقل من كمية الدفعة (ربما بيع جزء منها).",
            )
        elif code == "MISSING_PRODUCTION_MOVEMENT":
            messages.error(request, "بيانات الدفعة غير مكتملة — تعذر الإلغاء.")
        else:
            messages.error(request, code)
    return redirect(reverse("shell:product_card", args=[pk]))


def product_toggle_active(request, pk):
    if request.method != "POST":
        return _catalog_redirect(request, "product_list")
    product = get_object_or_404(Product, pk=pk)
    product.is_active = not product.is_active
    product.save(update_fields=["is_active"])
    status = "تفعيل" if product.is_active else "تعطيل"
    messages.success(request, f"تم {status} المنتج «{product.name_ar}»")
    return _catalog_redirect(request, "product_list")


def product_workspace(request):
    """صفحة واحدة: إضافة تصنيف، وحدة، ومنتج دون التنقّل بين قوائم متفرّقة."""
    quick_cat = QuickCategoryForm()
    quick_unit = QuickUnitForm()
    product_form = ProductForm()
    if request.method == "POST":
        action = request.POST.get("workspace_action", "")
        if action == "add_category":
            quick_cat = QuickCategoryForm(request.POST)
            if quick_cat.is_valid():
                obj = quick_cat.save(commit=False)
                obj.sort_order = 0
                obj.is_active = True
                obj.save()
                messages.success(request, f"تم إضافة التصنيف «{obj.name_ar}» — يمكنك اختياره في النموذج أدناه.")
                return _catalog_redirect(request, "product_workspace")
        elif action == "add_unit":
            quick_unit = QuickUnitForm(request.POST)
            if quick_unit.is_valid():
                obj = quick_unit.save()
                messages.success(request, f"تم إضافة الوحدة «{obj.name_ar}» — يمكنك اختيارها في النموذج أدناه.")
                return _catalog_redirect(request, "product_workspace")
        elif action == "save_product":
            product_form = ProductForm(request.POST)
            if product_form.is_valid():
                product_form.save()
                messages.success(request, "تم إضافة المنتج بنجاح.")
                return _catalog_redirect(request, "product_list")
        else:
            messages.error(request, "إجراء غير معروف.")
    return render(
        request,
        "shell/products_workspace.html",
        _catalog_ctx(request, quick_cat=quick_cat, quick_unit=quick_unit, product_form=product_form),
    )


def recipe_add(request, pk):
    product = get_object_or_404(
        Product, pk=pk, product_type=Product.ProductType.MANUFACTURED
    )
    errors = []

    if request.method == "POST":
        added, errors = _save_recipe_lines_from_post(request, product)
        if added > 0 and not errors:
            messages.success(request, f"تم إضافة {added} مكوّن بنجاح")
            return _catalog_redirect(request, "recipe_list", pk=product.pk)
        elif not errors:
            errors.append("يرجى إدخال مكوّن واحد على الأقل")

    return render(
        request,
        "shell/recipe_form.html",
        _catalog_ctx(
            request,
            product=product,
            errors=errors,
            recipe_rows=_recipe_form_rows(product),
            raw_materials_search_url=reverse("shell:raw_materials_search"),
        ),
    )


def recipe_delete(request, pk, line_id):
    product = get_object_or_404(
        Product, pk=pk, product_type=Product.ProductType.MANUFACTURED
    )
    line = get_object_or_404(RecipeLine, pk=line_id, manufactured_product=product)
    if request.method == "POST":
        line.delete()
        from apps.core.panel import panel_json_ok

        jr = panel_json_ok(request, reload=True, message="تم حذف المكوّن")
        if jr:
            return jr
        messages.success(request, "تم حذف المكوّن من الوصفة")
    return _catalog_redirect(request, "recipe_list", pk=product.pk)


def recipe_list(request, pk):
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
            sb = comp.stock_balance
            on_hand = sb.quantity_on_hand
        except StockBalance.DoesNotExist:
            on_hand = Decimal("0")
        max_units = (on_hand / qty).quantize(Decimal("1"), rounding="ROUND_DOWN") if qty > 0 else Decimal("0")
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

    profit = (product.selling_price - bom_cost).quantize(Decimal("0.01")) if bom_cost else None
    margin = ((profit / product.selling_price) * 100).quantize(Decimal("0.1")) if profit and product.selling_price else None
    max_producible = min((e["max_units"] for e in enriched), default=Decimal("0")) if enriched else Decimal("0")

    return render(
        request,
        "shell/recipe_list.html",
        _catalog_ctx(
            request,
            product=product,
            enriched=enriched,
            bom_cost=bom_cost,
            profit=profit,
            margin=margin,
            max_producible=max_producible,
        ),
    )
