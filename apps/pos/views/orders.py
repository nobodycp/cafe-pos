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
from apps.core.services import SessionService
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
    _ajax_or_redirect,
    _ajax_or_redirect_error,
    _get_order_for_session,
    _money,
    _parse_pos_discount_input,
    _post_redirect_after_cancel,
    _receipt_stamp_lines,
)

POS_CUSTOMER_FORM_PREFIX = "poscc"

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
def kitchen_ticket(request, order_id, batch_no):
    if not SessionService.pos_is_ready():
        messages.error(request, "الكاشير غير جاهز.")
        return redirect("pos:main")
    order = get_object_or_404(
        Order.objects.select_related("table", "work_session"),
        pk=order_id,
        **SessionService.pos_session_filter_kwargs(),
    )
    full = (request.GET.get("full") or "").strip() == "1"
    autoprint = (request.GET.get("autoprint") or "1").strip() != "0"
    if full:
        lines = list(order.lines.select_related("product").order_by("pk"))
    else:
        lines = list(
            order.lines.filter(kitchen_batch_no=int(batch_no)).select_related("product").order_by("pk")
        )
    return render(
        request,
        "pos/kitchen_ticket.html",
        {
            "order": order,
            "batch_no": batch_no,
            "lines": lines,
            "full_order": full,
            "autoprint": autoprint,
        },
    )
def kitchen_receipt_embed(request, order_id):
    """إيصال حراري للمطبخ بنفس قالب الدفع والطباعة (iframe داخل الكاشير)، بدون قسم طرق الدفع."""
    if not SessionService.pos_is_ready():
        return HttpResponse(
            '<!DOCTYPE html><html lang="ar" dir="rtl"><meta charset="utf-8">'
            "<body style=\"font:12px Tahoma;padding:12px;text-align:center\">الكاشير غير جاهز.</body></html>",
            status=403,
            content_type="text/html; charset=utf-8",
        )
    order = get_object_or_404(
        Order.objects.select_related(
            "table",
            "customer",
            "work_session",
            "table_session",
            "table_session__dining_table",
        ).prefetch_related(
            Prefetch("lines", queryset=OrderLine.objects.select_related("product").order_by("pk")),
        ),
        pk=order_id,
        **SessionService.pos_session_filter_kwargs(),
        status=Order.Status.OPEN,
    )
    full = (request.GET.get("full") or "").strip() == "1"
    batch_no = order.kitchen_batch_no
    ps = get_pos_settings()
    from apps.core.receipt_labels import merged_receipt_label_dict

    _L = merged_receipt_label_dict(ps)
    if full:
        lines = list(order.lines.all())
        batch_label = _L["kitchen_batch_full"]
    else:
        lines = list(
            order.lines.filter(kitchen_batch_no=int(batch_no)).select_related("product").order_by("pk")
        )
        batch_label = f'{_L["kitchen_batch_prefix"]} {batch_no}'
    if not lines:
        return HttpResponse(
            '<!DOCTYPE html><html lang="ar" dir="rtl"><meta charset="utf-8">'
            "<body style=\"font:12px Tahoma;padding:12px;text-align:center\">لا أصناف للطباعة.</body></html>",
            content_type="text/html; charset=utf-8",
        )
    totals = compute_order_totals(order)
    kitchen_totals = {
        "subtotal": totals["gross"],
        "discount": totals["discount"],
        "service": totals["service"],
        "tax": totals["tax"],
        "grand": totals["grand"],
    }
    kitchen_line_rows = []
    for ln in lines:
        unit = (ln.unit_price + ln.extra_unit_price).quantize(Decimal("0.01"))
        kitchen_line_rows.append(
            {
                "name_ar": ln.product.name_ar,
                "quantity": ln.quantity,
                "unit_price": unit,
                "line_subtotal": ln.line_total,
            }
        )
    lang = request.LANGUAGE_CODE or "ar"
    cafe = settings.CAFE_NAME_AR if lang == "ar" else getattr(settings, "CAFE_NAME_EN", settings.CAFE_NAME_AR)
    slogan = (ps.receipt_slogan_ar or "").strip()
    ctx = {
        "kitchen_receipt": True,
        "order": order,
        "kitchen_batch_label": batch_label,
        "kitchen_line_rows": kitchen_line_rows,
        "kitchen_totals": kitchen_totals,
        "kitchen_print_at": django_timezone.localtime(django_timezone.now()),
        "payments": [],
        "cafe_name": cafe,
        "receipt_slogan_line": slogan,
        "receipt_stamp_lines": _receipt_stamp_lines(),
    }
    return render(request, "pos/receipt_embed.html", ctx)
def order_resume(request, order_id):
    if not SessionService.pos_is_ready():
        messages.error(request, "الكاشير غير جاهز.")
        return redirect("pos:main")
    order = get_object_or_404(
        Order,
        pk=order_id,
        **SessionService.pos_session_filter_kwargs(),
        status=Order.Status.OPEN,
    )
    order.is_held = False
    order.save(update_fields=["is_held"])
    request.session["active_pos_order_id"] = order.id
    return redirect("pos:main")
def order_new(request):
    try:
        SessionService.require_open_session()
    except ValueError:
        messages.error(request, "افتح وردية عمل قبل إنشاء طلب (نمط الورديات).")
        return redirect("pos:main")
    otype = request.POST.get("order_type", Order.OrderType.DINE_IN)
    tid = request.POST.get("table_id")
    if otype == Order.OrderType.DINE_IN:
        if not tid:
            messages.error(request, "اختر طاولة لطلب الصالة من شبكة الطاولات في الكاشير.")
            return redirect("pos:main")
        table = get_object_or_404(DiningTable, pk=tid, is_active=True, is_cancelled=False)
        _ts, order = open_or_resume_table_session(user=request.user, dining_table=table)
        request.session["active_pos_order_id"] = order.id
        return redirect("pos:main")
    order = create_order(user=request.user, order_type=otype, table=None, customer=None)
    request.session["active_pos_order_id"] = order.id
    return redirect("pos:main")
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
def order_remove_line(request, order_id, line_id):
    order = _get_order_for_session(order_id, status=Order.Status.OPEN, is_held=False)
    try:
        delete_order_line(order=order, line_id=int(line_id), user=request.user)
    except ValueError as e:
        request.session["active_pos_order_id"] = order.id
        return _ajax_or_redirect_error(request, str(e))
    request.session["active_pos_order_id"] = order.id
    return _ajax_or_redirect(request)
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
def order_note(request, order_id):
    order = _get_order_for_session(order_id, status=Order.Status.OPEN)
    order.order_note = request.POST.get("order_note", "")[:2000]
    order.save(update_fields=["order_note", "updated_at"])
    return _ajax_or_redirect(request)
def order_discount(request, order_id):
    order = _get_order_for_session(order_id, status=Order.Status.OPEN)
    if "discount_value" in request.POST:
        order.discount_amount, order.discount_percent = _parse_pos_discount_input(
            request.POST.get("discount_value", "")
        )
    else:
        try:
            order.discount_amount = _money(request.POST.get("discount_amount", "0"))
            order.discount_percent = _money(request.POST.get("discount_percent", "0"))
        except (InvalidOperation, ValueError):
            pass
    order.save(update_fields=["discount_amount", "discount_percent", "updated_at"])
    return _ajax_or_redirect(request)
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
            return _post_redirect_after_cancel(request)
        request.session.pop("active_pos_order_id", None)
        if is_xhr:
            return JsonResponse(
                {"ok": True, "message": "تم إلغاء تعديل الفاتورة وإعادة حالتها السابقة."}
            )
        messages.success(request, "تم إلغاء تعديل الفاتورة وإعادة حالتها السابقة.")
        return _post_redirect_after_cancel(request)
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
            retire_ephemeral_dining_table_if_safe(dining_table_id=ts.dining_table_id)
    log_audit(request.user, "pos.order.cancel", "pos.Order", order.pk, {})
    request.session.pop("active_pos_order_id", None)
    ok_msg = f"تم إلغاء الطلب #{order.pk}"
    if is_xhr:
        return JsonResponse({"ok": True, "message": ok_msg})
    messages.success(request, ok_msg)
    return _post_redirect_after_cancel(request)
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
    return redirect("pos:main")
