# Generated manually — توسيع طرق الدفع وتوحيدها مع الفواتير.

from django.db import migrations, models


def forwards_bank_to_bank_ps(apps, schema_editor):
    Expense = apps.get_model("expenses", "Expense")
    Expense.objects.filter(payment_method="bank").update(payment_method="bank_ps")


def backwards_bank_ps_to_bank(apps, schema_editor):
    Expense = apps.get_model("expenses", "Expense")
    for m in ("bank_ps", "palpay", "jawwalpay", "credit"):
        Expense.objects.filter(payment_method=m).update(payment_method="bank")


class Migration(migrations.Migration):

    dependencies = [
        ("expenses", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="expense",
            name="payment_method",
            field=models.CharField(
                choices=[
                    ("cash", "كاش"),
                    ("bank", "شبكة (عام)"),
                    ("bank_ps", "بنك فلسطين"),
                    ("palpay", "بال باي"),
                    ("jawwalpay", "جوال باي"),
                    ("credit", "آجل"),
                ],
                max_length=20,
                verbose_name="طريقة الدفع",
            ),
        ),
        migrations.RunPython(forwards_bank_to_bank_ps, backwards_bank_ps_to_bank),
    ]
