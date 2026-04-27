from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from apps.catalog.forms import CategoryForm, ProductForm, RecipeLineForm, UnitForm
from apps.catalog.models import Category, Product, RecipeLine, Unit
from apps.inventory.models import StockBalance, StockMovement
from apps.inventory.services import get_unit_cost
from apps.billing.models import SaleInvoiceLine
from apps.purchasing.models import PurchaseLine


@login_required
def product_list(request):
    q = request.GET.get("q", "").strip()
    qs = Product.objects.select_related("category", "unit").exclude(product_type=Product.ProductType.RAW)
    if q:
        qs = qs.filter(name_ar__icontains=q)
    return render(request, "catalog/product_list.html", {"products": qs, "q": q})


@login_required
def product_create(request):
    if request.method == "POST":
        form = ProductForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "تم إضافة المنتج بنجاح")
            return redirect("catalog:product_list")
    else:
        form = ProductForm()
    return render(request, "catalog/product_form.html", {"form": form})


@login_required
def product_edit(request, pk):
    product = get_object_or_404(Product, pk=pk)
    if request.method == "POST":
        form = ProductForm(request.POST, instance=product)
        if form.is_valid():
            form.save()
            messages.success(request, "تم تعديل المنتج بنجاح")
            return redirect("catalog:product_list")
    else:
        form = ProductForm(instance=product)
    return render(request, "catalog/product_form.html", {"form": form})


@login_required
def product_toggle_active(request, pk):
    if request.method != "POST":
        return redirect("catalog:product_list")
    product = get_object_or_404(Product, pk=pk)
    product.is_active = not product.is_active
    product.save(update_fields=["is_active"])
    status = "تفعيل" if product.is_active else "تعطيل"
    messages.success(request, f"تم {status} المنتج «{product.name_ar}»")
    return redirect("catalog:product_list")


@login_required
def category_list(request):
    categories = Category.objects.select_related("parent").all()
    return render(request, "catalog/category_list.html", {"categories": categories})


@login_required
def category_create(request):
    if request.method == "POST":
        form = CategoryForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "تم إضافة التصنيف بنجاح")
            return redirect("catalog:category_list")
    else:
        form = CategoryForm()
    return render(request, "catalog/category_form.html", {"form": form})


@login_required
def category_edit(request, pk):
    category = get_object_or_404(Category, pk=pk)
    if request.method == "POST":
        form = CategoryForm(request.POST, instance=category)
        if form.is_valid():
            form.save()
            messages.success(request, "تم تعديل التصنيف بنجاح")
            return redirect("catalog:category_list")
    else:
        form = CategoryForm(instance=category)
    return render(request, "catalog/category_form.html", {"form": form})


@login_required
def unit_list(request):
    units = Unit.objects.all()
    return render(request, "catalog/unit_list.html", {"units": units})


@login_required
def unit_create(request):
    if request.method == "POST":
        form = UnitForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "تم إضافة الوحدة بنجاح")
            return redirect("catalog:unit_list")
    else:
        form = UnitForm()
    return render(request, "catalog/unit_form.html", {"form": form})


@login_required
def unit_edit(request, pk):
    unit = get_object_or_404(Unit, pk=pk)
    if request.method == "POST":
        form = UnitForm(request.POST, instance=unit)
        if form.is_valid():
            form.save()
            messages.success(request, "تم تعديل الوحدة بنجاح")
            return redirect("catalog:unit_list")
    else:
        form = UnitForm(instance=unit)
    return render(request, "catalog/unit_form.html", {"form": form})


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
        unit_code = comp.unit.code if comp.unit else ""
        if unit_code in ("kg", "kilo", "كيلو") and qty < 1:
            display_qty = (qty * 1000).quantize(Decimal("0.1"))
            display_unit = "غرام"
        elif unit_code in ("l", "liter", "لتر") and qty < 1:
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
        "catalog/recipe_list.html",
        {
            "product": product,
            "enriched": enriched,
            "bom_cost": bom_cost,
            "profit": profit,
            "margin": margin,
            "max_producible": max_producible,
        },
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
                if unit_mode == "gram" and comp.unit and comp.unit.code in ("kg", "kilo", "كيلو"):
                    qty = qty / Decimal("1000")
                elif unit_mode == "ml" and comp.unit and comp.unit.code in ("l", "liter", "لتر"):
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
            return redirect("catalog:recipe_list", pk=product.pk)
        elif not errors:
            errors.append("يرجى إدخال مكوّن واحد على الأقل")

    return render(
        request,
        "catalog/recipe_form.html",
        {"product": product, "components": components, "errors": errors, "range10": range(10)},
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
    return redirect("catalog:recipe_list", pk=product.pk)


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
    has_sub_unit = unit_code in ("kg", "kilo", "كيلو", "l", "liter", "لتر")
    sub_unit = ""
    if unit_code in ("kg", "kilo", "كيلو"):
        sub_unit = "غرام"
    elif unit_code in ("l", "liter", "لتر"):
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
