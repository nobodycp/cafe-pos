"""زر الرجوع الموحّد في غلاف /app/ — return= ثم الأب المنطقي (بدون referer لتجنّب حلقات الرجوع)."""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import quote, urlparse

from django.urls import NoReverseMatch, reverse


def safe_return_path(raw: str) -> str:
    path = (raw or "").strip()
    if not path.startswith("/") or path.startswith("//"):
        return ""
    if "\n" in path or "\r" in path:
        return ""
    return path


def safe_referer_path(request) -> str:
    ref = (request.META.get("HTTP_REFERER") or "").strip()
    if not ref:
        return ""
    try:
        parsed = urlparse(ref)
    except ValueError:
        return ""
    host = (request.get_host() or "").split(":")[0]
    ref_host = (parsed.netloc or "").split(":")[0]
    if ref_host and host and ref_host != host:
        return ""
    path = parsed.path or ""
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return safe_return_path(path)


def append_return(url: str, request) -> str:
    """يُلحق return=بالمسار الحالي لروابط التفاصيل."""
    back = safe_return_path(request.get_full_path())
    if not back:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}return={quote(back, safe='')}"


def resolve_toolbar_back(
    request,
    *,
    default_url: str,
    default_label: str,
    default_title: Optional[str] = None,
) -> dict[str, str]:
    ret = safe_return_path(request.GET.get("return", ""))
    if ret:
        return {
            "toolbar_back_url": ret,
            "toolbar_back_label": "← رجوع",
            "toolbar_back_title": "الصفحة السابقة",
        }
    return {
        "toolbar_back_url": default_url,
        "toolbar_back_label": default_label,
        "toolbar_back_title": default_title or default_label,
    }


def _rev(name: str, *args, **kwargs) -> str:
    try:
        return reverse(name, args=args, kwargs=kwargs)
    except NoReverseMatch:
        return reverse("pos:main")


def default_back_for_route(url_name: str, kwargs: dict[str, Any]) -> tuple[str, str, str]:
    """(url, label, title) للصفحة عند غياب return/referer."""
    pk = kwargs.get("pk")
    customer_id = kwargs.get("customer_id")
    invoice_pk = kwargs.get("invoice_pk")

    # ——— تقارير ———
    if url_name == "reports":
        return _rev("pos:main"), "← لوحة الطلبات", "لوحة الطلبات"
    if url_name in (
        "daily_sales",
        "expense_report",
        "weekly_report",
        "product_movement",
        "cash_flow",
        "payroll_report",
        "payment_channels",
        "payment_boxes",
        "treasury_vouchers_report",
    ):
        return _rev("shell:reports"), "← التقارير", "التقارير"
    if url_name == "payment_channel_ledger":
        return _rev("shell:payment_channels"), "← طرق الدفع", "طرق الدفع والتتبع"

    # ——— منتجات ———
    if url_name == "product_card":
        return _rev("shell:product_list"), "← المنتجات", "قائمة المنتجات"
    if url_name in ("product_edit", "recipe_list", "recipe_list_panel", "recipe_add", "recipe_add_panel"):
        if pk:
            return _rev("shell:product_card", pk), "← كرت الصنف", "كرت الصنف"
        return _rev("shell:product_list"), "← المنتجات", "قائمة المنتجات"
    if url_name in (
        "product_create",
        "manufactured_product_create",
        "product_workspace",
        "category_list",
        "unit_list",
    ):
        return _rev("shell:product_list"), "← المنتجات", "قائمة المنتجات"
    if url_name in ("category_edit", "category_create"):
        return _rev("shell:category_list"), "← التصنيفات", "التصنيفات"
    if url_name in ("unit_edit", "unit_create"):
        return _rev("shell:unit_list"), "← الوحدات", "الوحدات"

    # ——— مخزون ———
    if url_name == "inventory_home":
        return _rev("pos:main"), "← لوحة الطلبات", "لوحة الطلبات"
    if url_name in (
        "inventory_movements",
        "inventory_adjust",
        "raw_materials",
        "low_stock_alerts",
        "stocktake_list",
    ):
        return _rev("shell:inventory_home"), "← المخزون", "المخزون"
    if url_name in ("inventory_movement_create", "inventory_movement_edit"):
        return _rev("shell:inventory_movements"), "← حركات المخزون", "حركات المخزون"
    if url_name in ("raw_material_create", "raw_material_edit", "raw_material_card"):
        return _rev("shell:raw_materials"), "← المواد الخام", "المواد الخام"
    if url_name in ("stocktake_create", "stocktake_detail", "stocktake_edit"):
        return _rev("shell:stocktake_list"), "← الجرد", "الجرد الفعلي"

    # ——— عملاء ———
    if url_name in ("customers", "customer_create", "customer_balances"):
        return _rev("pos:main"), "← لوحة الطلبات", "لوحة الطلبات"
    if url_name == "customer_detail":
        return _rev("shell:customers"), "← قائمة العملاء", "قائمة العملاء"
    if url_name in ("customer_edit", "customer_payment", "customer_statement", "customer_invoices"):
        if pk:
            return _rev("shell:customer_detail", pk), "← العميل", "بطاقة العميل"
        return _rev("shell:customers"), "← قائمة العملاء", "قائمة العملاء"

    # ——— موردون ———
    if url_name in ("suppliers", "supplier_create", "supplier_balances", "commission_vendors", "purchase_new", "purchase_list"):
        return _rev("pos:main"), "← لوحة الطلبات", "لوحة الطلبات"
    if url_name == "supplier_detail":
        return _rev("shell:suppliers"), "← قائمة الموردين", "قائمة الموردين"
    if url_name in (
        "supplier_edit",
        "supplier_payment",
        "supplier_statement",
        "purchase_create",
    ):
        if pk:
            return _rev("shell:supplier_detail", pk), "← المورد", "بطاقة المورد"
        return _rev("shell:suppliers"), "← قائمة الموردين", "قائمة الموردين"
    if url_name in ("purchase_detail",):
        return _rev("shell:purchase_list"), "← فواتير الشراء", "فواتير الشراء"

    # ——— فواتير ———
    if url_name == "invoice_list":
        return _rev("pos:main"), "← لوحة الطلبات", "لوحة الطلبات"
    if url_name in ("invoice_detail", "sale_invoice_edit"):
        return _rev("shell:invoice_list"), "← الفواتير", "أرشيف الفواتير"

    # ——— محاسبة ———
    if url_name in ("accounting_chart", "trial_balance", "pnl", "journal_list", "accounting_treasury"):
        return _rev("pos:main"), "← لوحة الطلبات", "لوحة الطلبات"
    if url_name == "account_ledger":
        return _rev("shell:accounting_chart"), "← دليل الحسابات", "دليل الحسابات"
    if url_name in ("journal_detail", "journal_edit", "journal_create", "journal_transfer"):
        return _rev("shell:journal_list"), "← القيود اليومية", "القيود اليومية"

    # ——— موظفون ———
    if url_name in ("employees", "employee_create"):
        return _rev("pos:main"), "← لوحة الطلبات", "لوحة الطلبات"
    if url_name == "employee_detail":
        return _rev("shell:employees"), "← الموظفون", "قائمة الموظفين"
    if url_name in (
        "employee_edit",
        "employee_advance",
        "employee_payout",
        "employee_add_days",
        "employee_add_hours",
        "employee_cafe_purchase",
    ):
        if pk:
            return _rev("shell:employee_detail", pk), "← الموظف", "بطاقة الموظف"
        return _rev("shell:employees"), "← الموظفون", "قائمة الموظفين"

    # ——— مصروفات ———
    if url_name in ("expenses", "expense_create"):
        return _rev("pos:main"), "← لوحة الطلبات", "لوحة الطلبات"
    if url_name == "expense_edit":
        return _rev("shell:expenses"), "← المصروفات", "قائمة المصروفات"
    if url_name in ("expense_categories", "expense_category_create"):
        return _rev("shell:expenses"), "← المصروفات", "قائمة المصروفات"
    if url_name == "expense_category_edit":
        return _rev("shell:expense_categories"), "← التصنيفات", "تصنيفات المصروفات"

    # ——— إعدادات ———
    if url_name == "settings":
        return _rev("pos:main"), "← لوحة الطلبات", "لوحة الطلبات"
    if url_name in (
        "payment_methods",
        "payment_method_create",
        "payment_method_update",
        "tables_list",
        "table_create",
        "table_edit",
        "receipt_preview_live",
    ):
        return _rev("shell:settings"), "← الإعدادات", "الإعدادات"

    if url_name == "customer_invoices" and customer_id:
        return _rev("shell:customer_detail", customer_id), "← العميل", "بطاقة العميل"

    return _rev("pos:main"), "← لوحة الطلبات", "لوحة الطلبات"


def toolbar_back_for_request(request) -> dict[str, str]:
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {}
    match = getattr(request, "resolver_match", None)
    if not match or match.namespace != "shell":
        return {}
    url_name = match.url_name or ""
    kwargs = dict(match.kwargs or {})
    if url_name == "payment_channel_ledger" and request.GET.get("from") == "payment_boxes":
        default_url = _rev("shell:payment_boxes")
        default_label = "← الصناديق"
        default_title = "تقرير الصناديق"
    else:
        default_url, default_label, default_title = default_back_for_route(url_name, kwargs)
    return resolve_toolbar_back(
        request,
        default_url=default_url,
        default_label=default_label,
        default_title=default_title,
    )
