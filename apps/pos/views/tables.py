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

def table_open(request):
    SessionService.require_open_session()
    tid = request.POST.get("table_id")
    table = get_object_or_404(DiningTable, pk=tid, is_active=True, is_cancelled=False)
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
        return redirect("pos:main")
    if guest_label and order.table_session_id:
        ts = order.table_session
        if not ts.customer_id:
            ts.guest_label = guest_label[:160]
            ts.save(update_fields=["guest_label", "updated_at"])
    request.session["active_pos_order_id"] = order.id
    return redirect("pos:main")
def table_quick_create(request):
    SessionService.require_open_session()
    name = (request.POST.get("name_ar") or "").strip()[:100]
    if not name:
        return JsonResponse({"ok": False, "error": "name_required"}, status=400)
    max_order = DiningTable.objects.aggregate(m=models.Max("sort_order"))["m"] or 0
    t = DiningTable.objects.create(name_ar=name, sort_order=max_order + 1, ephemeral=True)
    log_audit(request.user, "pos.table.quick_create", "pos.DiningTable", t.pk, {"name": name})
    return JsonResponse({"ok": True, "id": t.pk, "name_ar": t.name_ar})
