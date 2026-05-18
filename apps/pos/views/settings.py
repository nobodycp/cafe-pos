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

def redirect_pos_settings_to_app(request, tail: str = ""):
    """روابط قديمة /pos/settings/… → /app/settings/… (تحت include الجذر path(\"app/\", …))."""
    path = "/app/settings/" + (tail.lstrip("/") if tail else "")
    if request.GET:
        path += "?" + request.GET.urlencode()
    return redirect(path)
