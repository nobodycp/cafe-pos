"""حسابات سندات الخصم المكتسب والمسموح به في الخزينة الموحّدة."""

from django.db import migrations

ACCOUNTS = [
    ("4004", "خصومات مكتسبة", "Purchase Discounts Earned", "revenue", "DISCOUNT_EARNED"),
    ("6009", "خصومات مسموح بها", "Sales Discounts Allowed", "expense", "DISCOUNT_ALLOWED"),
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
        ("accounting", "0005_reclassify_employee_advances_expense"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
