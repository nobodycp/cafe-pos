from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
import json
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.catalog.forms import (
    CategoryForm,
    ProductForm,
    QuickCategoryForm,
    QuickUnitForm,
    RecipeLineForm,
    UnitForm,
)
from apps.catalog.models import Category, Product, RecipeLine, Unit
from apps.inventory.models import StockBalance, StockMovement, StockTakeLine
from apps.inventory.services import get_unit_cost
from apps.billing.models import SaleInvoiceLine
from apps.pos.models import OrderLine
from apps.purchasing.models import PurchaseLine


WEIGHT_UNIT_CODES = {"kg", "kilo", "kilogram", "كيلو", "كيلوغرام"}
VOLUME_UNIT_CODES = {"l", "lt", "ltr", "liter", "litre", "lter", "لتر"}


def _unit_code(product: Product) -> str:
    return (product.unit.code if product.unit else "").strip().lower()


def _is_weight_unit(product: Product) -> bool:
    return _unit_code(product) in WEIGHT_UNIT_CODES


def _is_volume_unit(product: Product) -> bool:
    return _unit_code(product) in VOLUME_UNIT_CODES


def _catalog_ctx(request, **kwargs):
    ctx = {"catalog_ns": "shell"}
    ctx.update(kwargs)
    return ctx


def _catalog_reverse(request, viewname, *args, **kwargs):
    return reverse(f"shell:{viewname}", args=args, kwargs=kwargs)


def _catalog_redirect(request, viewname, *args, **kwargs):
    return redirect(_catalog_reverse(request, viewname, *args, **kwargs))


def _catalog_tpl(request, shell_tpl, classic_tpl):
    return shell_tpl


@login_required
def category_search(request):
    q = (request.GET.get("q") or "").strip()
    if len(q) < 1:
        return JsonResponse({"results": []})
    rows = Category.objects.filter(is_active=True, name_ar__icontains=q).order_by("name_ar")[:24]
    return JsonResponse({"results": [{"id": c.pk, "name_ar": c.name_ar} for c in rows]})


@login_required
@require_POST
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


@login_required
def product_list(request):
    q = request.GET.get("q", "").strip()
    active_tab = request.GET.get("tab", "products")
    if active_tab not in ("products", "units", "categories"):
        active_tab = "products"
    qs = Product.objects.select_related("category", "unit").exclude(product_type=Product.ProductType.RAW)
    if q:
        qs = qs.filter(name_ar__icontains=q)
    units = Unit.objects.all()
    categories = Category.objects.select_related("parent").all()
    if q and active_tab == "units":
        units = units.filter(Q(name_ar__icontains=q) | Q(name_en__icontains=q) | Q(code__icontains=q))
    if q and active_tab == "categories":
        categories = categories.filter(Q(name_ar__icontains=q) | Q(name_en__icontains=q))
    tpl = _catalog_tpl(request, "shell/products_list.html", "catalog/product_list.html")
    return render(
        request,
        tpl,
        _catalog_ctx(
            request,
            products=qs,
            units=units,
            categories=categories,
            q=q,
            active_tab=active_tab,
        ),
    )


@login_required
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
        _catalog_tpl(request, "shell/products_workspace.html", "catalog/product_workspace.html"),
        _catalog_ctx(request, quick_cat=quick_cat, quick_unit=quick_unit, product_form=product_form),
    )


@login_required
def product_create(request):
    """يُحوّل لمساحة الإعداد الموحّدة (تصنيف + وحدة + منتج في صفحة واحدة)."""
    return _catalog_redirect(request, "product_workspace")


@login_required
@transaction.atomic
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
        _catalog_tpl(request, "shell/manufactured_product_form.html", "catalog/recipe_form.html"),
        _catalog_ctx(
            request,
            categories=categories,
            units=units,
            components=components,
            errors=errors,
            range10=range(10),
        ),
    )


@login_required
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
    tpl = _catalog_tpl(request, "shell/products_form.html", "catalog/product_form.html")
    return render(request, tpl, _catalog_ctx(request, form=form))


@login_required
def product_toggle_active(request, pk):
    if request.method != "POST":
        return _catalog_redirect(request, "product_list")
    product = get_object_or_404(Product, pk=pk)
    product.is_active = not product.is_active
    product.save(update_fields=["is_active"])
    status = "تفعيل" if product.is_active else "تعطيل"
    messages.success(request, f"تم {status} المنتج «{product.name_ar}»")
    return _catalog_redirect(request, "product_list")


@login_required
@require_POST
@transaction.atomic
def product_delete(request, pk):
    product = get_object_or_404(Product, pk=pk)
    if OrderLine.objects.filter(product=product).exists():
        messages.error(request, "لا يمكن الحذف: المنتج مستخدم في طلبات. عطّله من «تعطيل» إن رغبت.")
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
    RecipeLine.objects.filter(Q(manufactured_product=product) | Q(component=product)).delete()
    StockMovement.objects.filter(product=product).delete()
    name = product.name_ar
    product.delete()
    messages.success(request, f"تم حذف المنتج «{name}» نهائياً.")
    return _catalog_redirect(request, "product_list")


@login_required
def category_list(request):
    categories = Category.objects.select_related("parent").all()
    tpl = _catalog_tpl(request, "shell/categories_list.html", "catalog/category_list.html")
    return render(request, tpl, _catalog_ctx(request, categories=categories))


@login_required
def category_create(request):
    if request.method == "POST":
        form = CategoryForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "تم إضافة التصنيف بنجاح")
            return _catalog_redirect(request, "category_list")
    else:
        form = CategoryForm()
    return render(request, _catalog_tpl(request, "shell/category_form.html", "catalog/category_form.html"), _catalog_ctx(request, form=form))


@login_required
def category_edit(request, pk):
    category = get_object_or_404(Category, pk=pk)
    if request.method == "POST":
        form = CategoryForm(request.POST, instance=category)
        if form.is_valid():
            form.save()
            messages.success(request, "تم تعديل التصنيف بنجاح")
            return _catalog_redirect(request, "category_list")
    else:
        form = CategoryForm(instance=category)
    return render(request, _catalog_tpl(request, "shell/category_form.html", "catalog/category_form.html"), _catalog_ctx(request, form=form))


@login_required
@require_POST
@transaction.atomic
def category_delete(request, pk):
    category = get_object_or_404(Category, pk=pk)
    if Product.objects.filter(category=category).exists():
        messages.error(request, "لا يمكن حذف التصنيف: توجد منتجات مرتبطة به.")
        return _catalog_redirect(request, "product_list")
    if Category.objects.filter(parent=category).exists():
        messages.error(request, "لا يمكن حذف التصنيف: توجد تصنيفات فرعية مرتبطة به.")
        return _catalog_redirect(request, "product_list")
    name = category.name_ar
    category.delete()
    messages.success(request, f"تم حذف التصنيف «{name}».")
    return _catalog_redirect(request, "product_list")


@login_required
def unit_list(request):
    units = Unit.objects.all()
    tpl = _catalog_tpl(request, "shell/units_list.html", "catalog/unit_list.html")
    return render(request, tpl, _catalog_ctx(request, units=units))


@login_required
def unit_create(request):
    if request.method == "POST":
        form = UnitForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "تم إضافة الوحدة بنجاح")
            return _catalog_redirect(request, "unit_list")
    else:
        form = UnitForm()
    return render(request, _catalog_tpl(request, "shell/unit_form.html", "catalog/unit_form.html"), _catalog_ctx(request, form=form))


@login_required
def unit_edit(request, pk):
    unit = get_object_or_404(Unit, pk=pk)
    if request.method == "POST":
        form = UnitForm(request.POST, instance=unit)
        if form.is_valid():
            form.save()
            messages.success(request, "تم تعديل الوحدة بنجاح")
            return _catalog_redirect(request, "unit_list")
    else:
        form = UnitForm(instance=unit)
    return render(request, _catalog_tpl(request, "shell/unit_form.html", "catalog/unit_form.html"), _catalog_ctx(request, form=form))


@login_required
@require_POST
@transaction.atomic
def unit_delete(request, pk):
    unit = get_object_or_404(Unit, pk=pk)
    if Product.objects.filter(unit=unit).exists():
        messages.error(request, "لا يمكن حذف الوحدة: توجد منتجات مرتبطة بها.")
        return _catalog_redirect(request, "product_list")
    name = unit.name_ar
    unit.delete()
    messages.success(request, f"تم حذف الوحدة «{name}».")
    return _catalog_redirect(request, "product_list")


@login_required
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
        _catalog_tpl(request, "shell/recipe_list.html", "catalog/recipe_list.html"),
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


@login_required
def recipe_add(request, pk):
    from decimal import Decimal, InvalidOperation

    product = get_object_or_404(
        Product, pk=pk, product_type=Product.ProductType.MANUFACTURED
    )
    components = Product.objects.filter(
        is_active=True, product_type=Product.ProductType.RAW
    ).select_related("unit").order_by("name_ar")
    errors = []

    if request.method == "POST":
        added = 0
        for i in range(10):
            comp_id = request.POST.get(f"component_{i}")
            qty_str = request.POST.get(f"qty_{i}", "").strip()
            unit_mode = request.POST.get(f"unit_mode_{i}", "base")
            if not comp_id or not qty_str:
                continue
            try:
                comp = Product.objects.get(pk=int(comp_id))
                qty = Decimal(qty_str)
                if qty <= 0:
                    errors.append(f"سطر {i+1}: الكمية يجب أن تكون أكبر من صفر")
                    continue
                if unit_mode == "gram" and _is_weight_unit(comp):
                    qty = qty / Decimal("1000")
                elif unit_mode == "ml" and _is_volume_unit(comp):
                    qty = qty / Decimal("1000")
                existing = RecipeLine.objects.filter(
                    manufactured_product=product, component=comp
                ).first()
                if existing:
                    existing.quantity_per_unit = qty
                    existing.save(update_fields=["quantity_per_unit", "updated_at"])
                else:
                    RecipeLine.objects.create(
                        manufactured_product=product,
                        component=comp,
                        quantity_per_unit=qty,
                    )
                added += 1
            except (Product.DoesNotExist, InvalidOperation, ValueError):
                errors.append(f"سطر {i+1}: بيانات غير صالحة")

        if added > 0 and not errors:
            messages.success(request, f"تم إضافة {added} مكوّن بنجاح")
            return _catalog_redirect(request, "recipe_list", pk=product.pk)
        elif not errors:
            errors.append("يرجى إدخال مكوّن واحد على الأقل")

    return render(
        request,
        _catalog_tpl(request, "shell/recipe_form.html", "catalog/recipe_form.html"),
        _catalog_ctx(request, product=product, components=components, errors=errors, range10=range(10)),
    )


@login_required
def recipe_delete(request, pk, line_id):
    product = get_object_or_404(
        Product, pk=pk, product_type=Product.ProductType.MANUFACTURED
    )
    line = get_object_or_404(RecipeLine, pk=line_id, manufactured_product=product)
    if request.method == "POST":
        line.delete()
        messages.success(request, "تم حذف المكوّن من الوصفة")
    return _catalog_redirect(request, "recipe_list", pk=product.pk)


@login_required
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


@login_required
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
    if product.product_type == Product.ProductType.MANUFACTURED:
        bom_cost = get_unit_cost(product)

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
        "date_from": date_from,
        "date_to": date_to,
    })
