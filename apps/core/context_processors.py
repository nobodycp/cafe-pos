from django.conf import settings as django_settings


def site_branding(request):
    from apps.core.models import get_pos_settings

    s = get_pos_settings()
    return {
        "CAFE_NAME_AR": s.cafe_name_ar or getattr(django_settings, "CAFE_NAME_AR", ""),
        "CAFE_NAME_EN": s.cafe_name_en or getattr(django_settings, "CAFE_NAME_EN", ""),
        "POS_SETTINGS": s,
    }


def ui_labels(request):
    """تسميات شريط الغلاف والوردية — عربي ثابت.

    لا نربطها بـ request.LANGUAGE_CODE: LocaleMiddleware قد يفعّل الإنجليزية من Accept-Language
    بينما القوالب والواجهة موجهة للعربية فقط، فيظهر نص مثل «Session summary & close» بالخطأ.
    """
    AR = {
        "nav_pos": "نقطة البيع",
        "nav_inventory": "المخزون",
        "nav_suppliers": "الموردون",
        "nav_customers": "العملاء",
        "nav_employees": "الموظفون",
        "nav_expenses": "المصروفات",
        "nav_reports": "التقارير",
        "nav_close_session": "إغلاق الوردية",
        "nav_session_summary": "ملخص وإغلاق الوردية",
        "nav_login": "دخول",
        "nav_logout": "خروج",
        "session_open": "فتح وردية",
        "session_active": "وردية نشطة",
        "session_none": "لا توجد وردية مفتوحة",
        "opening_cash": "رصيد الصندوق الافتتاحي",
        "open_btn": "فتح",
        "close_session": "إغلاق الوردية",
        "closing_cash": "رصيد الصندوق الختامي",
        "close_btn": "إغلاق",
        "lang_ar": "العربية",
        "lang_en": "English",
    }
    return {"LBL": AR}


def low_stock_count(request):
    if not hasattr(request, "user") or not request.user.is_authenticated:
        return {"LOW_STOCK_COUNT": 0}
    from django.db.models import F
    from apps.inventory.models import StockBalance
    count = StockBalance.objects.filter(
        product__is_active=True,
        product__is_stock_tracked=True,
        product__min_stock_level__gt=0,
        quantity_on_hand__lte=F("product__min_stock_level"),
    ).count()
    return {"LOW_STOCK_COUNT": count}


def open_work_session(request):
    """وردية الكاشير المفتوحة (إن وجدت) لعرض روابط الملخص/الفتح في شريط الغلاف."""
    if not hasattr(request, "user") or not request.user.is_authenticated:
        return {"open_work_session": None}
    from apps.core.services import SessionService

    return {"open_work_session": SessionService.get_open_session()}


def shell_route_namespaces(request):
    """مسارات الوحدات (فواتير، مخزون، …) تُعاد فقط عبر الغلاف /app/."""
    return {
        "reports_ns": "shell",
        "billing_ns": "shell",
        "inventory_ns": "shell",
        "accounting_ns": "shell",
        "payroll_ns": "shell",
        "catalog_ns": "shell",
    }


def _shell_topbar_active_id(path: str) -> str:
    """مفتاح تبويب الشريط العلوي من مسار الطلب تحت /app/."""
    if not path:
        return ""
    marker = "/app/"
    if marker not in path:
        return ""
    rel = path[path.index(marker) + len(marker) :].lstrip("/")
    rules = (
        ("billing/invoices", "invoices"),
        ("invoices/", "invoices"),
        ("purchase/", "suppliers"),
        ("suppliers/", "suppliers"),
        ("products/", "products"),
        ("inventory/", "inventory"),
        ("customers/", "customers"),
        ("expenses/", "expenses"),
        ("reports/", "reports"),
        ("accounting/", "accounting"),
        ("payroll/", "employees"),
        ("settings/", "settings"),
    )
    for prefix, key in rules:
        if rel.startswith(prefix):
            return key
    return ""


def shell_topbar(request):
    """روابط الشريط العلوي للغلاف + تمييز الصفحة النشطة (بدون تكرار في القوالب)."""
    from django.urls import NoReverseMatch, reverse

    path = getattr(request, "path", "") or ""
    active = _shell_topbar_active_id(path)

    specs = (
        ("products", "shell:product_list", "المنتجات", "includes/icons/topbar_products.html"),
        ("inventory", "shell:inventory_home", "المخزون", "includes/icons/nav_inventory.html"),
        ("customers", "shell:customers", "العملاء", "includes/icons/nav_customers.html"),
        ("suppliers", "shell:suppliers", "الموردون", "includes/icons/topbar_suppliers.html"),
        ("expenses", "shell:expenses", "المصروفات", "includes/icons/topbar_expenses.html"),
        ("reports", "shell:reports", "التقارير", "includes/icons/nav_reports.html"),
        ("invoices", "shell:invoice_list", "الفواتير", "includes/icons/topbar_invoices.html"),
        ("accounting", "shell:accounting_chart", "المحاسبة", "includes/icons/topbar_accounting.html"),
        ("employees", "shell:employees", "الموظفون", "includes/icons/topbar_employees.html"),
        ("settings", "shell:settings", "الإعدادات", "includes/icons/nav_settings.html"),
    )
    links = []
    for lid, url_name, label, icon_tpl in specs:
        try:
            href = reverse(url_name)
        except NoReverseMatch:
            href = "#"
        links.append(
            {
                "id": lid,
                "href": href,
                "label": label,
                "icon_tpl": icon_tpl,
                "title": label,
            }
        )

    return {
        "shell_topbar_active": active,
        "shell_topbar_links": links,
    }
