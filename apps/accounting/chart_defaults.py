"""
حسابات النظام الافتراضية (دليل الحسابات) — نفس بيانات هجرة 0002_seed_chart_of_accounts.
تُستدعى عند غياب حساب مرتبط بـ system_code لتفادي أخطاء ACCOUNT_NOT_FOUND بعد قواعد ناقصة أو يدوية.
"""
from __future__ import annotations

# (code, name_ar, name_en, account_type, system_code)
DEFAULT_SYSTEM_ACCOUNTS = (
    ("1001", "صندوق", "Cash", "asset", "CASH"),
    ("1002", "بنك", "Bank", "asset", "BANK"),
    ("1003", "ذمم زبائن", "Accounts Receivable", "asset", "AR"),
    ("1004", "مخزون", "Inventory", "asset", "INVENTORY"),
    ("2001", "ذمم موردين", "Accounts Payable", "liability", "AP"),
    ("2002", "ضريبة مستحقة", "Tax Payable", "liability", "TAX_PAYABLE"),
    ("3001", "رأس مال المالك", "Owner Capital", "equity", "OWNER_CAPITAL"),
    ("3002", "تسويات رصيد افتتاحي", "Opening Balance Equity", "equity", "OPENING_BALANCE_EQUITY"),
    ("4001", "إيرادات المبيعات", "Sales Revenue", "revenue", "SALES_REVENUE"),
    ("4002", "إيرادات عمولة", "Commission Revenue", "revenue", "COMMISSION_REVENUE"),
    ("4003", "إيرادات رسم خدمة", "Service Charge Revenue", "revenue", "SERVICE_REVENUE"),
    ("5001", "تكلفة بضاعة مباعة", "Cost of Goods Sold", "expense", "COGS"),
    ("6001", "رواتب", "Salaries", "expense", "EXP_SALARIES"),
    ("6002", "وقود", "Fuel", "expense", "EXP_FUEL"),
    ("6003", "تنظيف", "Cleaning", "expense", "EXP_CLEANING"),
    ("6004", "مستلزمات", "Supplies", "expense", "EXP_SUPPLIES"),
    ("6005", "إنترنت واتصالات", "Internet & Telecom", "expense", "EXP_INTERNET"),
    ("6006", "نقل", "Transport", "expense", "EXP_TRANSPORT"),
    ("6007", "صيانة", "Maintenance", "expense", "EXP_MAINTENANCE"),
    ("6008", "مصروفات أخرى", "Other Expenses", "expense", "EXP_OTHER"),
)


def ensure_default_chart_accounts() -> None:
    """يُنشئ أو يُحدّث حسابات النظام الافتراضية (آمن للتكرار)."""
    from apps.accounting.models import Account

    for code, name_ar, name_en, atype, sys_code in DEFAULT_SYSTEM_ACCOUNTS:
        Account.objects.update_or_create(
            system_code=sys_code,
            defaults={
                "code": code,
                "name_ar": name_ar,
                "name_en": name_en or "",
                "account_type": atype,
                "is_active": True,
            },
        )
