"""Add Employee Advances account to COA."""
from django.db import migrations


def seed(apps, schema_editor):
    Account = apps.get_model("accounting", "Account")
    Account.objects.update_or_create(
        system_code="EMPLOYEE_ADVANCES",
        defaults={
            "code": "1300",
            "name_ar": "سلف الموظفين",
            "name_en": "Employee Advances",
            "account_type": "asset",
            "is_active": True,
        },
    )


def unseed(apps, schema_editor):
    Account = apps.get_model("accounting", "Account")
    Account.objects.filter(system_code="EMPLOYEE_ADVANCES").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0002_seed_chart_of_accounts"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
