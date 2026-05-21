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

POS_CUSTOMER_FORM_PREFIX = "poscc"

def _get_order_for_session(order_id, **extra_filters):
    """طلب مفتوح ضمن سياق الكاشير الحالي (وردية أو مستمر)."""
    if not SessionService.pos_is_ready():
        from django.http import Http404

        raise Http404
    return get_object_or_404(
        Order,
        pk=order_id,
        **SessionService.pos_session_filter_kwargs(),
        **extra_filters,
    )
def _post_redirect_after_cancel(request):
    """مسموح فقط لقيم محدّدة بعد إلغاء الطلب (نماذج POST من الواجهة)."""
    if (request.POST.get("next") or "").strip() == "session_summary":
        return redirect("core:session_summary")
    return redirect("pos:main")
def _annotate_pos_product_stock(qs):
    """كمية الرصيد الحالية لعرضها في جدول الكاشير (من StockBalance)."""
    bal = StockBalance.objects.filter(product_id=OuterRef("pk")).values("quantity_on_hand")[:1]
    sub = Subquery(bal, output_field=DecimalField(max_digits=18, decimal_places=4))
    zero = Value(Decimal("0"), output_field=DecimalField(max_digits=18, decimal_places=4))
    return qs.annotate(pos_stock_qty=Coalesce(sub, zero))
def _money(s: str) -> Decimal:
    return Decimal(str(s).replace(",", ".").strip() or "0")
def _parse_pos_discount_input(raw: str) -> tuple[Decimal, Decimal]:
    """
    حقل خصم واحد: مبلغ ثابت افتراضياً، أو نسبة إذا وُجدت علامة % (أو ٪ العربية).
    يُرجع (discount_amount, discount_percent)؛ يُصفّر الحقل الآخر.
    """
    t = (raw or "").strip().replace("\u066a", "%").replace("٪", "%")
    if not t:
        return Decimal("0"), Decimal("0")
    is_percent = "%" in t
    core = t.replace("%", "").strip()
    if not core:
        return Decimal("0"), Decimal("0")
    try:
        val = _money(core)
    except (InvalidOperation, ValueError):
        return Decimal("0"), Decimal("0")
    if is_percent:
        if val < 0:
            val = Decimal("0")
        if val > Decimal("100"):
            val = Decimal("100")
        return Decimal("0"), val
    if val < 0:
        val = Decimal("0")
    return val, Decimal("0")
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
def _receipt_stamp_lines() -> list[str]:
    raw = (get_pos_settings().receipt_stamp_text or "").strip()
    if not raw:
        return []
    return [p.strip() for p in raw.split(";") if p.strip()]
