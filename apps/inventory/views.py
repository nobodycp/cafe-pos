from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.db.models import DecimalField, ExpressionWrapper, F, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.db import transaction
from django.views.decorators.http import require_POST

from apps.catalog.models import Category, Product, RecipeLine
from apps.core.models import log_audit
from apps.core.pagination import paginate_queryset
from apps.core.panel import PanelFormInvalid, handle_panel_form, panelize_form
from apps.core.services import SessionService
from apps.inventory.forms import ManualStockMovementForm, RawMaterialForm
from apps.inventory.models import StockBalance, StockMovement, StockTake, StockTakeLine
from apps.inventory.services import (
    adjust_stock,
    delete_manual_stock_movement,
    ensure_stock_balance,
    get_unit_cost,
    is_manual_stock_movement,
    movement_rows_with_balance_after,
    receive_purchase_stock,
    stock_home_base_queryset,
    sync_missing_stock_balance_rows,
    update_manual_stock_movement,
)
from apps.purchasing.models import PurchaseLine


_SHELL_INV_VIEW = {
    "home": "inventory_home",
    "movements": "inventory_movements",
    "adjust": "inventory_adjust",
    "movement_create": "inventory_movement_create",
    "movement_edit": "inventory_movement_edit",
}


def _inventory_ctx(request, **kwargs):
    ctx = {"inventory_ns": "shell"}
    ctx.update(kwargs)
    return ctx


def _inventory_reverse(request, viewname, *args, **kwargs):
    vn = _SHELL_INV_VIEW.get(viewname, viewname)
    return reverse(f"shell:{vn}", args=args, kwargs=kwargs)


def _inventory_redirect(request, viewname, *args, **kwargs):
    return redirect(_inventory_reverse(request, viewname, *args, **kwargs))



@login_required
def inventory_home(request):
    sync_missing_stock_balance_rows()
    base_qs = (
        stock_home_base_queryset()
        .select_related("product", "product__unit", "product__category")
        .order_by("product__name_ar")
    )
    zero_min = Value(Decimal("0"), output_field=DecimalField(max_digits=20, decimal_places=6))

    filtered_qs = base_qs
    q_text = (request.GET.get("q") or "").strip()
    if q_text:
        filtered_qs = filtered_qs.filter(
            Q(product__name_ar__icontains=q_text)
            | Q(product__name_en__icontains=q_text)
            | Q(product__barcode__icontains=q_text)
        )

    cat_raw = request.GET.get("category", "").strip()
    if cat_raw:
        try:
            filtered_qs = filtered_qs.filter(product__category_id=int(cat_raw))
        except (TypeError, ValueError):
            cat_raw = ""

    ptype = (request.GET.get("product_type") or "").strip()
    valid_product_types = {c[0] for c in Product.ProductType.choices}
    if ptype not in valid_product_types:
        ptype = ""

    if ptype:
        filtered_qs = filtered_qs.filter(product__product_type=ptype)

    stock_filter = (request.GET.get("stock") or "").strip().lower()
    if request.GET.get("filter") == "low":
        stock_filter = "low"
    if stock_filter == "low":
        filtered_qs = filtered_qs.annotate(
            _min_lvl_f=Coalesce(F("product__min_stock_level"), zero_min)
        ).filter(quantity_on_hand__lte=F("_min_lvl_f"))
    elif stock_filter == "zero":
        filtered_qs = filtered_qs.filter(quantity_on_hand=0)
    elif stock_filter == "positive":
        filtered_qs = filtered_qs.filter(quantity_on_hand__gt=0)

    line_value_expr = ExpressionWrapper(
        F("quantity_on_hand") * F("average_cost"),
        output_field=DecimalField(max_digits=24, decimal_places=6),
    )
    total_row = filtered_qs.annotate(_line_val=line_value_expr).aggregate(total=Sum("_line_val"))
    inventory_total_value = (total_row["total"] or Decimal("0")).quantize(Decimal("0.01"))

    pag = paginate_queryset(request, filtered_qs)
    balances_page = list(pag["page_obj"])
    for r in balances_page:
        r.value = (r.quantity_on_hand * r.average_cost).quantize(Decimal("0.01"))

    categories = Category.objects.filter(is_active=True).order_by("sort_order", "name_ar")
    ctx = _inventory_ctx(
        request,
        balances=balances_page,
        inventory_total_value=inventory_total_value,
        filter_q=q_text,
        filter_category=cat_raw,
        filter_product_type=ptype,
        filter_stock=stock_filter,
        categories=categories,
        product_type_choices=[("", "كل الأنواع")] + list(Product.ProductType.choices),
    )
    ctx.update(pag)
    return render(
        request,
        "shell/inventory_home.html",
        ctx,
    )


@login_required
def movement_list(request):
    from datetime import datetime

    qs = StockMovement.objects.select_related("product", "product__unit", "work_session").order_by("-created_at", "-pk")

    q_text = (request.GET.get("q") or "").strip()
    if q_text:
        qs = qs.filter(
            Q(product__name_ar__icontains=q_text)
            | Q(product__name_en__icontains=q_text)
            | Q(product__barcode__icontains=q_text)
        )

    mtype = (request.GET.get("type") or "").strip()
    if mtype in {c[0] for c in StockMovement.MovementType.choices}:
        qs = qs.filter(movement_type=mtype)

    pid = (request.GET.get("product") or "").strip()
    if pid.isdigit():
        qs = qs.filter(product_id=int(pid))

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
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)

    filter_products = Product.objects.filter(is_active=True, is_stock_tracked=True).order_by("name_ar")[:500]

    ctx = _inventory_ctx(
        request,
        filter_q=q_text,
        filter_type=mtype,
        filter_product=pid,
        filter_date_from=date_from,
        filter_date_to=date_to,
        movement_type_choices=[("", "كل الأنواع")] + list(StockMovement.MovementType.choices),
    )
    pag = paginate_queryset(request, qs)
    ctx.update(pag)
    ctx["movement_rows"] = movement_rows_with_balance_after(pag["page_obj"])
    return render(request, "shell/inventory_movements.html", ctx)


@login_required
def movement_manual_create(request):
    session = SessionService.get_open_session()
    if request.method == "POST":
        form = ManualStockMovementForm(request.POST)
        if form.is_valid():
            product = form.cleaned_data["product"]
            kind = form.cleaned_data["kind"]
            qty = form.cleaned_data["quantity"]
            cost = form.cleaned_data.get("unit_cost") or Decimal("0")
            note = (form.cleaned_data.get("note") or "").strip()
            try:
                mv = None
                if kind == ManualStockMovementForm.KIND_PURCHASE:
                    if not product.is_stock_tracked:
                        product.is_stock_tracked = True
                        product.save(update_fields=["is_stock_tracked"])
                    mv = receive_purchase_stock(
                        product=product,
                        quantity=qty,
                        unit_cost=cost if cost > 0 else Decimal("0"),
                        session=session,
                        reference_model="manual",
                        reference_pk="adjust",
                        note=note or "إضافة مخزون يدوية",
                    )
                    messages.success(request, f"تم تسجيل حركة شراء يدوية: {qty} من «{product.name_ar}»")
                elif kind == ManualStockMovementForm.KIND_WASTE:
                    mv = adjust_stock(
                        product=product,
                        quantity_delta=-qty,
                        movement_type=StockMovement.MovementType.WASTE,
                        session=session,
                        reference_model="manual",
                        reference_pk="waste",
                        note=note or "هالك / تلف",
                    )
                    messages.success(request, f"تم تسجيل هالك: {qty} من «{product.name_ar}»")
                else:
                    mv = adjust_stock(
                        product=product,
                        quantity_delta=qty,
                        movement_type=StockMovement.MovementType.ADJUSTMENT,
                        session=session,
                        reference_model="manual",
                        reference_pk="adjust",
                        note=note or "تسوية يدوية",
                    )
                    messages.success(request, f"تم تسجيل تسوية: {qty} «{product.name_ar}»")
                log_audit(
                    request.user,
                    "inventory.movement.manual_create",
                    "inventory.StockMovement",
                    str(mv.pk),
                    {"product_id": product.pk, "kind": kind},
                )
                return _inventory_redirect(request, "movements")
            except ValueError as e:
                messages.error(request, str(e))
    else:
        form = ManualStockMovementForm()
    return render(
        request,
        "shell/inventory_movement_form.html",
        _inventory_ctx(request, form=form, title="إضافة حركة مخزون يدوية", is_edit=False, movement=None),
    )


@login_required
def movement_manual_edit(request, pk):
    mv = get_object_or_404(StockMovement.objects.select_related("product"), pk=pk)
    if not is_manual_stock_movement(mv):
        messages.error(request, "لا يمكن تعديل هذه الحركة لأنها مرتبطة بفاتورة أو عملية نظام.")
        return _inventory_redirect(request, "movements")

    session = SessionService.get_open_session()
    if mv.movement_type == StockMovement.MovementType.PURCHASE:
        initial_kind = ManualStockMovementForm.KIND_PURCHASE
        initial_qty = mv.quantity_delta
        initial_cost = mv.unit_cost or Decimal("0")
    elif mv.movement_type == StockMovement.MovementType.WASTE:
        initial_kind = ManualStockMovementForm.KIND_WASTE
        initial_qty = -mv.quantity_delta
        initial_cost = Decimal("0")
    else:
        initial_kind = ManualStockMovementForm.KIND_ADJUSTMENT
        initial_qty = mv.quantity_delta
        initial_cost = Decimal("0")

    if request.method == "POST":
        form = ManualStockMovementForm(request.POST)
        if form.is_valid():
            try:
                update_manual_stock_movement(
                    mv=mv,
                    product=form.cleaned_data["product"],
                    kind=form.cleaned_data["kind"],
                    quantity=form.cleaned_data["quantity"],
                    unit_cost=form.cleaned_data.get("unit_cost") or Decimal("0"),
                    note=(form.cleaned_data.get("note") or "").strip(),
                    session=session,
                )
                log_audit(request.user, "inventory.movement.manual_update", "inventory.StockMovement", str(pk), {})
                messages.success(request, "تم تحديث الحركة وتحديث الرصيد بنجاح.")
                return _inventory_redirect(request, "movements")
            except ValueError as e:
                messages.error(request, str(e))
    else:
        form = ManualStockMovementForm(
            initial={
                "product": mv.product_id,
                "kind": initial_kind,
                "quantity": initial_qty,
                "unit_cost": initial_cost,
                "note": mv.note,
            }
        )
    return render(
        request,
        "shell/inventory_movement_form.html",
        _inventory_ctx(request, form=form, title="تعديل حركة مخزون يدوية", is_edit=True, movement=mv),
    )


@login_required
@require_POST
@transaction.atomic
def movement_manual_delete(request, pk):
    mv = get_object_or_404(StockMovement, pk=pk)
    if not is_manual_stock_movement(mv):
        messages.error(request, "لا يمكن حذف هذه الحركة لأنها مرتبطة بفاتورة أو عملية نظام.")
        return _inventory_redirect(request, "movements")
    try:
        delete_manual_stock_movement(mv)
        log_audit(request.user, "inventory.movement.manual_delete", "inventory.StockMovement", str(pk), {})
        messages.success(request, "تم حذف الحركة وعكس أثرها على الرصيد.")
    except ValueError as e:
        messages.error(request, str(e))
    return _inventory_redirect(request, "movements")


@login_required
def stock_adjust(request):
    products = Product.objects.filter(is_active=True, is_stock_tracked=True).order_by("name_ar")
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
            return render(request, "shell/inventory_adjust.html", _inventory_ctx(request, products=products, errors=errors))

        try:
            qty = Decimal(qty_str)
            cost = Decimal(cost_str) if cost_str else Decimal("0")
        except (InvalidOperation, ValueError):
            errors.append("أدخل كمية صالحة")
            return render(request, "shell/inventory_adjust.html", _inventory_ctx(request, products=products, errors=errors))

        if qty <= 0:
            errors.append("الكمية يجب أن تكون أكبر من صفر")
            return render(request, "shell/inventory_adjust.html", _inventory_ctx(request, products=products, errors=errors))

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

    return render(request, "shell/inventory_adjust.html", _inventory_ctx(request, products=products, errors=errors))


@login_required
def raw_material_list(request):
    from apps.core.list_filters import get_search_q

    qs = (
        Product.objects.filter(product_type=Product.ProductType.RAW, is_active=True)
        .select_related("unit", "stock_balance")
        .order_by("name_ar")
    )
    q_text = get_search_q(request)
    if q_text:
        qs = qs.filter(
            Q(name_ar__icontains=q_text)
            | Q(name_en__icontains=q_text)
            | Q(barcode__icontains=q_text)
        )

    stock_filter = (request.GET.get("stock") or "").strip().lower()
    zero_min = Value(Decimal("0"), output_field=DecimalField(max_digits=20, decimal_places=6))
    if stock_filter == "low":
        qs = qs.annotate(_on_hand=Coalesce(F("stock_balance__quantity_on_hand"), zero_min)).annotate(
            _min_lvl=Coalesce(F("min_stock_level"), zero_min),
        ).filter(_on_hand__lte=F("_min_lvl"))
    elif stock_filter == "zero":
        qs = qs.filter(
            Q(stock_balance__isnull=True) | Q(stock_balance__quantity_on_hand=0),
        )
    elif stock_filter == "positive":
        qs = qs.filter(stock_balance__quantity_on_hand__gt=0)

    pag = paginate_queryset(request, qs)
    enriched = []
    for m in pag["page_obj"]:
        sb = getattr(m, "stock_balance", None)
        if sb:
            on_hand = sb.quantity_on_hand
            avg_cost = sb.average_cost
        else:
            on_hand = Decimal("0")
            avg_cost = Decimal("0")
        value = (on_hand * avg_cost).quantize(Decimal("0.01"))
        min_lvl = m.min_stock_level or Decimal("0")
        enriched.append({
            "material": m,
            "on_hand": on_hand,
            "avg_cost": avg_cost,
            "value": value,
            "low": on_hand <= min_lvl,
        })

    ctx = _inventory_ctx(
        request,
        materials=enriched,
        filter_q=q_text,
        filter_stock=stock_filter,
        filters_open=bool(q_text or stock_filter),
    )
    ctx.update(pag)
    return render(
        request,
        "shell/raw_materials.html",
        ctx,
    )


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
    return render(request, "shell/raw_material_form.html", _inventory_ctx(request, form=form, title="إضافة مادة خام"))


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
    return render(request, "shell/raw_material_form.html", _inventory_ctx(request, form=form, title=f"تعديل: {material.name_ar}", material=material))


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
    from apps.core.list_filters import get_search_q

    qs = StockTake.objects.order_by("-created_at", "-pk")
    q_text = get_search_q(request)
    if q_text:
        if q_text.isdigit():
            qs = qs.filter(Q(pk=int(q_text)) | Q(note__icontains=q_text))
        else:
            qs = qs.filter(note__icontains=q_text)

    status = (request.GET.get("status") or "").strip().lower()
    if status in ("draft", "approved"):
        qs = qs.filter(status=status)

    ctx = _inventory_ctx(request, filter_q=q_text, filter_status=status, filters_open=bool(q_text or status))
    pag = paginate_queryset(request, qs)
    ctx["takes"] = pag["page_obj"]
    ctx.update(pag)
    return render(
        request,
        "shell/stocktake_list.html",
        ctx,
    )


@login_required
def stocktake_create(request):
    from apps.core.services import SessionService

    if request.method == "POST":
        session = SessionService.get_open_session()
        note = request.POST.get("note", "")
        take = StockTake.objects.create(work_session=session, note=note)

        products = Product.objects.filter(is_active=True, is_stock_tracked=True).order_by("name_ar")

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

    return render(request, "shell/stocktake_create.html", _inventory_ctx(request))


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

    return render(request, "shell/stocktake_edit.html", _inventory_ctx(request, take=take, lines=lines))


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
    return render(request, "shell/stocktake_detail.html", _inventory_ctx(request, take=take, lines=lines, total_diff=total_diff, diff_count=diff_count))


@login_required
def low_stock_alerts(request):
    from apps.inventory.services import low_stock_alert_queryset

    alerts = list(low_stock_alert_queryset())
    for sb in alerts:
        sb.deficit = sb.quantity_on_hand - sb.product.min_stock_level
    return render(request, "shell/low_stock_alerts.html", _inventory_ctx(request, alerts=alerts))


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


@login_required
def stock_adjust_panel(request):
    products = Product.objects.filter(is_active=True, is_stock_tracked=True).order_by("name_ar")
    errors: list[str] = []
    tpl = "shell/panels/stock_adjust_panel.html"

    def build_context():
        return _inventory_ctx(
            request,
            products=products,
            errors=errors,
            form_action=reverse("shell:stock_adjust_panel"),
            panel_title="تسوية مخزون",
        )

    def on_valid():
        nonlocal errors
        errors = []
        pid = request.POST.get("product_id")
        adj_type = request.POST.get("adj_type", "add")
        qty_str = request.POST.get("quantity", "0")
        cost_str = request.POST.get("unit_cost", "0")
        note = request.POST.get("note", "")

        try:
            product = Product.objects.get(pk=pid, is_active=True)
        except (Product.DoesNotExist, ValueError, TypeError):
            raise PanelFormInvalid("اختر منتجاً صالحاً") from None

        try:
            qty = Decimal(qty_str)
            cost = Decimal(cost_str) if cost_str else Decimal("0")
        except (InvalidOperation, ValueError):
            raise PanelFormInvalid("أدخل كمية صالحة") from None

        if qty <= 0:
            raise PanelFormInvalid("الكمية يجب أن تكون أكبر من صفر")

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
            else:
                raise PanelFormInvalid("نوع التسوية غير معروف")
            log_audit(request.user, f"inventory.{adj_type}", "inventory.StockBalance", str(product.pk), {"qty": str(qty)})
        except ValueError as e:
            raise PanelFormInvalid(str(e)) from e

    return handle_panel_form(request, template_name=tpl, build_context=build_context, on_valid=on_valid)


@login_required
def raw_material_create_panel(request):
    tpl = "shell/panels/raw_material_create_panel.html"

    def build_context():
        form = RawMaterialForm(request.POST or None)
        panelize_form(form)
        return {
            "form": form,
            "form_action": reverse("shell:raw_material_create_panel"),
            "panel_title": "إضافة مادة خام",
        }

    def on_valid():
        form = RawMaterialForm(request.POST)
        if not form.is_valid():
            raise PanelFormInvalid("راجع البيانات")
        mat = form.save()
        ensure_stock_balance(mat)
        log_audit(request.user, "inventory.raw_material.create", "catalog.Product", mat.pk, {})

    return handle_panel_form(request, template_name=tpl, build_context=build_context, on_valid=on_valid)


@login_required
def raw_material_edit_panel(request, pk):
    material = get_object_or_404(Product, pk=pk, product_type=Product.ProductType.RAW)
    tpl = "shell/panels/raw_material_edit_panel.html"

    def build_context():
        form = RawMaterialForm(request.POST or None, instance=material)
        panelize_form(form)
        return {
            "form": form,
            "material": material,
            "form_action": reverse("shell:raw_material_edit_panel", args=[pk]),
            "panel_title": "تعديل مادة خام",
        }

    def on_valid():
        form = RawMaterialForm(request.POST, instance=material)
        if not form.is_valid():
            raise PanelFormInvalid("راجع البيانات")
        form.save()

    return handle_panel_form(request, template_name=tpl, build_context=build_context, on_valid=on_valid)
