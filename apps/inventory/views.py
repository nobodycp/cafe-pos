from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.db import transaction
from django.views.decorators.http import require_POST

from apps.catalog.models import Product, RecipeLine
from apps.core.models import log_audit
from apps.core.services import SessionService
from apps.inventory.forms import RawMaterialForm
from apps.inventory.models import StockBalance, StockMovement, StockTake, StockTakeLine
from apps.inventory.routing import inventory_url_namespace
from apps.inventory.services import ensure_stock_balance, adjust_stock, get_unit_cost, receive_purchase_stock
from apps.purchasing.models import PurchaseLine


def _inventory_ctx(request, **kwargs):
    ctx = {"inventory_ns": inventory_url_namespace(request)}
    ctx.update(kwargs)
    return ctx


def _inventory_reverse(request, viewname, *args, **kwargs):
    return reverse(f"{inventory_url_namespace(request)}:{viewname}", args=args, kwargs=kwargs)


def _inventory_redirect(request, viewname, *args, **kwargs):
    return redirect(_inventory_reverse(request, viewname, *args, **kwargs))


def _inventory_tpl(request, shell_tpl, classic_tpl):
    return shell_tpl if inventory_url_namespace(request) == "shell" else classic_tpl


@login_required
def inventory_home(request):
    rows = (
        StockBalance.objects.select_related("product", "product__unit")
        .filter(product__is_stock_tracked=True)
        .exclude(product__product_type=Product.ProductType.MANUFACTURED)
        .exclude(product__product_type=Product.ProductType.SERVICE)
        .exclude(product__product_type=Product.ProductType.COMMISSION)
        .order_by("product__name_ar")
    )
    for r in rows:
        r.value = (r.quantity_on_hand * r.average_cost).quantize(Decimal("0.01"))
    low = [r for r in rows if r.quantity_on_hand <= (r.product.min_stock_level or 0)]
    return render(
        request,
        _inventory_tpl(request, "shell/inventory_home.html", "inventory/home.html"),
        _inventory_ctx(request, balances=rows, low_stock=low),
    )


@login_required
def movement_list(request):
    mv = (
        StockMovement.objects.select_related("product", "product__unit")
        .order_by("-created_at")[:500]
    )
    return render(request, _inventory_tpl(request, "shell/inventory_movements.html", "inventory/movements.html"), _inventory_ctx(request, movements=mv))


@login_required
def stock_adjust(request):
    products = (
        Product.objects.filter(is_active=True, is_stock_tracked=True)
        .exclude(product_type=Product.ProductType.MANUFACTURED)
        .exclude(product_type=Product.ProductType.SERVICE)
        .exclude(product_type=Product.ProductType.COMMISSION)
        .order_by("name_ar")
    )
    errors = []

    if request.method == "POST":
        pid = request.POST.get("product_id")
        adj_type = request.POST.get("adj_type", "add")
        qty_str = request.POST.get("quantity", "0")
        cost_str = request.POST.get("unit_cost", "0")
        note = request.POST.get("note", "")

        try:
            product = Product.objects.get(pk=pid, is_active=True)
        except (Product.DoesNotExist, ValueError, TypeError):
            errors.append("اختر منتجاً صالحاً")
            return render(request, _inventory_tpl(request, "shell/inventory_adjust.html", "inventory/adjust.html"), _inventory_ctx(request, products=products, errors=errors))

        try:
            qty = Decimal(qty_str)
            cost = Decimal(cost_str) if cost_str else Decimal("0")
        except (InvalidOperation, ValueError):
            errors.append("أدخل كمية صالحة")
            return render(request, _inventory_tpl(request, "shell/inventory_adjust.html", "inventory/adjust.html"), _inventory_ctx(request, products=products, errors=errors))

        if qty <= 0:
            errors.append("الكمية يجب أن تكون أكبر من صفر")
            return render(request, _inventory_tpl(request, "shell/inventory_adjust.html", "inventory/adjust.html"), _inventory_ctx(request, products=products, errors=errors))

        session = SessionService.get_open_session()

        try:
            if adj_type == "add":
                if not product.is_stock_tracked:
                    product.is_stock_tracked = True
                    product.save(update_fields=["is_stock_tracked"])
                receive_purchase_stock(
                    product=product,
                    quantity=qty,
                    unit_cost=cost if cost > 0 else Decimal("0"),
                    session=session,
                    reference_model="manual",
                    reference_pk="adjust",
                    note=note or "إضافة مخزون يدوية",
                )
                messages.success(request, f"تم إضافة {qty} وحدة من «{product.name_ar}» للمخزون")

            elif adj_type == "set":
                sb = ensure_stock_balance(product)
                current = sb.quantity_on_hand
                delta = qty - current
                if delta != 0:
                    adjust_stock(
                        product=product,
                        quantity_delta=delta,
                        movement_type=StockMovement.MovementType.ADJUSTMENT,
                        session=session,
                        reference_model="manual",
                        reference_pk="adjust",
                        note=note or f"تسوية مخزون من {current} إلى {qty}",
                    )
                    if cost > 0:
                        sb.refresh_from_db()
                        sb.average_cost = cost
                        sb.save(update_fields=["average_cost", "updated_at"])
                messages.success(request, f"تم تعديل مخزون «{product.name_ar}» إلى {qty}")

            elif adj_type == "waste":
                adjust_stock(
                    product=product,
                    quantity_delta=-qty,
                    movement_type=StockMovement.MovementType.WASTE,
                    session=session,
                    reference_model="manual",
                    reference_pk="waste",
                    note=note or "هالك / تلف",
                )
                messages.success(request, f"تم تسجيل هالك {qty} وحدة من «{product.name_ar}»")

            log_audit(request.user, f"inventory.{adj_type}", "inventory.StockBalance", str(product.pk), {"qty": str(qty)})
            return _inventory_redirect(request, "home")

        except ValueError as e:
            errors.append(str(e))

    return render(request, _inventory_tpl(request, "shell/inventory_adjust.html", "inventory/adjust.html"), _inventory_ctx(request, products=products, errors=errors))


@login_required
def raw_material_list(request):
    materials = (
        Product.objects.filter(product_type=Product.ProductType.RAW, is_active=True)
        .select_related("unit")
        .order_by("name_ar")
    )
    enriched = []
    for m in materials:
        try:
            sb = m.stock_balance
            on_hand = sb.quantity_on_hand
            avg_cost = sb.average_cost
            value = (on_hand * avg_cost).quantize(Decimal("0.01"))
        except StockBalance.DoesNotExist:
            on_hand = Decimal("0")
            avg_cost = Decimal("0")
            value = Decimal("0")
        low = on_hand <= (m.min_stock_level or 0)
        enriched.append({"material": m, "on_hand": on_hand, "avg_cost": avg_cost, "value": value, "low": low})
    return render(request, _inventory_tpl(request, "shell/raw_materials.html", "inventory/raw_materials.html"), _inventory_ctx(request, materials=enriched))


@login_required
def raw_material_create(request):
    if request.method == "POST":
        form = RawMaterialForm(request.POST)
        if form.is_valid():
            mat = form.save()
            ensure_stock_balance(mat)
            log_audit(request.user, "inventory.raw_material.create", "catalog.Product", mat.pk, {})
            messages.success(request, f"تم إضافة المادة الخام «{mat.name_ar}» بنجاح")
            return _inventory_redirect(request, "raw_materials")
    else:
        form = RawMaterialForm()
    return render(request, _inventory_tpl(request, "shell/raw_material_form.html", "inventory/raw_material_form.html"), _inventory_ctx(request, form=form, title="إضافة مادة خام"))


@login_required
def raw_material_edit(request, pk):
    material = get_object_or_404(Product, pk=pk, product_type=Product.ProductType.RAW)
    if request.method == "POST":
        form = RawMaterialForm(request.POST, instance=material)
        if form.is_valid():
            form.save()
            messages.success(request, f"تم تعديل «{material.name_ar}» بنجاح")
            return _inventory_redirect(request, "raw_materials")
    else:
        form = RawMaterialForm(instance=material)
    return render(request, _inventory_tpl(request, "shell/raw_material_form.html", "inventory/raw_material_form.html"), _inventory_ctx(request, form=form, title=f"تعديل: {material.name_ar}", material=material))


@login_required
@require_POST
@transaction.atomic
def raw_material_delete(request, pk):
    material = get_object_or_404(Product, pk=pk, product_type=Product.ProductType.RAW)
    if PurchaseLine.objects.filter(product=material).exists():
        messages.error(request, "لا يمكن حذف المادة الخام: مستخدمة في فواتير شراء.")
        return _inventory_redirect(request, "raw_materials")
    if RecipeLine.objects.filter(component=material).exists():
        messages.error(request, "لا يمكن حذف المادة الخام: مستخدمة في معادلات تصنيع.")
        return _inventory_redirect(request, "raw_materials")
    if StockTakeLine.objects.filter(product=material).exists():
        messages.error(request, "لا يمكن حذف المادة الخام: مستخدمة في جرد.")
        return _inventory_redirect(request, "raw_materials")
    name = material.name_ar
    StockMovement.objects.filter(product=material).delete()
    StockBalance.objects.filter(product=material).delete()
    material.delete()
    messages.success(request, f"تم حذف المادة الخام «{name}».")
    return _inventory_redirect(request, "raw_materials")


@login_required
def stocktake_list(request):
    takes = StockTake.objects.order_by("-created_at")[:50]
    return render(request, _inventory_tpl(request, "shell/stocktake_list.html", "inventory/stocktake_list.html"), _inventory_ctx(request, takes=takes))


@login_required
def stocktake_create(request):
    from apps.core.services import SessionService

    if request.method == "POST":
        session = SessionService.get_open_session()
        note = request.POST.get("note", "")
        take = StockTake.objects.create(work_session=session, note=note)

        products = Product.objects.filter(
            is_active=True, is_stock_tracked=True
        ).exclude(
            product_type__in=[Product.ProductType.MANUFACTURED, Product.ProductType.SERVICE, Product.ProductType.COMMISSION]
        ).order_by("name_ar")

        for p in products:
            try:
                sb = p.stock_balance
                sys_qty = sb.quantity_on_hand
            except StockBalance.DoesNotExist:
                sys_qty = Decimal("0")
            StockTakeLine.objects.create(
                stock_take=take,
                product=p,
                system_quantity=sys_qty,
            )

        messages.success(request, f"تم إنشاء جرد جديد ({take.lines.count()} صنف)")
        return _inventory_redirect(request, "stocktake_edit", pk=take.pk)

    return render(request, _inventory_tpl(request, "shell/stocktake_create.html", "inventory/stocktake_create.html"), _inventory_ctx(request))


@login_required
def stocktake_edit(request, pk):
    take = get_object_or_404(StockTake, pk=pk)
    if take.status == StockTake.Status.APPROVED:
        messages.warning(request, "هذا الجرد معتمد ولا يمكن تعديله")
        return _inventory_redirect(request, "stocktake_detail", pk=take.pk)

    lines = take.lines.select_related("product", "product__unit").order_by("product__name_ar")

    if request.method == "POST":
        updated = 0
        for line in lines:
            val = request.POST.get(f"actual_{line.pk}", "").strip()
            if val:
                try:
                    actual = Decimal(val)
                    line.actual_quantity = actual
                    line.difference = actual - line.system_quantity
                    line.save(update_fields=["actual_quantity", "difference", "updated_at"])
                    updated += 1
                except (InvalidOperation, ValueError):
                    pass
        messages.success(request, f"تم حفظ {updated} صنف")
        return _inventory_redirect(request, "stocktake_edit", pk=take.pk)

    return render(request, _inventory_tpl(request, "shell/stocktake_edit.html", "inventory/stocktake_edit.html"), _inventory_ctx(request, take=take, lines=lines))


@login_required
def stocktake_approve(request, pk):
    if request.method != "POST":
        return _inventory_redirect(request, "stocktake_edit", pk=pk)

    take = get_object_or_404(StockTake, pk=pk)
    if take.status == StockTake.Status.APPROVED:
        messages.warning(request, "الجرد معتمد بالفعل")
        return _inventory_redirect(request, "stocktake_detail", pk=take.pk)

    from django.utils import timezone

    lines_with_diff = take.lines.filter(actual_quantity__isnull=False).exclude(difference=0).select_related("product")
    session = take.work_session

    for line in lines_with_diff:
        adjust_stock(
            product=line.product,
            quantity_delta=line.difference,
            movement_type=StockMovement.MovementType.ADJUSTMENT,
            session=session,
            reference_model="inventory.StockTake",
            reference_pk=str(take.pk),
            note=f"تسوية جرد #{take.pk}: من {line.system_quantity} إلى {line.actual_quantity}",
        )

    take.status = StockTake.Status.APPROVED
    take.approved_at = timezone.now()
    take.save(update_fields=["status", "approved_at", "updated_at"])

    log_audit(request.user, "inventory.stocktake.approved", "inventory.StockTake", take.pk, {"lines": lines_with_diff.count()})
    messages.success(request, f"تم اعتماد الجرد — {lines_with_diff.count()} تسوية")
    return _inventory_redirect(request, "stocktake_detail", pk=take.pk)


@login_required
def stocktake_detail(request, pk):
    take = get_object_or_404(StockTake, pk=pk)
    lines = take.lines.select_related("product", "product__unit").order_by("product__name_ar")
    total_diff = sum(abs(l.difference) for l in lines if l.actual_quantity is not None and l.difference != 0)
    diff_count = sum(1 for l in lines if l.actual_quantity is not None and l.difference != 0)
    return render(request, _inventory_tpl(request, "shell/stocktake_detail.html", "inventory/stocktake_detail.html"), _inventory_ctx(request, take=take, lines=lines, total_diff=total_diff, diff_count=diff_count))


@login_required
def low_stock_alerts(request):
    from django.db.models import F
    alerts = StockBalance.objects.filter(
        product__is_active=True,
        product__is_stock_tracked=True,
        product__min_stock_level__gt=0,
        quantity_on_hand__lte=F("product__min_stock_level"),
    ).select_related("product", "product__unit").order_by("product__name_ar")
    for sb in alerts:
        sb.deficit = sb.quantity_on_hand - sb.product.min_stock_level
    return render(request, _inventory_tpl(request, "shell/low_stock_alerts.html", "inventory/low_stock_alerts.html"), _inventory_ctx(request, alerts=alerts))


@login_required
def raw_material_card(request, pk):
    from datetime import datetime

    product = get_object_or_404(Product, pk=pk, product_type=Product.ProductType.RAW)

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

    unit_cost = get_unit_cost(product)

    return render(request, "inventory/raw_material_card.html", {
        "product": product,
        "on_hand": on_hand,
        "avg_cost": avg_cost,
        "stock_value": stock_value,
        "unit_cost": unit_cost,
        "movements": movements,
        "suppliers_list": suppliers_list,
        "last_purchase_cost": last_purchase_cost,
        "used_in_recipes": used_in_recipes,
        "date_from": date_from,
        "date_to": date_to,
    })
