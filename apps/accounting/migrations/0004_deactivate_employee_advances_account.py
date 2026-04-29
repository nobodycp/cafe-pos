"""إخفاء حساب سلف الموظفين من الدليل — السلف تُسجَّل كمصروف رواتب."""
from django.db import migrations


def deactivate(apps, schema_editor):
    Account = apps.get_model("accounting", "Account")
    Account.objects.filter(system_code="EMPLOYEE_ADVANCES").update(is_active=False)


def activate(apps, schema_editor):
    Account = apps.get_model("accounting", "Account")
    Account.objects.filter(system_code="EMPLOYEE_ADVANCES").update(is_active=True)


class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0003_seed_employee_advances_account"),
    ]

    operations = [
        migrations.RunPython(deactivate, activate),
    ]
