"""سلف الموظفين: من أصول إلى مصروفات (أرشيف) — العمل الحالي عبر EXP_SALARIES فقط."""

from django.db import migrations


def reclassify(apps, schema_editor):
    Account = apps.get_model("accounting", "Account")
    Account.objects.filter(system_code="EMPLOYEE_ADVANCES").update(
        account_type="expense",
        code="6090",
        name_ar="سلف موظفين (أرشيف — السلف الحالية ضمن «رواتب»)",
        name_en="Employee advances (legacy; use Salaries)",
        is_active=False,
    )


def undo(apps, schema_editor):
    Account = apps.get_model("accounting", "Account")
    Account.objects.filter(system_code="EMPLOYEE_ADVANCES").update(
        account_type="asset",
        code="1300",
        name_ar="سلف الموظفين",
        name_en="Employee Advances",
        is_active=True,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0004_deactivate_employee_advances_account"),
    ]

    operations = [
        migrations.RunPython(reclassify, undo),
    ]
