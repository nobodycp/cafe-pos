"""Seed the chart of accounts with required system accounts."""
from django.db import migrations

ACCOUNTS = [
    # Assets
    ("1001", "صندوق", "Cash", "asset", "CASH"),
    ("1002", "بنك", "Bank", "asset", "BANK"),
    ("1003", "ذمم زبائن", "Accounts Receivable", "asset", "AR"),
    ("1004", "مخزون", "Inventory", "asset", "INVENTORY"),
    # Liabilities
    ("2001", "ذمم موردين", "Accounts Payable", "liability", "AP"),
    ("2002", "ضريبة مستحقة", "Tax Payable", "liability", "TAX_PAYABLE"),
    # Equity
    ("3001", "رأس مال المالك", "Owner Capital", "equity", "OWNER_CAPITAL"),
    # Revenue
    ("4001", "إيرادات المبيعات", "Sales Revenue", "revenue", "SALES_REVENUE"),
    ("4002", "إيرادات عمولة", "Commission Revenue", "revenue", "COMMISSION_REVENUE"),
    ("4003", "إيرادات رسم خدمة", "Service Charge Revenue", "revenue", "SERVICE_REVENUE"),
    # Expense — COGS
    ("5001", "تكلفة بضاعة مباعة", "Cost of Goods Sold", "expense", "COGS"),
    # Expenses — operational
    ("6001", "رواتب", "Salaries", "expense", "EXP_SALARIES"),
    ("6002", "وقود", "Fuel", "expense", "EXP_FUEL"),
    ("6003", "تنظيف", "Cleaning", "expense", "EXP_CLEANING"),
    ("6004", "مستلزمات", "Supplies", "expense", "EXP_SUPPLIES"),
    ("6005", "إنترنت واتصالات", "Internet & Telecom", "expense", "EXP_INTERNET"),
    ("6006", "نقل", "Transport", "expense", "EXP_TRANSPORT"),
    ("6007", "صيانة", "Maintenance", "expense", "EXP_MAINTENANCE"),
    ("6008", "مصروفات أخرى", "Other Expenses", "expense", "EXP_OTHER"),
]


def seed(apps, schema_editor):
    Account = apps.get_model("accounting", "Account")
    for code, name_ar, name_en, atype, sys_code in ACCOUNTS:
        Account.objects.update_or_create(
            system_code=sys_code,
            defaults={
                "code": code,
                "name_ar": name_ar,
                "name_en": name_en,
                "account_type": atype,
                "is_active": True,
            },
        )


def unseed(apps, schema_editor):
    Account = apps.get_model("accounting", "Account")
    Account.objects.filter(system_code__in=[a[4] for a in ACCOUNTS]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
