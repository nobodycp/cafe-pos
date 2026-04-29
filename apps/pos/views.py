from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.urls import reverse
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import models, transaction
from django.db.models import Count, Prefetch, Q, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.billing.receipt_escpos import build_invoice_receipt
from apps.billing.models import OrderPayment, SaleInvoice
from apps.billing.tab_service import (
    apply_tab_payments_and_maybe_finalize,
    cart_line_rows_for_template,
    compute_order_totals,
    finalize_order_invoice,
    sum_tab_payments,
)
from apps.catalog.models import Category, Product, ProductModifierGroup
from apps.contacts.customer_lookup import active_customers_search_qs
from apps.contacts.models import Customer
from apps.core.forms import TreasuryVoucherForm
from apps.core.treasury_services import recent_treasury_voucher_logs
from apps.core.models import get_pos_settings, log_audit
from apps.core.payment_methods import (
    credit_method_codes,
    get_payment_method_codes,
    method_codes_requiring_payer_details,
)
from apps.core.services import SessionService
from apps.pos.models import DiningTable, Order, OrderLine, TableSession
from apps.pos.services import (
    add_or_update_line,
    adjust_line_quantity,
    create_order,
    delete_order_line,
    hold_order,
    set_line_note,
    set_line_quantity,
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
    """
    دفعة واحدة لكل إرسال (بدون «مختلط»): يمكن دفع جزئي عبر pay_amount.
    يُرجع قائمة عناصر (method, amount, payer_name, payer_phone).
    """
    mode = request.POST.get("payment_mode", "").strip()
    if remaining <= 0:
        return []
    codes = get_payment_method_codes()
    if mode not in codes:
        return []
    payer_name = (request.POST.get("payer_name") or "").strip()[:120]
    payer_phone = (request.POST.get("payer_phone") or "").strip()[:40]
    raw_amt = (request.POST.get("pay_amount") or "").strip()
    try:
        pay_amt = _money(raw_amt) if raw_amt else remaining
    except (InvalidOperation, ValueError):
        pay_amt = remaining
    if pay_amt <= 0:
        return []
    if pay_amt > remaining + Decimal("0.02"):
        pay_amt = remaining
    return [(mode, pay_amt, payer_name, payer_phone)]


@login_required
def redirect_pos_settings_to_app(request, tail: str = ""):
    """روابط قديمة /pos/settings/… → /app/settings/… (تحت include الجذر path(\"app/\", …))."""
    path = "/app/settings/" + (tail.lstrip("/") if tail else "")
    if request.GET:
        path += "?" + request.GET.urlencode()
    return redirect(path)


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

    cart_line_rows = cart_line_rows_for_template(lines, order_totals) if order and order_totals else []

    floor_rows = floor_rows_for_session(session) if session else []

    open_orders = []
    if session:
        open_orders = list(
            Order.objects.filter(
                work_session=session,
                status=Order.Status.OPEN,
                order_type__in=[Order.OrderType.DELIVERY, Order.OrderType.TAKEAWAY],
            )
            .select_related("customer")
            .prefetch_related("lines", "tab_payments")
            .annotate(
                line_count=Count("lines"),
                total_qty=Sum("lines__quantity"),
            )
            .order_by("-created_at")
        )
        for o in open_orders:
            tot = compute_order_totals(o)
            paid = sum_tab_payments(o)
            grand = tot["grand"]
            o.calc_grand = grand
            o.calc_paid = paid
            o.calc_balance = max(grand - paid, Decimal("0"))

    lang = request.LANGUAGE_CODE or "ar"

    home_tab = (request.GET.get("home") or "").strip()
    if home_tab not in ("vouchers", "new"):
        home_tab = "new"
    treasury_voucher_form = None
    treasury_next = ""
    recent_treasury_rows = []
    if session and not order:
        treasury_voucher_form = TreasuryVoucherForm(prefix="tv")
        treasury_next = reverse("pos:main") + "?home=vouchers"
        recent_treasury_rows = list(recent_treasury_voucher_logs(limit=10))

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
            "cart_line_rows": cart_line_rows,
            "open_orders": open_orders,

            "ui_lang": lang,
            "cafe_name": settings.CAFE_NAME_AR if lang == "ar" else getattr(settings, "CAFE_NAME_EN", settings.CAFE_NAME_AR),
            "pos_home_initial": home_tab,
            "treasury_voucher_form": treasury_voucher_form,
            "treasury_next": treasury_next,
            "recent_treasury_rows": recent_treasury_rows,
        },
    )


@login_required
@require_GET
def last_sale_invoice_panel(request):
    """يرجع HTML جزئي لآخر فاتورة بيع (لعرضها داخل طبقة على شاشة الكاشير)."""
    session = SessionService.get_open_session()
    if not session:
        return HttpResponse(
            '<div class="p-6 text-center text-sm text-muted" dir="rtl">افتح وردية لعرض الفاتورة.</div>',
            content_type="text/html; charset=utf-8",
        )
    inv = (
        SaleInvoice.objects.filter(is_cancelled=False, work_session=session)
        .order_by("-created_at", "-pk")
        .first()
    )
    if inv is None:
        return HttpResponse(
            '<div class="p-6 text-center text-sm text-muted" dir="rtl">لا توجد فاتورة بيع مسجّلة بعد.</div>',
            content_type="text/html; charset=utf-8",
        )
    invoice = get_object_or_404(
        SaleInvoice.objects.select_related(
            "customer", "supplier_buyer",
            "order__table_session__dining_table", "work_session",
        ),
        pk=inv.pk,
    )
    lines = invoice.lines.select_related("product").order_by("pk")
    payments = invoice.payments.all()
    return render(
        request,
        "shell/_invoice_detail_fragment.html",
        {
            "invoice": invoice,
            "lines": lines,
            "payments": payments,
            "invoice_embedded": True,
            "has_sale_returns": invoice.returns.exists(),
        },
    )


@login_required
@require_GET
def last_sale_invoice_edit_redirect(request):
    """يفتح شاشة تعديل آخر فاتورة بيع (من شريط الكاشير)."""
    from apps.billing.models import SaleInvoice

    inv = (
        SaleInvoice.objects.filter(is_cancelled=False)
        .order_by("-created_at", "-pk")
        .first()
    )
    if inv is None:
        messages.info(request, "لا توجد فاتورة بيع مسجّلة بعد.")
        return redirect("pos:main")
    return redirect("shell:sale_invoice_edit", pk=inv.pk)


@login_required
@require_POST
def last_invoice_resume_into_cart(request):
    """تعليق السلة الحالية إن لزم، ثم تحميل آخر فاتورة الوردية في السلة للتعديل وإعادة التسوية لاحقاً."""
    session = SessionService.get_open_session()
    if not session:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"ok": False, "error": "لا توجد وردية مفتوحة."}, status=400)
        messages.error(request, "لا توجد وردية مفتوحة.")
        return redirect("pos:main")

    inv = (
        SaleInvoice.objects.filter(is_cancelled=False, work_session=session)
        .order_by("-created_at", "-pk")
        .first()
    )
    if inv is None:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"ok": False, "error": "لا توجد فاتورة في هذه الوردية."}, status=400)
        messages.error(request, "لا توجد فاتورة في هذه الوردية.")
        return redirect("pos:main")

    inv_label = inv.invoice_number
    oid = request.session.get("active_pos_order_id")
    if oid and int(oid) == inv.order_id:
        same = Order.objects.filter(
            pk=inv.order_id, work_session=session, status=Order.Status.OPEN
        ).first()
        if same and not OrderPayment.objects.filter(sale_invoice=inv).exists():
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return JsonResponse({"ok": True, "invoice_number": inv_label, "already": True})
            messages.info(request, "آخر فاتورة محمّلة بالفعل في السلة.")
            return redirect("pos:main")

    try:
        with transaction.atomic():
            from apps.billing.invoice_resume_service import (
                hold_current_pos_order_if_needed,
                resume_last_sale_invoice_into_cart,
            )

            hold_current_pos_order_if_needed(
                user=request.user,
                session=session,
                current_order_id=int(oid) if oid else None,
                target_order_id=inv.order_id,
            )
            if oid and int(oid) != inv.order_id:
                request.session.pop("active_pos_order_id", None)
            order = resume_last_sale_invoice_into_cart(user=request.user)
    except ValueError as e:
        msg = str(e)
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"ok": False, "error": msg}, status=400)
        messages.error(request, msg)
        return redirect("pos:main")

    request.session["active_pos_order_id"] = order.pk
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"ok": True, "invoice_number": inv_label})
    messages.success(request, f"تم تحميل {inv_label} في السلة للتعديل.")
    return redirect("pos:main")


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
    data = [
        {"id": c.pk, "name_ar": c.name_ar, "phone": c.phone or ""}
        for c in active_customers_search_qs(q, limit=20)
    ]
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
    set_raw = (request.POST.get("set_quantity") or "").strip()
    try:
        if set_raw != "":
            nq = _money(set_raw)
            set_line_quantity(order=order, line_id=int(line_id), quantity=nq, user=request.user)
        else:
            dq = _money(request.POST.get("qty_delta", "0"))
            adjust_line_quantity(order=order, line_id=int(line_id), quantity_delta=dq, user=request.user)
    except (InvalidOperation, ValueError):
        request.session["active_pos_order_id"] = order.id
        return _ajax_or_redirect_error(request, "كمية غير صالحة")
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
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"ok": False, "error": str(e)}, status=400)
        messages.error(request, str(e))
        return redirect("pos:main")
    request.session["active_pos_order_id"] = order.id
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"ok": True})
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
    if SaleInvoice.objects.filter(order=order).exists():
        messages.error(request, "لا يمكن التقسيم أثناء تعديل فاتورة مسجّلة. أكمل التعديل أو ألغِ الطلب.")
        return redirect("pos:main")
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
    is_xhr = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    order = _get_order_for_session(order_id, status=Order.Status.OPEN)
    inv = SaleInvoice.objects.filter(order=order).first()
    if inv:
        from apps.billing.invoice_resume_service import abort_resume_invoice_order

        try:
            abort_resume_invoice_order(order=order, invoice=inv, user=request.user)
        except ValueError as e:
            if is_xhr:
                return JsonResponse({"ok": False, "error": str(e)}, status=400)
            messages.error(request, str(e))
            return redirect("pos:main")
        request.session.pop("active_pos_order_id", None)
        if is_xhr:
            return JsonResponse(
                {"ok": True, "message": "تم إلغاء تعديل الفاتورة وإعادة حالتها السابقة."}
            )
        messages.success(request, "تم إلغاء تعديل الفاتورة وإعادة حالتها السابقة.")
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
    ok_msg = f"تم إلغاء الطلب #{order.pk}"
    if is_xhr:
        return JsonResponse({"ok": True, "message": ok_msg})
    messages.success(request, ok_msg)
    return redirect("pos:main")


@login_required
@require_POST
def order_hold(request, order_id):
    order = _get_order_for_session(order_id, status=Order.Status.OPEN, is_held=False)
    if SaleInvoice.objects.filter(order=order).exists():
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse(
                {"ok": False, "error": "لا يمكن تعليق الطلب أثناء تعديل فاتورة — استخدم «إلغاء طلب» للتراجع عن التعديل أولاً."},
                status=400,
            )
        messages.error(request, "لا يمكن تعليق الطلب أثناء تعديل فاتورة — استخدم «إلغاء طلب» للتراجع أولاً.")
        return redirect("pos:main")
    try:
        hold_order(order=order, user=request.user)
    except ValueError as e:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"ok": False, "error": str(e)}, status=400)
        messages.error(request, str(e))
        return redirect("pos:main")
    request.session.pop("active_pos_order_id", None)
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"ok": True})
    messages.success(request, "تم تعليق الطلب — يمكن استئنافه من الطاولة.")
    return redirect("pos:tables_floor")



@login_required
@require_POST
def order_checkout(request, order_id):
    is_xhr = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    def _err(msg: str):
        if is_xhr:
            return JsonResponse({"ok": False, "error": msg}, status=400)
        messages.error(request, msg)
        return redirect("pos:main")

    def _ok_receipt(inv_pk: int, flash_msg: str):
        if is_xhr:
            return JsonResponse(
                {
                    "ok": True,
                    "redirect": reverse("pos:receipt", kwargs={"invoice_id": inv_pk}),
                    "message": flash_msg,
                }
            )
        messages.success(request, flash_msg)
        return redirect("pos:receipt", invoice_id=inv_pk)

    def _ok_tables_floor(flash_msg: str):
        if is_xhr:
            return JsonResponse(
                {"ok": True, "redirect": reverse("pos:tables_floor"), "message": flash_msg}
            )
        messages.success(request, flash_msg)
        return redirect("pos:tables_floor")

    def _ok_partial_pay(flash_msg: str):
        if is_xhr:
            return JsonResponse({"ok": True, "refresh_cart": True, "message": flash_msg})
        messages.success(request, flash_msg)
        return redirect("pos:main")

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
                msg = "طلب فارغ به دفعات مسجّلة — أزل الدفعات أو ألغِ الطلب من الطاولة."
            else:
                msg = code
            return _err(msg)
        request.session.pop("active_pos_order_id", None)
        if inv:
            flash_msg = f"تم إتمام البيع. رقم الفاتورة: {inv.invoice_number}"
            return _ok_receipt(inv.pk, flash_msg)
        flash_msg = "تم إغلاق الطاولة (طلب فارغ — دون فاتورة)."
        return _ok_tables_floor(flash_msg)

    payments = _payments_from_checkout_form(request, remaining)
    new_sum = sum((p[1] for p in payments), Decimal("0")).quantize(Decimal("0.01"))  # type: ignore[index]

    if remaining > 0 and new_sum <= 0:
        raw_mode = (request.POST.get("payment_mode") or "").strip()
        codes = get_payment_method_codes()
        if not raw_mode or raw_mode not in codes:
            msg = "اختر طريقة الدفع أولاً." if not raw_mode else "طريقة الدفع غير صالحة."
        else:
            msg = "أدخل مبلغ دفع أكبر من صفر."
        return _err(msg)
    if new_sum > remaining + Decimal("0.02"):
        return _err("مجموع الدفعات يتجاوز المتبقي على الطلب.")

    ar_codes = credit_method_codes()
    for item in payments:
        _m = item[0]
        amt = item[1]
        if _m in ar_codes and amt > 0 and not customer:
            return _err("اختر عميلاً لجزء الائتمان.")
        if _m in method_codes_requiring_payer_details() and amt > 0:
            pn = item[2] if len(item) > 2 else ""
            ph = item[3] if len(item) > 3 else ""
            if len(str(pn).strip()) < 2 or len(str(ph).strip()) < 8:
                return _err(
                    "أدخل اسم المحوّل ورقم الجوال (للتتبع) مع بنك فلسطين / بال باي / جوال باي."
                )

    try:
        inv = apply_tab_payments_and_maybe_finalize(
            order=order, user=request.user, payments=payments, customer=customer
        )
    except ValueError as e:
        code = str(e)
        if code.startswith("INSUFFICIENT_STOCK"):
            msg = "المخزون غير كافٍ لهذا البند."
        elif code == "PAYMENT_SUM_MISMATCH":
            msg = "خطأ في تطابق المبالغ."
        elif code == "CREDIT_REQUIRES_CUSTOMER":
            msg = "الائتمان يتطلب عميلاً."
        elif code == "TAB_PAYMENT_ON_EMPTY_ORDER":
            msg = "طلب فارغ به دفعات مسجّلة — أزل الدفعات أو ألغِ الطلب من الطاولة."
        else:
            msg = code
        return _err(msg)

    if inv:
        request.session.pop("active_pos_order_id", None)
        flash_msg = f"تم إتمام البيع. رقم الفاتورة: {inv.invoice_number}"
        return _ok_receipt(inv.pk, flash_msg)
    order.refresh_from_db()
    if order.status == Order.Status.CHECKED_OUT:
        request.session.pop("active_pos_order_id", None)
        flash_msg = "تم إغلاق الطاولة (طلب فارغ — دون فاتورة)."
        return _ok_tables_floor(flash_msg)
    flash_msg = (
        f"تم تسجيل دفعة. المتبقي: {(remaining - new_sum).quantize(Decimal('0.01'))} ر.س"
    )
    return _ok_partial_pay(flash_msg)


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
    cart_line_rows = cart_line_rows_for_template(lines, order_totals) if order and order_totals else []
    return render(request, "pos/_cart_fragment.html", {
        "current_order": order,
        "lines": lines,
        "cart_line_rows": cart_line_rows,
        "order_totals": order_totals,
        "tab_paid": tab_paid,
        "tab_balance": tab_balance,
    })


@login_required
@require_GET
def payer_hints_search(request):
    """اقتراحات اسم/جوال المحوّل من دفعات سابقة (تبويب لاختيار أول نتيجة)."""
    from django.db.models import Q

    from apps.billing.models import InvoicePayment, OrderPayment, SaleInvoice

    q = (request.GET.get("q") or "").strip()
    if len(q) < 1:
        return JsonResponse({"results": []})
    seen = set()
    results = []
    inv_q = (
        InvoicePayment.objects.filter(Q(payer_name__icontains=q) | Q(payer_phone__icontains=q))
        .exclude(payer_name="", payer_phone="")
        .order_by("-id")[:40]
    )
    op_q = (
        OrderPayment.objects.filter(Q(payer_name__icontains=q) | Q(payer_phone__icontains=q))
        .exclude(payer_name="", payer_phone="")
        .order_by("-id")[:40]
    )
    for p in list(inv_q) + list(op_q):
        name = (p.payer_name or "").strip()
        phone = (p.payer_phone or "").strip()
        if not name and not phone:
            continue
        key = (name, phone)
        if key in seen:
            continue
        seen.add(key)
        results.append({"name_ar": name, "phone": phone})
        if len(results) >= 15:
            break
    return JsonResponse({"results": results})


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

    from apps.core.payment_methods import load_payment_method_rows

    inv = get_object_or_404(SaleInvoice, pk=invoice_id)
    lang = request.LANGUAGE_CODE or "ar"
    cafe = settings.CAFE_NAME_AR if lang == "ar" else getattr(settings, "CAFE_NAME_EN", settings.CAFE_NAME_AR)
    pm_label_map = {r["code"]: r["label_ar"] for r in load_payment_method_rows()}
    return render(
        request,
        "pos/receipt_preview.html",
        {"invoice": inv, "cafe_name": cafe, "pm_label_map": pm_label_map},
    )


@login_required
@require_GET
def receipt_raw(request, invoice_id):
    from apps.billing.models import SaleInvoice

    inv = get_object_or_404(SaleInvoice, pk=invoice_id)
    cafe = settings.CAFE_NAME_AR
    data = build_invoice_receipt(inv, cafe)
    return HttpResponse(data, content_type="application/octet-stream")
