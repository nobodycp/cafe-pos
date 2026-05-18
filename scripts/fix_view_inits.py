#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

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


def write_init(app: str, mapping: dict[str, list[str]]) -> None:
    lines = ['"""Views package — re-exports for urls and shell imports."""\n']
    for mod, names in mapping.items():
        for name in names:
            lines.append(f"from apps.{app}.views.{mod} import {name}\n")
    (ROOT / "apps" / app / "views" / "__init__.py").write_text("".join(lines), encoding="utf-8")


def main() -> None:
    write_init("pos", POS_MAP)
    write_init("purchasing", PURCH_MAP)


if __name__ == "__main__":
    main()
