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
    lang = getattr(request, "LANGUAGE_CODE", None) or "ar"
    if lang not in ("ar", "en"):
        lang = "ar"
    AR = {
        "nav_pos": "نقطة البيع",
        "nav_inventory": "المخزون",
        "nav_suppliers": "الموردون",
        "nav_customers": "العملاء",
        "nav_employees": "الموظفون",
        "nav_expenses": "المصروفات",
        "nav_reports": "التقارير",
        "nav_close_session": "إغلاق الوردية",
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
    EN = {
        "nav_pos": "POS",
        "nav_inventory": "Inventory",
        "nav_suppliers": "Suppliers",
        "nav_customers": "Customers",
        "nav_employees": "Employees",
        "nav_expenses": "Expenses",
        "nav_reports": "Reports",
        "nav_close_session": "Close session",
        "nav_login": "Login",
        "nav_logout": "Logout",
        "session_open": "Open session",
        "session_active": "Active session",
        "session_none": "No open work session",
        "opening_cash": "Opening cash",
        "open_btn": "Open",
        "close_session": "Close session",
        "closing_cash": "Closing cash",
        "close_btn": "Close",
        "lang_ar": "Arabic",
        "lang_en": "English",
    }
    return {"LBL": EN if lang == "en" else AR}


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
