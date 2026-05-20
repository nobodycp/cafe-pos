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
from apps.billing.sale_invoice_edit import parse_order_date_from_post
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
    _get_order_for_session,
    _money,
)

POS_CUSTOMER_FORM_PREFIX = "poscc"


def _checkout_transaction_at(request, order: Order):
    """تاريخ المعاملة من POST؛ ``None`` إذا لم يُرسل. يرفع ValueError عند صيغة غير صالحة."""
    try:
        at = parse_order_date_from_post(request.POST, fallback=order.created_at)
    except ValueError:
        raise ValueError("INVALID_ORDER_DATE")
    if at is None:
        return None
    if django_timezone.is_naive(at):
        at = django_timezone.make_aware(at, django_timezone.get_current_timezone())
    if django_timezone.localtime(at).date() > django_timezone.localdate():
        raise ValueError("ORDER_DATE_FUTURE")
    return at


def _payments_from_checkout_form(request, remaining: Decimal) -> list:
    """
    دفعة واحدة (payment_mode + pay_amount) أو دفع مختلط (use_payment_splits + payment_splits_json).
    يُرجع قائمة عناصر (method, amount, payer_name, payer_phone).
    """
    if remaining <= 0:
        return []
    codes = set(get_payment_method_codes())
    payer_name = (request.POST.get("payer_name") or "").strip()[:120]
    payer_phone = (request.POST.get("payer_phone") or "").strip()[:40]
    use_splits_raw = (request.POST.get("use_payment_splits") or "").strip().lower()
    use_splits = use_splits_raw in ("1", "true", "on", "yes")
    raw_json = (request.POST.get("payment_splits_json") or "").strip()

    if use_splits and raw_json:
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError:
            return []
        if not isinstance(data, list) or len(data) > 24:
            return []
        out: list = []
        for item in data:
            if isinstance(item, dict):
                method = str(item.get("method") or "").strip().lower()
                amt_raw = item.get("amount")
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                method = str(item[0] or "").strip().lower()
                amt_raw = item[1]
            else:
                continue
            if method not in codes:
                continue
            try:
                a = _money(str(amt_raw).replace(",", "."))
            except (InvalidOperation, ValueError, TypeError):
                continue
            if a <= 0:
                continue
            out.append((method, a, payer_name, payer_phone))
        return out

    mode = request.POST.get("payment_mode", "").strip()
    if mode not in codes:
        return []
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
def order_checkout(request, order_id):
    is_xhr = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    def _err(msg: str):
        if is_xhr:
            return JsonResponse({"ok": False, "error": msg}, status=400)
        messages.error(request, msg)
        return redirect("pos:main")

    def _ok_receipt(inv_pk: int, flash_msg: str):
        action = (request.POST.get("checkout_action") or "save").strip()
        if is_xhr:
            if action == "save_print":
                rel = reverse("pos:receipt", kwargs={"invoice_id": inv_pk}) + "?embed=1"
                return JsonResponse(
                    {
                        "ok": True,
                        "message": flash_msg,
                        "refresh_cart": True,
                        "receipt_embed_url": rel,
                    }
                )
            return JsonResponse({"ok": True, "message": flash_msg, "refresh_cart": True})
        messages.success(request, flash_msg)
        if action == "save_print":
            return redirect("pos:receipt", invoice_id=inv_pk)
        return redirect("pos:main")

    def _ok_pos_main(flash_msg: str):
        if is_xhr:
            return JsonResponse(
                {"ok": True, "redirect": reverse("pos:main"), "message": flash_msg}
            )
        messages.success(request, flash_msg)
        return redirect("pos:main")

    def _ok_partial_pay(flash_msg: str):
        if is_xhr:
            return JsonResponse({"ok": True, "refresh_cart": True, "message": flash_msg})
        messages.success(request, flash_msg)
        return redirect("pos:main")

    order = _get_order_for_session(order_id, status=Order.Status.OPEN, is_held=False)
    try:
        transaction_at = _checkout_transaction_at(request, order)
    except ValueError as e:
        code = str(e)
        if code == "INVALID_ORDER_DATE":
            return _err("تاريخ المعاملة غير صالح.")
        if code == "ORDER_DATE_FUTURE":
            return _err("لا يمكن اختيار تاريخ بعد اليوم.")
        return _err(code)
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
            inv = finalize_order_invoice(
                order=order, user=request.user, customer=customer, transaction_at=transaction_at
            )
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
        return _ok_pos_main(flash_msg)

    payments = _payments_from_checkout_form(request, remaining)
    new_sum = sum((p[1] for p in payments), Decimal("0")).quantize(Decimal("0.01"))  # type: ignore[index]

    draft_name = (request.POST.get("customer_name_draft") or "").strip()[:200]
    ar_codes = credit_method_codes()
    needs_credit_customer = any(
        str(p[0] or "").strip().lower() in ar_codes and p[1] > Decimal("0") for p in payments
    )
    if needs_credit_customer and customer is None and len(draft_name) >= 2:
        cust_draft, draft_reused = resolve_or_create_active_customer_by_name(draft_name)
        if cust_draft:
            customer = cust_draft
            if not draft_reused:
                log_audit(
                    request.user,
                    "contacts.customer.checkout_name_draft",
                    "contacts.Customer",
                    cust_draft.pk,
                    {"name_ar": draft_name[:120]},
                )

    if remaining > 0 and new_sum <= 0:
        raw_mode = (request.POST.get("payment_mode") or "").strip()
        codes = set(get_payment_method_codes())
        split_on = (request.POST.get("use_payment_splits") or "").strip().lower() in ("1", "true", "on", "yes")
        if split_on:
            msg = "أضف أسطر الدفع المختلط أو تأكد من المبالغ والطرق."
        elif not raw_mode or raw_mode not in codes:
            msg = "اختر طريقة الدفع أولاً." if not raw_mode else "طريقة الدفع غير صالحة."
        else:
            msg = "أدخل مبلغ دفع أكبر من صفر."
        return _err(msg)
    if new_sum > remaining + Decimal("0.02"):
        return _err("مجموع الدفعات يتجاوز المتبقي على الطلب.")

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
            order=order,
            user=request.user,
            payments=payments,
            customer=customer,
            transaction_at=transaction_at,
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
        return _ok_pos_main(flash_msg)
    flash_msg = (
        f"تم تسجيل دفعة. المتبقي: {(remaining - new_sum).quantize(Decimal('0.01'))} ر.س"
    )
    return _ok_partial_pay(flash_msg)
