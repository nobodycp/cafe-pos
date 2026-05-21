import json
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.urls import reverse
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import models, transaction
from django.db.models import Count, DecimalField, OuterRef, Prefetch, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone as django_timezone
from django.views.decorators.http import require_GET, require_POST

from apps.billing.receipt_escpos import build_invoice_receipt
from apps.billing.models import OrderPayment, SaleInvoice, SaleInvoiceLine
from apps.billing.tab_service import (
    apply_tab_payments_and_maybe_finalize,
    cart_line_rows_for_template,
    compute_order_totals,
    finalize_order_invoice,
    sum_tab_payments,
)
from apps.catalog.forms import PRODUCT_QUICK_FORM_PREFIX, ProductForm
from apps.catalog.models import Category, Product, ProductModifierGroup
from apps.contacts.customer_lookup import active_customers_search_qs, customer_search_result_row
from apps.contacts.forms import CustomerForm
from apps.contacts.services import resolve_or_create_active_customer_by_name
from apps.contacts.models import Customer
from apps.inventory.models import StockBalance
from apps.core.forms import TreasuryVoucherForm
from apps.core.models import get_pos_settings, log_audit
from apps.core.treasury_services import recent_treasury_voucher_logs
from apps.core.payment_methods import (
    credit_method_codes,
    get_payment_method_codes,
    method_codes_requiring_payer_details,
)
from apps.core.operation_mode import uses_shifts
from apps.core.services import SessionService
from apps.reports.payment_boxes import pos_cashier_balance_snapshot
from apps.purchasing.models import Supplier
from apps.purchasing.request_parsers import payment_rows as _payment_rows, purchase_form_state as _purchase_form_state
from apps.pos.models import DiningTable, Order, OrderLine, TableSession
from apps.pos.services import (
    add_or_update_line,
    adjust_line_quantity,
    create_order,
    delete_order_line,
    hold_order,
    open_orders_with_lines_queryset,
    set_line_note,
    set_line_quantity,
    set_line_unit_price,
)
from apps.pos.table_service import (
    floor_rows_for_session,
    open_or_resume_table_session,
    retire_ephemeral_dining_table_if_safe,
)

from apps.pos.views._helpers import (
    _annotate_pos_product_stock,
    _receipt_stamp_lines,
)

POS_CUSTOMER_FORM_PREFIX = "poscc"

def pos_main(request):
    err = request.session.pop("flash_error", None)
    ok = request.session.pop("flash_ok", None)
    if err:
        messages.error(request, err)
    if ok:
        messages.success(request, ok)

    products_qs = _annotate_pos_product_stock(
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
            _annotate_pos_product_stock(
                Product.objects.filter(pk__in=top_product_ids, is_active=True)
                .exclude(product_type=Product.ProductType.RAW)
                .select_related("category", "unit")
                .annotate(modifier_group_count=Count("modifier_groups"))
            ).prefetch_related(mod_pref)
        )
        id_order = {pid: i for i, pid in enumerate(top_product_ids)}
        featured_rows.sort(key=lambda p: id_order.get(p.pk, 999))
    else:
        featured_rows = list(
            _annotate_pos_product_stock(
                Product.objects.filter(is_active=True)
                .exclude(product_type=Product.ProductType.RAW)
                .select_related("category", "unit")
                .annotate(modifier_group_count=Count("modifier_groups"))
            )
            .prefetch_related(mod_pref)
            .order_by("name_ar")[:12]
        )

    order = None
    order_totals = None
    tab_paid = Decimal("0")
    tab_balance = Decimal("0")
    oid = request.session.get("active_pos_order_id")
    session = SessionService.get_open_session()
    pos_ready = SessionService.pos_is_ready()
    lines = []
    if oid and pos_ready:
        order = (
            Order.objects.filter(
                pk=oid,
                **SessionService.pos_session_filter_kwargs(),
                status=Order.Status.OPEN,
                is_held=False,
            )
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

    floor_rows = floor_rows_for_session(session) if pos_ready else []

    desk_balance_rows: list = []
    desk_balance_period = ""
    if pos_ready:
        snap = pos_cashier_balance_snapshot(work_session=session)
        desk_balance_rows = snap["rows"]
        desk_balance_period = snap["period_label"]

    open_orders = []
    if pos_ready:
        open_orders = list(
            open_orders_with_lines_queryset(session)
            .select_related("customer", "table")
            .prefetch_related("lines", "tab_payments")
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

    treasury_voucher_form = None
    treasury_next = ""
    recent_treasury_rows: list = []
    pi_errors: list = []
    pi_suppliers = list(Supplier.objects.filter(is_active=True).order_by("name_ar"))
    pi_payment_rows = _payment_rows()
    pi_form_state = _purchase_form_state(request)
    pi_next = reverse("pos:main")
    pi_form_action = reverse("shell:purchase_new")
    pos_product_quick_form = None
    pos_product_overlay_open = False
    pos_product_name_input_id = ""
    pos_customer_create_form = None
    pos_customer_overlay_open = False
    pos_customer_first_field_id = ""
    if pos_ready:
        treasury_voucher_form = TreasuryVoucherForm(prefix="tv")
        treasury_next = reverse("pos:main")
        recent_treasury_rows = list(recent_treasury_voucher_logs(limit=8))
        if request.user.has_perm("catalog.add_product"):
            retry = request.session.pop("pos_product_quick_retry", None)
            if retry:
                pos_product_quick_form = ProductForm(retry["data"], prefix=PRODUCT_QUICK_FORM_PREFIX)
                pos_product_overlay_open = True
            else:
                pos_product_quick_form = ProductForm(prefix=PRODUCT_QUICK_FORM_PREFIX)
            if pos_product_quick_form:
                pos_product_name_input_id = pos_product_quick_form["name_ar"].id_for_label

        if request.user.has_perm("contacts.add_customer"):
            retry_cust = request.session.pop("pos_customer_create_retry", None)
            if retry_cust:
                pos_customer_create_form = CustomerForm(retry_cust["data"], prefix=POS_CUSTOMER_FORM_PREFIX)
                pos_customer_overlay_open = True
            else:
                pos_customer_create_form = CustomerForm(prefix=POS_CUSTOMER_FORM_PREFIX)
            pos_customer_first_field_id = pos_customer_create_form["name_ar"].id_for_label

    return render(
        request,
        "pos/main.html",
        {
            "work_session": session,
            "uses_shifts": uses_shifts(),
            "pos_ready": pos_ready,
            "product_sections": sections,
            "featured_rows": featured_rows,
            "floor_rows": floor_rows,
            "desk_balance_rows": desk_balance_rows,
            "desk_balance_period": desk_balance_period,
            "current_order": order,
            "lines": lines,
            "order_totals": order_totals,
            "tab_paid": tab_paid,
            "tab_balance": tab_balance,
            "cart_line_rows": cart_line_rows,
            "open_orders": open_orders,

            "ui_lang": lang,
            "cafe_name": settings.CAFE_NAME_AR if lang == "ar" else getattr(settings, "CAFE_NAME_EN", settings.CAFE_NAME_AR),
            "treasury_voucher_form": treasury_voucher_form,
            "treasury_next": treasury_next,
            "recent_treasury_rows": recent_treasury_rows,
            "pi_errors": pi_errors,
            "pi_suppliers": pi_suppliers,
            "pi_payment_rows": pi_payment_rows,
            "pi_form_state": pi_form_state,
            "pi_next": pi_next,
            "pi_form_action": pi_form_action,
            "pos_product_quick_form": pos_product_quick_form,
            "pos_product_overlay_open": pos_product_overlay_open,
            "pos_product_name_input_id": pos_product_name_input_id,
            "pos_customer_create_form": pos_customer_create_form,
            "pos_customer_overlay_open": pos_customer_overlay_open,
            "pos_customer_first_field_id": pos_customer_first_field_id,
            "range10": range(10),
            "range20": range(20),
        },
    )
def pos_customer_create_save(request):
    if not request.user.has_perm("contacts.add_customer"):
        messages.error(request, "ليست لديك صلاحية إضافة عميل.")
        return redirect("pos:main")
    if not SessionService.pos_is_ready():
        messages.error(request, "الكاشير غير جاهز.")
        return redirect("pos:main")
    form = CustomerForm(request.POST, prefix=POS_CUSTOMER_FORM_PREFIX)
    if form.is_valid():
        from apps.contacts.services import replace_customer_opening_ledger
        from apps.core.decimalutil import as_decimal

        customer = form.save()
        opening_dec = as_decimal(form.cleaned_data.get("opening_balance") or 0).quantize(Decimal("0.01"))
        replace_customer_opening_ledger(customer=customer, opening=opening_dec)
        messages.success(request, f"تم إضافة العميل «{customer.name_ar}» بنجاح.")
        log_audit(request.user, "contacts.customer.create_from_pos", "contacts.Customer", customer.pk, {})
        return redirect("pos:main")
    request.session["pos_customer_create_retry"] = {"data": request.POST.dict()}
    messages.error(request, "تعذّر حفظ العميل — راجع الحقول في النموذج.")
    return redirect("pos:main")
def pos_product_quick_save(request):
    if not request.user.has_perm("catalog.add_product"):
        messages.error(request, "ليست لديك صلاحية إضافة منتج.")
        return redirect("pos:main")
    if not SessionService.pos_is_ready():
        messages.error(request, "الكاشير غير جاهز.")
        return redirect("pos:main")
    form = ProductForm(request.POST, prefix=PRODUCT_QUICK_FORM_PREFIX)
    if form.is_valid():
        form.save()
        messages.success(request, f"تم إضافة المنتج «{form.instance.name_ar}».")
        return redirect("pos:main")
    retry_data = {k: request.POST.get(k) for k in request.POST if k.startswith(f"{PRODUCT_QUICK_FORM_PREFIX}-")}
    request.session["pos_product_quick_retry"] = {"data": retry_data}
    messages.error(request, "تعذّر حفظ المنتج — راجع الحقول في النافذة.")
    return redirect("pos:main")
def last_sale_invoice_panel(request):
    """يرجع HTML جزئي لآخر فاتورة بيع (لعرضها داخل طبقة على شاشة الكاشير)."""
    if not SessionService.pos_is_ready():
        return HttpResponse(
            '<div class="p-6 text-center text-sm text-muted" dir="rtl">الكاشير غير جاهز.</div>',
            content_type="text/html; charset=utf-8",
        )
    inv = (
        SaleInvoice.objects.filter(is_cancelled=False, **SessionService.pos_session_filter_kwargs())
        .order_by("-created_at", "-pk")
        .first()
    )
    if inv is None:
        return HttpResponse(
            '<div class="p-6 text-center text-sm text-muted" dir="rtl">لا توجد فاتورة بيع مسجّلة بعد.</div>',
            content_type="text/html; charset=utf-8",
        )
    from apps.billing.views import _sale_invoice_detail_context, _sale_invoice_detail_queryset

    invoice = get_object_or_404(_sale_invoice_detail_queryset(), pk=inv.pk)
    return render(
        request,
        "shell/_sale_invoice_detail_modal_fragment.html",
        _sale_invoice_detail_context(request, invoice),
    )
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
def last_invoice_resume_into_cart(request):
    """تعليق السلة الحالية إن لزم، ثم تحميل آخر فاتورة الوردية في السلة للتعديل وإعادة التسوية لاحقاً."""
    session = SessionService.get_open_session()
    if not SessionService.pos_is_ready():
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"ok": False, "error": "الكاشير غير جاهز."}, status=400)
        messages.error(request, "الكاشير غير جاهز.")
        return redirect("pos:main")

    inv = (
        SaleInvoice.objects.filter(is_cancelled=False, **SessionService.pos_session_filter_kwargs())
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
            pk=inv.order_id,
            **SessionService.pos_session_filter_kwargs(),
            status=Order.Status.OPEN,
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
def cart_fragment(request):
    """Return just the cart HTML fragment for AJAX updates."""
    oid = request.session.get("active_pos_order_id")
    order = None
    order_totals = None
    tab_paid = Decimal("0")
    tab_balance = Decimal("0")
    lines = []
    if oid and SessionService.pos_is_ready():
        order = (
            Order.objects.filter(
                pk=oid,
                **SessionService.pos_session_filter_kwargs(),
                status=Order.Status.OPEN,
            )
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
def floor_tables_fragment(request):
    """HTML جزئي لقائمة الطاولات — تحديث بدون إعادة تحميل الصفحة بعد الدفع وإغلاق الجلسة."""
    session = SessionService.get_open_session()
    floor_rows = floor_rows_for_session(session) if SessionService.pos_is_ready() else []
    return render(request, "pos/_floor_tables_scroll_body.html", {"floor_rows": floor_rows})
def receipt_print(request, invoice_id):
    inv = get_object_or_404(
        SaleInvoice.objects.select_related(
            "customer",
            "supplier_buyer",
            "order",
            "order__table",
            "order__table_session__dining_table",
            "work_session",
            "work_session__opened_by",
        ).prefetch_related(
            Prefetch(
                "lines",
                queryset=SaleInvoiceLine.objects.select_related("product").order_by("pk"),
            ),
            "payments",
        ),
        pk=invoice_id,
    )
    lang = request.LANGUAGE_CODE or "ar"
    cafe = settings.CAFE_NAME_AR if lang == "ar" else getattr(settings, "CAFE_NAME_EN", settings.CAFE_NAME_AR)
    ps = get_pos_settings()
    slogan = (ps.receipt_slogan_ar or "").strip()
    ctx = {
        "invoice": inv,
        "lines": inv.lines.all(),
        "payments": list(inv.payments.order_by("pk")),
        "cafe_name": cafe,
        "receipt_slogan_line": slogan,
        "receipt_stamp_lines": _receipt_stamp_lines(),
    }
    if request.GET.get("embed") == "1":
        return render(request, "pos/receipt_embed.html", ctx)
    return render(request, "pos/receipt_preview.html", ctx)
def receipt_live_preview(request):
    """صفحة معاينة الإيصال (آخر فاتورة) لمراجعة التعديلات بعد حفظ الإعدادات."""
    from apps.billing.models import SaleInvoice

    inv = SaleInvoice.objects.order_by("-pk").first()
    ctx = {"iframe_src": None}
    if inv:
        ctx["iframe_src"] = reverse("pos:receipt", kwargs={"invoice_id": inv.pk}) + "?embed=1"
    return render(request, "pos/receipt_live_preview.html", ctx)
def receipt_raw(request, invoice_id):
    from apps.billing.models import SaleInvoice

    inv = get_object_or_404(
        SaleInvoice.objects.select_related("customer", "order", "order__table", "work_session__opened_by"),
        pk=invoice_id,
    )
    cafe = settings.CAFE_NAME_AR
    data = build_invoice_receipt(inv, cafe)
    return HttpResponse(data, content_type="application/octet-stream")
