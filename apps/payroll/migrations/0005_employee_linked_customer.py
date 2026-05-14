# Generated manually for employee ↔ customer link (POS آجل ↔ ذمة موظف)

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("contacts", "0001_initial"),
        ("payroll", "0004_employee_debt_repayment"),
    ]

    operations = [
        migrations.AddField(
            model_name="employee",
            name="linked_customer",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="linked_employee",
                to="contacts.customer",
                verbose_name="عميل مرتبط (آجل / طاولة)",
                help_text="إن وُجد: أي بيع بالآجل على هذا العميل يُسجَّل تلقائياً في «مشتريات المقهى» للموظف.",
            ),
        ),
        migrations.AddConstraint(
            model_name="employee",
            constraint=models.UniqueConstraint(
                fields=("linked_customer",),
                condition=models.Q(linked_customer__isnull=False),
                name="payroll_employee_linked_customer_uniq",
            ),
        ),
    ]
