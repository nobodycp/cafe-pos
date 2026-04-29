from decimal import Decimal

from django.db import migrations, models


def infer_pay_type(apps, schema_editor):
    Employee = apps.get_model("payroll", "Employee")
    for employee in Employee.objects.all():
        if employee.hourly_wage and employee.hourly_wage > Decimal("0") and not employee.daily_wage:
            employee.pay_type = "hourly"
        else:
            employee.pay_type = "daily"
        employee.monthly_salary = Decimal("0")
        employee.save(update_fields=["pay_type", "monthly_salary"])


class Migration(migrations.Migration):

    dependencies = [
        ("payroll", "0002_employee_hourly_expense_links"),
    ]

    operations = [
        migrations.AddField(
            model_name="employee",
            name="pay_type",
            field=models.CharField(
                choices=[("daily", "يومي"), ("hourly", "بالساعة"), ("monthly", "شهري")],
                default="daily",
                max_length=16,
                verbose_name="آلية العمل",
            ),
        ),
        migrations.AddField(
            model_name="employee",
            name="monthly_salary",
            field=models.DecimalField(default=0, decimal_places=2, max_digits=12, verbose_name="راتب الشهر"),
        ),
        migrations.RunPython(infer_pay_type, migrations.RunPython.noop),
    ]
