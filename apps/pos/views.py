from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import models
from django.db.models import Count, Prefetch, Q, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.billing.models import InvoicePayment
from apps.billing.receipt_escpos import build_invoice_receipt
from apps.billing.tab_service import (
    apply_tab_payments_and_maybe_finalize,
    compute_order_totals,
    finalize_order_invoice,
    sum_tab_payments,
)
from apps.catalog.models import Category, Product, ProductModifierGroup
from apps.contacts.models import Customer
from apps.core.models import get_pos_settings, log_audit
from apps.core.services import SessionService
from apps.pos.models import DiningTable, Order, OrderLine, TableSession
from apps.pos.services import (
    add_or_update_line,
    adjust_line_quantity,
    create_order,
    delete_order_line,
    hold_order,
    set_line_note,
    set_line_unit_price,
)
from apps.pos.table_service import (
    floor_rows_for_session,
    open_or_resume_table_session,
)

def _get_order_for_session(order_id, **extra_filters):
    """Get an open order that belongs to the current work session."""
    session = SessionService.get_open_session()
    if not session:
        from django.http import Http404
        raise Http404
    return get_object_or_404(Order, pk=order_id, work_session=session, **extra_filters)


def _money(s: str) -> Decimal:
    return Decimal(str(s).replace(",", ".").strip() or "0")


def _payments_from_checkout_form(request, remaining: Decimal) -> list:
    mode = request.POST.get("payment_mode", "").strip()
    if remaining <= 0:
        return []
    single_modes = {
        "cash": InvoicePayment.Method.CASH,
        "bank_ps": InvoicePayment.Method.BANK_PS,
        "palpay": InvoicePayment.Method.PALPAY,
        "jawwalpay": InvoicePayment.Method.JAWWALPAY,
    }
    if mode in single_modes:
        return [(single_modes[mode], remaining)]
    if mode == "credit":
        return [(InvoicePayment.Method.CREDIT, remaining)]
    if mode == "mixed":
        c = _money(request.POST.get("pay_cash", "0"))
        b = _money(request.POST.get("pay_bank", "0"))
        cr = _money(request.POST.get("pay_credit", "0"))
        out = []
        if c > 0:
            out.append((InvoicePayment.Method.CASH, c))
        if b > 0:
            # مبلغ «شبكة» في المختلط بدون تفصيل المحفظة — يُسجَّل كبنك عام
            out.append((InvoicePayment.Method.BANK, b))
        if cr > 0:
            out.append((InvoicePayment.Method.CREDIT, cr))
        return out
    return []


@login_required
def pos_main(request):
    err = request.session.pop("flash_error", None)
    ok = request.session.pop("flash_ok", None)
    if err:
        messages.error(request, err)
    if ok:
        messages.success(request, ok)

    products_qs = (
        Product.objects.filter(is_active=True)
        .exclude(product_type=Product.ProductType.RAW)
        .select_related("category", "unit")
        .annotate(modifier_group_count=Count("modifier_groups"))
    )

    mod_pref = Prefetch("modifier_groups", queryset=ProductModifierGroup.objects.prefetch_related("options"))

    sections = []
    idx = 0
    for cat in (
        Category.objects.filter(is_active=True)
        .annotate(active_product_count=Count("products", filter=Q(products__is_active=True) & ~Q(products__product_type=Product.ProductType.RAW)))
        .select_related("parent")
        .order_by("sort_order", "name_ar")
    ):
        prods = list(products_qs.filter(category=cat).order_by("name_ar").prefetch_related(mod_pref)[:200])
        if not prods:
            continue
        cnt = cat.active_product_count
        sections.append({"category": cat, "product_rows": prods, "count": cnt, "tab_index": idx})
        idx += 1

    top_product_ids = list(
        OrderLine.objects.filter(order__status__in=[Order.Status.OPEN, Order.Status.CHECKED_OUT])
        .values("product_id")
        .annotate(total_sold=Sum("quantity"))
        .order_by("-total_sold")
        .values_list("product_id", flat=True)[:12]
    )
    if top_product_ids:
        featured_rows = list(
            Product.objects.filter(pk__in=top_product_ids, is_active=True)
            .exclude(product_type=Product.ProductType.RAW)
            .select_related("category", "unit")
            .annotate(modifier_group_count=Count("modifier_groups"))
            .prefetch_related(mod_pref)
        )
        id_order = {pid: i for i, pid in enumerate(top_product_ids)}
        featured_rows.sort(key=lambda p: id_order.get(p.pk, 999))
    else:
        featured_rows = list(
            Product.objects.filter(is_active=True)
            .exclude(product_type=Product.ProductType.RAW)
            .select_related("category", "unit")
            .annotate(modifier_group_count=Count("modifier_groups"))
            .prefetch_related(mod_pref)
            .order_by("name_ar")[:12]
        )

    tables = DiningTable.objects.filter(is_active=True, is_cancelled=False).order_by("sort_order", "name_ar")
    order = None
    order_totals = None
    tab_paid = Decimal("0")
    tab_balance = Decimal("0")
    oid = request.session.get("active_pos_order_id")
    session = SessionService.get_open_session()
    lines = []
    if oid and session:
        order = (
            Order.objects.filter(pk=oid, work_session=session, status=Order.Status.OPEN, is_held=False)
            .prefetch_related("lines__product")
            .first()
        )
        if not order:
            request.session.pop("active_pos_order_id", None)
        else:
            lines = list(order.lines.select_related("product").all())
            order_totals = compute_order_totals(order)
            tab_paid = sum_tab_payments(order)
            tab_balance = (order_totals["grand"] - tab_paid).quantize(Decimal("0.01"))
            if tab_balance < 0:
                tab_balance = Decimal("0")

    floor_rows = floor_rows_for_session(session) if session else []

    open_orders = []
    if session:
        from apps.billing.models import OrderPayment
        open_orders = list(
            Order.objects.filter(
                work_session=session,
                status=Order.Status.OPEN,
                order_type__in=[Order.OrderType.DELIVERY, Order.OrderType.TAKEAWAY],
            )
            .select_related("customer")
            .annotate(
                line_count=Count("lines"),
                total_qty=Sum("lines__quantity"),
                lines_total=Sum(
                    models.F("lines__quantity") * models.F("lines__unit_price"),
                    output_field=models.DecimalField(),
                ),
                paid_total=Sum("tab_payments__amount"),
            )
            .order_by("-created_at")
        )
        for o in open_orders:
            grand = (o.lines_total or Decimal("0")).quantize(Decimal("0.01"))
            paid = (o.paid_total or Decimal("0")).quantize(Decimal("0.01"))
            o.calc_grand = grand
            o.calc_paid = paid
            o.calc_balance = max(grand - paid, Decimal("0"))

    lang = request.LANGUAGE_CODE or "ar"
    return render(
        request,
        "pos/main.html",
        {
            "work_session": session,
            "product_sections": sections,
            "featured_rows": featured_rows,
            "tables": tables,
            "floor_rows": floor_rows,
            "current_order": order,
            "lines": lines,
            "order_totals": order_totals,
            "tab_paid": tab_paid,
            "tab_balance": tab_balance,
            "open_orders": open_orders,

            "ui_lang": lang,
            "cafe_name": settings.CAFE_NAME_AR if lang == "ar" else getattr(settings, "CAFE_NAME_EN", settings.CAFE_NAME_AR),
        },
    )


@login_required
def tables_floor(request):
    session = SessionService.get_open_session()
    if not session:
        messages.error(request, "افتح وردية عمل لعرض الطاولات.")
        return redirect("pos:main")
    rows = floor_rows_for_session(session)
    return render(
        request,
        "pos/tables_floor.html",
        {
            "work_session": session,
            "floor_rows": rows,
        },
    )


@login_required
@require_POST
def table_open(request):
    SessionService.require_open_session()
    tid = request.POST.get("table_id")
    table = get_object_or_404(DiningTable, pk=tid, is_active=True)
    guest_label = (request.POST.get("guest_label") or "").strip()[:160]
    customer = None
    cid = request.POST.get("customer_id")
    if cid:
        customer = get_object_or_404(Customer, pk=cid, is_active=True)
    try:
        _ts, order = open_or_resume_table_session(
            user=request.user,
            dining_table=table,
            customer=customer,
            guest_label=guest_label,
        )
    except ValueError as e:
        messages.error(request, str(e))
        return redirect("pos:tables_floor")
    if guest_label and order.table_session_id:
        ts = order.table_session
        if not ts.customer_id:
            ts.guest_label = guest_label[:160]
            ts.save(update_fields=["guest_label", "updated_at"])
    request.session["active_pos_order_id"] = order.id
    return redirect("pos:main")


@login_required
@require_GET
def customers_search(request):
    q = (request.GET.get("q") or "").strip()[:80]
    qs = Customer.objects.filter(is_active=True)
    if q:
        qs = qs.filter(Q(name_ar__icontains=q) | Q(phone__icontains=q) | Q(name_en__icontains=q))
    data = [{"id": c.pk, "name_ar": c.name_ar, "phone": c.phone or ""} for c in qs.order_by("name_ar")[:20]]
    return JsonResponse({"results": data})


@login_required
@require_GET
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


@login_required
@require_POST
def customer_quick_create(request):
    SessionService.require_open_session()
    name = (request.POST.get("name_ar") or "").strip()[:200]
    if not name:
        return JsonResponse({"ok": False, "error": "name_required"}, status=400)
    phone = (request.POST.get("phone") or "").strip()[:32]
    c = Customer.objects.create(name_ar=name, phone=phone)
    log_audit(request.user, "contacts.customer.quick_create", "contacts.Customer", c.pk, {})
    return JsonResponse({"ok": True, "id": c.pk, "name_ar": c.name_ar})


@login_required
@require_POST
def table_quick_create(request):
    SessionService.require_open_session()
    name = (request.POST.get("name_ar") or "").strip()[:100]
    if not name:
        return JsonResponse({"ok": False, "error": "name_required"}, status=400)
    max_order = DiningTable.objects.aggregate(m=models.Max("sort_order"))["m"] or 0
    t = DiningTable.objects.create(name_ar=name, sort_order=max_order + 1)
    log_audit(request.user, "pos.table.quick_create", "pos.DiningTable", t.pk, {"name": name})
    return JsonResponse({"ok": True, "id": t.pk, "name_ar": t.name_ar})


@login_required
def customize_product(request, order_id, product_id):
    order = get_object_or_404(Order, pk=order_id, status=Order.Status.OPEN, is_held=False)
    product = get_object_or_404(Product, pk=product_id, is_active=True)
    groups = list(product.modifier_groups.prefetch_related("options").order_by("sort_order", "id"))

    if request.method == "POST":
        ids = [int(x) for x in request.POST.getlist("modifier_option") if str(x).isdigit()]
        note = (request.POST.get("line_note") or "")[:255]
        try:
            qty = _money(request.POST.get("qty", "1"))
        except (InvalidOperation, ValueError):
            qty = Decimal("1")
        pos = get_pos_settings()
        bump = pos.kitchen_auto_print or request.POST.get("bump_kitchen") == "1"
        try:
            add_or_update_line(
                order=order,
                product=product,
                quantity_delta=qty,
                user=request.user,
                modifier_option_ids=ids,
                line_note=note,
                bump_kitchen=bump,
            )
        except ValueError as e:
            messages.error(request, str(e))
            return redirect("pos:customize_product", order_id=order.pk, product_id=product.pk)
        request.session["active_pos_order_id"] = order.id
        if pos.kitchen_auto_print:
            order.refresh_from_db(fields=["kitchen_batch_no"])
            return redirect("pos:kitchen_ticket", order_id=order.pk, batch_no=order.kitchen_batch_no)
        return redirect("pos:main")

    return render(
        request,
        "pos/customize_product.html",
        {"order": order, "product": product, "groups": groups},
    )


@login_required
@require_GET
def kitchen_ticket(request, order_id, batch_no):
    order = get_object_or_404(Order, pk=order_id)
    lines = list(order.lines.filter(kitchen_batch_no=int(batch_no)).select_related("product"))
    return render(request, "pos/kitchen_ticket.html", {
        "order": order,
        "batch_no": batch_no,
        "lines": lines,
    })


@login_required
@require_POST
def order_resume(request, order_id):
    session = SessionService.get_open_session()
    if not session:
        messages.error(request, "افتح وردية عمل أولاً.")
        return redirect("pos:main")
    order = get_object_or_404(Order, pk=order_id, work_session=session, status=Order.Status.OPEN)
    order.is_held = False
    order.save(update_fields=["is_held"])
    request.session["active_pos_order_id"] = order.id
    return redirect("pos:main")


@login_required
@require_POST
def order_new(request):
    try:
        SessionService.require_open_session()
    except ValueError:
        messages.error(request, "افتح وردية عمل قبل إنشاء طلب.")
        return redirect("pos:main")
    otype = request.POST.get("order_type", Order.OrderType.DINE_IN)
    tid = request.POST.get("table_id")
    if otype == Order.OrderType.DINE_IN:
        if not tid:
            messages.error(request, "اختر طاولة لطلب الصالة أو استخدم خريطة الطاولات.")
            return redirect("pos:main")
        table = get_object_or_404(DiningTable, pk=tid, is_active=True)
        _ts, order = open_or_resume_table_session(user=request.user, dining_table=table)
        request.session["active_pos_order_id"] = order.id
        return redirect("pos:main")
    order = create_order(user=request.user, order_type=otype, table=None, customer=None)
    request.session["active_pos_order_id"] = order.id
    return redirect("pos:main")


@login_required
@require_POST
def order_add_product(request, order_id):
    order = _get_order_for_session(order_id, status=Order.Status.OPEN, is_held=False)
    pid = request.POST.get("product_id")
    product = get_object_or_404(Product, pk=pid, is_active=True)
    try:
        qty = _money(request.POST.get("qty", "1"))
    except (InvalidOperation, ValueError):
        qty = Decimal("1")
    ids = [int(x) for x in request.POST.getlist("modifier_option") if str(x).isdigit()]
    pos = get_pos_settings()
    bump = pos.kitchen_auto_print or request.POST.get("bump_kitchen") == "1"
    try:
        add_or_update_line(
            order=order,
            product=product,
            quantity_delta=qty,
            user=request.user,
            modifier_option_ids=ids or None,
            line_note=(request.POST.get("line_note") or "")[:255],
            bump_kitchen=bump,
        )
    except ValueError as e:
        request.session["active_pos_order_id"] = order.id
        return _ajax_or_redirect_error(request, str(e))
    request.session["active_pos_order_id"] = order.id
    return _ajax_or_redirect(request)


@login_required
@require_POST
def order_adjust_line(request, order_id, line_id):
    order = _get_order_for_session(order_id, status=Order.Status.OPEN, is_held=False)
    try:
        dq = _money(request.POST.get("qty_delta", "0"))
    except (InvalidOperation, ValueError):
        dq = Decimal("0")
    try:
        adjust_line_quantity(order=order, line_id=int(line_id), quantity_delta=dq, user=request.user)
    except ValueError as e:
        request.session["active_pos_order_id"] = order.id
        return _ajax_or_redirect_error(request, str(e))
    request.session["active_pos_order_id"] = order.id
    return _ajax_or_redirect(request)


@login_required
@require_POST
def order_remove_line(request, order_id, line_id):
    order = _get_order_for_session(order_id, status=Order.Status.OPEN, is_held=False)
    try:
        delete_order_line(order=order, line_id=int(line_id), user=request.user)
    except ValueError as e:
        request.session["active_pos_order_id"] = order.id
        return _ajax_or_redirect_error(request, str(e))
    request.session["active_pos_order_id"] = order.id
    return _ajax_or_redirect(request)


@login_required
@require_POST
def order_line_note(request, order_id, line_id):
    order = _get_order_for_session(order_id, status=Order.Status.OPEN, is_held=False)
    try:
        set_line_note(order=order, line_id=int(line_id), line_note=request.POST.get("line_note", ""), user=request.user)
    except ValueError as e:
        messages.error(request, str(e))
    request.session["active_pos_order_id"] = order.id
    return redirect("pos:main")


@login_required
@require_POST
def order_line_unit_price(request, order_id, line_id):
    order = _get_order_for_session(order_id, status=Order.Status.OPEN, is_held=False)
    try:
        up = _money(request.POST.get("unit_price", "0"))
    except (InvalidOperation, ValueError):
        return _ajax_or_redirect_error(request, "سعر غير صالح")
    try:
        set_line_unit_price(order=order, line_id=int(line_id), unit_price=up, user=request.user)
    except ValueError as e:
        code = str(e)
        if code == "INVALID_UNIT_PRICE":
            return _ajax_or_redirect_error(request, "السعر لا يمكن أن يكون سالباً")
        return _ajax_or_redirect_error(request, code)
    request.session["active_pos_order_id"] = order.id
    return _ajax_or_redirect(request)


@login_required
@require_POST
def order_set_customer(request, order_id):
    order = _get_order_for_session(order_id, status=Order.Status.OPEN)
    cid = request.POST.get("customer_id")
    if cid:
        order.customer = get_object_or_404(Customer, pk=cid, is_active=True)
    else:
        order.customer = None
    order.save(update_fields=["customer", "updated_at"])
    if order.table_session_id:
        ts = order.table_session
        if order.customer and not ts.customer_id:
            ts.customer = order.customer
            ts.save(update_fields=["customer", "updated_at"])
    request.session["active_pos_order_id"] = order.id
    return redirect("pos:main")


@login_required
@require_POST
def order_note(request, order_id):
    order = _get_order_for_session(order_id, status=Order.Status.OPEN)
    order.order_note = request.POST.get("order_note", "")[:2000]
    order.save(update_fields=["order_note", "updated_at"])
    return _ajax_or_redirect(request)


@login_required
@require_POST
def order_discount(request, order_id):
    order = _get_order_for_session(order_id, status=Order.Status.OPEN)
    try:
        order.discount_amount = _money(request.POST.get("discount_amount", "0"))
        order.discount_percent = _money(request.POST.get("discount_percent", "0"))
    except (InvalidOperation, ValueError):
        pass
    order.save(update_fields=["discount_amount", "discount_percent", "updated_at"])
    return _ajax_or_redirect(request)


@login_required
def order_split(request, order_id):
    """Split selected lines from an order into a new order (for bill splitting)."""
    order = _get_order_for_session(order_id, status=Order.Status.OPEN, is_held=False)
    lines = list(order.lines.select_related("product").all())

    if request.method == "POST":
        selected_ids = [int(x) for x in request.POST.getlist("line_ids") if x.isdigit()]
        if not selected_ids:
            messages.error(request, "اختر أصنافاً لنقلها")
            return redirect("pos:order_split", order_id=order.pk)

        from django.db import transaction as db_transaction
        with db_transaction.atomic():
            session = SessionService.require_open_session()
            matching_lines = list(order.lines.filter(pk__in=selected_ids))
            if not matching_lines:
                messages.error(request, "لم يتم نقل أي أصناف")
                return redirect("pos:main")

            new_order = Order.objects.create(
                work_session=session,
                order_type=order.order_type,
                table=order.table,
                table_session=order.table_session,
                customer=order.customer,
            )
            for line in matching_lines:
                line.order = new_order
                line.save(update_fields=["order", "updated_at"])
            moved = len(matching_lines)

            log_audit(request.user, "pos.order.split", "pos.Order", order.pk, {
                "new_order": new_order.pk, "moved_lines": moved
            })

        request.session["active_pos_order_id"] = new_order.id
        messages.success(request, f"تم تقسيم الطلب — طلب جديد #{new_order.pk} ({moved} أصناف)")
        return redirect("pos:main")

    return render(request, "pos/order_split.html", {
        "order": order,
        "lines": lines,
    })


@login_required
@require_POST
def order_cancel(request, order_id):
    order = _get_order_for_session(order_id, status=Order.Status.OPEN)
    from apps.billing.models import SaleInvoice
    if SaleInvoice.objects.filter(order=order).exists():
        messages.error(request, "لا يمكن إلغاء طلب صدرت له فاتورة.")
        return redirect("pos:main")
    order.status = Order.Status.CANCELLED
    order.save(update_fields=["status", "updated_at"])
    if order.table_session_id:
        ts = order.table_session
        remaining_orders = Order.objects.filter(
            table_session=ts, status=Order.Status.OPEN
        ).exclude(pk=order.pk).exists()
        if not remaining_orders:
            from django.utils import timezone as tz
            ts.status = TableSession.Status.CLOSED
            ts.closed_at = tz.now()
            ts.save(update_fields=["status", "closed_at", "updated_at"])
    log_audit(request.user, "pos.order.cancel", "pos.Order", order.pk, {})
    request.session.pop("active_pos_order_id", None)
    messages.success(request, f"تم إلغاء الطلب #{order.pk}")
    return redirect("pos:main")


@login_required
@require_POST
def order_hold(request, order_id):
    order = _get_order_for_session(order_id, status=Order.Status.OPEN, is_held=False)
    try:
        hold_order(order=order, user=request.user)
    except ValueError as e:
        messages.error(request, str(e))
        return redirect("pos:main")
    request.session.pop("active_pos_order_id", None)
    messages.success(request, "تم تعليق الطلب — يمكن استئنافه من الطاولة.")
    return redirect("pos:tables_floor")



@login_required
@require_POST
def order_checkout(request, order_id):
    order = _get_order_for_session(order_id, status=Order.Status.OPEN, is_held=False)
    customer = None
    cid = request.POST.get("customer_id")
    if cid:
        customer = get_object_or_404(Customer, pk=cid)
    if not customer and order.customer_id:
        customer = order.customer

    totals = compute_order_totals(order)
    paid_so_far = sum_tab_payments(order)
    remaining = (totals["grand"] - paid_so_far).quantize(Decimal("0.01"))

    if remaining <= Decimal("0.005") and paid_so_far + Decimal("0.005") >= totals["grand"]:
        try:
            inv = finalize_order_invoice(order=order, user=request.user, customer=customer)
        except ValueError as e:
            code = str(e)
            if code == "TAB_PAYMENT_ON_EMPTY_ORDER":
                messages.error(
                    request,
                    "طلب فارغ به دفعات مسجّلة — أزل الدفعات أو ألغِ الطلب من الطاولة.",
                )
            else:
                messages.error(request, code)
            return redirect("pos:main")
        request.session.pop("active_pos_order_id", None)
        if inv:
            messages.success(request, f"تم إتمام البيع. رقم الفاتورة: {inv.invoice_number}")
            return redirect("pos:receipt", invoice_id=inv.pk)
        messages.success(request, "تم إغلاق الطاولة (طلب فارغ — دون فاتورة).")
        return redirect("pos:tables_floor")

    payments = _payments_from_checkout_form(request, remaining)
    new_sum = sum((p[1] for p in payments), Decimal("0")).quantize(Decimal("0.01"))

    if remaining > 0 and new_sum <= 0:
        messages.error(request, "أدخل مبلغ دفع أكبر من صفر.")
        return redirect("pos:main")
    if new_sum > remaining + Decimal("0.02"):
        messages.error(request, "مجموع الدفعات يتجاوز المتبقي على الطلب.")
        return redirect("pos:main")

    for _m, amt in payments:
        if _m == InvoicePayment.Method.CREDIT and amt > 0 and not customer:
            messages.error(request, "اختر عميلاً لجزء الائتمان.")
            return redirect("pos:main")

    try:
        inv = apply_tab_payments_and_maybe_finalize(
            order=order, user=request.user, payments=payments, customer=customer
        )
    except ValueError as e:
        code = str(e)
        if code.startswith("INSUFFICIENT_STOCK"):
            messages.error(request, "المخزون غير كافٍ لهذا البند.")
        elif code == "PAYMENT_SUM_MISMATCH":
            messages.error(request, "خطأ في تطابق المبالغ.")
        elif code == "CREDIT_REQUIRES_CUSTOMER":
            messages.error(request, "الائتمان يتطلب عميلاً.")
        elif code == "TAB_PAYMENT_ON_EMPTY_ORDER":
            messages.error(
                request,
                "طلب فارغ به دفعات مسجّلة — أزل الدفعات أو ألغِ الطلب من الطاولة.",
            )
        else:
            messages.error(request, code)
        return redirect("pos:main")

    if inv:
        request.session.pop("active_pos_order_id", None)
        messages.success(request, f"تم إتمام البيع. رقم الفاتورة: {inv.invoice_number}")
        return redirect("pos:receipt", invoice_id=inv.pk)
    order.refresh_from_db()
    if order.status == Order.Status.CHECKED_OUT:
        request.session.pop("active_pos_order_id", None)
        messages.success(request, "تم إغلاق الطاولة (طلب فارغ — دون فاتورة).")
        return redirect("pos:tables_floor")
    messages.success(request, f"تم تسجيل دفعة. المتبقي: {(remaining - new_sum).quantize(Decimal('0.01'))} ر.س")
    return redirect("pos:main")


@login_required
@require_GET
def cart_fragment(request):
    """Return just the cart HTML fragment for AJAX updates."""
    oid = request.session.get("active_pos_order_id")
    session = SessionService.get_open_session()
    order = None
    order_totals = None
    tab_paid = Decimal("0")
    tab_balance = Decimal("0")
    lines = []
    if oid and session:
        order = (
            Order.objects.filter(pk=oid, work_session=session, status=Order.Status.OPEN)
            .first()
        )
        if order:
            lines = list(order.lines.select_related("product").all())
            order_totals = compute_order_totals(order)
            tab_paid = sum_tab_payments(order)
            tab_balance = (order_totals["grand"] - tab_paid).quantize(Decimal("0.01"))
            if tab_balance < 0:
                tab_balance = Decimal("0")
    return render(request, "pos/_cart_fragment.html", {
        "current_order": order,
        "lines": lines,
        "order_totals": order_totals,
        "tab_paid": tab_paid,
        "tab_balance": tab_balance,
    })


def _ajax_or_redirect(request, redirect_url="pos:main"):
    """For AJAX requests, return JSON success. For regular, redirect."""
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"ok": True})
    return redirect(redirect_url)


def _ajax_or_redirect_error(request, msg, redirect_url="pos:main"):
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"ok": False, "error": msg}, status=400)
    messages.error(request, msg)
    return redirect(redirect_url)


@login_required
@require_GET
def receipt_print(request, invoice_id):
    from apps.billing.models import SaleInvoice

    inv = get_object_or_404(SaleInvoice, pk=invoice_id)
    lang = request.LANGUAGE_CODE or "ar"
    cafe = settings.CAFE_NAME_AR if lang == "ar" else getattr(settings, "CAFE_NAME_EN", settings.CAFE_NAME_AR)
    return render(request, "pos/receipt_preview.html", {"invoice": inv, "cafe_name": cafe})


@login_required
@require_GET
def receipt_raw(request, invoice_id):
    from apps.billing.models import SaleInvoice

    inv = get_object_or_404(SaleInvoice, pk=invoice_id)
    cafe = settings.CAFE_NAME_AR
    data = build_invoice_receipt(inv, cafe)
    return HttpResponse(data, content_type="application/octet-stream")
