#!/usr/bin/env python3
"""Mechanical split of monolithic views.py into a package (no logic changes)."""
from __future__ import annotations

import ast
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def extract_functions(source: str) -> dict[str, tuple[int, int, str]]:
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    out: dict[str, tuple[int, int, str]] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            start = node.lineno - 1
            end = node.end_lineno
            out[node.name] = (start, end, "".join(lines[start:end]))
    return out


def write_module(path: Path, header: str, func_names: list[str], funcs: dict[str, tuple[int, int, str]]) -> None:
    parts = [header.rstrip() + "\n\n"]
    for name in func_names:
        if name not in funcs:
            raise KeyError(f"{path}: missing function {name}")
        parts.append(funcs[name][2])
        if not parts[-1].endswith("\n"):
            parts[-1] += "\n"
    path.write_text("".join(parts), encoding="utf-8")


def split_app(app: str, mapping: dict[str, list[str]], header: str) -> list[str]:
    src_path = ROOT / "apps" / app / "views.py"
    pkg_dir = ROOT / "apps" / app / "views"
    if not src_path.is_file():
        print(f"Skip {app}: {src_path} already split")
        return []
    source = src_path.read_text(encoding="utf-8")
    funcs = extract_functions(source)
    pkg_dir.mkdir(exist_ok=True)
    all_names: list[str] = []
    for mod, names in mapping.items():
        write_module(pkg_dir / f"{mod}.py", header, names, funcs)
        all_names.extend(names)
    missing = set(funcs) - set(all_names)
    if missing:
        raise RuntimeError(f"{app}: unassigned functions: {sorted(missing)}")
    init_lines = ['"""Views package — re-exports for urls and shell imports."""\n']
    for mod, names in mapping.items():
        for name in names:
            init_lines.append(f"from apps.{app}.views.{mod} import {name}  # noqa: F401\n")
    (pkg_dir / "__init__.py").write_text("".join(init_lines), encoding="utf-8")
    src_path.unlink()
    return all_names


POS_HEADER = textwrap.dedent(
    '''\
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
    '''
)

POS_MAP = {
    "_helpers": [
        "_get_order_for_session",
        "_post_redirect_after_cancel",
        "_annotate_pos_product_stock",
        "_money",
        "_parse_pos_discount_input",
        "_ajax_or_redirect",
        "_ajax_or_redirect_error",
        "_receipt_stamp_lines",
    ],
    "settings": ["redirect_pos_settings_to_app"],
    "main": [
        "pos_main",
        "pos_customer_create_save",
        "pos_product_quick_save",
        "last_sale_invoice_panel",
        "last_sale_invoice_edit_redirect",
        "last_invoice_resume_into_cart",
        "cart_fragment",
        "floor_tables_fragment",
        "receipt_print",
        "receipt_live_preview",
        "receipt_raw",
    ],
    "tables": ["table_open", "table_quick_create"],
    "search": [
        "customers_search",
        "products_search",
        "customer_quick_create",
        "payer_hints_search",
    ],
    "orders": [
        "customize_product",
        "kitchen_ticket",
        "kitchen_receipt_embed",
        "order_resume",
        "order_new",
        "order_add_product",
        "order_adjust_line",
        "order_remove_line",
        "order_line_note",
        "order_line_unit_price",
        "order_set_customer",
        "order_note",
        "order_discount",
        "order_cancel",
        "order_hold",
    ],
    "checkout": ["_payments_from_checkout_form", "order_checkout"],
}

PURCH_HEADER = textwrap.dedent(
    '''\
    import json
    from datetime import datetime
    from decimal import Decimal, InvalidOperation
    from typing import Optional

    from django.contrib import messages
    from django.contrib.auth.decorators import login_required
    from django.db import transaction
    from django.db.models import Max, Q, Sum
    from django.http import JsonResponse
    from django.shortcuts import get_object_or_404, redirect, render
    from django.urls import Resolver404, resolve, reverse
    from django.views.decorators.http import require_GET, require_POST

    from apps.catalog.models import Product, Unit
    from apps.core.models import log_audit
    from apps.core.ledger_pagination import paginate_amount_ledger
    from apps.core.list_filters import get_search_q, parse_date_range
    from apps.core.pagination import paginate_queryset
    from apps.core.panel import PanelFormInvalid, handle_panel_form, panelize_form
    from apps.core.payment_methods import credit_method_codes, load_payment_method_rows
    from apps.purchasing.forms import SupplierForm, SupplierPaymentForm
    from apps.purchasing.supplier_list_filters import (
        COMMISSION_FILTER_CHOICES,
        LINKED_FILTER_CHOICES,
        NET_SIDE_CHOICES,
        SUPPLIER_SORT_CHOICES,
        apply_supplier_filters,
        parse_supplier_filters,
        supplier_filters_open,
        supplier_list_base_queryset,
    )
    from apps.purchasing.models import (
        PurchaseInvoice,
        PurchaseLine,
        PurchaseReturn,
        PurchaseReturnLine,
        Supplier,
        SupplierCafePurchase,
        SupplierLedgerEntry,
        SupplierPayment,
    )
    from apps.purchasing.purge_service import purge_purchase_invoice
    from apps.purchasing.request_parsers import (
        payment_rows as _payment_rows,
        purchase_form_state as _purchase_form_state,
        purchase_lines_from_request as _purchase_lines_from_request,
        purchase_payments_from_request as _purchase_payments_from_request,
    )
    from apps.purchasing.services import post_purchase_invoice, record_supplier_payment
    from apps.billing.models import SaleInvoiceLine


    OPENING_BALANCE_LEDGER_NOTE = "رصيد افتتاحي"
    '''
)

PURCH_MAP = {
    "_helpers": [
        "_supplier_opening_ledger_qs",
        "_supplier_opening_balance_from_ledger",
        "_apply_supplier_opening_balance",
        "_safe_purchase_redirect_next",
        "_apply_general_discount",
        "_purchasing_ctx",
        "_purchasing_reverse",
        "_purchasing_redirect",
        "_safe_return_path",
        "_purchase_invoice_detail_queryset",
        "_purchase_detail_back_url",
        "_redirect_open_purchase_invoice_to",
        "_redirect_open_purchase_invoice",
        "_purchase_invoice_detail_context",
        "_purchasing_tpl",
    ],
    "suppliers": [
        "supplier_list",
        "supplier_detail",
        "supplier_create",
        "supplier_edit",
        "supplier_delete",
        "supplier_payment_create",
        "supplier_link_customer",
    ],
    "invoices": [
        "purchase_invoice_create",
        "purchase_invoice_new",
        "purchase_invoice_list",
        "purchase_invoice_detail",
        "purchase_invoice_delete",
    ],
    "returns": ["purchase_return_create"],
    "reports": [
        "supplier_statement",
        "supplier_balances",
        "commission_vendor_report",
    ],
    "panels": [
        "purchase_invoice_create_panel",
        "supplier_create_panel",
        "supplier_edit_panel",
        "purchase_invoice_detail_panel",
    ],
    "search_api": [
        "purchase_products_search",
        "purchase_units_search",
        "purchase_unit_quick_create",
        "purchase_suppliers_search",
        "purchase_supplier_quick_create",
        "purchase_product_quick_create",
    ],
}


def main() -> None:
    split_app("pos", POS_MAP, POS_HEADER)
    split_app("purchasing", PURCH_MAP, PURCH_HEADER)
    print("Split pos and purchasing views OK")


if __name__ == "__main__":
    main()
